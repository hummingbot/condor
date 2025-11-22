"""
CLOB Trading - Account management
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_error_message, escape_markdown_v2
from handlers.config.user_preferences import set_clob_account

logger = logging.getLogger(__name__)


async def handle_change_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle change account operation"""
    help_text = (
        r"🔧 *Change Trading Account*" + "\n\n"
        r"Enter account name:" + "\n\n"
        r"`<account_name>`" + "\n\n"
        r"*Example:*" + "\n"
        r"`master_account`"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="clob:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "change_account"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def process_change_account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_name: str
) -> None:
    """Process change account user input"""
    try:
        set_clob_account(context.user_data, account_name)

        success_msg = escape_markdown_v2(
            f"✅ Trading account changed to: {account_name}\n\n"
            f"This will be used for all future trades."
        )

        # Create keyboard with back button
        keyboard = [[InlineKeyboardButton("« Back to CLOB Trading", callback_data="clob:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            success_msg,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error changing account: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to change account: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
