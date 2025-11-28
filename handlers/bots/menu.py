"""
Bots menu - Main menu and bot status display

Provides:
- Main bots menu with interactive buttons
- Bot status display
- Navigation helpers
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_active_bots, format_bot_status, format_error_message, escape_markdown_v2
from ._shared import get_bots_client, clear_bots_state

logger = logging.getLogger(__name__)


# ============================================
# MENU KEYBOARD BUILDERS
# ============================================

def _build_main_menu_keyboard(has_bots: bool = False) -> InlineKeyboardMarkup:
    """Build the main bots menu keyboard

    Args:
        has_bots: Whether there are active bots to show

    Returns:
        InlineKeyboardMarkup with menu buttons
    """
    keyboard = [
        [
            InlineKeyboardButton("Controller Configs", callback_data="bots:controller_configs"),
            InlineKeyboardButton("Deploy Controllers", callback_data="bots:deploy_menu"),
        ],
    ]

    if has_bots:
        keyboard.append([
            InlineKeyboardButton("Refresh Status", callback_data="bots:refresh"),
        ])

    keyboard.append([
        InlineKeyboardButton("Close", callback_data="bots:close"),
    ])

    return InlineKeyboardMarkup(keyboard)


# ============================================
# MAIN MENU DISPLAY
# ============================================

async def show_bots_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the main bots menu with status and buttons

    Args:
        update: Telegram update
        context: Telegram context
    """
    # Clear any previous bots state
    clear_bots_state(context)

    # Determine if this is a callback query or direct command
    query = update.callback_query
    msg = update.message or (query.message if query else None)

    if not msg:
        logger.error("No message object available for show_bots_menu")
        return

    try:
        client = await get_bots_client()
        bots_data = await client.bot_orchestration.get_active_bots_status()

        # Format the bot status message
        status_message = format_active_bots(bots_data)
        has_bots = bool(bots_data)

        # Build the menu
        reply_markup = _build_main_menu_keyboard(has_bots)

        # Add header
        header = r"*Bots Dashboard*" + "\n\n"
        full_message = header + status_message

        if query:
            await query.message.edit_text(
                full_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            await msg.reply_text(
                full_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error showing bots menu: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch bots status: {str(e)}")

        reply_markup = _build_main_menu_keyboard(has_bots=False)

        if query:
            await query.message.edit_text(
                error_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            await msg.reply_text(
                error_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )


async def show_bot_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str) -> None:
    """Show detailed status for a specific bot

    Args:
        update: Telegram update
        context: Telegram context
        bot_name: Name of the bot to show
    """
    query = update.callback_query

    try:
        client = await get_bots_client()
        bot_status = await client.bot_orchestration.get_bot_status(bot_name)

        status_message = format_bot_status(bot_status)

        keyboard = [
            [InlineKeyboardButton("Back to Bots", callback_data="bots:main_menu")],
            [InlineKeyboardButton("Close", callback_data="bots:close")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            status_message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing bot detail: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch bot status: {str(e)}")
        await query.message.edit_text(error_message, parse_mode="MarkdownV2")


# ============================================
# MENU ACTIONS
# ============================================

async def handle_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle refresh button - reload bot status

    Args:
        update: Telegram update
        context: Telegram context
    """
    await show_bots_menu(update, context)


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle close button - delete the menu message

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    clear_bots_state(context)

    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete message: {e}")
