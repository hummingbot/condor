"""
Portfolio command handler using hummingbot_api_client
"""

import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from hummingbot_api_client import HummingbotAPIClient

from utils.auth import restricted
from utils.telegram_formatters import format_portfolio_summary, format_portfolio_state, format_error_message

logger = logging.getLogger(__name__)


@restricted
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /portfolio command - Display portfolio summary and holdings

    Usage:
        /portfolio - Show portfolio summary
        /portfolio detailed - Show detailed breakdown by account and connector
    """
    # Check if detailed view is requested
    detailed = len(context.args) > 0 and context.args[0].lower() == "detailed"

    # Send "typing" status
    await update.message.reply_chat_action("typing")

    try:
        # Initialize API client
        async with HummingbotAPIClient(
            base_url=f"http://{os.environ.get('BACKEND_API_HOST', 'localhost')}:{os.environ.get('BACKEND_API_PORT', '8000')}",
            username=os.environ.get("BACKEND_API_USERNAME", "admin"),
            password=os.environ.get("BACKEND_API_PASSWORD", "admin"),
        ) as client:

            if detailed:
                # Get detailed portfolio state
                state = await client.portfolio.get_state()
                message = format_portfolio_state(state)
            else:
                # Get portfolio summary
                summary = await client.portfolio.get_portfolio_summary()
                message = format_portfolio_summary(summary)

            await update.message.reply_text(message, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch portfolio: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
