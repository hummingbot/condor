"""The replay fill model: what it counts as a fill, a close, and a P&L."""

import pytest
from flybrain.decoder import NEUTRAL, DecoderSettings
from flybrain.posture import MarketSpec, build_config
from flybrain.replay import Ledger, Lot, frames, paired_stats, quote_prices, step

SPEC = MarketSpec(
    connector_name="hyperliquid_perpetual",
    trading_pair="XYZ:DRAM-USD",
    total_amount_quote=200,
    picked_spread_bps=2.0,
    leverage=1,
    portfolio_allocation=0.3,
    maker_fee_bps=1.3,
)
CONFIG = build_config(SPEC, NEUTRAL)


def _candle(high, low, close=None):
    return {"high": high, "low": low, "close": close if close else (high + low) / 2}


def test_a_quote_the_price_never_reached_does_not_fill():
    ledger = Ledger()
    mid = 100.0
    # a candle that never moves cannot touch a quote resting away from mid
    step(ledger, CONFIG, SPEC, _candle(100.0, 100.0), mid, max_lots=8)
    assert ledger.fills == 0 and ledger.open_lots == []


def test_a_quote_the_price_traded_through_fills_and_pays_its_fee():
    ledger = Ledger()
    mid = 100.0
    prices = quote_prices(CONFIG, mid)
    lowest_buy = min(prices["buy"])
    step(ledger, CONFIG, SPEC, _candle(100.0, lowest_buy * 0.999), mid, max_lots=8)
    assert ledger.fills == len(prices["buy"])  # both buy levels, no sell
    assert all(l.side == "buy" for l in ledger.open_lots)
    assert ledger.fees == pytest.approx(
        ledger.fills * (200 * 0.3 / 4) * SPEC.maker_fee_bps * 1e-4
    )


def test_a_lot_closes_when_its_take_profit_is_traded_through():
    ledger = Ledger()
    tp = float(CONFIG["take_profit"])
    ledger.open_lots = [Lot(side="buy", price=100.0, amount=1.0, take_profit=tp)]
    # max_lots=0 admits no new fill, so only the close is under test — a buy
    # fills whenever the low is under its price, which no choice of mid avoids
    step(ledger, CONFIG, SPEC, _candle(100.0 * (1 + tp) * 1.001, 99.999), 100.0, 0)
    assert ledger.round_trips == 1
    assert ledger.realized == pytest.approx(100.0 * tp, rel=1e-3)
    assert not ledger.open_lots


def test_equity_marks_open_inventory_and_nets_fees():
    ledger = Ledger(realized=1.0, fees=0.25)
    ledger.open_lots = [Lot(side="buy", price=100.0, amount=2.0, take_profit=0.001)]
    assert ledger.equity(101.0) == pytest.approx(1.0 - 0.25 + 2.0)
    assert ledger.equity(99.0) == pytest.approx(1.0 - 0.25 - 2.0)
    assert ledger.inventory == pytest.approx(2.0)


def test_a_tick_never_decides_on_a_candle_it_can_see():
    """The frame is built from candles strictly before the one it trades."""
    candles = [
        {"open": 1, "high": 1 + i, "low": 1, "close": 1 + i, "volume": 1}
        for i in range(1, 12)
    ]
    out = frames("XYZ:DRAM-USD", candles, window=5)
    assert len(out) == len(candles) - 5 - 1
    _, traded, mid = out[0]
    assert traded is candles[5]
    assert mid == pytest.approx(float(candles[4]["close"]))


def test_paired_stats_compares_earnings_not_the_gap_it_already_has():
    """A cumulative curve inherits every past difference, so a t on levels
    reports how long ago two runs diverged. Run that way on the 2026-09-13
    replay it returned |t| of 27 to 63 for variants a few percent apart."""
    flat = [0.0] * 50
    assert paired_stats(flat, flat)["t"] == 0.0

    # one run earns a steady amount more every tick: a real, detectable edge
    steady = paired_stats([i * 0.01 for i in range(50)], flat)
    assert steady["mean_diff"] == pytest.approx(0.01)
    assert steady["t"] == 0.0  # perfectly steady: no variance to test against

    # a run that jumped once and then tracked its control earns the same after
    # the jump, and must not read as an edge however wide the gap stays
    jumped = [0.0] * 10 + [5.0] * 40
    stats = paired_stats(jumped, flat)
    assert stats["final_gap"] == pytest.approx(5.0)
    assert abs(stats["t"]) < 2, "a single old jump is not a per-tick edge"


def test_the_disconnected_gain_is_a_gain_the_decoder_accepts():
    """`valence_gain: 0` is refused by design — a channel read and discarded —
    so the control uses the smallest gain that cannot move a posture."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "routines" / "fly_replay.py"
    spec = importlib.util.spec_from_file_location("fly_replay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(ValueError):
        DecoderSettings(valence_gain=0.0)
    settings = DecoderSettings(valence_gain=mod.OFF)
    assert round(1 + settings.valence_gain * 3.0, 4) == 1.0
