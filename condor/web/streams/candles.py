"""Candle streaming subsystem: buffer, backend WS stream, REST poll fallback,
historical backfill and idle-buffer cleanup.

``CandleStreamsMixin`` is mixed into ``WebSocketManager``, which provides the
host surface: it calls the host's ``broadcast`` / ``_send`` / ``_has_subscribers`` / ``_ensure_stream`` and
tracks its fire-and-forget work on the host's ``_oneshot_tasks``. The candle
state it drives (``_candle_buffers``, ``_candle_tasks``,
``_candle_poll_tasks``, teardown timers and staleness bookkeeping) is
initialized by the host's ``__init__``.

Only this module touches ``_CandleBuffer`` internals; everything else goes
through its accessors.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import TYPE_CHECKING, Any

from condor import dex_candles
from condor.asyncutil import TaskSet
from condor.fetchers.market_data import fetch_historical_candles, normalize_candle

if TYPE_CHECKING:
    from condor.web.ws_manager import _Connection

logger = logging.getLogger(__name__)

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

# A forming candle is worth refreshing a handful of times over its life — more
# than that redraws the same bar. Five is the target.
_GECKO_POLLS_PER_CANDLE = 5
# Never faster than this on a 1m chart, and never slower than this on a coarse
# one: a chart that has not moved in three minutes reads as broken even when the
# candle it is filling is an hour long.
_GECKO_POLL_MIN = 30
_GECKO_POLL_MAX = 180


def _gecko_poll_interval(interval_sec: int) -> int:
    """How often to re-fetch a GeckoTerminal-backed candle channel, in seconds.

    Every open pool chart polls upstream forever, bypassing the OHLCV cache so it
    sees the forming candle, and they all draw on one shared per-minute
    GeckoTerminal budget (``_GECKO_RATE_LIMIT`` in ``condor/pool_data.py``). So
    the poll rate has to scale with the candle: a 15m chart refreshed every
    minute spends fifteen requests per candle to redraw the same bar, which is
    exactly the budget a second trader's chart then cannot have.

    Scaling it is also what makes a coarser interval *cost* less — the reason to
    default a pool chart to 15m rather than 5m in the first place.
    """
    return max(
        _GECKO_POLL_MIN, min(interval_sec // _GECKO_POLLS_PER_CANDLE, _GECKO_POLL_MAX)
    )


# Auto-cleanup candle buffers unused for this long
_CANDLE_BUFFER_IDLE_TTL = 600  # 10 minutes


def _coerce_duration(value: object) -> int | None:
    """Coerce a client-supplied candle duration to a positive int (seconds).

    WS messages are untrusted input: a non-numeric value must not raise, or the
    ValueError would escape handle_message and tear down the client's entire
    multiplexed connection. Returns None for missing/invalid/non-positive values.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        duration = int(float(value))
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid candle duration from client: %r", value)
        return None
    return duration if duration > 0 else None


def parse_candle_channel(channel: str) -> tuple[str, str, str, str, str | None] | None:
    """Split a candle channel into its parts, or ``None`` if it is malformed.

    The channel is ``candles:{server}:{connector}:{pair}:{interval}[:{pool_address}]``.
    The pool address is an **optional sixth segment**, set by the DEX page so a
    chart is pinned to the exact pool the user opened instead of whichever pool
    ``dex_candles._resolve_pool`` judges deepest for the pair. A five-part channel
    (every CLOB chart) parses to ``pool_address=None`` and is byte-identical to
    what it was before the segment existed.

    Every consumer must go through here: unpacking five names off ``split(":")``
    raises ``ValueError`` on a six-part channel, and each of the three call sites
    fails silently in a different way (a stream that never backfills, a backfill
    that never ticks).
    """
    parts = channel.split(":")
    if len(parts) < 5:
        return None
    server_name, connector, pair, interval = parts[1:5]
    pool_address = parts[5] if len(parts) > 5 else None
    return server_name, connector, pair, interval, pool_address or None


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

    def get(self, timestamp: float) -> dict | None:
        """The buffered candle at ``timestamp``, or ``None``."""
        return self._data.get(timestamp)

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
    def latest_timestamp(self) -> float:
        """Timestamp of the newest buffered candle, or 0 when empty."""
        return max(self._data) if self._data else 0

    @property
    def needs_backfill(self) -> bool:
        """True if buffer has room for significantly more candles."""
        return len(self._data) < self._max_size * 0.5


class CandleStreamsMixin:
    """Candle stream family: subscribe handling, backfill, backend WS stream,
    REST poll fallback, deferred teardown and idle-buffer cleanup."""

    _CANDLE_KEEP_ALIVE = 300  # 5-minute grace period before tearing down candle streams

    if TYPE_CHECKING:
        # Candle state, initialized by WebSocketManager.__init__.
        _candle_tasks: dict[str, asyncio.Task]
        _candle_poll_tasks: dict[str, asyncio.Task]
        _candle_buffers: dict[str, _CandleBuffer]
        _candle_teardown_timers: dict[str, asyncio.TimerHandle]
        _last_candle_ws_update: dict[str, float]
        _candle_first_msg_logged: set[str]
        _connections: list[Any]
        # Host surface, provided by WebSocketManager.
        _last_data: dict[str, Any]
        _oneshot_tasks: TaskSet

        async def broadcast(self, channel: str, data: Any) -> None: ...

        async def _send(self, conn: Any, channel: str, data: Any) -> None: ...

        def _has_subscribers(self, channel: str) -> bool: ...

        def _ensure_stream(self, prefix: str, channel: str) -> None: ...

        # Provided by HummingbotStreamsMixin on the composed manager.
        @staticmethod
        def _is_permanent_ws_error(error_str: str) -> bool: ...

    @staticmethod
    def _normalize_candle(c: Any) -> dict | None:
        """Normalize a candle from any format to a uniform dict with float values."""
        return normalize_candle(c)

    # -- Subscribe handling --

    async def _handle_candle_subscribe(
        self, conn: "_Connection", channel: str, duration: object
    ) -> None:
        """Handle candle channel subscription with optional duration."""
        parts = channel.split(":")
        if len(parts) < 5:
            return
        interval = parts[4]
        dur = _coerce_duration(duration) or 3 * 86400  # default 3 days

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
                    self._oneshot_tasks.track(
                        asyncio.create_task(
                            self._backfill_candles(channel),
                            name=f"backfill:{channel}",
                        )
                    )

        # Send buffered candles as initial snapshot
        sorted_candles = buf.get_sorted()
        if sorted_candles:
            await self._send(conn, channel, {"type": "candles", "data": sorted_candles})

        # If the stream task is still running (kept alive during grace period), skip restart
        self._ensure_stream("candles", channel)

    async def _handle_candle_duration_change(
        self, conn: "_Connection", channel: str, duration: int
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
        parsed = parse_candle_channel(channel)
        if parsed is None:
            return
        server_name, connector, pair, interval, pool_address = parsed
        buf = self._candle_buffers.get(channel)
        if buf is None:
            return

        from config_manager import get_config_manager

        cm = get_config_manager()
        try:
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

            if dex_candles.uses_gecko_candles(connector):
                candles = await dex_candles.fetch_dex_candles(
                    connector, pool_address, pair, interval, start_time, end_time
                )
                if candles:
                    buf.upsert_many(candles)
                    logger.info(
                        "Backfilled %d GeckoTerminal candles for %s (buffer: %d/%d)",
                        len(candles),
                        channel,
                        buf.size,
                        buf.max_size,
                    )
                return

            client = await cm.get_client(server_name)
            candles = await fetch_historical_candles(
                client,
                connector,
                pair,
                interval,
                start_time=start_time,
                end_time=end_time,
                limit=min(buf.max_size, 5000),
            )
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

    # -- Deferred teardown --

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
        # Snapshot-on-resubscribe is served by _candle_buffers, not _last_data
        self._last_data.pop(channel, None)
        # NOTE: candle buffer is NOT deleted — the existing idle cleanup loop handles that

    # -- Backend WS stream --

    async def _candle_stream(self, channel: str) -> None:
        parsed = parse_candle_channel(channel)
        if parsed is None:
            return
        server_name, connector, pair, interval, pool_address = parsed

        from config_manager import get_config_manager

        cm = get_config_manager()
        backoff = 5

        # Start REST poll fallback alongside the WS stream
        self._ensure_candle_poll_fallback(channel)

        if dex_candles.uses_gecko_candles(connector):
            # No CandlesFactory feed to subscribe to (xrpl, DEX networks): the
            # poll loop above is the whole stream. Subscribing anyway would just
            # reconnect-loop on an error the API can never stop returning.
            logger.info(
                "Candle stream for %s is GeckoTerminal-polled (no API feed)", channel
            )
            return

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
                # Detect permanent failures — don't retry (issue #134, see
                # _is_permanent_ws_error).
                if self._is_permanent_ws_error(error_str):
                    logger.warning(
                        "Candle stream permanent error for %s: %s — giving up",
                        channel,
                        e,
                    )
                    await self.broadcast(
                        channel,
                        {"type": "error", "message": f"Stream failed: {error_str}"},
                    )
                    # Stop the paired REST poll fallback — nothing valid to poll.
                    poll_task = self._candle_poll_tasks.pop(channel, None)
                    if poll_task and not poll_task.done():
                        poll_task.cancel()
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

    # -- REST poll fallback --

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
        """Periodically poll REST for candles when WS stream goes silent.

        For a GeckoTerminal-backed connector there is no WS stream to fall back
        from — this loop *is* the feed, so it polls from the first tick and never
        waits for staleness.
        """
        parsed = parse_candle_channel(channel)
        if parsed is None:
            return
        server_name, connector, pair, interval, pool_address = parsed
        interval_sec = _INTERVAL_SECONDS.get(interval, 60)
        gecko = dex_candles.uses_gecko_candles(connector)
        # How long without a WS update before we consider it stale
        stale_threshold = max(interval_sec, 15)
        # How often to poll REST once stale (keep the last candle fresh). Gecko
        # polls stay well clear of its per-minute rate limit — every chart on the
        # dashboard shares one budget — while still refreshing a forming candle.
        poll_interval = (
            _gecko_poll_interval(interval_sec) if gecko else min(interval_sec, 10)
        )
        was_stale = gecko

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
                    now = int(time.time())
                    if gecko:
                        candles = await dex_candles.fetch_dex_candles(
                            connector,
                            pool_address,
                            pair,
                            interval,
                            now - interval_sec * 5,
                            now,
                            # The shared OHLCV cache holds a pool for minutes,
                            # long enough to freeze a live chart.
                            use_cache=False,
                        )
                    else:
                        cm = get_config_manager()
                        client = await cm.get_client(server_name)
                        # No `limit`: this poll only wants the fresh tail of the
                        # live range, so an empty historical answer stays empty
                        # rather than pulling a full unrelated window.
                        candles = await fetch_historical_candles(
                            client,
                            connector,
                            pair,
                            interval,
                            start_time=now - interval_sec * 5,
                            end_time=now,
                        )
                    if candles:
                        buf = self._candle_buffers.get(channel)
                        # Broadcast if we have newer candles OR if the latest
                        # candle's OHLCV changed (same timestamp, updated values)
                        newest_buf_ts = buf.latest_timestamp if buf else 0
                        newest_poll_ts = max(c["timestamp"] for c in candles)
                        changed = newest_poll_ts > newest_buf_ts
                        if not changed and buf:
                            old = buf.get(newest_poll_ts)
                            if old is not None:
                                # Same timestamp — check if OHLCV actually changed
                                new = next(
                                    c
                                    for c in candles
                                    if c["timestamp"] == newest_poll_ts
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

    # -- Buffer helpers --

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
