"""
CLOB Trading - Orders search functionality
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_error_message, escape_markdown_v2
from handlers.config.user_preferences import get_clob_account

logger = logging.getLogger(__name__)


async def handle_search_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str = "OPEN") -> None:
    """Handle search orders operation"""
    try:
        from servers import server_manager

        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            error_message = format_error_message("No enabled API servers available")
            await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
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

        # Store orders for cancel operations
        context.user_data["current_orders"] = orders

        if not orders:
            message = f"🔍 *{escape_markdown_v2(status_label)}*\n\nNo orders found\\."
            keyboard = []
        else:
            from utils.telegram_formatters import format_orders_table
            orders_table = format_orders_table(orders)
            message = f"🔍 *{escape_markdown_v2(status_label)}* \\({len(orders)} found\\)\n\n```\n{orders_table}\n```"

            # Build keyboard with cancel buttons for open orders
            keyboard = []
            if status == "OPEN":
                for i, order in enumerate(orders[:5]):
                    pair = order.get('trading_pair', 'N/A')
                    side = order.get('trade_type', order.get('side', 'N/A'))
                    order_type = order.get('order_type', 'N/A')
                    button_label = f"❌ Cancel {pair} {side} {order_type}"
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=f"clob:cancel_order:{i}")])

                if len(orders) > 5:
                    keyboard.append([InlineKeyboardButton("⋯ More Orders", callback_data="clob:orders_list")])

        # Filter buttons
        keyboard.append([
            InlineKeyboardButton("Open Orders", callback_data="clob:search_orders"),
            InlineKeyboardButton("All Orders", callback_data="clob:search_all"),
        ])
        keyboard.append([
            InlineKeyboardButton("Filled", callback_data="clob:search_filled"),
            InlineKeyboardButton("Cancelled", callback_data="clob:search_cancelled")
        ])
        keyboard.append([InlineKeyboardButton("« Back", callback_data="clob:main_menu")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error searching orders: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to search orders: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


async def handle_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_index: int) -> None:
    """Handle cancel order confirmation"""
    try:
        orders = context.user_data.get("current_orders", [])

        if order_index >= len(orders):
            error_message = format_error_message("Order not found. Please refresh orders.")
            await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
            return

        order = orders[order_index]

        # Extract order details
        connector_name = order.get('connector_name', 'N/A')
        trading_pair = order.get('trading_pair', 'N/A')
        client_order_id = order.get('client_order_id') or order.get('order_id', 'N/A')
        side = order.get('trade_type', order.get('side', 'N/A'))
        order_type = order.get('order_type', 'N/A')
        amount = order.get('amount', 'N/A')
        price = order.get('price', 'N/A')

        confirm_message = (
            r"⚠️ *Confirm Cancel Order*" + "\n\n"
            f"Pair: `{escape_markdown_v2(str(trading_pair))}`\n"
            f"Side: `{escape_markdown_v2(str(side))}`\n"
            f"Type: `{escape_markdown_v2(str(order_type))}`\n"
            f"Amount: `{escape_markdown_v2(str(amount))}`\n"
            f"Price: `{escape_markdown_v2(str(price))}`\n"
            f"Connector: `{escape_markdown_v2(str(connector_name))}`\n"
            f"Order ID: `{escape_markdown_v2(str(client_order_id))}`\n\n"
            r"This will cancel the order\."
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm Cancel", callback_data=f"clob:confirm_cancel:{order_index}"),
                InlineKeyboardButton("❌ Back", callback_data="clob:search_orders")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            confirm_message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error preparing to cancel order: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to cancel order: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


async def handle_confirm_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_index: int) -> None:
    """Confirm and execute order cancellation"""
    try:
        orders = context.user_data.get("current_orders", [])

        if order_index >= len(orders):
            error_message = format_error_message("Order not found. Please refresh orders.")
            await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
            return

        order = orders[order_index]
        account = get_clob_account(context.user_data)

        # Extract order details
        connector_name = order.get('connector_name', 'N/A')
        trading_pair = order.get('trading_pair', 'N/A')
        client_order_id = order.get('client_order_id') or order.get('order_id')

        if not client_order_id:
            raise ValueError("Order ID not found")

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers available")

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        # Cancel the order
        result = await client.trading.cancel_order(
            account_name=account,
            connector_name=connector_name,
            client_order_id=client_order_id,
        )

        success_msg = escape_markdown_v2(
            f"✅ Order cancelled successfully!\n\n"
            f"Pair: {trading_pair}\n"
            f"Connector: {connector_name}\n"
            f"Order ID: {client_order_id}"
        )

        keyboard = [
            [
                InlineKeyboardButton("🔍 View Orders", callback_data="clob:search_orders"),
                InlineKeyboardButton("« Back to Menu", callback_data="clob:main_menu")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            success_msg,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error cancelling order: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to cancel order: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
