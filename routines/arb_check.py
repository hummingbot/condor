"""Check for CEX/DEX arbitrage opportunities."""

from decimal import Decimal
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from servers import get_client


class Config(BaseModel):
    """Check CEX vs DEX price arbitrage opportunities."""

    trading_pair: str = Field(default="SOL-USDC", description="Trading pair (e.g. SOL-USDC)")
    amount: float = Field(default=1.0, description="Amount to quote")
    cex_connector: str = Field(default="binance", description="CEX connector")
    dex_connector: str = Field(default="jupiter", description="DEX connector")
    dex_network: str = Field(default="solana-mainnet-beta", description="DEX network")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Check arbitrage between CEX and DEX."""
    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    client = await get_client(chat_id)

    if not client:
        return "No server available. Configure servers in /config."

    results = []
    cex_buy, cex_sell = None, None
    dex_buy, dex_sell = None, None

    # --- CEX Quotes ---
    try:
        # Get CEX price for volume (buy and sell)
        async def get_cex_quote(is_buy: bool):
            try:
                result = await client.market_data.get_price_for_volume(
                    connector_name=config.cex_connector,
                    trading_pair=config.trading_pair,
                    volume=config.amount,
                    is_buy=is_buy
                )
                if isinstance(result, dict):
                    return (
                        result.get("result_price") or
                        result.get("price") or
                        result.get("average_price")
                    )
                return None
            except Exception as e:
                return None

        import asyncio
        cex_buy, cex_sell = await asyncio.gather(
            get_cex_quote(True),
            get_cex_quote(False)
        )

        if cex_buy:
            results.append(f"CEX BUY:  {float(cex_buy):.6f}")
        if cex_sell:
            results.append(f"CEX SELL: {float(cex_sell):.6f}")

        if not cex_buy and not cex_sell:
            results.append(f"CEX: No quotes from {config.cex_connector}")

    except Exception as e:
        results.append(f"CEX Error: {str(e)}")

    # --- DEX Quotes ---
    try:
        if hasattr(client, 'gateway_swap'):
            async def get_dex_quote(side: str):
                try:
                    result = await client.gateway_swap.get_swap_quote(
                        connector=config.dex_connector,
                        network=config.dex_network,
                        trading_pair=config.trading_pair,
                        side=side,
                        amount=Decimal(str(config.amount)),
                        slippage_pct=Decimal("1.0")
                    )
                    if isinstance(result, dict):
                        return result.get("price")
                    return None
                except Exception:
                    return None

            dex_buy, dex_sell = await asyncio.gather(
                get_dex_quote("BUY"),
                get_dex_quote("SELL")
            )

            if dex_buy:
                results.append(f"DEX BUY:  {float(dex_buy):.6f}")
            if dex_sell:
                results.append(f"DEX SELL: {float(dex_sell):.6f}")

            if not dex_buy and not dex_sell:
                results.append(f"DEX: No quotes from {config.dex_connector}")
        else:
            results.append("DEX: Gateway not available")

    except Exception as e:
        results.append(f"DEX Error: {str(e)}")

    # --- Arbitrage Analysis ---
    results.append("")
    results.append("--- Arbitrage ---")

    opportunities = []

    # Strategy 1: Buy CEX, Sell DEX
    if cex_buy and dex_sell:
        cex_buy_f = float(cex_buy)
        dex_sell_f = float(dex_sell)
        # For a BUY on CEX: price is what we pay per unit
        # For a SELL on DEX: price is what we receive per unit
        spread_pct = ((dex_sell_f - cex_buy_f) / cex_buy_f) * 100
        profit = (dex_sell_f - cex_buy_f) * config.amount
        if spread_pct > 0:
            opportunities.append(f"BUY CEX -> SELL DEX: +{spread_pct:.2f}% (${profit:.2f})")
        else:
            results.append(f"BUY CEX -> SELL DEX: {spread_pct:.2f}%")

    # Strategy 2: Buy DEX, Sell CEX
    if dex_buy and cex_sell:
        dex_buy_f = float(dex_buy)
        cex_sell_f = float(cex_sell)
        spread_pct = ((cex_sell_f - dex_buy_f) / dex_buy_f) * 100
        profit = (cex_sell_f - dex_buy_f) * config.amount
        if spread_pct > 0:
            opportunities.append(f"BUY DEX -> SELL CEX: +{spread_pct:.2f}% (${profit:.2f})")
        else:
            results.append(f"BUY DEX -> SELL CEX: {spread_pct:.2f}%")

    if opportunities:
        results.append("")
        results.append("OPPORTUNITIES FOUND:")
        results.extend(opportunities)
    else:
        results.append("No profitable arbitrage found.")

    return "\n".join(results)
