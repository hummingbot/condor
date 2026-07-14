"""Plan a CLMM LP position: range policy + live price -> lp_executor args.

The deterministic half of the LP Rebalancer agent. PURE planning — no gateway
I/O: the agent supplies the current pool price and its wallet balances (fetched
via hummingbot-api), and this routine returns the exact
manage_executors(lp_executor) argument block plus, when the deposit mix needs
it, the pre-swap. Range math lives in condor.agents.lp_ranges (ported from the
lp_rebalancer controller).
"""

import json
import logging
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Trading"

# Keep this much native SOL out of any plan — gas + position rents.
SOL_RESERVE = Decimal("0.05")


class Config(BaseModel):
    """Plan a CLMM LP position from a range policy and a supplied pool price."""

    connector: str = Field(default="raydium", description="CLMM dex: raydium | meteora | orca")
    pool_address: str = Field(description="Pool address")
    trading_pair: str = Field(default="SOL-USDC", description="BASE-QUOTE of the pool")
    chain_network: str = Field(default="solana-mainnet-beta")
    wallet_address: str = Field(default="", description="Wallet address (for the executor config)")
    current_price: float = Field(description="Live pool price (quote per base) from hummingbot-api")
    base_available: float = Field(default=0.0, description="Wallet base balance (from hummingbot-api)")
    quote_available: float = Field(default=0.0, description="Wallet quote balance (from hummingbot-api)")
    total_amount_quote: float = Field(default=1.0, description="Total deposit, quote units")
    side: str = Field(default="AUTO", description="AUTO | RANGE | BUY | SELL")
    position_width_pct: float = Field(default=4.0)
    position_offset_pct: float = Field(default=0.0)
    rebalance_threshold_pct: float = Field(default=1.0, description="Limit-price buffer beyond bounds, %")
    buy_price_min: Optional[float] = None
    buy_price_max: Optional[float] = None
    sell_price_min: Optional[float] = None
    sell_price_max: Optional[float] = None
    slippage_pct: float = Field(default=1.0)
    auto_swap: bool = Field(
        default=True,
        description="When the wallet lacks one side, plan a pre-swap from the "
        "other (True) or report BLOCKED and stand down (False)",
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    from condor.agents.lp_ranges import (
        RangePolicy,
        Side,
        determine_side_from_price,
        plan_position,
    )

    price = Decimal(str(config.current_price))
    if price <= 0:
        return json.dumps({"error": f"invalid current_price: {config.current_price}"})

    policy = RangePolicy(
        position_width_pct=Decimal(str(config.position_width_pct)),
        position_offset_pct=Decimal(str(config.position_offset_pct)),
        rebalance_threshold_pct=Decimal(str(config.rebalance_threshold_pct)),
        buy_price_min=Decimal(str(config.buy_price_min)) if config.buy_price_min else None,
        buy_price_max=Decimal(str(config.buy_price_max)) if config.buy_price_max else None,
        sell_price_min=Decimal(str(config.sell_price_min)) if config.sell_price_min else None,
        sell_price_max=Decimal(str(config.sell_price_max)) if config.sell_price_max else None,
    )

    # -- side --
    if config.side == "AUTO":
        has_limits = any([config.buy_price_min, config.buy_price_max,
                          config.sell_price_min, config.sell_price_max])
        side = determine_side_from_price(price, policy) if has_limits else Side.RANGE
    else:
        side = Side(config.side)

    plan = plan_position(side, price, Decimal(str(config.total_amount_quote)), policy)
    if plan is None:
        return json.dumps({
            "action": "STAND_DOWN",
            "reason": f"no valid position at price {price} within the configured limits",
            "pool_price": float(price),
        })

    # -- balance check (+ pre-swap when the deposit mix isn't there) --
    base_token, quote_token = config.trading_pair.split("-")
    base_avail = Decimal(str(config.base_available))
    quote_avail = Decimal(str(config.quote_available))
    if base_token == "SOL":
        base_avail = max(Decimal("0"), base_avail - SOL_RESERVE)
    if quote_token == "SOL":
        quote_avail = max(Decimal("0"), quote_avail - SOL_RESERVE)

    base_short = max(Decimal("0"), plan.base_amount - base_avail)
    quote_short = max(Decimal("0"), plan.quote_amount - quote_avail)

    # Dust shortfall (≤1% of the side): clamp the deposit to what the wallet
    # holds instead of swapping.
    clamped = []
    base_amount, quote_amount = plan.base_amount, plan.quote_amount
    if 0 < base_short <= plan.base_amount * Decimal("0.01"):
        base_amount = base_avail * Decimal("0.995")
        clamped.append(base_token)
        base_short = Decimal("0")
    if 0 < quote_short <= plan.quote_amount * Decimal("0.01"):
        quote_amount = quote_avail * Decimal("0.995")
        clamped.append(quote_token)
        quote_short = Decimal("0")

    # Pre-swaps buy a 2% buffer over the shortfall so slippage + fees can't
    # leave the deposit short again. Emitted as an order_executor MARKET order.
    SWAP_BUFFER = Decimal("1.02")
    pre_swap = None
    blocked = None
    if base_short > 0 and quote_short > 0:
        blocked = (
            f"insufficient funds: need {plan.base_amount:.6f} {base_token} + "
            f"{plan.quote_amount:.4f} {quote_token}, have {base_avail:.6f} + {quote_avail:.4f}"
        )
    elif (base_short > 0 or quote_short > 0) and not config.auto_swap:
        short_desc = (
            f"{base_short:.6f} {base_token}" if base_short > 0
            else f"{quote_short:.4f} {quote_token}"
        )
        blocked = f"short {short_desc} and auto_swap is disabled — not converting inventory"
    elif base_short > 0:
        buy_amount = base_short * SWAP_BUFFER
        if quote_avail - plan.quote_amount >= buy_amount * price:
            pre_swap = _order_executor_args(
                config, f"{base_token}-{quote_token}", "BUY", buy_amount)
        else:
            blocked = f"short {base_short:.6f} {base_token} and no excess {quote_token} to swap"
    elif quote_short > 0:
        need_base_sold = (quote_short / price) * SWAP_BUFFER
        if base_avail - plan.base_amount >= need_base_sold:
            pre_swap = _order_executor_args(
                config, f"{base_token}-{quote_token}", "SELL", need_base_sold)
        else:
            blocked = f"short {quote_short:.4f} {quote_token} and no excess {base_token} to swap"

    result = {
        "action": "BLOCKED" if blocked else "OPEN",
        "pool_price": float(price),
        "side": plan.side.value,
        "balances": {base_token: float(base_avail), quote_token: float(quote_avail)},
    }
    if blocked:
        result["reason"] = blocked
    if clamped:
        result["clamped_to_balance"] = clamped
    if pre_swap:
        result["pre_swap_create_args"] = pre_swap
        result["note"] = (
            "execute the pre-swap first, then RE-RUN this routine with fresh "
            "balances and use the new lp_create_args — do not reuse these amounts"
        )
    result["lp_create_args"] = {
        "executor_type": "lp_executor",
        "executor_config": {
            "type": "lp_executor",
            "connector_name": config.connector,
            "trading_pair": config.trading_pair,
            "pool_address": config.pool_address,
            "lower_price": f"{plan.lower_price:.10f}",
            "upper_price": f"{plan.upper_price:.10f}",
            "base_token_amount": f"{base_amount:.10f}",
            "quote_token_amount": f"{quote_amount:.10f}",
            "lower_limit_price": f"{plan.lower_limit_price:.10f}",
            "upper_limit_price": f"{plan.upper_limit_price:.10f}",
        },
    }
    return json.dumps(result, indent=2)


def _order_executor_args(config: Config, pair: str, side: str, amount: Decimal) -> dict:
    """A MARKET order_executor create block for a rebalance pre-swap."""
    return {
        "executor_type": "order_executor",
        "executor_config": {
            "type": "order_executor",
            "connector_name": config.chain_network,
            "trading_pair": pair,
            "side": side,
            "amount": str(amount),
            "execution_strategy": "MARKET",
        },
    }
