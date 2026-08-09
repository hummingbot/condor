from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.lp_agent_lite.routines import calculate_lp_requirements as sizing


def _config(**overrides):
    values = {
        "selected_allocation_quote": "5",
        "max_amount_quote_per_lp_position": "5",
        "remaining_session_quote": "10",
        "capital_headroom_pct": "5",
        "current_price": "100",
        "lower_price": "90",
        "upper_price": "110",
        "tick_spacing": 64,
        "available_base": "0",
        "available_quote": "10",
        "base_decimals": 9,
        "quote_decimals": 6,
    }
    values.update(overrides)
    return sizing.Config(**values)


def _run(config):
    raw = asyncio.run(sizing.run(config, None))
    assert len(raw) < 1_900
    result = json.loads(raw)
    assert result["report_id"] == "rpt001"
    assert result["report_error"] is None
    return result


def test_sizes_to_headroom_and_returns_preparation_shortfall():
    result = _run(_config())

    assert result["status"] == "feasible"
    assert result["feasible"] is True
    assert Decimal(result["authorization_quote"]) == Decimal("5")
    assert Decimal(result["usable_budget_quote"]) == Decimal("4.75")
    assert Decimal(result["budget_used_quote"]) <= Decimal("4.75")
    assert Decimal(result["budget_headroom_quote"]) >= Decimal("0.25")
    assert Decimal(result["base_shortfall"]) > 0
    assert Decimal(result["base_amount"]).as_tuple().exponent >= -9
    assert Decimal(result["quote_amount"]).as_tuple().exponent >= -6
    assert result["limiting_side"] == "budget"
    assert result["lower_tick"] % 64 == 0
    assert result["upper_tick"] % 64 == 0
    assert Decimal(result["aligned_lower_price"]) <= Decimal("90")
    assert Decimal(result["aligned_upper_price"]) >= Decimal("110")
    assert result["mutation"] is False


def test_uses_tightest_cap_and_current_inventory_without_exact_plan_failure():
    result = _run(
        _config(
            selected_allocation_quote="8",
            max_amount_quote_per_lp_position="6",
            remaining_session_quote="3",
            available_quote="2",
            available_base="0.005",
        )
    )

    assert result["status"] == "feasible"
    assert Decimal(result["authorization_quote"]) == Decimal("3")
    assert Decimal(result["budget_used_quote"]) <= Decimal("2.85")
    assert result["limiting_side"] in {"budget", "inventory", "quote_balance"}


def test_tiny_valid_authorization_becomes_structured_infeasible():
    result = _run(
        _config(
            selected_allocation_quote="0.00000001",
            max_amount_quote_per_lp_position="0.00000001",
            remaining_session_quote="0.00000001",
        )
    )

    assert result["status"] == "infeasible"
    assert result["feasible"] is False
    assert result["limiting_side"] == "precision"
    assert result["reasons"] == ["amount_below_token_precision"]


def test_zero_remaining_budget_is_an_ordinary_structured_shortage():
    result = _run(_config(remaining_session_quote="0"))

    assert result["status"] == "infeasible"
    assert result["authorization_quote"] == "0"
    assert result["budget_used_quote"] == "0"
    assert "error" not in result


@pytest.mark.parametrize(
    "overrides",
    [
        {"current_price": "nan"},
        {"available_base": "-1"},
        {"capital_headroom_pct": "100"},
        {"lower_price": "101"},
        {"tick_spacing": True},
        {"base_decimals": 19},
        {"unknown": 1},
    ],
)
def test_strict_numeric_and_cross_field_validation(overrides):
    with pytest.raises(ValidationError):
        _config(**overrides)


def test_whirlpool_tick_domain_failure_is_compact_and_non_mutating():
    result = _run(
        _config(
            current_price="1e30",
            lower_price="9e29",
            upper_price="2e30",
        )
    )

    assert result == {
        "schema": "lp_agent_lite.requirements.v1",
        "status": "error",
        "feasible": False,
        "error": "range_outside_whirlpool_ticks",
        "mutation": False,
        "report_id": "rpt001",
        "report_error": None,
    }
