"""Fixture market, book parsing, candle payload shapes, collateral requirement."""

import asyncio

import pytest
from flybrain.chart import market_frame
from flybrain.market import (
    Book,
    FixtureMarket,
    normalize_candle_payload,
    pnl_is_known,
    required_collateral,
)
from flybrain.posture import MarketSpec

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
        net, volume, per_pair, carry = asyncio.run(m.equity(PAIRS))
        assert abs(net / volume) * 1e4 < 5
        assert sum(i["net"] for i in per_pair.values()) == pytest.approx(net)
        assert sum(i["volume"] for i in per_pair.values()) == pytest.approx(volume)
        assert set(carry) == set(PAIRS)


def test_fixture_reports_so_an_offline_run_exercises_the_pulses():
    """A fixture book that did not report would pin the stimulus to ``none``
    and never advance the anchor, which is the whole point of the offline run."""
    m = FixtureMarket(PAIRS, 72)
    _, _, per_pair, _ = asyncio.run(m.equity(PAIRS))
    assert all(i["running"] and i["reported"] for i in per_pair.values())
    assert pnl_is_known(per_pair)
    # and the swing really does cross the deadband both ways
    nets = []
    for tick in range(12):
        m.tick = tick
        net, _, _, _ = asyncio.run(m.equity(PAIRS))
        nets.append(net)
    deltas = [b - a for a, b in zip(nets, nets[1:])]
    assert max(deltas) > 0.02 and min(deltas) < -0.02


@pytest.mark.parametrize(
    "per_pair,expected",
    [
        ({}, False),  # no book at all is silence, not a result
        ({"a": {"running": False}}, False),
        ({"a": {"running": True}}, False),  # running but no report yet
        ({"a": {"running": True, "reported": True}}, True),
        (
            {"a": {"running": True, "reported": True}, "b": {"running": True}},
            False,  # one silent book makes the combined figure unusable
        ),
        ({"a": {"running": True, "reported": True}, "b": {"running": False}}, True),
    ],
)
def test_pnl_is_known(per_pair, expected):
    assert pnl_is_known(per_pair) is expected


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
