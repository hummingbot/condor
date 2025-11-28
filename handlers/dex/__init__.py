"""
DEX Trading module - Decentralized Exchange trading via Gateway

Supports:
- DEX Swaps (Jupiter, 0x)
- CLMM Pools (Meteora, Raydium, Uniswap)
- CLMM Positions management
- Quick trading with saved parameters

Structure:
- menu.py: Main DEX menu and help
- swap_quote.py: Quote functionality
- swap_execute.py: Swap execution and quick swap
- swap_history.py: Swap history and status
- pools.py: Pool and position management
- _shared.py: Shared utilities
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from utils.auth import restricted
from handlers import clear_all_input_states

# Import submodule handlers
from .menu import show_dex_menu, handle_close, handle_refresh, cancel_dex_loading_task
from .swap_quote import (
    handle_swap_quote,
    show_swap_quote_menu,
    handle_quote_toggle_side,
    handle_quote_set_connector,
    handle_quote_set_network,
    handle_quote_set_pair,
    handle_quote_set_amount,
    handle_quote_set_slippage,
    handle_quote_get_confirm,
    process_swap_quote,
    process_quote_set_connector,
    process_quote_set_network,
    process_quote_set_pair,
    process_quote_set_amount,
    process_quote_set_slippage,
)
from .swap_execute import (
    handle_swap_execute,
    show_swap_execute_menu,
    handle_swap_toggle_side,
    handle_swap_set_connector,
    handle_swap_connector_select,
    handle_swap_set_network,
    handle_swap_network_select,
    handle_swap_set_pair,
    handle_swap_set_amount,
    handle_swap_set_slippage,
    handle_swap_execute_confirm,
    handle_quick_swap,
    process_quick_swap,
    process_swap_execute,
    process_swap_set_connector,
    process_swap_set_network,
    process_swap_set_pair,
    process_swap_set_amount,
    process_swap_set_slippage,
)
from .swap_history import (
    handle_swap_status,
    handle_swap_search,
    process_swap_status,
)
from .pools import (
    handle_pool_info,
    handle_pool_list,
    handle_pool_select,
    handle_pool_list_back,
    handle_pool_detail_refresh,
    handle_plot_liquidity,
    handle_manage_positions,
    handle_pos_view,
    handle_pos_collect_fees,
    handle_pos_close_confirm,
    handle_pos_close_execute,
    handle_position_list,
    handle_add_position,
    show_add_position_menu,
    handle_pos_set_connector,
    handle_pos_set_network,
    handle_pos_set_pool,
    handle_pos_set_lower,
    handle_pos_set_upper,
    handle_pos_set_base,
    handle_pos_set_quote,
    handle_pos_add_confirm,
    handle_pos_use_max_range,
    handle_pos_help,
    handle_pos_toggle_strategy,
    handle_pos_refresh,
    process_pool_info,
    process_pool_list,
    process_position_list,
    process_add_position,
    process_pos_set_connector,
    process_pos_set_network,
    process_pos_set_pool,
    process_pos_set_lower,
    process_pos_set_upper,
    process_pos_set_base,
    process_pos_set_quote,
)

logger = logging.getLogger(__name__)


# ============================================
# MAIN DEX TRADING COMMAND
# ============================================

@restricted
async def dex_trading_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /dex_trading command - DEX trading interface

    Usage:
        /dex_trading - Show DEX trading menu
    """
    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    # Get the appropriate message object for replies
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        logger.error("No message object available for dex_trading_command")
        return

    await msg.reply_chat_action("typing")
    await show_dex_menu(update, context)


# ============================================
# CALLBACK HANDLER
# ============================================

@restricted
async def dex_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks - Routes to appropriate sub-module"""
    query = update.callback_query
    await query.answer()

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        # Cancel any pending menu loading task when navigating to a different action
        # (show_dex_menu will cancel it internally anyway, so skip for main_menu)
        if action != "main_menu":
            cancel_dex_loading_task(context)

        # Only show typing for slow operations that need network calls
        slow_actions = {"main_menu", "swap_execute_confirm", "pool_info", "pool_list",
                        "manage_positions", "pos_add_confirm", "pos_close_exec"}
        if action in slow_actions:
            await query.message.reply_chat_action("typing")

        # Menu
        if action == "main_menu":
            await show_dex_menu(update, context)

        # Quote handlers
        elif action == "swap_quote":
            await handle_swap_quote(update, context)
        elif action == "quote_toggle_side":
            await handle_quote_toggle_side(update, context)
        elif action == "quote_set_connector":
            await handle_quote_set_connector(update, context)
        elif action == "quote_set_network":
            await handle_quote_set_network(update, context)
        elif action == "quote_set_pair":
            await handle_quote_set_pair(update, context)
        elif action == "quote_set_amount":
            await handle_quote_set_amount(update, context)
        elif action == "quote_set_slippage":
            await handle_quote_set_slippage(update, context)
        elif action == "quote_get_confirm":
            await handle_quote_get_confirm(update, context)

        # Execute handlers
        elif action == "swap_execute":
            await handle_swap_execute(update, context)
        elif action == "swap_toggle_side":
            await handle_swap_toggle_side(update, context)
        elif action == "swap_set_connector":
            await handle_swap_set_connector(update, context)
        elif action.startswith("swap_connector_"):
            connector_name = action.replace("swap_connector_", "")
            await handle_swap_connector_select(update, context, connector_name)
        elif action == "swap_set_network":
            await handle_swap_set_network(update, context)
        elif action.startswith("swap_network_"):
            network_id = action.replace("swap_network_", "")
            await handle_swap_network_select(update, context, network_id)
        elif action == "swap_set_pair":
            await handle_swap_set_pair(update, context)
        elif action == "swap_set_amount":
            await handle_swap_set_amount(update, context)
        elif action == "swap_set_slippage":
            await handle_swap_set_slippage(update, context)
        elif action == "swap_execute_confirm":
            await handle_swap_execute_confirm(update, context)

        # Quick swap
        elif action == "quick_swap":
            await handle_quick_swap(update, context)

        # History/Status handlers
        elif action == "swap_status":
            await handle_swap_status(update, context)
        elif action == "swap_search":
            await handle_swap_search(update, context)

        # Pool handlers
        elif action == "pool_info":
            await handle_pool_info(update, context)
        elif action == "pool_list":
            await handle_pool_list(update, context)
        elif action.startswith("pool_select:"):
            pool_index = int(action.split(":")[1])
            await handle_pool_select(update, context, pool_index)
        elif action == "pool_list_back":
            await handle_pool_list_back(update, context)
        elif action == "pool_detail_refresh":
            await handle_pool_detail_refresh(update, context)
        elif action.startswith("plot_liquidity:"):
            percentile = int(action.split(":")[1])
            await handle_plot_liquidity(update, context, percentile)

        # Manage positions (unified view)
        elif action == "manage_positions":
            await handle_manage_positions(update, context)
        elif action.startswith("pos_view:"):
            pos_index = action.split(":")[1]
            await handle_pos_view(update, context, pos_index)
        elif action.startswith("pos_collect:"):
            pos_index = action.split(":")[1]
            await handle_pos_collect_fees(update, context, pos_index)
        elif action.startswith("pos_close:"):
            pos_index = action.split(":")[1]
            await handle_pos_close_confirm(update, context, pos_index)
        elif action.startswith("pos_close_exec:"):
            pos_index = action.split(":")[1]
            await handle_pos_close_execute(update, context, pos_index)
        elif action == "position_list":
            await handle_position_list(update, context)

        # Add position handlers
        elif action == "add_position":
            await handle_add_position(update, context)
        elif action == "add_position_from_pool":
            # Show loading indicator
            await query.answer("Loading position form...")
            # Pre-fill add position with selected pool
            selected_pool = context.user_data.get("selected_pool", {})
            if selected_pool:
                pool_address = selected_pool.get('pool_address', selected_pool.get('address', ''))
                context.user_data["add_position_params"] = {
                    "connector": selected_pool.get('connector', 'meteora'),
                    "network": "solana-mainnet-beta",
                    "pool_address": pool_address,
                    "lower_price": "",
                    "upper_price": "",
                    "amount_base": "10%",  # Default to 10% of balance
                    "amount_quote": "10%",  # Default to 10% of balance
                    "strategy_type": "0",  # Default strategy type (Spot)
                }
            await show_add_position_menu(update, context)
        elif action.startswith("copy_pool:"):
            # Show pool address for copying
            selected_pool = context.user_data.get("selected_pool", {})
            pool_address = selected_pool.get('pool_address', selected_pool.get('address', 'N/A'))
            await query.answer(f"Address: {pool_address[:40]}...", show_alert=True)
        elif action == "pos_set_connector":
            await handle_pos_set_connector(update, context)
        elif action == "pos_set_network":
            await handle_pos_set_network(update, context)
        elif action == "pos_set_pool":
            await handle_pos_set_pool(update, context)
        elif action == "pos_set_lower":
            await handle_pos_set_lower(update, context)
        elif action == "pos_set_upper":
            await handle_pos_set_upper(update, context)
        elif action == "pos_set_base":
            await handle_pos_set_base(update, context)
        elif action == "pos_set_quote":
            await handle_pos_set_quote(update, context)
        elif action == "pos_add_confirm":
            await handle_pos_add_confirm(update, context)
        elif action == "pos_use_max_range":
            await handle_pos_use_max_range(update, context)
        elif action == "pos_help":
            await handle_pos_help(update, context)
        elif action == "pos_toggle_strategy":
            await handle_pos_toggle_strategy(update, context)
        elif action == "pos_refresh":
            await handle_pos_refresh(update, context)

        # Refresh data
        elif action == "refresh":
            await handle_refresh(update, context)

        # Close menu
        elif action == "close":
            await handle_close(update, context)

        else:
            await query.message.reply_text(f"Unknown action: {action}")

    except Exception as e:
        # Ignore "message is not modified" errors - they're harmless
        if "not modified" in str(e).lower():
            logger.debug(f"Message not modified (ignored): {e}")
            return

        logger.error(f"Error in DEX callback handler: {e}", exc_info=True)
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
async def dex_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user text input - Routes to appropriate processor"""
    dex_state = context.user_data.get("dex_state")

    if not dex_state:
        return

    user_input = update.message.text.strip()
    logger.info(f"DEX message handler - state: {dex_state}, input: {user_input}")

    try:
        # Only remove state for operations that complete (not parameter setting)
        if dex_state in ["quick_swap", "swap_quote", "swap_execute", "swap_status", "pool_info", "pool_list", "position_list", "add_position"]:
            context.user_data.pop("dex_state", None)

        # Quick swap
        if dex_state == "quick_swap":
            await process_quick_swap(update, context, user_input)

        # Quote handlers
        elif dex_state == "swap_quote":
            await process_swap_quote(update, context, user_input)
        elif dex_state == "quote_set_connector":
            await process_quote_set_connector(update, context, user_input)
        elif dex_state == "quote_set_network":
            await process_quote_set_network(update, context, user_input)
        elif dex_state == "quote_set_pair":
            await process_quote_set_pair(update, context, user_input)
        elif dex_state == "quote_set_amount":
            await process_quote_set_amount(update, context, user_input)
        elif dex_state == "quote_set_slippage":
            await process_quote_set_slippage(update, context, user_input)

        # Execute handlers
        elif dex_state == "swap_execute":
            await process_swap_execute(update, context, user_input)
        elif dex_state == "swap_set_connector":
            await process_swap_set_connector(update, context, user_input)
        elif dex_state == "swap_set_network":
            await process_swap_set_network(update, context, user_input)
        elif dex_state == "swap_set_pair":
            await process_swap_set_pair(update, context, user_input)
        elif dex_state == "swap_set_amount":
            await process_swap_set_amount(update, context, user_input)
        elif dex_state == "swap_set_slippage":
            await process_swap_set_slippage(update, context, user_input)

        # Status handler
        elif dex_state == "swap_status":
            await process_swap_status(update, context, user_input)

        # Pool handlers
        elif dex_state == "pool_info":
            await process_pool_info(update, context, user_input)
        elif dex_state == "pool_list":
            await process_pool_list(update, context, user_input)
        elif dex_state == "position_list":
            await process_position_list(update, context, user_input)

        # Add position handlers
        elif dex_state == "add_position":
            await process_add_position(update, context, user_input)
        elif dex_state == "pos_set_connector":
            await process_pos_set_connector(update, context, user_input)
        elif dex_state == "pos_set_network":
            await process_pos_set_network(update, context, user_input)
        elif dex_state == "pos_set_pool":
            await process_pos_set_pool(update, context, user_input)
        elif dex_state == "pos_set_lower":
            await process_pos_set_lower(update, context, user_input)
        elif dex_state == "pos_set_upper":
            await process_pos_set_upper(update, context, user_input)
        elif dex_state == "pos_set_base":
            await process_pos_set_base(update, context, user_input)
        elif dex_state == "pos_set_quote":
            await process_pos_set_quote(update, context, user_input)

        else:
            await update.message.reply_text(f"Unknown state: {dex_state}")

    except Exception as e:
        logger.error(f"Error processing DEX input: {e}", exc_info=True)
        from utils.telegram_formatters import format_error_message
        error_message = format_error_message(f"Failed to process input: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# HANDLER FACTORIES
# ============================================

def get_dex_callback_handler():
    """Get the callback query handler for DEX menu"""
    return CallbackQueryHandler(
        dex_callback_handler,
        pattern="^dex:"
    )


def get_dex_message_handler():
    """Returns the message handler"""
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        dex_message_handler
    )


__all__ = [
    'dex_trading_command',
    'dex_callback_handler',
    'dex_message_handler',
    'get_dex_callback_handler',
    'get_dex_message_handler',
]
