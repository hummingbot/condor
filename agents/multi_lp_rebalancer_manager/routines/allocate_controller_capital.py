"""Deterministically allocate one quote budget across selected controller slots."""

from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

CATEGORY = "Multi LP Controller Allocation"
SCHEMA = "multi_lp_rebalancer_manager.allocation.v1"
MAX_RESULT_CHARS = 1_899
_SAFETY_FIELDS = ("price_stability", "liquidity_depth", "execution_simplicity")
_PROFILES = ("conservative", "balanced", "high_yield")


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise ValueError("value must be a decimal number") from exc
    if not number.is_finite():
        raise ValueError("value must be finite")
    return number


class SafetyWeights(BaseModel):
    """Configured safety calculation weights."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    price_stability: Decimal = Field(ge=0, le=1)
    liquidity_depth: Decimal = Field(ge=0, le=1)
    execution_simplicity: Decimal = Field(ge=0, le=1)

    @field_validator(*_SAFETY_FIELDS, mode="before")
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)

    @model_validator(mode="after")
    def exact_sum(self) -> "SafetyWeights":
        if sum((getattr(self, key) for key in _SAFETY_FIELDS), Decimal(0)) != 1:
            raise ValueError("allocation_safety_weights must sum exactly to 1")
        return self


class ProfileExponents(BaseModel):
    """Configured exponent for each risk profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conservative: Decimal = Field(gt=0)
    balanced: Decimal = Field(gt=0)
    high_yield: Decimal = Field(gt=0)

    @field_validator(*_PROFILES, mode="before")
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)


class SelectedPool(BaseModel):
    """One LLM-selected vacant-slot candidate and its scanner components."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: StrictInt = Field(ge=1, le=3)
    pool_address: StrictStr = Field(min_length=32, max_length=64)
    base_mint: StrictStr = Field(min_length=32, max_length=64)
    price_stability: Decimal = Field(ge=0, le=1)
    liquidity_depth: Decimal = Field(ge=0, le=1)
    execution_simplicity: Decimal = Field(ge=0, le=1)

    @field_validator(*_SAFETY_FIELDS, mode="before")
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)


class ExistingAllocation(BaseModel):
    """Capital still reserved by an adopted, active, or unresolved controller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: StrictInt = Field(ge=1, le=3)
    pool_address: StrictStr = Field(min_length=32, max_length=64)
    base_mint: StrictStr = Field(min_length=32, max_length=64)
    amount_quote: Decimal = Field(gt=0)

    @field_validator("amount_quote", mode="before")
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)


class Config(BaseModel):
    """Validate bounded occupancy and allocate all currently unreserved quote."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_amount_quote: Decimal = Field(gt=0)
    min_controller_amount_quote: Decimal = Field(gt=0)
    quote_token_decimals: StrictInt = Field(ge=0, le=18)
    target_active_controllers: StrictInt = Field(ge=1, le=3)
    max_active_controllers: StrictInt = Field(ge=1, le=3)
    risk_profile: Literal["conservative", "balanced", "high_yield"]
    allocation_safety_weights: SafetyWeights
    allocation_profile_exponents: ProfileExponents
    selected_pools: list[SelectedPool] = Field(min_length=1, max_length=3)
    existing_allocations: list[ExistingAllocation] = Field(
        default_factory=list, max_length=3
    )

    @field_validator("total_amount_quote", "min_controller_amount_quote", mode="before")
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)

    @model_validator(mode="after")
    def portfolio_contract(self) -> "Config":
        if self.target_active_controllers > self.max_active_controllers:
            raise ValueError(
                "target_active_controllers cannot exceed max_active_controllers"
            )
        if (
            self.total_amount_quote
            < self.min_controller_amount_quote * self.target_active_controllers
        ):
            raise ValueError("portfolio budget cannot fund every target slot minimum")
        combined = [*self.existing_allocations, *self.selected_pools]
        if len(combined) > self.max_active_controllers:
            raise ValueError(
                "existing plus selected controllers exceed maximum occupancy"
            )
        for attribute, label in (
            ("slot", "slots"),
            ("pool_address", "pool addresses"),
            ("base_mint", "base mints"),
        ):
            values = [getattr(item, attribute) for item in combined]
            if len(values) != len(set(values)):
                raise ValueError(f"controller {label} must be distinct")
        reserved = sum(
            (item.amount_quote for item in self.existing_allocations), Decimal(0)
        )
        if reserved >= self.total_amount_quote:
            raise ValueError("existing allocations leave no quote for selected pools")
        return self


SafetyWeights.model_rebuild(_types_namespace={"Any": Any, "Decimal": Decimal})
ProfileExponents.model_rebuild(_types_namespace={"Any": Any, "Decimal": Decimal})
SelectedPool.model_rebuild(
    _types_namespace={
        "Any": Any,
        "Decimal": Decimal,
        "StrictInt": StrictInt,
        "StrictStr": StrictStr,
    }
)
ExistingAllocation.model_rebuild(
    _types_namespace={
        "Any": Any,
        "Decimal": Decimal,
        "StrictInt": StrictInt,
        "StrictStr": StrictStr,
    }
)
Config.model_rebuild(
    _types_namespace={
        "Any": Any,
        "Decimal": Decimal,
        "Literal": Literal,
        "StrictInt": StrictInt,
        "SafetyWeights": SafetyWeights,
        "ProfileExponents": ProfileExponents,
        "SelectedPool": SelectedPool,
        "ExistingAllocation": ExistingAllocation,
    }
)


def _format(value: Decimal) -> str:
    return format(value, "f")


def _encode(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) <= MAX_RESULT_CHARS:
        return encoded
    return json.dumps(
        {
            "schema": SCHEMA,
            "status": "unavailable",
            "reason": "response_limit",
            "mutation": False,
        },
        separators=(",", ":"),
    )


async def run(config: Config, context: Any) -> str:
    """Return an exact allocation receipt; this routine never mutates external state."""

    del context
    reserved = sum(
        (item.amount_quote for item in config.existing_allocations), Decimal(0)
    )
    available = config.total_amount_quote - reserved
    exponent = getattr(config.allocation_profile_exponents, config.risk_profile)
    quantum = Decimal(1).scaleb(-config.quote_token_decimals)

    with localcontext() as decimal_context:
        decimal_context.prec = 50
        scored: list[tuple[SelectedPool, Decimal, Decimal]] = []
        for pool in config.selected_pools:
            safety = sum(
                getattr(pool, key) * getattr(config.allocation_safety_weights, key)
                for key in _SAFETY_FIELDS
            )
            weight = decimal_context.power(
                Decimal("0.5") + Decimal("0.5") * safety, exponent
            )
            scored.append((pool, safety, weight))
        total_weight = sum((item[2] for item in scored), Decimal(0))
        allocations = [
            (available * weight / total_weight).quantize(quantum, rounding=ROUND_DOWN)
            for _, _, weight in scored
        ]

    # The approved policy assigns all quote-token rounding remainder to the safest pool.
    safest_index = min(
        range(len(scored)),
        key=lambda index: (-scored[index][1], scored[index][0].pool_address),
    )
    remainder = available - sum(allocations, Decimal(0))
    allocations[safest_index] += remainder
    if any(amount < config.min_controller_amount_quote for amount in allocations):
        return _encode(
            {
                "schema": SCHEMA,
                "status": "rejected_before_submit",
                "reason": "at least one selected slot is below min_controller_amount_quote",
                "reserved_quote": _format(reserved),
                "available_quote": _format(available),
                "mutation": False,
            },
        )

    result = []
    for (pool, safety, weight), amount in sorted(
        zip(scored, allocations, strict=True), key=lambda item: item[0][0].slot
    ):
        result.append(
            {
                "slot": pool.slot,
                "pool_address": pool.pool_address,
                "base_mint": pool.base_mint,
                "safety": _format(safety),
                "allocation_weight": _format(weight),
                "amount_quote": _format(amount),
            }
        )

    return _encode(
        {
            "schema": SCHEMA,
            "status": "complete",
            "risk_profile": config.risk_profile,
            "portfolio_total_quote": _format(config.total_amount_quote),
            "target_controllers": config.target_active_controllers,
            "resulting_controllers": len(config.existing_allocations)
            + len(config.selected_pools),
            "reserved_quote": _format(reserved),
            "allocated_quote": _format(sum(allocations, Decimal(0))),
            "rounding_remainder_quote": _format(remainder),
            "remainder_pool": scored[safest_index][0].pool_address,
            "allocations": result,
            "mutation": False,
        },
    )
