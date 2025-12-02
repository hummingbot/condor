"""
DEX Unified Swap functionality

Provides:
- Combined swap menu with quote and execute
- Compact recent swaps display
- Inline quote preview
"""

import asyncio
import logging
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from handlers.config.user_preferences import (
    get_dex_swap_defaults,
    get_dex_connector,
    get_dex_last_swap,
    set_dex_last_swap,
    DEFAULT_DEX_NETWORK,
)
from servers import get_client
from ._shared import (
    get_cached,
    set_cached,
    invalidate_cache,
    get_explorer_url,
    format_relative_time,
    _format_amount,
)

logger = logging.getLogger(__name__)


# ============================================
# HELPER FUNCTIONS
# ============================================

def _format_network_display(network_id: str) -> str:
    """Format network ID for button display

    Examples:
        solana-mainnet-beta -> Solana
        ethereum-mainnet -> Ethereum
        solana-devnet -> Solana Dev
    """
    if not network_id:
        return "Network"

    parts = network_id.split("-")
    chain = parts[0].capitalize()

    if len(parts) > 1:
        net = parts[1]
        if net in ("mainnet", "mainnet-beta"):
            return chain
        elif net == "devnet":
            return f"{chain} Dev"
        elif net == "testnet":
            return f"{chain} Test"
        else:
            return f"{chain} {net[:4]}"

    return chain


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


async def _fetch_recent_swaps(client, limit: int = 5) -> list:
    """Fetch recent swaps for display"""
    try:
        if not hasattr(client, 'gateway_swap'):
            return []

        result = await client.gateway_swap.search_swaps(limit=limit)
        return result.get("data", [])
    except Exception as e:
        logger.warning(f"Error fetching recent swaps: {e}")
        return []


def _format_compact_swap_line(swap: dict) -> str:
    """Format a single swap as a compact line

    Returns: "✅ SOL-USDC BUY @126.00 1.26 USDC  1d jup 🔗"
    """
    pair = swap.get('trading_pair', 'N/A')
    side = swap.get('side', '?')
    status = swap.get('status', 'UNKNOWN')
    network = swap.get('network', '')
    tx_hash = swap.get('transaction_hash', '')
    connector = swap.get('connector', '')[:3]  # Abbreviate connector
    timestamp = swap.get('timestamp', '')

    # Get amounts and tokens
    input_amount = swap.get('input_amount')
    output_amount = swap.get('output_amount')
    price = swap.get('price')
    quote_token = swap.get('quote_token', '')

    # Calculate display price (price of base in terms of quote)
    # BUY: input=quote, output=base, price=base/quote -> display = 1/price
    # SELL: input=base, output=quote, price=quote/base -> display = price
    price_display = "—"
    if price is not None and price > 0:
        if side == 'BUY':
            display_price = 1 / price
        else:
            display_price = price
        price_display = f"@{_format_number(display_price)}"

    # Quote amount (what was spent/received in quote token)
    # BUY: quote amount = input_amount (what we paid)
    # SELL: quote amount = output_amount (what we received)
    quote_amount_str = "—"
    if side == 'BUY' and input_amount is not None:
        quote_amount_str = f"{_format_number(input_amount)} {quote_token}"
    elif side == 'SELL' and output_amount is not None:
        quote_amount_str = f"{_format_number(output_amount)} {quote_token}"

    # Relative time
    age = format_relative_time(timestamp)

    # Status emoji (compact)
    status_char = "✅" if status == "CONFIRMED" else "⏳" if status == "PENDING" else "❌"

    # Build line: "✅ SOL-USDC BUY @126.00 1.26 USDC  1d jup 🔗"
    line_text = f"{status_char} {pair} {side} {price_display} {quote_amount_str}"

    # Add metadata
    meta_parts = []
    if age:
        meta_parts.append(age)
    if connector:
        meta_parts.append(connector)

    if meta_parts:
        line_text += f"  {' '.join(meta_parts)}"

    # Explorer link - only the 🔗 is clickable
    explorer_url = get_explorer_url(tx_hash, network) if tx_hash and network else None

    escaped_line = escape_markdown_v2(line_text)

    if explorer_url:
        # Escape URL for markdown
        escaped_url = explorer_url.replace("_", "\\_").replace("*", "\\*")
        escaped_url = escaped_url.replace("[", "\\[").replace("]", "\\]")
        escaped_url = escaped_url.replace("(", "\\(").replace(")", "\\)")
        return f"{escaped_line} [🔗]({escaped_url})"
    else:
        return escaped_line


async def _fetch_router_connectors(client) -> list:
    """Fetch connectors that have 'router' trading type"""
    try:
        response = await client.gateway.list_connectors()
        connectors = response.get('connectors', [])
        return [c for c in connectors if 'router' in c.get('trading_types', [])]
    except Exception as e:
        logger.warning(f"Error fetching router connectors: {e}")
        return []


async def _fetch_networks(client) -> list:
    """Fetch available networks"""
    try:
        response = await client.gateway.list_networks()
        return response.get('networks', [])
    except Exception as e:
        logger.warning(f"Error fetching networks: {e}")
        return []


def _get_routers_for_network(connectors: list, network_id: str) -> list:
    """Get router connectors available for a specific network"""
    if not network_id or not connectors:
        return connectors

    parts = network_id.split("-", 1)
    chain = parts[0] if parts else ""
    network = parts[1] if len(parts) > 1 else ""

    matching = []
    for c in connectors:
        c_chain = c.get('chain', '')
        c_networks = c.get('networks', [])
        if c_chain == chain and network in c_networks:
            matching.append(c)

    return matching if matching else connectors


# ============================================
# MENU DISPLAY
# ============================================

async def handle_swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle swap - unified menu for quote and execute"""
    if "swap_params" not in context.user_data:
        defaults = get_dex_swap_defaults(context.user_data)
        context.user_data["swap_params"] = defaults

    context.user_data["dex_state"] = "swap"

    await show_swap_menu(update, context)


async def show_swap_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, send_new: bool = False, quote_result: dict = None) -> None:
    """Display the unified swap menu with balances and recent swaps

    Args:
        update: The update object
        context: The context object
        send_new: If True, always send a new message instead of editing
        quote_result: Optional quote result to display inline
    """
    params = context.user_data.get("swap_params", {})

    # Get trading pair tokens for balance display
    trading_pair = params.get('trading_pair', 'SOL-USDC')
    network = params.get('network', 'solana-mainnet-beta')

    # Parse trading pair
    if '-' in trading_pair:
        base_token, quote_token = trading_pair.split('-', 1)
    else:
        base_token, quote_token = trading_pair, 'USDC'

    # Build header
    help_text = r"💱 *Swap*" + "\n\n"

    # Show wallet balances from cache
    try:
        gateway_data = get_cached(context.user_data, "gateway_balances", ttl=120)
        if gateway_data and gateway_data.get("balances_by_network"):
            network_key = network.split("-")[0].lower() if network else ""
            balances_found = {}

            for net_name, balances_list in gateway_data["balances_by_network"].items():
                if network_key in net_name.lower():
                    for bal in balances_list:
                        token = bal.get("token", "").upper()
                        if token == base_token.upper():
                            balances_found["base_balance"] = bal.get("units", 0)
                            balances_found["base_value"] = bal.get("value", 0)
                        elif token == quote_token.upper():
                            balances_found["quote_balance"] = bal.get("units", 0)
                            balances_found["quote_value"] = bal.get("value", 0)

            if balances_found.get("base_balance", 0) > 0 or balances_found.get("quote_balance", 0) > 0:
                if balances_found.get("base_balance", 0) > 0:
                    base_bal_str = _format_number(balances_found["base_balance"])
                    base_val_str = f"${_format_number(balances_found.get('base_value', 0))}" if balances_found.get("base_value", 0) > 0 else ""
                    help_text += f"💰 `{escape_markdown_v2(base_token)}`: `{escape_markdown_v2(base_bal_str)}` {escape_markdown_v2(base_val_str)}\n"

                if balances_found.get("quote_balance", 0) > 0:
                    quote_bal_str = _format_number(balances_found["quote_balance"])
                    quote_val_str = f"${_format_number(balances_found.get('quote_value', 0))}" if balances_found.get("quote_value", 0) > 0 else ""
                    help_text += f"💵 `{escape_markdown_v2(quote_token)}`: `{escape_markdown_v2(quote_bal_str)}` {escape_markdown_v2(quote_val_str)}\n"

                help_text += "\n"

    except Exception as e:
        logger.warning(f"Could not get cached balances: {e}")

    # Show quote result if available
    if quote_result:
        help_text += r"━━━ Quote ━━━" + "\n"
        pair = quote_result.get("trading_pair", trading_pair)
        amount_str = quote_result.get("amount", params.get("amount", "1"))

        # Show BUY quote (price = base/quote, display as quote/base = 1/price)
        buy_data = quote_result.get("buy")
        if buy_data and buy_data.get("price"):
            raw_price = float(buy_data["price"])
            display_price = 1 / raw_price if raw_price > 0 else 0
            buy_price_str = _format_number(display_price, 4)
            help_text += f"`{escape_markdown_v2(pair)} BUY {escape_markdown_v2(str(amount_str))} @{escape_markdown_v2(buy_price_str)}`\n"

        # Show SELL quote (price = quote/base, display as-is)
        sell_data = quote_result.get("sell")
        if sell_data and sell_data.get("price"):
            sell_price_str = _format_number(sell_data["price"], 4)
            help_text += f"`{escape_markdown_v2(pair)} SELL {escape_markdown_v2(str(amount_str))} @{escape_markdown_v2(sell_price_str)}`\n"

        # Show spread if available
        if "spread_pct" in quote_result:
            spread = quote_result["spread_pct"]
            spread_str = f"{spread:.2f}%"
            help_text += f"📊 Spread: `{escape_markdown_v2(spread_str)}`\n"

        help_text += "\n"

    # Type directly hint
    help_text += r"⌨️ `pair side amount [slippage]`" + "\n"
    example_pair = escape_markdown_v2(params.get('trading_pair', 'SOL-USDC'))
    example_side = params.get('side', 'BUY')
    example_amount = escape_markdown_v2(str(params.get('amount', '1.0')))
    help_text += f"*Ex:* `{example_pair} {example_side} {example_amount}`\n\n"

    # Fetch and show recent swaps (compact format)
    try:
        swaps = get_cached(context.user_data, "recent_swaps", ttl=60)
        if swaps is None:
            client = await get_client()
            swaps = await _fetch_recent_swaps(client, limit=5)
            set_cached(context.user_data, "recent_swaps", swaps)

        if swaps:
            help_text += r"━━━ Recent ━━━" + "\n"
            for swap in swaps[:5]:
                line = _format_compact_swap_line(swap)
                help_text += line + "\n"
    except Exception as e:
        logger.warning(f"Could not fetch recent swaps: {e}")

    # Build keyboard - compact layout
    keyboard = [
        [
            InlineKeyboardButton(
                f"🔌 {params.get('connector', 'jupiter')}",
                callback_data="dex:swap_set_connector"
            ),
            InlineKeyboardButton(
                f"🌐 {params.get('network', 'solana-mainnet-beta')}",
                callback_data="dex:swap_set_network"
            )
        ],
        [
            InlineKeyboardButton(
                f"💱 {params.get('trading_pair', 'SOL-USDC')}",
                callback_data="dex:swap_set_pair"
            ),
            InlineKeyboardButton(
                f"📈 {params.get('side', 'BUY')}",
                callback_data="dex:swap_toggle_side"
            )
        ],
        [
            InlineKeyboardButton(
                f"💰 {params.get('amount', '1.0')}",
                callback_data="dex:swap_set_amount"
            ),
            InlineKeyboardButton(
                f"📊 {params.get('slippage', '1.0')}%",
                callback_data="dex:swap_set_slippage"
            )
        ],
        [
            InlineKeyboardButton("💰 Quote", callback_data="dex:swap_get_quote"),
            InlineKeyboardButton("✅ Execute", callback_data="dex:swap_execute_confirm")
        ],
        [
            InlineKeyboardButton("🔍 History", callback_data="dex:swap_history"),
            InlineKeyboardButton("« Back", callback_data="dex:main_menu")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if send_new or not update.callback_query:
        await update.message.reply_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    else:
        await update.callback_query.message.edit_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )


# ============================================
# PARAMETER HANDLERS
# ============================================

async def handle_swap_toggle_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between BUY and SELL"""
    params = context.user_data.get("swap_params", {})
    current_side = params.get("side", "BUY")
    params["side"] = "SELL" if current_side == "BUY" else "BUY"
    await show_swap_menu(update, context)


async def handle_swap_set_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available router connectors for selection"""
    params = context.user_data.get("swap_params", {})
    network = params.get("network", "solana-mainnet-beta")

    try:
        client = await get_client()

        cache_key = "router_connectors"
        connectors = get_cached(context.user_data, cache_key, ttl=300)
        if connectors is None:
            connectors = await _fetch_router_connectors(client)
            set_cached(context.user_data, cache_key, connectors)

        available = _get_routers_for_network(connectors, network)

        if not available:
            help_text = r"🔌 *Select Connector*" + "\n\n" + r"_No routers available\._"
            keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]
        else:
            help_text = r"🔌 *Select Connector*"

            connector_buttons = []
            row = []
            for c in available:
                name = c.get('name', 'unknown')
                row.append(InlineKeyboardButton(name, callback_data=f"dex:swap_connector_{name}"))
                if len(row) == 3:
                    connector_buttons.append(row)
                    row = []
            if row:
                connector_buttons.append(row)

            keyboard = connector_buttons + [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing connectors: {e}", exc_info=True)
        error_text = format_error_message(f"Error loading connectors: {str(e)}")
        await update.callback_query.message.edit_text(error_text, parse_mode="MarkdownV2")


async def handle_swap_connector_select(update: Update, context: ContextTypes.DEFAULT_TYPE, connector_name: str) -> None:
    """Handle connector selection from button"""
    params = context.user_data.get("swap_params", {})
    params["connector"] = connector_name

    # Auto-update network based on connector
    cache_key = "router_connectors"
    connectors = get_cached(context.user_data, cache_key, ttl=300)
    if connectors:
        for c in connectors:
            if c.get('name') == connector_name:
                chain = c.get('chain', '')
                networks = c.get('networks', [])
                if chain == "solana" and "mainnet-beta" in networks:
                    params["network"] = "solana-mainnet-beta"
                elif chain == "ethereum" and "mainnet" in networks:
                    params["network"] = "ethereum-mainnet"
                elif chain == "ethereum" and networks:
                    params["network"] = f"ethereum-{networks[0]}"
                break

    context.user_data["dex_state"] = "swap"
    await show_swap_menu(update, context)


async def handle_swap_set_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available networks for selection"""
    try:
        client = await get_client()

        networks_cache_key = "gateway_networks"
        networks = get_cached(context.user_data, networks_cache_key, ttl=300)
        if networks is None:
            networks = await _fetch_networks(client)
            set_cached(context.user_data, networks_cache_key, networks)

        connectors_cache_key = "router_connectors"
        connectors = get_cached(context.user_data, connectors_cache_key, ttl=300)
        if connectors is None:
            connectors = await _fetch_router_connectors(client)
            set_cached(context.user_data, connectors_cache_key, connectors)

        # Filter to networks with routers
        router_networks = set()
        for c in connectors:
            chain = c.get('chain', '')
            for net in c.get('networks', []):
                router_networks.add((chain, net))

        available = [
            n for n in networks
            if (n.get('chain', ''), n.get('network', '')) in router_networks
        ]

        if not available:
            help_text = r"🌐 *Select Network*" + "\n\n" + r"_No networks available\._"
            keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]
        else:
            help_text = r"🌐 *Select Network*"

            network_buttons = []
            row = []
            for n in available:
                network_id = n.get('network_id', '')
                row.append(InlineKeyboardButton(network_id, callback_data=f"dex:swap_network_{network_id}"))
                if len(row) == 3:
                    network_buttons.append(row)
                    row = []
            if row:
                network_buttons.append(row)

            keyboard = network_buttons + [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            help_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing networks: {e}", exc_info=True)
        error_text = format_error_message(f"Error loading networks: {str(e)}")
        await update.callback_query.message.edit_text(error_text, parse_mode="MarkdownV2")


async def handle_swap_network_select(update: Update, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Handle network selection from button"""
    params = context.user_data.get("swap_params", {})
    params["network"] = network_id

    # Auto-update connector
    chain = network_id.split("-")[0] if network_id else ""
    network = network_id.split("-", 1)[1] if "-" in network_id else ""

    cache_key = "router_connectors"
    connectors = get_cached(context.user_data, cache_key, ttl=300)
    if connectors:
        for c in connectors:
            if c.get('chain') == chain and network in c.get('networks', []):
                params["connector"] = c.get('name')
                break

    context.user_data["dex_state"] = "swap"
    await show_swap_menu(update, context)


async def handle_swap_set_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input trading pair"""
    help_text = (
        r"📝 *Set Trading Pair*" + "\n\n"
        r"Enter the trading pair:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`SOL\-USDC`" + "\n"
        r"`ETH\-USDT`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_pair"
    context.user_data["dex_previous_state"] = "swap"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input amount"""
    help_text = (
        r"📝 *Set Amount*" + "\n\n"
        r"Enter the amount:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`1\.5`" + "\n"
        r"`0\.01`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_amount"
    context.user_data["dex_previous_state"] = "swap"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_set_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input slippage"""
    help_text = (
        r"📝 *Set Slippage*" + "\n\n"
        r"Enter slippage %:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`1\.0` \- 1%" + "\n"
        r"`2\.5` \- 2\.5%"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_slippage"
    context.user_data["dex_previous_state"] = "swap"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# QUOTE & EXECUTE
# ============================================

async def handle_swap_get_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get quote for both BUY and SELL in parallel, display with spread"""
    try:
        params = context.user_data.get("swap_params", {})

        connector = params.get("connector")
        network = params.get("network")
        trading_pair = params.get("trading_pair")
        amount = params.get("amount")
        slippage = params.get("slippage", "1.0")

        if not all([connector, network, trading_pair, amount]):
            raise ValueError("Missing required parameters")

        client = await get_client()

        # Fetch BUY and SELL quotes in parallel
        async def get_quote_safe(side: str):
            try:
                return await client.gateway_swap.get_swap_quote(
                    connector=connector,
                    network=network,
                    trading_pair=trading_pair,
                    side=side,
                    amount=Decimal(amount),
                    slippage_pct=Decimal(slippage)
                )
            except Exception as e:
                logger.warning(f"Quote failed for {side}: {e}")
                return None

        buy_result, sell_result = await asyncio.gather(
            get_quote_safe("BUY"),
            get_quote_safe("SELL")
        )

        if buy_result is None and sell_result is None:
            raise ValueError("No quotes available for this pair")

        # Build combined quote result
        quote_data = {
            "trading_pair": trading_pair,
            "amount": amount,
            "buy": buy_result if isinstance(buy_result, dict) else None,
            "sell": sell_result if isinstance(sell_result, dict) else None,
        }

        # Calculate spread if we have both quotes
        if buy_result and sell_result:
            raw_buy = buy_result.get("price", 0)
            raw_sell = sell_result.get("price", 0)
            if raw_buy and raw_sell:
                # Normalize prices to "quote per base" for comparison
                # BUY: raw = base/quote, display = 1/raw = quote/base
                # SELL: raw = quote/base, display = raw
                buy_display = 1 / raw_buy if raw_buy > 0 else 0
                sell_display = raw_sell
                # Spread = (buy_price - sell_price) / midpoint * 100
                # Higher buy price means you pay more to buy, lower sell means you get less selling
                if buy_display and sell_display:
                    midpoint = (buy_display + sell_display) / 2
                    spread_pct = abs(buy_display - sell_display) / midpoint * 100 if midpoint else 0
                    quote_data["spread_pct"] = spread_pct

        # Save params
        set_dex_last_swap(context.user_data, {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": params.get("side", "BUY"),
            "slippage": slippage
        })

        # Show menu with quote result inline
        await show_swap_menu(update, context, quote_result=quote_data)

    except Exception as e:
        logger.error(f"Error getting quote: {e}", exc_info=True)
        error_message = format_error_message(f"Quote failed: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


async def handle_swap_execute_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the swap with current parameters"""
    try:
        params = context.user_data.get("swap_params", {})

        connector = params.get("connector")
        network = params.get("network")
        trading_pair = params.get("trading_pair")
        side = params.get("side")
        amount = params.get("amount")
        slippage = params.get("slippage", "1.0")

        if not all([connector, network, trading_pair, side, amount]):
            raise ValueError("Missing required parameters")

        # Validate amount > 0
        amount_val = Decimal(str(amount))
        if amount_val <= 0:
            raise ValueError("Amount must be greater than 0")

        # Validate slippage > 0
        slippage_str = str(slippage).rstrip('%').strip()
        slippage_val = Decimal(slippage_str)
        if slippage_val <= 0:
            raise ValueError("Slippage must be greater than 0%")

        client = await get_client()

        if not hasattr(client, 'gateway_swap'):
            raise ValueError("Gateway swap not available")

        result = await client.gateway_swap.execute_swap(
            connector=connector,
            network=network,
            trading_pair=trading_pair,
            side=side,
            amount=Decimal(amount),
            slippage_pct=Decimal(slippage)
        )

        if result is None:
            raise ValueError("Swap execution failed")

        # Invalidate caches
        invalidate_cache(context.user_data, "balances", "swaps")

        # Save params
        set_dex_last_swap(context.user_data, {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "slippage": slippage
        })

        swap_info = escape_markdown_v2(
            f"✅ Swap executed!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount}\n"
            f"Slippage: {slippage}%"
        )

        if isinstance(result, dict):
            if 'tx_hash' in result:
                tx_short = result['tx_hash'][:16] + "..."
                swap_info += escape_markdown_v2(f"\nTx: {tx_short}")
            if 'status' in result:
                swap_info += escape_markdown_v2(f"\nStatus: {result['status']}")

        keyboard = [[InlineKeyboardButton("« Back to Swap", callback_data="dex:swap")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            swap_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error executing swap: {e}", exc_info=True)
        error_message = format_error_message(f"Swap failed: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


# ============================================
# HISTORY
# ============================================

async def handle_swap_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle swap status check - prompt for tx hash"""
    help_text = (
        r"📊 *Get Swap Status*" + "\n\n"
        r"Reply with transaction hash:" + "\n\n"
        r"`<tx_hash>`"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="dex:swap")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_status"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def process_swap_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tx_hash: str
) -> None:
    """Process swap status check"""
    try:
        client = await get_client()

        if not hasattr(client, 'gateway_swap'):
            raise ValueError("Gateway swap not available")

        result = await client.gateway_swap.get_swap_status(tx_hash)

        status_info = escape_markdown_v2(f"📊 Swap Status\n\nTx: {tx_hash[:16]}...\n")

        if isinstance(result, dict):
            for key in ['status', 'trading_pair', 'side', 'amount']:
                if key in result:
                    status_info += escape_markdown_v2(f"{key.title()}: {result[key]}\n")

        keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(status_info, parse_mode="MarkdownV2", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error getting status: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get status: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def handle_swap_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show full swap history"""
    try:
        client = await get_client()

        if not hasattr(client, 'gateway_swap'):
            error_message = format_error_message("Gateway swap not available")
            await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        result = await client.gateway_swap.search_swaps(limit=10)
        swaps = result.get("data", [])
        pagination = result.get("pagination", {})

        if not swaps:
            message = r"🔍 *Swap History*" + "\n\n" + r"No swaps found\."
            keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.callback_query.message.edit_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            return

        total_count = pagination.get("total_count", len(swaps))
        message = rf"🔍 *Swap History* \({len(swaps)}/{total_count}\)" + "\n\n"

        for swap in swaps:
            line = _format_compact_swap_line(swap)
            message += line + "\n"

        keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"Error fetching history: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch history: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


# ============================================
# TEXT INPUT PROCESSORS
# ============================================

async def process_swap(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap from text input: pair side amount [slippage]"""
    try:
        parts = user_input.split()

        if len(parts) < 3:
            raise ValueError("Need: pair side amount [slippage]")

        # Parse: pair side amount [slippage]
        trading_pair = parts[0]
        side = parts[1].upper()
        amount = parts[2]
        slippage = parts[3] if len(parts) > 3 else "1.0"

        # Get connector/network from defaults
        network = DEFAULT_DEX_NETWORK
        connector = get_dex_connector(context.user_data, network)

        # Update params
        context.user_data["swap_params"] = {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "amount": amount,
            "slippage": slippage,
        }

        context.user_data["dex_state"] = "swap"

        success_msg = escape_markdown_v2(f"✅ Updated: {trading_pair} {side} {amount}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error processing swap input: {e}", exc_info=True)
        error_message = format_error_message(f"Invalid input: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_pair(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process trading pair input"""
    try:
        params = context.user_data.get("swap_params", {})
        params["trading_pair"] = user_input.strip()

        context.user_data["dex_state"] = "swap"

        success_msg = escape_markdown_v2(f"✅ Pair: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting pair: {e}", exc_info=True)
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process amount input"""
    try:
        amount_str = user_input.strip()

        # Validate
        amount_val = Decimal(amount_str)
        if amount_val <= 0:
            raise ValueError("Amount must be > 0")

        params = context.user_data.get("swap_params", {})
        params["amount"] = amount_str

        context.user_data["dex_state"] = "swap"

        success_msg = escape_markdown_v2(f"✅ Amount: {amount_str}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting amount: {e}", exc_info=True)
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_slippage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process slippage input"""
    try:
        slippage_str = user_input.strip().rstrip('%').strip()

        # Validate
        slippage_val = Decimal(slippage_str)
        if slippage_val <= 0:
            raise ValueError("Slippage must be > 0%")

        params = context.user_data.get("swap_params", {})
        params["slippage"] = slippage_str

        context.user_data["dex_state"] = "swap"

        success_msg = escape_markdown_v2(f"✅ Slippage: {slippage_str}%")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting slippage: {e}", exc_info=True)
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
