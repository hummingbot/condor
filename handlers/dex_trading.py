"""
DEX Trading command handler - Decentralized Exchange trading via Gateway

Supports:
- DEX Swaps (Jupiter, 0x)
- CLMM Pools (Meteora, Raydium, Uniswap)
- CLMM Positions management
- Quick trading with saved parameters
"""

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from utils.auth import restricted
from utils.telegram_formatters import format_error_message, escape_markdown_v2
from handlers.config import clear_config_state
from handlers.config.trading_context import (
    get_last_dex_swap_params,
    set_last_dex_swap_params,
    get_last_dex_pool_params,
    set_last_dex_pool_params,
    get_default_dex_connector,
    DEFAULT_DEX_NETWORK,
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
    clear_config_state(context)
    # Clear any CLOB state to prevent interference
    context.user_data.pop("clob_state", None)
    context.user_data.pop("place_order_params", None)
    await update.message.reply_chat_action("typing")
    await show_dex_menu(update, context)


async def show_dex_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display main DEX trading menu"""

    last_swap = get_last_dex_swap_params(context.user_data)
    last_pool = get_last_dex_pool_params(context.user_data)

    header = r"🔄 *DEX Trading*" + "\n\n"

    # Show last swap params if available
    if last_swap and "connector" in last_swap:
        header += r"⚡ *Last Swap:*" + "\n"
        header += f"• Connector: {escape_markdown_v2(last_swap['connector'])}\n"
        if "trading_pair" in last_swap:
            header += f"• Pair: {escape_markdown_v2(last_swap['trading_pair'])}\n"
        header += "\n"

    header += "Select operation:"

    keyboard = []

    # Quick swap buttons if we have last params
    if last_swap and "connector" in last_swap and "trading_pair" in last_swap:
        keyboard.append([
            InlineKeyboardButton("⚡ Quick Swap", callback_data="dex:quick_swap")
        ])

    keyboard.extend([
        [
            InlineKeyboardButton("💰 Get Quote", callback_data="dex:swap_quote"),
            InlineKeyboardButton("✅ Execute Swap", callback_data="dex:swap_execute")
        ],
        [
            InlineKeyboardButton("📊 Swap Status", callback_data="dex:swap_status"),
            InlineKeyboardButton("🔍 Swap History", callback_data="dex:swap_search")
        ],
        [
            InlineKeyboardButton("📋 List Pools", callback_data="dex:pool_list"),
            InlineKeyboardButton("📍 Get Positions", callback_data="dex:position_list")
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="dex:help")
        ]
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.message.edit_text(
            header,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            header,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


# ============================================
# SWAP HANDLERS
# ============================================

async def handle_quick_swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quick swap with last used parameters"""
    help_text = (
        r"⚡ *Quick Swap*" + "\n\n"
        r"Reply with: `side amount [slippage]`" + "\n\n"
        r"*Examples:*" + "\n"
        r"`BUY 1\.5` \- Buy with last params" + "\n"
        r"`SELL 0\.5 2\.0` \- Sell with 2% slippage"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "quick_swap"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle swap quote - interactive menu similar to swap execute"""

    # Initialize quote params in context if not exists
    if "quote_swap_params" not in context.user_data:
        last_params = get_last_dex_swap_params(context.user_data)
        context.user_data["quote_swap_params"] = {
            "connector": last_params.get("connector", get_default_dex_connector(DEFAULT_DEX_NETWORK)),
            "network": last_params.get("network", DEFAULT_DEX_NETWORK),
            "trading_pair": last_params.get("trading_pair", "SOL-USDC"),
            "side": "BUY",
            "amount": "1.0",
            "slippage": "1.0",
        }

    # Set state to allow text input for direct quote request
    context.user_data["dex_state"] = "swap_quote"

    await show_swap_quote_menu(update, context)


async def show_swap_quote_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, send_new: bool = False) -> None:
    """Display the swap quote configuration menu with interactive buttons

    Args:
        update: The update object
        context: The context object
        send_new: If True, always send a new message instead of editing
    """
    params = context.user_data.get("quote_swap_params", {})

    # Build header with detailed explanation
    help_text = r"💰 *Get Swap Quote*" + "\n\n"

    help_text += r"*Configure your quote request using the buttons below or type parameters directly\.*" + "\n\n"

    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*📊 Current Configuration*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

    help_text += f"🔌 *Connector:* `{escape_markdown_v2(params.get('connector', 'N/A'))}`\n"
    help_text += f"🌐 *Network:* `{escape_markdown_v2(params.get('network', 'N/A'))}`\n"
    help_text += f"💱 *Trading Pair:* `{escape_markdown_v2(params.get('trading_pair', 'N/A'))}`\n"
    help_text += f"📈 *Side:* `{escape_markdown_v2(params.get('side', 'N/A'))}`\n"
    help_text += f"💰 *Amount:* `{escape_markdown_v2(params.get('amount', 'N/A'))}`\n"
    help_text += f"📊 *Slippage:* `{escape_markdown_v2(params.get('slippage', 'N/A'))}%`\n"

    help_text += "\n" + r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*🎮 Interactive Configuration*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"
    help_text += r"Click buttons below to configure each parameter:" + "\n"
    help_text += r"• *Toggle buttons* cycle through options" + "\n"
    help_text += r"• *Input buttons* prompt for new values" + "\n\n"

    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*⌨️ Or Type Directly*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"
    help_text += r"Reply with quote parameters:" + "\n\n"
    help_text += r"`trading_pair side amount [slippage]`" + "\n"
    help_text += r"or" + "\n"
    help_text += r"`connector network trading_pair side amount [slippage]`" + "\n\n"
    help_text += r"*Examples:*" + "\n"
    help_text += r"`SOL\-USDC BUY 1\.5`" + "\n"
    help_text += r"`jupiter solana\-mainnet\-beta SOL\-USDC BUY 1\.5 2\.0`" + "\n\n"

    # Build keyboard with parameter buttons
    keyboard = []

    # Row 1: Connector and Network
    keyboard.append([
        InlineKeyboardButton(
            f"{params.get('connector', 'jupiter')}",
            callback_data="dex:quote_set_connector"
        ),
        InlineKeyboardButton(
            f"{params.get('network', 'solana-mainnet-beta')}",
            callback_data="dex:quote_set_network"
        )
    ])

    # Row 2: Trading Pair and Side
    keyboard.append([
        InlineKeyboardButton(
            f"{params.get('trading_pair', 'SOL-USDC')}",
            callback_data="dex:quote_set_pair"
        ),
        InlineKeyboardButton(
            f"{params.get('side', 'BUY')}",
            callback_data="dex:quote_toggle_side"
        )
    ])

    # Row 3: Amount and Slippage
    keyboard.append([
        InlineKeyboardButton(
            f"{params.get('amount', '1.0')}",
            callback_data="dex:quote_set_amount"
        ),
        InlineKeyboardButton(
            f"{params.get('slippage', '1.0')}%",
            callback_data="dex:quote_set_slippage"
        )
    ])

    # Row 4: Get Quote and Cancel
    keyboard.append([
        InlineKeyboardButton("💰 Get Quote", callback_data="dex:quote_get_confirm"),
        InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if send_new or not update.callback_query:
        await update.message.reply_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    else:
        await update.callback_query.message.edit_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


async def handle_swap_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle swap execute - interactive menu similar to CLOB place order"""

    # Initialize swap params in context if not exists
    if "execute_swap_params" not in context.user_data:
        last_params = get_last_dex_swap_params(context.user_data)
        context.user_data["execute_swap_params"] = {
            "connector": last_params.get("connector", get_default_dex_connector(DEFAULT_DEX_NETWORK)),
            "network": last_params.get("network", DEFAULT_DEX_NETWORK),
            "trading_pair": last_params.get("trading_pair", "SOL-USDC"),
            "side": "BUY",
            "amount": "1.0",
            "slippage": "1.0",
        }

    # Set state to allow text input for direct swap execution
    context.user_data["dex_state"] = "swap_execute"

    await show_swap_execute_menu(update, context)


async def show_swap_execute_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, send_new: bool = False) -> None:
    """Display the swap execution configuration menu with interactive buttons

    Args:
        update: The update object
        context: The context object
        send_new: If True, always send a new message instead of editing
    """
    params = context.user_data.get("execute_swap_params", {})

    # Build header with detailed explanation
    help_text = r"✅ *Execute Swap*" + "\n\n"

    help_text += r"⚠️ *This executes a real transaction\!*" + "\n\n"

    help_text += r"*Configure your swap using the buttons below or type parameters directly\.*" + "\n\n"

    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*📊 Current Configuration*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

    help_text += f"🔌 *Connector:* `{escape_markdown_v2(params.get('connector', 'N/A'))}`\n"
    help_text += f"🌐 *Network:* `{escape_markdown_v2(params.get('network', 'N/A'))}`\n"
    help_text += f"💱 *Trading Pair:* `{escape_markdown_v2(params.get('trading_pair', 'N/A'))}`\n"
    help_text += f"📈 *Side:* `{escape_markdown_v2(params.get('side', 'N/A'))}`\n"
    help_text += f"💰 *Amount:* `{escape_markdown_v2(params.get('amount', 'N/A'))}`\n"
    help_text += f"📊 *Slippage:* `{escape_markdown_v2(params.get('slippage', 'N/A'))}%`\n"

    help_text += "\n" + r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*🎮 Interactive Configuration*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"
    help_text += r"Click buttons below to configure each parameter:" + "\n"
    help_text += r"• *Toggle buttons* cycle through options" + "\n"
    help_text += r"• *Input buttons* prompt for new values" + "\n\n"

    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*⌨️ Or Type Directly*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"
    help_text += r"Reply with swap parameters:" + "\n\n"
    help_text += r"`trading_pair side amount [slippage]`" + "\n"
    help_text += r"or" + "\n"
    help_text += r"`connector network trading_pair side amount [slippage]`" + "\n\n"
    help_text += r"*Examples:*" + "\n"
    help_text += r"`SOL\-USDC BUY 1\.5`" + "\n"
    help_text += r"`jupiter solana\-mainnet\-beta SOL\-USDC BUY 1\.5 2\.0`" + "\n\n"

    # Build keyboard with parameter buttons
    keyboard = []

    # Row 1: Connector and Network
    keyboard.append([
        InlineKeyboardButton(
            f"{params.get('connector', 'jupiter')}",
            callback_data="dex:swap_set_connector"
        ),
        InlineKeyboardButton(
            f"{params.get('network', 'solana-mainnet-beta')}",
            callback_data="dex:swap_set_network"
        )
    ])

    # Row 2: Trading Pair and Side
    keyboard.append([
        InlineKeyboardButton(
            f"{params.get('trading_pair', 'SOL-USDC')}",
            callback_data="dex:swap_set_pair"
        ),
        InlineKeyboardButton(
            f"{params.get('side', 'BUY')}",
            callback_data="dex:swap_toggle_side"
        )
    ])

    # Row 3: Amount and Slippage
    keyboard.append([
        InlineKeyboardButton(
            f"{params.get('amount', '1.0')}",
            callback_data="dex:swap_set_amount"
        ),
        InlineKeyboardButton(
            f"{params.get('slippage', '1.0')}%",
            callback_data="dex:swap_set_slippage"
        )
    ])

    # Row 4: Execute and Cancel
    keyboard.append([
        InlineKeyboardButton("✅ Execute Swap", callback_data="dex:swap_execute_confirm"),
        InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if send_new or not update.callback_query:
        await update.message.reply_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    else:
        await update.callback_query.message.edit_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


async def handle_swap_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle swap status check"""
    help_text = (
        r"📊 *Get Swap Status*" + "\n\n"
        r"Reply with transaction hash:" + "\n\n"
        r"`<tx_hash>`"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_status"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle swap history search"""
    try:
        from servers import server_manager

        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            error_message = format_error_message("No enabled API servers available")
            await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        if not hasattr(client, 'gateway_swap'):
            error_message = format_error_message("Gateway swap not available")
            await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        result = await client.gateway_swap.search_swaps(limit=10)
        swaps = result.get("data", [])

        if not swaps:
            message = r"🔍 *Swap History*\n\nNo swaps found\."
        else:
            swap_lines = []
            for swap in swaps:
                pair = swap.get('trading_pair', 'N/A')
                side = swap.get('side', 'N/A')
                amount = swap.get('amount', 'N/A')
                status = swap.get('status', 'N/A')
                swap_lines.append(f"• {pair} {side} {amount} - {status}")

            swap_text = escape_markdown_v2("\n".join(swap_lines))
            message = rf"🔍 *Recent Swaps* \({len(swaps)} found\)\n\n{swap_text}"

        keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.reply_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error searching swaps: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to search swaps: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# POOL HANDLERS
# ============================================

async def handle_pool_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle CLMM pool list"""
    help_text = (
        r"📋 *List CLMM Pools*" + "\n\n"
        r"Reply with:" + "\n\n"
        r"`connector [search_term] [limit]`" + "\n\n"
        r"*Examples:*" + "\n"
        r"`meteora SOL 10`" + "\n"
        r"`raydium USDC`"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pool_list"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_position_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle CLMM position list"""
    help_text = (
        r"📍 *Get CLMM Positions*" + "\n\n"
        r"Reply with:" + "\n\n"
        r"`connector network pool_address`" + "\n\n"
        r"*Example:*" + "\n"
        r"`meteora solana\-mainnet\-beta POOL_ADDRESS`"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "position_list"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# QUOTE - PARAMETER HANDLERS
# ============================================

async def handle_quote_toggle_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between BUY and SELL for quote"""
    params = context.user_data.get("quote_swap_params", {})
    current_side = params.get("side", "BUY")
    params["side"] = "SELL" if current_side == "BUY" else "BUY"
    await show_swap_quote_menu(update, context)


async def handle_quote_set_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input connector for quote"""
    help_text = (
        r"📝 *Set Connector*" + "\n\n"
        r"Enter the DEX connector name:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`jupiter` \- Solana" + "\n"
        r"`uniswap` \- Ethereum/Arbitrum/Base" + "\n"
        r"`meteora` \- Solana CLMM" + "\n"
        r"`raydium` \- Solana CLMM"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_quote")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "quote_set_connector"
    context.user_data["dex_previous_state"] = "swap_quote"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_quote_set_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input network for quote"""
    help_text = (
        r"📝 *Set Network*" + "\n\n"
        r"Enter the network name:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`solana\-mainnet\-beta`" + "\n"
        r"`ethereum\-mainnet`" + "\n"
        r"`ethereum\-arbitrum`" + "\n"
        r"`ethereum\-base`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_quote")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "quote_set_network"
    context.user_data["dex_previous_state"] = "swap_quote"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_quote_set_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input trading pair for quote"""
    help_text = (
        r"📝 *Set Trading Pair*" + "\n\n"
        r"Enter the trading pair:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`SOL\-USDC`" + "\n"
        r"`ETH\-USDT`" + "\n"
        r"`BTC\-USDC`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_quote")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "quote_set_pair"
    context.user_data["dex_previous_state"] = "swap_quote"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_quote_set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input amount for quote"""
    help_text = (
        r"📝 *Set Amount*" + "\n\n"
        r"Enter the amount for quote:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`1\.5` \- Quote for 1\.5 tokens" + "\n"
        r"`0\.01` \- Quote for 0\.01 tokens"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_quote")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "quote_set_amount"
    context.user_data["dex_previous_state"] = "swap_quote"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_quote_set_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input slippage for quote"""
    help_text = (
        r"📝 *Set Slippage*" + "\n\n"
        r"Enter the slippage percentage:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`1\.0` \- 1% slippage" + "\n"
        r"`2\.5` \- 2\.5% slippage"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_quote")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "quote_set_slippage"
    context.user_data["dex_previous_state"] = "swap_quote"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_quote_get_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get the quote with current parameters"""
    try:
        params = context.user_data.get("quote_swap_params", {})

        connector = params.get("connector")
        network = params.get("network")
        trading_pair = params.get("trading_pair")
        side = params.get("side")
        amount = params.get("amount")
        slippage = params.get("slippage")

        # Validate required parameters
        if not all([connector, network, trading_pair, side, amount]):
            raise ValueError("Missing required parameters")

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers available")

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        if not hasattr(client, 'gateway_swap'):
            raise ValueError("Gateway swap not available")

        result = await client.gateway_swap.get_swap_quote(
            connector=connector,
            network=network,
            trading_pair=trading_pair,
            side=side,
            amount=Decimal(amount),
            slippage_pct=Decimal(slippage)
        )

        # Save parameters for quick trading
        set_last_dex_swap_params(context.user_data, {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "slippage": slippage
        })

        # Update quote_swap_params with the values used
        context.user_data["quote_swap_params"] = {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "amount": amount,
            "slippage": slippage,
        }

        quote_info = escape_markdown_v2(
            f"💰 Swap Quote\n\n"
            f"Connector: {connector}\n"
            f"Network: {network}\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount}"
        )

        if isinstance(result, dict):
            if 'expected_amount' in result:
                quote_info += escape_markdown_v2(f"\nExpected: {result['expected_amount']}")
            if 'price' in result:
                quote_info += escape_markdown_v2(f"\nPrice: {result['price']}")
            if 'slippage' in result:
                quote_info += escape_markdown_v2(f"\nSlippage: {slippage}%")

        # Create keyboard with back to menu button
        keyboard = [[InlineKeyboardButton("« Back to DEX Trading", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.reply_text(
            quote_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error getting quote: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get quote: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# SWAP EXECUTE - PARAMETER HANDLERS
# ============================================

async def handle_swap_toggle_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between BUY and SELL"""
    params = context.user_data.get("execute_swap_params", {})
    current_side = params.get("side", "BUY")
    params["side"] = "SELL" if current_side == "BUY" else "BUY"
    await show_swap_execute_menu(update, context)


async def handle_swap_set_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input connector"""
    help_text = (
        r"📝 *Set Connector*" + "\n\n"
        r"Enter the DEX connector name:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`jupiter` \- Solana" + "\n"
        r"`uniswap` \- Ethereum/Arbitrum/Base" + "\n"
        r"`meteora` \- Solana CLMM" + "\n"
        r"`raydium` \- Solana CLMM"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_connector"
    context.user_data["dex_previous_state"] = "swap_execute"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_set_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input network"""
    help_text = (
        r"📝 *Set Network*" + "\n\n"
        r"Enter the network name:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`solana\-mainnet\-beta`" + "\n"
        r"`ethereum\-mainnet`" + "\n"
        r"`ethereum\-arbitrum`" + "\n"
        r"`ethereum\-base`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_network"
    context.user_data["dex_previous_state"] = "swap_execute"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_set_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input trading pair"""
    help_text = (
        r"📝 *Set Trading Pair*" + "\n\n"
        r"Enter the trading pair:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`SOL\-USDC`" + "\n"
        r"`ETH\-USDT`" + "\n"
        r"`BTC\-USDC`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_pair"
    context.user_data["dex_previous_state"] = "swap_execute"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input amount"""
    help_text = (
        r"📝 *Set Amount*" + "\n\n"
        r"Enter the amount to swap:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`1\.5` \- Swap 1\.5 tokens" + "\n"
        r"`0\.01` \- Swap 0\.01 tokens"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_amount"
    context.user_data["dex_previous_state"] = "swap_execute"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_set_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input slippage"""
    help_text = (
        r"📝 *Set Slippage*" + "\n\n"
        r"Enter the slippage percentage:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`1\.0` \- 1% slippage" + "\n"
        r"`2\.5` \- 2\.5% slippage"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_slippage"
    context.user_data["dex_previous_state"] = "swap_execute"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_execute_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the swap with current parameters"""
    try:
        params = context.user_data.get("execute_swap_params", {})

        connector = params.get("connector")
        network = params.get("network")
        trading_pair = params.get("trading_pair")
        side = params.get("side")
        amount = params.get("amount")
        slippage = params.get("slippage")

        # Validate required parameters
        if not all([connector, network, trading_pair, side, amount]):
            raise ValueError("Missing required parameters")

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers available")

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        if not hasattr(client, 'gateway_swap'):
            raise ValueError("Gateway swap not available")

        result = await client.gateway_swap.execute_swap(
            connector=connector,
            network=network,
            trading_pair=trading_pair,
            side=side,
            amount=Decimal(amount),
            slippage_pct=Decimal(slippage)
        )

        # Save parameters for quick trading
        set_last_dex_swap_params(context.user_data, {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "slippage": slippage
        })

        # Update execute_swap_params with the values used
        context.user_data["execute_swap_params"] = {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "amount": amount,
            "slippage": slippage,
        }

        swap_info = escape_markdown_v2(
            f"✅ Swap executed successfully!\n\n"
            f"Connector: {connector}\n"
            f"Network: {network}\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount}\n"
            f"Slippage: {slippage}%"
        )

        if isinstance(result, dict):
            if 'tx_hash' in result:
                swap_info += escape_markdown_v2(f"\nTx: {result['tx_hash'][:16]}...")
            if 'status' in result:
                swap_info += escape_markdown_v2(f"\nStatus: {result['status']}")

        # Create keyboard with back to menu button
        keyboard = [[InlineKeyboardButton("« Back to DEX Trading", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.reply_text(
            swap_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error executing swap: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to execute swap: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# HELP
# ============================================

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display help"""
    help_text = (
        r"❓ *DEX Trading Help*" + "\n\n"
        r"*Quick Swap:*" + "\n"
        r"Fast trading using your last used DEX and pair\." + "\n\n"
        r"*Defaults:*" + "\n"
        r"• Network: `solana\-mainnet\-beta`" + "\n"
        r"• Solana Connector: `jupiter`" + "\n"
        r"• Ethereum Connector: `uniswap`" + "\n\n"
        r"*Supported DEXs:*" + "\n"
        r"• Jupiter \(Solana\)" + "\n"
        r"• Uniswap \(Ethereum/Arbitrum/Base\)" + "\n"
        r"• Meteora CLMM \(Solana\)" + "\n"
        r"• Raydium CLMM \(Solana\)"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# CALLBACK HANDLER
# ============================================

@restricted
async def dex_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks"""
    query = update.callback_query
    await query.answer()
    await query.message.reply_chat_action("typing")

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        if action == "main_menu":
            await show_dex_menu(update, context)
        elif action == "quick_swap":
            await handle_quick_swap(update, context)
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
        elif action == "swap_execute":
            await handle_swap_execute(update, context)
        elif action == "swap_toggle_side":
            await handle_swap_toggle_side(update, context)
        elif action == "swap_set_connector":
            await handle_swap_set_connector(update, context)
        elif action == "swap_set_network":
            await handle_swap_set_network(update, context)
        elif action == "swap_set_pair":
            await handle_swap_set_pair(update, context)
        elif action == "swap_set_amount":
            await handle_swap_set_amount(update, context)
        elif action == "swap_set_slippage":
            await handle_swap_set_slippage(update, context)
        elif action == "swap_execute_confirm":
            await handle_swap_execute_confirm(update, context)
        elif action == "swap_status":
            await handle_swap_status(update, context)
        elif action == "swap_search":
            await handle_swap_search(update, context)
        elif action == "pool_list":
            await handle_pool_list(update, context)
        elif action == "position_list":
            await handle_position_list(update, context)
        elif action == "help":
            await show_help(update, context)
        else:
            await query.message.reply_text(f"Unknown action: {action}")

    except Exception as e:
        logger.error(f"Error in DEX callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        await query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# MESSAGE HANDLER
# ============================================

@restricted
async def dex_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user text input"""
    dex_state = context.user_data.get("dex_state")

    if not dex_state:
        return

    user_input = update.message.text.strip()

    try:
        # Only remove state for operations that complete (not parameter setting)
        if dex_state in ["quick_swap", "swap_quote", "swap_execute", "swap_status", "pool_list", "position_list"]:
            context.user_data.pop("dex_state", None)

        if dex_state == "quick_swap":
            await process_quick_swap(update, context, user_input)
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
        elif dex_state == "swap_status":
            await process_swap_status(update, context, user_input)
        elif dex_state == "pool_list":
            await process_pool_list(update, context, user_input)
        elif dex_state == "position_list":
            await process_position_list(update, context, user_input)
        else:
            await update.message.reply_text(f"Unknown state: {dex_state}")

    except Exception as e:
        logger.error(f"Error processing DEX input: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to process input: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# PROCESSING FUNCTIONS
# ============================================

async def process_quick_swap(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process quick swap"""
    try:
        parts = user_input.split()
        if len(parts) < 2:
            raise ValueError("Need: side amount [slippage]")

        side = parts[0].upper()
        amount = parts[1]
        slippage = parts[2] if len(parts) > 2 else "1.0"

        last_params = get_last_dex_swap_params(context.user_data)
        if not last_params or "connector" not in last_params:
            raise ValueError("No previous swap parameters. Use 'Get Quote' first.")

        connector = last_params["connector"]
        network = last_params["network"]
        trading_pair = last_params["trading_pair"]

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers")

        client = await server_manager.get_client(enabled_servers[0])

        if not hasattr(client, 'gateway_swap'):
            raise ValueError("Gateway swap not available")

        result = await client.gateway_swap.execute_swap(
            connector=connector,
            network=network,
            trading_pair=trading_pair,
            side=side,
            amount=Decimal(amount),
            slippage_pct=Decimal(slippage)
        )

        swap_info = escape_markdown_v2(
            f"✅ Quick Swap Executed!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount}"
        )

        if isinstance(result, dict) and 'tx_hash' in result:
            swap_info += escape_markdown_v2(f"\nTx: {result['tx_hash'][:16]}...")

        await update.message.reply_text(swap_info, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error in quick swap: {e}", exc_info=True)
        error_message = format_error_message(f"Quick swap failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_quote(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap quote"""
    try:
        parts = user_input.split()

        # Allow flexible input - can omit connector/network to use defaults
        if len(parts) < 3:
            raise ValueError("Need at least: trading_pair side amount\n"
                           "Optional: connector network trading_pair side amount [slippage]")

        # Try to parse with all params first
        if len(parts) >= 5:
            # Full format: connector network trading_pair side amount [slippage]
            connector = parts[0]
            network = parts[1]
            trading_pair = parts[2]
            side = parts[3].upper()
            amount = parts[4]
            slippage = parts[5] if len(parts) > 5 else "1.0"
        else:
            # Short format: trading_pair side amount [slippage]
            # Use defaults
            network = DEFAULT_DEX_NETWORK
            connector = get_default_dex_connector(network)
            trading_pair = parts[0]
            side = parts[1].upper()
            amount = parts[2]
            slippage = parts[3] if len(parts) > 3 else "1.0"

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers")

        client = await server_manager.get_client(enabled_servers[0])

        if not hasattr(client, 'gateway_swap'):
            raise ValueError("Gateway swap not available")

        result = await client.gateway_swap.get_swap_quote(
            connector=connector,
            network=network,
            trading_pair=trading_pair,
            side=side,
            amount=Decimal(amount),
            slippage_pct=Decimal(slippage)
        )

        # Save params
        set_last_dex_swap_params(context.user_data, {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "slippage": slippage
        })

        quote_info = escape_markdown_v2(
            f"💰 Swap Quote\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount}"
        )

        if isinstance(result, dict):
            if 'expected_amount' in result:
                quote_info += escape_markdown_v2(f"\nExpected: {result['expected_amount']}")
            if 'price' in result:
                quote_info += escape_markdown_v2(f"\nPrice: {result['price']}")

        await update.message.reply_text(quote_info, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error getting quote: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get quote: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_execute(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap execute"""
    try:
        parts = user_input.split()

        # Allow flexible input - can omit connector/network to use defaults
        if len(parts) < 3:
            raise ValueError("Need at least: trading_pair side amount\n"
                           "Optional: connector network trading_pair side amount [slippage]")

        # Try to parse with all params first
        if len(parts) >= 5:
            # Full format: connector network trading_pair side amount [slippage]
            connector = parts[0]
            network = parts[1]
            trading_pair = parts[2]
            side = parts[3].upper()
            amount = parts[4]
            slippage = parts[5] if len(parts) > 5 else "1.0"
        else:
            # Short format: trading_pair side amount [slippage]
            # Use defaults
            network = DEFAULT_DEX_NETWORK
            connector = get_default_dex_connector(network)
            trading_pair = parts[0]
            side = parts[1].upper()
            amount = parts[2]
            slippage = parts[3] if len(parts) > 3 else "1.0"

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers")

        client = await server_manager.get_client(enabled_servers[0])

        if not hasattr(client, 'gateway_swap'):
            raise ValueError("Gateway swap not available")

        result = await client.gateway_swap.execute_swap(
            connector=connector,
            network=network,
            trading_pair=trading_pair,
            side=side,
            amount=Decimal(amount),
            slippage_pct=Decimal(slippage)
        )

        # Save params
        set_last_dex_swap_params(context.user_data, {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "slippage": slippage
        })

        swap_info = escape_markdown_v2(
            f"✅ Swap Executed!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount}"
        )

        if isinstance(result, dict):
            if 'tx_hash' in result:
                swap_info += escape_markdown_v2(f"\nTx: {result['tx_hash'][:16]}...")
            if 'status' in result:
                swap_info += escape_markdown_v2(f"\nStatus: {result['status']}")

        # Create keyboard with back to menu button
        keyboard = [[InlineKeyboardButton("« Back to DEX Trading", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            swap_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error executing swap: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to execute swap: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_quote_set_connector(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process quote set connector input"""
    try:
        params = context.user_data.get("quote_swap_params", {})
        params["connector"] = user_input.strip()

        # Auto-update network if switching connector
        connector = params["connector"]
        if connector in ["jupiter", "meteora", "raydium"]:
            params["network"] = "solana-mainnet-beta"
        elif connector == "uniswap":
            params["network"] = "ethereum-mainnet"

        # Restore swap_quote state for text input
        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Connector set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to quote menu by sending a new message
        await show_swap_quote_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting connector: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set connector: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_quote_set_network(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process quote set network input"""
    try:
        params = context.user_data.get("quote_swap_params", {})
        network = user_input.strip()
        params["network"] = network

        # Auto-update connector based on network
        params["connector"] = get_default_dex_connector(network)

        # Restore swap_quote state for text input
        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Network set to: {network}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to quote menu by sending a new message
        await show_swap_quote_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting network: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set network: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_quote_set_pair(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process quote set trading pair input"""
    try:
        params = context.user_data.get("quote_swap_params", {})
        params["trading_pair"] = user_input.strip()

        # Restore swap_quote state for text input
        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Trading pair set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to quote menu by sending a new message
        await show_swap_quote_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting trading pair: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set trading pair: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_quote_set_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process quote set amount input"""
    try:
        params = context.user_data.get("quote_swap_params", {})
        params["amount"] = user_input.strip()

        # Restore swap_quote state for text input
        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Amount set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to quote menu by sending a new message
        await show_swap_quote_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting amount: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set amount: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_quote_set_slippage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process quote set slippage input"""
    try:
        params = context.user_data.get("quote_swap_params", {})
        params["slippage"] = user_input.strip()

        # Restore swap_quote state for text input
        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Slippage set to: {user_input}%")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to quote menu by sending a new message
        await show_swap_quote_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting slippage: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set slippage: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_connector(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set connector input"""
    try:
        params = context.user_data.get("execute_swap_params", {})
        params["connector"] = user_input.strip()

        # Auto-update network if switching connector
        connector = params["connector"]
        if connector in ["jupiter", "meteora", "raydium"]:
            params["network"] = "solana-mainnet-beta"
        elif connector == "uniswap":
            params["network"] = "ethereum-mainnet"

        # Restore swap_execute state for text input
        context.user_data["dex_state"] = "swap_execute"

        success_msg = escape_markdown_v2(f"✅ Connector set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to swap menu by sending a new message
        await show_swap_execute_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting connector: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set connector: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_network(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set network input"""
    try:
        params = context.user_data.get("execute_swap_params", {})
        network = user_input.strip()
        params["network"] = network

        # Auto-update connector based on network
        params["connector"] = get_default_dex_connector(network)

        # Restore swap_execute state for text input
        context.user_data["dex_state"] = "swap_execute"

        success_msg = escape_markdown_v2(f"✅ Network set to: {network}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to swap menu by sending a new message
        await show_swap_execute_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting network: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set network: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_pair(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set trading pair input"""
    try:
        params = context.user_data.get("execute_swap_params", {})
        params["trading_pair"] = user_input.strip()

        # Restore swap_execute state for text input
        context.user_data["dex_state"] = "swap_execute"

        success_msg = escape_markdown_v2(f"✅ Trading pair set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to swap menu by sending a new message
        await show_swap_execute_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting trading pair: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set trading pair: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set amount input"""
    try:
        params = context.user_data.get("execute_swap_params", {})
        params["amount"] = user_input.strip()

        # Restore swap_execute state for text input
        context.user_data["dex_state"] = "swap_execute"

        success_msg = escape_markdown_v2(f"✅ Amount set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to swap menu by sending a new message
        await show_swap_execute_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting amount: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set amount: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_slippage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set slippage input"""
    try:
        params = context.user_data.get("execute_swap_params", {})
        params["slippage"] = user_input.strip()

        # Restore swap_execute state for text input
        context.user_data["dex_state"] = "swap_execute"

        success_msg = escape_markdown_v2(f"✅ Slippage set to: {user_input}%")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to swap menu by sending a new message
        await show_swap_execute_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting slippage: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set slippage: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tx_hash: str
) -> None:
    """Process swap status check"""
    try:
        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers")

        client = await server_manager.get_client(enabled_servers[0])

        if not hasattr(client, 'gateway_swap'):
            raise ValueError("Gateway swap not available")

        result = await client.gateway_swap.get_swap_status(tx_hash)

        status_info = escape_markdown_v2(f"📊 Swap Status\n\nTx: {tx_hash[:16]}...\n")

        if isinstance(result, dict):
            for key in ['status', 'trading_pair', 'side', 'amount']:
                if key in result:
                    status_info += escape_markdown_v2(f"{key.title()}: {result[key]}\n")

        await update.message.reply_text(status_info, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error getting status: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get status: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_pool_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process pool list"""
    try:
        parts = user_input.split()
        if len(parts) < 1:
            raise ValueError("Need: connector [search_term] [limit]")

        connector = parts[0]
        search_term = parts[1] if len(parts) > 1 and parts[1] != "_" else None
        limit = int(parts[2]) if len(parts) > 2 else 10

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers")

        client = await server_manager.get_client(enabled_servers[0])

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        result = await client.gateway_clmm.get_pools(
            connector=connector,
            page=0,
            limit=limit,
            search_term=search_term
        )

        pools = result.get("pools", [])

        if not pools:
            message = escape_markdown_v2(f"📋 No pools found")
        else:
            pool_lines = []
            for pool in pools[:10]:
                pair = pool.get('trading_pair', 'N/A')
                liquidity = pool.get('liquidity', 'N/A')
                pool_lines.append(f"• {pair} - Liq: {liquidity}")

            pool_text = escape_markdown_v2("\n".join(pool_lines))
            total = result.get("total", len(pools))
            message = rf"📋 *CLMM Pools* \({len(pools)} of {total}\)\n\n{pool_text}"

        await update.message.reply_text(message, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error listing pools: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to list pools: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_position_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process position list"""
    try:
        parts = user_input.split()
        if len(parts) < 3:
            raise ValueError("Need: connector network pool_address")

        connector = parts[0]
        network = parts[1]
        pool_address = parts[2]

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers")

        client = await server_manager.get_client(enabled_servers[0])

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        positions = await client.gateway_clmm.get_positions_owned(
            connector=connector,
            network=network,
            pool_address=pool_address
        )

        # Save params
        set_last_dex_pool_params(context.user_data, {
            "connector": connector,
            "network": network,
            "pool_address": pool_address
        })

        if not positions:
            message = escape_markdown_v2("📍 No positions found")
        else:
            pos_lines = []
            for pos in positions[:5]:
                pos_id = pos.get('position_address', pos.get('nft_id', 'N/A'))
                lower = pos.get('lower_price', 'N/A')
                upper = pos.get('upper_price', 'N/A')
                pos_lines.append(f"• {pos_id[:8]}... [{lower}-{upper}]")

            pos_text = escape_markdown_v2("\n".join(pos_lines))
            message = rf"📍 *CLMM Positions* \({len(positions)} found\)\n\n{pos_text}"

        await update.message.reply_text(message, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error getting positions: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get positions: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# HELPER
# ============================================

def get_dex_message_handler():
    """Returns the message handler"""
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        dex_message_handler
    )
