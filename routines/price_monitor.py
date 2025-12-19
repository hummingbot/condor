"""Monitor price and alert on threshold."""

import logging
import time
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from servers import get_client
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Live price monitor with configurable alerts."""

    connector: str = Field(default="binance", description="CEX connector name")
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair to monitor")
    threshold_pct: float = Field(default=1.0, description="Alert threshold in %")
    interval_sec: int = Field(default=10, description="Refresh interval in seconds")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Monitor price - single iteration.

    Runs silently in background. Sends alert messages when threshold is crossed.
    Returns status string for the routine handler to display.
    """
    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    client = await get_client(chat_id)

    if not client:
        return "No server available"

    # Get user_data and instance_id
    user_data = getattr(context, '_user_data', None) or getattr(context, 'user_data', {})
    instance_id = getattr(context, '_instance_id', 'default')

    # State key for this routine instance
    state_key = f"price_monitor_state_{chat_id}_{instance_id}"

    # Get or initialize state
    state = user_data.get(state_key, {})

    # Get current price
    try:
        prices = await client.market_data.get_prices(
            connector_name=config.connector,
            trading_pairs=config.trading_pair
        )
        current_price = prices["prices"].get(config.trading_pair)
        if not current_price:
            return f"No price for {config.trading_pair}"
    except Exception as e:
        return f"Error: {e}"

    # Initialize state on first run
    if not state:
        state = {
            "initial_price": current_price,
            "last_price": current_price,
            "high_price": current_price,
            "low_price": current_price,
            "alerts_sent": 0,
            "updates": 0,
            "start_time": time.time(),
        }
        user_data[state_key] = state

        # Send start notification
        try:
            pair_esc = escape_markdown_v2(config.trading_pair)
            price_esc = escape_markdown_v2(f"${current_price:,.2f}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🟢 *Price Monitor Started*\n{pair_esc}: `{price_esc}`",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass

    # Update tracking
    state["high_price"] = max(state["high_price"], current_price)
    state["low_price"] = min(state["low_price"], current_price)

    # Calculate changes
    change_from_last = ((current_price - state["last_price"]) / state["last_price"]) * 100
    change_from_start = ((current_price - state["initial_price"]) / state["initial_price"]) * 100

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

    # Update state
    state["last_price"] = current_price
    state["updates"] += 1
    user_data[state_key] = state

    # Build status string for handler display
    elapsed = int(time.time() - state["start_time"])
    mins, secs = divmod(elapsed, 60)

    trend = "📈" if change_from_start > 0.01 else "📉" if change_from_start < -0.01 else "➡️"

    return (
        f"{trend} ${current_price:,.2f} ({change_from_start:+.2f}%)\n"
        f"High: ${state['high_price']:,.2f} | Low: ${state['low_price']:,.2f}\n"
        f"Updates: {state['updates']} | Alerts: {state['alerts_sent']} | {mins}m {secs}s"
    )
