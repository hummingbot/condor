"""Pure, deterministic Orca CLMM range and inventory planner."""

import hashlib
import json
import math
from decimal import ROUND_DOWN, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.routines import _routine_report as reports
from agents.lp_expert.routines._orca_pool_metrics import USDC_MINT

CATEGORY = "Orca LP Planning"
NETWORK = "solana-mainnet-beta"
LP_PROVIDER = "orca/clmm"
SWAP_PROVIDER = "jupiter/router"


def _decimal(value: Any, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{label} must be a decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{label} must be finite{' and positive' if positive else ''}")
    return result


def _floor(value: Decimal, decimals: int) -> Decimal:
    quantum = Decimal(1).scaleb(-decimals)
    return value.quantize(quantum, rounding=ROUND_DOWN)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def plan_digest(plan: dict[str, Any]) -> str:
    encoded = json.dumps(
        _json_value(plan), separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class Config(BaseModel):
    """Calculate one bounded double-sided Orca LP executor plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pool_address: StrictStr
    base_symbol: StrictStr
    base_mint: StrictStr
    base_decimals: StrictInt = Field(ge=0, le=18)
    quote_symbol: StrictStr = "USDC"
    quote_mint: StrictStr = USDC_MINT
    quote_decimals: StrictInt = 6
    current_price: Decimal
    tick_spacing: StrictInt = Field(gt=0)
    amount_quote: Decimal
    range_half_width_pct: Decimal
    minimum_range_half_width_pct: Decimal
    maximum_range_half_width_pct: Decimal
    rebalance_threshold_pct: Decimal
    max_slippage_pct: Decimal
    attributed_base_amount: Decimal | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> "Config":
        for field in (
            "pool_address",
            "base_symbol",
            "base_mint",
            "quote_symbol",
            "quote_mint",
        ):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} must not be empty")
        price = _decimal(self.current_price, "current_price", positive=True)
        _decimal(self.amount_quote, "amount_quote", positive=True)
        width = _decimal(
            self.range_half_width_pct, "range_half_width_pct", positive=True
        )
        minimum = _decimal(
            self.minimum_range_half_width_pct,
            "minimum_range_half_width_pct",
            positive=True,
        )
        maximum = _decimal(
            self.maximum_range_half_width_pct,
            "maximum_range_half_width_pct",
            positive=True,
        )
        rebalance_threshold = _decimal(
            self.rebalance_threshold_pct, "rebalance_threshold_pct", positive=True
        )
        slippage = _decimal(self.max_slippage_pct, "max_slippage_pct", positive=True)
        if (
            self.quote_symbol.upper() != "USDC"
            or self.quote_mint != USDC_MINT
            or self.quote_decimals != 6
            or self.base_mint == USDC_MINT
            or self.base_symbol.upper() == "USDC"
        ):
            raise ValueError("tokens must be non-USDC base and canonical USDC quote")
        if minimum > maximum or maximum >= Decimal("100"):
            raise ValueError(
                "range safety bounds must be ordered and below 100 percent"
            )
        if not minimum <= width <= maximum:
            raise ValueError("range half-width is outside the configured bounds")
        if rebalance_threshold >= Decimal("100"):
            raise ValueError("rebalance_threshold_pct must be below 100")
        if slippage > Decimal("100"):
            raise ValueError("max_slippage_pct must not exceed 100")
        if self.attributed_base_amount is not None:
            _decimal(self.attributed_base_amount, "attributed_base_amount")
            if self.attributed_base_amount < 0:
                raise ValueError("attributed_base_amount must be non-negative")
        if price <= 0:
            raise ValueError("current_price must be positive")
        return self


def _price_from_tick(tick: int, decimal_factor: float) -> Decimal:
    return Decimal(str(math.exp(tick * math.log(1.0001)) * decimal_factor))


def _build(config: Config) -> dict[str, Any]:
    price = _decimal(config.current_price, "current_price", positive=True)
    width = _decimal(config.range_half_width_pct, "range_half_width_pct") / 100
    decimal_factor = 10.0 ** (config.base_decimals - config.quote_decimals)
    lower_target = float(price * (1 - width))
    upper_target = float(price * (1 + width))
    if lower_target <= 0:
        raise ValueError("calculated lower price must be positive")
    log_step = math.log(1.0001)
    current_tick = math.log(float(price) / decimal_factor) / log_step
    center_twice = (
        math.ceil(2 * current_tick / config.tick_spacing) * config.tick_spacing
    )
    lower_inside = (
        math.ceil(
            math.log(lower_target / decimal_factor) / log_step / config.tick_spacing
        )
        * config.tick_spacing
    )
    upper_inside = (
        math.floor(
            math.log(upper_target / decimal_factor) / log_step / config.tick_spacing
        )
        * config.tick_spacing
    )
    minimum_width = float(config.minimum_range_half_width_pct / 100)
    minimum_lower_tick = (
        math.floor(
            math.log(float(price) * (1 - minimum_width) / decimal_factor)
            / log_step
            / config.tick_spacing
        )
        * config.tick_spacing
    )
    minimum_upper_tick = (
        math.ceil(
            math.log(float(price) * (1 + minimum_width) / decimal_factor)
            / log_step
            / config.tick_spacing
        )
        * config.tick_spacing
    )
    desired_lower_tick = max(
        lower_inside,
        center_twice - upper_inside,
    )
    minimum_required_lower_tick = min(
        minimum_lower_tick,
        center_twice - minimum_upper_tick,
    )
    lower_tick = min(desired_lower_tick, minimum_required_lower_tick)
    upper_tick = center_twice - lower_tick
    if lower_tick >= upper_tick:
        raise ValueError("tick-aligned range is empty")
    lower = _price_from_tick(lower_tick, decimal_factor)
    upper = _price_from_tick(upper_tick, decimal_factor)
    if not lower < price < upper:
        raise ValueError("current price is not inside the aligned range")
    actual_half_widths = (
        (price - lower) / price * 100,
        (upper - price) / price * 100,
    )
    if (
        min(actual_half_widths) < config.minimum_range_half_width_pct
        or max(actual_half_widths) > config.maximum_range_half_width_pct
    ):
        raise ValueError(
            "tick-aligned range is outside the configured half-width bounds"
        )
    sqrt_price, sqrt_lower, sqrt_upper = map(
        lambda value: Decimal(str(math.sqrt(float(value)))),
        (price, lower, upper),
    )
    valuation_price = (lower * upper).sqrt()
    base_per_liquidity = (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper)
    quote_per_liquidity = sqrt_price - sqrt_lower
    value_per_liquidity = base_per_liquidity * valuation_price + quote_per_liquidity
    attributed = (
        _decimal(config.attributed_base_amount, "attributed_base_amount")
        if config.attributed_base_amount is not None
        else None
    )
    slippage_ratio = config.max_slippage_pct / 100
    if attributed is None:
        commitment_per_liquidity = (
            base_per_liquidity * price * (1 + slippage_ratio) + quote_per_liquidity
        )
        liquidity = config.amount_quote / commitment_per_liquidity
    else:
        liquidity = min(
            config.amount_quote / value_per_liquidity,
            attributed / base_per_liquidity,
        )
    base_amount = _floor(liquidity * base_per_liquidity, config.base_decimals)
    quote_amount = _floor(liquidity * quote_per_liquidity, config.quote_decimals)
    if base_amount <= 0 or quote_amount <= 0:
        raise ValueError("allocation is too small for a double-sided position")
    attributed = attributed or Decimal(0)
    base_shortfall = max(Decimal(0), base_amount - attributed)
    swap_quote_estimate = _floor(base_shortfall * price, config.quote_decimals)
    swap_quote_cap = (
        _floor(config.amount_quote - quote_amount, config.quote_decimals)
        if base_shortfall > 0
        else Decimal(0)
    )
    swap_quote_headroom = swap_quote_cap - swap_quote_estimate
    if swap_quote_headroom < 0:
        raise ValueError("planned preparation swap exceeds the allocation")
    declared_exposure = base_amount * valuation_price + quote_amount
    rebalance_threshold = config.rebalance_threshold_pct / 100
    plan = {
        "identity": {
            "pool_address": config.pool_address.strip(),
            "trading_pair": f"{config.base_symbol.strip()}-USDC",
            "base_symbol": config.base_symbol.strip(),
            "base_mint": config.base_mint.strip(),
            "base_decimals": config.base_decimals,
            "quote_symbol": "USDC",
            "quote_mint": USDC_MINT,
            "quote_decimals": 6,
            "price_orientation": "token_b_per_token_a",
        },
        "inputs": {
            "current_price": price,
            "amount_quote": config.amount_quote,
            "range_half_width_pct": config.range_half_width_pct,
            "actual_lower_half_width_pct": actual_half_widths[0],
            "actual_upper_half_width_pct": actual_half_widths[1],
            "rebalance_threshold_pct": config.rebalance_threshold_pct,
            "max_slippage_pct": config.max_slippage_pct,
            "attributed_base_amount": attributed,
        },
        "range": {
            "lower_tick": lower_tick,
            "upper_tick": upper_tick,
            "lower_price": lower,
            "upper_price": upper,
            "lower_limit_price": lower * (1 - rebalance_threshold),
            "upper_limit_price": upper * (1 + rebalance_threshold),
        },
        "inventory": {
            "base_amount": base_amount,
            "quote_amount": quote_amount,
            "base_shortfall": base_shortfall,
            "estimated_usdc_for_preparation_swap": swap_quote_estimate,
            "estimated_total_usdc": swap_quote_estimate + quote_amount,
            "max_usdc_for_preparation_swap": swap_quote_cap,
            "preparation_slippage_headroom_quote": swap_quote_headroom,
            "maximum_total_usdc": swap_quote_cap + quote_amount,
            "schema_native_valuation_price": valuation_price,
            "schema_native_exposure_quote": declared_exposure,
            "capital_dust_quote": config.amount_quote - declared_exposure,
            "inventory_ready": attributed >= base_amount,
            "attribution_rule": "only exact preparation-swap output counts as base inventory",
        },
        "executor_config": {
            "type": "lp_executor",
            "connector_name": NETWORK,
            "lp_provider": LP_PROVIDER,
            "trading_pair": f"{config.base_symbol.strip()}-USDC",
            "pool_address": config.pool_address.strip(),
            "lower_price": lower,
            "upper_price": upper,
            "lower_limit_price": lower * (1 - rebalance_threshold),
            "upper_limit_price": upper * (1 + rebalance_threshold),
            "side": 3,
            "base_amount": base_amount,
            "quote_amount": quote_amount,
            "swap_provider": SWAP_PROVIDER,
            "keep_position": False,
        },
    }
    payload = {"status": "planned", **_json_value(plan)}
    payload["plan_digest"] = plan_digest(plan)
    return payload


async def run(config: Config, context: Any) -> str:
    try:
        payload = _build(config)
        status = "planned"
        evidence = [
            {
                "kind": "range",
                **payload["range"],
            },
            {
                "kind": "inventory",
                "base_amount": payload["inventory"]["base_amount"],
                "quote_amount": payload["inventory"]["quote_amount"],
                "base_shortfall": payload["inventory"]["base_shortfall"],
                "estimated_usdc_for_preparation_swap": payload["inventory"][
                    "estimated_usdc_for_preparation_swap"
                ],
                "max_usdc_for_preparation_swap": payload["inventory"][
                    "max_usdc_for_preparation_swap"
                ],
                "preparation_slippage_headroom_quote": payload["inventory"][
                    "preparation_slippage_headroom_quote"
                ],
                "maximum_total_usdc": payload["inventory"]["maximum_total_usdc"],
                "schema_native_exposure_quote": payload["inventory"][
                    "schema_native_exposure_quote"
                ],
                "capital_dust_quote": payload["inventory"]["capital_dust_quote"],
                "inventory_ready": payload["inventory"]["inventory_ready"],
            },
        ]
    except Exception as exc:
        status = "rejected"
        payload = {
            "status": status,
            "reason": f"{type(exc).__name__}: {exc}",
            "mutation": False,
        }
        evidence = [{"kind": "error", "reason": payload["reason"]}]
    payload = await reports.attach_trace(
        payload,
        title="Orca LP Position Plan",
        source="clmm_position_plan",
        status=status,
        summary={
            "pool_address": config.pool_address,
            "trading_pair": f"{config.base_symbol}-USDC",
            "amount_quote": config.amount_quote,
            "current_price": config.current_price,
            "range_half_width_pct": config.range_half_width_pct,
            "rebalance_threshold_pct": config.rebalance_threshold_pct,
        },
        evidence=evidence,
    )
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
