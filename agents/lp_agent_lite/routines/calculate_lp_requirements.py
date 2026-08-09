"""Calculate floored LP legs and BASE shortfall for one selected Orca range."""

from __future__ import annotations

import asyncio
import json
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from agents.lp_agent_lite.routines._reporting import DiagnosticTrace, report_result

CATEGORY = "Orca LP Arithmetic"
_SCHEMA = "lp_agent_lite.requirements.v1"
_TRANSPORT_MAX_CHARS = 1_900
_MIN_TICK = -443_636
_MAX_TICK = 443_636
_TICK_BASE = Decimal("1.0001")
_NUMERIC_FIELDS = (
    "selected_allocation_quote",
    "max_amount_quote_per_lp_position",
    "remaining_session_quote",
    "capital_headroom_pct",
    "current_price",
    "lower_price",
    "upper_price",
    "available_base",
    "available_quote",
)


class Config(BaseModel):
    """Calculate one selected Orca range's largest feasible double-sided size."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_allocation_quote: Decimal = Field(gt=0)
    max_amount_quote_per_lp_position: Decimal = Field(gt=0)
    remaining_session_quote: Decimal = Field(ge=0)
    capital_headroom_pct: Decimal = Field(ge=0, lt=100)
    current_price: Decimal = Field(gt=0)
    lower_price: Decimal = Field(gt=0)
    upper_price: Decimal = Field(gt=0)
    tick_spacing: StrictInt = Field(gt=0, le=32_768)
    available_base: Decimal = Field(ge=0)
    available_quote: Decimal = Field(ge=0)
    base_decimals: StrictInt = Field(ge=0, le=18)
    quote_decimals: StrictInt = Field(ge=0, le=18)

    @field_validator(*_NUMERIC_FIELDS, mode="before")
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("boolean is not a numeric amount")
        try:
            number = Decimal(str(value))
        except Exception as exc:
            raise ValueError("value must be a decimal number") from exc
        if not number.is_finite():
            raise ValueError("value must be finite")
        return number

    @model_validator(mode="after")
    def valid_range(self) -> "Config":
        if not self.lower_price < self.current_price < self.upper_price:
            raise ValueError("prices must satisfy lower < current < upper")
        return self


# Agent-local routines are executed without normal package module registration.
Config.model_rebuild(
    _types_namespace={
        "Any": Any,
        "Decimal": Decimal,
        "StrictInt": StrictInt,
    }
)


def _floor(value: Decimal, decimals: int) -> Decimal:
    unit = Decimal(1).scaleb(-decimals)
    return value.quantize(unit, rounding=ROUND_FLOOR)


def _text(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _tick(price: Decimal, decimal_factor: Decimal, rounding: str) -> int:
    with localcontext() as ctx:
        ctx.prec = 50
        exact = (price / decimal_factor).ln() / _TICK_BASE.ln()
    return int(exact.to_integral_value(rounding=rounding))


def _aligned_tick(raw_tick: int, spacing: int, *, upper: bool) -> int:
    quotient, remainder = divmod(raw_tick, spacing)
    return (quotient + (1 if upper and remainder else 0)) * spacing


def _price_at_tick(tick: int, decimal_factor: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        return (_TICK_BASE**tick) * decimal_factor


def _error(code: str) -> str:
    return json.dumps(
        {
            "schema": _SCHEMA,
            "status": "error",
            "feasible": False,
            "error": code,
            "mutation": False,
        },
        separators=(",", ":"),
    )


def _encode(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"))
    return encoded if len(encoded) < _TRANSPORT_MAX_CHARS else _error("transport_limit")


async def run(config: Config, context: Any) -> str:
    """Return compact sizing facts; perform no I/O and no mutation."""
    trace = DiagnosticTrace()
    trace.record("input_validated")

    async def finish(raw: str) -> str:
        return await report_result(
            raw,
            routine_name="calculate_lp_requirements",
            title="LP Agent Lite — LP Requirements Diagnostic",
            config=config,
            trace=trace,
            context=context,
        )

    try:
        await asyncio.sleep(0)
        with localcontext() as ctx:
            ctx.prec = 50
            factor = Decimal(10) ** (config.base_decimals - config.quote_decimals)
            lower_tick = _aligned_tick(
                _tick(config.lower_price, factor, ROUND_FLOOR),
                config.tick_spacing,
                upper=False,
            )
            upper_tick = _aligned_tick(
                _tick(config.upper_price, factor, ROUND_CEILING),
                config.tick_spacing,
                upper=True,
            )
            if lower_tick < _MIN_TICK or upper_tick > _MAX_TICK:
                trace.record(
                    "range_alignment",
                    "rejected",
                    lower_tick=lower_tick,
                    upper_tick=upper_tick,
                    reason="range_outside_whirlpool_ticks",
                )
                return await finish(_error("range_outside_whirlpool_ticks"))

            aligned_lower = _price_at_tick(lower_tick, factor)
            aligned_upper = _price_at_tick(upper_tick, factor)
            if not aligned_lower < config.current_price < aligned_upper:
                trace.record(
                    "range_alignment",
                    "rejected",
                    lower_tick=lower_tick,
                    upper_tick=upper_tick,
                    reason="aligned_range_excludes_current_price",
                )
                return await finish(_error("aligned_range_excludes_current_price"))
            trace.record(
                "range_alignment",
                lower_tick=lower_tick,
                upper_tick=upper_tick,
            )

            authorization = min(
                config.selected_allocation_quote,
                config.max_amount_quote_per_lp_position,
                config.remaining_session_quote,
            )
            usable_budget = authorization * (
                Decimal(1) - config.capital_headroom_pct / Decimal(100)
            )

            sqrt_price = config.current_price.sqrt()
            sqrt_lower = aligned_lower.sqrt()
            sqrt_upper = aligned_upper.sqrt()
            base_per_liquidity = (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper)
            quote_per_liquidity = sqrt_price - sqrt_lower
            value_per_liquidity = (
                base_per_liquidity * config.current_price + quote_per_liquidity
            )

            limits = {
                "budget": usable_budget / value_per_liquidity,
                "inventory": (
                    config.available_quote
                    + config.available_base * config.current_price
                )
                / value_per_liquidity,
                "quote_balance": config.available_quote / quote_per_liquidity,
            }
            limiting_side, liquidity = min(limits.items(), key=lambda item: item[1])

            available_base = _floor(config.available_base, config.base_decimals)
            base_amount = _floor(liquidity * base_per_liquidity, config.base_decimals)
            quote_amount = _floor(
                liquidity * quote_per_liquidity, config.quote_decimals
            )
            base_shortfall = max(base_amount - available_base, Decimal(0))
            budget_used = base_amount * config.current_price + quote_amount
            budget_headroom = max(authorization - budget_used, Decimal(0))
            feasible = base_amount > 0 and quote_amount > 0 and budget_used > 0

            payload = {
                "schema": _SCHEMA,
                "status": "feasible" if feasible else "infeasible",
                "feasible": feasible,
                "authorization_quote": _text(authorization),
                "usable_budget_quote": _text(usable_budget),
                "base_amount": _text(base_amount),
                "quote_amount": _text(quote_amount),
                "base_shortfall": _text(base_shortfall),
                "budget_used_quote": _text(budget_used),
                "budget_headroom_quote": _text(budget_headroom),
                "limiting_side": limiting_side if feasible else "precision",
                "lower_tick": lower_tick,
                "upper_tick": upper_tick,
                "aligned_lower_price": _text(aligned_lower),
                "aligned_upper_price": _text(aligned_upper),
                "reasons": [] if feasible else ["amount_below_token_precision"],
                "mutation": False,
            }
            trace.record(
                "requirements_calculated",
                status=payload["status"],
                limiting_side=payload["limiting_side"],
                feasible=feasible,
            )
            return await finish(_encode(payload))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        trace.record(
            "requirements_calculated",
            "error",
            error_type=type(exc).__name__,
        )
        return await finish(_error("calculation_error"))
