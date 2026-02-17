"""
DEX Callback and Message Router

Central routing for all DEX-related callback queries and text messages.
Dispatches to appropriate handlers based on action patterns.
"""

import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from utils.auth import restricted
from utils.telegram_formatters import format_error_message

from .geckoterminal import (
    handle_back_to_list,
    handle_copy_address,
    handle_gecko_add_liquidity,
    handle_gecko_add_tokens,
    handle_gecko_networks,
    handle_gecko_new,
    handle_gecko_pool_tf,
    handle_gecko_refresh,
    handle_gecko_restart_gateway,
    handle_gecko_search,
    handle_gecko_search_network,
    handle_gecko_search_set_network,
    handle_gecko_select_network,
    handle_gecko_set_network,
    handle_gecko_show_pools,
    handle_gecko_swap,
    handle_gecko_toggle_view,
    handle_gecko_token_add,
    handle_gecko_token_info,
    handle_gecko_token_search,
    handle_gecko_top,
    handle_gecko_trending,
    process_gecko_search,
    show_gecko_charts_menu,
    show_gecko_combined,
    show_gecko_explore_menu,
    show_gecko_info,
    show_gecko_liquidity,
    show_network_menu,
    show_new_pools,
    show_ohlcv_chart,
    show_pool_detail,
    show_recent_trades,
    show_top_pools,
    show_trending_pools,
)
from .liquidity import (
    handle_liquidity,
    handle_lp_collect_all,
    handle_lp_hist_clear,
    handle_lp_hist_filter_connector,
    handle_lp_hist_filter_pair,
    handle_lp_hist_filter_status,
    handle_lp_hist_page,
    handle_lp_hist_set_filter,
    handle_lp_history,
    handle_lp_pos_view,
    handle_lp_refresh,
)
from .lp_monitor_handlers import (
    handle_lpm_cancel_countdown,
    handle_lpm_collect_fees,
    handle_lpm_detail,
    handle_lpm_dismiss,
    handle_lpm_navigation,
    handle_lpm_oor_navigation,
    handle_lpm_rebalance,
    handle_lpm_rebalance_execute,
    handle_lpm_skip,
)
from .menu import cancel_dex_loading_task, handle_close, handle_refresh
from .pools import (
    handle_add_position,
    handle_add_to_gateway,
    handle_manage_positions,
    handle_plot_liquidity,
    handle_pool_combined_chart,
    handle_pool_detail_refresh,
    handle_pool_info,
    handle_pool_list,
    handle_pool_list_back,
    handle_pool_ohlcv,
    handle_pool_select,
    handle_pos_add_confirm,
    handle_pos_close_confirm,
    handle_pos_close_execute,
    handle_pos_collect_fees,
    handle_pos_help,
    handle_pos_refresh,
    handle_pos_set_base,
    handle_pos_set_connector,
    handle_pos_set_lower,
    handle_pos_set_network,
    handle_pos_set_pool,
    handle_pos_set_quote,
    handle_pos_set_upper,
    handle_pos_toggle_strategy,
    handle_pos_use_max_range,
    handle_pos_view,
    handle_pos_view_pool,
    handle_position_list,
    process_add_position,
    process_pool_info,
    process_pool_list,
    process_pos_set_base,
    process_pos_set_connector,
    process_pos_set_lower,
    process_pos_set_network,
    process_pos_set_pool,
    process_pos_set_quote,
    process_pos_set_upper,
    process_position_list,
    show_add_position_menu,
)

# Import all handler modules
from .swap import (
    handle_swap,
    handle_swap_connector_select,
    handle_swap_execute_confirm,
    handle_swap_get_quote,
    handle_swap_hist_clear,
    handle_swap_hist_filter_connector,
    handle_swap_hist_filter_pair,
    handle_swap_hist_filter_status,
    handle_swap_hist_page,
    handle_swap_hist_set_filter,
    handle_swap_history,
    handle_swap_network_select,
    handle_swap_refresh,
    handle_swap_set_amount,
    handle_swap_set_connector,
    handle_swap_set_network,
    handle_swap_set_pair,
    handle_swap_set_slippage,
    handle_swap_status,
    handle_swap_toggle_side,
    process_swap,
    process_swap_set_amount,
    process_swap_set_pair,
    process_swap_set_slippage,
    process_swap_status,
)

logger = logging.getLogger(__name__)

# Type alias for handler functions
HandlerFunc = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


# ============================================
# SLOW ACTIONS (require typing indicator)
# ============================================

SLOW_ACTIONS = frozenset(
    {
        "main_menu",
        "swap",
        "swap_refresh",
        "swap_get_quote",
        "swap_execute_confirm",
        "swap_history",
        "swap_hist_clear",
        "swap_hist_filter_pair",
        "swap_hist_filter_connector",
        "swap_hist_filter_status",
        "swap_hist_page_prev",
        "swap_hist_page_next",
        "liquidity",
        "lp_refresh",
        "lp_history",
        "lp_collect_all",
        "lp_hist_clear",
        "lp_hist_filter_pair",
        "lp_hist_filter_connector",
        "lp_hist_filter_status",
        "lp_hist_page_prev",
        "lp_hist_page_next",
        "pool_info",
        "pool_list",
        "manage_positions",
        "pos_add_confirm",
        "pos_close_exec",
        "add_to_gateway",
        "pool_detail_refresh",
        "gecko_networks",
        "gecko_trades",
        "gecko_show_pools",
        "gecko_refresh",
        "gecko_token_search",
        "gecko_token_add",
        "gecko_explore",
        "gecko_swap",
        "gecko_info",
        "gecko_add_tokens",
        "gecko_restart_gateway",
    }
)

SLOW_PREFIXES = (
    "gecko_trending_",
    "gecko_top_",
    "gecko_new_",
    "gecko_pool:",
    "gecko_ohlcv:",
    "gecko_pool_tf:",
    "gecko_token:",
    "swap_hist_set_",
    "lp_hist_set_",
    "lpm_nav:",
    "lpm_cancel_countdown:",
    "pos_close:",
)


def _is_slow_action(action: str) -> bool:
    """Check if an action requires a typing indicator."""
    return action in SLOW_ACTIONS or action.startswith(SLOW_PREFIXES)


# ============================================
# CALLBACK ACTION DISPATCH TABLE
# ============================================

# Simple action -> handler mapping (no parameters)
SIMPLE_ACTIONS: dict[str, HandlerFunc] = {
    # Menu
    "main_menu": handle_swap,
    "close": handle_close,
    "refresh": handle_refresh,
    "noop": lambda u, c: None,  # No-op for page indicators
    "lpm_noop": lambda u, c: None,
    # Swap
    "swap": handle_swap,
    "swap_refresh": handle_swap_refresh,
    "swap_toggle_side": handle_swap_toggle_side,
    "swap_set_connector": handle_swap_set_connector,
    "swap_set_network": handle_swap_set_network,
    "swap_set_pair": handle_swap_set_pair,
    "swap_set_amount": handle_swap_set_amount,
    "swap_set_slippage": handle_swap_set_slippage,
    "swap_get_quote": handle_swap_get_quote,
    "swap_execute_confirm": handle_swap_execute_confirm,
    "swap_history": handle_swap_history,
    "swap_status": handle_swap_status,
    "swap_hist_filter_pair": handle_swap_hist_filter_pair,
    "swap_hist_filter_connector": handle_swap_hist_filter_connector,
    "swap_hist_filter_status": handle_swap_hist_filter_status,
    "swap_hist_clear": handle_swap_hist_clear,
    # Legacy swap redirects
    "swap_quote": handle_swap,
    "swap_execute": handle_swap,
    "swap_search": handle_swap_history,
    # Liquidity
    "liquidity": handle_liquidity,
    "lp_refresh": handle_lp_refresh,
    "lp_collect_all": handle_lp_collect_all,
    "lp_history": handle_lp_history,
    "lp_hist_filter_pair": handle_lp_hist_filter_pair,
    "lp_hist_filter_connector": handle_lp_hist_filter_connector,
    "lp_hist_filter_status": handle_lp_hist_filter_status,
    "lp_hist_clear": handle_lp_hist_clear,
    # Legacy liquidity redirects
    "explore_pools": handle_lp_refresh,
    # Pool
    "pool_info": handle_pool_info,
    "pool_list": handle_pool_list,
    "pool_list_back": handle_pool_list_back,
    "pool_detail_refresh": handle_pool_detail_refresh,
    "add_to_gateway": handle_add_to_gateway,
    # Position management
    "manage_positions": handle_manage_positions,
    "position_list": handle_position_list,
    "add_position": handle_add_position,
    "pos_set_connector": handle_pos_set_connector,
    "pos_set_network": handle_pos_set_network,
    "pos_set_pool": handle_pos_set_pool,
    "pos_set_lower": handle_pos_set_lower,
    "pos_set_upper": handle_pos_set_upper,
    "pos_set_base": handle_pos_set_base,
    "pos_set_quote": handle_pos_set_quote,
    "pos_add_confirm": handle_pos_add_confirm,
    "pos_use_max_range": handle_pos_use_max_range,
    "pos_help": handle_pos_help,
    "pos_toggle_strategy": handle_pos_toggle_strategy,
    "pos_refresh": handle_pos_refresh,
    # GeckoTerminal
    "gecko_explore": show_gecko_explore_menu,
    "gecko_toggle_view": handle_gecko_toggle_view,
    "gecko_select_network": handle_gecko_select_network,
    "gecko_show_pools": handle_gecko_show_pools,
    "gecko_refresh": handle_gecko_refresh,
    "gecko_trending": handle_gecko_trending,
    "gecko_top": handle_gecko_top,
    "gecko_new": handle_gecko_new,
    "gecko_networks": handle_gecko_networks,
    "gecko_search": handle_gecko_search,
    "gecko_search_network": handle_gecko_search_network,
    "gecko_charts": show_gecko_charts_menu,
    "gecko_add_liquidity": handle_gecko_add_liquidity,
    "gecko_token_search": handle_gecko_token_search,
    "gecko_token_add": handle_gecko_token_add,
    "gecko_swap": handle_gecko_swap,
    "gecko_info": show_gecko_info,
    "gecko_liquidity": show_gecko_liquidity,
    "gecko_trades": show_recent_trades,
    "gecko_copy_addr": handle_copy_address,
    "gecko_back_to_list": handle_back_to_list,
    "gecko_add_tokens": handle_gecko_add_tokens,
    "gecko_restart_gateway": handle_gecko_restart_gateway,
}


# ============================================
# CEX SWITCH HANDLER
# ============================================


async def _handle_switch_to_cex(
    update: Update, context: ContextTypes.DEFAULT_TYPE, connector_name: str
) -> None:
    """Switch from DEX to CEX trading"""
    from handlers.cex.trade import handle_trade as cex_handle_trade
    from handlers.config.user_preferences import (
        get_clob_order_defaults,
        set_last_trade_connector,
    )

    # Clear DEX state
    context.user_data.pop("dex_state", None)
    context.user_data.pop("swap_params", None)

    # Save preference and set up CEX trade
    set_last_trade_connector(context.user_data, "cex", connector_name)
    defaults = get_clob_order_defaults(context.user_data)
    defaults["connector"] = connector_name
    context.user_data["trade_params"] = defaults

    # Route to CEX trade menu
    await cex_handle_trade(update, context)


# ============================================
# PARAMETERIZED ACTION HANDLERS
# ============================================


async def _handle_parameterized_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE, action: str
) -> bool:
    """
    Handle actions that require parameter extraction.
    Returns True if action was handled, False otherwise.
    """
    query = update.callback_query

    # Swap connector selection: swap_connector_{name}
    if action.startswith("swap_connector_"):
        connector_name = action.replace("swap_connector_", "")
        await handle_swap_connector_select(update, context, connector_name)
        return True

    # Swap network selection: swap_network_{id}
    if action.startswith("swap_network_"):
        network_id = action.replace("swap_network_", "")
        await handle_swap_network_select(update, context, network_id)
        return True

    # Switch to CEX: switch_cex_{connector}
    if action.startswith("switch_cex_"):
        connector_name = action.replace("switch_cex_", "")
        await _handle_switch_to_cex(update, context, connector_name)
        return True

    # Swap history filters
    if action.startswith("swap_hist_set_pair_"):
        value = action.replace("swap_hist_set_pair_", "")
        await handle_swap_hist_set_filter(update, context, "pair", value)
        return True
    if action.startswith("swap_hist_set_connector_"):
        value = action.replace("swap_hist_set_connector_", "")
        await handle_swap_hist_set_filter(update, context, "connector", value)
        return True
    if action.startswith("swap_hist_set_status_"):
        value = action.replace("swap_hist_set_status_", "")
        await handle_swap_hist_set_filter(update, context, "status", value)
        return True

    # Swap history pagination
    if action == "swap_hist_page_prev":
        await handle_swap_hist_page(update, context, "prev")
        return True
    if action == "swap_hist_page_next":
        await handle_swap_hist_page(update, context, "next")
        return True

    # LP history filters
    if action.startswith("lp_hist_set_pair_"):
        value = action.replace("lp_hist_set_pair_", "")
        await handle_lp_hist_set_filter(update, context, "pair", value)
        return True
    if action.startswith("lp_hist_set_connector_"):
        value = action.replace("lp_hist_set_connector_", "")
        await handle_lp_hist_set_filter(update, context, "connector", value)
        return True
    if action.startswith("lp_hist_set_status_"):
        value = action.replace("lp_hist_set_status_", "")
        await handle_lp_hist_set_filter(update, context, "status", value)
        return True

    # LP history pagination
    if action == "lp_hist_page_prev":
        await handle_lp_hist_page(update, context, "prev")
        return True
    if action == "lp_hist_page_next":
        await handle_lp_hist_page(update, context, "next")
        return True

    # LP position view: lp_pos_view:{index}
    if action.startswith("lp_pos_view:"):
        pos_index = int(action.split(":")[1])
        await handle_lp_pos_view(update, context, pos_index)
        return True

    # Pool selection: pool_select:{index}
    if action.startswith("pool_select:"):
        pool_index = int(action.split(":")[1])
        await handle_pool_select(update, context, pool_index)
        return True

    # Pool timeframe: pool_tf:{timeframe}
    if action.startswith("pool_tf:"):
        timeframe = action.split(":")[1]
        await handle_pool_detail_refresh(update, context, timeframe=timeframe)
        return True

    # Plot liquidity: plot_liquidity:{percentile}
    if action.startswith("plot_liquidity:"):
        percentile = int(action.split(":")[1])
        await handle_plot_liquidity(update, context, percentile)
        return True

    # Position view: pos_view:{index}
    if action.startswith("pos_view:"):
        pos_index = action.split(":")[1]
        await handle_pos_view(update, context, pos_index)
        return True

    # Position view with timeframe: pos_view_tf:{index}:{timeframe}
    if action.startswith("pos_view_tf:"):
        parts = action.split(":")
        pos_index = parts[1]
        timeframe = parts[2] if len(parts) > 2 else "1h"
        await handle_pos_view(update, context, pos_index, timeframe=timeframe)
        return True

    # Position view pool: pos_view_pool:{index}
    if action.startswith("pos_view_pool:"):
        pos_index = action.split(":")[1]
        await handle_pos_view_pool(update, context, pos_index)
        return True

    # Position actions
    if action.startswith("pos_collect:"):
        pos_index = action.split(":")[1]
        await handle_pos_collect_fees(update, context, pos_index)
        return True
    if action.startswith("pos_close:"):
        pos_index = action.split(":")[1]
        await handle_pos_close_confirm(update, context, pos_index)
        return True
    if action.startswith("pos_close_exec:"):
        pos_index = action.split(":")[1]
        await handle_pos_close_execute(update, context, pos_index)
        return True

    # Position timeframe in add menu: pos_tf:{timeframe}
    if action.startswith("pos_tf:"):
        timeframe = action.split(":")[1]
        await handle_pos_refresh(update, context, timeframe=timeframe)
        return True

    # Copy pool address
    if action.startswith("copy_pool:"):
        selected_pool = context.user_data.get("selected_pool", {})
        pool_address = selected_pool.get(
            "pool_address", selected_pool.get("address", "N/A")
        )
        await query.answer("Address sent below ⬇️")
        await query.message.reply_text(f"`{pool_address}`", parse_mode="Markdown")
        return True

    # Add position from pool
    if action == "add_position_from_pool":
        await query.answer("Loading position form...")
        selected_pool = context.user_data.get("selected_pool", {})
        if selected_pool:
            pool_address = selected_pool.get(
                "pool_address", selected_pool.get("address", "")
            )
            context.user_data["add_position_params"] = {
                "connector": selected_pool.get("connector", "meteora"),
                "network": "solana-mainnet-beta",
                "pool_address": pool_address,
                "lower_price": "",
                "upper_price": "",
                "amount_base": "10%",
                "amount_quote": "10%",
                "strategy_type": "0",
            }
        await show_add_position_menu(update, context)
        return True

    # GeckoTerminal handlers
    if action.startswith("gecko_set_network:"):
        network = action.split(":")[1]
        await handle_gecko_set_network(update, context, network)
        return True
    if action.startswith("gecko_trending_"):
        network = action.replace("gecko_trending_", "")
        network = None if network == "all" else network
        await show_trending_pools(update, context, network)
        return True
    if action.startswith("gecko_top_"):
        network = action.replace("gecko_top_", "")
        await show_top_pools(update, context, network)
        return True
    if action.startswith("gecko_new_"):
        network = action.replace("gecko_new_", "")
        network = None if network == "all" else network
        await show_new_pools(update, context, network)
        return True
    if action.startswith("gecko_net_"):
        network = action.replace("gecko_net_", "")
        await show_network_menu(update, context, network)
        return True
    if action.startswith("gecko_search_set_net:"):
        network = action.split(":")[1]
        await handle_gecko_search_set_network(update, context, network)
        return True
    if action.startswith("gecko_pool:"):
        pool_index = int(action.split(":")[1])
        await show_pool_detail(update, context, pool_index)
        return True
    if action.startswith("gecko_token:"):
        token_type = action.split(":")[1]
        await handle_gecko_token_info(update, context, token_type)
        return True
    if action.startswith("gecko_ohlcv:"):
        timeframe = action.split(":")[1]
        await show_ohlcv_chart(update, context, timeframe)
        return True
    if action.startswith("gecko_combined:"):
        timeframe = action.split(":")[1]
        await show_gecko_combined(update, context, timeframe)
        return True
    if action.startswith("gecko_pool_tf:"):
        timeframe = action.split(":")[1]
        await handle_gecko_pool_tf(update, context, timeframe)
        return True

    # Pool OHLCV: pool_ohlcv:{timeframe}:{currency}
    if action.startswith("pool_ohlcv:"):
        parts = action.split(":")
        timeframe = parts[1]
        currency = parts[2] if len(parts) > 2 else "usd"
        await handle_pool_ohlcv(update, context, timeframe, currency)
        return True

    # Pool combined chart: pool_combined:{timeframe}:{currency}
    if action.startswith("pool_combined:"):
        parts = action.split(":")
        timeframe = parts[1]
        currency = parts[2] if len(parts) > 2 else "usd"
        await handle_pool_combined_chart(update, context, timeframe, currency)
        return True

    # LP Monitor handlers
    if action.startswith("lpm_skip:"):
        cache_key = action.split(":")[1]
        await handle_lpm_skip(update, context, cache_key)
        return True
    if action.startswith("lpm_nav:"):
        parts = action.split(":")
        if len(parts) >= 3:
            instance_id = parts[1]
            new_index = int(parts[2])
            await handle_lpm_navigation(update, context, instance_id, new_index)
        return True
    if action.startswith("lpm_dismiss:"):
        await handle_lpm_dismiss(update, context)
        return True
    if action.startswith("lpm_detail:"):
        parts = action.split(":")
        if len(parts) >= 3:
            instance_id = parts[1]
            index = int(parts[2])
            await handle_lpm_detail(update, context, instance_id, index)
        return True
    if action.startswith("lpm_collect:"):
        cache_key = action.replace("lpm_collect:", "")
        await handle_lpm_collect_fees(update, context, cache_key)
        return True
    if action.startswith("lpm_rebalance_confirm:"):
        cache_key = action.replace("lpm_rebalance_confirm:", "")
        await handle_lpm_rebalance_execute(update, context, cache_key)
        return True
    if action.startswith("lpm_rebalance:"):
        cache_key = action.replace("lpm_rebalance:", "")
        await handle_lpm_rebalance(update, context, cache_key)
        return True
    if action.startswith("lpm_oor:"):
        parts = action.split(":")
        if len(parts) >= 3:
            instance_id = parts[1]
            index = int(parts[2])
            await handle_lpm_oor_navigation(update, context, instance_id, index)
        return True
    if action.startswith("lpm_cancel_countdown:"):
        parts = action.split(":")
        if len(parts) >= 3:
            instance_id = parts[1]
            pos_id = parts[2]
            await handle_lpm_cancel_countdown(update, context, instance_id, pos_id)
        return True

    return False


# ============================================
# MESSAGE STATE HANDLERS
# ============================================

# State -> processor function mapping
MESSAGE_STATE_HANDLERS: dict[str, Callable] = {
    # Swap states
    "swap": process_swap,
    "swap_set_pair": process_swap_set_pair,
    "swap_set_amount": process_swap_set_amount,
    "swap_set_slippage": process_swap_set_slippage,
    "swap_status": process_swap_status,
    # Pool states
    "pool_info": process_pool_info,
    "pool_list": process_pool_list,
    "position_list": process_position_list,
    # Add position states
    "add_position": process_add_position,
    "pos_set_connector": process_pos_set_connector,
    "pos_set_network": process_pos_set_network,
    "pos_set_pool": process_pos_set_pool,
    "pos_set_lower": process_pos_set_lower,
    "pos_set_upper": process_pos_set_upper,
    "pos_set_base": process_pos_set_base,
    "pos_set_quote": process_pos_set_quote,
    # GeckoTerminal
    "gecko_search": process_gecko_search,
}

# States that should be cleared after processing
CLEAR_STATE_AFTER = frozenset(
    {"swap", "swap_status", "pool_info", "pool_list", "position_list", "add_position"}
)


# ============================================
# MAIN CALLBACK HANDLER
# ============================================


@restricted
async def dex_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle inline button callbacks - Routes to appropriate sub-module."""
    query = update.callback_query
    await query.answer()

    try:
        # Parse action from callback data (format: dex:{action})
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        # Cancel any pending menu loading task when navigating away
        if action != "main_menu":
            cancel_dex_loading_task(context)

        # Show typing indicator for slow operations
        if _is_slow_action(action):
            await query.message.reply_chat_action("typing")

        # Try simple action dispatch first
        if action in SIMPLE_ACTIONS:
            handler = SIMPLE_ACTIONS[action]
            if handler is not None:
                await handler(update, context)
            return

        # Try parameterized action handlers
        if await _handle_parameterized_action(update, context, action):
            return

        # Unknown action
        await query.message.reply_text(f"Unknown action: {action}")

    except Exception as e:
        # Ignore "message is not modified" errors - they're harmless
        if "not modified" in str(e).lower():
            logger.debug(f"Message not modified (ignored): {e}")
            return

        logger.error(f"Error in DEX callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        try:
            await query.message.reply_text(error_message, parse_mode="MarkdownV2")
        except Exception as reply_error:
            logger.warning(f"Failed to send error message: {reply_error}")


# ============================================
# MAIN MESSAGE HANDLER
# ============================================


@restricted
async def dex_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle user text input - Routes to appropriate processor."""
    dex_state = context.user_data.get("dex_state")

    if not dex_state:
        return

    user_input = update.message.text.strip()
    logger.info(f"DEX message handler - state: {dex_state}, input: {user_input}")

    try:
        # Clear state for operations that complete
        if dex_state in CLEAR_STATE_AFTER:
            context.user_data.pop("dex_state", None)

        # Dispatch to appropriate handler
        handler = MESSAGE_STATE_HANDLERS.get(dex_state)
        if handler:
            await handler(update, context, user_input)
        else:
            await update.message.reply_text(f"Unknown state: {dex_state}")

    except Exception as e:
        logger.error(f"Error processing DEX input: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to process input: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# HANDLER FACTORIES
# ============================================


def get_dex_callback_handler() -> CallbackQueryHandler:
    """Get the callback query handler for DEX menu."""
    return CallbackQueryHandler(dex_callback_handler, pattern="^dex:")


def get_dex_message_handler() -> MessageHandler:
    """Returns the message handler for DEX text input."""
    return MessageHandler(filters.TEXT & ~filters.COMMAND, dex_message_handler)
