"""WebSocket connection registry and channel broadcaster for the dashboard.

The upstream stream implementations live in ``condor.web.streams`` (candles in
``streams.candles``, the Hummingbot-bridged protocols in
``streams.hummingbot_ws``) as mixins over the small host surface this module
implements — see ``condor.web.streams.StreamHost``. ``WebSocketManager`` keeps
only connection/subscription bookkeeping, the SDS bridge, broadcasting and the
stream lifecycle registry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Optional

from fastapi import WebSocket

# Re-exported for back-compat: tests and callers import the candle helpers
# (and patch ``ws_manager.dex_candles``) through this module.
from condor import dex_candles  # noqa: F401
from condor.asyncutil import TaskSet
from condor.web.auth import decode_jwt
from condor.web.streams.candles import (  # noqa: F401
    CandleStreamsMixin,
    _CandleBuffer,
    _coerce_duration,
    parse_candle_channel,
)
from condor.web.streams.hummingbot_ws import HummingbotStreamsMixin

if TYPE_CHECKING:
    from condor.server_data_service import CacheKey

logger = logging.getLogger(__name__)

# Mapping from WS channel prefix to ServerDataType
# NOTE: executors now uses dedicated WS streaming (not SDS polling)
_CHANNEL_TO_SDT = {
    "portfolio": "PORTFOLIO",
    "bots": "BOTS_STATUS",
    "prices": "PRICES",
}

# Reverse mapping, for naming the channel a cache write belongs to
_SDT_TO_CHANNEL_PREFIX = {
    "PORTFOLIO": "portfolio",
    "BOTS_STATUS": "bots",
    "PRICES": "prices",
}

# CacheKey params a data type's channel is scoped by, in channel-segment order.
# A data type absent from this table has a server-wide channel (`{prefix}:{server}`)
# and its key must carry no params at all — an account-scoped portfolio or any
# other parameterized variant is a *different* dataset and must not be broadcast
# to the server-wide channel. This is the single source of truth for both
# directions: channel -> key params when subscribing, key -> channel when
# broadcasting.
_CHANNEL_KEY_PARAMS: dict[str, tuple[str, ...]] = {
    "PRICES": ("connector_name", "trading_pair"),
}


def channel_for_key(key: CacheKey) -> str | None:
    """WS channel a written SDS ``CacheKey`` belongs to, or ``None`` if none does.

    ``None`` means "no channel carries this data" (a data type the dashboard does
    not stream, e.g. portfolio history, or a parameterized variant of a
    server-wide type) — never a silent drop of data a channel was waiting for:
    a key that names a channel but is missing the params to address it is logged.
    """
    prefix = _SDT_TO_CHANNEL_PREFIX.get(key.data_type.name)
    if not prefix:
        return None

    params = key.params_dict
    param_names = _CHANNEL_KEY_PARAMS.get(key.data_type.name, ())
    if set(params) != set(param_names):
        logger.debug(
            "No %s channel for key with params %s (expected %s)",
            prefix,
            sorted(params),
            list(param_names),
        )
        return None

    segments = [params[name] for name in param_names]
    return ":".join([prefix, key.server, *segments])


class _Connection:
    __slots__ = ("ws", "user_id", "channels")

    def __init__(self, ws: WebSocket, user_id: int):
        self.ws = ws
        self.user_id = user_id
        self.channels: set[str] = set()


class WebSocketManager(CandleStreamsMixin, HummingbotStreamsMixin):
    """Manages WebSocket connections and channel-based data broadcasting.

    Subscribes to ServerDataService for data updates and broadcasts
    to connected WebSocket clients. The upstream stream protocols are
    supplied by the mixins (``CandleStreamsMixin``,
    ``HummingbotStreamsMixin``); this class owns their shared state and
    the host surface they run on (``broadcast``, ``_send``,
    ``_has_subscribers``, ``_ensure_stream``, ``_oneshot_tasks``).
    """

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
        self._oneshot_tasks = TaskSet(logger, "One-shot task %s failed: %s")
        # Lazily-built registry of per-type stream lifecycles (see _stream_registry)
        self._stream_registry_cache: dict | None = None

    # -- Helpers --

    @staticmethod
    def _server_from_channel(channel: str) -> str | None:
        """Server name encoded as the second segment of a channel
        (``portfolio:<server>``, ``bots_ws:<server>``, ``candles:<server>:...``,
        ``prices:<server>:<connector>:<pair>``, ...). Every WS channel is
        server-scoped; returns None when no server segment is present."""
        parts = channel.split(":")
        return parts[1] if len(parts) >= 2 and parts[1] else None

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

        # Cancel every registered stream task (all 8 stream types), plus the
        # candle REST poll fallbacks which live outside the registry.
        task_dicts = [spec["task_dict"] for spec in self._stream_registry().values()]
        task_dicts.append(self._candle_poll_tasks)
        for tasks in task_dicts:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            tasks.clear()
        self._last_candle_ws_update.clear()
        self._candle_first_msg_logged.clear()

        # Cancel any still-pending one-shot tasks.
        self._oneshot_tasks.cancel_all()

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            self._cleanup_task = None

        self._last_data.clear()

    def _cleanup_sds_subscriptions(self) -> None:
        """Remove all SDS subscriptions."""
        from condor.server_data_service import get_server_data_service

        sds = get_server_data_service()
        sds.unsubscribe_all("ws_manager")
        self._sds_subscriptions.clear()

    # -- Connection handling --

    async def connect(
        self, ws: WebSocket, token: Optional[str], subprotocol: Optional[str] = None
    ) -> Optional[_Connection]:
        payload = decode_jwt(token) if token else None
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

        await ws.accept(subprotocol=subprotocol)
        conn = _Connection(ws, user_id)
        self._connections.append(conn)
        logger.info("WS connected: user %s", user_id)
        return conn

    def disconnect(self, conn: _Connection) -> None:
        if conn in self._connections:
            self._connections.remove(conn)
            logger.info("WS disconnected: user %s", conn.user_id)
            for channel in list(conn.channels):
                prefix = channel.split(":", 1)[0]
                if prefix in self._stream_registry():
                    self._maybe_stop_stream(prefix, channel)
                else:
                    self._maybe_unsub_sds(channel)

    def _maybe_unsub_sds(self, channel: str) -> None:
        """Unsubscribe from SDS if no WS clients remain for this channel."""
        if self._has_subscribers(channel):
            return

        # Drop the retained last payload — re-subscribers get a fresh snapshot
        # from the SDS cache when the subscription restarts.
        self._last_data.pop(channel, None)

        if channel in self._sds_subscriptions:
            from condor.server_data_service import get_server_data_service

            sds = get_server_data_service()
            cache_key = self._sds_subscriptions.pop(channel)
            sds.unsubscribe(cache_key, "ws_manager")
            logger.debug("WS unsubscribed SDS for channel %s", channel)

            # The history ranges ride on this subscription — drop them too, or
            # their polls would outlive the last dashboard client watching.
            if channel.startswith("portfolio:"):
                server_name = channel.split(":")[1] if ":" in channel else ""
                if server_name:
                    self._unsubscribe_portfolio_history(server_name)

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
            prefix = channel.split(":", 1)[0]
            if prefix == "candles":
                # Candles are special: snapshot comes from the candle buffer
                # (not _last_data) and the subscribe carries a duration param.
                duration = msg.get("duration")  # seconds, sent by frontend
                await self._handle_candle_subscribe(conn, channel, duration)
            elif prefix in self._stream_registry():
                # Send last known data immediately, then ensure the stream runs
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                self._ensure_stream(prefix, channel)
            else:
                if channel in self._last_data:
                    await self._send(conn, channel, self._last_data[channel])
                await self._subscribe_sds(channel)

        elif action == "unsubscribe" and channel:
            conn.channels.discard(channel)
            prefix = channel.split(":", 1)[0]
            if prefix in self._stream_registry():
                self._maybe_stop_stream(prefix, channel)
            else:
                self._maybe_unsub_sds(channel)

        elif action == "set_candle_duration" and channel:
            # Frontend changed duration without re-subscribing
            duration = _coerce_duration(msg.get("duration"))
            if channel.startswith("candles:") and duration:
                await self._handle_candle_duration_change(conn, channel, duration)

    # -- SDS bridge --

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

        # Params the channel scopes its key by, read off the channel segments
        # that follow the server (same table the broadcast direction uses).
        param_names = _CHANNEL_KEY_PARAMS.get(sdt_name, ())
        if len(parts) < 2 + len(param_names):
            return
        params = dict(zip(param_names, parts[2:]))

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

            # Portfolio history rides on the portfolio subscription: one SDS
            # key per range, primed now and polled until the last client leaves.
            if prefix == "portfolio":
                self._oneshot_tasks.track(
                    asyncio.create_task(
                        self._subscribe_portfolio_history(channel, server_name),
                        name=f"portfolio_history:{server_name}",
                    )
                )
        except Exception as e:
            logger.debug("Failed to subscribe SDS for %s: %s", channel, e)

    async def _subscribe_portfolio_history(
        self, channel: str, server_name: str
    ) -> None:
        """Subscribe one SDS key per history range behind a portfolio channel.

        Runs off the subscribe path as a one-shot task: SDS primes each key with
        a real backend fetch and the WS client must not wait for four of them.
        Subscribing is idempotent per (key, subscriber), so a second client on
        the same channel never starts a second poll. If the channel is torn down
        while the priming is in flight, unsubscribe again — otherwise the poll
        would outlive its last subscriber.
        """
        from condor.fetchers.portfolio import PORTFOLIO_HISTORY_RANGES
        from condor.server_data_service import ServerDataType, get_server_data_service

        sds = get_server_data_service()

        async def _sub(range_key: str) -> None:
            try:
                await sds.subscribe(
                    server=server_name,
                    data_type=ServerDataType.PORTFOLIO_HISTORY,
                    subscriber_id="ws_manager",
                    range_key=range_key,
                )
            except Exception as e:
                logger.debug(
                    "Failed to subscribe portfolio history %s/%s: %s",
                    server_name,
                    range_key,
                    e,
                )

        await asyncio.gather(*[_sub(rk) for rk in PORTFOLIO_HISTORY_RANGES])

        if channel not in self._sds_subscriptions:
            self._unsubscribe_portfolio_history(server_name)

    def _unsubscribe_portfolio_history(self, server_name: str) -> None:
        """Drop this manager's history subscriptions for a server.

        SDS stops polling a key once it has no subscribers left, so this is what
        ends the history refresh.
        """
        from condor.fetchers.portfolio import PORTFOLIO_HISTORY_RANGES
        from condor.server_data_service import (
            CacheKey,
            ServerDataType,
            get_server_data_service,
        )

        sds = get_server_data_service()
        for range_key in PORTFOLIO_HISTORY_RANGES:
            sds.unsubscribe(
                CacheKey.make(
                    server_name,
                    ServerDataType.PORTFOLIO_HISTORY,
                    range_key=range_key,
                ),
                "ws_manager",
            )

    # -- SDS listener --

    def _on_data_update(self, key: CacheKey, value: Any) -> None:
        """Called by SDS on every cache write. Maps the key to a WS channel and
        broadcasts. ``key`` is an SDS ``CacheKey``: server, data type and params."""
        channel = channel_for_key(key)
        if channel is None:
            return

        dt_name = key.data_type.name
        server_name = key.server

        has_subscribers = any(channel in conn.channels for conn in self._connections)
        if not has_subscribers:
            return

        # Skip SDS-triggered broadcast for channels with active WS streams
        # (the WS stream handler already broadcasts directly)
        if dt_name == "BOTS_STATUS" and channel in self._bots_ws_tasks:
            task = self._bots_ws_tasks.get(channel)
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

        self._oneshot_tasks.track(
            asyncio.create_task(
                self._broadcast_update(channel, value),
                name=f"broadcast:{channel}",
            )
        )

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
        # The frame is identical for every subscriber (no per-connection state
        # goes into it), so encode it once instead of once per connection: the
        # hot channels re-broadcast the whole executor/bots list every couple of
        # seconds to every dashboard tab on the same shared event loop.
        try:
            payload = self._encode(channel, data)
        except Exception as e:
            # Previously a serialization failure surfaced inside ``_send`` and
            # was absorbed by ``return_exceptions=True``; with a single up-front
            # encode it would escape and kill the calling stream task.
            logger.warning("Broadcast encode failed: channel=%s: %s", channel, e)
            return
        # Fan out concurrently so a slow/backpressured client does not block the
        # rest of the subscribers in the same broadcast tick.
        results = await asyncio.gather(
            *(conn.ws.send_text(payload) for conn in subscribers),
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

    @staticmethod
    def _encode(channel: str, data: Any) -> str:
        """Serialize one frame exactly as Starlette's ``send_json`` would.

        Same separators and ``ensure_ascii`` as ``WebSocket.send_json`` so the
        bytes on the wire are unchanged, and deliberately no ``default=str``:
        payloads that fail to serialize today must keep failing fast.
        """
        return json.dumps(
            {"channel": channel, "data": data, "ts": time.time()},
            separators=(",", ":"),
            ensure_ascii=False,
        )

    async def _send(self, conn: _Connection, channel: str, data: Any) -> None:
        """Send one frame to a single connection (initial snapshots)."""
        await conn.ws.send_text(self._encode(channel, data))

    # -- Generic stream lifecycle --

    def _has_subscribers(self, channel: str) -> bool:
        """True if any connection is currently subscribed to the channel."""
        return any(channel in c.channels for c in self._connections)

    # All 8 stream types share the same start/stop lifecycle; only candle needs
    # a non-uniform stop (deferred teardown with keep-alive) supplied via
    # ``teardown_hook``. The registry is keyed by channel prefix so that
    # subscribe/unsubscribe/disconnect resolve a stream with one lookup.
    def _stream_registry(self) -> dict:
        """channel prefix -> {task_dict, factory, start_log, stop_log, teardown_hook?}."""
        if self._stream_registry_cache is None:
            self._stream_registry_cache = {
                "candles": {
                    "task_dict": self._candle_tasks,
                    "factory": self._candle_stream,
                    "start_log": "Started candle stream for %s",
                    # Non-uniform stop: deferred teardown with keep-alive.
                    "teardown_hook": self._maybe_stop_candle_stream,
                },
                "trades": {
                    "task_dict": self._trade_tasks,
                    "factory": self._trade_stream,
                    "start_log": "Started trade stream for %s",
                    "stop_log": "Stopped trade stream for %s",
                },
                "orderbook": {
                    "task_dict": self._order_book_tasks,
                    "factory": self._order_book_stream,
                    "start_log": "Started order book stream for %s",
                    "stop_log": "Stopped order book stream for %s",
                },
                "executors": {
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

    def _ensure_stream(self, prefix: str, channel: str) -> None:
        spec = self._stream_registry()[prefix]
        tasks = spec["task_dict"]
        if channel in tasks and not tasks[channel].done():
            return
        tasks[channel] = asyncio.create_task(spec["factory"](channel))
        logger.info(spec["start_log"], channel)

    def _maybe_stop_stream(self, prefix: str, channel: str) -> None:
        spec = self._stream_registry()[prefix]
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
        self._last_data.pop(channel, None)


# -- Singleton --

_instance: Optional[WebSocketManager] = None


def get_ws_manager() -> WebSocketManager:
    global _instance
    if _instance is None:
        _instance = WebSocketManager()
    return _instance
