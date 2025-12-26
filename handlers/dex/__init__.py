"""
DEX Trading module - Decentralized Exchange trading via Gateway

Supports:
- DEX Swaps (Jupiter, 0x)
- CLMM Pools (Meteora, Raydium, Uniswap)
- CLMM Positions management
- Quick trading with saved parameters
- GeckoTerminal pool exploration with OHLCV charts

Structure:
- menu.py: Main DEX menu and help
- swap.py: Unified swap (quote, execute, history with filters/pagination)
- liquidity.py: Unified liquidity pools (balances, positions, history with filters/pagination)
- pools.py: Pool info, position management (add, close, collect fees)
- pool_data.py: Pool data fetching utilities (OHLCV, liquidity bins)
- geckoterminal.py: GeckoTerminal pool explorer with charts
- visualizations.py: Chart generation (liquidity distribution, OHLCV candlesticks)
- _shared.py: Shared utilities (caching, formatters, history filters)
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from utils.auth import restricted
from handlers import clear_all_input_states

# Import submodule handlers
from .menu import show_dex_menu, handle_close, handle_refresh, cancel_dex_loading_task
# Unified swap module
from .swap import (
    handle_swap,
    handle_swap_refresh,
    show_swap_menu,
    handle_swap_toggle_side,
    handle_swap_set_connector,
    handle_swap_connector_select,
    handle_swap_set_network,
    handle_swap_network_select,
    handle_swap_set_pair,
    handle_swap_set_amount,
    handle_swap_set_slippage,
    handle_swap_get_quote,
    handle_swap_execute_confirm,
    handle_swap_history,
    handle_swap_status,
    handle_swap_hist_filter_pair,
    handle_swap_hist_filter_connector,
    handle_swap_hist_filter_status,
    handle_swap_hist_set_filter,
    handle_swap_hist_page,
    handle_swap_hist_clear,
    process_swap,
    process_swap_set_pair,
    process_swap_set_amount,
    process_swap_set_slippage,
    process_swap_status,
)
from .pools import (
    handle_pool_info,
    handle_pool_list,
    handle_pool_select,
    handle_pool_list_back,
    handle_pool_detail_refresh,
    handle_add_to_gateway,
    handle_plot_liquidity,
    handle_pool_ohlcv,
    handle_pool_combined_chart,
    handle_manage_positions,
    handle_pos_view,
    handle_pos_view_pool,
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
from .geckoterminal import (
    show_gecko_explore_menu,
    handle_gecko_toggle_view,
    handle_gecko_select_network,
    handle_gecko_set_network,
    handle_gecko_show_pools,
    handle_gecko_refresh,
    handle_gecko_trending,
    show_trending_pools,
    handle_gecko_top,
    show_top_pools,
    handle_gecko_new,
    show_new_pools,
    handle_gecko_networks,
    show_network_menu,
    handle_gecko_search,
    handle_gecko_search_network,
    handle_gecko_search_set_network,
    process_gecko_search,
    show_pool_detail,
    show_gecko_charts_menu,
    show_ohlcv_chart,
    show_recent_trades,
    show_gecko_liquidity,
    show_gecko_combined,
    handle_copy_address,
    handle_gecko_token_info,
    handle_gecko_token_search,
    handle_gecko_token_add,
    handle_back_to_list,
    handle_gecko_add_liquidity,
    handle_gecko_swap,
    show_gecko_info,
    handle_gecko_pool_tf,
    handle_gecko_add_tokens,
    handle_gecko_restart_gateway,
)
# Unified liquidity module
from .liquidity import (
    handle_liquidity,
    show_liquidity_menu,
    handle_lp_refresh,
    handle_lp_pos_view,
    handle_lp_collect_all,
    handle_lp_history,
    handle_lp_hist_filter_pair,
    handle_lp_hist_filter_connector,
    handle_lp_hist_filter_status,
    handle_lp_hist_set_filter,
    handle_lp_hist_page,
    handle_lp_hist_clear,
)

logger = logging.getLogger(__name__)


# ============================================
# MAIN DEX COMMANDS
# ============================================

@restricted
async def swap_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /swap command - Quick market swaps via DEX routers

    Usage:
        /swap - Show swap menu for token exchanges
    """
    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    # Get the appropriate message object for replies
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        logger.error("No message object available for swap_command")
        return

    await msg.reply_chat_action("typing")
    await handle_swap(update, context)


@restricted
async def lp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /lp command - Liquidity pool management

    Usage:
        /lp - Show liquidity pools menu (positions, pools, explorer)
    """
    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    # Get the appropriate message object for replies
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        logger.error("No message object available for lp_command")
        return

    await msg.reply_chat_action("typing")
    await handle_liquidity(update, context)


# ============================================
# LP MONITOR NAVIGATION HELPER
# ============================================

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from utils.telegram_formatters import escape_markdown_v2, resolve_token_symbol


async def _handle_lpm_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE, instance_id: str, new_index: int) -> None:
    """Handle navigation in LP monitor alert message."""
    query = update.callback_query
    positions_cache = context.user_data.get("positions_cache", {})
    token_cache = context.user_data.get("token_cache", {})

    # Find all positions for this instance
    positions = []
    i = 0
    while True:
        cache_key = f"lpm_{instance_id}_{i}"
        if cache_key in positions_cache:
            positions.append(positions_cache[cache_key])
            i += 1
        else:
            break

    if not positions:
        await query.answer("Positions not found")
        return

    # Clamp index
    new_index = max(0, min(new_index, len(positions) - 1))
    pos = positions[new_index]

    # Format the position
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"
    connector = pos.get('connector', 'unknown')

    # Price info
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))
    current = pos.get('current_price', '')

    range_str = ""
    if lower and upper:
        try:
            lower_f = float(lower)
            upper_f = float(upper)
            decimals = 2 if lower_f >= 1 else (6 if lower_f >= 0.001 else 8)
            range_str = f"Range: {lower_f:.{decimals}f} - {upper_f:.{decimals}f}"
        except (ValueError, TypeError):
            range_str = f"Range: {lower} - {upper}"

    current_str = ""
    direction = ""
    if current:
        try:
            current_f = float(current)
            lower_f = float(lower) if lower else 0
            upper_f = float(upper) if upper else 0
            decimals = 2 if current_f >= 1 else (6 if current_f >= 0.001 else 8)
            current_str = f"Current: {current_f:.{decimals}f}"
            if current_f < lower_f:
                direction = "▼ Below range"
            elif current_f > upper_f:
                direction = "▲ Above range"
        except (ValueError, TypeError):
            current_str = f"Current: {current}"

    # Value
    pnl_summary = pos.get('pnl_summary', {})
    value = pnl_summary.get('current_lp_value_quote', 0)
    value_str = ""
    if value:
        try:
            value_str = f"Value: {float(value):.2f} {quote_symbol}"
        except (ValueError, TypeError):
            pass

    # Build message
    total = len(positions)
    header = f"🚨 *Out of Range* \\({new_index + 1}/{total}\\)" if total > 1 else "🚨 *Position Out of Range*"
    lines = [header, "", f"*{escape_markdown_v2(pair)}* \\({escape_markdown_v2(connector)}\\)"]
    if direction:
        lines.append(f"_{escape_markdown_v2(direction)}_")
    if range_str:
        lines.append(escape_markdown_v2(range_str))
    if current_str:
        lines.append(escape_markdown_v2(current_str))
    if value_str:
        lines.append(escape_markdown_v2(value_str))

    text = "\n".join(lines)

    # Build keyboard
    cache_key = f"lpm_{instance_id}_{new_index}"
    keyboard = []

    if total > 1:
        nav_row = []
        if new_index > 0:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"dex:lpm_nav:{instance_id}:{new_index - 1}"))
        nav_row.append(InlineKeyboardButton(f"{new_index + 1}/{total}", callback_data="dex:lpm_noop"))
        if new_index < total - 1:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"dex:lpm_nav:{instance_id}:{new_index + 1}"))
        keyboard.append(nav_row)

    keyboard.append([
        InlineKeyboardButton("❌ Close", callback_data=f"dex:pos_close:{cache_key}"),
        InlineKeyboardButton("⏭ Skip", callback_data=f"dex:lpm_skip:{cache_key}"),
        InlineKeyboardButton("✅ Dismiss", callback_data=f"dex:lpm_dismiss:{instance_id}"),
    ])

    try:
        await query.message.edit_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning(f"Failed to update LPM navigation: {e}")


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
        slow_actions = {"main_menu", "swap", "swap_refresh", "swap_get_quote", "swap_execute_confirm", "swap_history",
                        "swap_hist_clear", "swap_hist_filter_pair", "swap_hist_filter_connector", "swap_hist_filter_status",
                        "swap_hist_page_prev", "swap_hist_page_next",
                        "liquidity", "lp_refresh", "lp_history", "lp_collect_all",
                        "lp_hist_clear", "lp_hist_filter_pair", "lp_hist_filter_connector", "lp_hist_filter_status",
                        "lp_hist_page_prev", "lp_hist_page_next",
                        "pool_info", "pool_list", "manage_positions", "pos_add_confirm", "pos_close_exec",
                        "add_to_gateway", "pool_detail_refresh",
                        "gecko_networks", "gecko_trades", "gecko_show_pools", "gecko_refresh", "gecko_token_search", "gecko_token_add",
                        "gecko_explore", "gecko_swap", "gecko_info", "gecko_add_tokens", "gecko_restart_gateway"}
        # Also show typing for actions that start with these prefixes
        slow_prefixes = ("gecko_trending_", "gecko_top_", "gecko_new_", "gecko_pool:", "gecko_ohlcv:",
                         "gecko_pool_tf:", "gecko_token:", "swap_hist_set_", "lp_hist_set_",
                         "lpm_nav:", "pos_close:")
        if action in slow_actions or action.startswith(slow_prefixes):
            await query.message.reply_chat_action("typing")

        # Menu (legacy - redirect to swap)
        if action == "main_menu":
            await handle_swap(update, context)

        # Unified swap handlers
        elif action == "swap":
            await handle_swap(update, context)
        elif action == "swap_refresh":
            await handle_swap_refresh(update, context)
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
        elif action == "swap_get_quote":
            await handle_swap_get_quote(update, context)
        elif action == "swap_execute_confirm":
            await handle_swap_execute_confirm(update, context)
        elif action == "swap_history":
            await handle_swap_history(update, context)

        # Swap history filter handlers
        elif action == "swap_hist_filter_pair":
            await handle_swap_hist_filter_pair(update, context)
        elif action == "swap_hist_filter_connector":
            await handle_swap_hist_filter_connector(update, context)
        elif action == "swap_hist_filter_status":
            await handle_swap_hist_filter_status(update, context)
        elif action.startswith("swap_hist_set_pair_"):
            value = action.replace("swap_hist_set_pair_", "")
            await handle_swap_hist_set_filter(update, context, "pair", value)
        elif action.startswith("swap_hist_set_connector_"):
            value = action.replace("swap_hist_set_connector_", "")
            await handle_swap_hist_set_filter(update, context, "connector", value)
        elif action.startswith("swap_hist_set_status_"):
            value = action.replace("swap_hist_set_status_", "")
            await handle_swap_hist_set_filter(update, context, "status", value)
        elif action == "swap_hist_page_prev":
            await handle_swap_hist_page(update, context, "prev")
        elif action == "swap_hist_page_next":
            await handle_swap_hist_page(update, context, "next")
        elif action == "swap_hist_clear":
            await handle_swap_hist_clear(update, context)

        # Legacy swap handlers (redirect to unified)
        elif action == "swap_quote":
            await handle_swap(update, context)
        elif action == "swap_execute":
            await handle_swap(update, context)
        elif action == "swap_search":
            await handle_swap_history(update, context)

        # Status handler (still separate)
        elif action == "swap_status":
            await handle_swap_status(update, context)

        # Unified liquidity handlers
        elif action == "liquidity":
            await handle_liquidity(update, context)
        elif action == "lp_refresh":
            await handle_lp_refresh(update, context)
        elif action.startswith("lp_pos_view:"):
            pos_index = int(action.split(":")[1])
            await handle_lp_pos_view(update, context, pos_index)
        elif action == "lp_collect_all":
            await handle_lp_collect_all(update, context)
        elif action == "lp_history":
            await handle_lp_history(update, context)

        # LP history filter handlers
        elif action == "lp_hist_filter_pair":
            await handle_lp_hist_filter_pair(update, context)
        elif action == "lp_hist_filter_connector":
            await handle_lp_hist_filter_connector(update, context)
        elif action == "lp_hist_filter_status":
            await handle_lp_hist_filter_status(update, context)
        elif action.startswith("lp_hist_set_pair_"):
            value = action.replace("lp_hist_set_pair_", "")
            await handle_lp_hist_set_filter(update, context, "pair", value)
        elif action.startswith("lp_hist_set_connector_"):
            value = action.replace("lp_hist_set_connector_", "")
            await handle_lp_hist_set_filter(update, context, "connector", value)
        elif action.startswith("lp_hist_set_status_"):
            value = action.replace("lp_hist_set_status_", "")
            await handle_lp_hist_set_filter(update, context, "status", value)
        elif action == "lp_hist_page_prev":
            await handle_lp_hist_page(update, context, "prev")
        elif action == "lp_hist_page_next":
            await handle_lp_hist_page(update, context, "next")
        elif action == "lp_hist_clear":
            await handle_lp_hist_clear(update, context)

        # No-op handler for page indicator buttons
        elif action == "noop":
            pass  # Do nothing, just acknowledge the callback

        # Legacy - redirect to main LP menu
        elif action == "explore_pools":
            await handle_lp_refresh(update, context)

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
        elif action.startswith("pool_tf:"):
            # Format: pool_tf:timeframe
            timeframe = action.split(":")[1]
            await handle_pool_detail_refresh(update, context, timeframe=timeframe)
        elif action == "add_to_gateway":
            await handle_add_to_gateway(update, context)
        elif action.startswith("plot_liquidity:"):
            percentile = int(action.split(":")[1])
            await handle_plot_liquidity(update, context, percentile)

        # Manage positions (unified view)
        elif action == "manage_positions":
            await handle_manage_positions(update, context)
        elif action.startswith("pos_view:"):
            pos_index = action.split(":")[1]
            await handle_pos_view(update, context, pos_index)
        elif action.startswith("pos_view_tf:"):
            # Format: pos_view_tf:pos_index:timeframe
            parts = action.split(":")
            pos_index = parts[1]
            timeframe = parts[2] if len(parts) > 2 else "1h"
            await handle_pos_view(update, context, pos_index, timeframe=timeframe)
        elif action.startswith("pos_view_pool:"):
            pos_index = action.split(":")[1]
            await handle_pos_view_pool(update, context, pos_index)
        elif action.startswith("pos_collect:"):
            pos_index = action.split(":")[1]
            await handle_pos_collect_fees(update, context, pos_index)
        elif action.startswith("pos_close:"):
            pos_index = action.split(":")[1]
            await handle_pos_close_confirm(update, context, pos_index)
        elif action.startswith("pos_close_exec:"):
            pos_index = action.split(":")[1]
            await handle_pos_close_execute(update, context, pos_index)
        elif action.startswith("lpm_skip:"):
            # Handle skip action from LP monitor routine
            cache_key = action.split(":")[1]
            await query.answer("Skipped")
            # Remove position from cache
            positions_cache = context.user_data.get("positions_cache", {})
            if cache_key in positions_cache:
                del positions_cache[cache_key]
            # Delete or update the alert message
            try:
                await query.message.edit_text(
                    "⏭ _Position skipped_",
                    parse_mode="MarkdownV2"
                )
            except Exception:
                pass
        elif action.startswith("lpm_nav:"):
            # Handle navigation in LP monitor alert - lpm_nav:{instance_id}:{index}
            parts = action.split(":")
            if len(parts) >= 3:
                instance_id = parts[1]
                new_index = int(parts[2])
                await _handle_lpm_navigation(update, context, instance_id, new_index)
            else:
                await query.answer()
        elif action.startswith("lpm_dismiss:"):
            # Handle dismiss action from LP monitor routine
            await query.answer("Dismissed")
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.message.edit_text(
                        "✅ _Alert dismissed_",
                        parse_mode="MarkdownV2"
                    )
                except Exception:
                    pass
        elif action == "lpm_noop":
            # No-op for page indicator button
            await query.answer()
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
            # Show pool address for copying - send as message so user can easily copy
            selected_pool = context.user_data.get("selected_pool", {})
            pool_address = selected_pool.get('pool_address', selected_pool.get('address', 'N/A'))
            # Send as a code block message for easy copying (Telegram allows tap-to-copy on code blocks)
            await query.answer("Address sent below ⬇️")
            await query.message.reply_text(
                f"`{pool_address}`",
                parse_mode="Markdown"
            )
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
        elif action.startswith("pos_tf:"):
            # Format: pos_tf:timeframe - switch timeframe in add position menu
            timeframe = action.split(":")[1]
            await handle_pos_refresh(update, context, timeframe=timeframe)

        # GeckoTerminal explore handlers
        elif action == "gecko_explore":
            await show_gecko_explore_menu(update, context)
        elif action == "gecko_toggle_view":
            await handle_gecko_toggle_view(update, context)
        elif action == "gecko_select_network":
            await handle_gecko_select_network(update, context)
        elif action.startswith("gecko_set_network:"):
            network = action.split(":")[1]
            await handle_gecko_set_network(update, context, network)
        elif action == "gecko_show_pools":
            await handle_gecko_show_pools(update, context)
        elif action == "gecko_refresh":
            await handle_gecko_refresh(update, context)
        elif action == "gecko_trending":
            await handle_gecko_trending(update, context)
        elif action.startswith("gecko_trending_"):
            network = action.replace("gecko_trending_", "")
            network = None if network == "all" else network
            await show_trending_pools(update, context, network)
        elif action == "gecko_top":
            await handle_gecko_top(update, context)
        elif action.startswith("gecko_top_"):
            network = action.replace("gecko_top_", "")
            await show_top_pools(update, context, network)
        elif action == "gecko_new":
            await handle_gecko_new(update, context)
        elif action.startswith("gecko_new_"):
            network = action.replace("gecko_new_", "")
            network = None if network == "all" else network
            await show_new_pools(update, context, network)
        elif action == "gecko_networks":
            await handle_gecko_networks(update, context)
        elif action.startswith("gecko_net_"):
            network = action.replace("gecko_net_", "")
            await show_network_menu(update, context, network)
        elif action == "gecko_search":
            await handle_gecko_search(update, context)
        elif action == "gecko_search_network":
            await handle_gecko_search_network(update, context)
        elif action.startswith("gecko_search_set_net:"):
            network = action.split(":")[1]
            await handle_gecko_search_set_network(update, context, network)
        elif action.startswith("gecko_pool:"):
            pool_index = int(action.split(":")[1])
            await show_pool_detail(update, context, pool_index)
        elif action == "gecko_charts":
            await show_gecko_charts_menu(update, context)
        elif action == "gecko_add_liquidity":
            await handle_gecko_add_liquidity(update, context)
        elif action.startswith("gecko_token:"):
            token_type = action.split(":")[1]
            await handle_gecko_token_info(update, context, token_type)
        elif action == "gecko_token_search":
            await handle_gecko_token_search(update, context)
        elif action == "gecko_token_add":
            await handle_gecko_token_add(update, context)
        elif action == "gecko_swap":
            await handle_gecko_swap(update, context)
        elif action == "gecko_info":
            await show_gecko_info(update, context)
        elif action.startswith("gecko_ohlcv:"):
            timeframe = action.split(":")[1]
            await show_ohlcv_chart(update, context, timeframe)
        elif action == "gecko_liquidity":
            await show_gecko_liquidity(update, context)
        elif action.startswith("gecko_combined:"):
            timeframe = action.split(":")[1]
            await show_gecko_combined(update, context, timeframe)
        elif action == "gecko_trades":
            await show_recent_trades(update, context)
        elif action == "gecko_copy_addr":
            await handle_copy_address(update, context)
        elif action == "gecko_back_to_list":
            await handle_back_to_list(update, context)
        elif action.startswith("gecko_pool_tf:"):
            timeframe = action.split(":")[1]
            await handle_gecko_pool_tf(update, context, timeframe)
        elif action == "gecko_add_tokens":
            await handle_gecko_add_tokens(update, context)
        elif action == "gecko_restart_gateway":
            await handle_gecko_restart_gateway(update, context)

        # Pool OHLCV and combined chart handlers (for Meteora/CLMM pools)
        elif action.startswith("pool_ohlcv:"):
            parts = action.split(":")
            timeframe = parts[1]
            currency = parts[2] if len(parts) > 2 else "usd"
            await handle_pool_ohlcv(update, context, timeframe, currency)
        elif action.startswith("pool_combined:"):
            parts = action.split(":")
            timeframe = parts[1]
            currency = parts[2] if len(parts) > 2 else "usd"
            await handle_pool_combined_chart(update, context, timeframe, currency)

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
        if dex_state in ["swap", "swap_status", "pool_info", "pool_list", "position_list", "add_position"]:
            context.user_data.pop("dex_state", None)

        # Unified swap handlers
        if dex_state == "swap":
            await process_swap(update, context, user_input)
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

        # GeckoTerminal search handler
        elif dex_state == "gecko_search":
            await process_gecko_search(update, context, user_input)

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
    'swap_command',
    'lp_command',
    'dex_callback_handler',
    'dex_message_handler',
    'get_dex_callback_handler',
    'get_dex_message_handler',
]
