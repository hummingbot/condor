"""
Portfolio command handler using hummingbot_api_client
"""

import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from utils.auth import restricted
from utils.telegram_formatters import (
    format_portfolio_summary,
    format_portfolio_state,
    format_portfolio_overview,
    format_error_message,
    escape_markdown_v2
)
from handlers.config import clear_config_state
from handlers.config.user_preferences import (
    get_portfolio_prefs,
    set_portfolio_days,
    set_portfolio_interval,
    PORTFOLIO_DAYS_OPTIONS,
    PORTFOLIO_INTERVAL_OPTIONS,
)
from utils.portfolio_graphs import generate_portfolio_dashboard
from utils.trading_data import get_portfolio_overview

logger = logging.getLogger(__name__)


def _calculate_start_time(days: int) -> int:
    """Calculate start_time as now - days in Unix timestamp"""
    return int(time.time()) - (days * 24 * 60 * 60)


async def _fetch_dashboard_data(client, days: int, interval: str):
    """
    Fetch all data needed for the portfolio dashboard.

    Returns:
        Tuple of (overview_data, history, token_distribution, accounts_distribution)
    """
    import asyncio

    # Calculate start_time based on days
    start_time = _calculate_start_time(days)
    logger.info(f"Fetching portfolio data: days={days}, interval={interval}, start_time={start_time}")

    # Fetch all data in parallel
    overview_task = get_portfolio_overview(
        client,
        account_names=None,
        include_balances=True,
        include_perp_positions=True,
        include_lp_positions=True,
        include_active_orders=True
    )

    history_task = client.portfolio.get_history(
        start_time=start_time,
        limit=100,
        interval=interval
    )

    token_dist_task = client.portfolio.get_distribution()
    accounts_dist_task = client.portfolio.get_accounts_distribution()

    results = await asyncio.gather(
        overview_task,
        history_task,
        token_dist_task,
        accounts_dist_task,
        return_exceptions=True
    )

    # Handle any exceptions
    overview_data = results[0] if not isinstance(results[0], Exception) else None
    history = results[1] if not isinstance(results[1], Exception) else None
    token_distribution = results[2] if not isinstance(results[2], Exception) else None
    accounts_distribution = results[3] if not isinstance(results[3], Exception) else None

    # Log what the API returned for history
    if history and not isinstance(history, Exception):
        pagination = history.get("pagination", {})
        data_count = len(history.get("data", []))
        logger.info(f"History API response: {data_count} data points, pagination={pagination}")

    if isinstance(results[0], Exception):
        logger.error(f"Error fetching overview: {results[0]}")
    if isinstance(results[1], Exception):
        logger.error(f"Error fetching history: {results[1]}")
    if isinstance(results[2], Exception):
        logger.error(f"Error fetching token distribution: {results[2]}")
    if isinstance(results[3], Exception):
        logger.error(f"Error fetching accounts distribution: {results[3]}")

    return overview_data, history, token_distribution, accounts_distribution


@restricted
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /portfolio command - Display comprehensive portfolio dashboard

    Usage:
        /portfolio - Show portfolio dashboard with all graphs and information
    """
    # Clear any config state to prevent interference
    clear_config_state(context)

    # Send "typing" status
    await update.message.reply_chat_action("typing")

    try:
        from servers import server_manager

        # Get first enabled server
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            error_message = format_error_message("No enabled API servers. Edit servers.yml to enable a server.")
            await update.message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        # Get active server from context or use first enabled
        if not context.user_data.get("active_portfolio_server"):
            context.user_data["active_portfolio_server"] = enabled_servers[0]

        server_name = context.user_data["active_portfolio_server"]

        # Validate server is still enabled
        if server_name not in enabled_servers:
            server_name = enabled_servers[0]
            context.user_data["active_portfolio_server"] = server_name

        client = await server_manager.get_client(server_name)

        # Check server status
        server_status_info = await server_manager.check_server_status(server_name)
        server_status = server_status_info.get("status", "online")

        # Get portfolio config
        config = get_portfolio_prefs(context.user_data)
        days = config.get("days", 3)
        interval = config.get("interval", "1h")

        # Fetch all data in parallel
        overview_data, history, token_distribution, accounts_distribution = await _fetch_dashboard_data(
            client, days, interval
        )

        # Format the complete overview
        message = format_portfolio_overview(
            overview_data,
            server_name=server_name,
            server_status=server_status
        )

        # Send text overview first
        text_msg = await update.message.reply_text(message, parse_mode="MarkdownV2")

        # Send "upload_photo" status
        await update.message.reply_chat_action("upload_photo")

        # Generate the comprehensive dashboard
        dashboard_bytes = generate_portfolio_dashboard(
            history_data=history,
            token_distribution_data=token_distribution,
            accounts_distribution_data=accounts_distribution
        )

        # Create settings button
        keyboard = [[
            InlineKeyboardButton(f"⚙️ Settings ({days}d / {interval})", callback_data="portfolio:settings")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send the dashboard image with settings button
        photo_msg = await update.message.reply_photo(
            photo=dashboard_bytes,
            caption=f"📊 Portfolio Dashboard - {server_name}",
            reply_markup=reply_markup
        )

        # Store message IDs for later updates
        context.user_data["portfolio_text_message_id"] = text_msg.message_id
        context.user_data["portfolio_photo_message_id"] = photo_msg.message_id
        context.user_data["portfolio_chat_id"] = update.message.chat_id

    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch portfolio: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# PORTFOLIO SETTINGS CALLBACK HANDLERS
# ============================================

@restricted
async def portfolio_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks for portfolio operations"""
    query = update.callback_query
    await query.answer()

    logger.info(f"Portfolio callback received: {query.data}")

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        logger.info(f"Portfolio action: {action}")

        if action == "settings":
            await show_portfolio_settings(update, context)
        elif action.startswith("set_days:"):
            days = int(action.split(":")[1])
            set_portfolio_days(context.user_data, days)
            await show_portfolio_settings(update, context, message=f"Days set to {days}")
        elif action.startswith("set_interval:"):
            interval = action.split(":")[1]
            set_portfolio_interval(context.user_data, interval)
            await show_portfolio_settings(update, context, message=f"Interval set to {interval}")
        elif action == "close":
            # Close settings menu and refresh dashboard with new settings
            try:
                await query.message.delete()
            except Exception:
                pass
            await refresh_portfolio_dashboard(update, context)
        else:
            logger.warning(f"Unknown portfolio action: {action}")

    except Exception as e:
        logger.error(f"Error in portfolio callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        try:
            await query.message.reply_text(error_message, parse_mode="MarkdownV2")
        except Exception as e2:
            logger.error(f"Failed to send error message: {e2}")


async def refresh_portfolio_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh both the text message and photo with new settings"""
    query = update.callback_query
    bot = query.get_bot()

    chat_id = context.user_data.get("portfolio_chat_id")
    text_message_id = context.user_data.get("portfolio_text_message_id")
    photo_message_id = context.user_data.get("portfolio_photo_message_id")

    if not chat_id or not photo_message_id:
        logger.warning("Missing message IDs for refresh")
        return

    try:
        from servers import server_manager

        server_name = context.user_data.get("active_portfolio_server")
        if not server_name:
            return

        # Update caption to show "Updating..." status
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=photo_message_id,
                caption="🔄 Updating graph..."
            )
        except Exception as e:
            logger.warning(f"Failed to update caption to 'Updating': {e}")

        client = await server_manager.get_client(server_name)
        server_status_info = await server_manager.check_server_status(server_name)
        server_status = server_status_info.get("status", "online")

        # Get current config
        config = get_portfolio_prefs(context.user_data)
        days = config.get("days", 3)
        interval = config.get("interval", "1h")

        # Fetch all data
        overview_data, history, token_distribution, accounts_distribution = await _fetch_dashboard_data(
            client, days, interval
        )

        # Update text message if we have it
        if text_message_id:
            message = format_portfolio_overview(
                overview_data,
                server_name=server_name,
                server_status=server_status
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=text_message_id,
                    text=message,
                    parse_mode="MarkdownV2"
                )
            except Exception as e:
                logger.warning(f"Failed to update text message: {e}")

        # Generate new dashboard
        dashboard_bytes = generate_portfolio_dashboard(
            history_data=history,
            token_distribution_data=token_distribution,
            accounts_distribution_data=accounts_distribution
        )

        # Create settings button
        keyboard = [[
            InlineKeyboardButton(f"⚙️ Settings ({days}d / {interval})", callback_data="portfolio:settings")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Update photo with new image
        from telegram import InputMediaPhoto
        await bot.edit_message_media(
            chat_id=chat_id,
            message_id=photo_message_id,
            media=InputMediaPhoto(
                media=dashboard_bytes,
                caption=f"📊 Portfolio Dashboard - {server_name}"
            ),
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Failed to refresh portfolio dashboard: {e}", exc_info=True)


async def show_portfolio_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str = None) -> None:
    """Display portfolio settings menu"""
    query = update.callback_query

    config = get_portfolio_prefs(context.user_data)
    current_days = config.get("days", 3)
    current_interval = config.get("interval", "1h")

    # Build settings message
    settings_text = "⚙️ *Portfolio Graph Settings*\n\n"
    settings_text += f"📅 *Days:* `{current_days}`\n"
    settings_text += f"⏱️ *Interval:* `{current_interval}`\n"

    if message:
        settings_text += f"\n_{escape_markdown_v2(message)}_"

    # Build keyboard with days options
    days_buttons = []
    for days in PORTFOLIO_DAYS_OPTIONS:
        label = f"{'✓ ' if days == current_days else ''}{days}d"
        days_buttons.append(InlineKeyboardButton(label, callback_data=f"portfolio:set_days:{days}"))

    # Build keyboard with interval options
    interval_buttons = []
    for interval in PORTFOLIO_INTERVAL_OPTIONS:
        label = f"{'✓ ' if interval == current_interval else ''}{interval}"
        interval_buttons.append(InlineKeyboardButton(label, callback_data=f"portfolio:set_interval:{interval}"))

    keyboard = [
        days_buttons,
        interval_buttons,
        [
            InlineKeyboardButton("✅ Apply & Close", callback_data="portfolio:close")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Check if message has text (settings menu) or photo (dashboard image)
    if query.message.text:
        # Edit existing text message
        await query.edit_message_text(
            settings_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    else:
        # Message is a photo, send new text message for settings
        await query.message.reply_text(
            settings_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


def get_portfolio_callback_handler():
    """Returns the callback query handler for portfolio operations"""
    return CallbackQueryHandler(portfolio_callback_handler, pattern="^portfolio:")
