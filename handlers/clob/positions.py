"""
CLOB Trading - Positions management
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_error_message, escape_markdown_v2, format_number
from handlers.config.user_preferences import get_clob_account

logger = logging.getLogger(__name__)


async def handle_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle get positions operation"""
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

        # Get all positions
        result = await client.trading.get_positions(limit=100)

        positions = result.get("data", [])

        if not positions:
            message = r"📊 *Open Positions*" + "\n\n" + r"No positions found\."
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="clob:positions"),
                    InlineKeyboardButton("« Back", callback_data="clob:main_menu")
                ]
            ]
        else:
            from utils.telegram_formatters import format_positions_table
            positions_table = format_positions_table(positions)
            message = r"📊 *Open Positions* \(" + escape_markdown_v2(str(len(positions))) + r" found\)" + "\n\n" + r"```" + "\n" + positions_table + "\n" + r"```"

            # Store positions data in context for later use
            context.user_data["current_positions"] = positions

            # Build keyboard with close buttons for each position
            keyboard = []

            # Add close position buttons (max 5 positions shown)
            for i, pos in enumerate(positions[:5]):
                pair = pos.get('trading_pair', 'N/A')
                side = pos.get('position_side') or pos.get('side') or pos.get('trade_type', 'LONG')

                # Format button label
                button_label = f"❌ Close {pair} {side}"
                callback_data = f"clob:close_position:{i}"

                keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])

            if len(positions) > 5:
                keyboard.append([InlineKeyboardButton("⋯ Show More Positions", callback_data="clob:positions_list")])

            # Add refresh and back buttons
            keyboard.append([
                InlineKeyboardButton("🔄 Refresh", callback_data="clob:positions"),
                InlineKeyboardButton("« Back", callback_data="clob:main_menu")
            ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error getting positions: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get positions: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


async def handle_trade_position(update: Update, context: ContextTypes.DEFAULT_TYPE, position_index: int) -> None:
    """Handle quick trade for a specific position - opens place order menu with position details pre-filled"""
    from .place_order import show_place_order_menu

    try:
        positions = context.user_data.get("current_positions", [])

        if position_index >= len(positions):
            error_message = format_error_message("Position not found. Please refresh positions.")
            await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
            return

        position = positions[position_index]

        # Extract position details
        connector_name = position.get('connector_name', 'binance_perpetual')
        trading_pair = position.get('trading_pair', 'BTC-USDT')
        amount = position.get('amount', 0)
        side = position.get('position_side') or position.get('side') or position.get('trade_type', 'LONG')
        entry_price = position.get('entry_price', 0)

        # Determine the opposite side for closing
        opposite_side = "SELL" if side in ["LONG", "BUY"] else "BUY"

        # Pre-fill order parameters with position details
        # Default to closing the position (opposite side)
        context.user_data["place_order_params"] = {
            "connector": connector_name,
            "trading_pair": trading_pair,
            "side": opposite_side,  # Default to close side
            "order_type": "MARKET",
            "position_mode": "CLOSE",
            "amount": str(amount),
            "price": str(entry_price),
        }

        # Set state to allow text input for direct order placement
        context.user_data["clob_state"] = "place_order"

        # Show the place order menu with pre-filled parameters (edit current message)
        await show_place_order_menu(update, context, send_new=False)

    except Exception as e:
        logger.error(f"Error preparing trade for position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to prepare trade: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


async def handle_close_position(update: Update, context: ContextTypes.DEFAULT_TYPE, position_index: int) -> None:
    """Handle closing a specific position"""
    try:
        positions = context.user_data.get("current_positions", [])

        if position_index >= len(positions):
            error_message = format_error_message("Position not found. Please refresh positions.")
            await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
            return

        position = positions[position_index]

        # Extract position details
        connector_name = position.get('connector_name', 'N/A')
        trading_pair = position.get('trading_pair', 'N/A')
        amount = position.get('amount', 0)
        entry_price = position.get('entry_price', 0)
        unrealized_pnl = position.get('unrealized_pnl', 0)
        side = position.get('position_side') or position.get('side') or position.get('trade_type', 'LONG')

        # Determine the opposite side to close the position
        close_side = "SELL" if side in ["LONG", "BUY"] else "BUY"

        # Format side display
        side_upper = side.upper() if side else 'N/A'
        if side_upper in ('LONG', 'BUY'):
            side_display = 'LONG'
        elif side_upper in ('SHORT', 'SELL'):
            side_display = 'SHRT'
        else:
            side_display = side[:4] if len(side) > 4 else side

        # Calculate position value
        try:
            position_value = abs(float(amount) * float(entry_price))
            value_str = format_number(position_value).replace('$', '')[:7]
        except (ValueError, TypeError):
            value_str = "N/A"

        # Format PnL
        try:
            pnl_float = float(unrealized_pnl)
            if pnl_float >= 0:
                pnl_str = f"+{pnl_float:.2f}"[:7]
            else:
                pnl_str = f"{pnl_float:.2f}"[:7]
        except (ValueError, TypeError):
            pnl_str = str(unrealized_pnl)[:7]

        # Truncate display values
        connector_display = connector_name[:9] if len(connector_name) > 9 else connector_name
        pair_display = trading_pair[:9] if len(trading_pair) > 9 else trading_pair

        # Build table
        table_content = f"{'Connector':<10} {'Pair':<10} {'Side':<4} {'Value':<7} {'PnL($)':>7}\n"
        table_content += f"{'─'*10} {'─'*10} {'─'*4} {'─'*7} {'─'*7}\n"
        table_content += f"{connector_display:<10} {pair_display:<10} {side_display:<4} {value_str:<7} {pnl_str:>7}\n"

        # Confirm with user
        confirm_message = (
            r"⚠️ *Confirm Close Position*" + "\n\n"
            f"```\n{table_content}```\n"
            f"This will place a {close_side} market order to close the position\\."
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm Close", callback_data=f"clob:confirm_close:{position_index}"),
                InlineKeyboardButton("❌ Cancel", callback_data="clob:positions")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            confirm_message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error preparing to close position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to close position: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


async def handle_confirm_close_position(update: Update, context: ContextTypes.DEFAULT_TYPE, position_index: int) -> None:
    """Confirm and execute closing a position"""
    from ._shared import invalidate_cache

    try:
        positions = context.user_data.get("current_positions", [])

        if position_index >= len(positions):
            error_message = format_error_message("Position not found. Please refresh positions.")
            await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
            return

        position = positions[position_index]
        account = get_clob_account(context.user_data)

        # Extract position details
        connector_name = position.get('connector_name', 'N/A')
        trading_pair = position.get('trading_pair', 'N/A')
        amount = position.get('amount', 0)
        side = position.get('position_side') or position.get('side') or position.get('trade_type', 'LONG')

        # Determine the opposite side to close the position
        close_side = "SELL" if side in ["LONG", "BUY"] else "BUY"

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers available")

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        # Place market order to close position
        result = await client.trading.place_order(
            account_name=account,
            connector_name=connector_name,
            trading_pair=trading_pair,
            trade_type=close_side,
            amount=abs(float(amount)),
            order_type="MARKET",
            price=None,
            position_action="CLOSE",
        )

        # Invalidate cache after successful position close
        invalidate_cache(context.user_data, "balances", "positions")

        success_msg = escape_markdown_v2(
            f"✅ Position closed successfully!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Size: {amount}\n"
            f"Close Order: {close_side} MARKET"
        )

        if "order_id" in result:
            success_msg += escape_markdown_v2(f"\nOrder ID: {result['order_id']}")

        keyboard = [
            [
                InlineKeyboardButton("📊 View Positions", callback_data="clob:positions"),
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
        logger.error(f"Error closing position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to close position: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
