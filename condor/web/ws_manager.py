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
_CHANNEL_TO_SDT = {
    "portfolio": "PORTFOLIO",
    "bots": "BOTS_STATUS",
    "executors": "EXECUTORS",
    "prices": "PRICES",
}

# Reverse mapping for listener compatibility
_SDT_TO_CHANNEL_PREFIX = {
    "PORTFOLIO": "portfolio",
    "BOTS_STATUS": "bots",
    "EXECUTORS": "executors",
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
        self._sds_listener_registered = False
        # Track SDS subscriptions: channel -> CacheKey
        self._sds_subscriptions: dict[str, Any] = {}

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
            else:
                await self._subscribe_sds(channel)

        elif action == "unsubscribe" and channel:
            conn.channels.discard(channel)
            if channel.startswith("candles:"):
                self._maybe_stop_candle_stream(channel)
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
        except Exception as e:
            logger.debug("Failed to subscribe SDS for %s: %s", channel, e)

    # -- SDS listener (legacy-compatible signature) --

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
                            await self.broadcast(
                                channel,
                                {"type": "candle_update", "candle": msg.get("data")},
                            )
                        elif msg_type == "candles":
                            await self.broadcast(
                                channel,
                                {"type": "candles", "data": msg.get("data")},
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


# -- Singleton --

_instance: Optional[WebSocketManager] = None


def get_ws_manager() -> WebSocketManager:
    global _instance
    if _instance is None:
        _instance = WebSocketManager()
    return _instance
