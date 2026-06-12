from __future__ import annotations

import asyncio
import json
import logging
import math
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

# Interval string -> seconds for buffer sizing
_INTERVAL_SECONDS: dict[str, int] = {
    "1s": 1,
    "5s": 5,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}

# Auto-cleanup candle buffers unused for this long
_CANDLE_BUFFER_IDLE_TTL = 600  # 10 minutes


class _CandleBuffer:
    """Per-channel candle buffer with dynamic sizing based on interval + duration."""

    __slots__ = ("interval", "_data", "_max_size", "last_accessed")

    def __init__(self, interval: str, duration_seconds: int = 3600):
        self.interval = interval
        self._data: dict[float, dict] = {}
        self._max_size: int = 200
        self.last_accessed: float = time.monotonic()
        self.set_duration(duration_seconds)

    def set_duration(self, duration_seconds: int) -> int:
        """Resize buffer for the given duration. Returns the new max size."""
        interval_sec = _INTERVAL_SECONDS.get(self.interval, 60)
        needed = math.ceil(duration_seconds / interval_sec)
        new_max = max(needed, 200)  # minimum 200
        old_max = self._max_size
        self._max_size = new_max
        self.last_accessed = time.monotonic()
        self._evict()
        if new_max != old_max:
            logger.debug(
                "Candle buffer resized %s: %d -> %d (duration=%ds)",
                self.interval,
                old_max,
                new_max,
                duration_seconds,
            )
        return new_max

    def upsert(self, candle: dict) -> None:
        self._data[candle["timestamp"]] = candle
        self._evict()

    def upsert_many(self, candles: list[dict]) -> None:
        for c in candles:
            self._data[c["timestamp"]] = c
        self._evict()

    def get_sorted(self) -> list[dict]:
        self.last_accessed = time.monotonic()
        return sorted(self._data.values(), key=lambda c: c["timestamp"])

    def _evict(self) -> None:
        excess = len(self._data) - self._max_size
        if excess <= 0:
            return
        # Drop the `excess` oldest timestamps in a single O(n log n) pass
        # instead of calling min() (O(n)) per evicted candle.
        for ts in sorted(self._data)[:excess]:
            del self._data[ts]

    @property
    def size(self) -> int:
        return len(self._data)

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def needs_backfill(self) -> bool:
        """True if buffer has room for significantly more candles."""
        return len(self._data) < self._max_size * 0.5


class _Connection:
    __slots__ = ("ws", "user_id", "channels")

    def __init__(self, ws: WebSocket, user_id: int):
        self.ws = ws
        self.user_id = user_id
        self.channels: set[str] = set()


class WebSocketManager:
    """Manages WebSocket connections and channel-based data broadcasting.

    Subscribes to ServerDataService for data updates and broadcasts
    to connected WebSocket clients. Candle streaming uses dedicated
    WebSocket connections with dynamic per-channel buffering.
    """

    _CANDLE_KEEP_ALIVE = 300  # 5-minute grace period before tearing down candle streams

    def __init__(self):
        self._connections: list[_Connection] = []
        self._last_data: dict[str, Any] = {}  # channel -> last broadcast payload
        self._candle_tasks: dict[str, asyncio.Task] = {}
        self._candle_poll_tasks: dict[str, asyncio.Task] = {}
        self._trade_tasks: dict[str, asyncio.Task] = {}
        self._executor_tasks: dict[str, asyncio.Task] = {}
        self._order_book_tasks: dict[str, asyncio.Task] = {}
        self._bots_ws_tasks: dict[str, asyncio.Task] = {}
        self._positions_ws_tasks: dict[str, asyncio.Task] = {}
        self._performance_ws_tasks: dict[str, asyncio.Task] = {}
        self._controller_perf_tasks: dict[str, asyncio.Task] = {}
        self._sds_listener_registered = False
        # Track SDS subscriptions: channel -> CacheKey
        self._sds_subscriptions: dict[str, Any] = {}
        # Candle buffers: channel -> _CandleBuffer (dynamic sizing)
        self._candle_buffers: dict[str, _CandleBuffer] = {}
        # Deferred teardown timers for candle streams
        self._candle_teardown_timers: dict[str, asyncio.TimerHandle] = {}
        # Periodic cleanup task
        self._cleanup_task: asyncio.Task | None = None
        # Track last WS candle update time per channel (monotonic)
        self._last_candle_ws_update: dict[str, float] = {}
        # Track whether first message per channel has been logged
        self._candle_first_msg_logged: set[str] = set()
        # Strong refs to fire-and-forget one-shot tasks (backfill, warm cache)
        # so the GC can't cancel them mid-flight (the event loop only keeps a
        # weak reference). Entries auto-remove on completion.
        self._oneshot_tasks: set[asyncio.Task] = set()
        # Lazily-built registry of per-type stream lifecycles (see _stream_registry)
        self._stream_registry_cache: dict | None = None

    # -- Helpers --

    def _track_oneshot(self, task: asyncio.Task) -> None:
        """Keep a strong reference to a fire-and-forget task until it finishes,
        so the GC can't silently cancel it (the event loop only holds a weak
        reference). The reference is dropped automatically on completion."""
        self._oneshot_tasks.add(task)
        task.add_done_callback(self._oneshot_tasks.discard)

    @staticmethod
    def _server_from_channel(channel: str) -> str | None:
        """Server name encoded as the second segment of a channel
        (``portfolio:<server>``, ``bots_ws:<server>``, ``candles:<server>:...``,
        ``prices:<server>:<connector>:<pair>``, ...). Every WS channel is
        server-scoped; returns None when no server segment is present."""
        parts = channel.split(":")
        return parts[1] if len(parts) >= 2 and parts[1] else None

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
        # Start periodic candle buffer cleanup
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._candle_buffer_cleanup_loop())
        logger.info("WebSocketManager started (listening to ServerDataService)")

    def stop(self) -> None:
        if self._sds_listener_registered:
            from condor.server_data_service import get_server_data_service

            sds = get_server_data_service()
            sds.remove_listener(self._on_data_update)
            self._sds_listener_registered = False

        # Unsubscribe all SDS subscriptions
        self._cleanup_sds_subscriptions()

        for handle in self._candle_teardown_timers.values():
            handle.cancel()
        self._candle_teardown_timers.clear()

        for task in self._candle_tasks.values():
            if not task.done():
                task.cancel()
        self._candle_tasks.clear()

        for task in self._candle_poll_tasks.values():
            if not task.done():
                task.cancel()
        self._candle_poll_tasks.clear()
        self._last_candle_ws_update.clear()
        self._candle_first_msg_logged.clear()

        for task in self._trade_tasks.values():
            if not task.done():
                task.cancel()
        self._trade_tasks.clear()

        for task in self._executor_tasks.values():
            if not task.done():
                task.cancel()
        self._executor_tasks.clear()

        for task in self._order_book_tasks.values():
            if not task.done():
                task.cancel()
        self._order_book_tasks.clear()

        for task in self._bots_ws_tasks.values():
            if not task.done():
                task.cancel()
        self._bots_ws_tasks.clear()

        for task in self._positions_ws_tasks.values():
            if not task.done():
                task.cancel()
        self._positions_ws_tasks.clear()

        for task in self._performance_ws_tasks.values():
            if not task.done():
                task.cancel()
        self._performance_ws_tasks.clear()

        for task in self._controller_perf_tasks.values():
            if not task.done():
                task.cancel()
        self._controller_perf_tasks.clear()

        # Cancel any still-pending one-shot tasks (snapshot: cancel() fires the
        # done-callback that mutates the set).
        for task in list(self._oneshot_tasks):
            if not task.done():
                task.cancel()
        self._oneshot_tasks.clear()

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            self._cleanup_task = None

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
                elif channel.startswith("orderbook:"):
                    self._maybe_stop_order_book_stream(channel)
                elif channel.startswith("bots_ws:"):
                    self._maybe_stop_bots_ws_stream(channel)
                elif channel.startswith("positions_ws:"):
                    self._maybe_stop_positions_ws_stream(channel)
                elif channel.startswith("performance_ws:"):
                    self._maybe_stop_performance_ws_stream(channel)
                elif channel.startswith("controller_perf:"):
                    self._maybe_stop_controller_perf_stream(channel)
                else:
                    self._maybe_unsub_sds(channel)

    def _maybe_unsub_sds(self, channel: str) -> None:
        """Unsubscribe from SDS if no WS clients remain for this channel."""
        if self._has_subscribers(channel):
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
            # Object-level access control: channels encode their target server as
            # the second segment, and the stream coroutines push that server's
            # data back to the subscriber. Only allow subscribing to servers the
            # user can access (mirrors the REST has_server_access checks); reject
            # otherwise to prevent cross-server data leaks (IDOR).
            from config_manager import get_config_manager

            server_name = self._server_from_channel(channel)
            if server_name is None or not get_config_manager().has_server_access(
                conn.user_id, server_name
            ):
                logger.warning(
                    "WS subscribe denied: user=%s channel=%s server=%s (no server access)",
                    conn.user_id,
                    channel,
                    server_name,
                )
                return
            conn.channels.add(channel)
            logger.info("WS subscribe: user=%s channel=%s", conn.user_id, channel)
            # Send last known data immediately (candles use buffer instead)
            if channel.startswith("candles:"):
                duration = msg.get("duration")  # seconds, sent by frontend
                await self._handle_candle_subscribe(conn, channel, duration)
            elif channel.startswith("orderbook:"):
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                self._ensure_order_book_stream(channel)
            elif channel.startswith("trades:"):
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                self._ensure_trade_stream(channel)
            elif channel.startswith("executors:"):
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                self._ensure_executor_stream(channel)
            elif channel.startswith("bots_ws:"):
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                self._ensure_bots_ws_stream(channel)
            elif channel.startswith("positions_ws:"):
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                self._ensure_positions_ws_stream(channel)
            elif channel.startswith("performance_ws:"):
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                self._ensure_performance_ws_stream(channel)
            elif channel.startswith("controller_perf:"):
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                self._ensure_controller_perf_stream(channel)
            else:
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                await self._subscribe_sds(channel)

        elif action == "unsubscribe" and channel:
            conn.channels.discard(channel)
            if channel.startswith("candles:"):
                self._maybe_stop_candle_stream(channel)
            elif channel.startswith("orderbook:"):
                self._maybe_stop_order_book_stream(channel)
            elif channel.startswith("trades:"):
                self._maybe_stop_trade_stream(channel)
            elif channel.startswith("executors:"):
                self._maybe_stop_executor_stream(channel)
            elif channel.startswith("bots_ws:"):
                self._maybe_stop_bots_ws_stream(channel)
            elif channel.startswith("positions_ws:"):
                self._maybe_stop_positions_ws_stream(channel)
            elif channel.startswith("performance_ws:"):
                self._maybe_stop_performance_ws_stream(channel)
            elif channel.startswith("controller_perf:"):
                self._maybe_stop_controller_perf_stream(channel)
            else:
                self._maybe_unsub_sds(channel)

        elif action == "set_candle_duration" and channel:
            # Frontend changed duration without re-subscribing
            duration = msg.get("duration")
            if channel.startswith("candles:") and duration:
                await self._handle_candle_duration_change(conn, channel, int(duration))

    async def _handle_candle_subscribe(
        self, conn: _Connection, channel: str, duration: int | None
    ) -> None:
        """Handle candle channel subscription with optional duration."""
        parts = channel.split(":")
        if len(parts) < 5:
            return
        interval = parts[4]
        dur = int(duration) if duration else 3 * 86400  # default 3 days

        # Cancel any pending teardown — a subscriber just came back
        timer = self._candle_teardown_timers.pop(channel, None)
        if timer is not None:
            timer.cancel()
            logger.debug("Cancelled pending candle teardown for %s", channel)

        buf = self._candle_buffers.get(channel)
        if buf is None:
            buf = _CandleBuffer(interval, dur)
            self._candle_buffers[channel] = buf
        else:
            # Expand buffer if this client needs more
            if dur > 0:
                old_max = buf.max_size
                buf.set_duration(dur)
                if buf.max_size > old_max and buf.needs_backfill:
                    self._track_oneshot(
                        asyncio.create_task(self._backfill_candles(channel))
                    )

        # Send buffered candles as initial snapshot
        sorted_candles = buf.get_sorted()
        if sorted_candles:
            await self._send(conn, channel, {"type": "candles", "data": sorted_candles})

        # If the stream task is still running (kept alive during grace period), skip restart
        self._ensure_candle_stream(channel)

    async def _handle_candle_duration_change(
        self, conn: _Connection, channel: str, duration: int
    ) -> None:
        """Handle duration change for an existing candle subscription.

        Only grows the buffer — never shrinks. The frontend manages its own
        display window; the backend just ensures enough history is buffered.
        """
        buf = self._candle_buffers.get(channel)
        if buf is None:
            return
        old_max = buf.max_size
        # Only expand — skip if requested duration would shrink the buffer
        interval_sec = _INTERVAL_SECONDS.get(buf.interval, 60)
        needed = max(math.ceil(duration / interval_sec), 200)
        if needed <= old_max:
            return
        buf.set_duration(duration)
        if buf.needs_backfill:
            await self._backfill_candles(channel)
        # Broadcast updated snapshot to ALL subscribers on this channel
        sorted_candles = buf.get_sorted()
        if sorted_candles:
            await self.broadcast(channel, {"type": "candles", "data": sorted_candles})

    async def _backfill_candles(self, channel: str) -> None:
        """Fetch historical candles to fill the buffer gap."""
        parts = channel.split(":")
        if len(parts) < 5:
            return
        _, server_name, connector, pair, interval = parts
        buf = self._candle_buffers.get(channel)
        if buf is None:
            return

        from config_manager import get_config_manager

        cm = get_config_manager()
        try:
            client = await cm.get_client(server_name)
            interval_sec = _INTERVAL_SECONDS.get(interval, 60)
            end_time = int(time.time())
            start_time = end_time - (buf.max_size * interval_sec)

            logger.info(
                "Backfilling candles for %s: need %d, have %d, fetching %ds-%ds",
                channel,
                buf.max_size,
                buf.size,
                start_time,
                end_time,
            )

            result = await client.market_data.get_historical_candles(
                connector,
                pair,
                interval,
                start_time=start_time,
                end_time=end_time,
            )

            candles_raw = (
                result
                if isinstance(result, list)
                else result.get("data", []) if isinstance(result, dict) else []
            )
            # Fallback to regular candles if historical returned nothing
            if not candles_raw:
                result = await client.market_data.get_candles(
                    connector, pair, interval, min(buf.max_size, 5000)
                )
                candles_raw = (
                    result
                    if isinstance(result, list)
                    else result.get("data", []) if isinstance(result, dict) else []
                )

            candles = [
                c for r in candles_raw if (c := self._normalize_candle(r)) is not None
            ]
            if candles:
                buf.upsert_many(candles)
                logger.info(
                    "Backfilled %d candles for %s (buffer: %d/%d)",
                    len(candles),
                    channel,
                    buf.size,
                    buf.max_size,
                )
        except Exception as e:
            logger.warning("Candle backfill failed for %s: %s", channel, e)

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

                self._track_oneshot(
                    asyncio.create_task(warm_portfolio_history(server_name))
                )
        except Exception as e:
            logger.debug("Failed to subscribe SDS for %s: %s", channel, e)

    # -- SDS listener (legacy-compatible signature) --

    @staticmethod
    def _transform_executors(raw_data: Any) -> list[dict]:
        """Transform raw executor data to ExecutorInfo-compatible dicts for WS broadcast."""
        from condor.fetchers.executors import extract_executors_list
        from condor.web.routes.executors import _build_executor_info

        executors_list = extract_executors_list(raw_data)
        result = []
        for ex in executors_list:
            info = _build_executor_info(ex)
            if info:
                result.append(info.model_dump())
        return result

    @staticmethod
    def _transform_bots(raw_data: Any) -> dict:
        """Transform raw BOTS_STATUS data to BotsPageResponse-compatible dict for WS broadcast."""
        from condor.web.routes.bots import _extract_bots_list

        bots_list = _extract_bots_list(raw_data)
        controllers = []
        bots = []
        total_pnl = 0.0
        total_volume = 0.0

        for bot_data in bots_list:
            bot_name = bot_data.get("bot_name", "")
            bot_status = bot_data.get("status", "unknown")
            performance = bot_data.get("performance", {})
            error_logs = bot_data.get("error_logs", [])
            general_logs = bot_data.get("general_logs", [])
            if not isinstance(error_logs, list):
                error_logs = []
            if not isinstance(general_logs, list):
                general_logs = []

            num_controllers = 0

            if isinstance(performance, dict):
                for ctrl_name, ctrl_info in performance.items():
                    if not isinstance(ctrl_info, dict):
                        continue
                    num_controllers += 1
                    ctrl_perf = ctrl_info.get("performance", {})
                    if not isinstance(ctrl_perf, dict):
                        ctrl_perf = {}

                    realized = float(ctrl_perf.get("realized_pnl_quote", 0) or 0)
                    unrealized = float(ctrl_perf.get("unrealized_pnl_quote", 0) or 0)
                    global_pnl = realized + unrealized
                    global_pnl_pct = float(ctrl_perf.get("global_pnl_pct", 0) or 0)
                    volume = float(ctrl_perf.get("volume_traded", 0) or 0)
                    close_types = ctrl_perf.get("close_type_counts", {})
                    if not isinstance(close_types, dict):
                        close_types = {}
                    positions = ctrl_perf.get("positions_summary", [])
                    if not isinstance(positions, list):
                        positions = []

                    # Parse connector/pair from controller name
                    connector = ""
                    trading_pair = ""
                    parts = ctrl_name.split("_")
                    for i, part in enumerate(parts):
                        if "-" in part and part[0].isupper():
                            if not trading_pair:
                                trading_pair = part
                            if not connector and i > 0:
                                connector = "_".join(parts[:i])
                            break

                    total_pnl += global_pnl
                    total_volume += volume

                    controllers.append(
                        {
                            "controller_name": ctrl_name,
                            "controller_id": ctrl_name,
                            "bot_name": bot_name,
                            "status": ctrl_info.get("status", "running"),
                            "connector": connector,
                            "trading_pair": trading_pair,
                            "realized_pnl_quote": realized,
                            "unrealized_pnl_quote": unrealized,
                            "global_pnl_quote": global_pnl,
                            "global_pnl_pct": global_pnl_pct,
                            "volume_traded": volume,
                            "close_type_counts": close_types,
                            "positions_summary": positions,
                            "deployed_at": None,
                            "config": {},
                        }
                    )

            bots.append(
                {
                    "bot_name": bot_name,
                    "status": bot_status,
                    "num_controllers": num_controllers,
                    "error_count": len(error_logs),
                    "deployed_at": None,
                    "error_logs": error_logs[-100:],
                    "general_logs": general_logs[-100:],
                }
            )

        return {
            "controllers": controllers,
            "bots": bots,
            "total_pnl": total_pnl,
            "total_volume": total_volume,
            "server_online": True,
        }

    @staticmethod
    def _overlay_stopping_state(server_name: str, data: dict) -> None:
        """Apply transitional 'stopping' state to WS broadcast data."""
        from condor.web.routes.bots import (
            _stopping_controllers,
            clear_bot_stopping,
            get_stopping_bots,
            get_stopping_controllers,
        )

        stopping_bot_names = get_stopping_bots(server_name)
        stopping_ctrl_keys = get_stopping_controllers(server_name)

        if not stopping_bot_names and not stopping_ctrl_keys:
            return

        active_bot_names = set()
        for bot in data.get("bots", []):
            active_bot_names.add(bot.get("bot_name", ""))
            if bot.get("bot_name") in stopping_bot_names:
                if bot.get("status") in ("running",):
                    bot["status"] = "stopping"
                else:
                    clear_bot_stopping(server_name, bot["bot_name"])

        # Clear stopping entries for bots that disappeared (fully stopped)
        for sbn in list(stopping_bot_names):
            if sbn not in active_bot_names:
                clear_bot_stopping(server_name, sbn)

        for ctrl in data.get("controllers", []):
            key = f"{ctrl.get('bot_name')}:{ctrl.get('controller_id')}"
            if key in stopping_ctrl_keys:
                if ctrl.get("config", {}).get("manual_kill_switch") is True:
                    _stopping_controllers.pop(f"{server_name}:{key}", None)
                else:
                    ctrl["status"] = "stopping"

    def _on_data_update(
        self, server_name: str, cache_key: str, data_type: Any, value: Any
    ) -> None:
        """Called by SDS when cache is updated. Maps to WS channels and broadcasts."""
        dt_name = data_type.name if hasattr(data_type, "name") else str(data_type)
        prefix = _SDT_TO_CHANNEL_PREFIX.get(dt_name)
        if not prefix:
            return

        # Build channel name
        if dt_name in ("CEX_PRICES", "PRICES"):
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

        # Skip SDS-triggered broadcast for channels with active WS streams
        # (the WS stream handler already broadcasts directly)
        if dt_name == "BOTS_STATUS" and channel in self._bots_ws_tasks:
            task = self._bots_ws_tasks.get(channel)
            if task and not task.done():
                return
        if dt_name == "EXECUTORS" and channel in self._executor_tasks:
            task = self._executor_tasks.get(channel)
            if task and not task.done():
                return

        # Transform raw data to match REST endpoint response shapes
        if dt_name == "BOTS_STATUS":
            try:
                value = self._transform_bots(value)
                # Overlay transitional "stopping" state from Condor's in-memory store
                self._overlay_stopping_state(server_name, value)
            except Exception as e:
                logger.debug("Failed to transform bots data for WS: %s", e)
                return

        asyncio.ensure_future(self._broadcast_update(channel, value))

    async def _broadcast_update(self, channel: str, data: Any) -> None:
        prev = self._last_data.get(channel)
        if data != prev:
            await self.broadcast(channel, data)

    # -- Broadcasting --

    async def broadcast(self, channel: str, data: Any) -> None:
        self._last_data[channel] = data
        subscribers = [
            conn for conn in list(self._connections) if channel in conn.channels
        ]
        if not subscribers:
            return
        # Fan out concurrently so a slow/backpressured client does not block the
        # rest of the subscribers in the same broadcast tick.
        results = await asyncio.gather(
            *(self._send(conn, channel, data) for conn in subscribers),
            return_exceptions=True,
        )
        dead: list[_Connection] = []
        for conn, result in zip(subscribers, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Broadcast send failed: channel=%s user=%s: %s",
                    channel,
                    conn.user_id,
                    result,
                )
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)

    async def _send(self, conn: _Connection, channel: str, data: Any) -> None:
        await conn.ws.send_json({"channel": channel, "data": data, "ts": time.time()})

    # -- Candle streaming --

    def _ensure_candle_stream(self, channel: str) -> None:
        self._ensure_stream("candle", channel)

    def _has_subscribers(self, channel: str) -> bool:
        """True if any connection is currently subscribed to the channel."""
        return any(channel in c.channels for c in self._connections)

    # -- Generic stream lifecycle --
    #
    # All 8 stream types share the same start/stop lifecycle; only candle needs
    # a non-uniform stop (deferred teardown with keep-alive) supplied via
    # ``teardown_hook``. The thin ``_ensure_*_stream`` / ``_maybe_stop_*_stream``
    # wrappers delegate to ``_ensure_stream`` / ``_maybe_stop_stream`` so the
    # lifecycle logic lives in one place.
    def _stream_registry(self) -> dict:
        """stream_type -> {task_dict, factory, start_log, stop_log, teardown_hook?}."""
        if self._stream_registry_cache is None:
            self._stream_registry_cache = {
                "candle": {
                    "task_dict": self._candle_tasks,
                    "factory": self._candle_stream,
                    "start_log": "Started candle stream for %s",
                    # Non-uniform stop: deferred teardown with keep-alive.
                    "teardown_hook": self._maybe_stop_candle_stream,
                },
                "trade": {
                    "task_dict": self._trade_tasks,
                    "factory": self._trade_stream,
                    "start_log": "Started trade stream for %s",
                    "stop_log": "Stopped trade stream for %s",
                },
                "order_book": {
                    "task_dict": self._order_book_tasks,
                    "factory": self._order_book_stream,
                    "start_log": "Started order book stream for %s",
                    "stop_log": "Stopped order book stream for %s",
                },
                "executor": {
                    "task_dict": self._executor_tasks,
                    "factory": self._executor_stream,
                    "start_log": "Started executor stream for %s",
                    "stop_log": "Stopped executor stream for %s",
                },
                "bots_ws": {
                    "task_dict": self._bots_ws_tasks,
                    "factory": self._bots_ws_stream,
                    "start_log": "Started bots WS stream for %s",
                    "stop_log": "Stopped bots WS stream for %s",
                },
                "positions_ws": {
                    "task_dict": self._positions_ws_tasks,
                    "factory": self._positions_ws_stream,
                    "start_log": "Started positions WS stream for %s",
                    "stop_log": "Stopped positions WS stream for %s",
                },
                "performance_ws": {
                    "task_dict": self._performance_ws_tasks,
                    "factory": self._performance_ws_stream,
                    "start_log": "Started performance WS stream for %s",
                    "stop_log": "Stopped performance WS stream for %s",
                },
                "controller_perf": {
                    "task_dict": self._controller_perf_tasks,
                    "factory": self._controller_perf_stream,
                    "start_log": "Started controller performance stream for %s",
                    "stop_log": "Stopped controller performance stream for %s",
                },
            }
        return self._stream_registry_cache

    def _ensure_stream(self, stream_type: str, channel: str) -> None:
        spec = self._stream_registry()[stream_type]
        tasks = spec["task_dict"]
        if channel in tasks and not tasks[channel].done():
            return
        tasks[channel] = asyncio.create_task(spec["factory"](channel))
        logger.info(spec["start_log"], channel)

    def _maybe_stop_stream(self, stream_type: str, channel: str) -> None:
        spec = self._stream_registry()[stream_type]
        teardown_hook = spec.get("teardown_hook")
        if teardown_hook is not None:
            teardown_hook(channel)
            return
        if self._has_subscribers(channel):
            return
        task = spec["task_dict"].pop(channel, None)
        if task and not task.done():
            task.cancel()
            logger.info(spec["stop_log"], channel)

    def _maybe_stop_candle_stream(self, channel: str) -> None:
        # If subscribers still exist, cancel any pending teardown and return
        if self._has_subscribers(channel):
            timer = self._candle_teardown_timers.pop(channel, None)
            if timer is not None:
                timer.cancel()
            return

        # Already have a pending teardown scheduled — nothing to do
        if channel in self._candle_teardown_timers:
            return

        # Schedule deferred teardown
        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            self._CANDLE_KEEP_ALIVE,
            self._deferred_stop_candle_stream,
            channel,
        )
        self._candle_teardown_timers[channel] = handle
        logger.info(
            "Scheduled candle stream teardown for %s in %ds",
            channel,
            self._CANDLE_KEEP_ALIVE,
        )

    def _deferred_stop_candle_stream(self, channel: str) -> None:
        """Actually tear down a candle stream after the grace period."""
        self._candle_teardown_timers.pop(channel, None)

        # Re-check: subscribers may have appeared during the grace period
        if self._has_subscribers(channel):
            logger.debug(
                "Candle teardown cancelled — subscribers returned for %s", channel
            )
            return

        task = self._candle_tasks.pop(channel, None)
        if task and not task.done():
            task.cancel()
            logger.info("Deferred candle stream teardown completed for %s", channel)
        poll_task = self._candle_poll_tasks.pop(channel, None)
        if poll_task and not poll_task.done():
            poll_task.cancel()
        self._last_candle_ws_update.pop(channel, None)
        self._candle_first_msg_logged.discard(channel)
        # NOTE: candle buffer is NOT deleted — the existing idle cleanup loop handles that

    async def _candle_stream(self, channel: str) -> None:
        parts = channel.split(":")
        if len(parts) < 5:
            return
        _, server_name, connector, pair, interval = parts

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        # Start REST poll fallback alongside the WS stream
        self._ensure_candle_poll_fallback(channel)

        while True:
            try:
                client = await cm.get_client(server_name)
                async with client.ws.market_data() as ws:
                    await ws.subscribe_candles(
                        connector,
                        pair,
                        interval=interval,
                        max_records=100,
                        update_interval=1.0,
                    )
                    logger.info("Candle WS subscribed: %s", channel)
                    backoff = 5  # Reset on successful connection
                    async for msg in ws:
                        # Log first message per channel at INFO for diagnostics
                        if channel not in self._candle_first_msg_logged:
                            self._candle_first_msg_logged.add(channel)
                            logger.info(
                                "Candle WS first message for %s: %s",
                                channel,
                                (
                                    json.dumps(msg)[:500]
                                    if isinstance(msg, dict)
                                    else type(msg).__name__
                                ),
                            )
                        else:
                            logger.debug(
                                "Candle WS raw msg keys for %s: %s",
                                channel,
                                (
                                    list(msg.keys())
                                    if isinstance(msg, dict)
                                    else type(msg).__name__
                                ),
                            )

                        msg_type = msg.get("type")
                        if msg_type == "candle_update":
                            raw = msg.get("data")
                            candle = self._normalize_candle(raw) if raw else None
                            if candle:
                                self._last_candle_ws_update[channel] = time.monotonic()
                                self._upsert_candle_buffer(channel, candle)
                                await self.broadcast(
                                    channel,
                                    {"type": "candle_update", "candle": candle},
                                )
                        elif msg_type == "candles":
                            raw_list = msg.get("data") or []
                            candles = [
                                c
                                for r in raw_list
                                if (c := self._normalize_candle(r)) is not None
                            ]
                            if candles:
                                self._last_candle_ws_update[channel] = time.monotonic()
                                self._upsert_candle_buffer_many(channel, candles)
                                await self.broadcast(
                                    channel,
                                    {"type": "candles", "data": candles},
                                )
                        elif msg_type in ("heartbeat", "subscribed"):
                            continue
                        elif msg_type == "error":
                            error_msg = msg.get("message", "unknown error")
                            logger.warning(
                                "Candle stream error for %s: %s — continuing",
                                channel,
                                error_msg,
                            )
                            await self.broadcast(
                                channel,
                                {
                                    "type": "error",
                                    "message": f"Stream error: {error_msg}",
                                },
                            )
                            # Don't break — the WS may still be alive.
                            # If truly dead, next recv raises and we reconnect.
                            continue
                        else:
                            logger.info(
                                "Candle stream unrecognized msg type for %s: type=%s keys=%s",
                                channel,
                                msg_type,
                                (
                                    list(msg.keys())
                                    if isinstance(msg, dict)
                                    else type(msg).__name__
                                ),
                            )

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                # Detect permanent failures (auth, not found) — don't retry
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning(
                        "Candle stream permanent error for %s: %s — giving up",
                        channel,
                        e,
                    )
                    await self.broadcast(
                        channel,
                        {"type": "error", "message": f"Stream failed: {error_str}"},
                    )
                    return

                logger.warning(
                    "Candle stream error for %s: %s, reconnecting in %ds...",
                    channel,
                    e,
                    backoff,
                )
                await self.broadcast(
                    channel,
                    {
                        "type": "error",
                        "message": f"Connection lost, retrying in {backoff}s",
                    },
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # -- Candle REST poll fallback --

    def _ensure_candle_poll_fallback(self, channel: str) -> None:
        """Start a REST poll fallback task for a candle channel if not already running."""
        if (
            channel in self._candle_poll_tasks
            and not self._candle_poll_tasks[channel].done()
        ):
            return
        self._candle_poll_tasks[channel] = asyncio.create_task(
            self._candle_poll_fallback(channel)
        )

    async def _candle_poll_fallback(self, channel: str) -> None:
        """Periodically poll REST for candles when WS stream goes silent."""
        parts = channel.split(":")
        if len(parts) < 5:
            return
        _, server_name, connector, pair, interval = parts
        interval_sec = _INTERVAL_SECONDS.get(interval, 60)
        # How long without a WS update before we consider it stale
        stale_threshold = max(interval_sec, 15)
        # How often to poll REST once stale (keep the last candle fresh)
        poll_interval = min(interval_sec, 10)
        was_stale = False

        from config_manager import get_config_manager

        try:
            while True:
                await asyncio.sleep(poll_interval if was_stale else stale_threshold)

                last_update = self._last_candle_ws_update.get(channel)
                if (
                    last_update is not None
                    and (time.monotonic() - last_update) <= stale_threshold
                ):
                    if was_stale:
                        logger.info(
                            "Candle WS stream resumed for %s, stopping REST fallback polling",
                            channel,
                        )
                        was_stale = False
                    continue

                # Stream is stale — poll REST
                if not was_stale:
                    logger.warning(
                        "Candle WS stream appears stale for %s (threshold=%ds), polling REST fallback",
                        channel,
                        stale_threshold,
                    )
                    was_stale = True

                try:
                    cm = get_config_manager()
                    client = await cm.get_client(server_name)
                    now = int(time.time())
                    result = await client.market_data.get_historical_candles(
                        connector,
                        pair,
                        interval,
                        start_time=now - interval_sec * 5,
                        end_time=now,
                    )
                    candles_raw = (
                        result
                        if isinstance(result, list)
                        else result.get("data", []) if isinstance(result, dict) else []
                    )
                    candles = [
                        c
                        for r in candles_raw
                        if (c := self._normalize_candle(r)) is not None
                    ]
                    if candles:
                        buf = self._candle_buffers.get(channel)
                        # Broadcast if we have newer candles OR if the latest
                        # candle's OHLCV changed (same timestamp, updated values)
                        newest_buf_ts = (
                            max(buf._data.keys()) if buf and buf._data else 0
                        )
                        newest_poll_ts = max(c["timestamp"] for c in candles)
                        changed = newest_poll_ts > newest_buf_ts
                        if not changed and buf and newest_poll_ts in buf._data:
                            # Same timestamp — check if OHLCV actually changed
                            old = buf._data[newest_poll_ts]
                            new = next(
                                c for c in candles if c["timestamp"] == newest_poll_ts
                            )
                            changed = any(
                                old.get(k) != new.get(k)
                                for k in ("open", "high", "low", "close", "volume")
                            )
                        if changed:
                            self._upsert_candle_buffer_many(channel, candles)
                            await self.broadcast(
                                channel,
                                {"type": "candles", "data": candles},
                            )
                            logger.debug(
                                "REST fallback delivered %d candles for %s",
                                len(candles),
                                channel,
                            )
                except Exception as e:
                    logger.debug("REST candle poll failed for %s: %s", channel, e)

        except asyncio.CancelledError:
            return

    # -- Trade streaming --

    def _ensure_trade_stream(self, channel: str) -> None:
        self._ensure_stream("trade", channel)

    def _maybe_stop_trade_stream(self, channel: str) -> None:
        self._maybe_stop_stream("trade", channel)

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
                        connector,
                        pair,
                        update_interval=1.0,
                    )
                    logger.info("Trade WS subscribed: %s", channel)
                    backoff = 5
                    async for msg in ws:
                        if not self._has_subscribers(channel):
                            logger.info(
                                "No subscribers for %s, closing trade stream", channel
                            )
                            return

                        msg_type = msg.get("type")
                        if msg_type == "trades":
                            trade_data = msg.get("data", [])
                            trades = []
                            for t in trade_data:
                                if isinstance(t, dict):
                                    trades.append(
                                        {
                                            "price": float(t.get("price", 0)),
                                            "amount": float(
                                                t.get("amount", t.get("quantity", 0))
                                            ),
                                            "side": t.get(
                                                "side", t.get("trade_type", "buy")
                                            ).lower(),
                                            "timestamp": float(t.get("timestamp", 0)),
                                        }
                                    )
                            if trades:
                                await self.broadcast(
                                    channel,
                                    {"type": "trades", "data": trades},
                                )
                        elif msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            error_msg = msg.get("message", "unknown error")
                            logger.warning(
                                "Trade stream error for %s: %s", channel, error_msg
                            )
                            break

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning(
                        "Trade stream permanent error for %s: %s — giving up",
                        channel,
                        e,
                    )
                    return

                logger.warning(
                    "Trade stream error for %s: %s, reconnecting in %ds...",
                    channel,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # -- Candle buffer helpers --

    def _get_or_create_candle_buffer(self, channel: str) -> _CandleBuffer:
        """Get existing buffer or create with default duration."""
        buf = self._candle_buffers.get(channel)
        if buf is None:
            parts = channel.split(":")
            interval = parts[4] if len(parts) >= 5 else "1m"
            buf = _CandleBuffer(interval, 3 * 86400)  # default 3 days
            self._candle_buffers[channel] = buf
        return buf

    def _upsert_candle_buffer(self, channel: str, candle: dict) -> None:
        """Upsert a single candle into the per-channel buffer."""
        buf = self._get_or_create_candle_buffer(channel)
        buf.upsert(candle)

    def _upsert_candle_buffer_many(self, channel: str, candles: list[dict]) -> None:
        """Upsert multiple candles into the per-channel buffer."""
        buf = self._get_or_create_candle_buffer(channel)
        buf.upsert_many(candles)

    async def _candle_buffer_cleanup_loop(self) -> None:
        """Periodically remove candle buffers that haven't been accessed."""
        try:
            while True:
                await asyncio.sleep(60)  # check every minute
                now = time.monotonic()
                stale = [
                    ch
                    for ch, buf in self._candle_buffers.items()
                    if (now - buf.last_accessed) > _CANDLE_BUFFER_IDLE_TTL
                    and not any(ch in c.channels for c in self._connections)
                ]
                for ch in stale:
                    buf = self._candle_buffers.pop(ch, None)
                    if buf:
                        logger.info(
                            "Cleaned up idle candle buffer: %s (%d candles)",
                            ch,
                            buf.size,
                        )
        except asyncio.CancelledError:
            return

    # -- Order book streaming --

    def _ensure_order_book_stream(self, channel: str) -> None:
        self._ensure_stream("order_book", channel)

    def _maybe_stop_order_book_stream(self, channel: str) -> None:
        self._maybe_stop_stream("order_book", channel)

    async def _order_book_stream(self, channel: str) -> None:
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
                    await ws.subscribe_order_book(
                        connector,
                        pair,
                        depth=20,
                        update_interval=1.0,
                    )
                    logger.info("Order book WS subscribed: %s", channel)
                    backoff = 5
                    async for msg in ws:
                        if not self._has_subscribers(channel):
                            logger.info(
                                "No subscribers for %s, closing order book stream",
                                channel,
                            )
                            return

                        msg_type = msg.get("type")
                        if msg_type == "order_book":
                            raw_data = msg.get("data", {})
                            bids = []
                            asks = []
                            for b in raw_data.get("bids") or []:
                                if isinstance(b, dict):
                                    bids.append(
                                        {
                                            "price": float(b.get("price", 0)),
                                            "amount": float(
                                                b.get("amount", b.get("quantity", 0))
                                            ),
                                        }
                                    )
                                elif isinstance(b, (list, tuple)) and len(b) >= 2:
                                    bids.append(
                                        {"price": float(b[0]), "amount": float(b[1])}
                                    )
                            for a in raw_data.get("asks") or []:
                                if isinstance(a, dict):
                                    asks.append(
                                        {
                                            "price": float(a.get("price", 0)),
                                            "amount": float(
                                                a.get("amount", a.get("quantity", 0))
                                            ),
                                        }
                                    )
                                elif isinstance(a, (list, tuple)) and len(a) >= 2:
                                    asks.append(
                                        {"price": float(a[0]), "amount": float(a[1])}
                                    )
                            ob_data = {"bids": bids, "asks": asks}
                            await self.broadcast(channel, ob_data)
                        elif msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            error_msg = msg.get("message", "unknown error")
                            logger.warning(
                                "Order book stream error for %s: %s", channel, error_msg
                            )
                            break

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning(
                        "Order book stream permanent error for %s: %s — giving up",
                        channel,
                        e,
                    )
                    return

                logger.warning(
                    "Order book stream error for %s: %s, reconnecting in %ds...",
                    channel,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # -- Executor streaming (via Hummingbot WS) --

    def _ensure_executor_stream(self, channel: str) -> None:
        self._ensure_stream("executor", channel)

    def _maybe_stop_executor_stream(self, channel: str) -> None:
        self._maybe_stop_stream("executor", channel)

    async def _executor_stream(self, channel: str) -> None:
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        # Try SDS cache first (pre-warmed by auto_subscribe_servers or REST prefetch)
        from condor.server_data_service import ServerDataType, get_server_data_service

        if channel not in self._last_data:
            sds = get_server_data_service()
            cached = sds.get(server_name, ServerDataType.EXECUTORS)
            if cached is not None:
                executors = self._transform_executors(cached)
                if executors:
                    await self.broadcast(channel, executors)
                    logger.info(
                        "Executor SDS cache hit: %d executors for %s",
                        len(executors),
                        channel,
                    )

        # Wait briefly for SDS to be populated by a concurrent REST request
        # (usePrefetchData fires getExecutors which calls get_or_fetch on SDS)
        if channel not in self._last_data:
            sds = get_server_data_service()
            for _ in range(6):  # up to 3 seconds
                await asyncio.sleep(0.5)
                cached = sds.get(server_name, ServerDataType.EXECUTORS)
                if cached is not None:
                    executors = self._transform_executors(cached)
                    if executors:
                        await self.broadcast(channel, executors)
                        logger.info(
                            "Executor SDS cache populated during wait: %d executors for %s",
                            len(executors),
                            channel,
                        )
                    break

        # Progressive pre-fetch only if we still have no data
        if channel not in self._last_data:
            try:
                from condor.fetchers.executors import (
                    extract_executors_list as _extract_executors_list,
                )

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
                            page_num,
                            len(page),
                            len(executors),
                            channel,
                        )

                    # Determine next cursor
                    next_cursor = None
                    if isinstance(result, dict):
                        next_cursor = result.get("next_cursor") or result.get("cursor")
                        pagination = result.get("pagination")
                        if not next_cursor and isinstance(pagination, dict):
                            next_cursor = pagination.get(
                                "next_cursor"
                            ) or pagination.get("cursor")
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
                    got_message = False
                    async for msg in ws:
                        if not self._has_subscribers(channel):
                            logger.info(
                                "No subscribers for %s, closing executor stream",
                                channel,
                            )
                            return

                        got_message = True
                        msg_type = msg.get("type")
                        if msg_type == "executors":
                            raw_data = msg.get("data", [])
                            executors = self._transform_executors(raw_data)
                            await self._broadcast_update(channel, executors)
                        elif msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            error_msg = msg.get("message", "unknown error")
                            logger.warning(
                                "Executor stream error for %s: %s", channel, error_msg
                            )
                            break

                    # Connection closed cleanly — apply backoff if it was short-lived
                    if got_message:
                        backoff = 5
                    else:
                        logger.warning(
                            "Executor stream closed immediately for %s, reconnecting in %ds...",
                            channel,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60)

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning(
                        "Executor stream permanent error for %s: %s — giving up",
                        channel,
                        e,
                    )
                    return

                logger.warning(
                    "Executor stream error for %s: %s, reconnecting in %ds...",
                    channel,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # -- Bots WS streaming (via Hummingbot /ws/executors all_bots_status) --

    def _ensure_bots_ws_stream(self, channel: str) -> None:
        self._ensure_stream("bots_ws", channel)

    def _maybe_stop_bots_ws_stream(self, channel: str) -> None:
        self._maybe_stop_stream("bots_ws", channel)

    async def _bots_ws_stream(self, channel: str) -> None:
        """Stream all_bots_status from Hummingbot /ws/executors and update SDS cache."""
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        # Send SDS-cached bots data as initial snapshot
        if channel not in self._last_data:
            from condor.server_data_service import (
                ServerDataType,
                get_server_data_service,
            )

            sds = get_server_data_service()
            cached = sds.get(server_name, ServerDataType.BOTS_STATUS)
            if cached is not None:
                try:
                    data = self._transform_bots(cached)
                    await self.broadcast(channel, data)
                except Exception as e:
                    logger.debug("Failed to send initial bots snapshot: %s", e)

        while True:
            try:
                client = await cm.get_client(server_name)
                async with client.ws.executors() as ws:
                    # all_bots_status is not in the client library, send raw
                    await ws._send(
                        {
                            "action": "subscribe",
                            "type": "all_bots_status",
                            "update_interval": 5.0,
                        }
                    )
                    resp = await ws._receive()
                    if resp.get("type") == "error":
                        raise RuntimeError(f"Subscribe failed: {resp.get('message')}")

                    logger.info("Bots WS subscribed: %s", channel)
                    backoff = 5
                    async for msg in ws:
                        if not self._has_subscribers(channel):
                            logger.info(
                                "No subscribers for %s, closing bots WS stream", channel
                            )
                            return

                        msg_type = msg.get("type")
                        if msg_type == "all_bots_status":
                            raw_data = msg.get("data", {})
                            # Update SDS cache so REST and Telegram benefit
                            from condor.server_data_service import (
                                ServerDataType,
                                get_server_data_service,
                            )

                            get_server_data_service().put(
                                server_name, ServerDataType.BOTS_STATUS, raw_data
                            )
                            try:
                                data = self._transform_bots(raw_data)
                                self._overlay_stopping_state(server_name, data)
                                await self._broadcast_update(channel, data)
                            except Exception as e:
                                logger.debug("Failed to transform bots WS data: %s", e)
                        elif msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            logger.warning(
                                "Bots WS stream error for %s: %s",
                                channel,
                                msg.get("message"),
                            )
                            break

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning(
                        "Bots WS stream permanent error for %s: %s — giving up",
                        channel,
                        e,
                    )
                    return
                logger.warning(
                    "Bots WS stream error for %s: %s, reconnecting in %ds...",
                    channel,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # -- Positions WS streaming (via Hummingbot /ws/executors positions) --

    def _ensure_positions_ws_stream(self, channel: str) -> None:
        self._ensure_stream("positions_ws", channel)

    def _maybe_stop_positions_ws_stream(self, channel: str) -> None:
        self._maybe_stop_stream("positions_ws", channel)

    async def _positions_ws_stream(self, channel: str) -> None:
        """Stream positions from Hummingbot /ws/executors and update SDS cache."""
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        while True:
            try:
                client = await cm.get_client(server_name)
                async with client.ws.executors() as ws:
                    await ws.subscribe_positions(update_interval=5.0)
                    logger.info("Positions WS subscribed: %s", channel)
                    backoff = 5
                    async for msg in ws:
                        if not self._has_subscribers(channel):
                            logger.info(
                                "No subscribers for %s, closing positions WS stream",
                                channel,
                            )
                            return

                        msg_type = msg.get("type")
                        if msg_type == "positions":
                            raw_data = msg.get("data", [])
                            # Update SDS cache
                            from condor.server_data_service import (
                                ServerDataType,
                                get_server_data_service,
                            )

                            get_server_data_service().put(
                                server_name, ServerDataType.POSITIONS, raw_data
                            )
                            await self._broadcast_update(channel, raw_data)
                        elif msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            logger.warning(
                                "Positions WS stream error for %s: %s",
                                channel,
                                msg.get("message"),
                            )
                            break

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning(
                        "Positions WS stream permanent error for %s: %s — giving up",
                        channel,
                        e,
                    )
                    return
                logger.warning(
                    "Positions WS stream error for %s: %s, reconnecting in %ds...",
                    channel,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # -- Performance WS streaming (via Hummingbot /ws/executors performance) --

    def _ensure_performance_ws_stream(self, channel: str) -> None:
        self._ensure_stream("performance_ws", channel)

    def _maybe_stop_performance_ws_stream(self, channel: str) -> None:
        self._maybe_stop_stream("performance_ws", channel)

    async def _performance_ws_stream(self, channel: str) -> None:
        """Stream performance from Hummingbot /ws/executors and update SDS cache."""
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        while True:
            try:
                client = await cm.get_client(server_name)
                async with client.ws.executors() as ws:
                    await ws.subscribe_performance(update_interval=5.0)
                    logger.info("Performance WS subscribed: %s", channel)
                    backoff = 5
                    async for msg in ws:
                        if not self._has_subscribers(channel):
                            logger.info(
                                "No subscribers for %s, closing performance WS stream",
                                channel,
                            )
                            return

                        msg_type = msg.get("type")
                        if msg_type == "performance":
                            raw_data = msg.get("data", {})
                            await self._broadcast_update(channel, raw_data)
                        elif msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            logger.warning(
                                "Performance WS stream error for %s: %s",
                                channel,
                                msg.get("message"),
                            )
                            break

            except asyncio.CancelledError:
                return
            except Exception as e:
                error_str = str(e)
                is_permanent = any(code in error_str for code in ("401", "403", "404"))
                if is_permanent:
                    logger.warning(
                        "Performance WS stream permanent error for %s: %s — giving up",
                        channel,
                        e,
                    )
                    return
                logger.warning(
                    "Performance WS stream error for %s: %s, reconnecting in %ds...",
                    channel,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # -- Controller Performance polling stream --

    def _ensure_controller_perf_stream(self, channel: str) -> None:
        self._ensure_stream("controller_perf", channel)

    def _maybe_stop_controller_perf_stream(self, channel: str) -> None:
        self._maybe_stop_stream("controller_perf", channel)

    async def _controller_perf_stream(self, channel: str) -> None:
        """Poll latest controller performance every 30s and broadcast snapshots."""
        parts = channel.split(":")
        if len(parts) < 2:
            return
        server_name = parts[1]

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        while True:
            try:
                if not self._has_subscribers(channel):
                    logger.info(
                        "No subscribers for %s, stopping controller perf stream",
                        channel,
                    )
                    self._controller_perf_tasks.pop(channel, None)
                    return

                client = await cm.get_client(server_name)
                result = (
                    await client.bot_orchestration.get_latest_controller_performance()
                )

                # Normalize to list of snapshots
                snapshots = []
                if isinstance(result, list):
                    snapshots = result
                elif isinstance(result, dict):
                    data = result.get(
                        "data", result.get("snapshots", result.get("records", []))
                    )
                    if isinstance(data, list):
                        snapshots = data
                    elif isinstance(data, dict):
                        for key, val in data.items():
                            if isinstance(val, dict):
                                val.setdefault("controller_id", key)
                                snapshots.append(val)

                if snapshots:
                    await self._broadcast_update(channel, {"snapshots": snapshots})
                    backoff = 5

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(
                    "Controller perf stream error for %s: %s, retrying in %ds...",
                    channel,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)


# -- Singleton --

_instance: Optional[WebSocketManager] = None


def get_ws_manager() -> WebSocketManager:
    global _instance
    if _instance is None:
        _instance = WebSocketManager()
    return _instance
