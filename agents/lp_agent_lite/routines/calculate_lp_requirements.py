"""Calculate buffered LP legs and BASE shortfall for one selected Orca range."""

from __future__ import annotations

import asyncio
import json
import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from agents.lp_agent_lite.routines._reporting import DiagnosticTrace, report_result

CATEGORY = "Orca LP Arithmetic"
_SCHEMA = "lp_agent_lite.requirements.v2"
_TRANSPORT_MAX_CHARS = 1_900
_MIN_TICK = -443_636
_MAX_TICK = 443_636
_TICK_BASE = Decimal("1.0001")
_BALANCE_DISPLAY = re.compile(r"^(?P<number>\d+\.\d{4})(?P<suffix>[KM]?)$")
_BALANCE_MULTIPLIERS = {
    "": Decimal(1),
    "K": Decimal(1_000),
    "M": Decimal(1_000_000),
}
_NUMERIC_FIELDS = (
    "selected_allocation_quote",
    "max_amount_quote_per_lp_position",
    "remaining_session_quote",
    "remaining_risk_quote",
    "capital_headroom_pct",
    "lp_open_balance_buffer_pct",
    "current_price",
    "lower_price",
    "upper_price",
)


class Config(BaseModel):
    """Calculate one selected Orca range's largest feasible double-sided size."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_allocation_quote: Decimal = Field(gt=0)
    max_amount_quote_per_lp_position: Decimal = Field(gt=0)
    remaining_session_quote: Decimal = Field(ge=0)
    remaining_risk_quote: Decimal = Field(ge=0)
    capital_headroom_pct: Decimal = Field(ge=0, lt=100)
    lp_open_balance_buffer_pct: Decimal = Field(ge=0, lt=100)
    allow_base_preparation: StrictBool
    current_price: Decimal = Field(gt=0)
    lower_price: Decimal = Field(gt=0)
    upper_price: Decimal = Field(gt=0)
    tick_spacing: StrictInt = Field(gt=0, le=32_768)
    available_base_display: StrictStr
    available_quote_display: StrictStr
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

    @field_validator("available_base_display", "available_quote_display")
    @classmethod
    def wallet_display(cls, value: str) -> str:
        _parse_balance_display(value)
        return value

    @model_validator(mode="after")
    def valid_range(self) -> "Config":
        if not self.lower_price < self.current_price < self.upper_price:
            raise ValueError("prices must satisfy lower < current < upper")
        if self.lp_open_balance_buffer_pct > self.capital_headroom_pct:
            raise ValueError(
                "lp_open_balance_buffer_pct cannot exceed capital_headroom_pct"
            )
        return self


# Agent-local routines are executed without normal package module registration.
Config.model_rebuild(
    _types_namespace={
        "Any": Any,
        "Decimal": Decimal,
        "StrictBool": StrictBool,
        "StrictInt": StrictInt,
        "StrictStr": StrictStr,
    }
)


def _floor(value: Decimal, decimals: int) -> Decimal:
    unit = Decimal(1).scaleb(-decimals)
    return value.quantize(unit, rounding=ROUND_FLOOR)


def _ceil(value: Decimal, decimals: int) -> Decimal:
    unit = Decimal(1).scaleb(-decimals)
    return value.quantize(unit, rounding=ROUND_CEILING)


def _text(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _parse_balance_display(display: str) -> tuple[Decimal, Decimal]:
    """Expand one native portfolio balance and return its displayed quantum."""
    if display == "0":
        return Decimal(0), Decimal("0.0001")
    match = _BALANCE_DISPLAY.fullmatch(display)
    if match is None:
        raise ValueError(
            "wallet balance must be exact absent-token zero or preserve the native "
            "four-decimal display and optional K/M suffix"
        )
    multiplier = _BALANCE_MULTIPLIERS[match.group("suffix")]
    return Decimal(match.group("number")) * multiplier, Decimal("0.0001") * multiplier


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
                config.remaining_risk_quote,
            )
            usable_budget = authorization * (
                Decimal(1) - config.capital_headroom_pct / Decimal(100)
            )
            open_balance_factor = Decimal(1) + (
                config.lp_open_balance_buffer_pct / Decimal(100)
            )
            available_base, base_observation_quantum = _parse_balance_display(
                config.available_base_display
            )
            available_quote, quote_observation_quantum = _parse_balance_display(
                config.available_quote_display
            )
            observed_available_base = _floor(available_base, config.base_decimals)
            observed_available_quote = _floor(available_quote, config.quote_decimals)
            safe_available_base = _floor(
                max(available_base - base_observation_quantum, Decimal(0)),
                config.base_decimals,
            )
            safe_available_quote = _floor(
                max(available_quote - quote_observation_quantum, Decimal(0)),
                config.quote_decimals,
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
                    safe_available_quote + safe_available_base * config.current_price
                )
                / value_per_liquidity,
                "quote_balance": safe_available_quote
                / (quote_per_liquidity * open_balance_factor),
            }
            if not config.allow_base_preparation:
                limits["base_balance"] = safe_available_base / (
                    base_per_liquidity * open_balance_factor
                )
            limiting_side, liquidity = min(limits.items(), key=lambda item: item[1])

            base_amount = _floor(liquidity * base_per_liquidity, config.base_decimals)
            quote_amount = _floor(
                liquidity * quote_per_liquidity, config.quote_decimals
            )
            base_balance_required = _ceil(
                base_amount * open_balance_factor, config.base_decimals
            )
            quote_balance_required = _ceil(
                quote_amount * open_balance_factor, config.quote_decimals
            )
            base_shortfall = (
                max(base_balance_required - safe_available_base, Decimal(0))
                if config.allow_base_preparation
                else Decimal(0)
            )
            budget_used = base_amount * config.current_price + quote_amount
            budget_headroom = max(authorization - budget_used, Decimal(0))
            balances_cover_open = quote_balance_required <= safe_available_quote and (
                config.allow_base_preparation
                or base_balance_required <= safe_available_base
            )
            feasible = (
                base_amount > 0
                and quote_amount > 0
                and budget_used > 0
                and balances_cover_open
            )
            status = (
                "preparation_required"
                if feasible and base_shortfall > 0
                else "feasible" if feasible else "infeasible"
            )
            reasons: list[str] = []
            if not feasible:
                if authorization <= 0:
                    reasons.append("no_authorized_budget")
                else:
                    if safe_available_quote <= 0:
                        reasons.append("insufficient_quote_balance")
                    if not config.allow_base_preparation and safe_available_base <= 0:
                        reasons.append("insufficient_base_balance")
                    if not reasons:
                        if base_amount <= 0:
                            reasons.append("base_amount_below_token_precision")
                        if quote_amount <= 0:
                            reasons.append("quote_amount_below_token_precision")
                        if quote_balance_required > safe_available_quote:
                            reasons.append("insufficient_quote_balance")
                        if (
                            not config.allow_base_preparation
                            and base_balance_required > safe_available_base
                        ):
                            reasons.append("insufficient_base_balance")
                reasons = list(dict.fromkeys(reasons))

            payload = {
                "schema": _SCHEMA,
                "status": status,
                "feasible": feasible,
                "authorization_quote": _text(authorization),
                "remaining_risk_quote": _text(config.remaining_risk_quote),
                "usable_budget_quote": _text(usable_budget),
                "lp_open_balance_buffer_pct": _text(config.lp_open_balance_buffer_pct),
                "allow_base_preparation": config.allow_base_preparation,
                "available_base_display": config.available_base_display,
                "available_quote_display": config.available_quote_display,
                "available_base_observed": _text(observed_available_base),
                "available_quote_observed": _text(observed_available_quote),
                "base_balance_observation_quantum": _text(base_observation_quantum),
                "quote_balance_observation_quantum": _text(quote_observation_quantum),
                "safe_available_base": _text(safe_available_base),
                "safe_available_quote": _text(safe_available_quote),
                "base_amount": _text(base_amount),
                "quote_amount": _text(quote_amount),
                "base_shortfall": _text(base_shortfall),
                "base_balance_required": _text(base_balance_required),
                "quote_balance_required": _text(quote_balance_required),
                "budget_used_quote": _text(budget_used),
                "budget_headroom_quote": _text(budget_headroom),
                "limiting_side": limiting_side,
                "lower_tick": lower_tick,
                "upper_tick": upper_tick,
                "aligned_lower_price": _text(aligned_lower),
                "aligned_upper_price": _text(aligned_upper),
                "reasons": reasons,
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
