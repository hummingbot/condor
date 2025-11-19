"""
Configuration management command handlers
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from utils.auth import restricted

logger = logging.getLogger(__name__)


def clear_config_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear all config-related state from user context.
    Call this when starting other commands to prevent state pollution.
    """
    context.user_data.pop('modifying_server', None)
    context.user_data.pop('modifying_field', None)
    context.user_data.pop('awaiting_modify_input', None)
    context.user_data.pop('adding_server', None)
    context.user_data.pop('awaiting_add_server_input', None)
    context.user_data.pop('configuring_api_key', None)
    context.user_data.pop('awaiting_api_key_input', None)
    context.user_data.pop('api_key_config_data', None)
    context.user_data.pop('gateway_state', None)
    context.user_data.pop('awaiting_gateway_input', None)


def _get_config_menu_markup_and_text():
    """
    Build the main config menu keyboard and message text
    """
    keyboard = [
        [
            InlineKeyboardButton("🔌 API Servers", callback_data="config_api_servers"),
            InlineKeyboardButton("🔑 API Keys", callback_data="config_api_keys"),
        ],
        [
            InlineKeyboardButton("🌐 Gateway", callback_data="config_gateway"),
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="config_close"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_text = (
        "⚙️ *Configuration Menu*\n\n"
        "Select a configuration category:\n\n"
        "🔌 *API Servers* \- Manage Hummingbot API instances\n"
        "🔑 *API Keys* \- Manage exchange credentials\n"
        "🌐 *Gateway* \- Manage Gateway container and DEX configuration"
    )

    return reply_markup, message_text


async def show_config_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show the main config menu
    """
    reply_markup, message_text = _get_config_menu_markup_and_text()

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


@restricted
async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /config command - Show configuration options

    Displays a menu with configuration categories:
    - API Servers (Hummingbot instances)
    - API Keys (Exchange API credentials)
    - Gateway (Gateway container and DEX operations)
    """
    reply_markup, message_text = _get_config_menu_markup_and_text()

    await update.message.reply_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def config_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle callback queries from config menu buttons - Routes to appropriate sub-module
    """
    from .servers import handle_servers_callback
    from .api_keys import handle_api_keys_callback
    from .gateway import handle_gateway_callback

    query = update.callback_query
    await query.answer()

    # Main menu actions
    if query.data == "config_close":
        await query.message.delete()
        return
    elif query.data == "config_back":
        await show_config_menu(query, context)
        return

    # Route to appropriate sub-module based on callback data prefix
    if query.data == "config_api_servers" or query.data.startswith(("api_server_", "modify_field_", "add_server_")):
        await handle_servers_callback(update, context)
    elif query.data == "config_api_keys" or query.data.startswith("api_key_"):
        await handle_api_keys_callback(update, context)
    elif query.data == "config_gateway" or query.data.startswith("gateway_"):
        await handle_gateway_callback(update, context)


# Create callback handler instance for registration
def get_config_callback_handler():
    """Get the callback query handler for config menu"""
    return CallbackQueryHandler(
        config_callback_handler,
        pattern="^config_|^modify_field_|^add_server_|^api_server_|^api_key_|^gateway_"
    )


def get_modify_value_handler():
    """
    Get the message handler for text input during configuration flows
    """
    from .servers import handle_server_input
    from .api_keys import handle_api_key_input
    from .gateway import handle_gateway_input

    async def handle_all_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Route text input to appropriate handler based on context state"""
        # Check which config flow is active
        if context.user_data.get('awaiting_add_server_input') or context.user_data.get('awaiting_modify_input'):
            await handle_server_input(update, context)
        elif context.user_data.get('awaiting_api_key_input'):
            await handle_api_key_input(update, context)
        elif context.user_data.get('awaiting_gateway_input'):
            await handle_gateway_input(update, context)

    return MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_config_input)
