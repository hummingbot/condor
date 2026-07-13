"""Plan an LP position: live pool price + range policy -> exact executor args.

The deterministic half of the LP Rebalancer agent (docs/condor-simple.md M2):
wraps condor.executors.ranges (the math ported from the lp_rebalancer
controller) and the gateway pool feed, so the agent never hand-computes
bounds. Output is the verbatim argument block for manage_executors(create),
plus a balance check and, when the deposit mix needs it, the pre-swap.
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
    """Plan a CLMM LP position from a range policy and the live pool price."""

    connector: str = Field(default="raydium", description="CLMM dex: raydium | meteora | orca")
    pool_address: str = Field(description="Pool address")
    trading_pair: str = Field(default="SOL-USDC", description="BASE-QUOTE of the pool")
    chain_network: str = Field(default="solana-mainnet-beta")
    wallet_address: str = Field(default="", description="Wallet; empty = first gateway solana wallet")
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
    from condor.executors.gateway import GatewayClient
    from condor.executors.ranges import (
        RangePolicy,
        Side,
        determine_side_from_price,
        plan_position,
    )

    gateway = GatewayClient()
    try:
        # -- wallet + live price --
        wallet = config.wallet_address
        if not wallet:
            chains = await gateway.get_wallets()
            solana = next((c for c in chains if c.get("chain") == "solana"), None)
            addresses = (solana or {}).get("walletAddresses") or []
            if not addresses:
                return json.dumps({"error": "no solana wallet configured in gateway"})
            wallet = addresses[0]

        pool = await gateway.clmm_pool_info(
            config.connector, config.chain_network, config.pool_address
        )
        price = Decimal(str(pool["price"]))

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
        chain = config.chain_network.split("-", 1)[0]
        network = config.chain_network.split("-", 1)[1]
        balances = await gateway.get_balances(
            chain, network, wallet, tokens=[base_token, quote_token]
        )
        base_avail = Decimal(str(balances.get(base_token, 0)))
        quote_avail = Decimal(str(balances.get(quote_token, 0)))
        if base_token == "SOL":
            base_avail = max(Decimal("0"), base_avail - SOL_RESERVE)
        if quote_token == "SOL":
            quote_avail = max(Decimal("0"), quote_avail - SOL_RESERVE)

        base_short = max(Decimal("0"), plan.base_amount - base_avail)
        quote_short = max(Decimal("0"), plan.quote_amount - quote_avail)

        # Dust shortfall (≤1% of the side): clamp the deposit to what the
        # wallet holds instead of swapping — this is what killed the exact-
        # sizing plan (pre-swap slippage left the wallet 0.4c short and
        # gateway rejected with INSUFFICIENT_BALANCE).
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

        # Pre-swaps buy a 2% buffer over the shortfall so slippage + fees
        # can't leave the deposit short again.
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
            # Buy the missing base with excess quote (controller autoswap)
            buy_amount = base_short * SWAP_BUFFER
            if quote_avail - plan.quote_amount >= buy_amount * price:
                pre_swap = {"executor_type": "swap", "config": {
                    "chain_network": config.chain_network, "wallet_address": wallet,
                    "base_token": base_token, "quote_token": quote_token,
                    "amount": str(buy_amount), "side": "BUY",
                    "slippage_pct": str(config.slippage_pct),
                }}
            else:
                blocked = f"short {base_short:.6f} {base_token} and no excess {quote_token} to swap"
        elif quote_short > 0:
            need_base_sold = (quote_short / price) * SWAP_BUFFER
            if base_avail - plan.base_amount >= need_base_sold:
                pre_swap = {"executor_type": "swap", "config": {
                    "chain_network": config.chain_network, "wallet_address": wallet,
                    "base_token": base_token, "quote_token": quote_token,
                    "amount": str(need_base_sold), "side": "SELL",
                    "slippage_pct": str(config.slippage_pct),
                }}
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
                "execute the pre-swap first, then RE-RUN this routine and use "
                "the fresh lp_create_args — do not reuse these amounts"
            )
        result["lp_create_args"] = {
            "executor_type": "lp",
            "config": {
                "chain_network": config.chain_network,
                "wallet_address": wallet,
                "connector": config.connector,
                "pool_address": config.pool_address,
                "trading_pair": config.trading_pair,
                "lower_price": f"{plan.lower_price:.10f}",
                "upper_price": f"{plan.upper_price:.10f}",
                "base_amount": f"{base_amount:.10f}",
                "quote_amount": f"{quote_amount:.10f}",
                "lower_limit_price": f"{plan.lower_limit_price:.10f}",
                "upper_limit_price": f"{plan.upper_limit_price:.10f}",
                "slippage_pct": str(config.slippage_pct),
                "keep_position": True,
            },
        }
        return json.dumps(result, indent=2)
    finally:
        await gateway.close()
