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

    run(mgr._handle_candle_duration_change(object(), CHANNEL, THIRTY_DAYS))

    assert buf.max_size == 30 * 1440
    assert backfilled == [CHANNEL]
    assert len(broadcast) == 1 and len(broadcast[0]) == 100
