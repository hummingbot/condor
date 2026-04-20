from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import WebSocket

from condor.web.auth import decode_jwt

logger = logging.getLogger(__name__)

# Mapping from WS channel prefix to ServerDataType
# NOTE: executors now uses dedicated WS streaming (not SDS polling)
_CHANNEL_TO_SDT = {
    "portfolio": "PORTFOLIO",
    "bots": "BOTS_STATUS",
    "prices": "PRICES",
}

# Reverse mapping for listener compatibility
_SDT_TO_CHANNEL_PREFIX = {
    "PORTFOLIO": "portfolio",
    "BOTS_STATUS": "bots",
    "PRICES": "prices",
    "CEX_PRICES": "prices",  # Legacy DataManager name
}


class _Connection:
    __slots__ = ("ws", "user_id", "channels")

    def __init__(self, ws: WebSocket, user_id: int):
        self.ws = ws
        self.user_id = user_id
        self.channels: set[str] = set()


class WebSocketManager:
    """Manages WebSocket connections and channel-based data broadcasting.

    Subscribes to ServerDataService for data updates and broadcasts
    to connected WebSocket clients. Candle streaming remains as dedicated
    WebSocket connections (not polled data).
    """

    def __init__(self):
        self._connections: list[_Connection] = []
        self._last_data: dict[str, Any] = {}  # channel -> last broadcast payload
        self._candle_tasks: dict[str, asyncio.Task] = {}
        self._trade_tasks: dict[str, asyncio.Task] = {}
        self._executor_tasks: dict[str, asyncio.Task] = {}
        self._sds_listener_registered = False
        # Track SDS subscriptions: channel -> CacheKey
        self._sds_subscriptions: dict[str, Any] = {}

    # -- Helpers --

    @staticmethod
    def _normalize_candle(c: Any) -> dict | None:
        """Normalize a candle from any format to a uniform dict with float values."""
        try:
            if isinstance(c, dict):
                return {
                    "timestamp": float(c.get("timestamp", 0)),
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("high", 0)),
                    "low": float(c.get("low", 0)),
                    "close": float(c.get("close", 0)),
                    "volume": float(c.get("volume", 0)),
                }
            elif isinstance(c, (list, tuple)) and len(c) >= 6:
                return {
                    "timestamp": float(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
        except (TypeError, ValueError):
            pass
        return None

    # -- Lifecycle --

    def start(self) -> None:
        if self._sds_listener_registered:
            return
        from condor.server_data_service import get_server_data_service

        sds = get_server_data_service()
        sds.add_listener(self._on_data_update)
        self._sds_listener_registered = True
        logger.info("WebSocketManager started (listening to ServerDataService)")

    def stop(self) -> None:
        if self._sds_listener_registered:
            from condor.server_data_service import get_server_data_service

            sds = get_server_data_service()
            sds.remove_listener(self._on_data_update)
            self._sds_listener_registered = False

        # Unsubscribe all SDS subscriptions
        self._cleanup_sds_subscriptions()

        for task in self._candle_tasks.values():
            if not task.done():
                task.cancel()
        self._candle_tasks.clear()

        for task in self._trade_tasks.values():
            if not task.done():
                task.cancel()
        self._trade_tasks.clear()

        for task in self._executor_tasks.values():
            if not task.done():
                task.cancel()
        self._executor_tasks.clear()

    def _cleanup_sds_subscriptions(self) -> None:
        """Remove all SDS subscriptions."""
        from condor.server_data_service import get_server_data_service

        sds = get_server_data_service()
        sds.unsubscribe_all("ws_manager")
        self._sds_subscriptions.clear()

    # -- Connection handling --

    async def connect(self, ws: WebSocket, token: str) -> Optional[_Connection]:
        payload = decode_jwt(token)
        if payload is None:
            await ws.close(code=4001, reason="Invalid token")
            return None

        from config_manager import UserRole, get_config_manager

        user_id = int(payload["sub"])
        cm = get_config_manager()
        role = cm.get_user_role(user_id)
        if role not in (UserRole.USER, UserRole.ADMIN):
            await ws.close(code=4003, reason="Forbidden")
            return None

        await ws.accept()
        conn = _Connection(ws, user_id)
        self._connections.append(conn)
        logger.info("WS connected: user %s", user_id)
        return conn

    def disconnect(self, conn: _Connection) -> None:
        if conn in self._connections:
            self._connections.remove(conn)
            logger.info("WS disconnected: user %s", conn.user_id)
            for channel in list(conn.channels):
                if channel.startswith("candles:"):
                    self._maybe_stop_candle_stream(channel)
                elif channel.startswith("trades:"):
                    self._maybe_stop_trade_stream(channel)
                elif channel.startswith("executors:"):
                    self._maybe_stop_executor_stream(channel)
                else:
                    self._maybe_unsub_sds(channel)

    def _maybe_unsub_sds(self, channel: str) -> None:
        """Unsubscribe from SDS if no WS clients remain for this channel."""
        has_subscribers = any(channel in c.channels for c in self._connections)
        if has_subscribers:
            return

        if channel in self._sds_subscriptions:
            from condor.server_data_service import get_server_data_service

            sds = get_server_data_service()
            cache_key = self._sds_subscriptions.pop(channel)
            sds.unsubscribe(cache_key, "ws_manager")
            logger.debug("WS unsubscribed SDS for channel %s", channel)

            # Stop portfolio history refresh when no subscribers remain
            if channel.startswith("portfolio:"):
                from condor.web.routes.portfolio import stop_history_refresh

                server_name = channel.split(":")[1] if ":" in channel else ""
                if server_name:
                    stop_history_refresh(server_name)

    # -- Message handling --

    async def handle_message(self, conn: _Connection, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        action = msg.get("action")
        channel = msg.get("channel", "")

        if action == "subscribe" and channel:
            conn.channels.add(channel)
            # Send last known data immediately
            if channel in self._last_data:
                await self._send(conn, channel, self._last_data[channel])
            if channel.startswith("candles:"):
                self._ensure_candle_stream(channel)
            elif channel.startswith("trades:"):
                self._ensure_trade_stream(channel)
            elif channel.startswith("executors:"):
                self._ensure_executor_stream(channel)
            else:
                await self._subscribe_sds(channel)

        elif action == "unsubscribe" and channel:
            conn.channels.discard(channel)
            if channel.startswith("candles:"):
                self._maybe_stop_candle_stream(channel)
            elif channel.startswith("trades:"):
                self._maybe_stop_trade_stream(channel)
            elif channel.startswith("executors:"):
                self._maybe_stop_executor_stream(channel)
            else:
                self._maybe_unsub_sds(channel)

    async def _subscribe_sds(self, channel: str) -> None:
        """Subscribe to SDS for a channel and prime cache."""
        if channel in self._sds_subscriptions:
            return  # Already subscribed

        parts = channel.split(":")
        if len(parts) < 2:
            return
        prefix = parts[0]
        server_name = parts[1]

        sdt_name = _CHANNEL_TO_SDT.get(prefix)
        if not sdt_name:
            return

        from condor.server_data_service import ServerDataType, get_server_data_service

        sds = get_server_data_service()
        data_type = ServerDataType[sdt_name]

        # Build params for price channels
        params = {}
        if sdt_name == "PRICES" and len(parts) >= 4:
            params = {"connector_name": parts[2], "trading_pair": parts[3]}

        try:
            cache_key = await sds.subscribe(
                server=server_name,
                data_type=data_type,
                subscriber_id="ws_manager",
                **params,
            )
            self._sds_subscriptions[channel] = cache_key

            # Broadcast the primed data
            result = sds.get(server_name, data_type, **params)
            if result is not None:
                prev = self._last_data.get(channel)
                if result != prev:
                    await self.broadcast(channel, result)

            # Pre-warm portfolio history cache on subscription
            if prefix == "portfolio":
                from condor.web.routes.portfolio import warm_portfolio_history

                asyncio.create_task(warm_portfolio_history(server_name))
        except Exception as e:
            logger.debug("Failed to subscribe SDS for %s: %s", channel, e)

    # -- SDS listener (legacy-compatible signature) --

    @staticmethod
    def _transform_executors(raw_data: Any) -> list[dict]:
        """Transform raw executor data to ExecutorInfo-compatible dicts for WS broadcast."""
        from condor.web.routes.executors import _extract_executors_list, _build_executor_info

        executors_list = _extract_executors_list(raw_data)
        result = []
        for ex in executors_list:
            info = _build_executor_info(ex)
            if info:
                result.append(info.model_dump())
        return result

    def _on_data_update(self, server_name: str, cache_key: str, data_type: Any, value: Any) -> None:
        """Called by SDS when cache is updated. Maps to WS channels and broadcasts."""
        prefix = _SDT_TO_CHANNEL_PREFIX.get(data_type.name)
        if not prefix:
            return

        # Build channel name
        if data_type.name in ("CEX_PRICES", "PRICES"):
            parts = cache_key.split(":")
            if len(parts) >= 3:
                channel = f"prices:{server_name}:{parts[1]}:{parts[2]}"
            else:
                return
        else:
            channel = f"{prefix}:{server_name}"

        has_subscribers = any(channel in conn.channels for conn in self._connections)
        if not has_subscribers:
            return

        asyncio.ensure_future(self._broadcast_update(channel, value))

    async def _broadcast_update(self, channel: str, data: Any) -> None:
        prev = self._last_data.get(channel)
        if data != prev:
            await self.broadcast(channel, data)

    # -- Broadcasting --

    async def broadcast(self, channel: str, data: Any) -> None:
        self._last_data[channel] = data
        dead: list[_Connection] = []
        for conn in self._connections:
            if channel in conn.channels:
                try:
                    await self._send(conn, channel, data)
                except Exception:
                    dead.append(conn)
        for conn in dead:
            self.disconnect(conn)

    async def _send(self, conn: _Connection, channel: str, data: Any) -> None:
        await conn.ws.send_json({"channel": channel, "data": data, "ts": time.time()})

    # -- Candle streaming (stays as-is) --

    def _ensure_candle_stream(self, channel: str) -> None:
        if channel in self._candle_tasks and not self._candle_tasks[channel].done():
            return
        self._candle_tasks[channel] = asyncio.create_task(
            self._candle_stream(channel)
        )
        logger.info("Started candle stream for %s", channel)

    def _maybe_stop_candle_stream(self, channel: str) -> None:
        for conn in self._connections:
            if channel in conn.channels:
                return
        task = self._candle_tasks.pop(channel, None)
        if task and not task.done():
            task.cancel()
            logger.info("Stopped candle stream for %s", channel)

    async def _candle_stream(self, channel: str) -> None:
        parts = channel.split(":")
        if len(parts) < 5:
            return
        _, server_name, connector, pair, interval = parts

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        # Historical candles are fetched by the frontend via REST endpoint.
        # WS stream only handles live updates to avoid duplicate slow API calls.

        while True:
            try:
                client = await cm.get_client(server_name)
                async with client.ws.market_data() as ws:
                    await ws.subscribe_candles(
                        connector, pair, interval=interval,
                        max_records=500, update_interval=1.0,
                    )
                    logger.info("Candle WS subscribed: %s", channel)
                    backoff = 5  # Reset on successful connection
                    async for msg in ws:
                        if not any(channel in c.channels for c in self._connections):
                            logger.info("No subscribers for %s, closing stream", channel)
                            return

                        logger.debug("Candle WS raw msg keys for %s: %s", channel, list(msg.keys()) if isinstance(msg, dict) else type(msg).__name__)
                        msg_type = msg.get("type")
                        if msg_type == "candle_update":
                            raw = msg.get("data")
                            candle = self._normalize_candle(raw) if raw else None
                            if candle:
                                await self.broadcast(
                                    channel,
                                    {"type": "candle_update", "candle": candle},
                                )
                        elif msg_type == "candles":
                            raw_list = msg.get("data") or []
                            candles = [
                                c for r in raw_list
                                if (c := self._normalize_candle(r)) is not None
                            ]
                            if candles:
                                await self.broadcast(
                                    channel,
                                    {"type": "candles", "data": candles},
                                )
                        elif msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            error_msg = msg.get("message", "unknown error")
                            logger.warning("Candle stream error for %s: %s", channel, error_msg)
                            await self.broadcast(channel, {"type": "error", "message": f"Stream error: {error_msg}"})
                            break
                        else:
                            logger.info("Candle stream unrecognized msg type for %s: type=%s keys=%s", channel, msg_type, list(msg.keys()) if isinstance(msg, dict) else type(msg).__name__)

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                # Detect permanent failures (auth, not found) — don't retry
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning("Candle stream permanent error for %s: %s — giving up", channel, e)
                    await self.broadcast(channel, {"type": "error", "message": f"Stream failed: {error_str}"})
                    return

                logger.warning("Candle stream error for %s: %s, reconnecting in %ds...", channel, e, backoff)
                await self.broadcast(channel, {"type": "error", "message": f"Connection lost, retrying in {backoff}s"})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


    # -- Trade streaming --

    def _ensure_trade_stream(self, channel: str) -> None:
        if channel in self._trade_tasks and not self._trade_tasks[channel].done():
            return
        self._trade_tasks[channel] = asyncio.create_task(
            self._trade_stream(channel)
        )
        logger.info("Started trade stream for %s", channel)

    def _maybe_stop_trade_stream(self, channel: str) -> None:
        for conn in self._connections:
            if channel in conn.channels:
                return
        task = self._trade_tasks.pop(channel, None)
        if task and not task.done():
            task.cancel()
            logger.info("Stopped trade stream for %s", channel)

    async def _trade_stream(self, channel: str) -> None:
        parts = channel.split(":")
        if len(parts) < 4:
            return
        _, server_name, connector, pair = parts[:4]

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        while True:
            try:
                client = await cm.get_client(server_name)
                async with client.ws.market_data() as ws:
                    await ws.subscribe_trades(
                        connector, pair, update_interval=1.0,
                    )
                    logger.info("Trade WS subscribed: %s", channel)
                    backoff = 5
                    async for msg in ws:
                        if not any(channel in c.channels for c in self._connections):
                            logger.info("No subscribers for %s, closing trade stream", channel)
                            return

                        msg_type = msg.get("type")
                        if msg_type == "trades":
                            trade_data = msg.get("data", [])
                            trades = []
                            for t in trade_data:
                                if isinstance(t, dict):
                                    trades.append({
                                        "price": float(t.get("price", 0)),
                                        "amount": float(t.get("amount", t.get("quantity", 0))),
                                        "side": t.get("side", t.get("trade_type", "buy")).lower(),
                                        "timestamp": float(t.get("timestamp", 0)),
                                    })
                            if trades:
                                await self.broadcast(
                                    channel,
                                    {"type": "trades", "data": trades},
                                )
                        elif msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            error_msg = msg.get("message", "unknown error")
                            logger.warning("Trade stream error for %s: %s", channel, error_msg)
                            break

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning("Trade stream permanent error for %s: %s — giving up", channel, e)
                    return

                logger.warning("Trade stream error for %s: %s, reconnecting in %ds...", channel, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


    # -- Executor streaming (via Hummingbot WS) --

    def _ensure_executor_stream(self, channel: str) -> None:
        if channel in self._executor_tasks and not self._executor_tasks[channel].done():
            return
        self._executor_tasks[channel] = asyncio.create_task(
            self._executor_stream(channel)
        )
        logger.info("Started executor stream for %s", channel)

    def _maybe_stop_executor_stream(self, channel: str) -> None:
        for conn in self._connections:
            if channel in conn.channels:
                return
        task = self._executor_tasks.pop(channel, None)
        if task and not task.done():
            task.cancel()
            logger.info("Stopped executor stream for %s", channel)

    async def _executor_stream(self, channel: str) -> None:
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        # Try SDS cache first (pre-warmed by auto_subscribe_servers on startup)
        if channel not in self._last_data:
            from condor.server_data_service import ServerDataType, get_server_data_service

            sds = get_server_data_service()
            cached = sds.get(server_name, ServerDataType.EXECUTORS)
            if cached is not None:
                executors = self._transform_executors(cached)
                if executors:
                    await self.broadcast(channel, executors)
                    logger.info(
                        "Executor SDS cache hit: %d executors for %s",
                        len(executors), channel,
                    )

        # Progressive pre-fetch only if we still have no data
        if channel not in self._last_data:
            try:
                from condor.web.routes.executors import _extract_executors_list

                sds = get_server_data_service()
                client = await cm.get_client(server_name)
                all_raw: list[dict] = []
                cursor: str | None = None
                page_num = 0
                FIRST_PAGE = 50
                NEXT_PAGE = 500

                while True:
                    page_size = FIRST_PAGE if page_num == 0 else NEXT_PAGE
                    kwargs: dict = {"limit": page_size}
                    if cursor:
                        kwargs["cursor"] = cursor
                    result = await client.executors.search_executors(**kwargs)
                    page = _extract_executors_list(result)
                    all_raw.extend(page)

                    # Transform and broadcast accumulated results after each page
                    executors = self._transform_executors(all_raw)
                    if executors:
                        await self.broadcast(channel, executors)
                        logger.info(
                            "Executor pre-fetch page %d: %d executors (total %d) for %s",
                            page_num, len(page), len(executors), channel,
                        )

                    # Determine next cursor
                    next_cursor = None
                    if isinstance(result, dict):
                        next_cursor = result.get("next_cursor") or result.get("cursor")
                        pagination = result.get("pagination")
                        if not next_cursor and isinstance(pagination, dict):
                            next_cursor = pagination.get("next_cursor") or pagination.get("cursor")
                    if not next_cursor or len(page) < page_size:
                        break
                    if len(all_raw) >= 5000:
                        break
                    cursor = next_cursor
                    page_num += 1

                # Cache in SDS so other consumers benefit
                sds.put(server_name, ServerDataType.EXECUTORS, all_raw)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("Executor pre-fetch failed for %s: %s", channel, e)

        while True:
            try:
                client = await cm.get_client(server_name)
                async with client.ws.executors() as ws:
                    await ws.subscribe_executors(update_interval=2.0)
                    logger.info("Executor WS subscribed: %s", channel)
                    backoff = 5  # Reset on successful connection
                    async for msg in ws:
                        if not any(channel in c.channels for c in self._connections):
                            logger.info("No subscribers for %s, closing executor stream", channel)
                            return

                        msg_type = msg.get("type")
                        if msg_type == "executors":
                            raw_data = msg.get("data", [])
                            executors = self._transform_executors(raw_data)
                            await self._broadcast_update(channel, executors)
                        elif msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            error_msg = msg.get("message", "unknown error")
                            logger.warning("Executor stream error for %s: %s", channel, error_msg)
                            break

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning("Executor stream permanent error for %s: %s — giving up", channel, e)
                    return

                logger.warning("Executor stream error for %s: %s, reconnecting in %ds...", channel, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


# -- Singleton --

_instance: Optional[WebSocketManager] = None


def get_ws_manager() -> WebSocketManager:
    global _instance
    if _instance is None:
        _instance = WebSocketManager()
    return _instance
