"""
Bots command handler using hummingbot_api_client
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from utils.auth import restricted
from utils.telegram_formatters import format_active_bots, format_bot_status, format_error_message
from handlers.config import clear_config_state

logger = logging.getLogger(__name__)


@restricted
async def bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /bots command - Display active bots status

    Usage:
        /bots - Show all active bots with their status
        /bots <bot_name> - Show detailed status for a specific bot
    """
    # Clear any config state to prevent interference
    clear_config_state(context)

    # Get the appropriate message object for replies
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        logger.error("No message object available for bots_command")
        return

    # Send "typing" status
    await msg.reply_chat_action("typing")

    try:
        from servers import server_manager

        # Get first enabled server
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            error_message = format_error_message("No enabled API servers. Edit servers.yml to enable a server.")
            await msg.reply_text(error_message, parse_mode="MarkdownV2")
            return

        # Get client from first enabled server
        client = await server_manager.get_client(enabled_servers[0])

        if context.args and len(context.args) > 0:
            # Get specific bot status
            bot_name = context.args[0]
            bot_status = await client.bot_orchestration.get_bot_status(bot_name)
            response_message = format_bot_status(bot_status)
        else:
            # Get all active bots
            bots_data = await client.bot_orchestration.get_active_bots_status()
            response_message = format_active_bots(bots_data)

        await msg.reply_text(response_message, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error fetching bots status: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch bots status: {str(e)}")
        await msg.reply_text(error_message, parse_mode="MarkdownV2")
