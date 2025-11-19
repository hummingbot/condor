"""
Portfolio command handler using hummingbot_api_client
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from utils.auth import restricted
from utils.telegram_formatters import format_portfolio_summary, format_portfolio_state, format_error_message
from handlers.config import clear_config_state

logger = logging.getLogger(__name__)


@restricted
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /portfolio command - Display detailed portfolio breakdown by account and connector

    Usage:
        /portfolio - Show detailed breakdown by account and connector
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

        # Get client from first enabled server
        client = await server_manager.get_client(enabled_servers[0])

        # Get detailed portfolio state
        state = await client.portfolio.get_state()
        message = format_portfolio_state(state)

        await update.message.reply_text(message, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch portfolio: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
