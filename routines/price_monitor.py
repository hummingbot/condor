"""Monitor price and alert on threshold."""

import asyncio
import logging
import time
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from servers import get_client
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

# Mark as continuous routine - has internal loop
CONTINUOUS = True


class Config(BaseModel):
    """Live price monitor with configurable alerts."""

    connector: str = Field(default="binance", description="CEX connector name")
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair to monitor")
    threshold_pct: float = Field(default=1.0, description="Alert threshold in %")
    interval_sec: int = Field(default=10, description="Check interval in seconds")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Monitor price continuously.

    This is a continuous routine - runs forever until cancelled.
    Sends alert messages when threshold is crossed.
    """
    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    instance_id = getattr(context, '_instance_id', 'default')

    client = await get_client(chat_id)
    if not client:
        return "No server available"

    # State for tracking
    state = {
        "initial_price": None,
        "last_price": None,
        "high_price": None,
        "low_price": None,
        "alerts_sent": 0,
        "updates": 0,
        "start_time": time.time(),
    }

    # Send start notification
    try:
        pair_esc = escape_markdown_v2(config.trading_pair)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🟢 *Price Monitor Started*\n{pair_esc} @ {escape_markdown_v2(config.connector)}",
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Failed to send start message: {e}")

    try:
        # Main monitoring loop
        while True:
            try:
                # Get current price
                prices = await client.market_data.get_prices(
                    connector_name=config.connector,
                    trading_pairs=config.trading_pair
                )
                current_price = prices["prices"].get(config.trading_pair)

                if not current_price:
                    await asyncio.sleep(config.interval_sec)
                    continue

                # Initialize on first price
                if state["initial_price"] is None:
                    state["initial_price"] = current_price
                    state["last_price"] = current_price
                    state["high_price"] = current_price
                    state["low_price"] = current_price

                # Update tracking
                state["high_price"] = max(state["high_price"], current_price)
                state["low_price"] = min(state["low_price"], current_price)
                state["updates"] += 1

                # Calculate changes
                change_from_last = ((current_price - state["last_price"]) / state["last_price"]) * 100

                # Check threshold for alert
                if abs(change_from_last) >= config.threshold_pct:
                    direction = "📈" if change_from_last > 0 else "📉"
                    pair_esc = escape_markdown_v2(config.trading_pair)
                    price_esc = escape_markdown_v2(f"${current_price:,.2f}")
                    change_esc = escape_markdown_v2(f"{change_from_last:+.2f}%")

                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"{direction} *{pair_esc} Alert*\n"
                                f"Price: `{price_esc}`\n"
                                f"Change: `{change_esc}`"
                            ),
                            parse_mode="MarkdownV2"
                        )
                        state["alerts_sent"] += 1
                    except Exception:
                        pass

                # Update last price
                state["last_price"] = current_price

            except asyncio.CancelledError:
                raise  # Re-raise to exit the loop
            except Exception as e:
                logger.error(f"Price monitor error: {e}")

            # Wait for next check
            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        # Send stop notification
        elapsed = int(time.time() - state["start_time"])
        mins, secs = divmod(elapsed, 60)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔴 *Price Monitor Stopped*\n"
                    f"{escape_markdown_v2(config.trading_pair)}\n"
                    f"Duration: {mins}m {secs}s \\| Updates: {state['updates']} \\| Alerts: {state['alerts_sent']}"
                ),
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass

        return f"Stopped after {mins}m {secs}s, {state['updates']} updates, {state['alerts_sent']} alerts"
