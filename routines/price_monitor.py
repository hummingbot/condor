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
    interval_sec: int = Field(default=5, description="Refresh interval in seconds")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Monitor price - single iteration.

    This routine is called repeatedly by JobQueue at interval_sec intervals.
    State is stored in context.user_data to persist between iterations.
    """
    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    client = await get_client(chat_id)

    if not client:
        return "No server available"

    # Get user_data and instance_id (use _user_data for job callbacks)
    user_data = getattr(context, '_user_data', None) or getattr(context, 'user_data', {})
    instance_id = getattr(context, '_instance_id', 'default')

    # Get message info for live updates
    msg_id = user_data.get("routines_msg_id")

    if not msg_id or not chat_id:
        return "Could not get message reference for live updates"

    # State key for this routine instance (includes instance_id for multi-instance support)
    state_key = f"price_monitor_state_{chat_id}_{instance_id}"

    # Initialize or get state
    state = user_data.get(state_key, {})

    # Escape config values for MarkdownV2
    pair_escaped = escape_markdown_v2(config.trading_pair)
    connector_escaped = escape_markdown_v2(config.connector)

    # Get current price
    try:
        prices = await client.market_data.get_prices(
            connector_name=config.connector,
            trading_pairs=config.trading_pair
        )
        current_price = prices["prices"].get(config.trading_pair)
        if not current_price:
            return f"Could not get price for {config.trading_pair}"
    except Exception as e:
        return f"Error getting price: {e}"

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

    # Update state
    state["high_price"] = max(state["high_price"], current_price)
    state["low_price"] = min(state["low_price"], current_price)

    # Calculate changes
    change_from_last = ((current_price - state["last_price"]) / state["last_price"]) * 100
    change_from_start = ((current_price - state["initial_price"]) / state["initial_price"]) * 100

    # Determine trend
    if change_from_last > 0.01:
        trend = "📈"
    elif change_from_last < -0.01:
        trend = "📉"
    else:
        trend = "➡️"

    # Build live dashboard
    elapsed = int(time.time() - state["start_time"])
    mins, secs = divmod(elapsed, 60)

    # Escape price values
    price_str = escape_markdown_v2(f"${current_price:,.2f}")
    high_str = escape_markdown_v2(f"${state['high_price']:,.2f}")
    low_str = escape_markdown_v2(f"${state['low_price']:,.2f}")
    change_start_str = escape_markdown_v2(f"{change_from_start:+.2f}%")

    dashboard = (
        f"⚡ *PRICE MONITOR*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 _Continuous • Running {mins}m {secs}s_\n\n"
        f"┌─ {trend} {pair_escaped} ─────────────\n"
        f"│ `{price_str}`\n"
        f"│ Change: `{change_start_str}`\n"
        f"│ High: `{high_str}` Low: `{low_str}`\n"
        f"└─────────────────────────\n\n"
        f"┌─ Config ─────────────────\n"
        f"```\n"
        f"connector={config.connector}\n"
        f"interval={config.interval_sec}s\n"
        f"alert_threshold={config.threshold_pct}%\n"
        f"```\n"
        f"└─ _Alerts sent: {state['alerts_sent']}_"
    )

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = [
        [InlineKeyboardButton("⏹ Stop", callback_data="routines:stop:price_monitor")],
        [InlineKeyboardButton("« Back", callback_data="routines:menu")],
    ]

    # Update message
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=dashboard,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.debug(f"Price monitor update #{state['updates'] + 1}: ${current_price:,.2f}")
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.error(f"Price monitor edit failed: {e}")

    # Check threshold for alert
    if abs(change_from_last) >= config.threshold_pct:
        direction = "📈" if change_from_last > 0 else "📉"
        change_str = escape_markdown_v2(f"{change_from_last:+.2f}%")
        alert_msg = (
            f"{direction} *{pair_escaped} Alert*\n"
            f"Price: `{price_str}`\n"
            f"Change: `{change_str}`"
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=alert_msg,
                parse_mode="MarkdownV2"
            )
            state["alerts_sent"] += 1
        except Exception:
            pass

    # Update state for next iteration
    state["last_price"] = current_price
    state["updates"] += 1
    user_data[state_key] = state

    return f"${current_price:,.2f} ({change_from_start:+.2f}%)"
