"""Monitor price and alert on threshold."""

import asyncio
import logging
import time
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from servers import get_client
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Live price monitor with real-time updates"""

    connector: str = Field(default="binance", description="CEX connector name")
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair to monitor")
    threshold_pct: float = Field(default=1.0, description="Alert threshold in %")
    interval_sec: int = Field(default=5, description="Refresh interval in seconds")
    max_updates: int = Field(default=60, description="Max updates before stopping")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Monitor price with live dashboard updates."""
    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    client = await get_client(chat_id)

    if not client:
        return "No server available"

    # Get message info for live updates
    msg_id = context.user_data.get("routines_msg_id")

    if not msg_id or not chat_id:
        return "Could not get message reference for live updates"

    # Escape config values for MarkdownV2
    pair_escaped = escape_markdown_v2(config.trading_pair)
    connector_escaped = escape_markdown_v2(config.connector)

    # Get initial price
    try:
        prices = await client.market_data.get_prices(
            connector_name=config.connector,
            trading_pairs=config.trading_pair
        )
        initial_price = prices["prices"].get(config.trading_pair)
        if not initial_price:
            return f"Could not get price for {config.trading_pair}"
    except Exception as e:
        return f"Error getting initial price: {e}"

    last_price = initial_price
    alerts_sent = 0
    updates = 0
    start_time = time.time()
    high_price = initial_price
    low_price = initial_price

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = [
        [InlineKeyboardButton("⏹ Stop", callback_data="routines:stop:price_monitor")],
        [InlineKeyboardButton("« Back", callback_data="routines:menu")],
    ]

    # Monitor loop with live updates
    while updates < config.max_updates:
        try:
            prices = await client.market_data.get_prices(
                connector_name=config.connector,
                trading_pairs=config.trading_pair
            )
            current_price = prices["prices"].get(config.trading_pair)

            if current_price:
                # Track high/low
                high_price = max(high_price, current_price)
                low_price = min(low_price, current_price)

                # Calculate changes
                change_from_last = ((current_price - last_price) / last_price) * 100
                change_from_start = ((current_price - initial_price) / initial_price) * 100

                # Determine trend
                if change_from_last > 0.01:
                    trend = "📈"
                elif change_from_last < -0.01:
                    trend = "📉"
                else:
                    trend = "➡️"

                # Build live dashboard
                elapsed = int(time.time() - start_time)
                mins, secs = divmod(elapsed, 60)

                # Escape price values
                price_str = escape_markdown_v2(f"${current_price:,.2f}")
                high_str = escape_markdown_v2(f"${high_price:,.2f}")
                low_str = escape_markdown_v2(f"${low_price:,.2f}")
                change_start_str = escape_markdown_v2(f"{change_from_start:+.2f}%")

                dashboard = (
                    f"⚡ *PRICE MONITOR*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Status: 🟢 Live \\({mins}m {secs}s\\)\n\n"
                    f"┌─ {trend} Price ─────────────────\n"
                    f"│ `{price_str}`\n"
                    f"│ Change: `{change_start_str}`\n"
                    f"│ High: `{high_str}` Low: `{low_str}`\n"
                    f"└─────────────────────────\n\n"
                    f"┌─ Config ─────────────────\n"
                    f"```\n"
                    f"trading_pair={config.trading_pair}\n"
                    f"connector={config.connector}\n"
                    f"interval_sec={config.interval_sec}\n"
                    f"threshold_pct={config.threshold_pct}\n"
                    f"```\n"
                    f"└─ _Updates: {updates + 1}/{config.max_updates}_"
                )

                # Update message
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=dashboard,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    logger.info(f"Price monitor update #{updates + 1}: ${current_price:,.2f}")
                except Exception as e:
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
                        alerts_sent += 1
                    except Exception:
                        pass

                last_price = current_price
                updates += 1

        except asyncio.CancelledError:
            return f"Stopped after {updates} updates ({alerts_sent} alerts)"
        except Exception:
            pass  # Continue on errors

        await asyncio.sleep(config.interval_sec)

    return f"Completed {updates} updates ({alerts_sent} alerts) - Final: ${current_price:,.2f}"
