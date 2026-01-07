"""
CEX Trading - Orders search functionality
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_error_message, escape_markdown_v2
from handlers.config.user_preferences import get_clob_account

logger = logging.getLogger(__name__)


async def handle_search_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str = "ALL") -> None:
    """Handle search orders operation

    Status options:
    - ALL: All orders with open orders section at top (default)
    - FILLED: Only filled orders
    - CANCELLED: Only cancelled orders
    """
    try:
        from config_manager import get_client
        import asyncio

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, context=context)

        keyboard = []

        if status == "ALL":
            # Fetch open orders (from active endpoint) and all orders in parallel
            async def get_open():
                try:
                    result = await client.trading.get_active_orders(limit=50)
                    return result.get("data", [])
                except Exception as e:
                    logger.warning(f"Error fetching open orders: {e}")
                    return []

            async def get_all():
                try:
                    result = await client.trading.search_orders(limit=100)
                    return result.get("data", [])
                except Exception as e:
                    logger.warning(f"Error fetching all orders: {e}")
                    return []

            open_orders, all_orders = await asyncio.gather(get_open(), get_all())

            # Store open orders for cancel operations
            context.user_data["current_orders"] = open_orders

            # Build set of truly open order IDs for status correction
            open_order_ids = {
                o.get('client_order_id') or o.get('order_id')
                for o in open_orders
            }

            # Correct stale "OPEN" status in all_orders based on actual open orders
            for order in all_orders:
                order_id = order.get('client_order_id') or order.get('order_id')
                if order.get('status') == 'OPEN' and order_id not in open_order_ids:
                    order['status'] = 'FILLED'  # Most likely filled

            # Build message with sections
            sections = []

            # Open orders section with cancel buttons
            if open_orders:
                from utils.telegram_formatters import format_orders_table
                open_table = format_orders_table(open_orders[:10])
                sections.append(f"*🟢 Open Orders* \\({len(open_orders)}\\)\n```\n{open_table}\n```")

                # Cancel buttons for open orders
                for i, order in enumerate(open_orders[:3]):
                    pair = order.get('trading_pair', 'N/A')
                    side = order.get('trade_type', order.get('side', 'N/A'))
                    button_label = f"❌ Cancel {pair} {side}"
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=f"cex:cancel_order:{i}")])
            else:
                sections.append("*🟢 Open Orders*\n_No open orders_")

            # All orders section
            if all_orders:
                from utils.telegram_formatters import format_orders_table
                all_table = format_orders_table(all_orders)
                sections.append(f"\n*📋 All Orders* \\({len(all_orders)}\\)\n```\n{all_table}\n```")

            message = "\n".join(sections)

        else:
            # Specific status filter (FILLED, CANCELLED)
            result = await client.trading.search_orders(status=status, limit=100)
            orders = result.get("data", [])
            context.user_data["current_orders"] = []
            status_label = f"{status.title()} Orders"
            emoji = "✅" if status == "FILLED" else "❌" if status == "CANCELLED" else "📋"

            if not orders:
                message = f"{emoji} *{escape_markdown_v2(status_label)}*\n\n_No orders found_"
            else:
                from utils.telegram_formatters import format_orders_table
                orders_table = format_orders_table(orders)
                message = f"{emoji} *{escape_markdown_v2(status_label)}* \\({len(orders)}\\)\n\n```\n{orders_table}\n```"

        # Filter buttons - highlight current filter
        def btn(label, action, current):
            prefix = "• " if current else ""
            return InlineKeyboardButton(f"{prefix}{label}", callback_data=f"cex:{action}")

        keyboard.append([
            btn("All", "search_orders", status == "ALL"),
            btn("Filled", "search_filled", status == "FILLED"),
            btn("Cancelled", "search_cancelled", status == "CANCELLED"),
        ])
        keyboard.append([InlineKeyboardButton("« Back", callback_data="cex:trade")])

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
                InlineKeyboardButton("✅ Confirm Cancel", callback_data=f"cex:confirm_cancel:{order_index}"),
                InlineKeyboardButton("❌ Back", callback_data="cex:search_orders")
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
    from ._shared import invalidate_cache

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

        from config_manager import get_client

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, context=context)

        # Cancel the order
        result = await client.trading.cancel_order(
            account_name=account,
            connector_name=connector_name,
            client_order_id=client_order_id,
        )

        # Invalidate cache after successful order cancellation
        invalidate_cache(context.user_data, "balances", "orders")

        success_msg = escape_markdown_v2(
            f"✅ Order cancelled successfully!\n\n"
            f"Pair: {trading_pair}\n"
            f"Connector: {connector_name}\n"
            f"Order ID: {client_order_id}"
        )

        keyboard = [
            [
                InlineKeyboardButton("🔍 View Orders", callback_data="cex:search_orders"),
                InlineKeyboardButton("« Back", callback_data="cex:trade")
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
