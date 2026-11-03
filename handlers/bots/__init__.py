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
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from handlers import clear_all_input_states
from utils.auth import hummingbot_api_required, restricted

# Archived bots handlers
from .archived import (
    handle_archived_refresh,
    handle_generate_report,
    show_archived_detail,
    show_archived_menu,
    show_bot_chart,
    show_timeline_chart,
)
from .controller_handlers import (  # Unified configs menu with multi-select; Edit loop; Progressive deploy flow; Streamlined deploy flow; Progressive Grid Strike wizard; PMM Mister wizard; Custom config upload
    handle_cfg_branch,
    handle_cfg_clear_selection,
    handle_cfg_delete_confirm,
    handle_cfg_delete_execute,
    handle_cfg_deploy,
    handle_cfg_edit_cancel,
    handle_cfg_edit_field,
    handle_cfg_edit_loop,
    handle_cfg_edit_next,
    handle_cfg_edit_prev,
    handle_cfg_edit_save,
    handle_cfg_edit_save_all,
    handle_cfg_page,
    handle_cfg_toggle,
    handle_clear_all,
    handle_config_file_upload,
    handle_configs_page,
    handle_cycle_order_type,
    handle_deploy_confirm,
    handle_deploy_custom_name,
    handle_deploy_edit_field,
    handle_deploy_prev_field,
    handle_deploy_progressive_input,
    handle_deploy_set_field,
    handle_deploy_skip_field,
    handle_deploy_use_default,
    handle_edit_config,
    handle_execute_deploy,
    handle_gs_accept_prices,
    handle_gs_back_to_amount,
    handle_gs_back_to_connector,
    handle_gs_back_to_leverage,
    handle_gs_back_to_pair,
    handle_gs_back_to_prices,
    handle_gs_back_to_side,
    handle_gs_edit_act,
    handle_gs_edit_batch,
    handle_gs_edit_id,
    handle_gs_edit_keep,
    handle_gs_edit_max_orders,
    handle_gs_edit_min_amt,
    handle_gs_edit_price,
    handle_gs_edit_spread,
    handle_gs_edit_tp,
    handle_gs_interval_change,
    handle_gs_pair_select,
    handle_gs_review_back,
    handle_gs_save,
    handle_gs_wizard_amount,
    handle_gs_wizard_connector,
    handle_gs_wizard_leverage,
    handle_gs_wizard_pair,
    handle_gs_wizard_side,
    handle_gs_wizard_take_profit,
    handle_pmm_adv_setting,
    handle_pmm_back,
    handle_pmm_edit_advanced,
    handle_pmm_edit_field,
    handle_pmm_edit_id,
    handle_pmm_pair_select,
    handle_pmm_review_back,
    handle_pmm_save,
    handle_pmm_set_field,
    handle_pmm_wizard_allocation,
    handle_pmm_wizard_amount,
    handle_pmm_wizard_connector,
    handle_pmm_wizard_leverage,
    handle_pmm_wizard_pair,
    handle_pmm_wizard_spreads,
    handle_pmm_wizard_tp,
    handle_pv1_back,
    handle_pv1_pair_select,
    handle_pv1_review_back,
    handle_pv1_save,
    handle_pv1_wizard_amount,
    handle_pv1_wizard_connector,
    handle_pv1_wizard_pair,
    handle_pv1_wizard_spreads,
    handle_save_config,
    handle_select_all,
    handle_select_connector,
    handle_select_credentials,
    handle_select_image,
    handle_select_instance_name,
    handle_set_field,
    handle_toggle_deploy_selection,
    handle_toggle_position_mode,
    handle_toggle_side,
    handle_upload_cancel,
    process_cfg_edit_input,
    process_deploy_custom_name_input,
    process_deploy_field_input,
    process_field_input,
    process_gs_wizard_input,
    process_instance_name_input,
    process_pmm_wizard_input,
    process_pv1_wizard_input,
    show_cfg_edit_form,
    show_config_form,
    show_configs_by_type,
    show_configs_list,
    show_controller_configs_menu,
    show_deploy_config_step,
    show_deploy_configure,
    show_deploy_form,
    show_deploy_menu,
    show_new_grid_strike_form,
    show_new_pmm_mister_form,
    show_new_pmm_v1_form,
    show_type_selector,
    show_upload_config_prompt,
)

# Import submodule handlers
from .menu import (  # Controller chart & edit
    handle_back_to_bot,
    handle_clone_controller,
    handle_close,
    handle_confirm_start_controller,
    handle_confirm_stop_bot,
    handle_confirm_stop_controller,
    handle_controller_confirm_set,
    handle_controller_set_field,
    handle_quick_start_controller,
    handle_quick_stop_controller,
    handle_refresh,
    handle_refresh_bot,
    handle_refresh_controller,
    handle_start_controller,
    handle_stop_bot,
    handle_stop_controller,
    process_controller_field_input,
    show_bot_detail,
    show_bot_logs,
    show_bots_menu,
    show_controller_chart,
    show_controller_detail,
    show_controller_edit,
)

logger = logging.getLogger(__name__)


# ============================================
# MAIN BOTS COMMAND
# ============================================


@restricted
@hummingbot_api_required
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
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg:
        logger.error("No message object available for bots_command")
        return

    await msg.reply_chat_action("typing")

    # Check if specific bot name was provided
    if update.message and context.args and len(context.args) > 0:
        bot_name = context.args[0]
        chat_id = update.effective_chat.id
        # For direct command with bot name, show detail view
        from utils.telegram_formatters import format_bot_status, format_error_message

        from ._shared import get_bots_client

        try:
            client, _ = await get_bots_client(chat_id, context.user_data)
            bot_status = await client.bot_orchestration.get_bot_status(bot_name)
            response_message = format_bot_status(bot_status)
            await msg.reply_text(response_message, parse_mode="MarkdownV2")
        except Exception as e:
            logger.error(f"Error fetching bot status: {e}", exc_info=True)
            error_message = format_error_message(
                f"Failed to fetch bot status: {str(e)}"
            )
            await msg.reply_text(error_message, parse_mode="MarkdownV2")
        return

    # Show the interactive menu
    await show_bots_menu(update, context)


@restricted
@hummingbot_api_required
async def new_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /new_bot command - Show controller configs menu for creating new bots"""
    clear_all_input_states(context)
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg:
        await msg.reply_chat_action("typing")
    await show_controller_configs_menu(update, context)


# ============================================
# CALLBACK ACTION DISPATCH TABLES
# ============================================

HandlerFunc = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]
ParamHandlerFunc = Callable[..., Awaitable[None]]

# A parameter parser reads one value out of a callback. It receives the tail of
# the callback parts starting at its own parameter, so _as_rest can take
# everything that is left.
ParamParser = Callable[[list[str]], Any]


def _as_text(parts: list[str]) -> str:
    """Take the next part verbatim."""
    return parts[0]


def _as_int(parts: list[str]) -> int:
    """Take the next part as an integer."""
    return int(parts[0])


def _as_float(parts: list[str]) -> float:
    """Take the next part as a float."""
    return float(parts[0])


def _as_rest(parts: list[str]) -> str:
    """Take every remaining part, rejoined - for values that contain a colon."""
    return ":".join(parts)


# Actions dispatched with no parameters: "bots:{action}". A None handler is an
# action that deliberately does nothing.
SIMPLE_ACTIONS: dict[str, HandlerFunc | None] = {
    # Menu navigation
    "main_menu": show_bots_menu,
    "refresh": handle_refresh,
    "close": handle_close,
    # Controller configs menu
    "controller_configs": show_controller_configs_menu,
    "list_configs": show_configs_list,
    # Unified configs menu with multi-select
    "cfg_select_type": show_type_selector,
    "cfg_clear_selection": handle_cfg_clear_selection,
    "cfg_deploy": handle_cfg_deploy,
    "cfg_delete_confirm": handle_cfg_delete_confirm,
    "cfg_delete_execute": handle_cfg_delete_execute,
    # Edit loop handlers
    "cfg_edit_loop": handle_cfg_edit_loop,
    "cfg_edit_form": show_cfg_edit_form,
    "cfg_edit_prev": handle_cfg_edit_prev,
    "cfg_edit_next": handle_cfg_edit_next,
    "cfg_edit_save": handle_cfg_edit_save,
    "cfg_edit_save_all": handle_cfg_edit_save_all,
    "cfg_edit_cancel": handle_cfg_edit_cancel,
    "cfg_branch": handle_cfg_branch,
    # Custom config upload
    "upload_config": show_upload_config_prompt,
    "upload_cancel": handle_upload_cancel,
    "noop": None,  # Pagination display button - deliberately does nothing
    "new_grid_strike": show_new_grid_strike_form,
    "new_pmm_mister": show_new_pmm_mister_form,
    "new_pmm_v1": show_new_pmm_v1_form,
    # PMM V1 wizard
    "pv1_save": handle_pv1_save,
    "pv1_review_back": handle_pv1_review_back,
    # Config form
    "edit_config_back": show_config_form,
    "toggle_side": handle_toggle_side,
    "toggle_position_mode": handle_toggle_position_mode,
    "save_config": handle_save_config,
    # Deploy menu
    "deploy_menu": show_deploy_menu,
    "select_all": handle_select_all,
    "clear_all": handle_clear_all,
    "deploy_configure": show_deploy_configure,
    "deploy_form_back": show_deploy_form,
    "execute_deploy": handle_execute_deploy,
    "deploy_skip_field": handle_deploy_skip_field,
    "deploy_prev_field": handle_deploy_prev_field,
    # Streamlined deploy flow
    "deploy_config": show_deploy_config_step,
    "deploy_confirm": handle_deploy_confirm,
    "deploy_custom_name": handle_deploy_custom_name,
    # Progressive Grid Strike wizard
    "gs_accept_prices": handle_gs_accept_prices,
    "gs_back_to_prices": handle_gs_back_to_prices,
    "gs_back_to_connector": handle_gs_back_to_connector,
    "gs_back_to_pair": handle_gs_back_to_pair,
    "gs_back_to_side": handle_gs_back_to_side,
    "gs_back_to_leverage": handle_gs_back_to_leverage,
    "gs_back_to_amount": handle_gs_back_to_amount,
    "gs_edit_id": handle_gs_edit_id,
    "gs_edit_keep": handle_gs_edit_keep,
    "gs_edit_tp": handle_gs_edit_tp,
    "gs_edit_act": handle_gs_edit_act,
    "gs_edit_max_orders": handle_gs_edit_max_orders,
    "gs_edit_batch": handle_gs_edit_batch,
    "gs_edit_min_amt": handle_gs_edit_min_amt,
    "gs_edit_spread": handle_gs_edit_spread,
    "gs_save": handle_gs_save,
    "gs_review_back": handle_gs_review_back,
    # PMM Mister wizard
    "pmm_save": handle_pmm_save,
    "pmm_review_back": handle_pmm_review_back,
    "pmm_edit_id": handle_pmm_edit_id,
    "pmm_edit_advanced": handle_pmm_edit_advanced,
    # Controller chart & edit
    "ctrl_chart": show_controller_chart,
    "ctrl_edit": show_controller_edit,
    # Stop controller (uses context)
    "stop_ctrl": handle_stop_controller,
    "confirm_stop_ctrl": handle_confirm_stop_controller,
    # Start controller (uses context)
    "start_ctrl": handle_start_controller,
    "confirm_start_ctrl": handle_confirm_start_controller,
    # Clone controller (PMM Mister only)
    "clone_ctrl": handle_clone_controller,
    # Stop bot (uses context)
    "stop_bot": handle_stop_bot,
    "confirm_stop_bot": handle_confirm_stop_bot,
    # View logs (uses context)
    "view_logs": show_bot_logs,
    # Navigation
    "back_to_bot": handle_back_to_bot,
    "refresh_bot": handle_refresh_bot,
    # Archived bots handlers
    "archived": show_archived_menu,
    "archived_timeline": show_timeline_chart,
    "archived_refresh": handle_archived_refresh,
}

# Actions carrying parameters: "bots:{action}:{param}[:{param}]". The parser
# tuple names each parameter the handler takes after (update, context).
PARAMETERIZED_ACTIONS: dict[str, tuple[ParamHandlerFunc, tuple[ParamParser, ...]]] = {
    "configs_page": (handle_configs_page, (_as_int,)),
    "cfg_type": (show_configs_by_type, (_as_text,)),
    "cfg_toggle": (handle_cfg_toggle, (_as_text,)),
    "cfg_page": (handle_cfg_page, (_as_int,)),
    "cfg_edit_field": (handle_cfg_edit_field, (_as_text,)),
    "pv1_connector": (handle_pv1_wizard_connector, (_as_text,)),
    "pv1_pair": (handle_pv1_wizard_pair, (_as_text,)),
    "pv1_pair_select": (handle_pv1_pair_select, (_as_text,)),
    "pv1_amount": (handle_pv1_wizard_amount, (_as_text,)),
    "pv1_spreads": (handle_pv1_wizard_spreads, (_as_text,)),
    "pv1_back": (handle_pv1_back, (_as_text,)),
    "edit_config": (handle_edit_config, (_as_int,)),
    "set_field": (handle_set_field, (_as_text,)),
    "cycle_order_type": (
        handle_cycle_order_type,
        (_as_text,),
    ),  # order_type_key: 'open' or 'tp'
    "select_connector": (handle_select_connector, (_as_text,)),
    "toggle_deploy": (handle_toggle_deploy_selection, (_as_int,)),
    "deploy_set": (handle_deploy_set_field, (_as_text,)),
    # Progressive deploy flow
    "deploy_use_default": (handle_deploy_use_default, (_as_text,)),
    "deploy_edit": (handle_deploy_edit_field, (_as_text,)),
    # Streamlined deploy flow
    "select_creds": (handle_select_credentials, (_as_text,)),
    "select_image": (
        handle_select_image,
        (_as_rest,),
    ),  # colons preserved: 'hummingbot:development'
    "select_name": (handle_select_instance_name, (_as_text,)),
    # Progressive Grid Strike wizard
    "gs_connector": (handle_gs_wizard_connector, (_as_text,)),
    "gs_pair": (handle_gs_wizard_pair, (_as_text,)),
    "gs_pair_select": (handle_gs_pair_select, (_as_text,)),
    "gs_side": (handle_gs_wizard_side, (_as_text,)),
    "gs_leverage": (handle_gs_wizard_leverage, (_as_int,)),
    "gs_amount": (handle_gs_wizard_amount, (_as_float,)),
    "gs_interval": (handle_gs_interval_change, (_as_text,)),
    "gs_edit_price": (handle_gs_edit_price, (_as_text,)),
    "gs_tp": (handle_gs_wizard_take_profit, (_as_float,)),
    # PMM Mister wizard
    "pmm_connector": (handle_pmm_wizard_connector, (_as_text,)),
    "pmm_pair": (handle_pmm_wizard_pair, (_as_text,)),
    "pmm_pair_select": (handle_pmm_pair_select, (_as_text,)),
    "pmm_leverage": (handle_pmm_wizard_leverage, (_as_int,)),
    "pmm_alloc": (handle_pmm_wizard_allocation, (_as_float,)),
    "pmm_amount": (handle_pmm_wizard_amount, (_as_float,)),
    "pmm_spreads": (handle_pmm_wizard_spreads, (_as_text,)),
    "pmm_tp": (handle_pmm_wizard_tp, (_as_float,)),
    "pmm_back": (handle_pmm_back, (_as_text,)),
    "pmm_edit": (handle_pmm_edit_field, (_as_text,)),
    "pmm_set": (handle_pmm_set_field, (_as_text, _as_text)),
    "pmm_adv": (handle_pmm_adv_setting, (_as_text,)),
    # Bot detail
    "bot_detail": (show_bot_detail, (_as_text,)),
    # Controller detail (by index, uses context)
    "ctrl_idx": (show_controller_detail, (_as_int,)),
    "ctrl_set": (handle_controller_set_field, (_as_text,)),
    "ctrl_confirm_set": (handle_controller_confirm_set, (_as_text, _as_text)),
    # Quick stop/start controller (from bot detail view)
    "stop_ctrl_quick": (handle_quick_stop_controller, (_as_int,)),
    "start_ctrl_quick": (handle_quick_start_controller, (_as_int,)),
    "refresh_ctrl": (handle_refresh_controller, (_as_int,)),
    # Archived bots handlers
    "archived_page": (show_archived_menu, (_as_int,)),
    "archived_select": (show_archived_detail, (_as_int,)),
    "archived_chart": (show_bot_chart, (_as_int,)),
    "archived_report": (handle_generate_report, (_as_int,)),
}


async def _handle_parameterized_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE, action_parts: list[str]
) -> bool:
    """
    Dispatch an action that carries parameters.

    Returns True when the action belongs to this module and was consumed -
    including the case where the callback carries too few parts to call the
    handler, which is ignored just as the missing-parameter branches were.
    Returns False when the action is not ours, or when a parameter could not be
    parsed; both fall through to the unknown-action reply.
    """
    entry = PARAMETERIZED_ACTIONS.get(action_parts[0])
    if entry is None:
        return False

    handler, parsers = entry
    params = action_parts[1:]
    if len(params) < len(parsers):
        return True

    try:
        args = [parse(params[i:]) for i, parse in enumerate(parsers)]
    except ValueError:
        logger.warning(f"Malformed parameters in bots action: {':'.join(action_parts)}")
        return False

    await handler(update, context, *args)
    return True


# ============================================
# CALLBACK HANDLER
# ============================================


@restricted
async def bots_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle inline button callbacks - Routes to appropriate handler"""
    query = update.callback_query
    await query.answer()

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        # Parse action and any additional parameters
        action_parts = action.split(":")
        main_action = action_parts[0]

        # Zero-parameter actions
        if main_action in SIMPLE_ACTIONS:
            handler = SIMPLE_ACTIONS[main_action]
            if handler is not None:
                await handler(update, context)
            return

        # Actions carrying parameters
        if await _handle_parameterized_action(update, context, action_parts):
            return

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
async def bots_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        # Handle live controller bulk edit input
        elif bots_state == "ctrl_bulk_edit":
            await process_controller_field_input(update, context, user_input)
        # Handle live controller field input (legacy single field)
        elif bots_state.startswith("ctrl_set:"):
            await process_controller_field_input(update, context, user_input)
        # Handle deploy field input (legacy form)
        elif bots_state.startswith("deploy_set:"):
            await process_deploy_field_input(update, context, user_input)
        # Handle progressive deploy flow input
        elif bots_state == "deploy_progressive":
            await handle_deploy_progressive_input(update, context)
        # Handle custom instance name input for streamlined deploy
        elif bots_state == "deploy_custom_name":
            await process_deploy_custom_name_input(update, context, user_input)
        # Handle instance name edit in config step
        elif bots_state == "deploy_edit_name":
            await process_instance_name_input(update, context, user_input)
        # Handle Grid Strike wizard input
        elif bots_state == "gs_wizard_input":
            await process_gs_wizard_input(update, context, user_input)
        # Handle PMM Mister wizard input
        elif bots_state == "pmm_wizard_input":
            await process_pmm_wizard_input(update, context, user_input)
        # Handle PMM V1 wizard input
        elif bots_state == "pv1_wizard_input":
            await process_pv1_wizard_input(update, context, user_input)
        # Handle config edit loop field input (legacy single field)
        elif bots_state.startswith("cfg_edit_input:"):
            await process_cfg_edit_input(update, context, user_input)
        # Handle config bulk edit (key=value format)
        elif bots_state == "cfg_bulk_edit":
            await process_cfg_edit_input(update, context, user_input)
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
    return CallbackQueryHandler(bots_callback_handler, pattern="^bots:")


def get_bots_message_handler():
    """Returns the message handler"""
    return MessageHandler(filters.TEXT & ~filters.COMMAND, bots_message_handler)


@restricted
async def bots_document_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle document uploads for bots module (e.g., config file uploads)"""
    # Only process if we're expecting a config upload
    if context.user_data.get("bots_state") == "awaiting_config_upload":
        await handle_config_file_upload(update, context)


def get_bots_document_handler():
    """Get the document handler for bots module"""
    return MessageHandler(filters.Document.ALL, bots_document_handler)


__all__ = [
    "bots_command",
    "bots_callback_handler",
    "bots_message_handler",
    "bots_document_handler",
    "get_bots_callback_handler",
    "get_bots_message_handler",
    "get_bots_document_handler",
]
