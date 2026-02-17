"""
DEX Trading module - Decentralized Exchange trading via Gateway

Supports:
- DEX Swaps (Jupiter, 0x)
- CLMM Pools (Meteora, Raydium, Uniswap)
- CLMM Positions management
- Quick trading with saved parameters
- GeckoTerminal pool exploration with OHLCV charts

Module Structure:
- menu.py: Main DEX menu and help
- swap.py: Unified swap (quote, execute, history with filters/pagination)
- liquidity.py: Unified liquidity pools (balances, positions, history with filters/pagination)
- pools.py: Pool info, position management (add, close, collect fees)
- pool_data.py: Pool data fetching utilities (OHLCV, liquidity bins)
- geckoterminal.py: GeckoTerminal pool explorer with charts
- visualizations.py: Chart generation (liquidity distribution, OHLCV candlesticks)
- lp_monitor_handlers.py: LP monitor alert handling (navigation, rebalance, fees)
- router.py: Callback and message routing
- _shared.py: Shared utilities (caching, formatters, history filters)
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers import clear_all_input_states
from utils.auth import restricted

from .liquidity import handle_liquidity

# Import router components
from .router import (
    dex_callback_handler,
    dex_message_handler,
    get_dex_callback_handler,
    get_dex_message_handler,
)

# Import command handlers
from .swap import handle_swap

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
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
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
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg:
        logger.error("No message object available for lp_command")
        return

    await msg.reply_chat_action("typing")
    await handle_liquidity(update, context)


# ============================================
# MODULE EXPORTS
# ============================================

__all__ = [
    # Commands
    "swap_command",
    "lp_command",
    # Handlers
    "dex_callback_handler",
    "dex_message_handler",
    # Handler factories
    "get_dex_callback_handler",
    "get_dex_message_handler",
]
