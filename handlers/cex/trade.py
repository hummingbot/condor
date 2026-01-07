"""
CEX Unified Trade functionality

Provides:
- Combined trade menu with quote and execute
- Compact recent orders/positions display
- Inline balances and trading rules
- Leverage/Position mode for perpetual exchanges
"""

import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from handlers.config.user_preferences import (
    get_clob_account,
    get_clob_order_defaults,
    set_clob_last_order,
    set_last_trade_connector,
)
from config_manager import get_client
from ._shared import (
    get_cached,
    set_cached,
    invalidate_cache,
    get_cex_balances,
    get_positions,
    get_trading_rules,
    get_available_cex_connectors,
)
from handlers.dex._shared import format_relative_time

logger = logging.getLogger(__name__)

# Default leverage when not set
DEFAULT_LEVERAGE = 5


# ============================================
# HELPER FUNCTIONS
# ============================================

def _format_number(value, decimals: int = 2) -> str:
    """Format number with K/M suffix for readability"""
    if value is None:
        return "—"
    try:
        num = float(value)
        if num == 0:
            return "0"
        if abs(num) >= 1_000_000:
            return f"{num/1_000_000:.{decimals}f}M"
        if abs(num) >= 1_000:
            return f"{num/1_000:.{decimals}f}K"
        if abs(num) >= 1:
            return f"{num:.{decimals}f}"
        if abs(num) >= 0.01:
            return f"{num:.4f}"
        return f"{num:.6f}"
    except (ValueError, TypeError):
        return "—"


def _format_price(price: float) -> str:
    """Format price with appropriate precision"""
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


def _is_perpetual_connector(connector_name: str) -> bool:
    """Check if connector is a perpetual futures exchange"""
    return "perpetual" in connector_name.lower() if connector_name else False


def _get_leverage_cache_key(connector: str, trading_pair: str) -> str:
    """Get cache key for leverage"""
    return f"leverage_{connector}_{trading_pair}"


def _get_position_mode_cache_key(connector: str) -> str:
    """Get cache key for position mode"""
    return f"pos_mode_{connector}"


async def _fetch_recent_orders(client, limit: int = 5) -> list:
    """Fetch recent orders for display"""
    try:
        result = await client.trading.get_active_orders(limit=limit)
        return result.get("data", [])
    except Exception as e:
        logger.warning(f"Error fetching recent orders: {e}")
        return []


def _format_compact_order_line(order: dict) -> str:
    """Format a single order as a compact line (like swap format)

    Returns: "✅ BTC-USDT BUY @95000 0.01  1d bin"
    """
    pair = order.get('trading_pair', 'N/A')
    side = order.get('trade_type', order.get('side', '?'))
    status = order.get('status', 'UNKNOWN')
    amount = order.get('amount', 0)
    price = order.get('price', 0)
    connector = order.get('connector_name', '')[:3]
    timestamp = order.get('creation_timestamp') or order.get('timestamp', '')

    # Status emoji
    status_char = "✅" if status == "FILLED" else "🟢" if status == "OPEN" else "⏳" if status == "PENDING" else "❌"

    # Format price
    price_str = f"@{_format_number(price, 4)}" if price else "@MKT"

    # Build line like swap format: "✅ SOL-USDT BUY @144.51 0.5  1d bin"
    line = f"{status_char} {pair} {side} {price_str} {_format_number(amount)}"

    # Add metadata (age and connector)
    meta_parts = []
    age = format_relative_time(str(timestamp)) if timestamp else ""
    if age:
        meta_parts.append(age)
    if connector:
        meta_parts.append(connector)

    if meta_parts:
        line += f"  {' '.join(meta_parts)}"

    return escape_markdown_v2(line)


def _format_compact_position_line(pos: dict) -> str:
    """Format a single position as a compact line

    Returns: "🟢 binance_perpetual SOL-USDT LONG @144.50 0.5 PnL:+$1.23"
    """
    connector = pos.get('connector_name', '')
    pair = pos.get('trading_pair', 'N/A')
    side = pos.get('position_side') or pos.get('side') or pos.get('trade_type', '?')
    amount = pos.get('amount', 0)
    entry_price = pos.get('entry_price', 0)
    pnl = pos.get('unrealized_pnl', 0)

    # Side display
    side_upper = str(side).upper()
    side_emoji = "🟢" if side_upper in ('LONG', 'BUY') else "🔴"
    side_display = "LONG" if side_upper in ('LONG', 'BUY') else "SHORT"

    # PnL with sign
    try:
        pnl_float = float(pnl)
        pnl_str = f"+${pnl_float:.2f}" if pnl_float >= 0 else f"-${abs(pnl_float):.2f}"
    except (ValueError, TypeError):
        pnl_str = "—"

    line = f"{side_emoji} {connector} {pair} {side_display} @{_format_number(entry_price, 4)} {_format_number(amount)} PnL:{pnl_str}"

    return escape_markdown_v2(line)


# ============================================
# MENU DISPLAY
# ============================================

async def handle_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle trade - unified menu for place order and view"""
    if "trade_params" not in context.user_data:
        defaults = get_clob_order_defaults(context.user_data)
        context.user_data["trade_params"] = defaults

    context.user_data["cex_state"] = "trade"

    await show_trade_menu(update, context)


async def handle_trade_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle trade refresh - clear cache and reload"""
    query = update.callback_query
    if query:
        await query.answer("Refreshing...")

    # Invalidate caches
    invalidate_cache(context.user_data, "balances", "orders", "positions")

    await handle_trade(update, context)


def _build_trade_keyboard(params: dict, is_perpetual: bool = False,
                          leverage: int = None, position_mode: str = None) -> list:
    """Build the trade menu keyboard

    Layout:
    - Row 1: connector
    - Row 2: trading pair | side | order type
    - Row 3: amount | position action
    - Row 4: price (only for LIMIT)
    - Row 5 (perpetual): leverage | pos mode | execute
    - Row 5 (spot): quote | execute
    - Row 6: orders | positions | close
    """
    connector = params.get('connector', 'binance_perpetual')

    keyboard = [
        # Row 1: Connector (full width)
        [
            InlineKeyboardButton(
                f"🔌 {connector}",
                callback_data="cex:trade_set_connector"
            )
        ],
        # Row 2: Trading Pair | Side | Order Type
        [
            InlineKeyboardButton(
                f"💱 {params.get('trading_pair', 'BTC-USDT')}",
                callback_data="cex:trade_set_pair"
            ),
            InlineKeyboardButton(
                f"📈 {params.get('side', 'BUY')}",
                callback_data="cex:trade_toggle_side"
            ),
            InlineKeyboardButton(
                f"📋 {params.get('order_type', 'MARKET')}",
                callback_data="cex:trade_toggle_type"
            )
        ],
    ]

    # Row 3: Amount | Position Action (perpetual only) | Price (LIMIT only)
    row3 = [
        InlineKeyboardButton(
            f"💰 {params.get('amount', '0.5')}",
            callback_data="cex:trade_set_amount"
        ),
    ]
    # Only show position action for perpetual exchanges
    if is_perpetual:
        row3.append(InlineKeyboardButton(
            f"🎯 {params.get('position_mode', 'OPEN')}",
            callback_data="cex:trade_toggle_position"
        ))
    if params.get('order_type') in ['LIMIT', 'LIMIT_MAKER']:
        row3.append(InlineKeyboardButton(
            f"💵 {params.get('price', '—')}",
            callback_data="cex:trade_set_price"
        ))
    keyboard.append(row3)

    # Row 5 (perpetual): Leverage | Pos Mode
    # Row 6 (perpetual): Quote | Execute
    if is_perpetual:
        lev_display = f"{leverage}x" if leverage else f"{DEFAULT_LEVERAGE}x"
        mode_display = position_mode or "HEDGE"
        keyboard.append([
            InlineKeyboardButton(f"⚡ {lev_display}", callback_data="cex:trade_set_leverage"),
            InlineKeyboardButton(f"🔄 {mode_display}", callback_data="cex:trade_toggle_pos_mode"),
        ])
        keyboard.append([
            InlineKeyboardButton("📊 Quote", callback_data="cex:trade_get_quote"),
            InlineKeyboardButton("✅ Execute", callback_data="cex:trade_execute")
        ])
    else:
        # Row 5 (spot): Quote | Execute
        keyboard.append([
            InlineKeyboardButton("📊 Quote", callback_data="cex:trade_get_quote"),
            InlineKeyboardButton("✅ Execute", callback_data="cex:trade_execute")
        ])

    # Row 6: Navigation
    keyboard.append([
        InlineKeyboardButton("📋 Orders", callback_data="cex:search_orders"),
        InlineKeyboardButton("📊 Positions", callback_data="cex:positions"),
        InlineKeyboardButton("❌ Close", callback_data="cex:close")
    ])

    return keyboard


def _build_trade_menu_text(user_data: dict, params: dict,
                           balances: dict = None, positions: list = None,
                           orders: list = None, trading_rules: dict = None,
                           current_price: float = None, quote_data: dict = None) -> str:
    """Build the trade menu text content (swap.py style)"""
    connector = params.get('connector', 'binance_perpetual')
    trading_pair = params.get('trading_pair', 'BTC-USDT')

    # Parse trading pair
    if '-' in trading_pair:
        base_token, quote_token = trading_pair.split('-', 1)
    else:
        base_token, quote_token = trading_pair, 'USDT'

    # Build header
    help_text = r"📝 *Trade*" + "\n\n"

    # Show balances section (with loading placeholder)
    help_text += r"━━━ Balance ━━━" + "\n"
    if balances is not None:
        connector_balances = balances.get(connector, [])
        balances_found = {}

        # Collect base and quote token balances
        for bal in connector_balances:
            token = bal.get("token", "").upper()
            units = float(bal.get("units", 0))
            value = float(bal.get("value", 0))
            if token == base_token.upper() and (units > 0 or value > 0):
                balances_found["base"] = {"units": units, "value": value, "token": token}
            elif token == quote_token.upper() and (units > 0 or value > 0):
                balances_found["quote"] = {"units": units, "value": value, "token": token}

        if balances_found:
            if "base" in balances_found:
                b = balances_found["base"]
                val_str = f"(${_format_number(b['value'])})" if b['value'] > 0 else ""
                help_text += f"💰 `{escape_markdown_v2(b['token'])}`: `{escape_markdown_v2(_format_number(b['units']))}` {escape_markdown_v2(val_str)}\n"
            if "quote" in balances_found:
                q = balances_found["quote"]
                val_str = f"(${_format_number(q['value'])})" if q['value'] > 0 else ""
                help_text += f"💵 `{escape_markdown_v2(q['token'])}`: `{escape_markdown_v2(_format_number(q['units']))}` {escape_markdown_v2(val_str)}\n"
        else:
            help_text += "_No balance found_\n".replace(".", "\\.")
    else:
        help_text += "⏳ _Loading balances\\.\\.\\._\n"
    help_text += "\n"

    # Show market data section (quote + trading rules combined)
    help_text += f"━━━ {escape_markdown_v2(trading_pair)} ━━━\n"

    # Calculate spread for first line
    spread_str = ""
    buy_price = None
    sell_price = None
    if quote_data and not quote_data.get("loading") and not quote_data.get("error"):
        buy_price = quote_data.get("buy_price")
        sell_price = quote_data.get("sell_price")
        if buy_price and sell_price:
            midpoint = (buy_price + sell_price) / 2
            spread_pct = abs(buy_price - sell_price) / midpoint * 100 if midpoint else 0
            spread_str = f" \\| 📊 Spread: {escape_markdown_v2(f'{spread_pct:.2f}')}%"

    # Line 1: Price | Spread
    if current_price:
        help_text += f"💵 Price: `{escape_markdown_v2(_format_price(current_price))}`{spread_str}\n"
    else:
        help_text += "💵 Price: _loading\\.\\.\\._\n"

    # Line 2: Min amount | Min notional
    if trading_rules and trading_pair in trading_rules:
        rules = trading_rules[trading_pair]
        min_order = rules.get("min_order_size", 0)
        min_notional = rules.get("min_notional_size", 0)
        rules_line = []
        if min_order > 0:
            rules_line.append(f"📏 Min amount: `{_format_number(min_order)}`")
        if min_notional > 0:
            rules_line.append(f"Min notional: `${_format_number(min_notional)}`")
        if rules_line:
            help_text += escape_markdown_v2(" | ".join(rules_line)).replace("\\`", "`") + "\n"

    # Quote data (buy/sell lines)
    if quote_data:
        if quote_data.get("loading"):
            help_text += "⏳ _Fetching quotes\\.\\.\\._\n"
        elif quote_data.get("error"):
            error_msg = escape_markdown_v2(str(quote_data.get("error")))
            help_text += f"❌ _{error_msg}_\n"
        elif buy_price or sell_price:
            amount_requested = quote_data.get("amount", params.get("amount", "1"))

            try:
                amt_float = float(amount_requested)
            except (ValueError, TypeError):
                amt_float = 0

            if buy_price:
                buy_cost = amt_float * buy_price
                help_text += f"🟢 `BUY  {_format_number(amt_float)} {escape_markdown_v2(base_token)} → {_format_number(buy_cost, 2)} {escape_markdown_v2(quote_token)} @{_format_number(buy_price, 2)}`\n"

            if sell_price:
                sell_proceeds = amt_float * sell_price
                help_text += f"🔴 `SELL {_format_number(amt_float)} {escape_markdown_v2(base_token)} → {_format_number(sell_proceeds, 2)} {escape_markdown_v2(quote_token)} @{_format_number(sell_price, 2)}`\n"
        else:
            help_text += "_No quotes available_\n".replace(".", "\\.")
    else:
        help_text += "⏳ _Loading quotes\\.\\.\\._\n"

    help_text += "\n"

    # Show positions (for perpetual)
    if positions and _is_perpetual_connector(connector):
        conn_positions = [p for p in positions if p.get('connector_name') == connector]
        if conn_positions:
            help_text += r"━━━ Positions ━━━" + "\n"
            for pos in conn_positions[:3]:
                line = _format_compact_position_line(pos)
                help_text += line + "\n"
            if len(conn_positions) > 3:
                help_text += f"_\\+{len(conn_positions) - 3} more_\n"
            help_text += "\n"

    # Quick trade hint
    is_perp = _is_perpetual_connector(connector)
    help_text += r"━━━ Quick Trade ━━━" + "\n"
    if is_perp:
        help_text += r"⌨️ `pair side amt [type] [price] [pos]`" + "\n"
        help_text += f"Market: `{escape_markdown_v2(trading_pair)} BUY 0\\.5`\n"
        help_text += f"Limit: `{escape_markdown_v2(trading_pair)} SELL 0\\.5 LIMIT 150 CLOSE`\n\n"
    else:
        help_text += r"⌨️ `pair side amt [type] [price]`" + "\n"
        help_text += f"Market: `{escape_markdown_v2(trading_pair)} BUY 0\\.5`\n"
        help_text += f"Limit: `{escape_markdown_v2(trading_pair)} SELL 0\\.5 LIMIT 150`\n\n"

    # Show recent orders (swap.py style)
    if orders:
        help_text += r"━━━ Recent ━━━" + "\n"
        for order in orders[:5]:
            line = _format_compact_order_line(order)
            help_text += line + "\n"

    return help_text


async def show_trade_menu(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          send_new: bool = False, auto_fetch: bool = True,
                          quote_data: dict = None) -> None:
    """Display the unified trade menu with balances and data"""
    params = context.user_data.get("trade_params", {})
    connector = params.get("connector", "binance_perpetual")
    trading_pair = params.get("trading_pair", "BTC-USDT")
    account = get_clob_account(context.user_data)
    is_perpetual = _is_perpetual_connector(connector)

    # Try to get cached data
    balances = get_cached(context.user_data, f"cex_balances_{account}", ttl=60)
    positions = get_cached(context.user_data, f"positions_{connector}", ttl=60) if is_perpetual else None
    orders = get_cached(context.user_data, "recent_orders", ttl=60)
    trading_rules = get_cached(context.user_data, f"trading_rules_{connector}", ttl=300)
    current_price = context.user_data.get("current_market_price")

    # Use cached quote if available and no explicit quote provided
    if quote_data is None:
        quote_data = get_cached(context.user_data, "trade_quote", ttl=30)

    # Get leverage and position mode for perpetual
    leverage = None
    position_mode = None
    if is_perpetual:
        leverage = context.user_data.get(_get_leverage_cache_key(connector, trading_pair), DEFAULT_LEVERAGE)
        position_mode = context.user_data.get(_get_position_mode_cache_key(connector), "HEDGE")

    # Build text and keyboard
    help_text = _build_trade_menu_text(
        context.user_data, params, balances, positions, orders, trading_rules, current_price, quote_data
    )
    keyboard = _build_trade_keyboard(params, is_perpetual, leverage, position_mode)
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send or edit message
    message = None
    if send_new or not update.callback_query:
        if update.message:
            message = await update.message.reply_text(
                help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        elif update.callback_query:
            message = await update.callback_query.message.reply_text(
                help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
    else:
        try:
            await update.callback_query.message.edit_text(
                help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            message = update.callback_query.message
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.warning(f"Could not edit message: {e}")
            message = update.callback_query.message

    # Store message for later editing
    chat_id = update.effective_chat.id
    if message:
        context.user_data["trade_menu_message_id"] = message.message_id
        context.user_data["trade_menu_chat_id"] = message.chat_id

    # Launch background data fetch if needed (when any key data is missing)
    needs_fetch = balances is None or quote_data is None or current_price is None
    if auto_fetch and message and needs_fetch:
        asyncio.create_task(_fetch_trade_data_background(context, message, params, chat_id))


async def _update_trade_message(context: ContextTypes.DEFAULT_TYPE, message) -> None:
    """Helper to rebuild and update the trade menu message"""
    if context.user_data.get("cex_state") != "trade":
        return

    params = context.user_data.get("trade_params", {})
    connector = params.get("connector", "binance_perpetual")
    trading_pair = params.get("trading_pair", "BTC-USDT")
    account = get_clob_account(context.user_data)
    is_perpetual = _is_perpetual_connector(connector)

    # Get all cached data
    balances = get_cached(context.user_data, f"cex_balances_{account}", ttl=60)
    positions = get_cached(context.user_data, f"positions_{connector}", ttl=60) if is_perpetual else None
    orders = get_cached(context.user_data, "recent_orders", ttl=60)
    trading_rules = get_cached(context.user_data, f"trading_rules_{connector}", ttl=300)
    current_price = context.user_data.get("current_market_price")
    quote_data = get_cached(context.user_data, "trade_quote", ttl=30)

    leverage = context.user_data.get(_get_leverage_cache_key(connector, trading_pair), DEFAULT_LEVERAGE) if is_perpetual else None
    position_mode = context.user_data.get(_get_position_mode_cache_key(connector), "HEDGE") if is_perpetual else None

    help_text = _build_trade_menu_text(
        context.user_data, params, balances, positions, orders, trading_rules, current_price, quote_data
    )
    keyboard = _build_trade_keyboard(params, is_perpetual, leverage, position_mode)
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await message.edit_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning(f"Could not update message: {e}")


async def _fetch_trade_data_background(
    context: ContextTypes.DEFAULT_TYPE,
    message,
    params: dict,
    chat_id: int = None
) -> None:
    """Fetch trade data in background and update message when done (like swap.py)"""
    logger.info(f"Starting background fetch for trade data...")
    connector = params.get("connector", "binance_perpetual")
    trading_pair = params.get("trading_pair", "BTC-USDT")
    account = get_clob_account(context.user_data)
    is_perpetual = _is_perpetual_connector(connector)

    try:
        client = await get_client(chat_id, context=context)
    except Exception as e:
        logger.warning(f"Could not get client for trade data: {e}")
        return

    # Define safe fetch functions
    async def fetch_balances_safe():
        try:
            return await get_cex_balances(context.user_data, client, account)
        except Exception as e:
            logger.warning(f"Could not fetch balances: {e}")
            # Cache empty dict so display shows "No balance found" instead of "Loading..."
            set_cached(context.user_data, f"cex_balances_{account}", {})
            return {}

    async def fetch_price_safe():
        try:
            prices = await client.market_data.get_prices(
                connector_name=connector,
                trading_pairs=trading_pair
            )
            price = prices["prices"].get(trading_pair)
            if price:
                context.user_data["current_market_price"] = price
            return price
        except Exception as e:
            logger.warning(f"Could not fetch price: {e}")
            return None

    async def fetch_rules_safe():
        try:
            return await get_trading_rules(context.user_data, client, connector)
        except Exception as e:
            logger.warning(f"Could not fetch rules: {e}")
            return None

    async def fetch_orders_safe():
        try:
            orders = await _fetch_recent_orders(client, limit=5)
            set_cached(context.user_data, "recent_orders", orders)
            return orders
        except Exception as e:
            logger.warning(f"Could not fetch orders: {e}")
            return []

    async def fetch_quote_safe():
        try:
            amount = params.get("amount", "1")
            volume = float(str(amount).replace("$", ""))

            # If amount is in USD, convert to base token volume
            if "$" in str(amount):
                prices = await client.market_data.get_prices(
                    connector_name=connector,
                    trading_pairs=trading_pair
                )
                current_price = prices["prices"].get(trading_pair, 1)
                volume = volume / current_price

            # Fetch BUY and SELL quotes
            async def get_quote(is_buy: bool):
                try:
                    result = await client.market_data.get_price_for_volume(
                        connector_name=connector,
                        trading_pair=trading_pair,
                        volume=volume,
                        is_buy=is_buy
                    )
                    if isinstance(result, dict):
                        price = (
                            result.get("result_price") or
                            result.get("price") or
                            result.get("average_price") or
                            result.get("data", {}).get("price") or
                            result.get("data", {}).get("result_price")
                        )
                        return float(price) if price else None
                    return None
                except Exception:
                    return None

            buy_price, sell_price = await asyncio.gather(
                get_quote(True),
                get_quote(False)
            )

            # Fallback to mid price if quotes failed
            if buy_price is None and sell_price is None:
                try:
                    prices = await client.market_data.get_prices(
                        connector_name=connector,
                        trading_pairs=trading_pair
                    )
                    mid_price = prices["prices"].get(trading_pair)
                    if mid_price:
                        buy_price = mid_price * 1.0005
                        sell_price = mid_price * 0.9995
                except Exception:
                    pass

            quote_data = {
                "trading_pair": trading_pair,
                "amount": volume,
                "buy_price": buy_price,
                "sell_price": sell_price,
            }
            set_cached(context.user_data, "trade_quote", quote_data)
            return quote_data
        except Exception as e:
            logger.warning(f"Could not fetch quote: {e}")
            set_cached(context.user_data, "trade_quote", {"error": str(e)})
            return None

    async def fetch_positions_safe():
        try:
            return await get_positions(context.user_data, client, connector)
        except Exception as e:
            logger.warning(f"Could not fetch positions: {e}")
            return []

    async def fetch_perpetual_settings_safe():
        try:
            mode_result = await client.trading.get_position_mode(
                account_name=account,
                connector_name=connector
            )
            position_mode = mode_result.get("position_mode", "HEDGE")
            context.user_data[_get_position_mode_cache_key(connector)] = position_mode

            lev_key = _get_leverage_cache_key(connector, trading_pair)
            if lev_key not in context.user_data:
                try:
                    await client.trading.set_leverage(
                        account_name=account,
                        connector_name=connector,
                        trading_pair=trading_pair,
                        leverage=DEFAULT_LEVERAGE,
                    )
                    context.user_data[lev_key] = DEFAULT_LEVERAGE
                except Exception:
                    pass
            return position_mode
        except Exception as e:
            logger.warning(f"Could not fetch perpetual settings: {e}")
            return None

    # Fetch all data in parallel
    tasks = [
        fetch_balances_safe(),
        fetch_price_safe(),
        fetch_rules_safe(),
        fetch_orders_safe(),
        fetch_quote_safe(),
    ]

    if is_perpetual:
        tasks.extend([
            fetch_positions_safe(),
            fetch_perpetual_settings_safe(),
        ])

    # Wait for all to complete
    await asyncio.gather(*tasks, return_exceptions=True)

    # Update message once after all data is fetched
    logger.info(f"Background fetch complete, updating message...")
    await _update_trade_message(context, message)


# ============================================
# QUOTE HANDLER
# ============================================

async def handle_trade_get_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get BUY/SELL quotes using get_price_for_volume"""
    params = context.user_data.get("trade_params", {})
    connector = params.get("connector")
    trading_pair = params.get("trading_pair")
    amount = params.get("amount", "1")

    if not all([connector, trading_pair]):
        error_message = format_error_message("Missing connector or trading pair")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")
        return

    # Show loading immediately with current menu data
    loading_quote = {"loading": True, "trading_pair": trading_pair, "amount": amount}
    set_cached(context.user_data, "trade_quote", loading_quote)
    await show_trade_menu(update, context, quote_data=loading_quote, auto_fetch=False)

    try:
        # Parse amount (remove $ if present)
        volume = float(str(amount).replace("$", ""))

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, context=context)

        # If amount is in USD, we need to convert to base token volume
        if "$" in str(amount):
            prices = await client.market_data.get_prices(
                connector_name=connector,
                trading_pairs=trading_pair
            )
            current_price = prices["prices"].get(trading_pair, 1)
            volume = volume / current_price

        # Fetch BUY and SELL quotes in parallel
        async def get_quote_safe(is_buy: bool):
            try:
                result = await client.market_data.get_price_for_volume(
                    connector_name=connector,
                    trading_pair=trading_pair,
                    volume=volume,
                    is_buy=is_buy
                )
                logger.debug(f"Quote result for {'BUY' if is_buy else 'SELL'}: {result}")
                if isinstance(result, dict):
                    price = (
                        result.get("result_price") or
                        result.get("price") or
                        result.get("average_price") or
                        result.get("data", {}).get("price") or
                        result.get("data", {}).get("result_price")
                    )
                    return float(price) if price else None
                return None
            except Exception as e:
                logger.warning(f"Quote failed for {'BUY' if is_buy else 'SELL'}: {e}")
                return None

        buy_price, sell_price = await asyncio.gather(
            get_quote_safe(True),
            get_quote_safe(False)
        )

        # If quotes failed, try using get_prices as fallback
        if buy_price is None and sell_price is None:
            logger.info("get_price_for_volume failed, trying get_prices fallback")
            try:
                prices = await client.market_data.get_prices(
                    connector_name=connector,
                    trading_pairs=trading_pair
                )
                mid_price = prices["prices"].get(trading_pair)
                if mid_price:
                    buy_price = mid_price * 1.0005
                    sell_price = mid_price * 0.9995
            except Exception as e:
                logger.warning(f"Fallback price fetch failed: {e}")

        if buy_price is None and sell_price is None:
            raise ValueError("No quotes available")

        # Build quote data
        quote_data = {
            "trading_pair": trading_pair,
            "amount": volume,
            "buy_price": buy_price,
            "sell_price": sell_price,
        }

        # Cache the quote and update menu
        set_cached(context.user_data, "trade_quote", quote_data)
        await show_trade_menu(update, context, quote_data=quote_data, auto_fetch=False)

    except Exception as e:
        logger.error(f"Error getting quote: {e}", exc_info=True)
        # Show error in quote section
        error_quote = {"error": str(e), "trading_pair": trading_pair}
        set_cached(context.user_data, "trade_quote", error_quote)
        await show_trade_menu(update, context, quote_data=error_quote, auto_fetch=False)


# ============================================
# PARAMETER HANDLERS
# ============================================

def _invalidate_trade_cache(user_data: dict) -> None:
    """Invalidate cached price/quote when params change"""
    user_data.pop("current_market_price", None)
    # Also clear quote cache
    cache = user_data.get("_cache", {})
    if "trade_quote" in cache:
        del cache["trade_quote"]


async def handle_trade_toggle_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between BUY and SELL"""
    params = context.user_data.get("trade_params", {})
    current_side = params.get("side", "BUY")
    params["side"] = "SELL" if current_side == "BUY" else "BUY"
    await show_trade_menu(update, context, auto_fetch=False)


async def handle_trade_toggle_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between MARKET, LIMIT, LIMIT_MAKER"""
    params = context.user_data.get("trade_params", {})
    current_type = params.get("order_type", "MARKET")

    if current_type == "MARKET":
        params["order_type"] = "LIMIT"
        # Set default price from current market price
        current_price = context.user_data.get("current_market_price")
        if current_price:
            params["price"] = str(current_price)
        else:
            params["price"] = "—"
    elif current_type == "LIMIT":
        params["order_type"] = "LIMIT_MAKER"
    else:
        params["order_type"] = "MARKET"

    await show_trade_menu(update, context, auto_fetch=False)


async def handle_trade_toggle_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between OPEN and CLOSE"""
    params = context.user_data.get("trade_params", {})
    current_mode = params.get("position_mode", "OPEN")
    params["position_mode"] = "CLOSE" if current_mode == "OPEN" else "OPEN"
    await show_trade_menu(update, context, auto_fetch=False)


async def handle_trade_set_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available CEX connectors for selection"""
    help_text = r"🔌 *Select Connector*"

    keyboard = []

    try:
        chat_id = update.effective_chat.id
        client = await get_client(chat_id, context=context)
        cex_connectors = await get_available_cex_connectors(context.user_data, client)

        # Build buttons (2 per row)
        row = []
        for connector in cex_connectors:
            row.append(InlineKeyboardButton(
                connector,
                callback_data=f"cex:trade_connector_{connector}"
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
        help_text += "\n\n_Could not fetch connectors_"

    keyboard.append([InlineKeyboardButton("« Back", callback_data="cex:trade")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_trade_connector_select(update: Update, context: ContextTypes.DEFAULT_TYPE, connector_name: str) -> None:
    """Handle connector selection"""
    params = context.user_data.get("trade_params", {})
    params["connector"] = connector_name

    # Save unified preference for /trade command
    set_last_trade_connector(context.user_data, "cex", connector_name)

    _invalidate_trade_cache(context.user_data)
    invalidate_cache(context.user_data, "balances", "positions", "trading_rules")
    context.user_data["cex_state"] = "trade"

    await show_trade_menu(update, context)


async def handle_trade_set_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt for trading pair input - edits message"""
    params = context.user_data.get("trade_params", {})
    help_text = (
        r"💱 *Set Trading Pair*" + "\n\n"
        r"Enter the trading pair:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`BTC\-USDT`" + "\n"
        r"`ETH\-USDT`" + "\n"
        r"`SOL\-USDT`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="cex:trade")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["cex_state"] = "trade_set_pair"

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_trade_set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt for amount input - edits message"""
    help_text = (
        r"💰 *Set Amount*" + "\n\n"
        r"Enter amount:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`0\.5` \- Trade 0\.5 of base token" + "\n"
        r"`$100` \- Trade $100 worth"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="cex:trade")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["cex_state"] = "trade_set_amount"

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_trade_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt for price input - edits message"""
    help_text = (
        r"💵 *Set Price*" + "\n\n"
        r"Enter limit price:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`95000`" + "\n"
        r"`88000\.50`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="cex:trade")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["cex_state"] = "trade_set_price"

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# LEVERAGE & POSITION MODE (PERPETUAL ONLY)
# ============================================

async def handle_trade_set_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt for leverage input - edits message"""
    params = context.user_data.get("trade_params", {})
    connector = params.get("connector", "binance_perpetual")
    trading_pair = params.get("trading_pair", "BTC-USDT")

    current_lev = context.user_data.get(_get_leverage_cache_key(connector, trading_pair), DEFAULT_LEVERAGE)

    help_text = (
        r"⚡ *Set Leverage*" + "\n\n"
        f"Current: `{current_lev}x` for `{escape_markdown_v2(trading_pair)}`\n\n"
        r"Enter new leverage:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`5` \- Set 5x leverage" + "\n"
        r"`10` \- Set 10x leverage" + "\n"
        r"`20` \- Set 20x leverage"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="cex:trade")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["cex_state"] = "trade_set_leverage"

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_trade_toggle_pos_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle position mode between HEDGE and ONEWAY directly"""
    params = context.user_data.get("trade_params", {})
    connector = params.get("connector", "binance_perpetual")
    account = get_clob_account(context.user_data)

    try:
        chat_id = update.effective_chat.id
        client = await get_client(chat_id, context=context)

        # Get current mode
        current_mode = context.user_data.get(_get_position_mode_cache_key(connector), "HEDGE")
        new_mode = "ONEWAY" if current_mode == "HEDGE" else "HEDGE"

        # Set new mode on exchange
        await client.trading.set_position_mode(
            account_name=account,
            connector_name=connector,
            position_mode=new_mode
        )

        # Update cache
        context.user_data[_get_position_mode_cache_key(connector)] = new_mode

        # Show updated menu
        await show_trade_menu(update, context, auto_fetch=False)

    except Exception as e:
        logger.error(f"Error toggling position mode: {e}", exc_info=True)
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


# ============================================
# EXECUTE
# ============================================

async def handle_trade_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the trade with current parameters"""
    try:
        params = context.user_data.get("trade_params", {})
        account = get_clob_account(context.user_data)

        connector = params.get("connector")
        trading_pair = params.get("trading_pair")
        side = params.get("side")
        amount = params.get("amount")
        order_type = params.get("order_type")
        price = params.get("price")
        position_action = params.get("position_mode", "OPEN")

        if not all([connector, trading_pair, side, amount, order_type]):
            raise ValueError("Missing required parameters")

        if order_type in ["LIMIT", "LIMIT_MAKER"] and (not price or price == "—"):
            raise ValueError("Price required for LIMIT orders")

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, context=context)

        # Handle USD amount
        is_quote_amount = "$" in str(amount)
        if is_quote_amount:
            usd_value = float(str(amount).replace("$", ""))
            prices = await client.market_data.get_prices(
                connector_name=connector,
                trading_pairs=trading_pair
            )
            current_price = prices["prices"][trading_pair]
            amount_float = usd_value / current_price
        else:
            amount_float = float(amount)

        result = await client.trading.place_order(
            account_name=account,
            connector_name=connector,
            trading_pair=trading_pair,
            trade_type=side,
            amount=amount_float,
            order_type=order_type,
            price=float(price) if price and order_type in ["LIMIT", "LIMIT_MAKER"] else None,
            position_action=position_action,
        )

        # Invalidate cache
        invalidate_cache(context.user_data, "balances", "orders", "positions")

        # Save for quick repeat
        set_clob_last_order(context.user_data, {
            "connector": connector,
            "trading_pair": trading_pair,
            "side": side,
            "order_type": order_type,
            "position_mode": position_action,
            "amount": amount,
            "price": price if price else "—",
        })

        order_info = escape_markdown_v2(
            f"✅ Order placed!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount_float:.6f}\n"
            f"Type: {order_type}"
        )

        if price and order_type in ["LIMIT", "LIMIT_MAKER"]:
            order_info += escape_markdown_v2(f"\nPrice: {price}")

        if "order_id" in result:
            order_info += escape_markdown_v2(f"\nOrder ID: {result['order_id']}")

        keyboard = [[InlineKeyboardButton("« Back to Trade", callback_data="cex:trade")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            order_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error executing trade: {e}", exc_info=True)
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


# ============================================
# TEXT INPUT PROCESSORS
# ============================================

async def _update_trade_menu_after_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update the trade menu message after text input"""
    # Delete user's input message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Edit the stored trade menu message
    msg_id = context.user_data.get("trade_menu_message_id")
    chat_id = context.user_data.get("trade_menu_chat_id")

    if msg_id and chat_id:
        params = context.user_data.get("trade_params", {})
        connector = params.get("connector", "binance_perpetual")
        trading_pair = params.get("trading_pair", "BTC-USDT")
        account = get_clob_account(context.user_data)
        is_perpetual = _is_perpetual_connector(connector)

        # Get cached data
        balances = get_cached(context.user_data, f"cex_balances_{account}", ttl=60)
        positions = get_cached(context.user_data, f"positions_{connector}", ttl=60) if is_perpetual else None
        orders = get_cached(context.user_data, "recent_orders", ttl=60)
        trading_rules = get_cached(context.user_data, f"trading_rules_{connector}", ttl=300)
        current_price = context.user_data.get("current_market_price")
        quote_data = get_cached(context.user_data, "trade_quote", ttl=30)

        leverage = None
        position_mode = None
        if is_perpetual:
            leverage = context.user_data.get(_get_leverage_cache_key(connector, trading_pair), DEFAULT_LEVERAGE)
            position_mode = context.user_data.get(_get_position_mode_cache_key(connector), "HEDGE")

        help_text = _build_trade_menu_text(
            context.user_data, params, balances, positions, orders, trading_rules, current_price, quote_data
        )
        keyboard = _build_trade_keyboard(params, is_perpetual, leverage, position_mode)
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.debug(f"Could not update trade menu: {e}")
            # Fallback: send new message
            await show_trade_menu(update, context, send_new=True)
    else:
        await show_trade_menu(update, context, send_new=True)


async def process_trade(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process trade from text input: pair side amount [type] [price] [position]

    This is a QUICK TRADE - it executes immediately, not just updates params.
    """
    try:
        parts = user_input.split()

        if len(parts) < 3:
            raise ValueError("Need: pair side amount [type] [price] [pos]")

        # Get current connector from params
        params = context.user_data.get("trade_params", {})
        connector = params.get("connector", "binance_perpetual")
        account = get_clob_account(context.user_data)

        trading_pair = parts[0].upper()
        side = parts[1].upper()
        amount = parts[2]
        order_type = parts[3].upper() if len(parts) > 3 else "MARKET"
        price = parts[4] if len(parts) > 4 else None
        position_action = parts[5].upper() if len(parts) > 5 else "OPEN"

        # Validate
        if side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {side}. Use BUY or SELL")
        if order_type not in ("MARKET", "LIMIT", "LIMIT_MAKER"):
            raise ValueError(f"Invalid type: {order_type}. Use MARKET, LIMIT, or LIMIT_MAKER")
        if order_type in ("LIMIT", "LIMIT_MAKER") and not price:
            raise ValueError("Price required for LIMIT orders")

        # Delete user's input message
        try:
            await update.message.delete()
        except Exception:
            pass

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, context=context)

        # Handle USD amount
        is_quote_amount = "$" in str(amount)
        if is_quote_amount:
            usd_value = float(str(amount).replace("$", ""))
            prices = await client.market_data.get_prices(
                connector_name=connector,
                trading_pairs=trading_pair
            )
            current_price = prices["prices"][trading_pair]
            amount_float = usd_value / current_price
        else:
            amount_float = float(amount)

        # Execute the trade
        result = await client.trading.place_order(
            account_name=account,
            connector_name=connector,
            trading_pair=trading_pair,
            trade_type=side,
            amount=amount_float,
            order_type=order_type,
            price=float(price) if price and order_type in ["LIMIT", "LIMIT_MAKER"] else None,
            position_action=position_action,
        )

        # Invalidate cache
        invalidate_cache(context.user_data, "balances", "orders", "positions")

        # Update params for next trade
        context.user_data["trade_params"] = {
            "connector": connector,
            "trading_pair": trading_pair,
            "side": side,
            "amount": amount,
            "order_type": order_type,
            "price": price if price else "—",
            "position_mode": position_action,
        }

        # Save for quick repeat
        set_clob_last_order(context.user_data, {
            "connector": connector,
            "trading_pair": trading_pair,
            "side": side,
            "order_type": order_type,
            "position_mode": position_action,
            "amount": amount,
            "price": price if price else "—",
        })

        # Build success message
        order_info = escape_markdown_v2(
            f"✅ Order placed!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount_float:.6f}\n"
            f"Type: {order_type}"
        )

        if price and order_type in ["LIMIT", "LIMIT_MAKER"]:
            order_info += escape_markdown_v2(f"\nPrice: {price}")

        if "order_id" in result:
            order_info += escape_markdown_v2(f"\nOrder ID: {result['order_id']}")

        keyboard = [[InlineKeyboardButton("« Back to Trade", callback_data="cex:trade")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Update the trade menu message with success
        msg_id = context.user_data.get("trade_menu_message_id")
        menu_chat_id = context.user_data.get("trade_menu_chat_id")

        if msg_id and menu_chat_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=menu_chat_id,
                    message_id=msg_id,
                    text=order_info,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.debug(f"Could not update trade menu: {e}")
                await update.effective_chat.send_message(
                    order_info,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
        else:
            await update.effective_chat.send_message(
                order_info,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

        context.user_data["cex_state"] = "trade"

    except Exception as e:
        logger.error(f"Error processing quick trade: {e}", exc_info=True)
        error_message = format_error_message(f"Trade failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_trade_set_pair(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process trading pair input"""
    try:
        params = context.user_data.get("trade_params", {})
        params["trading_pair"] = user_input.strip().upper()

        _invalidate_trade_cache(context.user_data)
        context.user_data["cex_state"] = "trade"

        await _update_trade_menu_after_input(update, context)

    except Exception as e:
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_trade_set_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process amount input"""
    try:
        params = context.user_data.get("trade_params", {})
        params["amount"] = user_input.strip()

        _invalidate_trade_cache(context.user_data)
        context.user_data["cex_state"] = "trade"

        await _update_trade_menu_after_input(update, context)

    except Exception as e:
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_trade_set_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process price input"""
    try:
        params = context.user_data.get("trade_params", {})
        params["price"] = user_input.strip()

        context.user_data["cex_state"] = "trade"

        await _update_trade_menu_after_input(update, context)

    except Exception as e:
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_trade_set_leverage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process leverage input - just a number"""
    try:
        leverage = int(user_input.strip().replace('x', '').replace('X', ''))

        if leverage <= 0:
            raise ValueError("Leverage must be positive")

        params = context.user_data.get("trade_params", {})
        connector = params.get("connector", "binance_perpetual")
        trading_pair = params.get("trading_pair", "BTC-USDT")
        account = get_clob_account(context.user_data)

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, context=context)

        # Set leverage on exchange
        await client.trading.set_leverage(
            account_name=account,
            connector_name=connector,
            trading_pair=trading_pair,
            leverage=leverage,
        )

        # Update cache
        context.user_data[_get_leverage_cache_key(connector, trading_pair)] = leverage
        context.user_data["cex_state"] = "trade"

        await _update_trade_menu_after_input(update, context)

    except ValueError as e:
        error_message = format_error_message(str(e))
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
    except Exception as e:
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# CLOSE HANDLER
# ============================================

async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close the trade interface"""
    context.user_data.pop("cex_state", None)
    context.user_data.pop("trade_params", None)
    context.user_data.pop("trade_menu_message_id", None)
    context.user_data.pop("trade_menu_chat_id", None)

    try:
        await update.callback_query.message.delete()
    except Exception:
        pass
