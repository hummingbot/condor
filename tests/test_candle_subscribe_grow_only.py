"""Subscribing to a shared candle channel must never shrink its buffer (CORR-581).

``_candle_buffers`` is keyed by channel, so every tab charting
``candles:srv:binance:SOL-USDC:1m`` shares one buffer. The subscribe path used
to call ``set_duration`` unconditionally, and ``set_duration`` evicts down to
the new max — so a second tab (or the same tab's automatic re-subscribe after a
WS reconnect, which sends no duration and lands on the 3-day default) physically
deleted the history a 30-day window had already backfilled, with nothing to
refill it: the backfill only fires when the buffer *grew*.

``_handle_candle_duration_change`` always documented and enforced the opposite
invariant. Both paths now share it through ``_CandleBuffer.grow_to``.
"""

import asyncio

import pytest

from condor.web.streams.candles import _CandleBuffer
from condor.web.ws_manager import WebSocketManager

CHANNEL = "candles:srv:binance:SOL-USDC:1m"
MINUTE = 60
THIRTY_DAYS = 30 * 86400
ONE_HOUR = 3600


def run(coro):
    return asyncio.run(coro)


def _candles(n: int) -> list[dict]:
    return [{"timestamp": float(i * MINUTE), "close": 1.0 + i} for i in range(n)]


@pytest.fixture
def mgr(monkeypatch):
    """A manager whose subscribe path is fully driven but has no I/O."""
    manager = WebSocketManager()
    sent: list[list[dict]] = []

    async def fake_send(self, conn, channel, data):
        if data.get("type") == "candles":
            sent.append(data["data"])

    monkeypatch.setattr(WebSocketManager, "_send", fake_send)
    monkeypatch.setattr(WebSocketManager, "_ensure_stream", lambda self, p, c: None)
    monkeypatch.setattr("condor.web.ws_manager.is_active_user", lambda uid: True)
    manager.sent_snapshots = sent
    return manager


# ── the buffer primitive ──


def test_grow_to_refuses_to_shrink():
    buf = _CandleBuffer("1m", THIRTY_DAYS)
    buf.upsert_many(_candles(5000))
    assert buf.grow_to(ONE_HOUR) == 30 * 1440
    assert buf.size == 5000


def test_grow_to_still_grows():
    buf = _CandleBuffer("1m", ONE_HOUR)
    assert buf.max_size == 200  # the floor
    assert buf.grow_to(THIRTY_DAYS) == 30 * 1440


def test_set_duration_remains_the_unconditional_primitive():
    """``__init__`` and nothing else relies on set_duration shrinking."""
    buf = _CandleBuffer("1m", THIRTY_DAYS)
    buf.upsert_many(_candles(5000))
    assert buf.set_duration(ONE_HOUR) == 200
    assert buf.size == 200


# ── the subscribe path, end to end ──


def test_second_subscribe_with_a_smaller_window_keeps_the_history(mgr):
    """Tab A charts 30 days; tab B subscribes with the 3-day default."""
    run(mgr._handle_candle_subscribe(object(), CHANNEL, THIRTY_DAYS))
    buf = mgr._candle_buffers[CHANNEL]
    buf.upsert_many(_candles(5000))

    run(mgr._handle_candle_subscribe(object(), CHANNEL, None))

    assert buf.max_size == 30 * 1440
    assert buf.size == 5000, "a second subscriber evicted the shared history"
    assert buf.get_sorted()[0]["timestamp"] == 0.0
    # Tab B's opening snapshot carries the whole buffer, not a truncated tail.
    assert len(mgr.sent_snapshots[-1]) == 5000


def test_second_subscribe_with_a_larger_window_still_grows_and_backfills(mgr):
    backfilled: list[str] = []

    async def fake_backfill(channel):
        backfilled.append(channel)

    async def scenario():
        await mgr._handle_candle_subscribe(object(), CHANNEL, ONE_HOUR)
        mgr._backfill_candles = fake_backfill
        await mgr._handle_candle_subscribe(object(), CHANNEL, THIRTY_DAYS)
        await asyncio.sleep(0)  # let the tracked backfill task run

    run(scenario())

    assert mgr._candle_buffers[CHANNEL].max_size == 30 * 1440
    assert backfilled == [CHANNEL]


# ── the sibling path is unchanged ──


def test_duration_change_shrink_is_still_a_no_op(mgr):
    run(mgr._handle_candle_subscribe(object(), CHANNEL, THIRTY_DAYS))
    buf = mgr._candle_buffers[CHANNEL]
    buf.upsert_many(_candles(5000))
    broadcast: list = []

    async def fake_broadcast(channel, data):
        broadcast.append(data)

    mgr.broadcast = fake_broadcast

    run(mgr._handle_candle_duration_change(object(), CHANNEL, ONE_HOUR))

    assert buf.max_size == 30 * 1440
    assert buf.size == 5000
    assert broadcast == [], "a shrink must not re-broadcast"


def test_duration_change_grow_resizes_backfills_and_rebroadcasts(mgr):
    run(mgr._handle_candle_subscribe(object(), CHANNEL, ONE_HOUR))
    buf = mgr._candle_buffers[CHANNEL]
    buf.upsert_many(_candles(100))
    backfilled: list[str] = []
    broadcast: list = []

    async def fake_backfill(channel):
        backfilled.append(channel)

    async def fake_broadcast(channel, data):
        broadcast.append(data["data"])

    mgr._backfill_candles = fake_backfill
    mgr.broadcast = fake_broadcast

    async def scenario():
        await mgr._handle_candle_duration_change(object(), CHANNEL, THIRTY_DAYS)
        await asyncio.gather(*mgr._oneshot_tasks)  # the tracked backfill

    run(scenario())

    assert buf.max_size == 30 * 1440
    assert backfilled == [CHANNEL]
    assert len(broadcast) == 1 and len(broadcast[0]) == 100


# ── set_candle_duration is untrusted input (WS-09) ──


def test_duration_change_returns_before_the_backfill_completes(mgr):
    """The backfill is a REST round-trip; the socket's receive loop must not wait."""
    run(mgr._handle_candle_subscribe(object(), CHANNEL, ONE_HOUR))
    release = None
    broadcast: list = []

    async def slow_backfill(channel):
        await release.wait()

    async def fake_broadcast(channel, data):
        broadcast.append(data["data"])

    mgr._backfill_candles = slow_backfill
    mgr.broadcast = fake_broadcast

    async def scenario():
        nonlocal release
        release = asyncio.Event()
        mgr._candle_buffers[CHANNEL].upsert_many(_candles(100))
        await asyncio.wait_for(
            mgr._handle_candle_duration_change(object(), CHANNEL, THIRTY_DAYS), 1
        )
        assert broadcast == [], "the snapshot went out before the backfill"
        release.set()
        await asyncio.gather(*mgr._oneshot_tasks)

    run(scenario())

    assert len(broadcast) == 1


class _Conn:
    user_id = 7

    def __init__(self, *channels):
        self.channels = set(channels)


def _set_duration(mgr, conn, channel, duration):
    import json

    called: list = []

    async def record(conn, channel, duration):
        called.append((channel, duration))

    mgr._handle_candle_duration_change = record
    # A live socket: handle_message refuses one the manager no longer holds.
    mgr._connections.append(conn)
    msg = {"action": "set_candle_duration", "channel": channel, "duration": duration}
    run(mgr.handle_message(conn, json.dumps(msg)))
    return called


def test_set_candle_duration_is_ignored_for_a_channel_not_subscribed(mgr):
    """Subscribing is the server-access gate; this action must not bypass it."""
    other = "candles:someone-elses:binance:BTC-USDT:1s"
    mgr._candle_buffers[other] = _CandleBuffer("1s", ONE_HOUR)

    assert _set_duration(mgr, _Conn(CHANNEL), other, THIRTY_DAYS) == []
    assert mgr._candle_buffers[other].max_size == ONE_HOUR


def test_set_candle_duration_still_works_for_a_subscribed_channel(mgr):
    assert _set_duration(mgr, _Conn(CHANNEL), CHANNEL, THIRTY_DAYS) == [
        (CHANNEL, THIRTY_DAYS)
    ]


@pytest.mark.parametrize("absurd", [1e12, "1e300", 10**40])
def test_an_absurd_duration_is_clamped(mgr, absurd):
    from condor.web.streams.candles import (
        _MAX_CANDLE_BUFFER_SIZE,
        _MAX_CANDLE_DURATION,
        _coerce_duration,
    )

    assert _coerce_duration(absurd) == _MAX_CANDLE_DURATION
    assert _set_duration(mgr, _Conn(CHANNEL), CHANNEL, absurd) == [
        (CHANNEL, _MAX_CANDLE_DURATION)
    ]
    # ...and on subscribe, which accepts the same field.
    one_second = "candles:srv:binance:SOL-USDC:1s"
    run(mgr._handle_candle_subscribe(object(), one_second, absurd))
    assert mgr._candle_buffers[one_second].max_size == _MAX_CANDLE_BUFFER_SIZE


def test_an_infinite_duration_is_ignored_not_raised():
    from condor.web.streams.candles import _coerce_duration

    assert _coerce_duration(float("inf")) is None
    assert _coerce_duration("nan") is None
