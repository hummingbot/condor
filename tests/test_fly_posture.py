"""Posture → pmm_mister config: floors, lean cap, timing table, pause switch."""

import pytest

from condor.fly.decoder import NEUTRAL, Posture
from condor.fly.posture import (
    BPS,
    TIMING,
    MarketSpec,
    base_levels_bps,
    build_config,
    config_diff,
    take_profit_floor,
)

SPEC = MarketSpec(
    connector_name="hyperliquid_perpetual",
    trading_pair="XYZ:DRAM-USD",
    total_amount_quote=500,
    picked_spread_bps=8.0,
)


def _spreads(value):
    return [float(x) for x in value.split(",")]


def test_neutral_config_matches_hip3_base():
    cfg = build_config(SPEC, NEUTRAL)
    l1, l2 = base_levels_bps(SPEC)
    assert (l1, l2) == (4.0, 9.0)
    assert _spreads(cfg["buy_spreads"]) == pytest.approx([4 * BPS, 9 * BPS])
    assert _spreads(cfg["sell_spreads"]) == pytest.approx([4 * BPS, 9 * BPS])
    assert (
        cfg["controller_name"] == "pmm_mister" and cfg["controller_type"] == "generic"
    )
    assert cfg["trading_pair"] == "XYZ:DRAM-USD"
    assert cfg["global_sl_enabled"] is True and cfg["global_stop_loss"] == 0.02
    assert cfg["manual_kill_switch"] is False
    assert (cfg["executor_refresh_time"], cfg["buy_cooldown_time"]) == TIMING["ranging"]
    assert cfg["open_order_type"] == 3 and cfg["take_profit_order_type"] == 3


def test_take_profit_floor_beats_fees_and_spread():
    assert take_profit_floor(SPEC) == pytest.approx(2.2 * 2 * 1.3 * BPS)
    cfg = build_config(SPEC, NEUTRAL)
    assert cfg["take_profit"] >= take_profit_floor(SPEC)
    cheap = MarketSpec(**{**SPEC.__dict__, "maker_fee_bps": 0.1})
    assert take_profit_floor(cheap) == pytest.approx(4 * BPS)


def test_volatile_widens_quiet_tightens_with_floor():
    wide = build_config(SPEC, Posture("volatile", 2.0, 0.0, 0, 1.5, False, True))
    assert _spreads(wide["buy_spreads"])[0] == pytest.approx(8 * BPS)
    assert (wide["executor_refresh_time"], wide["buy_cooldown_time"]) == TIMING[
        "volatile"
    ]
    tight = build_config(SPEC, Posture("quiet", 0.6, 0.0, 0, -1.5, False, True))
    # 4 bp × 0.6 = 2.4 bp, floored to the 3 bp minimum
    assert _spreads(tight["buy_spreads"])[0] == pytest.approx(3 * BPS)
    assert (tight["executor_refresh_time"], tight["buy_cooldown_time"]) == TIMING[
        "quiet"
    ]


def test_lean_is_asymmetric_and_capped():
    up = build_config(SPEC, Posture("trending_up", 1.0, 3.0, 2.0, 0, True, True))
    buy, sell = _spreads(up["buy_spreads"]), _spreads(up["sell_spreads"])
    # lean capped at half of level 1 (4 bp → 2 bp); buy 2 bp floored to 3 bp
    assert buy[0] == pytest.approx(3 * BPS) and sell[0] == pytest.approx(6 * BPS)
    assert buy[1] == pytest.approx(7 * BPS) and sell[1] == pytest.approx(11 * BPS)
    down = build_config(SPEC, Posture("trending_down", 1.0, -3.0, -2.0, 0, True, True))
    assert _spreads(down["sell_spreads"])[0] == pytest.approx(3 * BPS)
    assert _spreads(down["buy_spreads"])[0] == pytest.approx(6 * BPS)


def test_pause_sets_kill_switch():
    cfg = build_config(SPEC, Posture("pause", 2.5, 0.0, 0, 3.0, False, True))
    assert cfg["manual_kill_switch"] is True


def test_every_spread_respects_min():
    for regime in TIMING:
        for mult in (0.6, 1.0, 2.5):
            for shift in (-3.0, 0.0, 3.0):
                cfg = build_config(SPEC, Posture(regime, mult, shift, 0, 0, True, True))
                for key in ("buy_spreads", "sell_spreads"):
                    assert min(_spreads(cfg[key])) >= SPEC.min_spread_bps * BPS - 1e-12


def test_spec_validation():
    with pytest.raises(ValueError):
        MarketSpec("hyperliquid_perpetual", "xyz:dram-usd", 500, 8)
    with pytest.raises(ValueError):
        MarketSpec("hyperliquid_perpetual", "DRAM-USD", 500, 8)
    with pytest.raises(ValueError):
        MarketSpec("hyperliquid_perpetual", "XYZ:DRAM-USD", 500, 8, leverage=10)
    with pytest.raises(ValueError):
        MarketSpec("hyperliquid_perpetual", "XYZ:DRAM-USD", 0, 8)


def test_config_diff():
    a = build_config(SPEC, NEUTRAL)
    b = build_config(SPEC, Posture("volatile", 2.0, 0.0, 0, 1.5, False, True))
    diff = config_diff(a, b)
    assert "buy_spreads" in diff and "trading_pair" not in diff
    assert config_diff(None, a) == a
