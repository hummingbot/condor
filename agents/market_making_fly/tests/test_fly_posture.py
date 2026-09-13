"""Posture → pmm_mister config: floors, lean cap, timing table, pause switch."""

import pytest
from flybrain.decoder import NEUTRAL, Posture
from flybrain.posture import (
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
    # What a HIP-3 market in growth mode costs all-in: 0.3 bp to the venue plus
    # 1.0 bp of builder fee. The loop fetches this per market; a core
    # Hyperliquid perp is 2.5 bp, which is why it is not assumed here.
    maker_fee_bps=1.3,
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
    assert cfg["global_sl_enabled"] is True and cfg["global_stop_loss"] == 0.05
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
    wide = build_config(SPEC, Posture("volatile", 2.0, 1.0, 0.0, 0, 1.5, False, True))
    assert _spreads(wide["buy_spreads"])[0] == pytest.approx(8 * BPS)
    assert (wide["executor_refresh_time"], wide["buy_cooldown_time"]) == TIMING[
        "volatile"
    ]
    tight = build_config(SPEC, Posture("quiet", 0.6, 1.0, 0.0, 0, -1.5, False, True))
    # 4 bp × 0.6 = 2.4 bp, which clears this market's 1.3 bp fee floor
    assert _spreads(tight["buy_spreads"])[0] == pytest.approx(2.4 * BPS)
    assert (tight["executor_refresh_time"], tight["buy_cooldown_time"]) == TIMING[
        "quiet"
    ]


def test_lean_is_asymmetric_and_capped():
    up = build_config(SPEC, Posture("trending_up", 1.0, 1.0, 3.0, 2.0, 0, True, True))
    buy, sell = _spreads(up["buy_spreads"]), _spreads(up["sell_spreads"])
    # lean capped at half of level 1 (4 bp → 2 bp), and 2 bp still clears the fee
    assert buy[0] == pytest.approx(2 * BPS) and sell[0] == pytest.approx(6 * BPS)
    assert buy[1] == pytest.approx(7 * BPS) and sell[1] == pytest.approx(11 * BPS)
    down = build_config(
        SPEC, Posture("trending_down", 1.0, 1.0, -3.0, -2.0, 0, True, True)
    )
    assert _spreads(down["sell_spreads"])[0] == pytest.approx(2 * BPS)
    assert _spreads(down["buy_spreads"])[0] == pytest.approx(6 * BPS)


def test_the_spread_floor_is_the_market_own_fee():
    """A quote at the floor breaks even: buy at −f and sell at +f capture 2f,
    exactly the round trip. A fixed 3 bp was too wide for a HIP-3 perp and far
    too tight for a spot book."""
    assert SPEC.min_spread_bps == pytest.approx(SPEC.maker_fee_bps)
    dear = MarketSpec(
        connector_name="binance",
        trading_pair="SOL-USDT",
        total_amount_quote=500,
        picked_spread_bps=8.0,
    )
    assert dear.min_spread_bps == pytest.approx(7.5)  # binance spot
    # a lean that would quote inside the fee is pushed back out to it
    leaned = build_config(
        dear, Posture("trending_up", 1.0, 1.0, 3.0, 2.0, 0, True, True)
    )
    assert min(_spreads(leaned["buy_spreads"])) == pytest.approx(7.5 * BPS)


def test_pause_sets_kill_switch():
    cfg = build_config(SPEC, Posture("pause", 2.5, 1.0, 0.0, 0, 3.0, False, True))
    assert cfg["manual_kill_switch"] is True


def test_every_spread_respects_min():
    for regime in TIMING:
        for mult in (0.6, 1.0, 2.5):
            for shift in (-3.0, 0.0, 3.0):
                cfg = build_config(
                    SPEC, Posture(regime, mult, 1.0, shift, 0, 0, True, True)
                )
                for key in ("buy_spreads", "sell_spreads"):
                    assert min(_spreads(cfg[key])) >= SPEC.min_spread_bps * BPS - 1e-12


def test_spec_validation():
    with pytest.raises(ValueError):  # lowercase pair
        MarketSpec("hyperliquid_perpetual", "xyz:dram-usd", 500, 8)
    with pytest.raises(ValueError):  # no quote
        MarketSpec("hyperliquid_perpetual", "DRAMUSD", 500, 8)
    with pytest.raises(ValueError):  # leverage above the cap
        MarketSpec("hyperliquid_perpetual", "XYZ:DRAM-USD", 500, 8, leverage=10)
    with pytest.raises(ValueError):  # no capital
        MarketSpec("hyperliquid_perpetual", "XYZ:DRAM-USD", 0, 8)


def test_spot_and_perp_are_settled_by_the_connector():
    perp = MarketSpec("binance_perpetual", "SOL-USDT", 1000, 6.0, leverage=3)
    spot = MarketSpec("binance", "SOL-USDT", 1000, 6.0)
    assert (perp.market_type, spot.market_type) == ("perp", "spot")
    assert not perp.is_spot and spot.is_spot
    # a spot book has nothing to lever
    with pytest.raises(ValueError, match="leverage must be 1"):
        MarketSpec("binance", "SOL-USDT", 1000, 6.0, leverage=3)
    # and a mislabelled connector is refused rather than silently reinterpreted
    with pytest.raises(ValueError, match="looks like a perp"):
        MarketSpec("binance_perpetual", "SOL-USDT", 1000, 6.0, market_type="spot")


def test_position_mode_is_a_perp_field():
    perp = build_config(MarketSpec("binance_perpetual", "SOL-USDT", 1000, 6.0), NEUTRAL)
    spot = build_config(MarketSpec("binance", "SOL-USDT", 1000, 6.0), NEUTRAL)
    assert perp["position_mode"] == "ONEWAY" and perp["leverage"] == 1
    assert "position_mode" not in spot


def test_the_fee_floor_follows_the_venue():
    """Spot fees run several times perp fees, and a take-profit that is
    comfortably profitable on a perp loses money on spot."""
    perp = MarketSpec("binance_perpetual", "SOL-USDT", 1000, 6.0)
    spot = MarketSpec("binance", "SOL-USDT", 1000, 6.0)
    assert perp.maker_fee_bps == 2.0 and spot.maker_fee_bps == 7.5
    assert take_profit_floor(spot) > 3 * take_profit_floor(perp)
    assert build_config(spot, NEUTRAL)["take_profit"] >= take_profit_floor(spot)
    # an unknown venue is quoted wide, not tight
    unknown = MarketSpec("some_new_dex", "SOL-USDT", 1000, 6.0)
    assert unknown.maker_fee_bps == 10.0
    # and the real figure always wins
    assert (
        MarketSpec("binance", "SOL-USDT", 1000, 6.0, maker_fee_bps=1.0).maker_fee_bps
        == 1.0
    )


def test_config_diff():
    a = build_config(SPEC, NEUTRAL)
    b = build_config(SPEC, Posture("volatile", 2.0, 1.0, 0.0, 0, 1.5, False, True))
    diff = config_diff(a, b)
    assert "buy_spreads" in diff and "trading_pair" not in diff
    assert config_diff(None, a) == a


def test_an_order_sized_to_the_bare_minimum_is_refused():
    """The live failure of 2026-09-13: 200 quote at 0.2 allocation sizes each
    order to exactly the 10 USD minimum, the controller rounds the base amount
    down to the market's step, and Hyperliquid rejected all of them at 9.94."""
    bare = MarketSpec(
        **{**SPEC.__dict__, "total_amount_quote": 200, "portfolio_allocation": 0.2}
    )
    assert bare.order_notional == pytest.approx(10.0)
    with pytest.raises(ValueError, match="after rounding"):
        bare.check_order_size()
    # and the message names an allocation that actually clears it
    roomy = MarketSpec(**{**bare.__dict__, "portfolio_allocation": 0.3})
    roomy.check_order_size()
    assert roomy.order_notional == pytest.approx(15.0)


def test_the_outer_level_never_lands_inside_the_inner_one():
    """XYZ:DRAM-USD quotes 0.35 bp, where the playbook's S+1 (1.35) falls
    inside max(2, S/2) (2.0) and the ladder inverts."""
    from flybrain.posture import base_levels_from_spread

    for spread in (0.1, 0.35, 1.75, 2.0, 8.0, 20.0):
        first, second = base_levels_from_spread(spread)
        assert second > first, f"levels inverted at S={spread}"
    assert base_levels_from_spread(0.35) == (2.0, 3.0)
    assert base_levels_from_spread(8.0) == (4.0, 9.0)  # wide markets unchanged
    tight = build_config(
        MarketSpec(**{**SPEC.__dict__, "picked_spread_bps": 0.35}), NEUTRAL
    )
    buys = _spreads(tight["buy_spreads"])
    assert buys == sorted(buys) and len(set(buys)) == 2


def test_size_follows_arousal_and_stays_inside_both_limits():
    """The fly quotes more of the book when aroused, but never so little that
    an order falls under the venue minimum, nor more than the whole book."""
    # 200 quote at 0.3 is a 15 order; scaled down by 0.6 it would be 9, under
    # the 12 the venue needs once the base amount is rounded, so the floor
    # binds before the multiplier does.
    spec = MarketSpec(
        **{**SPEC.__dict__, "total_amount_quote": 200, "portfolio_allocation": 0.3}
    )
    hot = build_config(spec, Posture("ranging", 1.0, 2.5, 0.0, 0, 0, False, True))
    calm = build_config(spec, Posture("ranging", 1.0, 0.6, 0.0, 0, 0, False, True))
    assert hot["portfolio_allocation"] > spec.portfolio_allocation
    assert calm["portfolio_allocation"] < spec.portfolio_allocation
    assert calm["portfolio_allocation"] == pytest.approx(0.24)
    assert calm["portfolio_allocation"] == pytest.approx(spec.min_portfolio_allocation)
    # and a big book scaled up still cannot quote more than all of itself
    big = MarketSpec(**{**SPEC.__dict__, "portfolio_allocation": 0.5})
    assert build_config(big, Posture("ranging", 1.0, 2.5, 0.0, 0, 0, False, True))[
        "portfolio_allocation"
    ] == pytest.approx(1.0)


def test_the_config_matches_the_experts_balanced_profile():
    """Everything that is not spread, floor or feedback is the expert's vetted
    profile, and is stated rather than left to the controller's defaults."""
    cfg = build_config(SPEC, NEUTRAL)
    assert cfg["target_base_pct"] == 0.5
    assert cfg["min_base_pct"] == 0.35 and cfg["max_base_pct"] == 0.65
    assert cfg["max_active_executors_by_level"] == 3
    assert cfg["min_skew"] == 1.5
    assert cfg["buy_position_effectivization_time"] == 120
    assert cfg["sell_position_effectivization_time"] == 120
    assert cfg["price_distance_tolerance"] == 0.0005
    assert cfg["refresh_tolerance"] == 0.0005
    assert cfg["tolerance_scaling"] == 1.2
    assert cfg["global_tp_enabled"] is False and cfg["global_sl_enabled"] is True
    assert cfg["global_sl_activation_from"] == "target_base"
    assert cfg["global_pnl_reference"] == "position"
    assert cfg["tick_mode"] is False
