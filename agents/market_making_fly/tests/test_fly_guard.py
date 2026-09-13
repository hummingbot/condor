"""Every guard rule: veto vs halt, and a financial halt that review cannot clear."""

import pytest
from flybrain.decoder import NEUTRAL
from flybrain.guard import (
    GuardSettings,
    GuardState,
    Halt,
    Veto,
    check_apply_window,
    check_collateral,
    check_config,
    check_market_open,
    check_not_halted,
    check_pnl,
    check_price_move,
    default_max_loss,
    record_apply,
    resume,
)
from flybrain.posture import MarketSpec, build_config

SPEC = MarketSpec("hyperliquid_perpetual", "XYZ:DRAM-USD", 500, 8.0)
S = GuardSettings()


def test_market_open_counts_closed_ticks_then_asks_for_stop():
    st = GuardState()
    assert check_market_open("XYZ:DRAM-USD", True, st, S) is False
    small = GuardSettings(closed_ticks_to_stop=3)
    for _ in range(2):
        with pytest.raises(Veto):
            check_market_open("XYZ:DRAM-USD", False, st, small)
    assert check_market_open("XYZ:DRAM-USD", False, st, small) is True
    assert check_market_open("XYZ:DRAM-USD", True, st, small) is False
    assert st.closed_ticks["XYZ:DRAM-USD"] == 0


def test_collateral():
    check_collateral(100.0, 50.0)
    with pytest.raises(Veto):
        check_collateral(40.0, 50.0)
    with pytest.raises(Veto):
        check_collateral(float("nan"), 50.0)


def test_config_floors():
    good = build_config(SPEC, NEUTRAL)
    check_config(good, SPEC)
    for bad in (
        {"buy_spreads": "0.0001,0.0009"},
        {"take_profit": 0.0001},
        {"leverage": 20},
        {"trading_pair": "XYZ:SPCX-USD"},
        {"global_sl_enabled": False},
    ):
        with pytest.raises(Veto):
            check_config({**good, **bad}, SPEC)


def test_apply_window_resets_per_utc_day():
    st = GuardState()
    small = GuardSettings(max_applies_per_day=2)
    day1 = 1_700_000_000.0
    check_apply_window(st, day1, small)
    record_apply(st, "P", day1, True, small)
    record_apply(st, "P", day1 + 60, True, small)
    with pytest.raises(Veto):
        check_apply_window(st, day1 + 120, small)
    check_apply_window(st, day1 + 86_400, small)
    assert st.applies_today == 0


def test_price_move():
    check_price_move(100.0, 100.4, S)
    with pytest.raises(Veto):
        check_price_move(100.0, 101.0, S)
    with pytest.raises(Veto):
        check_price_move(0, 1, S)


def test_apply_failures_halt_transiently_and_resume():
    st = GuardState()
    small = GuardSettings(max_apply_failures=2)
    record_apply(st, "P", 1.0, False, small)
    with pytest.raises(Halt) as info:
        record_apply(st, "P", 2.0, False, small)
    assert info.value.financial is False
    with pytest.raises(Halt):
        check_not_halted(st)
    with pytest.raises(Halt):
        resume(st, reviewed=False)
    resume(st, reviewed=True)
    check_not_halted(st)
    assert st.consecutive_failures == 0
    record_apply(st, "P", 3.0, True, small)


def test_loss_stop_is_financial_and_unclearable():
    st = GuardState()
    check_pnl(1.0, 1000.0, st, S, max_loss_quote=20.0)
    with pytest.raises(Halt) as info:
        check_pnl(-20.0, 1000.0, st, S, max_loss_quote=20.0)
    assert info.value.financial is True
    with pytest.raises(Halt):
        resume(st, reviewed=True)


def test_loss_rate_breaker_needs_volume():
    st = GuardState()
    check_pnl(-0.1, 10.0, st, S, max_loss_quote=100.0)  # too little volume to judge
    with pytest.raises(Halt):
        check_pnl(-1.0, 1000.0, st, S, max_loss_quote=100.0)  # -10 bp of volume


def test_no_new_high_breaker():
    st = GuardState()
    small = GuardSettings(loss_no_new_high_ticks=3)
    check_pnl(1.0, 1000.0, st, small, 100.0)
    check_pnl(0.9, 1000.0, st, small, 100.0)
    check_pnl(0.9, 1000.0, st, small, 100.0)
    with pytest.raises(Halt):
        check_pnl(0.9, 1000.0, st, small, 100.0)


def test_first_reported_figure_is_the_high_not_a_drawdown():
    st = GuardState()
    small = GuardSettings(loss_no_new_high_ticks=2)
    check_pnl(-0.5, 100_000.0, st, small, 100.0)  # first report, negative
    assert st.session_high_net == -0.5 and st.ticks_since_high == 0
    check_pnl(-0.6, 100_000.0, st, small, 100.0)
    with pytest.raises(Halt):
        check_pnl(-0.6, 100_000.0, st, small, 100.0)


def test_new_high_resets_counter():
    st = GuardState()
    small = GuardSettings(loss_no_new_high_ticks=3)
    for net in (1.0, 0.9, 0.9, 1.1, 1.0, 1.0):
        check_pnl(net, 1000.0, st, small, 100.0)
    assert st.ticks_since_high == 2


def test_default_max_loss_and_roundtrip():
    assert default_max_loss([SPEC, SPEC], S) == pytest.approx(40.0)
    assert default_max_loss([SPEC], GuardSettings(max_loss_quote=7.0)) == 7.0
    st = GuardState(applies_today=3, closed_ticks={"X:Y-USD": 1}, halted="x")
    assert GuardState.from_dict(st.to_dict()) == st


def test_a_config_sitting_exactly_on_the_floor_is_not_vetoed():
    """Live on 2026-09-13, tick 18: a trending posture leaned the buy side down
    to the fee floor, the config serialized it as 0.00013, and the guard vetoed
    it because 0.00013 < 1.3 * 1e-4 by one ulp. The guard was refusing the
    posture builder's own arithmetic, and a real apply was lost."""
    from flybrain.decoder import Posture
    from flybrain.posture import MarketSpec, build_config

    spec = MarketSpec(
        connector_name="hyperliquid_perpetual",
        trading_pair="XYZ:DRAM-USD",
        total_amount_quote=200,
        range_bps=4.0,
        leverage=1,
        portfolio_allocation=0.3,
        maker_fee_bps=1.3,
    )
    leaned = Posture("trending_up", 1.14, 1.0, 1.0, 1.27, 2.0, 0.3, 0.0, True, True)
    config = build_config(spec, leaned)
    assert min(float(x) for x in config["buy_spreads"].split(",")) == pytest.approx(
        spec.min_spread_bps * 1e-4
    )
    check_config(config, spec)  # must not raise
