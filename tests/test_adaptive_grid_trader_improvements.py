"""Regression tests for the AGT 0.4.0 safety contract."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from condor.agents.config import load_full_config
from mcp_servers.hummingbot_api.executor_preferences import _merge_one_level

ROOT = Path(__file__).resolve().parents[1]
ROUTINES = ROOT / "agents" / "adaptive_grid_trader" / "routines"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROUTINES / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


position_mode = _load("position_mode_check")
hourly = _load("hourly_mtf_check")
baseline = _load("baseline_7d")


class _Trading:
    def __init__(self, mode, orders, positions, errors):
        self.mode = mode
        self.orders = orders
        self.positions = positions
        self.errors = errors
        self.active_order_call = None

    async def get_position_mode(self, _account, _connector):
        if "mode" in self.errors:
            raise self.errors["mode"]
        return self.mode

    async def get_active_orders(self, **kwargs):
        self.active_order_call = kwargs
        if "orders" in self.errors:
            raise self.errors["orders"]
        return self.orders

    async def get_open_positions(self, _account, _connector):
        if "positions" in self.errors:
            raise self.errors["positions"]
        return self.positions


class _Portfolio:
    def __init__(self, balances, errors):
        self.balances = balances
        self.errors = errors
        self.call = None

    async def get_state(self, **kwargs):
        self.call = kwargs
        if "balance" in self.errors:
            raise self.errors["balance"]
        return self.balances


def _run_mode(
    monkeypatch,
    mode=None,
    *,
    orders=None,
    positions=None,
    balances=None,
    errors=None,
    leverage=None,
):
    errors = errors or {}
    trading = _Trading(
        {"position_mode": "ONEWAY"} if mode is None else mode,
        [] if orders is None else orders,
        [] if positions is None else positions,
        errors,
    )
    portfolio = _Portfolio(
        (
            {
                "master_account": {
                    "binance_perpetual": [
                        {"token": "USDT", "units": 100, "available_units": 90}
                    ]
                }
            }
            if balances is None
            else balances
        ),
        errors,
    )
    client = SimpleNamespace(trading=trading, portfolio=portfolio)

    async def fake_get_client(*_args, **_kwargs):
        return client

    monkeypatch.setattr(position_mode, "get_client", fake_get_client)
    context = SimpleNamespace(_chat_id=7)
    config = position_mode.Config(trading_pair="SOL-USDT", leverage=leverage)
    output = asyncio.run(position_mode.run(config, context))
    return output, trading, portfolio


def test_confirmed_empty_state_is_safe_and_uses_exact_filters(monkeypatch):
    output, trading, portfolio = _run_mode(monkeypatch)

    assert "mode: ONEWAY" in output
    assert "oneway_ok: YES" in output
    assert "pair_orders: 0 [NONE]" in output
    assert "pair_position: FLAT" in output
    assert "pair_state_read: CONFIRMED" in output
    assert "quote_available_balance: 90 USDT" in output
    assert "balance_state_read: CONFIRMED" in output
    assert trading.active_order_call == {
        "account_names": ["master_account"],
        "connector_names": ["binance_perpetual"],
        "trading_pairs": ["SOL-USDT"],
        "limit": 1000,
    }
    assert portfolio.call == {
        "account_names": ["master_account"],
        "connector_names": ["binance_perpetual"],
        "refresh": True,
    }


def test_active_order_ids_are_exposed_for_pair_state_reporting(monkeypatch):
    output, _, _ = _run_mode(
        monkeypatch,
        orders=[{"client_order_id": "b"}, {"order_id": "a"}],
    )

    assert "pair_orders: 2 [a, b]" in output
    assert "pair_state_read: CONFIRMED" in output


@pytest.mark.parametrize(
    ("orders", "expected"),
    [({"unexpected": []}, "pair_orders: UNREADABLE"), ([{}], "UNREADABLE")],
)
def test_malformed_orders_are_not_treated_as_empty(monkeypatch, orders, expected):
    output, _, _ = _run_mode(monkeypatch, orders=orders)

    assert expected in output
    assert "pair_state_read: UNREADABLE" in output


def test_order_read_failure_fails_closed_without_secret_text(monkeypatch):
    output, _, _ = _run_mode(
        monkeypatch,
        errors={"orders": RuntimeError("secret exchange payload")},
    )

    assert "pair_orders: UNREADABLE" in output
    assert "pair_state_read: UNREADABLE" in output
    assert "secret exchange payload" not in output


def test_incomplete_order_page_fails_closed(monkeypatch):
    output, _, _ = _run_mode(
        monkeypatch,
        orders={
            "data": [{"client_order_id": "a"}],
            "pagination": {"has_more": True, "next_cursor": "page-2"},
        },
    )

    assert "pair_orders: UNREADABLE" in output
    assert "pair_state_read: UNREADABLE" in output


def test_other_pair_position_does_not_make_target_pair_nonflat(monkeypatch):
    output, _, _ = _run_mode(
        monkeypatch,
        positions=[{"trading_pair": "BTC-USDT", "amount": 1}],
    )

    assert "pair_position: FLAT" in output
    assert "pair_state_read: CONFIRMED" in output


@pytest.mark.parametrize(
    ("position", "state", "pnl"),
    [
        (
            {
                "trading_pair": "SOL-USDT",
                "amount": "2.5",
                "side": "LONG",
                "unrealized_pnl": "-1.25",
                "leverage": "5",
            },
            "LONG 2.5",
            "-1.25",
        ),
        (
            {
                "tradingPair": "SOL-USDT",
                "net_amount_base": "-3",
                "unrealized_pnl_quote": "0.5",
                "leverage": 5,
            },
            "SHORT 3",
            "0.5",
        ),
        (
            {
                "trading_pair": "SOL-USDT",
                "side": "BOTH",
                "amount": "1.25",
                "unrealized_pnl": "0.2",
                "leverage": 5,
            },
            "LONG 1.25",
            "0.2",
        ),
        (
            {
                "trading_pair": "SOL-USDT",
                "side": "BOTH",
                "amount": "-1.25",
                "unrealized_pnl": "-0.2",
                "leverage": "5",
            },
            "SHORT 1.25",
            "-0.2",
        ),
    ],
)
def test_same_pair_position_reports_side_amount_and_pnl(
    monkeypatch, position, state, pnl
):
    output, _, _ = _run_mode(monkeypatch, positions=[position])

    assert f"pair_position: {state}" in output
    assert f"pair_unrealized_pnl: {pnl}" in output
    assert "exchange_leverage: 5x" in output
    assert "leverage_match: YES" in output
    assert "leverage_state_read: CONFIRMED" in output
    assert "pair_state_read: CONFIRMED" in output


def test_live_position_leverage_mismatch_fails_closed(monkeypatch):
    output, _, _ = _run_mode(
        monkeypatch,
        positions=[
            {
                "trading_pair": "SOL-USDT",
                "side": "BOTH",
                "amount": "0.56",
                "unrealized_pnl": "0.0384",
                "leverage": "1",
            }
        ],
    )

    assert "pair_position: LONG 0.56" in output
    assert "exchange_leverage: 1x" in output
    assert "leverage_match: NO" in output
    assert "leverage_state_read: MISMATCH" in output


def test_live_position_without_exchange_leverage_fails_closed(monkeypatch):
    output, _, _ = _run_mode(
        monkeypatch,
        positions=[
            {
                "trading_pair": "SOL-USDT",
                "side": "BOTH",
                "amount": "0.56",
                "unrealized_pnl": "0.0384",
            }
        ],
    )

    assert "exchange_leverage: UNREADABLE" in output
    assert "leverage_match: UNREADABLE" in output
    assert "leverage_state_read: UNREADABLE" in output


@pytest.mark.parametrize(
    "position",
    [
        {
            "trading_pair": "SOL-USDT",
            "side": "LONG",
            "amount": "-2.5",
            "leverage": "5",
        },
        {
            "trading_pair": "SOL-USDT",
            "side": "SHORT",
            "amount": "3",
            "leverage": "5",
        },
        {
            "trading_pair": "SOL-USDT",
            "side": "UNKNOWN",
            "amount": "1",
            "leverage": "5",
        },
    ],
)
def test_contradictory_or_unknown_position_side_fails_closed(monkeypatch, position):
    output, _, _ = _run_mode(monkeypatch, positions=[position])

    assert "pair_position: UNREADABLE" in output
    assert "pair_state_read: UNREADABLE" in output


def test_malformed_position_is_not_treated_as_flat(monkeypatch):
    output, _, _ = _run_mode(
        monkeypatch,
        positions=[{"trading_pair": "SOL-USDT", "amount": "bad"}],
    )

    assert "pair_position: UNREADABLE" in output
    assert "pair_state_read: UNREADABLE" in output


def test_balance_read_failure_fails_closed(monkeypatch):
    output, _, _ = _run_mode(
        monkeypatch,
        errors={"balance": RuntimeError("private details")},
    )

    assert "quote_available_balance: UNREADABLE" in output
    assert "balance_state_read: UNREADABLE" in output
    assert "private details" not in output


def test_malformed_balance_is_not_treated_as_zero(monkeypatch):
    output, _, _ = _run_mode(monkeypatch, balances={"unexpected": {}})

    assert "quote_available_balance: UNREADABLE" in output
    assert "balance_state_read: UNREADABLE" in output


def test_position_mode_requires_positive_oneway_confirmation(monkeypatch):
    output, _, _ = _run_mode(monkeypatch, {"position_mode": "ONEWAY"})

    assert "mode: ONEWAY" in output
    assert "oneway_ok: YES" in output
    assert "mode_read: CONFIRMED" in output


def test_hedge_mode_is_not_an_allowed_trading_profile(monkeypatch):
    output, _, _ = _run_mode(monkeypatch, {"positionMode": "HEDGE"})

    assert "mode: HEDGE" in output
    assert "oneway_ok: NO" in output
    assert "two_sided_allowed" not in output


def test_unreadable_mode_is_unknown_not_defaulted_to_oneway(monkeypatch):
    output, _, _ = _run_mode(monkeypatch, {"unexpected": "shape"})

    assert "mode: UNKNOWN" in output
    assert "oneway_ok: NO" in output
    assert "mode_read: UNREADABLE" in output
    assert "defaulted to ONEWAY" not in output


def test_mode_error_is_unknown_and_does_not_expose_exception_text(monkeypatch):
    output, _, _ = _run_mode(
        monkeypatch,
        errors={"mode": RuntimeError("secret exchange payload")},
    )

    assert "mode: UNKNOWN" in output
    assert "oneway_ok: NO" in output
    assert "mode_read: ERROR: RuntimeError" in output
    assert "secret exchange payload" not in output


@pytest.mark.parametrize("leverage", [None, 3, 3.0, "4"])
def test_valid_leverage_is_resolved_explicitly(monkeypatch, leverage):
    output, _, _ = _run_mode(monkeypatch, leverage=leverage)

    expected = 5 if leverage is None else float(leverage)
    assert f"leverage: {expected:g}" in output
    assert "leverage_state_read: CONFIRMED" in output
    assert ("(default)" in output) is (leverage is None)


@pytest.mark.parametrize(
    "leverage", [True, "bad", 0, -1, 2.5, float("inf"), float("nan")]
)
def test_invalid_leverage_fails_closed(monkeypatch, leverage):
    output, _, _ = _run_mode(monkeypatch, leverage=leverage)

    assert "leverage: INVALID" in output
    assert "leverage_state_read: INVALID" in output


def test_strategy_specific_top_level_leverage_survives_config_loading(tmp_path):
    assert load_full_config(tmp_path, {"leverage": None})["leverage"] is None
    assert load_full_config(tmp_path, {"leverage": 7})["leverage"] == 7


def test_hourly_profiles_are_one_way_only_with_explicit_lean():
    assert hourly._synthesise_profile("BULLISH", "BULLISH")[:2] == (
        "LONG_GRID",
        "LONG",
    )
    assert hourly._synthesise_profile("BEARISH", "BEARISH")[:2] == (
        "SHORT_GRID",
        "SHORT",
    )
    assert hourly._synthesise_profile("BULLISH", "NEUTRAL")[:2] == (
        "HOLD",
        "LONG",
    )
    assert hourly._synthesise_profile("BULLISH", "BEARISH")[:2] == (
        "HOLD",
        "NONE",
    )


def test_hold_has_no_actionable_price_levels():
    assert hourly._price_levels("HOLD", 100.0, 4.0) == (None, None, None)
    assert hourly._price_levels("LONG_GRID", 100.0, 4.0) == (96.0, 112.0, 94.0)
    assert hourly._price_levels("SHORT_GRID", 100.0, 4.0) == (88.0, 104.0, 106.0)


def test_volatility_uses_separate_seven_day_atr_reference():
    candles = [
        {"high": 101, "low": 99, "close": 100},
        {"high": 102, "low": 98, "close": 100},
    ]

    assert hourly._volatility_level(7.9, 10.0, candles)[0] == "LOW"
    assert hourly._volatility_level(8.0, 10.0, candles)[0] == "MODERATE"
    assert hourly._volatility_level(12.0, 10.0, candles)[0] == "MODERATE"
    assert hourly._volatility_level(12.1, 10.0, candles)[0] == "HIGH"


def test_baseline_rolling_atr_reference_is_stable():
    candles = [
        {"high": 101 + index, "low": 99 + index, "close": 100 + index}
        for index in range(20)
    ]

    values = baseline._rolling_atr_values(candles, 5)

    assert values
    assert all(value == 2.0 for value in values)


def test_agent_and_loops_encode_the_safety_contract():
    agent = (ROOT / "agents" / "adaptive_grid_trader" / "AGENT.md").read_text()
    loops = [
        (
            ROOT / "agents" / "adaptive_grid_trader" / "loops" / name / "loop.md"
        ).read_text()
        for name in ("btc_usdt_adaptive_grid", "sol_usdt_adaptive_grid")
    ]

    assert "- create_order_executor" in agent
    assert "profit-taking;\n3. stale-grid recycling" in agent
    assert "Never describe an old executor as resumed or adopted" in agent
    assert "do not require or claim an exact executor/order-ID match" in agent
    assert "do not substitute price matching" in agent
    assert "controller_ids=[current_session_id]" in agent
    assert "Never use nested `Config: controller_id` as ownership evidence" in agent
    assert "cannot detect a stale AGT order" in agent
    assert "rerun `position_mode_check`" in agent
    assert "- set_account_position_mode_and_leverage" in agent
    assert "without** `position_mode`" in agent
    assert "Never call it for a CLOSE" in agent
    assert "These are two calls, not an atomic transaction" in agent
    assert "`exchange_leverage` must equal" in agent
    assert "### Explicit grid-create contract" in agent
    assert "A missing resting TP is not itself a fault" in agent
    assert "roughly 75% of opening levels immediately marketable" in agent
    assert "controller IDs only when authoritatively exposed" in agent
    assert "not an executor-config field" in agent
    assert "derived `avg_take_profit`" in agent
    for loop in loops:
        assert "max_open_executors: 2" in loop
        assert "leverage: null" in loop
        assert "max_leverage" not in loop
        assert "profiles: LONG, SHORT, HOLD" in loop
        assert "no `stop_loss` or `trailing_stop`" in loop
        assert "Previous-session grid: create nothing" in loop
        assert "Do not require or claim exact ID matching" in loop
        assert "do not price-match orders" in loop
        assert "controller_ids=[current_session_id]" in loop
        assert "Never use nested `Config: controller_id`" in loop
        assert "weaker exclusivity-based attribution" in loop
        assert "rerun `position_mode_check`" in loop
        assert "call `set_account_position_mode_and_leverage`" in loop
        assert "omit `position_mode`" in loop
        assert "Never call it for a CLOSE" in loop
        assert "setter and create are separate calls" in loop
        assert "`exchange_leverage` must equal" in loop
        assert "`take_profit: 0.001`" in loop
        assert "`open_order_type: 3`; `take_profit_order_type: 3`" in loop
        assert "`min_spread_between_orders: 0.001`" in loop
        assert "`max_orders_per_batch: 1`; `order_frequency: 5`" in loop
        assert "`safe_extra_spread: 0.0001`" in loop
        assert "`coerce_tp_to_step: false`" in loop
        assert "`deduct_base_fees: false`" in loop
        assert "`save_as_default: false`" in loop
        assert "then call `get_executor` and verify every executor-config field" in loop
        assert "`save_as_default` is request-only" in loop
        assert "derived `avg_take_profit`" in loop
        assert "Do not require a resting TP order" in loop
        assert "roughly 75% of opening levels immediately marketable" in loop


@pytest.mark.parametrize(
    ("loop_name", "amount", "minimum", "max_open", "level_bound"),
    [
        ("sol_usdt_adaptive_grid", 90, 7, 12, 12),
        ("btc_usdt_adaptive_grid", 54, 6.5, 8, 7),
    ],
)
def test_loop_grid_level_bound_includes_hummingbot_notional_margin(
    loop_name, amount, minimum, max_open, level_bound
):
    loop = (
        ROOT / "agents" / "adaptive_grid_trader" / "loops" / loop_name / "loop.md"
    ).read_text()

    assert int(amount / (minimum * 1.05)) == level_bound
    assert f"`max_open_orders: {max_open}`" in loop
    assert f"= {level_bound}` levels before further base-increment rounding" in loop
    assert "separately caps simultaneous resting orders" in loop
    assert "Fail before create if the fully quantized effective minimum" in loop


def test_liquidation_guard_mirrors_hummingbot_level_count_caps():
    guard = (
        ROOT
        / "agents"
        / "adaptive_grid_trader"
        / "skills"
        / "liquidation_guard"
        / "SKILL.md"
    ).read_text()
    agent = (ROOT / "agents" / "adaptive_grid_trader" / "AGENT.md").read_text()

    assert "minimum_notional × 1.05" in guard
    assert "base_amount_increment" in guard
    assert (
        "levels_by_budget = floor(total_amount_quote / effective_minimum_quote)"
        in guard
    )
    assert "levels_by_spacing = floor(range_fraction / effective_spacing)" in guard
    assert "levels = min(levels_by_budget, levels_by_spacing)" in guard
    assert "one-level fallback would exceed the configured budget" in guard
    assert "If any exchange rule or current price" in guard
    assert "floor(total_amount_quote / effective_minimum_quote)" in agent
    assert (
        "floor(((end_price - start_price) / start_price) / effective_spacing)" in agent
    )
    assert "at most 12 levels for SOL and at most 7 for BTC" in agent
    assert "fail before create if even one effective-minimum level" in agent


def test_explicit_grid_config_overrides_every_behavior_bearing_saved_default():
    saved = {
        "min_spread_between_orders": 0.9,
        "min_order_amount_quote": 999,
        "max_open_orders": 99,
        "max_orders_per_batch": 99,
        "order_frequency": 999,
        "activation_bounds": 0.9,
        "safe_extra_spread": 0.9,
        "leverage": 99,
        "keep_position": True,
        "coerce_tp_to_step": True,
        "deduct_base_fees": True,
        "level_id": "saved-default",
        "triple_barrier_config": {
            "take_profit": 0.9,
            "time_limit": 1,
            "open_order_type": 1,
            "take_profit_order_type": 1,
        },
    }
    explicit = {
        "min_spread_between_orders": 0.001,
        "min_order_amount_quote": 7,
        "max_open_orders": 12,
        "max_orders_per_batch": 1,
        "order_frequency": 5,
        "activation_bounds": 0.002,
        "safe_extra_spread": 0.0001,
        "leverage": 5,
        "keep_position": False,
        "coerce_tp_to_step": False,
        "deduct_base_fees": False,
        "level_id": "adaptive_grid_trader.sol_usdt_adaptive_grid_8",
        "triple_barrier_config": {
            "take_profit": 0.001,
            "time_limit": 43200,
            "open_order_type": 3,
            "take_profit_order_type": 3,
        },
    }

    assert _merge_one_level(saved, explicit) == explicit


def test_volatility_label_uses_current_atr_not_baseline_blend(monkeypatch):
    # Constant 2.0 true range: current ATR is 2.0; a 1.0 baseline would blend it to 1.5.
    candles = [{"high": 101.0, "low": 99.0, "close": 100.0} for _ in range(30)]

    class MarketData:
        async def get_candles(self, *args, **kwargs):
            return candles

    async def fake_get_client(*args, **kwargs):
        return SimpleNamespace(market_data=MarketData())

    seen = {}
    real_volatility_level = hourly._volatility_level

    def spy(atr, atr_reference, window):
        seen["atr"] = atr
        return real_volatility_level(atr, atr_reference, window)

    monkeypatch.setattr(hourly, "get_client", fake_get_client)
    monkeypatch.setattr(hourly, "_volatility_level", spy)
    config = hourly.Config(baseline_atr=1.0, atr_reference_7d=1.5)

    asyncio.run(hourly.run(config, SimpleNamespace(_chat_id=0)))

    assert seen["atr"] == pytest.approx(2.0)


def test_deploy_proof_comes_from_the_leverage_call_not_the_create_result():
    for pair in ("btc_usdt_adaptive_grid", "sol_usdt_adaptive_grid"):
        loop = (
            ROOT / "agents" / "adaptive_grid_trader" / "loops" / pair / "loop.md"
        ).read_text()
        assert "create result carries no leverage acknowledgement" in loop
        assert "Require the create result's successful leverage-setup" not in loop
