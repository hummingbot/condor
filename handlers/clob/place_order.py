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


def _format_price(price: float) -> str:
    """Format price with appropriate precision, removing trailing zeros"""
    if price >= 10000:
        return f"${price:,.0f}"
    elif price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}".rstrip('0').rstrip('.')
    elif price >= 0.0001:
        return f"${price:.6f}".rstrip('0').rstrip('.')
    else:
        return f"${price:.8f}".rstrip('0').rstrip('.')


async def handle_repeat_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle repeat last trade - opens place order menu with last order params pre-filled"""
    last_params = get_clob_last_order(context.user_data)

    if not last_params or not last_params.get("connector"):
        error_message = format_error_message("No previous trade found. Place an order first.")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
        return

    # Pre-fill order parameters with last trade (use persisted values)
    context.user_data["place_order_params"] = {
        "connector": last_params.get("connector", "binance_perpetual"),
        "trading_pair": last_params.get("trading_pair", "BTC-USDT"),
        "side": last_params.get("side", "BUY"),
        "order_type": last_params.get("order_type", "MARKET"),
        "position_mode": last_params.get("position_mode", "OPEN"),
        "amount": last_params.get("amount", "$10"),  # Use persisted amount
        "price": last_params.get("price", "88000"),
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


async def show_place_order_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, send_new: bool = False, show_help: bool = False) -> None:
    """Display the place order configuration menu with interactive buttons

    Args:
        update: The update object
        context: The context object
        send_new: If True, always send a new message instead of editing
        show_help: If True, show detailed help instead of balances
    """
    params = context.user_data.get("place_order_params", {})
    connector_name = params.get("connector", "binance_perpetual")

    if show_help:
        # Show detailed help view
        help_text = r"📖 *Place Order \- Help*" + "\n\n"

        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
        help_text += r"*📊 Current Configuration*" + "\n"
        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

        help_text += f"🔌 *Connector:* `{escape_markdown_v2(params.get('connector', 'N/A'))}`\n"
        help_text += f"💱 *Trading Pair:* `{escape_markdown_v2(params.get('trading_pair', 'N/A'))}`\n"
        help_text += f"📈 *Side:* `{escape_markdown_v2(params.get('side', 'N/A'))}`\n"
        help_text += f"📋 *Order Type:* `{escape_markdown_v2(params.get('order_type', 'N/A'))}`\n"
        help_text += f"🎯 *Position Mode:* `{escape_markdown_v2(params.get('position_mode', 'N/A'))}`\n"
        help_text += f"💰 *Amount:* `{escape_markdown_v2(params.get('amount', 'N/A'))}`\n"

        if params.get('order_type') in ['LIMIT', 'LIMIT_MAKER']:
            help_text += f"💵 *Price:* `{escape_markdown_v2(params.get('price', 'N/A'))}`\n"

        help_text += "\n" + r"━━━━━━━━━━━━━━━━━━━━" + "\n"
        help_text += r"*🎮 Button Guide*" + "\n"
        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

        help_text += r"• *Row 1:* Connector \& Trading Pair" + "\n"
        help_text += r"  _Tap to change exchange or pair_" + "\n\n"

        help_text += r"• *Row 2:* Side \& Order Type" + "\n"
        help_text += r"  _Tap to toggle BUY/SELL or MARKET/LIMIT_" + "\n\n"

        help_text += r"• *Row 3:* Position Mode \& Amount" + "\n"
        help_text += r"  _OPEN/CLOSE position, set trade size_" + "\n\n"

        help_text += r"• *Row 4:* Price \(LIMIT orders only\)" + "\n"
        help_text += r"  _Set your limit price_" + "\n\n"

        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
        help_text += r"*⌨️ Text Input Format*" + "\n"
        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

        help_text += r"`connector pair side amount [type] [price] [action]`" + "\n\n"

        help_text += r"*Parameters:*" + "\n"
        help_text += r"• `connector` \- Exchange \(binance, binance\_perpetual\)" + "\n"
        help_text += r"• `pair` \- Trading pair \(BTC\-USDT\)" + "\n"
        help_text += r"• `side` \- BUY or SELL" + "\n"
        help_text += r"• `amount` \- Quantity or $USD value" + "\n"
        help_text += r"• `type` \- MARKET, LIMIT, LIMIT\_MAKER" + "\n"
        help_text += r"• `price` \- Limit price \(required for LIMIT\)" + "\n"
        help_text += r"• `action` \- OPEN or CLOSE position" + "\n"

    else:
        # Show main view with balances and trading rules
        from ._shared import get_cex_balances, get_trading_rules, format_trading_rules_info, get_positions

        help_text = r"📝 *Place Order*" + "\n\n"

        trading_pair = params.get("trading_pair", "BTC-USDT")

        # Fetch and display balances and trading rules
        try:
            from servers import get_client

            client = await get_client()
            account = get_clob_account(context.user_data)

            # Fetch CEX balances with caching
            cex_balances = await get_cex_balances(
                context.user_data,
                client,
                account
            )

            help_text += f"*💰 Balances on* `{escape_markdown_v2(connector_name)}`\n"

            # Extract balances for the selected connector
            shown = 0
            connector_balances = cex_balances.get(connector_name, [])

            if connector_balances:
                # Calculate total for percentage
                total_value = sum(float(h.get("value", 0)) for h in connector_balances)

                # Sort by value (descending) and filter by >= 0.3% allocation
                sorted_balances = sorted(
                    connector_balances,
                    key=lambda h: float(h.get("value", 0)),
                    reverse=True
                )

                table = "```\n"
                table += f"{'Token':<6} {'Price':<8} {'Value':<8} {'%':>5}\n"
                table += f"{'─'*6} {'─'*8} {'─'*8} {'─'*5}\n"

                for holding in sorted_balances:
                    token = holding.get("token", "")
                    value = float(holding.get("value", 0))
                    units = float(holding.get("units", 0))

                    # Calculate percentage first to filter
                    pct = (value / total_value * 100) if total_value > 0 else 0

                    # Only show if >= 0.3% allocation
                    if pct >= 0.3:
                        # Calculate price
                        price = value / units if units > 0 else 0
                        # Format price
                        if price >= 1000:
                            price_str = f"${price:,.0f}"
                        elif price >= 1:
                            price_str = f"${price:.2f}"
                        elif price >= 0.0001:
                            price_str = f"${price:.4f}"
                        else:
                            price_str = f"${price:.2e}"
                        price_str = price_str[:8]

                        # Format value
                        if value >= 1000:
                            value_str = f"{value/1000:.2f}K"
                        else:
                            value_str = f"{value:.2f}"
                        value_str = value_str[:8]

                        # Format percentage
                        pct_str = f"{pct:.0f}%" if pct >= 10 else f"{pct:.1f}%"

                        # Truncate token name
                        token_display = token[:5] if len(token) > 5 else token

                        table += f"{token_display:<6} {price_str:<8} {value_str:<8} {pct_str:>5}\n"
                        shown += 1

                table += "```"
                help_text += table + "\n"

            if shown == 0:
                help_text += r"_No balances found_" + "\n"

            # Fetch current market price
            current_price = None
            try:
                prices = await client.market_data.get_prices(
                    connector_name=connector_name,
                    trading_pairs=trading_pair
                )
                current_price = prices["prices"].get(trading_pair)
                if current_price:
                    # Store for use in examples
                    context.user_data["current_market_price"] = current_price
            except Exception as e:
                logger.debug(f"Could not fetch current price: {e}")

            # Fetch and display trading rules (with price included)
            trading_rules = await get_trading_rules(
                context.user_data,
                client,
                connector_name
            )

            rules_info = format_trading_rules_info(trading_rules, trading_pair, current_price)
            if rules_info:
                help_text += f"\n📏 *{escape_markdown_v2(trading_pair)}:*\n"
                help_text += f"```\n{rules_info}\n```\n"

            # Show positions for perpetual exchanges
            if "perpetual" in connector_name.lower():
                positions = await get_positions(
                    context.user_data,
                    client,
                    connector_name
                )

                if positions:
                    help_text += f"\n📊 *Positions on* `{escape_markdown_v2(connector_name)}`\n"
                    pos_table = "```\n"
                    pos_table += f"{'Pair':<9} {'Side':<5} {'Size':<8} {'Entry':>8} {'PnL':>8}\n"
                    pos_table += f"{'─'*9} {'─'*5} {'─'*8} {'─'*8} {'─'*8}\n"

                    for pos in positions[:5]:  # Limit to 5 positions
                        pair = pos.get('trading_pair', 'N/A')
                        side = pos.get('position_side') or pos.get('side') or pos.get('trade_type', 'N/A')
                        amount = pos.get('amount', 0)
                        entry_price = pos.get('entry_price', 0)
                        pnl = pos.get('unrealized_pnl', 0)

                        # Format side
                        side_upper = str(side).upper()
                        if side_upper in ('LONG', 'BUY'):
                            side_display = 'LONG'
                        elif side_upper in ('SHORT', 'SELL'):
                            side_display = 'SHORT'
                        else:
                            side_display = side_upper[:5]

                        # Format amount
                        try:
                            amt = float(amount)
                            if abs(amt) >= 1000:
                                amt_str = f"{amt/1000:.2f}K"
                            elif abs(amt) >= 1:
                                amt_str = f"{amt:.2f}"
                            else:
                                amt_str = f"{amt:.4f}"
                            amt_str = amt_str[:8]
                        except (ValueError, TypeError):
                            amt_str = str(amount)[:8]

                        # Format entry price
                        try:
                            entry = float(entry_price)
                            if entry >= 1000:
                                entry_str = f"{entry:,.0f}"
                            elif entry >= 1:
                                entry_str = f"{entry:.2f}"
                            else:
                                entry_str = f"{entry:.4f}"
                            entry_str = entry_str[:8]
                        except (ValueError, TypeError):
                            entry_str = str(entry_price)[:8]

                        # Format PnL
                        try:
                            pnl_float = float(pnl)
                            if pnl_float >= 0:
                                pnl_str = f"+{pnl_float:.2f}"
                            else:
                                pnl_str = f"{pnl_float:.2f}"
                            pnl_str = pnl_str[:8]
                        except (ValueError, TypeError):
                            pnl_str = str(pnl)[:8]

                        # Truncate pair
                        pair_display = pair[:9] if len(pair) > 9 else pair

                        pos_table += f"{pair_display:<9} {side_display:<5} {amt_str:<8} {entry_str:>8} {pnl_str:>8}\n"

                    pos_table += "```"
                    help_text += pos_table + "\n"

                    if len(positions) > 5:
                        help_text += f"_\\+{len(positions) - 5} more positions_\n"

        except Exception as e:
            logger.error(f"Error fetching data: {e}", exc_info=True)
            help_text += r"_Could not fetch data_" + "\n"

        help_text += "\n"
        help_text += r"Use buttons or reply with order parameters:" + "\n\n"
        help_text += r"`connector pair side amount [type] [price] [action]`" + "\n\n"
        help_text += r"*Examples* \(tap to copy\):" + "\n"
        # Use current trading pair in examples, escape for MarkdownV2
        pair_escaped = escape_markdown_v2(trading_pair)
        # First example: MARKET order on current connector
        help_text += f"`{escape_markdown_v2(connector_name)} {pair_escaped} BUY 0\\.01 MARKET`" + "\n"
        # Second example: LIMIT with CLOSE - use dynamic price based on current market
        perp_connector = connector_name if "perpetual" in connector_name.lower() else "binance_perpetual"
        example_price = context.user_data.get("current_market_price", 100)
        # Format example price (slightly below market for a limit sell)
        example_limit = example_price * 1.02  # 2% above for sell limit
        if example_limit >= 1000:
            limit_str = f"{example_limit:.0f}"
        elif example_limit >= 1:
            limit_str = f"{example_limit:.2f}"
        else:
            limit_str = f"{example_limit:.4f}"
        limit_escaped = escape_markdown_v2(limit_str)
        help_text += f"`{escape_markdown_v2(perp_connector)} {pair_escaped} SELL $100 LIMIT {limit_escaped} CLOSE`" + "\n"

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

    # Row 5: Execute, Help/Back toggle, and Cancel
    help_button = (
        InlineKeyboardButton("« Order", callback_data="clob:place_order")
        if show_help else
        InlineKeyboardButton("❓ Help", callback_data="clob:order_help")
    )
    keyboard.append([
        InlineKeyboardButton("✅ Execute", callback_data="clob:order_execute"),
        help_button,
        InlineKeyboardButton("« Menu", callback_data="clob:main_menu")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if send_new or not update.callback_query:
        sent_msg = await update.message.reply_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        # Store the menu message ID for later editing
        context.user_data["place_order_menu_msg_id"] = sent_msg.message_id
        context.user_data["place_order_menu_chat_id"] = sent_msg.chat_id
    else:
        await update.callback_query.message.edit_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        # Store the menu message ID for later editing
        context.user_data["place_order_menu_msg_id"] = update.callback_query.message.message_id
        context.user_data["place_order_menu_chat_id"] = update.callback_query.message.chat_id


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
            from servers import get_client

            client = await get_client()

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
    """Show connector selection keyboard with available CEX connectors"""
    from ._shared import get_available_cex_connectors

    help_text = r"📝 *Select Connector*" + "\n\n" + r"Choose an exchange:"

    # Build keyboard with available CEX connectors
    keyboard = []

    try:
        from servers import get_client

        client = await get_client()

        # Get available CEX connectors
        cex_connectors = await get_available_cex_connectors(context.user_data, client)

        # Create buttons for each CEX connector (max 2 per row)
        row = []
        for connector in cex_connectors:
            row.append(InlineKeyboardButton(
                connector,
                callback_data=f"clob:select_connector:{connector}"
            ))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        if not cex_connectors:
            help_text += "\n\n_No CEX connectors available_"

    except Exception as e:
        logger.error(f"Error fetching connectors: {e}", exc_info=True)
        help_text += "\n\n_Could not fetch available connectors_"

    # Add back button
    keyboard.append([InlineKeyboardButton("« Back", callback_data="clob:place_order")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_select_connector(update: Update, context: ContextTypes.DEFAULT_TYPE, connector_name: str) -> None:
    """Handle connector selection from keyboard"""
    from ._shared import get_trading_rules

    params = context.user_data.get("place_order_params", {})
    params["connector"] = connector_name

    # Fetch and cache trading rules for this connector
    try:
        from servers import get_client

        client = await get_client()
        await get_trading_rules(context.user_data, client, connector_name)

    except Exception as e:
        logger.error(f"Error fetching trading rules: {e}", exc_info=True)

    # Return to place order menu
    await show_place_order_menu(update, context)


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

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="clob:place_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "order_set_pair"
    context.user_data["clob_previous_state"] = "place_order"

    # Send as new message so user can still see the order menu
    prompt_msg = await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )
    # Store prompt message ID for deletion later
    context.user_data["input_prompt_msg_id"] = prompt_msg.message_id


async def handle_order_set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input amount"""
    help_text = (
        r"📝 *Set Amount*" + "\n\n"
        r"Enter the amount to trade:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`0\.01` \- Trade 0\.01 of base token" + "\n"
        r"`$100` \- Trade $100 worth"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="clob:place_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "order_set_amount"
    context.user_data["clob_previous_state"] = "place_order"

    # Send as new message so user can still see the order menu
    prompt_msg = await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )
    # Store prompt message ID for deletion later
    context.user_data["input_prompt_msg_id"] = prompt_msg.message_id


async def handle_order_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input price"""
    help_text = (
        r"📝 *Set Price*" + "\n\n"
        r"Enter the limit price:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`45000`" + "\n"
        r"`88000\.50`"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="clob:place_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["clob_state"] = "order_set_price"
    context.user_data["clob_previous_state"] = "place_order"

    # Send as new message so user can still see the order menu
    prompt_msg = await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )
    # Store prompt message ID for deletion later
    context.user_data["input_prompt_msg_id"] = prompt_msg.message_id


async def handle_order_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed help for place order"""
    await show_place_order_menu(update, context, show_help=True)


async def handle_order_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the order with current parameters"""
    from ._shared import get_trading_rules, validate_order_against_rules, invalidate_cache

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

        from servers import get_client

        client = await get_client()

        # Check if amount is in quote currency (USD)
        is_quote_amount = "$" in amount

        # Handle USD amount conversion
        if is_quote_amount:
            usd_value = float(amount.replace("$", ""))

            # Get current market price for conversion
            prices = await client.market_data.get_prices(
                connector_name=connector_name,
                trading_pairs=trading_pair
            )
            current_price = prices["prices"][trading_pair]

            # Validate against min_notional_size before conversion
            trading_rules = await get_trading_rules(context.user_data, client, connector_name)
            is_valid, error_msg = validate_order_against_rules(
                trading_rules, trading_pair, usd_value, is_quote_amount=True
            )
            if not is_valid:
                raise ValueError(error_msg)

            # Convert USD to base token amount
            amount_float = usd_value / current_price
        else:
            amount_float = float(amount)

            # Validate against min_order_size
            trading_rules = await get_trading_rules(context.user_data, client, connector_name)
            is_valid, error_msg = validate_order_against_rules(
                trading_rules, trading_pair, amount_float, is_quote_amount=False
            )
            if not is_valid:
                raise ValueError(error_msg)

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

        # Invalidate cache after successful order placement
        invalidate_cache(context.user_data, "balances", "orders")

        # Save parameters for quick trading (persist all order params for next session)
        set_clob_last_order(context.user_data, {
            "connector": connector_name,
            "trading_pair": trading_pair,
            "side": trade_type,
            "order_type": order_type,
            "position_mode": position_action,
            "amount": amount,  # Persist the amount (with $ if it had it)
            "price": price if price else "88000",
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

        await update.callback_query.message.edit_text(
            order_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error executing order: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to execute order: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


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

        from servers import get_client

        client = await get_client()

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

        # Save parameters for quick trading (persist all order params for next session)
        set_clob_last_order(context.user_data, {
            "connector": connector_name,
            "trading_pair": trading_pair,
            "side": trade_type,
            "order_type": order_type,
            "position_mode": position_action,
            "amount": amount,  # Persist the amount (with $ if it had it)
            "price": price if price else "88000",
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


async def _cleanup_input_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete prompt message and user input message after processing"""
    try:
        chat_id = update.message.chat_id

        # Delete the prompt message
        prompt_msg_id = context.user_data.pop("input_prompt_msg_id", None)
        if prompt_msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=prompt_msg_id)
            except Exception:
                pass  # Message may already be deleted

        # Delete the user's input message
        try:
            await update.message.delete()
        except Exception:
            pass  # May not have permission
    except Exception as e:
        logger.debug(f"Error cleaning up messages: {e}")


async def _update_order_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update the original order menu message with new values"""
    from ._shared import get_cex_balances, get_trading_rules, format_trading_rules_info, get_positions

    menu_msg_id = context.user_data.get("place_order_menu_msg_id")
    chat_id = context.user_data.get("place_order_menu_chat_id")

    if menu_msg_id and chat_id:
        # Rebuild the menu content and edit the original message
        params = context.user_data.get("place_order_params", {})
        connector_name = params.get("connector", "binance_perpetual")
        trading_pair = params.get("trading_pair", "BTC-USDT")

        help_text = r"📝 *Place Order*" + "\n\n"

        try:
            from servers import get_client
            from handlers.config.user_preferences import get_clob_account

            client = await get_client()
            account = get_clob_account(context.user_data)

            # Fetch CEX balances with caching
            cex_balances = await get_cex_balances(context.user_data, client, account)

            help_text += f"*💰 Balances on* `{escape_markdown_v2(connector_name)}`\n"

            connector_balances = cex_balances.get(connector_name, [])
            shown = 0

            if connector_balances:
                total_value = sum(float(h.get("value", 0)) for h in connector_balances)
                sorted_balances = sorted(connector_balances, key=lambda h: float(h.get("value", 0)), reverse=True)

                table = "```\n"
                table += f"{'Token':<6} {'Price':<8} {'Value':<8} {'%':>5}\n"
                table += f"{'─'*6} {'─'*8} {'─'*8} {'─'*5}\n"

                for holding in sorted_balances:
                    token = holding.get("token", "")
                    value = float(holding.get("value", 0))
                    units = float(holding.get("units", 0))
                    pct = (value / total_value * 100) if total_value > 0 else 0

                    if pct >= 0.3:
                        price = value / units if units > 0 else 0
                        if price >= 1000:
                            price_str = f"${price:,.0f}"
                        elif price >= 1:
                            price_str = f"${price:.2f}"
                        elif price >= 0.0001:
                            price_str = f"${price:.4f}"
                        else:
                            price_str = f"${price:.2e}"
                        price_str = price_str[:8]

                        if value >= 1000:
                            value_str = f"{value/1000:.2f}K"
                        else:
                            value_str = f"{value:.2f}"
                        value_str = value_str[:8]

                        pct_str = f"{pct:.0f}%" if pct >= 10 else f"{pct:.1f}%"
                        token_display = token[:5] if len(token) > 5 else token

                        table += f"{token_display:<6} {price_str:<8} {value_str:<8} {pct_str:>5}\n"
                        shown += 1

                table += "```"
                help_text += table + "\n"

            if shown == 0:
                help_text += r"_No balances found_" + "\n"

            # Fetch current market price
            current_price = None
            try:
                prices = await client.market_data.get_prices(
                    connector_name=connector_name,
                    trading_pairs=trading_pair
                )
                current_price = prices["prices"].get(trading_pair)
                if current_price:
                    context.user_data["current_market_price"] = current_price
            except Exception as e:
                logger.debug(f"Could not fetch current price: {e}")

            # Trading rules (with price included)
            trading_rules = await get_trading_rules(context.user_data, client, connector_name)
            rules_info = format_trading_rules_info(trading_rules, trading_pair, current_price)
            if rules_info:
                help_text += f"\n📏 *{escape_markdown_v2(trading_pair)}:*\n"
                help_text += f"```\n{rules_info}\n```\n"

            # Positions for perpetual exchanges
            if "perpetual" in connector_name.lower():
                positions = await get_positions(context.user_data, client, connector_name)
                if positions:
                    help_text += f"\n📊 *Positions on* `{escape_markdown_v2(connector_name)}`\n"
                    pos_table = "```\n"
                    pos_table += f"{'Pair':<9} {'Side':<5} {'Size':<8} {'Entry':>8} {'PnL':>8}\n"
                    pos_table += f"{'─'*9} {'─'*5} {'─'*8} {'─'*8} {'─'*8}\n"

                    for pos in positions[:5]:
                        pair = pos.get('trading_pair', 'N/A')
                        side = pos.get('position_side') or pos.get('side') or pos.get('trade_type', 'N/A')
                        amount = pos.get('amount', 0)
                        entry_price = pos.get('entry_price', 0)
                        pnl = pos.get('unrealized_pnl', 0)

                        side_upper = str(side).upper()
                        side_display = 'LONG' if side_upper in ('LONG', 'BUY') else 'SHORT' if side_upper in ('SHORT', 'SELL') else side_upper[:5]

                        try:
                            amt = float(amount)
                            amt_str = f"{amt/1000:.2f}K" if abs(amt) >= 1000 else f"{amt:.2f}" if abs(amt) >= 1 else f"{amt:.4f}"
                            amt_str = amt_str[:8]
                        except (ValueError, TypeError):
                            amt_str = str(amount)[:8]

                        try:
                            entry = float(entry_price)
                            entry_str = f"{entry:,.0f}" if entry >= 1000 else f"{entry:.2f}" if entry >= 1 else f"{entry:.4f}"
                            entry_str = entry_str[:8]
                        except (ValueError, TypeError):
                            entry_str = str(entry_price)[:8]

                        try:
                            pnl_float = float(pnl)
                            pnl_str = f"+{pnl_float:.2f}" if pnl_float >= 0 else f"{pnl_float:.2f}"
                            pnl_str = pnl_str[:8]
                        except (ValueError, TypeError):
                            pnl_str = str(pnl)[:8]

                        pair_display = pair[:9] if len(pair) > 9 else pair
                        pos_table += f"{pair_display:<9} {side_display:<5} {amt_str:<8} {entry_str:>8} {pnl_str:>8}\n"

                    pos_table += "```"
                    help_text += pos_table + "\n"
                    if len(positions) > 5:
                        help_text += f"_\\+{len(positions) - 5} more positions_\n"

        except Exception as e:
            logger.error(f"Error fetching data: {e}", exc_info=True)
            help_text += r"_Could not fetch data_" + "\n"

        help_text += "\n"
        help_text += r"Use buttons or reply with order parameters:" + "\n\n"
        help_text += r"`connector pair side amount [type] [price] [action]`" + "\n\n"
        help_text += r"*Examples* \(tap to copy\):" + "\n"
        pair_escaped = escape_markdown_v2(trading_pair)
        help_text += f"`{escape_markdown_v2(connector_name)} {pair_escaped} BUY 0\\.01 MARKET`" + "\n"
        perp_connector = connector_name if "perpetual" in connector_name.lower() else "binance_perpetual"
        # Use dynamic price based on current market
        example_price = context.user_data.get("current_market_price", 100)
        example_limit = example_price * 1.02  # 2% above for sell limit
        if example_limit >= 1000:
            limit_str = f"{example_limit:.0f}"
        elif example_limit >= 1:
            limit_str = f"{example_limit:.2f}"
        else:
            limit_str = f"{example_limit:.4f}"
        limit_escaped = escape_markdown_v2(limit_str)
        help_text += f"`{escape_markdown_v2(perp_connector)} {pair_escaped} SELL $100 LIMIT {limit_escaped} CLOSE`" + "\n"

        # Build keyboard
        keyboard = []
        keyboard.append([
            InlineKeyboardButton(f"{params.get('connector', 'binance_perpetual')}", callback_data="clob:order_set_connector"),
            InlineKeyboardButton(f"{params.get('trading_pair', 'BTC-USDT')}", callback_data="clob:order_set_pair")
        ])
        keyboard.append([
            InlineKeyboardButton(f"{params.get('side', 'BUY')}", callback_data="clob:order_toggle_side"),
            InlineKeyboardButton(f"{params.get('order_type', 'MARKET')}", callback_data="clob:order_toggle_type")
        ])
        keyboard.append([
            InlineKeyboardButton(f"{params.get('position_mode', 'OPEN')}", callback_data="clob:order_toggle_position"),
            InlineKeyboardButton(f"{params.get('amount', '$10')}", callback_data="clob:order_set_amount")
        ])
        if params.get('order_type') in ['LIMIT', 'LIMIT_MAKER']:
            keyboard.append([InlineKeyboardButton(f"{params.get('price', '88000')}", callback_data="clob:order_set_price")])
        keyboard.append([
            InlineKeyboardButton("✅ Execute", callback_data="clob:order_execute"),
            InlineKeyboardButton("❓ Help", callback_data="clob:order_help"),
            InlineKeyboardButton("« Menu", callback_data="clob:main_menu")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=menu_msg_id,
                text=help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error editing menu message: {e}")
            # Fall back to sending new message
            await update.message.reply_text(help_text, parse_mode="MarkdownV2", reply_markup=reply_markup)
    else:
        # No stored message ID, send new
        await show_place_order_menu(update, context, send_new=True)


async def process_order_set_pair(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process order set trading pair input"""
    try:
        params = context.user_data.get("place_order_params", {})
        params["trading_pair"] = user_input.strip().upper()

        # Restore place_order state for text input
        context.user_data["clob_state"] = "place_order"

        # Clean up prompt and input messages
        await _cleanup_input_messages(update, context)

        # Update the original menu message
        await _update_order_menu(update, context)

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

        # Clean up prompt and input messages
        await _cleanup_input_messages(update, context)

        # Update the original menu message
        await _update_order_menu(update, context)

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

        # Clean up prompt and input messages
        await _cleanup_input_messages(update, context)

        # Update the original menu message
        await _update_order_menu(update, context)

    except Exception as e:
        logger.error(f"Error setting price: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set price: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
