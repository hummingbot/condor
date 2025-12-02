"""
DEX Swap Quote functionality

Provides:
- Quote menu display
- Parameter setting handlers
- Quote execution
"""

import logging
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from handlers.config.user_preferences import (
    get_dex_swap_defaults,
    get_dex_connector,
    set_dex_last_swap,
    DEFAULT_DEX_NETWORK,
)
from servers import get_client

logger = logging.getLogger(__name__)


# ============================================
# MENU DISPLAY
# ============================================

async def handle_swap_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle swap quote - interactive menu"""

    # Initialize quote params in context if not exists
    if "quote_swap_params" not in context.user_data:
        defaults = get_dex_swap_defaults(context.user_data)
        context.user_data["quote_swap_params"] = defaults

    # Set state to allow text input
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

    # Build header
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
    help_text += r"*⌨️ Or Type Directly*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"
    help_text += r"`trading_pair side amount [slippage]`" + "\n"
    help_text += r"*Example:* `SOL\-USDC BUY 1\.5`" + "\n"

    # Build keyboard
    keyboard = [
        [
            InlineKeyboardButton(
                f"{params.get('connector', 'jupiter')}",
                callback_data="dex:quote_set_connector"
            ),
            InlineKeyboardButton(
                f"{params.get('network', 'solana-mainnet-beta')}",
                callback_data="dex:quote_set_network"
            )
        ],
        [
            InlineKeyboardButton(
                f"{params.get('trading_pair', 'SOL-USDC')}",
                callback_data="dex:quote_set_pair"
            ),
            InlineKeyboardButton(
                f"{params.get('side', 'BUY')}",
                callback_data="dex:quote_toggle_side"
            )
        ],
        [
            InlineKeyboardButton(
                f"{params.get('amount', '1.0')}",
                callback_data="dex:quote_set_amount"
            ),
            InlineKeyboardButton(
                f"{params.get('slippage', '1.0')}%",
                callback_data="dex:quote_set_slippage"
            )
        ],
        [
            InlineKeyboardButton("💰 Get Quote", callback_data="dex:quote_get_confirm"),
            InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")
        ]
    ]

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


# ============================================
# PARAMETER HANDLERS
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


# ============================================
# QUOTE EXECUTION
# ============================================

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

        client = await get_client()

        result = await client.gateway_swap.get_swap_quote(
            connector=connector,
            network=network,
            trading_pair=trading_pair,
            side=side,
            amount=Decimal(amount),
            slippage_pct=Decimal(slippage)
        )

        if result is None:
            raise ValueError("Gateway returned no quote data. The swap may not be available for this pair/network.")

        # Save parameters for quick trading
        set_dex_last_swap(context.user_data, {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "slippage": slippage
        })

        # Update quote_swap_params
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

        keyboard = [[InlineKeyboardButton("« Back to DEX Trading", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            quote_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error getting quote: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get quote: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


# ============================================
# TEXT INPUT PROCESSORS
# ============================================

async def process_swap_quote(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap quote from text input"""
    try:
        parts = user_input.split()

        if len(parts) < 3:
            raise ValueError("Need at least: trading_pair side amount\n"
                           "Optional: connector network trading_pair side amount [slippage]")

        # Parse with all params or use defaults
        if len(parts) >= 5:
            connector = parts[0]
            network = parts[1]
            trading_pair = parts[2]
            side = parts[3].upper()
            amount = parts[4]
            slippage = parts[5] if len(parts) > 5 else "1.0"
        else:
            network = DEFAULT_DEX_NETWORK
            connector = get_dex_connector(context.user_data, network)
            trading_pair = parts[0]
            side = parts[1].upper()
            amount = parts[2]
            slippage = parts[3] if len(parts) > 3 else "1.0"

        client = await get_client()

        result = await client.gateway_swap.get_swap_quote(
            connector=connector,
            network=network,
            trading_pair=trading_pair,
            side=side,
            amount=Decimal(amount),
            slippage_pct=Decimal(slippage)
        )

        if result is None:
            raise ValueError("Gateway returned no quote data.")

        # Save params
        set_dex_last_swap(context.user_data, {
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


async def process_quote_set_connector(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process quote set connector input"""
    try:
        params = context.user_data.get("quote_swap_params", {})
        params["connector"] = user_input.strip()

        # Auto-update network
        connector = params["connector"]
        if connector in ["jupiter", "meteora", "raydium"]:
            params["network"] = "solana-mainnet-beta"
        elif connector == "uniswap":
            params["network"] = "ethereum-mainnet"

        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Connector set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
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
        params["connector"] = get_dex_connector(context.user_data, network)

        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Network set to: {network}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
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

        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Trading pair set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
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

        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Amount set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
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

        context.user_data["dex_state"] = "swap_quote"

        success_msg = escape_markdown_v2(f"✅ Slippage set to: {user_input}%")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_quote_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting slippage: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set slippage: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
