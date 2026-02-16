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

from utils.auth import restricted, hummingbot_api_required
from handlers import clear_all_input_states

# Import submodule handlers
from .menu import (
    show_bots_menu,
    show_bot_detail,
    handle_refresh,
    handle_close,
    show_controller_detail,
    handle_stop_controller,
    handle_confirm_stop_controller,
    handle_start_controller,
    handle_confirm_start_controller,
    handle_clone_controller,
    handle_quick_stop_controller,
    handle_quick_start_controller,
    handle_stop_bot,
    handle_confirm_stop_bot,
    show_bot_logs,
    handle_back_to_bot,
    handle_refresh_bot,
    handle_refresh_controller,
    # Controller chart & edit
    show_controller_chart,
    show_controller_edit,
    handle_controller_set_field,
    handle_controller_confirm_set,
    process_controller_field_input,
)
from .controller_handlers import (
    show_controller_configs_menu,
    show_configs_list,
    handle_configs_page,
    # Unified configs menu with multi-select
    show_configs_by_type,
    show_type_selector,
    handle_cfg_toggle,
    handle_cfg_page,
    handle_cfg_clear_selection,
    handle_cfg_delete_confirm,
    handle_cfg_delete_execute,
    handle_cfg_deploy,
    # Edit loop
    handle_cfg_edit_loop,
    show_cfg_edit_form,
    handle_cfg_edit_field,
    process_cfg_edit_input,
    handle_cfg_edit_prev,
    handle_cfg_edit_next,
    handle_cfg_edit_save,
    handle_cfg_edit_save_all,
    handle_cfg_edit_cancel,
    handle_cfg_branch,
    show_new_grid_strike_form,
    show_new_pmm_mister_form,
    show_config_form,
    handle_set_field,
    handle_toggle_side,
    handle_toggle_position_mode,
    handle_cycle_order_type,
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
    # Progressive deploy flow
    handle_deploy_progressive_input,
    handle_deploy_use_default,
    handle_deploy_skip_field,
    handle_deploy_prev_field,
    handle_deploy_edit_field,
    # Streamlined deploy flow
    show_deploy_config_step,
    handle_select_credentials,
    handle_select_image,
    handle_select_instance_name,
    process_instance_name_input,
    handle_deploy_confirm,
    handle_deploy_custom_name,
    process_deploy_custom_name_input,
    # Progressive Grid Strike wizard
    handle_gs_wizard_connector,
    handle_gs_wizard_pair,
    handle_gs_wizard_side,
    handle_gs_wizard_leverage,
    handle_gs_wizard_amount,
    handle_gs_accept_prices,
    handle_gs_back_to_prices,
    handle_gs_back_to_connector,
    handle_gs_back_to_pair,
    handle_gs_back_to_side,
    handle_gs_back_to_leverage,
    handle_gs_back_to_amount,
    handle_gs_interval_change,
    handle_gs_wizard_take_profit,
    handle_gs_edit_id,
    handle_gs_edit_keep,
    handle_gs_edit_tp,
    handle_gs_edit_act,
    handle_gs_edit_max_orders,
    handle_gs_edit_batch,
    handle_gs_edit_min_amt,
    handle_gs_edit_spread,
    handle_gs_save,
    handle_gs_review_back,
    handle_gs_edit_price,
    process_gs_wizard_input,
    handle_gs_pair_select,
    # PMM Mister wizard
    handle_pmm_wizard_connector,
    handle_pmm_wizard_pair,
    handle_pmm_pair_select,
    handle_pmm_wizard_leverage,
    handle_pmm_wizard_allocation,
    handle_pmm_wizard_amount,
    handle_pmm_wizard_spreads,
    handle_pmm_wizard_tp,
    handle_pmm_back,
    handle_pmm_save,
    handle_pmm_review_back,
    handle_pmm_edit_id,
    handle_pmm_edit_field,
    handle_pmm_set_field,
    handle_pmm_edit_advanced,
    handle_pmm_adv_setting,
    process_pmm_wizard_input,
    # Custom config upload
    show_upload_config_prompt,
    handle_upload_cancel,
    handle_config_file_upload,
)
# Archived bots handlers
from .archived import (
    show_archived_menu,
    show_archived_detail,
    show_timeline_chart,
    show_bot_chart,
    handle_generate_report,
    handle_archived_refresh,
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
    msg = update.message or (update.callback_query.message if update.callback_query else None)
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
            error_message = format_error_message(f"Failed to fetch bot status: {str(e)}")
            await msg.reply_text(error_message, parse_mode="MarkdownV2")
        return

    # Show the interactive menu
    await show_bots_menu(update, context)


@restricted
@hummingbot_api_required
async def new_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /new_bot command - Show controller configs menu for creating new bots"""
    clear_all_input_states(context)
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        await msg.reply_chat_action("typing")
    await show_controller_configs_menu(update, context)


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

        elif main_action == "configs_page":
            if len(action_parts) > 1:
                page = int(action_parts[1])
                await handle_configs_page(update, context, page)

        elif main_action == "list_configs":
            await show_configs_list(update, context)

        # Unified configs menu with multi-select
        elif main_action == "cfg_select_type":
            await show_type_selector(update, context)

        elif main_action == "cfg_type":
            if len(action_parts) > 1:
                controller_type = action_parts[1]
                await show_configs_by_type(update, context, controller_type)

        elif main_action == "cfg_toggle":
            if len(action_parts) > 1:
                config_id = action_parts[1]
                await handle_cfg_toggle(update, context, config_id)

        elif main_action == "cfg_page":
            if len(action_parts) > 1:
                page = int(action_parts[1])
                await handle_cfg_page(update, context, page)

        elif main_action == "cfg_clear_selection":
            await handle_cfg_clear_selection(update, context)

        elif main_action == "cfg_deploy":
            await handle_cfg_deploy(update, context)

        elif main_action == "cfg_delete_confirm":
            await handle_cfg_delete_confirm(update, context)

        elif main_action == "cfg_delete_execute":
            await handle_cfg_delete_execute(update, context)

        # Edit loop handlers
        elif main_action == "cfg_edit_loop":
            await handle_cfg_edit_loop(update, context)

        elif main_action == "cfg_edit_form":
            await show_cfg_edit_form(update, context)

        elif main_action == "cfg_edit_field":
            if len(action_parts) > 1:
                field_name = action_parts[1]
                await handle_cfg_edit_field(update, context, field_name)

        elif main_action == "cfg_edit_prev":
            await handle_cfg_edit_prev(update, context)

        elif main_action == "cfg_edit_next":
            await handle_cfg_edit_next(update, context)

        elif main_action == "cfg_edit_save":
            await handle_cfg_edit_save(update, context)

        elif main_action == "cfg_edit_save_all":
            await handle_cfg_edit_save_all(update, context)

        elif main_action == "cfg_edit_cancel":
            await handle_cfg_edit_cancel(update, context)

        elif main_action == "cfg_branch":
            await handle_cfg_branch(update, context)

        # Custom config upload
        elif main_action == "upload_config":
            await show_upload_config_prompt(update, context)

        elif main_action == "upload_cancel":
            await handle_upload_cancel(update, context)

        elif main_action == "noop":
            pass  # Do nothing - used for pagination display button

        elif main_action == "new_grid_strike":
            await show_new_grid_strike_form(update, context)

        elif main_action == "new_pmm_mister":
            await show_new_pmm_mister_form(update, context)

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

        elif main_action == "toggle_position_mode":
            await handle_toggle_position_mode(update, context)

        elif main_action == "cycle_order_type":
            if len(action_parts) > 1:
                order_type_key = action_parts[1]  # 'open' or 'tp'
                await handle_cycle_order_type(update, context, order_type_key)

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

        # Progressive deploy flow
        elif main_action == "deploy_use_default":
            if len(action_parts) > 1:
                field_name = action_parts[1]
                await handle_deploy_use_default(update, context, field_name)

        elif main_action == "deploy_skip_field":
            await handle_deploy_skip_field(update, context)

        elif main_action == "deploy_prev_field":
            await handle_deploy_prev_field(update, context)

        elif main_action == "deploy_edit":
            if len(action_parts) > 1:
                field_name = action_parts[1]
                await handle_deploy_edit_field(update, context, field_name)

        # Streamlined deploy flow
        elif main_action == "deploy_config":
            await show_deploy_config_step(update, context)

        elif main_action == "select_creds":
            if len(action_parts) > 1:
                creds = action_parts[1]
                await handle_select_credentials(update, context, creds)

        elif main_action == "select_image":
            if len(action_parts) > 1:
                # Rejoin parts to preserve colons in image tag (e.g., "hummingbot:development")
                image = ":".join(action_parts[1:])
                await handle_select_image(update, context, image)

        elif main_action == "select_name":
            if len(action_parts) > 1:
                name = action_parts[1]
                await handle_select_instance_name(update, context, name)

        elif main_action == "deploy_confirm":
            await handle_deploy_confirm(update, context)

        elif main_action == "deploy_custom_name":
            await handle_deploy_custom_name(update, context)

        # Progressive Grid Strike wizard
        elif main_action == "gs_connector":
            if len(action_parts) > 1:
                connector = action_parts[1]
                await handle_gs_wizard_connector(update, context, connector)

        elif main_action == "gs_pair":
            if len(action_parts) > 1:
                pair = action_parts[1]
                await handle_gs_wizard_pair(update, context, pair)

        elif main_action == "gs_pair_select":
            if len(action_parts) > 1:
                pair = action_parts[1]
                await handle_gs_pair_select(update, context, pair)

        elif main_action == "gs_side":
            if len(action_parts) > 1:
                side_str = action_parts[1]
                await handle_gs_wizard_side(update, context, side_str)

        elif main_action == "gs_leverage":
            if len(action_parts) > 1:
                leverage = int(action_parts[1])
                await handle_gs_wizard_leverage(update, context, leverage)

        elif main_action == "gs_amount":
            if len(action_parts) > 1:
                amount = float(action_parts[1])
                await handle_gs_wizard_amount(update, context, amount)

        elif main_action == "gs_accept_prices":
            await handle_gs_accept_prices(update, context)

        elif main_action == "gs_back_to_prices":
            await handle_gs_back_to_prices(update, context)

        elif main_action == "gs_back_to_connector":
            await handle_gs_back_to_connector(update, context)

        elif main_action == "gs_back_to_pair":
            await handle_gs_back_to_pair(update, context)

        elif main_action == "gs_back_to_side":
            await handle_gs_back_to_side(update, context)

        elif main_action == "gs_back_to_leverage":
            await handle_gs_back_to_leverage(update, context)

        elif main_action == "gs_back_to_amount":
            await handle_gs_back_to_amount(update, context)

        elif main_action == "gs_interval":
            if len(action_parts) > 1:
                interval = action_parts[1]
                await handle_gs_interval_change(update, context, interval)

        elif main_action == "gs_edit_price":
            if len(action_parts) > 1:
                price_type = action_parts[1]
                await handle_gs_edit_price(update, context, price_type)

        elif main_action == "gs_tp":
            if len(action_parts) > 1:
                tp = float(action_parts[1])
                await handle_gs_wizard_take_profit(update, context, tp)

        elif main_action == "gs_edit_id":
            await handle_gs_edit_id(update, context)

        elif main_action == "gs_edit_keep":
            await handle_gs_edit_keep(update, context)

        elif main_action == "gs_edit_tp":
            await handle_gs_edit_tp(update, context)

        elif main_action == "gs_edit_act":
            await handle_gs_edit_act(update, context)

        elif main_action == "gs_edit_max_orders":
            await handle_gs_edit_max_orders(update, context)

        elif main_action == "gs_edit_batch":
            await handle_gs_edit_batch(update, context)

        elif main_action == "gs_edit_min_amt":
            await handle_gs_edit_min_amt(update, context)

        elif main_action == "gs_edit_spread":
            await handle_gs_edit_spread(update, context)

        elif main_action == "gs_save":
            await handle_gs_save(update, context)

        elif main_action == "gs_review_back":
            await handle_gs_review_back(update, context)

        # PMM Mister wizard
        elif main_action == "pmm_connector":
            if len(action_parts) > 1:
                connector = action_parts[1]
                await handle_pmm_wizard_connector(update, context, connector)

        elif main_action == "pmm_pair":
            if len(action_parts) > 1:
                pair = action_parts[1]
                await handle_pmm_wizard_pair(update, context, pair)

        elif main_action == "pmm_pair_select":
            if len(action_parts) > 1:
                pair = action_parts[1]
                await handle_pmm_pair_select(update, context, pair)

        elif main_action == "pmm_leverage":
            if len(action_parts) > 1:
                leverage = int(action_parts[1])
                await handle_pmm_wizard_leverage(update, context, leverage)

        elif main_action == "pmm_alloc":
            if len(action_parts) > 1:
                allocation = float(action_parts[1])
                await handle_pmm_wizard_allocation(update, context, allocation)

        elif main_action == "pmm_amount":
            if len(action_parts) > 1:
                amount = float(action_parts[1])
                await handle_pmm_wizard_amount(update, context, amount)

        elif main_action == "pmm_spreads":
            if len(action_parts) > 1:
                spreads = action_parts[1]
                await handle_pmm_wizard_spreads(update, context, spreads)

        elif main_action == "pmm_tp":
            if len(action_parts) > 1:
                tp = float(action_parts[1])
                await handle_pmm_wizard_tp(update, context, tp)

        elif main_action == "pmm_back":
            if len(action_parts) > 1:
                target = action_parts[1]
                await handle_pmm_back(update, context, target)

        elif main_action == "pmm_save":
            await handle_pmm_save(update, context)

        elif main_action == "pmm_review_back":
            await handle_pmm_review_back(update, context)

        elif main_action == "pmm_edit_id":
            await handle_pmm_edit_id(update, context)

        elif main_action == "pmm_edit":
            if len(action_parts) > 1:
                field = action_parts[1]
                await handle_pmm_edit_field(update, context, field)

        elif main_action == "pmm_set":
            if len(action_parts) > 2:
                field = action_parts[1]
                value = action_parts[2]
                await handle_pmm_set_field(update, context, field, value)

        elif main_action == "pmm_edit_advanced":
            await handle_pmm_edit_advanced(update, context)

        elif main_action == "pmm_adv":
            if len(action_parts) > 1:
                setting = action_parts[1]
                await handle_pmm_adv_setting(update, context, setting)

        # Bot detail
        elif main_action == "bot_detail":
            if len(action_parts) > 1:
                bot_name = action_parts[1]
                await show_bot_detail(update, context, bot_name)

        # Controller detail (by index, uses context)
        elif main_action == "ctrl_idx":
            if len(action_parts) > 1:
                idx = int(action_parts[1])
                await show_controller_detail(update, context, idx)

        # Controller chart & edit
        elif main_action == "ctrl_chart":
            await show_controller_chart(update, context)

        elif main_action == "ctrl_edit":
            await show_controller_edit(update, context)

        elif main_action == "ctrl_set":
            if len(action_parts) > 1:
                field_name = action_parts[1]
                await handle_controller_set_field(update, context, field_name)

        elif main_action == "ctrl_confirm_set":
            if len(action_parts) > 2:
                field_name = action_parts[1]
                value = action_parts[2]
                await handle_controller_confirm_set(update, context, field_name, value)

        # Stop controller (uses context)
        elif main_action == "stop_ctrl":
            await handle_stop_controller(update, context)

        elif main_action == "confirm_stop_ctrl":
            await handle_confirm_stop_controller(update, context)

        # Start controller (uses context)
        elif main_action == "start_ctrl":
            await handle_start_controller(update, context)

        elif main_action == "confirm_start_ctrl":
            await handle_confirm_start_controller(update, context)

        # Clone controller (PMM Mister only)
        elif main_action == "clone_ctrl":
            await handle_clone_controller(update, context)

        # Quick stop/start controller (from bot detail view)
        elif main_action == "stop_ctrl_quick":
            if len(action_parts) > 1:
                idx = int(action_parts[1])
                await handle_quick_stop_controller(update, context, idx)

        elif main_action == "start_ctrl_quick":
            if len(action_parts) > 1:
                idx = int(action_parts[1])
                await handle_quick_start_controller(update, context, idx)

        # Stop bot (uses context)
        elif main_action == "stop_bot":
            await handle_stop_bot(update, context)

        elif main_action == "confirm_stop_bot":
            await handle_confirm_stop_bot(update, context)

        # View logs (uses context)
        elif main_action == "view_logs":
            await show_bot_logs(update, context)

        # Navigation
        elif main_action == "back_to_bot":
            await handle_back_to_bot(update, context)

        elif main_action == "refresh_bot":
            await handle_refresh_bot(update, context)

        elif main_action == "refresh_ctrl":
            if len(action_parts) > 1:
                idx = int(action_parts[1])
                await handle_refresh_controller(update, context, idx)

        # Archived bots handlers
        elif main_action == "archived":
            await show_archived_menu(update, context)

        elif main_action == "archived_page":
            if len(action_parts) > 1:
                page = int(action_parts[1])
                await show_archived_menu(update, context, page)

        elif main_action == "archived_select":
            if len(action_parts) > 1:
                db_index = int(action_parts[1])
                await show_archived_detail(update, context, db_index)

        elif main_action == "archived_timeline":
            await show_timeline_chart(update, context)

        elif main_action == "archived_chart":
            if len(action_parts) > 1:
                db_index = int(action_parts[1])
                await show_bot_chart(update, context, db_index)

        elif main_action == "archived_report":
            if len(action_parts) > 1:
                db_index = int(action_parts[1])
                await handle_generate_report(update, context, db_index)

        elif main_action == "archived_refresh":
            await handle_archived_refresh(update, context)

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


@restricted
async def bots_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document uploads for bots module (e.g., config file uploads)"""
    # Only process if we're expecting a config upload
    if context.user_data.get("bots_state") == "awaiting_config_upload":
        await handle_config_file_upload(update, context)


def get_bots_document_handler():
    """Get the document handler for bots module"""
    return MessageHandler(
        filters.Document.ALL,
        bots_document_handler
    )


__all__ = [
    'bots_command',
    'bots_callback_handler',
    'bots_message_handler',
    'bots_document_handler',
    'get_bots_callback_handler',
    'get_bots_message_handler',
    'get_bots_document_handler',
]
