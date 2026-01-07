"""
CEX Trading command handlers - Centralized Exchange trading

Supports:
- Spot & Perpetual exchanges (Binance, Bybit, etc.)
- Place orders (Market/Limit)
- Set leverage & position mode
- Search orders & positions
- Quick trading with saved parameters
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from utils.auth import restricted, hummingbot_api_required
from utils.telegram_formatters import format_error_message
from handlers import clear_all_input_states

logger = logging.getLogger(__name__)


def clear_cex_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear all CEX-related state from user context.
    Call this when starting other commands to prevent state pollution.
    """
    context.user_data.pop("cex_state", None)
    context.user_data.pop("trade_params", None)
    context.user_data.pop("place_order_params", None)  # Legacy
    context.user_data.pop("current_positions", None)
    context.user_data.pop("cex_previous_state", None)
    context.user_data.pop("trade_menu_message_id", None)
    context.user_data.pop("trade_menu_chat_id", None)


@restricted
@hummingbot_api_required
async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /trade command - CEX trading interface with order books

    Usage:
        /trade - Show trading menu with limit/market orders
    """
    from .trade import handle_trade

    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    # Get the appropriate message object for replies
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        logger.error("No message object available for trade_command")
        return

    # Send "typing" status
    await msg.reply_chat_action("typing")

    # Show unified trade menu
    await handle_trade(update, context)


@restricted
async def cex_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks for CEX trading operations"""
    from .menu import cancel_cex_loading_task
    from .trade import (
        handle_trade,
        handle_trade_refresh,
        handle_trade_toggle_side,
        handle_trade_toggle_type,
        handle_trade_toggle_position,
        handle_trade_set_connector,
        handle_trade_connector_select,
        handle_trade_set_pair,
        handle_trade_set_amount,
        handle_trade_set_price,
        handle_trade_set_leverage,
        handle_trade_toggle_pos_mode,
        handle_trade_get_quote,
        handle_trade_execute,
        handle_close,
    )
    from .orders import handle_search_orders, handle_cancel_order, handle_confirm_cancel_order
    from .positions import (
        handle_positions,
        handle_trade_position,
        handle_close_position,
        handle_confirm_close_position,
    )

    query = update.callback_query
    await query.answer()

    await query.message.reply_chat_action("typing")

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        # Cancel any pending menu loading task when navigating away
        if action not in ("main_menu", "trade"):
            cancel_cex_loading_task(context)

        # Main menu / trade
        if action in ("main_menu", "trade"):
            await handle_trade(update, context)
        elif action == "trade_refresh":
            await handle_trade_refresh(update, context)

        # Trade parameter handlers
        elif action == "trade_toggle_side":
            await handle_trade_toggle_side(update, context)
        elif action == "trade_toggle_type":
            await handle_trade_toggle_type(update, context)
        elif action == "trade_toggle_position":
            await handle_trade_toggle_position(update, context)
        elif action == "trade_set_connector":
            await handle_trade_set_connector(update, context)
        elif action.startswith("trade_connector_"):
            connector_name = action.replace("trade_connector_", "")
            await handle_trade_connector_select(update, context, connector_name)
        elif action == "trade_set_pair":
            await handle_trade_set_pair(update, context)
        elif action == "trade_set_amount":
            await handle_trade_set_amount(update, context)
        elif action == "trade_set_price":
            await handle_trade_set_price(update, context)
        elif action == "trade_get_quote":
            await handle_trade_get_quote(update, context)
        elif action == "trade_execute":
            await handle_trade_execute(update, context)

        # Leverage & Position mode (perpetual)
        elif action == "trade_set_leverage":
            await handle_trade_set_leverage(update, context)
        elif action == "trade_toggle_pos_mode":
            await handle_trade_toggle_pos_mode(update, context)

        # Orders
        elif action == "search_orders":
            await handle_search_orders(update, context, status="OPEN")
        elif action == "search_all":
            await handle_search_orders(update, context, status="ALL")
        elif action == "search_filled":
            await handle_search_orders(update, context, status="FILLED")
        elif action == "search_cancelled":
            await handle_search_orders(update, context, status="CANCELLED")
        elif action.startswith("cancel_order:"):
            order_index = int(action.split(":")[1])
            await handle_cancel_order(update, context, order_index)
        elif action.startswith("confirm_cancel:"):
            order_index = int(action.split(":")[1])
            await handle_confirm_cancel_order(update, context, order_index)

        # Positions
        elif action == "positions":
            await handle_positions(update, context)
        elif action.startswith("trade_position:"):
            position_index = int(action.split(":")[1])
            await handle_trade_position(update, context, position_index)
        elif action.startswith("close_position:"):
            position_index = int(action.split(":")[1])
            await handle_close_position(update, context, position_index)
        elif action.startswith("confirm_close:"):
            position_index = int(action.split(":")[1])
            await handle_confirm_close_position(update, context, position_index)

        # Close
        elif action == "close":
            await handle_close(update, context)

        else:
            await query.message.reply_text(f"Unknown action: {action}")

    except Exception as e:
        logger.error(f"Error in CEX callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        await query.message.reply_text(error_message, parse_mode="MarkdownV2")


@restricted
async def cex_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user text input for CEX trading operations"""
    from .trade import (
        process_trade,
        process_trade_set_pair,
        process_trade_set_amount,
        process_trade_set_price,
        process_trade_set_leverage,
    )

    cex_state = context.user_data.get("cex_state")

    logger.info(f"CEX message handler called. State: {cex_state}, Message: {update.message.text if update.message else 'No message'}")

    if not cex_state:
        logger.info("No cex_state found, returning")
        return

    user_input = update.message.text.strip()
    logger.info(f"Processing input for state: {cex_state}, input: {user_input}")

    try:
        # Only remove state for completing operations
        if cex_state in ["trade"]:
            context.user_data.pop("cex_state", None)
            logger.info(f"Removed cex_state for completing operation: {cex_state}")

        if cex_state == "trade":
            await process_trade(update, context, user_input)
        elif cex_state == "trade_set_pair":
            await process_trade_set_pair(update, context, user_input)
        elif cex_state == "trade_set_amount":
            await process_trade_set_amount(update, context, user_input)
        elif cex_state == "trade_set_price":
            await process_trade_set_price(update, context, user_input)
        elif cex_state == "trade_set_leverage":
            await process_trade_set_leverage(update, context, user_input)
        else:
            await update.message.reply_text(f"Unknown state: {cex_state}")

    except Exception as e:
        logger.error(f"Error processing CEX input: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to process input: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


def get_cex_callback_handler():
    """Get the callback query handler for CEX trading"""
    return CallbackQueryHandler(
        cex_callback_handler,
        pattern="^cex:"
    )


def get_cex_message_handler():
    """Returns the message handler for CEX trading text input.

    Returns a tuple of (handler, group) where group=0 is higher priority.
    The handler only processes messages when cex_state is set.
    """
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        cex_message_handler
    )
