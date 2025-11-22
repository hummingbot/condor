"""
DEX Swap History and Status functionality

Provides:
- Swap status check
- Swap history search with improved formatting
- Explorer links for transactions
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from ._shared import (
    get_gateway_client,
    get_explorer_url,
    get_explorer_name,
    format_swap_summary,
    format_swap_detail,
    get_status_emoji,
    _format_amount,
)

logger = logging.getLogger(__name__)


# ============================================
# SWAP STATUS
# ============================================

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


async def process_swap_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tx_hash: str
) -> None:
    """Process swap status check"""
    try:
        client = await get_gateway_client()

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


# ============================================
# SWAP HISTORY
# ============================================

async def handle_swap_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle swap history search"""
    try:
        client = await get_gateway_client()

        if not hasattr(client, 'gateway_swap'):
            error_message = format_error_message("Gateway swap not available")
            await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        result = await client.gateway_swap.search_swaps(limit=10)
        swaps = result.get("data", [])
        pagination = result.get("pagination", {})

        if not swaps:
            message = r"🔍 *Swap History*" + "\n\n" + r"No swaps found\."
            keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.callback_query.message.reply_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            return

        # Build formatted swap list
        total_count = pagination.get("total_count", len(swaps))
        message = rf"🔍 *Recent Swaps* \({len(swaps)} of {total_count}\)" + "\n\n"

        for i, swap in enumerate(swaps, 1):
            message += _format_swap_line(swap, i) + "\n\n"

        # Add keyboard with back button
        keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.reply_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"Error searching swaps: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to search swaps: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


def _format_swap_line(swap: dict, index: int) -> str:
    """Format a single swap line for the history list

    Args:
        swap: Swap data dictionary
        index: Line number

    Returns:
        Formatted and escaped markdown string
    """
    # Extract fields
    pair = swap.get('trading_pair', 'N/A')
    side = swap.get('side', 'N/A')
    status = swap.get('status', 'UNKNOWN')
    network = swap.get('network', '')
    tx_hash = swap.get('transaction_hash', '')
    connector = swap.get('connector', '')

    # Format amounts
    input_amount = swap.get('input_amount')
    output_amount = swap.get('output_amount')
    base_token = swap.get('base_token', '')
    quote_token = swap.get('quote_token', '')
    price = swap.get('price')

    # Status emoji
    status_emoji = get_status_emoji(status)

    # Build amount string based on side
    if input_amount is not None and output_amount is not None:
        if side == 'BUY':
            # Bought base_token with quote_token
            amount_str = f"{_format_amount(output_amount)} {base_token} for {_format_amount(input_amount)} {quote_token}"
        else:
            # Sold base_token for quote_token
            amount_str = f"{_format_amount(input_amount)} {base_token} for {_format_amount(output_amount)} {quote_token}"
    elif output_amount is not None:
        amount_str = f"{_format_amount(output_amount)}"
    elif input_amount is not None:
        amount_str = f"{_format_amount(input_amount)}"
    else:
        amount_str = "N/A"

    # Format price if available
    price_str = ""
    if price is not None and price > 0:
        # Price is typically base/quote
        price_str = f" @ {_format_amount(price)}"

    # Format timestamp
    timestamp = swap.get('timestamp', '')
    time_str = ""
    if timestamp:
        if 'T' in timestamp:
            date_part = timestamp.split('T')[0]
            time_part = timestamp.split('T')[1].split('.')[0] if '.' in timestamp.split('T')[1] else timestamp.split('T')[1].split('+')[0]
            time_str = f"{date_part} {time_part[:5]}"

    # Build the formatted line
    line_parts = []

    # Line 1: Status emoji, pair, side
    line1 = f"{status_emoji} *{escape_markdown_v2(pair)}* {escape_markdown_v2(side)}"
    line_parts.append(line1)

    # Line 2: Amount details
    line2 = f"   {escape_markdown_v2(amount_str)}{escape_markdown_v2(price_str)}"
    line_parts.append(line2)

    # Line 3: Connector, time, and explorer link
    meta_parts = []
    if connector:
        meta_parts.append(escape_markdown_v2(connector))
    if time_str:
        meta_parts.append(escape_markdown_v2(time_str))

    # Add explorer link if tx_hash available
    if tx_hash and network:
        explorer_url = get_explorer_url(tx_hash, network)
        explorer_name = get_explorer_name(network)
        if explorer_url:
            # Escape special markdown characters in URL
            escaped_url = explorer_url.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]").replace("(", "\\(").replace(")", "\\)")
            meta_parts.append(f"[🔗 {explorer_name}]({escaped_url})")

    if meta_parts:
        line3 = f"   {' • '.join(meta_parts)}"
        line_parts.append(line3)

    return "\n".join(line_parts)
