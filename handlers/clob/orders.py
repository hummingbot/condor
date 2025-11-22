"""
CLOB Trading - Orders search functionality
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_error_message, escape_markdown_v2

logger = logging.getLogger(__name__)


async def handle_search_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str = "OPEN") -> None:
    """Handle search orders operation"""
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

        # Search for orders with specified status
        if status == "OPEN":
            # Use get_active_orders for real-time open orders from exchange
            result = await client.trading.get_active_orders(limit=100)
            status_label = "Open Orders"
        elif status == "ALL":
            result = await client.trading.search_orders(limit=100)
            status_label = "All Orders"
        else:
            result = await client.trading.search_orders(
                status=status,
                limit=100
            )
            status_label = f"{status.title()} Orders"

        orders = result.get("data", [])

        if not orders:
            message = f"🔍 *{escape_markdown_v2(status_label)}*\n\nNo orders found\\."
        else:
            from utils.telegram_formatters import format_orders_table
            orders_table = format_orders_table(orders)
            message = f"🔍 *{escape_markdown_v2(status_label)}* \\({len(orders)} found\\)\n\n```\n{orders_table}\n```"

        keyboard = [
            [
                InlineKeyboardButton("Open Orders", callback_data="clob:search_orders"),
                InlineKeyboardButton("All Orders", callback_data="clob:search_all"),
            ],
            [
                InlineKeyboardButton("Filled", callback_data="clob:search_filled"),
                InlineKeyboardButton("Cancelled", callback_data="clob:search_cancelled")
            ],
            [InlineKeyboardButton("« Back", callback_data="clob:main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.reply_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error searching orders: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to search orders: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")
