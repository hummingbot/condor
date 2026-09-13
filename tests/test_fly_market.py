"""Fixture market, book parsing, candle payload shapes, collateral requirement."""

import asyncio

import pytest

from condor.fly.chart import market_frame
from condor.fly.market import (
    Book,
    FixtureMarket,
    normalize_candle_payload,
    required_collateral,
)
from condor.fly.posture import MarketSpec

PAIRS = ["XYZ:A-USD", "XYZ:B-USD", "XYZ:C-USD"]


def test_fixture_observation_renders_and_advances():
    m = FixtureMarket(PAIRS, 72)
    obs = asyncio.run(m.observe("XYZ:B-USD"))
    assert obs.open and obs.bid < obs.ask and len(obs.candles) == 72
    frame = market_frame(obs.pair, obs.candles, obs.bid, obs.ask)
    assert frame.shape == (180, 320, 3)
    again = asyncio.run(m.observe("XYZ:B-USD"))
    assert again.candles[-1]["close"] != obs.candles[-1]["close"]


def test_fixture_equity_stays_inside_the_breaker():
    m = FixtureMarket(PAIRS, 72)
    for tick in range(60):
        m.tick = tick
        net, volume, per_pair = asyncio.run(m.equity(PAIRS))
        assert abs(net / volume) * 1e4 < 5
        assert per_pair["XYZ:A-USD"]["running"] is False


def test_fixture_never_applies():
    m = FixtureMarket(PAIRS, 72)
    with pytest.raises(RuntimeError):
        asyncio.run(m.apply("XYZ:A-USD", {}))
    assert asyncio.run(m.stop_bot("XYZ:A-USD")) is False


def test_book_open():
    assert Book(1.0, 1.1).open
    assert not Book(None, None).open


def test_candle_payload_shapes():
    rows = [{"close": 1}]
    assert normalize_candle_payload(rows) == rows
    assert normalize_candle_payload({"data": rows}) == rows
    assert normalize_candle_payload({"candles": rows}) == rows
    with pytest.raises(RuntimeError):
        normalize_candle_payload([])
    with pytest.raises(RuntimeError):
        normalize_candle_payload({"data": []})


def test_required_collateral():
    spec = MarketSpec("hyperliquid_perpetual", "XYZ:A-USD", 500, 8.0, leverage=2)
    assert required_collateral([spec, spec]) == pytest.approx(2 * 500 * 0.5 / 2)
