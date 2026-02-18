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
async def executors_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle all executors: callback queries

    Routing:
        executors:menu           -> show_executors_menu()
        executors:list           -> show_executors_menu() (alias)
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
    from .grid import handle_connector_select as grid_handle_connector_select
    from .grid import handle_deploy as grid_handle_deploy
    from .grid import (
        handle_interval_select,
    )
    from .grid import handle_pair_input as grid_handle_pair_input
    from .grid import (
        show_step_2_combined,
        start_grid_wizard,
    )
    from .menu import (
        handle_close,
        handle_confirm_stop_executor,
        handle_stop_executor,
        show_create_menu,
        show_executor_detail,
        show_executors_menu,
    )
    from .position import handle_connector_select as pos_handle_connector_select
    from .position import handle_deploy as pos_handle_deploy
    from .position import handle_pair_input as pos_handle_pair_input
    from .position import (
        show_step_2_config,
        start_position_wizard,
    )

    # Menu actions
    if action == "menu":
        await show_executors_menu(update, context)

    elif action == "list":
        await show_executors_menu(update, context)

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

    elif action == "create_position":
        await start_position_wizard(update, context)

    elif action == "close":
        await handle_close(update, context)

    # Grid wizard actions
    elif action == "grid_conn" and len(parts) >= 3:
        connector = parts[2]
        await grid_handle_connector_select(update, context, connector)

    elif action == "grid_pair" and len(parts) >= 3:
        pair = ":".join(parts[2:])  # Rejoin for HIP3 pairs with colons
        await grid_handle_pair_input(update, context, pair)

    elif action == "grid_pair_select" and len(parts) >= 3:
        pair = ":".join(parts[2:])  # Rejoin for HIP3 pairs with colons
        await grid_handle_pair_input(update, context, pair)

    elif action == "grid_step2":
        await show_step_2_combined(update, context)

    elif action == "grid_interval" and len(parts) >= 3:
        interval = parts[2]
        await handle_interval_select(update, context, interval)

    elif action == "grid_deploy":
        await grid_handle_deploy(update, context)

    # Position wizard actions
    elif action == "pos_conn" and len(parts) >= 3:
        connector = parts[2]
        await pos_handle_connector_select(update, context, connector)

    elif action == "pos_pair" and len(parts) >= 3:
        pair = ":".join(parts[2:])  # Rejoin for HIP3 pairs with colons
        await pos_handle_pair_input(update, context, pair)

    elif action == "pos_pair_select" and len(parts) >= 3:
        pair = ":".join(parts[2:])  # Rejoin for HIP3 pairs with colons
        await pos_handle_pair_input(update, context, pair)

    elif action == "pos_step2":
        await show_step_2_config(update, context)

    elif action == "pos_deploy":
        await pos_handle_deploy(update, context)


# ============================================
# MESSAGE HANDLER
# ============================================


async def executors_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
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
    wizard_type = context.user_data.get("executor_wizard_type", "grid")

    if wizard_type == "position":
        from .position import handle_config_input as pos_config_input
        from .position import handle_pair_input as pos_pair_input

        if state == "wizard_pair_input":
            await pos_pair_input(update, context, text)
            return True
        elif state == "wizard_config_input":
            await pos_config_input(update, context, text)
            return True
    else:
        from .grid import handle_config_input as grid_config_input
        from .grid import handle_pair_input as grid_pair_input

        if state == "wizard_pair_input":
            await grid_pair_input(update, context, text)
            return True
        elif state == "wizard_config_input":
            await grid_config_input(update, context, text)
            return True

    return False


# Exports
__all__ = [
    "executors_command",
    "executors_callback_handler",
    "executors_message_handler",
]
