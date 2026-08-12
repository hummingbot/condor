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
        "remaining_risk_quote": "10",
        "capital_headroom_pct": "5",
        "lp_open_balance_buffer_pct": "2",
        "allow_base_preparation": True,
        "current_price": "100",
        "lower_price": "90",
        "upper_price": "110",
        "tick_spacing": 64,
        "available_base_display": "0.0000",
        "available_quote_display": "10.0000",
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

    assert result["status"] == "preparation_required"
    assert result["feasible"] is True
    assert Decimal(result["authorization_quote"]) == Decimal("5")
    assert Decimal(result["usable_budget_quote"]) == Decimal("4.75")
    assert Decimal(result["budget_used_quote"]) <= Decimal("4.75")
    assert Decimal(result["budget_headroom_quote"]) >= Decimal("0.25")
    assert result["lp_open_balance_buffer_pct"] == "2"
    assert result["allow_base_preparation"] is True
    assert result["available_base_observed"] == "0"
    assert result["safe_available_base"] == "0"
    assert result["available_quote_observed"] == "10"
    assert result["safe_available_quote"] == "9.9999"
    assert result["base_balance_observation_quantum"] == "0.0001"
    assert result["quote_balance_observation_quantum"] == "0.0001"
    assert Decimal(result["base_shortfall"]) == Decimal(result["base_balance_required"])
    assert Decimal(result["quote_balance_required"]) <= Decimal("9.9999")
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
            available_quote_display="2.0000",
            available_base_display="0.0050",
        )
    )

    assert result["status"] == "preparation_required"
    assert Decimal(result["authorization_quote"]) == Decimal("3")
    assert Decimal(result["budget_used_quote"]) <= Decimal("2.85")
    assert result["limiting_side"] in {"budget", "inventory", "quote_balance"}


def test_remaining_core_risk_capacity_is_a_hard_sizing_cap():
    result = _run(_config(remaining_risk_quote="2"))

    assert result["status"] == "preparation_required"
    assert Decimal(result["authorization_quote"]) == Decimal("2")
    assert result["remaining_risk_quote"] == "2"
    assert Decimal(result["budget_used_quote"]) <= Decimal("1.9")


def test_post_preparation_sizes_down_to_buffered_actual_balances():
    result = _run(
        _config(
            allow_base_preparation=False,
            available_base_display="0.0100",
            available_quote_display="10.0000",
        )
    )

    assert result["status"] == "feasible"
    assert result["allow_base_preparation"] is False
    assert result["limiting_side"] == "base_balance"
    assert result["base_shortfall"] == "0"
    assert Decimal(result["base_balance_required"]) <= Decimal("0.0099")
    assert Decimal(result["quote_balance_required"]) <= Decimal("9.9999")


def test_post_preparation_never_requests_dust_top_up():
    result = _run(
        _config(
            allow_base_preparation=False,
            available_base_display="0.0000",
        )
    )

    assert result["status"] == "infeasible"
    assert result["feasible"] is False
    assert result["base_shortfall"] == "0"
    assert result["limiting_side"] == "base_balance"
    assert result["reasons"] == ["insufficient_base_balance"]


def test_rounded_wallet_balance_uses_lower_bound_before_open_buffer():
    result = _run(
        _config(
            allow_base_preparation=False,
            available_base_display="0.0016",
            available_quote_display="10.0000",
        )
    )

    assert result["status"] == "feasible"
    assert result["available_base_observed"] == "0.0016"
    assert result["base_balance_observation_quantum"] == "0.0001"
    assert result["safe_available_base"] == "0.0015"
    assert result["available_quote_observed"] == "10"
    assert result["safe_available_quote"] == "9.9999"
    assert Decimal(result["base_balance_required"]) <= Decimal("0.0015")
    assert Decimal(result["quote_balance_required"]) <= Decimal("9.9999")


def test_wallet_display_always_derives_nonzero_observation_quantum():
    result = _run(
        _config(
            allow_base_preparation=False,
            available_base_display="0.0016",
        )
    )

    assert result["status"] == "feasible"
    assert result["available_base_observed"] == "0.0016"
    assert result["base_balance_observation_quantum"] == "0.0001"
    assert result["safe_available_base"] == "0.0015"


def test_absent_wallet_token_plain_zero_uses_native_display_quantum():
    result = _run(_config(available_base_display="0"))

    assert result["available_base_display"] == "0"
    assert result["available_base_observed"] == "0"
    assert result["base_balance_observation_quantum"] == "0.0001"
    assert result["safe_available_base"] == "0"


def test_compact_wallet_display_expands_value_and_quantum():
    result = _run(
        _config(
            allow_base_preparation=False,
            available_base_display="1.2345K",
            available_quote_display="1.0000M",
            base_decimals=4,
        )
    )

    assert result["available_base_observed"] == "1234.5"
    assert result["base_balance_observation_quantum"] == "0.1"
    assert result["safe_available_base"] == "1234.4"
    assert result["available_quote_observed"] == "1000000"
    assert result["quote_balance_observation_quantum"] == "100"
    assert result["safe_available_quote"] == "999900"


def test_session_9_pump_balance_is_downsized_below_buffered_wallet_limit():
    result = _run(
        _config(
            selected_allocation_quote="2.95",
            max_amount_quote_per_lp_position="3",
            remaining_session_quote="7.1197205061097595",
            current_price="0.00274291826924525",
            lower_price="0.00252348480770563",
            upper_price="0.00296235173078487",
            tick_spacing=16,
            available_base_display="491.2615",
            available_quote_display="10.4368",
            base_decimals=6,
            quote_decimals=6,
            allow_base_preparation=False,
        )
    )

    assert result["status"] == "feasible"
    assert result["limiting_side"] == "base_balance"
    assert Decimal(result["base_amount"]) < Decimal("490.176165")
    assert result["safe_available_base"] == "491.2614"
    assert result["safe_available_quote"] == "10.4367"
    assert Decimal(result["base_balance_required"]) <= Decimal("491.2614")
    assert Decimal(result["quote_balance_required"]) <= Decimal("10.4367")


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
    assert result["limiting_side"] == "budget"
    assert result["reasons"] == [
        "base_amount_below_token_precision",
        "quote_amount_below_token_precision",
    ]


def test_zero_remaining_budget_is_an_ordinary_structured_shortage():
    result = _run(_config(remaining_session_quote="0"))

    assert result["status"] == "infeasible"
    assert result["authorization_quote"] == "0"
    assert result["budget_used_quote"] == "0"
    assert result["limiting_side"] == "budget"
    assert result["reasons"] == ["no_authorized_budget"]
    assert "error" not in result


def test_zero_remaining_risk_capacity_is_an_ordinary_structured_shortage():
    result = _run(_config(remaining_risk_quote="0"))

    assert result["status"] == "infeasible"
    assert result["authorization_quote"] == "0"
    assert result["reasons"] == ["no_authorized_budget"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"current_price": "nan"},
        {"available_base_display": "-1.0000"},
        {"available_base_display": "1"},
        {"available_quote_display": "1.00000"},
        {"available_quote_display": 1},
        {"capital_headroom_pct": "100"},
        {"lp_open_balance_buffer_pct": "6"},
        {"remaining_risk_quote": "-1"},
        {"allow_base_preparation": 1},
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
        "schema": "lp_agent_lite.requirements.v2",
        "status": "error",
        "feasible": False,
        "error": "range_outside_whirlpool_ticks",
        "mutation": False,
        "report_id": "rpt001",
        "report_error": None,
    }
