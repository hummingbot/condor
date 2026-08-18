"""The candle channel's optional sixth segment: the pool address (FEAT-044).

A DEX chart must be drawn from the pool the user opened, not from whichever pool
``dex_candles._resolve_pool`` judges deepest for the pair. The channel carries it
as ``candles:{server}:{connector}:{pair}:{interval}[:{pool_address}]``.

Three sites read that channel — the backfill, the WS stream and the REST poll
fallback — and each used to unpack exactly five names, so a six-part channel
raised ``ValueError`` in a place nobody watches: a chart that backfills but never
ticks, or ticks but never backfills. All three go through
``parse_candle_channel`` now, and this file exercises all three deliberately.
"""

import asyncio

import pytest

import condor.web.ws_manager as wsm
from condor.web.ws_manager import WebSocketManager, parse_candle_channel

POOL = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"
FIVE = "candles:srv:solana-mainnet-beta:SOL-USDC:1m"
SIX = f"{FIVE}:{POOL}"


def run(coro):
    return asyncio.run(coro)


# ── the parser ──


def test_five_part_channel_parses_with_no_pool():
    assert parse_candle_channel(FIVE) == (
        "srv",
        "solana-mainnet-beta",
        "SOL-USDC",
        "1m",
        None,
    )


def test_six_part_channel_parses_the_pool_address():
    assert parse_candle_channel(SIX) == (
        "srv",
        "solana-mainnet-beta",
        "SOL-USDC",
        "1m",
        POOL,
    )


def test_clob_channel_is_unchanged_shape():
    """A CEX chart's channel keeps five segments and no pool address."""
    assert parse_candle_channel("candles:srv:binance:BTC-USDT:1m") == (
        "srv",
        "binance",
        "BTC-USDT",
        "1m",
        None,
    )


def test_empty_sixth_segment_is_no_pool():
    assert parse_candle_channel(FIVE + ":")[4] is None


@pytest.mark.parametrize("channel", ["candles", "candles:srv", "candles:srv:binance"])
def test_short_channels_are_rejected(channel):
    assert parse_candle_channel(channel) is None


# ── the three call sites ──


@pytest.fixture
def spy_fetch(monkeypatch):
    """Record every ``fetch_dex_candles`` call this module makes."""
    calls: list[dict] = []

    async def fake_fetch(connector, pool_address, pair, interval, *a, **kw):
        calls.append(
            {
                "connector": connector,
                "pool_address": pool_address,
                "pair": pair,
                "interval": interval,
            }
        )
        return []

    monkeypatch.setattr(wsm.dex_candles, "fetch_dex_candles", fake_fetch)
    return calls


@pytest.mark.parametrize(
    "channel,expected", [(FIVE, None), (SIX, POOL)], ids=["five-part", "six-part"]
)
def test_backfill_passes_the_channel_pool(spy_fetch, channel, expected):
    mgr = WebSocketManager()
    mgr._candle_buffers[channel] = wsm._CandleBuffer("1m", 3600)
    run(mgr._backfill_candles(channel))
    assert spy_fetch, "backfill never reached fetch_dex_candles"
    assert spy_fetch[0]["pool_address"] == expected
    assert spy_fetch[0]["pair"] == "SOL-USDC"


@pytest.mark.parametrize("channel", [FIVE, SIX], ids=["five-part", "six-part"])
def test_candle_stream_parses_without_raising(monkeypatch, channel):
    """The gecko branch short-circuits, but only after unpacking the channel."""
    started: list[str] = []
    monkeypatch.setattr(
        WebSocketManager,
        "_ensure_candle_poll_fallback",
        lambda self, ch: started.append(ch),
    )
    run(WebSocketManager()._candle_stream(channel))
    assert started == [channel]


@pytest.mark.parametrize(
    "channel,expected", [(FIVE, None), (SIX, POOL)], ids=["five-part", "six-part"]
)
def test_poll_fallback_passes_the_channel_pool(monkeypatch, channel, expected):
    """The REST fallback is the whole stream for a gecko-charted venue."""
    mgr = WebSocketManager()
    mgr._candle_buffers[channel] = wsm._CandleBuffer("1m", 3600)
    seen: list = []

    async def fake_fetch(connector, pool_address, pair, interval, *a, **kw):
        seen.append(pool_address)
        raise asyncio.CancelledError  # one poll is all we need

    monkeypatch.setattr(wsm.dex_candles, "fetch_dex_candles", fake_fetch)

    # The loop sleeps a 30s poll interval before its first fetch; collapse it.
    real_sleep = asyncio.sleep

    async def instant(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(wsm.asyncio, "sleep", instant)

    async def drive():
        task = asyncio.ensure_future(mgr._candle_poll_fallback(channel))
        for _ in range(50):
            if seen:
                break
            await real_sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    run(drive())
    assert seen, "poll fallback never reached fetch_dex_candles"
    assert seen[0] == expected
