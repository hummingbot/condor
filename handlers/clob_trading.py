"""
CLOB Trading command handler - Centralized Limit Order Book trading

Supports:
- Spot & Perpetual exchanges (Binance, Bybit, etc.)
- Place orders (Market/Limit)
- Set leverage & position mode
- Search orders & positions
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
    get_account,
    get_last_clob_params,
    set_last_clob_params,
)

logger = logging.getLogger(__name__)


# ============================================
# MAIN CLOB TRADING COMMAND
# ============================================

@restricted
async def clob_trading_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /clob_trading command - CLOB trading interface with quick access

    Usage:
        /clob_trading - Show trading menu with quick actions
    """
    # Clear any config state to prevent interference
    clear_config_state(context)

    # Clear any DEX state to prevent interference
    context.user_data.pop("dex_state", None)

    # Send "typing" status
    await update.message.reply_chat_action("typing")

    # Show main CLOB trading menu
    await show_clob_menu(update, context)


async def show_clob_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display main CLOB trading menu with quick trading options and overview"""

    last_params = get_last_clob_params(context.user_data)
    account = get_account(context.user_data)

    # Build header with account info
    header = f"🏦 *CLOB Trading*\n\n"
    header += f"📋 Account: `{escape_markdown_v2(account)}`\n\n"

    # Try to fetch quick overview of positions and orders
    try:
        from servers import server_manager

        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if enabled_servers:
            server_name = enabled_servers[0]
            client = await server_manager.get_client(server_name)

            # Get positions and orders in parallel
            positions_result = await client.trading.get_positions(limit=5)
            orders_result = await client.trading.search_orders(status="OPEN", limit=5)

            positions = positions_result.get("data", [])
            orders = orders_result.get("orders", [])

            # Show positions summary
            if positions:
                from utils.telegram_formatters import format_positions_table
                positions_table = format_positions_table(positions)
                header += f"📊 *Open Positions* \\({len(positions)}\\)\n```\n{positions_table}```\n"
            else:
                header += "📊 *Open Positions*\n_No open positions_\n\n"

            # Show orders summary
            if orders:
                from utils.telegram_formatters import format_orders_table
                orders_table = format_orders_table(orders)
                header += f"📋 *Open Orders* \\({len(orders)}\\)\n```\n{orders_table}```\n"
            else:
                header += "📋 *Open Orders*\n_No open orders_\n\n"

    except Exception as e:
        logger.error(f"Error fetching overview data: {e}", exc_info=True)
        header += "_Could not fetch positions/orders overview_\n\n"

    header += "Select an action:"

    # Create keyboard with main operations
    keyboard = [
        [
            InlineKeyboardButton("📝 Place Order", callback_data="clob:place_order"),
            InlineKeyboardButton("⚙️ Set Leverage", callback_data="clob:leverage")
        ],
        [
            InlineKeyboardButton("🔍 Orders Details", callback_data="clob:search_orders"),
            InlineKeyboardButton("📊 Positions Details", callback_data="clob:positions")
        ],
        [
            InlineKeyboardButton("🔧 Change Account", callback_data="clob:change_account"),
            InlineKeyboardButton("❓ Help", callback_data="clob:help")
        ]
    ]

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
# PLACE ORDER
# ============================================

async def handle_place_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle place order operation with customizable parameters"""

    # Initialize order params in context if not exists
    if "place_order_params" not in context.user_data:
        last_params = get_last_clob_params(context.user_data)
        context.user_data["place_order_params"] = {
            "connector": last_params.get("connector", "binance_perpetual"),
            "trading_pair": last_params.get("trading_pair", "BTC-USDT"),
            "side": "BUY",
            "order_type": "MARKET",
            "position_mode": "OPEN",
            "amount": "$10",
            "price": "88000",
        }

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
        account = get_account(context.user_data)

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
        set_last_clob_params(context.user_data, {
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
# SET LEVERAGE
# ============================================

async def handle_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle set leverage operation"""
    help_text = (
        r"⚙️ *Set Leverage & Position Mode*" + "\n\n"
        r"Reply with configuration:" + "\n\n"
        r"`connector [trading_pair] [position_mode] [leverage]`" + "\n\n"
        r"*Examples:*" + "\n"
        r"`binance_perpetual BTC\-USDT HEDGE 10`" + "\n"
        r"`bybit_perpetual \_ ONE\-WAY 20`" + "\n"
        r"`binance_perpetual BTC\-USDT \_ 5`" + "\n\n"
        r"*Parameters:*" + "\n"
        r"• connector: Exchange name" + "\n"
        r"• trading\_pair: Required for leverage \(use \_ to skip\)" + "\n"
        r"• position\_mode: HEDGE or ONE\-WAY \(use \_ to skip\)" + "\n"
        r"• leverage: Integer value \(e\.g\. 10\)"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="clob:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "leverage"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# SEARCH ORDERS
# ============================================

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
        if status == "ALL":
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


# ============================================
# GET POSITIONS
# ============================================

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

        await update.callback_query.message.reply_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error getting positions: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get positions: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# CLOSE POSITION
# ============================================

async def handle_close_position(update: Update, context: ContextTypes.DEFAULT_TYPE, position_index: int) -> None:
    """Handle closing a specific position"""
    try:
        positions = context.user_data.get("current_positions", [])

        if position_index >= len(positions):
            error_message = format_error_message("Position not found. Please refresh positions.")
            await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        position = positions[position_index]
        account = get_account(context.user_data)

        # Extract position details
        connector_name = position.get('connector_name', 'N/A')
        trading_pair = position.get('trading_pair', 'N/A')
        amount = position.get('amount', 0)
        side = position.get('position_side') or position.get('side') or position.get('trade_type', 'LONG')

        # Determine the opposite side to close the position
        close_side = "SELL" if side in ["LONG", "BUY"] else "BUY"

        # Confirm with user
        confirm_message = (
            r"⚠️ *Confirm Close Position*" + "\n\n"
            f"Pair: `{escape_markdown_v2(trading_pair)}`\n"
            f"Side: `{escape_markdown_v2(side)}`\n"
            f"Size: `{escape_markdown_v2(str(amount))}`\n"
            f"Connector: `{escape_markdown_v2(connector_name)}`\n\n"
            f"This will place a {close_side} market order to close the position\\."
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm Close", callback_data=f"clob:confirm_close:{position_index}"),
                InlineKeyboardButton("❌ Cancel", callback_data="clob:positions")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.reply_text(
            confirm_message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error preparing to close position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to close position: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


async def handle_confirm_close_position(update: Update, context: ContextTypes.DEFAULT_TYPE, position_index: int) -> None:
    """Confirm and execute closing a position"""
    try:
        positions = context.user_data.get("current_positions", [])

        if position_index >= len(positions):
            error_message = format_error_message("Position not found. Please refresh positions.")
            await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        position = positions[position_index]
        account = get_account(context.user_data)

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
            amount=float(amount),
            order_type="MARKET",
            price=None,
            position_action="CLOSE",
        )

        success_msg = escape_markdown_v2(
            f"✅ Position closed successfully!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Size: {amount}\n"
            f"Close Order: {close_side} MARKET"
        )

        if "order_id" in result:
            success_msg += escape_markdown_v2(f"\nOrder ID: {result['order_id']}")

        await update.callback_query.message.reply_text(success_msg, parse_mode="MarkdownV2")

        # Return to positions view
        await handle_positions(update, context)

    except Exception as e:
        logger.error(f"Error closing position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to close position: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# CHANGE ACCOUNT
# ============================================

async def handle_change_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle change account operation"""
    help_text = (
        r"🔧 *Change Trading Account*" + "\n\n"
        r"Enter account name:" + "\n\n"
        r"`<account_name>`" + "\n\n"
        r"*Example:*" + "\n"
        r"`master_account`"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="clob:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "change_account"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# HELP
# ============================================

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display help information"""

    help_text = (
        r"❓ *CLOB Trading Help*" + "\n\n"
        r"*Place Order:*" + "\n"
        r"Market or limit orders on centralized exchanges\." + "\n"
        r"Supports both spot and perpetual markets\." + "\n\n"
        r"*Set Leverage:*" + "\n"
        r"Configure leverage and position mode for perpetual trading\." + "\n\n"
        r"*Search Orders & Positions:*" + "\n"
        r"View your open orders, order history, and current positions\." + "\n\n"
        r"*Account Management:*" + "\n"
        r"Default account is `master_account`\. Change it in settings if needed\."
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="clob:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# CALLBACK QUERY HANDLER
# ============================================

@restricted
async def clob_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks for CLOB trading operations"""
    query = update.callback_query
    await query.answer()

    await query.message.reply_chat_action("typing")

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        if action == "main_menu":
            await show_clob_menu(update, context)
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
        elif action == "order_set_pair":
            await handle_order_set_pair(update, context)
        elif action == "order_set_amount":
            await handle_order_set_amount(update, context)
        elif action == "order_set_price":
            await handle_order_set_price(update, context)
        elif action == "order_execute":
            await handle_order_execute(update, context)
        elif action == "leverage":
            await handle_leverage(update, context)
        elif action == "search_orders":
            await handle_search_orders(update, context, status="OPEN")
        elif action == "search_all":
            await handle_search_orders(update, context, status="ALL")
        elif action == "search_filled":
            await handle_search_orders(update, context, status="FILLED")
        elif action == "search_cancelled":
            await handle_search_orders(update, context, status="CANCELLED")
        elif action == "positions":
            await handle_positions(update, context)
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
        elif action == "help":
            await show_help(update, context)
        else:
            await query.message.reply_text(f"Unknown action: {action}")

    except Exception as e:
        logger.error(f"Error in CLOB callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        await query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# MESSAGE HANDLER FOR USER INPUT
# ============================================

@restricted
async def clob_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user text input for CLOB trading operations"""
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


# ============================================
# PROCESSING FUNCTIONS
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

        account = get_account(context.user_data)

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
        set_last_clob_params(context.user_data, {
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


async def process_leverage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process set leverage user input"""
    try:
        parts = user_input.split()

        if len(parts) < 1:
            raise ValueError("Missing required parameter: connector")

        connector_name = parts[0]
        trading_pair = parts[1] if len(parts) > 1 and parts[1] != "_" else None
        position_mode = parts[2].upper() if len(parts) > 2 and parts[2] != "_" else None
        leverage = int(parts[3]) if len(parts) > 3 else None

        account = get_account(context.user_data)

        if position_mode and position_mode not in ["HEDGE", "ONE-WAY"]:
            raise ValueError("position_mode must be HEDGE or ONE-WAY")
        if leverage is not None and leverage <= 0:
            raise ValueError("leverage must be a positive integer")
        if leverage and not trading_pair:
            raise ValueError("trading_pair is required when setting leverage")
        if not position_mode and not leverage:
            raise ValueError("At least one of position_mode or leverage must be specified")

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers available")

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        results = {}

        if position_mode:
            position_mode_result = await client.trading.set_position_mode(
                account_name=account,
                connector_name=connector_name,
                position_mode=position_mode
            )
            results["position_mode"] = position_mode_result

        if leverage is not None:
            leverage_result = await client.trading.set_leverage(
                account_name=account,
                connector_name=connector_name,
                trading_pair=trading_pair,
                leverage=leverage,
            )
            results["leverage"] = leverage_result

        config_info = escape_markdown_v2(
            f"✅ Configuration updated successfully!\n\n"
            f"Connector: {connector_name}\n"
            f"Account: {account}"
        )

        if trading_pair:
            config_info += escape_markdown_v2(f"\nPair: {trading_pair}")
        if position_mode:
            config_info += escape_markdown_v2(f"\nPosition Mode: {position_mode}")
        if leverage:
            config_info += escape_markdown_v2(f"\nLeverage: {leverage}x")

        await update.message.reply_text(config_info, parse_mode="MarkdownV2")

    except ValueError as e:
        error_message = format_error_message(str(e))
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
    except Exception as e:
        logger.error(f"Error setting configuration: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to update configuration: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_change_account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_name: str
) -> None:
    """Process change account user input"""
    try:
        from handlers.config.trading_context import set_account

        set_account(context.user_data, account_name)

        success_msg = escape_markdown_v2(
            f"✅ Trading account changed to: {account_name}\n\n"
            f"This will be used for all future trades."
        )

        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error changing account: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to change account: {str(e)}")
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


# ============================================
# HELPER: Get message handler
# ============================================

def get_clob_message_handler():
    """Returns the message handler for CLOB trading text input"""
    async def debug_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"Message handler triggered! user_data keys: {list(context.user_data.keys())}")
        logger.info(f"clob_state value: {context.user_data.get('clob_state')}")
        await clob_message_handler(update, context)

    return MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        debug_wrapper
    )
