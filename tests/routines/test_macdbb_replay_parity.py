"""Replay parity fixes: streak timing, journal ratios, barriers, open-leg carry."""

from __future__ import annotations

import datetime as dt

from routines.macdbb_replay.hl_prices import scan_barriers_between
from routines.macdbb_replay.journal import _parse_barrier_events, _parse_signals_1h
from routines.macdbb_replay.metrics import compute_metrics, parsed_report_from_journal
from routines.macdbb_replay.models import JournalSignal1h, StrategyReplayConfig
from routines.macdbb_replay.simulator import _advance_simulated_streak


def _snapshot(signal: str):
    return type("Snap", (), {"signal": signal})()


def test_strength_gate_uses_journal_gap_when_macd_rounds_to_zero():
    raw = (
        "HMSTR-USD:bb=39.90,macd=0.0000,sig=0.0000,hist=-0.0000,gap=0.3659,hr=0.5772,"
        "tr=bull,mom=inc,fL=0,fS=0,aL=1,aS=0,sL=1.7348,sS=0,p=0.000267"
    )
    journal_signal = _parse_signals_1h(raw)["HMSTR-USD"]
    parsed = parsed_report_from_journal(journal_signal, price=0.000267)
    config = StrategyReplayConfig(
        preset="custom",
        adaptive_min_macd_gap_ratio=0.06,
        adaptive_min_hist_ratio=0.09,
        adaptive_score_open_min=1.0,
        adaptive_score_open_min_extreme=0.75,
        adaptive_long_bb_pos_max=90,
        adaptive_strong_long_bb_pos_max=30,
    )
    metrics = compute_metrics(parsed, config, journal_signal=journal_signal)
    assert metrics["strength_gate"] is True
    assert metrics["adaptive_long_open"] is True


def test_advance_simulated_streak_waits_one_neutral_tick_before_activation():
    streak = 0
    neutral_snaps = {"BTC-USD": _snapshot("NEUTRAL")}
    streak = _advance_simulated_streak(neutral_snaps, streak, 0, opened_this_tick=False)
    assert streak == 1
    streak = _advance_simulated_streak(neutral_snaps, streak, 0, opened_this_tick=False)
    assert streak == 2
    streak = _advance_simulated_streak(neutral_snaps, streak, 0, opened_this_tick=True)
    assert streak == 0


def test_advance_simulated_streak_does_not_increment_with_open_position():
    streak = 2
    streak = _advance_simulated_streak(
        {"BTC-USD": _snapshot("NEUTRAL")},
        streak,
        open_position_count=1,
        opened_this_tick=False,
    )
    assert streak == 2


def test_scan_barriers_between_detects_long_stop_loss():
    start = dt.datetime(2026, 6, 12, 0, 0, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 6, 12, 1, 0, tzinfo=dt.timezone.utc)
    entry = 100.0
    candles = [
        {
            "timestamp_ms": int(start.timestamp() * 1000) + 600_000,
            "open": 99.0,
            "high": 99.5,
            "low": 96.0,
            "close": 97.0,
            "volume": 1.0,
        }
    ]
    hit = scan_barriers_between(candles, start, end, "long", entry, sl_pct=2.6, tp_pct=5.0)
    assert hit is not None
    reason, exit_price = hit
    assert reason == "stop_loss_close_proxy"
    assert exit_price == entry * (1.0 - 0.026)


def test_parse_barrier_events_from_structured_field_and_narrative():
    line = (
        "barrier_close=HMSTR-USD:STOP_LOSS:pnl=-9.58 "
        "Tick #5: HMSTR hit STOP_LOSS (-$9.58)"
    )
    events = _parse_barrier_events(line)
    assert len(events) == 1
    assert events[0].pair == "HMSTR-USD"
    assert events[0].close_type == "stop_loss"
    assert events[0].pnl_quote == -9.58


def test_parse_barrier_events_ignores_open_pair_on_barrier_open_decision():
    line = (
        "position_action=barrier_close,open pair=ZEC-USD close_type=STOP_LOSS pnl=-6.69 "
        "WLD-USD LONG hit **STOP_LOSS** between ticks (PnL **-$6.69**)"
    )
    events = _parse_barrier_events(line)
    assert len(events) == 1
    assert events[0].pair == "WLD-USD"
    assert events[0].pnl_quote == -6.69
