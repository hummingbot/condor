"""
CLOB Trading - Place Order functionality
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_error_message, escape_markdown_v2
from handlers.config.user_preferences import (
    get_clob_account,
    get_clob_last_order,
    set_clob_last_order,
    get_clob_order_defaults,
)

logger = logging.getLogger(__name__)


async def handle_repeat_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle repeat last trade - opens place order menu with last order params pre-filled"""
    last_params = get_clob_last_order(context.user_data)

    if not last_params or not last_params.get("connector"):
        error_message = format_error_message("No previous trade found. Place an order first.")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")
        return

    # Pre-fill order parameters with last trade
    context.user_data["place_order_params"] = {
        "connector": last_params.get("connector", "binance_perpetual"),
        "trading_pair": last_params.get("trading_pair", "BTC-USDT"),
        "side": last_params.get("side", "BUY"),
        "order_type": last_params.get("order_type", "MARKET"),
        "position_mode": "OPEN",
        "amount": "$10",  # Default amount, user can change
        "price": "88000",
    }

    # Set state to allow text input for direct order placement
    context.user_data["clob_state"] = "place_order"

    await show_place_order_menu(update, context)


async def handle_place_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle place order operation with customizable parameters"""

    # Initialize order params in context if not exists
    if "place_order_params" not in context.user_data:
        # Get defaults from user preferences (includes last order params)
        defaults = get_clob_order_defaults(context.user_data)
        context.user_data["place_order_params"] = defaults

    # Set state to allow text input for direct order placement
    context.user_data["clob_state"] = "place_order"

    await show_place_order_menu(update, context)


async def show_place_order_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, send_new: bool = False) -> None:
    """Display the place order configuration menu with interactive buttons

    Args:
        update: The update object
        context: The context object
        send_new: If True, always send a new message instead of editing
    """
    params = context.user_data.get("place_order_params", {})

    # Build header with detailed explanation
    help_text = r"📝 *Place Order*" + "\n\n"

    help_text += r"*Configure your order using the buttons below or type parameters directly\.*" + "\n\n"

    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*📊 Current Configuration*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

    help_text += f"🔌 *Connector:* `{escape_markdown_v2(params.get('connector', 'N/A'))}`\n"
    help_text += f"💱 *Trading Pair:* `{escape_markdown_v2(params.get('trading_pair', 'N/A'))}`\n"
    help_text += f"📈 *Side:* `{escape_markdown_v2(params.get('side', 'N/A'))}`\n"
    help_text += f"📋 *Order Type:* `{escape_markdown_v2(params.get('order_type', 'N/A'))}`\n"
    help_text += f"🎯 *Position Mode:* `{escape_markdown_v2(params.get('position_mode', 'N/A'))}`\n"
    help_text += f"💰 *Amount:* `{escape_markdown_v2(params.get('amount', 'N/A'))}`\n"

    if params.get('order_type') == 'LIMIT' or params.get('order_type') == 'LIMIT_MAKER':
        help_text += f"💵 *Price:* `{escape_markdown_v2(params.get('price', 'N/A'))}`\n"

    help_text += "\n" + r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*🎮 Interactive Configuration*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"
    help_text += r"Click buttons below to configure each parameter:" + "\n"
    help_text += r"• *Toggle buttons* cycle through options" + "\n"
    help_text += r"• *Input buttons* prompt for new values" + "\n\n"

    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*⌨️ Or Type Directly*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"
    help_text += r"Reply with order parameters:" + "\n\n"
    help_text += r"`connector trading_pair side amount [order_type] [price] [position_action]`" + "\n\n"
    help_text += r"*Examples:*" + "\n"
    help_text += r"`binance_perpetual BTC\-USDT BUY 0\.01 MARKET`" + "\n"
    help_text += r"`binance BTC\-USDT SELL $100 LIMIT 45000 CLOSE`" + "\n\n"

    # Build keyboard with parameter buttons
    keyboard = []

    # Row 1: Connector and Trading Pair (just show values)
    keyboard.append([
        InlineKeyboardButton(
            f"{params.get('connector', 'binance_perpetual')}",
            callback_data="clob:order_set_connector"
        ),
        InlineKeyboardButton(
            f"{params.get('trading_pair', 'BTC-USDT')}",
            callback_data="clob:order_set_pair"
        )
    ])

    # Row 2: Side and Order Type (toggle buttons - just show values)
    keyboard.append([
        InlineKeyboardButton(
            f"{params.get('side', 'BUY')}",
            callback_data="clob:order_toggle_side"
        ),
        InlineKeyboardButton(
            f"{params.get('order_type', 'MARKET')}",
            callback_data="clob:order_toggle_type"
        )
    ])

    # Row 3: Position Mode (toggle) and Amount (just show values)
    keyboard.append([
        InlineKeyboardButton(
            f"{params.get('position_mode', 'OPEN')}",
            callback_data="clob:order_toggle_position"
        ),
        InlineKeyboardButton(
            f"{params.get('amount', '$10')}",
            callback_data="clob:order_set_amount"
        )
    ])

    # Row 4: Price (only if LIMIT order - just show value)
    if params.get('order_type') in ['LIMIT', 'LIMIT_MAKER']:
        keyboard.append([
            InlineKeyboardButton(
                f"{params.get('price', '88000')}",
                callback_data="clob:order_set_price"
            )
        ])

    # Row 5: Execute and Cancel
    keyboard.append([
        InlineKeyboardButton("✅ Execute Order", callback_data="clob:order_execute"),
        InlineKeyboardButton("« Cancel", callback_data="clob:main_menu")
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


# ============================================
# PLACE ORDER - PARAMETER HANDLERS
# ============================================

async def handle_order_toggle_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between BUY and SELL"""
    params = context.user_data.get("place_order_params", {})
    current_side = params.get("side", "BUY")
    params["side"] = "SELL" if current_side == "BUY" else "BUY"
    await show_place_order_menu(update, context)


async def handle_order_toggle_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between MARKET, LIMIT, and LIMIT_MAKER"""
    params = context.user_data.get("place_order_params", {})
    current_type = params.get("order_type", "MARKET")

    if current_type == "MARKET":
        params["order_type"] = "LIMIT"
        # Fetch current market price when switching to LIMIT
        try:
            from servers import server_manager
            servers = server_manager.list_servers()
            enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

            if enabled_servers:
                server_name = enabled_servers[0]
                client = await server_manager.get_client(server_name)

                connector_name = params.get("connector", "binance_perpetual")
                trading_pair = params.get("trading_pair", "BTC-USDT")

                prices = await client.market_data.get_prices(
                    connector_name=connector_name,
                    trading_pairs=trading_pair
                )
                current_price = prices["prices"][trading_pair]
                params["price"] = str(current_price)
        except Exception as e:
            logger.error(f"Error fetching market price: {e}", exc_info=True)
            params["price"] = "88000"  # Fallback to default
    elif current_type == "LIMIT":
        params["order_type"] = "LIMIT_MAKER"
    else:  # LIMIT_MAKER
        params["order_type"] = "MARKET"

    await show_place_order_menu(update, context)


async def handle_order_toggle_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between OPEN and CLOSE position modes"""
    params = context.user_data.get("place_order_params", {})
    current_mode = params.get("position_mode", "OPEN")
    params["position_mode"] = "CLOSE" if current_mode == "OPEN" else "OPEN"
    await show_place_order_menu(update, context)


async def handle_order_set_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input connector"""
    help_text = (
        r"📝 *Set Connector*" + "\n\n"
        r"Enter the exchange connector name:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`binance_perpetual`" + "\n"
        r"`binance`" + "\n"
        r"`bybit_perpetual`" + "\n"
        r"`bybit`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="clob:place_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "order_set_connector"
    context.user_data["clob_previous_state"] = "place_order"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_order_set_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input trading pair"""
    help_text = (
        r"📝 *Set Trading Pair*" + "\n\n"
        r"Enter the trading pair:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`BTC\-USDT`" + "\n"
        r"`ETH\-USDT`" + "\n"
        r"`SOL\-USDT`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="clob:place_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "order_set_pair"
    context.user_data["clob_previous_state"] = "place_order"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_order_set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input amount"""
    help_text = (
        r"📝 *Set Amount*" + "\n\n"
        r"Enter the amount to trade:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`0\.01` \- Trade 0\.01 of base token" + "\n"
        r"`$100` \- Trade $100 worth"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="clob:place_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "order_set_amount"
    context.user_data["clob_previous_state"] = "place_order"

    logger.info(f"Set clob_state to: order_set_amount, user_data: {context.user_data.get('clob_state')}")

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_order_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input price"""
    help_text = (
        r"📝 *Set Price*" + "\n\n"
        r"Enter the limit price:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`45000`" + "\n"
        r"`88000\.50`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="clob:place_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "order_set_price"
    context.user_data["clob_previous_state"] = "place_order"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_order_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the order with current parameters"""
    try:
        params = context.user_data.get("place_order_params", {})
        account = get_clob_account(context.user_data)

        connector_name = params.get("connector")
        trading_pair = params.get("trading_pair")
        trade_type = params.get("side")
        amount = params.get("amount")
        order_type = params.get("order_type")
        price = params.get("price")
        position_action = params.get("position_mode", "OPEN")

        # Validate required parameters
        if not all([connector_name, trading_pair, trade_type, amount, order_type]):
            raise ValueError("Missing required parameters")

        if order_type in ["LIMIT", "LIMIT_MAKER"] and not price:
            raise ValueError("Price is required for LIMIT orders")

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers available")

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        # Handle USD amount conversion
        if "$" in amount:
            usd_value = float(amount.replace("$", ""))

            # Get current market price for conversion
            prices = await client.market_data.get_prices(
                connector_name=connector_name,
                trading_pairs=trading_pair
            )
            current_price = prices["prices"][trading_pair]

            # Convert USD to base token amount
            amount_float = usd_value / current_price
        else:
            amount_float = float(amount)

        result = await client.trading.place_order(
            account_name=account,
            connector_name=connector_name,
            trading_pair=trading_pair,
            trade_type=trade_type,
            amount=amount_float,
            order_type=order_type,
            price=float(price) if price and order_type in ["LIMIT", "LIMIT_MAKER"] else None,
            position_action=position_action,
        )

        # Save parameters for quick trading
        set_clob_last_order(context.user_data, {
            "connector": connector_name,
            "trading_pair": trading_pair,
            "side": trade_type,
            "order_type": order_type,
        })

        # Update place_order_params with the values used (don't clear, keep user's preferences)
        context.user_data["place_order_params"] = {
            "connector": connector_name,
            "trading_pair": trading_pair,
            "side": trade_type,
            "order_type": order_type,
            "position_mode": position_action,
            "amount": amount,  # Keep the original amount string (with $ if it had it)
            "price": price if price else "88000",
        }

        order_info = escape_markdown_v2(
            f"✅ Order placed successfully!\n\n"
            f"Connector: {connector_name}\n"
            f"Pair: {trading_pair}\n"
            f"Side: {trade_type}\n"
            f"Amount: {amount_float}\n"
            f"Type: {order_type}\n"
            f"Position: {position_action}\n"
            f"Account: {account}"
        )

        if price and order_type in ["LIMIT", "LIMIT_MAKER"]:
            order_info += escape_markdown_v2(f"\nPrice: {price}")

        if "order_id" in result:
            order_info += escape_markdown_v2(f"\nOrder ID: {result['order_id']}")

        # Create keyboard with back to menu button
        keyboard = [[InlineKeyboardButton("« Back to CLOB Trading", callback_data="clob:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.reply_text(
            order_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error executing order: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to execute order: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# PROCESSING FUNCTIONS (for text input)
# ============================================

async def process_place_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process place order user input"""
    try:
        parts = user_input.split()

        if len(parts) < 4:
            raise ValueError("Missing required parameters. Need at least: connector trading_pair side amount")

        connector_name = parts[0]
        trading_pair = parts[1]
        trade_type = parts[2].upper()
        amount = parts[3]
        order_type = parts[4].upper() if len(parts) > 4 and parts[4] != "_" else "MARKET"
        price = parts[5] if len(parts) > 5 and parts[5] != "_" else None
        position_action = parts[6].upper() if len(parts) > 6 and parts[6] != "_" else "OPEN"

        account = get_clob_account(context.user_data)

        if trade_type not in ["BUY", "SELL"]:
            raise ValueError("trade_type must be BUY or SELL")
        if order_type not in ["MARKET", "LIMIT", "LIMIT_MAKER"]:
            raise ValueError("order_type must be MARKET, LIMIT, or LIMIT_MAKER")
        if order_type in ["LIMIT", "LIMIT_MAKER"] and not price:
            raise ValueError("price is required for LIMIT orders")
        if position_action not in ["OPEN", "CLOSE"]:
            raise ValueError("position_action must be OPEN or CLOSE")

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers available")

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        # Handle USD amount conversion
        if "$" in amount and price is None:
            prices = await client.market_data.get_prices(
                connector_name=connector_name,
                trading_pairs=trading_pair
            )
            price_value = prices["prices"][trading_pair]
            amount_float = float(amount.replace("$", "")) / price_value
        else:
            amount_float = float(amount)

        result = await client.trading.place_order(
            account_name=account,
            connector_name=connector_name,
            trading_pair=trading_pair,
            trade_type=trade_type,
            amount=amount_float,
            order_type=order_type,
            price=float(price) if price else None,
            position_action=position_action,
        )

        # Save parameters for quick trading
        set_clob_last_order(context.user_data, {
            "connector": connector_name,
            "trading_pair": trading_pair,
            "side": trade_type,
            "order_type": order_type,
        })

        order_info = escape_markdown_v2(
            f"✅ Order placed successfully!\n\n"
            f"Connector: {connector_name}\n"
            f"Pair: {trading_pair}\n"
            f"Side: {trade_type}\n"
            f"Amount: {amount_float}\n"
            f"Type: {order_type}\n"
            f"Account: {account}"
        )

        if "order_id" in result:
            order_info += escape_markdown_v2(f"\nOrder ID: {result['order_id']}")

        # Create keyboard with back to menu button
        keyboard = [[InlineKeyboardButton("« Back to CLOB Trading", callback_data="clob:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            order_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except ValueError as e:
        error_message = format_error_message(str(e))
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
    except Exception as e:
        logger.error(f"Error placing order: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to place order: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_order_set_connector(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process order set connector input"""
    try:
        params = context.user_data.get("place_order_params", {})
        params["connector"] = user_input.strip()

        # Restore place_order state for text input
        context.user_data["clob_state"] = "place_order"

        success_msg = escape_markdown_v2(f"✅ Connector set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to order menu by sending a new message
        await show_place_order_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting connector: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set connector: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_order_set_pair(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process order set trading pair input"""
    try:
        params = context.user_data.get("place_order_params", {})
        params["trading_pair"] = user_input.strip()

        # Restore place_order state for text input
        context.user_data["clob_state"] = "place_order"

        success_msg = escape_markdown_v2(f"✅ Trading pair set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to order menu by sending a new message
        await show_place_order_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting trading pair: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set trading pair: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_order_set_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process order set amount input"""
    try:
        params = context.user_data.get("place_order_params", {})
        params["amount"] = user_input.strip()

        # Restore place_order state for text input
        context.user_data["clob_state"] = "place_order"

        success_msg = escape_markdown_v2(f"✅ Amount set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to order menu by sending a new message
        await show_place_order_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting amount: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set amount: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_order_set_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process order set price input"""
    try:
        params = context.user_data.get("place_order_params", {})
        params["price"] = user_input.strip()

        # Restore place_order state for text input
        context.user_data["clob_state"] = "place_order"

        success_msg = escape_markdown_v2(f"✅ Price set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to order menu by sending a new message
        await show_place_order_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting price: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set price: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
