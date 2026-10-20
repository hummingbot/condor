"""Regression tests for the AGT 0.4.0 safety contract."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from condor.agents.config import load_full_config

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


def test_active_order_ids_are_exposed_for_exact_ownership_matching(monkeypatch):
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
    assert "sets must match exactly" in agent
    assert "rerun `position_mode_check`" in agent
    assert "- set_account_position_mode_and_leverage" in agent
    assert "without** `position_mode`" in agent
    assert "Never call it for a CLOSE" in agent
    assert "These are two calls, not an atomic transaction" in agent
    assert "`exchange_leverage` must equal" in agent
    for loop in loops:
        assert "max_open_executors: 2" in loop
        assert "leverage: null" in loop
        assert "max_leverage" not in loop
        assert "profiles: LONG, SHORT, HOLD" in loop
        assert "no `stop_loss` or `trailing_stop`" in loop
        assert "Previous-session grid: create nothing" in loop
        assert "exactly match" in loop
        assert "rerun `position_mode_check`" in loop
        assert "call `set_account_position_mode_and_leverage`" in loop
        assert "omit `position_mode`" in loop
        assert "Never call it for a CLOSE" in loop
        assert "setter and create are separate calls" in loop
        assert "`exchange_leverage` must equal" in loop
