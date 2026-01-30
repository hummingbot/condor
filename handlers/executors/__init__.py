"""
Executors Handler - Deploy and manage trading executors directly via Hummingbot Backend API.

This module provides a streamlined interface for deploying executors (grid, etc.)
without going through the full controller/bot infrastructure.

Features:
- Direct executor deployment via API
- Live view of running executors with PnL
- Stop executors instantly
- Grid executor wizard with chart visualization

Commands:
- /executors - Main menu and running executors view
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers import clear_all_input_states
from utils.auth import restricted
from ._shared import clear_executors_state

logger = logging.getLogger(__name__)


# ============================================
# COMMAND HANDLER
# ============================================

@restricted
async def executors_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /executors command - show main menu

    Args:
        update: Telegram update
        context: Telegram context
    """
    clear_all_input_states(context)

    from .menu import show_executors_menu
    await show_executors_menu(update, context)


# ============================================
# CALLBACK ROUTER
# ============================================

@restricted
async def executors_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all executors: callback queries

    Routing:
        executors:menu           -> show_executors_menu()
        executors:list           -> show_running_executors()
        executors:list_prev      -> previous page
        executors:list_next      -> next page
        executors:detail:{id}    -> show_executor_detail()
        executors:stop:{id}      -> handle_stop_executor()
        executors:confirm_stop:{id} -> handle_confirm_stop_executor()
        executors:create         -> show_create_menu()
        executors:create_grid    -> start_grid_wizard()
        executors:grid_*         -> route to grid.py handlers
        executors:close          -> handle_close()

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split(":")

    if len(parts) < 2:
        return

    action = parts[1]

    # Import handlers lazily to avoid circular imports
    from .menu import (
        show_executors_menu,
        show_running_executors,
        show_executor_detail,
        handle_stop_executor,
        handle_confirm_stop_executor,
        show_create_menu,
        handle_close,
    )
    from .grid import (
        start_grid_wizard,
        show_step_1_connector,
        handle_connector_select,
        handle_pair_input,
        show_step_2_combined,
        handle_side_select,
        handle_leverage_select,
        handle_amount_select,
        handle_custom_amount,
        handle_interval_select,
        show_edit_prices,
        show_edit_settings,
        handle_deploy,
    )

    # Menu actions
    if action == "menu":
        await show_executors_menu(update, context)

    elif action == "list":
        await show_running_executors(update, context)

    elif action == "list_prev":
        page = context.user_data.get("executor_list_page", 0)
        context.user_data["executor_list_page"] = max(0, page - 1)
        await show_running_executors(update, context)

    elif action == "list_next":
        page = context.user_data.get("executor_list_page", 0)
        context.user_data["executor_list_page"] = page + 1
        await show_running_executors(update, context)

    elif action == "detail" and len(parts) >= 3:
        executor_id = parts[2]
        await show_executor_detail(update, context, executor_id)

    elif action == "stop" and len(parts) >= 3:
        executor_id = parts[2]
        await handle_stop_executor(update, context, executor_id)

    elif action == "confirm_stop" and len(parts) >= 3:
        executor_id = parts[2]
        await handle_confirm_stop_executor(update, context, executor_id)

    elif action == "create":
        await show_create_menu(update, context)

    elif action == "create_grid":
        await start_grid_wizard(update, context)

    elif action == "close":
        await handle_close(update, context)

    # Grid wizard actions
    elif action == "grid_conn" and len(parts) >= 3:
        connector = parts[2]
        await handle_connector_select(update, context, connector)

    elif action == "grid_pair" and len(parts) >= 3:
        pair = parts[2]
        await handle_pair_input(update, context, pair)

    elif action == "grid_step2":
        await show_step_2_combined(update, context)

    elif action == "grid_side" and len(parts) >= 3:
        side_str = parts[2]
        await handle_side_select(update, context, side_str)

    elif action == "grid_lev" and len(parts) >= 3:
        leverage = int(parts[2])
        await handle_leverage_select(update, context, leverage)

    elif action == "grid_amt" and len(parts) >= 3:
        amount = int(parts[2])
        await handle_amount_select(update, context, amount)

    elif action == "grid_amt_custom":
        await handle_custom_amount(update, context)

    elif action == "grid_interval" and len(parts) >= 3:
        interval = parts[2]
        await handle_interval_select(update, context, interval)

    elif action == "grid_edit_prices":
        await show_edit_prices(update, context)

    elif action == "grid_edit_settings":
        await show_edit_settings(update, context)

    elif action == "grid_deploy":
        await handle_deploy(update, context)


# ============================================
# MESSAGE HANDLER
# ============================================

async def executors_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input for executors wizard

    Returns:
        True if message was handled, False otherwise

    Args:
        update: Telegram update
        context: Telegram context
    """
    state = context.user_data.get("executors_state")

    if not state or not state.startswith("wizard"):
        return False

    text = update.message.text.strip()

    from .grid import (
        handle_pair_input,
        handle_amount_input,
        handle_prices_input,
        handle_settings_input,
    )

    if state == "wizard_pair_input":
        await handle_pair_input(update, context, text)
        return True

    elif state == "wizard_amount_input":
        await handle_amount_input(update, context, text)
        return True

    elif state == "wizard_prices_input":
        await handle_prices_input(update, context, text)
        return True

    elif state == "wizard_settings_input":
        await handle_settings_input(update, context, text)
        return True

    return False


# Exports
__all__ = [
    "executors_command",
    "executors_callback_handler",
    "executors_message_handler",
]
