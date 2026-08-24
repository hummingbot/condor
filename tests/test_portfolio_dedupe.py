"""Unit tests for the valuation-only Hyperliquid unified-account dedup."""

import copy

import pytest

from utils.portfolio_dedupe import UNIFIED_NOTE, dedupe_hyperliquid_unified


def _state(spot_value=2646.7851, perp_value=2646.79, extra_perp=None):
    """Raw portfolio state in the Hummingbot API shape, from the real incident."""
    perp_balances = [
        {
            "token": "USD",
            "units": perp_value,
            "price": 1.0,
            "value": perp_value,
            "available_units": 0.0,
        }
    ]
    if extra_perp:
        perp_balances.append(extra_perp)
    return {
        "master_account": {
            "hyperliquid": [
                {
                    "token": "USDC",
                    "units": spot_value,
                    "price": 1.0,
                    "value": spot_value,
                    "available_units": spot_value,
                }
            ],
            "hyperliquid_perpetual": perp_balances,
            "binance": [
                {
                    "token": "USDT",
                    "units": 2229.0,
                    "price": 1.0,
                    "value": 2229.0,
                    "available_units": 2229.0,
                }
            ],
        }
    }


def _total(state):
    """Sum values the way format_portfolio_overview does."""
    return sum(
        b.get("value", 0)
        for connectors in state.values()
        for balances in connectors.values()
        for b in balances
        if b.get("value", 0) > 0
    )


def test_unified_collateral_counted_once():
    state = _state()
    doubled = _total(state)
    out, adjusted = dedupe_hyperliquid_unified(state)

    assert adjusted == {"master_account"}
    # The overlap (min of the two stables) is removed: the perp side keeps
    # only its 0.0049 excess over the spot balance.
    perp = out["master_account"]["hyperliquid_perpetual"]
    assert perp[0]["value"] == pytest.approx(0.0049, abs=1e-6)
    assert _total(out) == pytest.approx(doubled - 2646.7851, abs=1e-6)
    # Spot and unrelated connectors untouched.
    assert out["master_account"]["hyperliquid"][0]["value"] == 2646.7851
    assert out["master_account"]["binance"][0]["value"] == 2229.0


def test_input_is_not_mutated():
    state = _state()
    snapshot = copy.deepcopy(state)
    dedupe_hyperliquid_unified(state)
    assert state == snapshot


def test_open_position_drift_stands_down():
    # Perp equity drifted >1% from spot collateral (open position): no dedup.
    state = _state(spot_value=2646.79, perp_value=2800.0)
    out, adjusted = dedupe_hyperliquid_unified(state)
    assert adjusted == set()
    assert out == state


def test_missing_leg_is_noop():
    state = _state()
    del state["master_account"]["hyperliquid"]
    out, adjusted = dedupe_hyperliquid_unified(state)
    assert adjusted == set()
    assert out == state


def test_non_stable_perp_balance_survives():
    hype = {"token": "HYPE", "units": 10.0, "price": 40.0, "value": 400.0}
    state = _state(extra_perp=hype)
    out, adjusted = dedupe_hyperliquid_unified(state)
    assert adjusted == {"master_account"}
    perp = out["master_account"]["hyperliquid_perpetual"]
    assert [b for b in perp if b["token"] == "HYPE"][0]["value"] == 400.0


def test_within_tolerance_keeps_the_excess():
    # 2670 vs 2646.79 is inside the 1% band; only the overlap is removed.
    state = _state(spot_value=2646.79, perp_value=2670.0)
    out, adjusted = dedupe_hyperliquid_unified(state)
    assert adjusted == {"master_account"}
    perp_stable = sum(
        b["value"]
        for b in out["master_account"]["hyperliquid_perpetual"]
        if b["token"] == "USD"
    )
    assert perp_stable == pytest.approx(2670.0 - 2646.79, abs=1e-6)


def test_idempotent():
    once, _ = dedupe_hyperliquid_unified(_state())
    twice, adjusted = dedupe_hyperliquid_unified(once)
    assert adjusted == set()  # perp stable is ~0 now, the gate stands down
    assert twice == once


def test_alias_keys_usd_value_and_asset():
    state = {
        "master_account": {
            "hyperliquid": [{"asset": "USDC", "usd_value": 100.0, "total_balance": 100.0}],
            "hyperliquid_perpetual": [{"asset": "USD", "usd_value": 100.0, "total_balance": 100.0}],
        }
    }
    out, adjusted = dedupe_hyperliquid_unified(state)
    assert adjusted == {"master_account"}
    assert out["master_account"]["hyperliquid_perpetual"][0]["usd_value"] == pytest.approx(0.0)
    assert out["master_account"]["hyperliquid_perpetual"][0]["total_balance"] == pytest.approx(0.0)


def test_accounts_are_independent():
    state = _state()
    state["other_account"] = {
        "hyperliquid": [{"token": "USDC", "value": 500.0, "units": 500.0}],
        "hyperliquid_perpetual": [{"token": "USD", "value": 900.0, "units": 900.0}],
    }
    out, adjusted = dedupe_hyperliquid_unified(state)
    assert adjusted == {"master_account"}
    assert out["other_account"]["hyperliquid_perpetual"][0]["value"] == 900.0


def test_non_dict_state_passes_through():
    for state in (None, [], "oops", 42):
        out, adjusted = dedupe_hyperliquid_unified(state)
        assert out == state
        assert adjusted == set()


def test_note_text_matches_frontend_expectation():
    assert UNIFIED_NOTE == (
        "Unified account — USDC balance is reflected in the hyperliquid (spot) balance."
    )
