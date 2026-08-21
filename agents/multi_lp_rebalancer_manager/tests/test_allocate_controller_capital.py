import asyncio
import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agents.multi_lp_rebalancer_manager.routines import (
    allocate_controller_capital as allocation,
)


def _address(character: str) -> str:
    return character * 32


def _config(**changes):
    data = {
        "total_amount_quote": 30,
        "min_controller_amount_quote": 1,
        "quote_token_decimals": 6,
        "target_active_controllers": 3,
        "max_active_controllers": 3,
        "risk_profile": "balanced",
        "allocation_safety_weights": {
            "price_stability": 0.5,
            "liquidity_depth": 0.35,
            "execution_simplicity": 0.15,
        },
        "allocation_profile_exponents": {
            "conservative": 2,
            "balanced": 1,
            "high_yield": 0.5,
        },
        "selected_pools": [
            {
                "slot": 1,
                "pool_address": _address("A"),
                "base_mint": _address("B"),
                "price_stability": 1,
                "liquidity_depth": 1,
                "execution_simplicity": 1,
            },
            {
                "slot": 2,
                "pool_address": _address("C"),
                "base_mint": _address("D"),
                "price_stability": 0.5,
                "liquidity_depth": 0.5,
                "execution_simplicity": 0.5,
            },
            {
                "slot": 3,
                "pool_address": _address("E"),
                "base_mint": _address("F"),
                "price_stability": 0,
                "liquidity_depth": 0,
                "execution_simplicity": 0,
            },
        ],
        "existing_allocations": [],
    }
    data.update(changes)
    return allocation.Config(**data)


def test_allocation_is_exact_deterministic_and_rewards_safety():
    raw = asyncio.run(allocation.run(_config(), None))
    result = json.loads(raw)
    amounts = [Decimal(row["amount_quote"]) for row in result["allocations"]]
    assert result["schema"] == allocation.SCHEMA
    assert result["status"] == "complete"
    assert result["mutation"] is False
    assert sum(amounts) == Decimal("30")
    assert amounts[0] > amounts[1] > amounts[2]
    assert result["remainder_pool"] == _address("A")
    assert len(raw) <= allocation.MAX_RESULT_CHARS == 1_899


def test_existing_allocation_is_preserved_and_only_free_quote_is_split():
    config = _config(
        selected_pools=_config().model_dump()["selected_pools"][1:],
        existing_allocations=[
            {
                "slot": 1,
                "pool_address": _address("A"),
                "base_mint": _address("B"),
                "amount_quote": 12,
            }
        ],
    )
    result = json.loads(asyncio.run(allocation.run(config, None)))
    assert Decimal(result["reserved_quote"]) == 12
    assert Decimal(result["allocated_quote"]) == 18
    assert sum(Decimal(row["amount_quote"]) for row in result["allocations"]) == 18


def test_two_suitable_candidates_are_valid_partial_target_and_use_full_budget():
    config = _config(selected_pools=_config().model_dump()["selected_pools"][:2])

    result = json.loads(asyncio.run(allocation.run(config, None)))
    amounts = [Decimal(row["amount_quote"]) for row in result["allocations"]]

    assert result["status"] == "complete"
    assert result["target_controllers"] == 3
    assert result["resulting_controllers"] == 2
    assert sum(amounts) == Decimal("30")
    assert Decimal(result["reserved_quote"]) + Decimal(
        result["allocated_quote"]
    ) == Decimal("30")


def test_target_is_desired_occupancy_and_maximum_remains_hard_ceiling():
    partial = _config(
        target_active_controllers=2,
        max_active_controllers=3,
        selected_pools=_config().model_dump()["selected_pools"][:1],
    )
    assert partial.target_active_controllers == 2

    data = _config().model_dump()
    data["target_active_controllers"] = 2
    data["max_active_controllers"] = 2
    with pytest.raises(ValidationError, match="exceed maximum occupancy"):
        allocation.Config(**data)


@pytest.mark.parametrize("duplicate_key", ["slot", "pool_address", "base_mint"])
def test_duplicate_slot_pool_or_base_fails_closed(duplicate_key):
    data = _config().model_dump()
    data["selected_pools"][1][duplicate_key] = data["selected_pools"][0][duplicate_key]
    with pytest.raises(ValidationError, match="must be distinct"):
        allocation.Config(**data)


def test_profile_policy_is_config_driven_not_hardcoded():
    config = _config(
        risk_profile="conservative",
        allocation_profile_exponents={
            "conservative": 1,
            "balanced": 1,
            "high_yield": 1,
        },
    )
    result = json.loads(asyncio.run(allocation.run(config, None)))
    assert result["status"] == "complete"
    assert Decimal(result["allocations"][0]["allocation_weight"]) == 1


def test_rejected_allocation_response_stays_below_transport_limit():
    config = _config(
        min_controller_amount_quote=9,
        risk_profile="conservative",
        allocation_profile_exponents={
            "conservative": 20,
            "balanced": 1,
            "high_yield": 1,
        },
    )
    raw = asyncio.run(allocation.run(config, None))
    result = json.loads(raw)

    assert result["status"] == "rejected_before_submit"
    assert len(raw) <= allocation.MAX_RESULT_CHARS == 1_899
