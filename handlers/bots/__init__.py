"""
Bots module - Bot management and controller configuration

Supports:
- View active bots status
- Controller configuration (Grid Strike)
- Deploy controllers to backend

Structure:
- menu.py: Main bots menu and status display
- controllers.py: Controller config management
- _shared.py: Shared utilities and defaults
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from utils.auth import restricted
from handlers import clear_all_input_states

# Import submodule handlers
from .menu import show_bots_menu, show_bot_detail, handle_refresh, handle_close
from .controllers import (
    show_controller_configs_menu,
    show_configs_list,
    show_new_grid_strike_form,
    show_config_form,
    handle_set_field,
    handle_toggle_side,
    handle_select_connector,
    process_field_input,
    handle_save_config,
    handle_edit_config,
    show_deploy_menu,
    show_deploy_configure,
    show_deploy_form,
    handle_toggle_deploy_selection,
    handle_select_all,
    handle_clear_all,
    handle_deploy_set_field,
    process_deploy_field_input,
    handle_execute_deploy,
)
from ._shared import clear_bots_state, SIDE_LONG, SIDE_SHORT

logger = logging.getLogger(__name__)


# ============================================
# MAIN BOTS COMMAND
# ============================================

@restricted
async def bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /bots command - Display bots dashboard

    Usage:
        /bots - Show bots dashboard with status and controller options
        /bots <bot_name> - Show detailed status for a specific bot
    """
    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    # Get the appropriate message object for replies
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        logger.error("No message object available for bots_command")
        return

    await msg.reply_chat_action("typing")

    # Check if specific bot name was provided
    if update.message and context.args and len(context.args) > 0:
        bot_name = context.args[0]
        # For direct command with bot name, show detail view
        from utils.telegram_formatters import format_bot_status, format_error_message
        from ._shared import get_bots_client

        try:
            client = await get_bots_client()
            bot_status = await client.bot_orchestration.get_bot_status(bot_name)
            response_message = format_bot_status(bot_status)
            await msg.reply_text(response_message, parse_mode="MarkdownV2")
        except Exception as e:
            logger.error(f"Error fetching bot status: {e}", exc_info=True)
            error_message = format_error_message(f"Failed to fetch bot status: {str(e)}")
            await msg.reply_text(error_message, parse_mode="MarkdownV2")
        return

    # Show the interactive menu
    await show_bots_menu(update, context)


# ============================================
# CALLBACK HANDLER
# ============================================

@restricted
async def bots_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks - Routes to appropriate handler"""
    query = update.callback_query
    await query.answer()

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        # Parse action and any additional parameters
        action_parts = action.split(":")
        main_action = action_parts[0]

        # Menu navigation
        if main_action == "main_menu":
            await show_bots_menu(update, context)

        elif main_action == "refresh":
            await handle_refresh(update, context)

        elif main_action == "close":
            await handle_close(update, context)

        # Controller configs menu
        elif main_action == "controller_configs":
            await show_controller_configs_menu(update, context)

        elif main_action == "list_configs":
            await show_configs_list(update, context)

        elif main_action == "new_grid_strike":
            await show_new_grid_strike_form(update, context)

        elif main_action == "edit_config":
            if len(action_parts) > 1:
                config_index = int(action_parts[1])
                await handle_edit_config(update, context, config_index)

        elif main_action == "edit_config_back":
            await show_config_form(update, context)

        elif main_action == "set_field":
            if len(action_parts) > 1:
                field_name = action_parts[1]
                await handle_set_field(update, context, field_name)

        elif main_action == "toggle_side":
            await handle_toggle_side(update, context)

        elif main_action == "select_connector":
            if len(action_parts) > 1:
                connector_name = action_parts[1]
                await handle_select_connector(update, context, connector_name)

        elif main_action == "save_config":
            await handle_save_config(update, context)

        # Deploy menu
        elif main_action == "deploy_menu":
            await show_deploy_menu(update, context)

        elif main_action == "toggle_deploy":
            if len(action_parts) > 1:
                index = int(action_parts[1])
                await handle_toggle_deploy_selection(update, context, index)

        elif main_action == "select_all":
            await handle_select_all(update, context)

        elif main_action == "clear_all":
            await handle_clear_all(update, context)

        elif main_action == "deploy_configure":
            await show_deploy_configure(update, context)

        elif main_action == "deploy_form_back":
            await show_deploy_form(update, context)

        elif main_action == "deploy_set":
            if len(action_parts) > 1:
                field_name = action_parts[1]
                await handle_deploy_set_field(update, context, field_name)

        elif main_action == "execute_deploy":
            await handle_execute_deploy(update, context)

        # Bot detail
        elif main_action == "bot_detail":
            if len(action_parts) > 1:
                bot_name = action_parts[1]
                await show_bot_detail(update, context, bot_name)

        else:
            logger.warning(f"Unknown bots action: {action}")
            await query.message.reply_text(f"Unknown action: {action}")

    except Exception as e:
        # Ignore "message is not modified" errors
        if "not modified" in str(e).lower():
            logger.debug(f"Message not modified (ignored): {e}")
            return

        logger.error(f"Error in bots callback handler: {e}", exc_info=True)
        from utils.telegram_formatters import format_error_message
        error_message = format_error_message(f"Operation failed: {str(e)}")
        try:
            await query.message.reply_text(error_message, parse_mode="MarkdownV2")
        except Exception as reply_error:
            logger.warning(f"Failed to send error message: {reply_error}")


# ============================================
# MESSAGE HANDLER
# ============================================

@restricted
async def bots_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user text input - Routes to appropriate processor"""
    bots_state = context.user_data.get("bots_state")

    if not bots_state:
        return

    user_input = update.message.text.strip()
    logger.info(f"Bots message handler - state: {bots_state}, input: {user_input}")

    try:
        # Handle controller config field input
        if bots_state.startswith("set_field:"):
            await process_field_input(update, context, user_input)
        # Handle deploy field input
        elif bots_state.startswith("deploy_set:"):
            await process_deploy_field_input(update, context, user_input)
        else:
            logger.debug(f"Unhandled bots state: {bots_state}")

    except Exception as e:
        logger.error(f"Error processing bots input: {e}", exc_info=True)
        from utils.telegram_formatters import format_error_message
        error_message = format_error_message(f"Failed to process input: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# HANDLER FACTORIES
# ============================================

def get_bots_callback_handler():
    """Get the callback query handler for bots menu"""
    return CallbackQueryHandler(
        bots_callback_handler,
        pattern="^bots:"
    )


def get_bots_message_handler():
    """Returns the message handler"""
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        bots_message_handler
    )


__all__ = [
    'bots_command',
    'bots_callback_handler',
    'bots_message_handler',
    'get_bots_callback_handler',
    'get_bots_message_handler',
]
