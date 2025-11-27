"""
CLOB Trading command handlers - Centralized Limit Order Book trading

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

from utils.auth import restricted
from utils.telegram_formatters import format_error_message
from handlers import clear_all_input_states

logger = logging.getLogger(__name__)


def clear_clob_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear all CLOB-related state from user context.
    Call this when starting other commands to prevent state pollution.
    """
    context.user_data.pop("clob_state", None)
    context.user_data.pop("place_order_params", None)
    context.user_data.pop("current_positions", None)
    context.user_data.pop("clob_previous_state", None)


@restricted
async def clob_trading_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /clob_trading command - CLOB trading interface with quick access

    Usage:
        /clob_trading - Show trading menu with quick actions
    """
    from .menu import show_clob_menu

    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    # Get the appropriate message object for replies
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        logger.error("No message object available for clob_trading_command")
        return

    # Send "typing" status
    await msg.reply_chat_action("typing")

    # Show main CLOB trading menu
    await show_clob_menu(update, context)


@restricted
async def clob_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks for CLOB trading operations"""
    from .menu import show_clob_menu, handle_close
    from .place_order import (
        handle_place_order,
        handle_repeat_last,
        handle_order_toggle_side,
        handle_order_toggle_type,
        handle_order_toggle_position,
        handle_order_set_connector,
        handle_select_connector,
        handle_order_set_pair,
        handle_order_set_amount,
        handle_order_set_price,
        handle_order_execute,
        handle_order_help,
    )
    from .leverage import handle_leverage, handle_toggle_position_mode
    from .orders import handle_search_orders, handle_cancel_order, handle_confirm_cancel_order
    from .positions import (
        handle_positions,
        handle_trade_position,
        handle_close_position,
        handle_confirm_close_position,
    )
    from .account import handle_change_account

    query = update.callback_query
    await query.answer()

    await query.message.reply_chat_action("typing")

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        if action == "main_menu":
            await show_clob_menu(update, context)
        elif action == "repeat_last":
            await handle_repeat_last(update, context)
        elif action == "place_order":
            await handle_place_order(update, context)
        elif action == "order_toggle_side":
            await handle_order_toggle_side(update, context)
        elif action == "order_toggle_type":
            await handle_order_toggle_type(update, context)
        elif action == "order_toggle_position":
            await handle_order_toggle_position(update, context)
        elif action == "order_set_connector":
            await handle_order_set_connector(update, context)
        elif action.startswith("select_connector:"):
            # Extract connector name from callback data
            connector_name = action.split(":", 1)[1]
            await handle_select_connector(update, context, connector_name)
        elif action == "order_set_pair":
            await handle_order_set_pair(update, context)
        elif action == "order_set_amount":
            await handle_order_set_amount(update, context)
        elif action == "order_set_price":
            await handle_order_set_price(update, context)
        elif action == "order_execute":
            await handle_order_execute(update, context)
        elif action == "order_help":
            await handle_order_help(update, context)
        elif action == "leverage":
            await handle_leverage(update, context)
        elif action.startswith("toggle_pos_mode:"):
            # Extract connector name from callback data
            connector_name = action.split(":", 1)[1]
            await handle_toggle_position_mode(update, context, connector_name)
        elif action == "search_orders":
            await handle_search_orders(update, context, status="OPEN")
        elif action == "search_all":
            await handle_search_orders(update, context, status="ALL")
        elif action == "search_filled":
            await handle_search_orders(update, context, status="FILLED")
        elif action == "search_cancelled":
            await handle_search_orders(update, context, status="CANCELLED")
        elif action.startswith("cancel_order:"):
            # Extract order index from callback data
            order_index = int(action.split(":")[1])
            await handle_cancel_order(update, context, order_index)
        elif action.startswith("confirm_cancel:"):
            # Extract order index from callback data
            order_index = int(action.split(":")[1])
            await handle_confirm_cancel_order(update, context, order_index)
        elif action == "positions":
            await handle_positions(update, context)
        elif action.startswith("trade_position:"):
            # Extract position index from callback data
            position_index = int(action.split(":")[1])
            await handle_trade_position(update, context, position_index)
        elif action.startswith("close_position:"):
            # Extract position index from callback data
            position_index = int(action.split(":")[1])
            await handle_close_position(update, context, position_index)
        elif action.startswith("confirm_close:"):
            # Extract position index from callback data
            position_index = int(action.split(":")[1])
            await handle_confirm_close_position(update, context, position_index)
        elif action == "change_account":
            await handle_change_account(update, context)
        elif action == "close":
            await handle_close(update, context)
        else:
            await query.message.reply_text(f"Unknown action: {action}")

    except Exception as e:
        logger.error(f"Error in CLOB callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        await query.message.reply_text(error_message, parse_mode="MarkdownV2")


@restricted
async def clob_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user text input for CLOB trading operations"""
    from .place_order import (
        process_place_order,
        process_order_set_connector,
        process_order_set_pair,
        process_order_set_amount,
        process_order_set_price,
    )
    from .leverage import process_leverage
    from .account import process_change_account

    clob_state = context.user_data.get("clob_state")

    logger.info(f"CLOB message handler called. State: {clob_state}, Message: {update.message.text if update.message else 'No message'}")

    if not clob_state:
        logger.info("No clob_state found, returning")
        return

    user_input = update.message.text.strip()
    logger.info(f"Processing input for state: {clob_state}, input: {user_input}")

    try:
        # Only remove state for operations that complete (not parameter setting)
        if clob_state in ["place_order", "leverage", "change_account"]:
            context.user_data.pop("clob_state", None)
            logger.info(f"Removed clob_state for completing operation: {clob_state}")

        if clob_state == "place_order":
            await process_place_order(update, context, user_input)
        elif clob_state == "order_set_connector":
            logger.info("Processing order_set_connector")
            await process_order_set_connector(update, context, user_input)
        elif clob_state == "order_set_pair":
            logger.info("Processing order_set_pair")
            await process_order_set_pair(update, context, user_input)
        elif clob_state == "order_set_amount":
            logger.info("Processing order_set_amount")
            await process_order_set_amount(update, context, user_input)
        elif clob_state == "order_set_price":
            logger.info("Processing order_set_price")
            await process_order_set_price(update, context, user_input)
        elif clob_state == "leverage":
            await process_leverage(update, context, user_input)
        elif clob_state == "change_account":
            await process_change_account(update, context, user_input)
        else:
            await update.message.reply_text(f"Unknown state: {clob_state}")

    except Exception as e:
        logger.error(f"Error processing CLOB input: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to process input: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


def get_clob_callback_handler():
    """Get the callback query handler for CLOB trading"""
    return CallbackQueryHandler(
        clob_callback_handler,
        pattern="^clob:"
    )


def get_clob_message_handler():
    """Returns the message handler for CLOB trading text input.

    Returns a tuple of (handler, group) where group=0 is higher priority.
    The handler only processes messages when clob_state is set.
    """
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        clob_message_handler
    )
