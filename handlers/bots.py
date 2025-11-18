"""
Bots command handler using hummingbot_api_client
"""

import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from hummingbot_api_client import HummingbotAPIClient

from utils.auth import restricted
from utils.telegram_formatters import format_active_bots, format_bot_status, format_error_message

logger = logging.getLogger(__name__)


@restricted
async def bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /bots command - Display active bots status

    Usage:
        /bots - Show all active bots with their status
        /bots <bot_name> - Show detailed status for a specific bot
    """
    # Send "typing" status
    await update.message.reply_chat_action("typing")

    try:
        # Initialize API client
        async with HummingbotAPIClient(
            base_url=f"http://{os.environ.get('BACKEND_API_HOST', 'localhost')}:{os.environ.get('BACKEND_API_PORT', '8000')}",
            username=os.environ.get("BACKEND_API_USERNAME", "admin"),
            password=os.environ.get("BACKEND_API_PASSWORD", "admin"),
        ) as client:

            if len(context.args) > 0:
                # Get specific bot status
                bot_name = context.args[0]
                bot_status = await client.bot_orchestration.get_bot_status(bot_name)
                message = format_bot_status(bot_status)
            else:
                # Get all active bots
                bots_data = await client.bot_orchestration.get_active_bots_status()
                message = format_active_bots(bots_data)

            await update.message.reply_text(message, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error fetching bots status: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch bots status: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
