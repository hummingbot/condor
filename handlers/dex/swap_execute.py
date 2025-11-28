"""
DEX Swap Execute functionality

Provides:
- Execute swap menu display
- Parameter setting handlers
- Swap execution
- Quick swap with last params
"""

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
from ._shared import get_gateway_client, get_cached, set_cached, DEFAULT_CACHE_TTL

logger = logging.getLogger(__name__)


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


async def _fetch_token_balances(client, network: str, base_token: str, quote_token: str) -> dict:
    """Fetch wallet balances for base and quote tokens"""
    result = {
        "base_balance": 0.0,
        "quote_balance": 0.0,
        "base_value": 0.0,
        "quote_value": 0.0,
    }

    try:
        if not hasattr(client, 'portfolio'):
            return result

        state = await client.portfolio.get_state()
        if not state:
            return result

        base_upper = base_token.upper() if base_token else ""
        quote_upper = quote_token.upper() if quote_token else ""
        network_key = network.split("-")[0].lower() if network else ""

        for account_name, account_data in state.items():
            for connector_name, balances in account_data.items():
                connector_lower = connector_name.lower()
                is_match = (
                    network_key in connector_lower or
                    "gateway" in connector_lower and network_key in connector_lower
                )

                if is_match and balances:
                    for bal in balances:
                        token = bal.get("token", "").upper()
                        units = float(bal.get("units", 0) or 0)
                        value = float(bal.get("value", 0) or 0)

                        if token == base_upper:
                            result["base_balance"] = units
                            result["base_value"] = value
                        elif token == quote_upper:
                            result["quote_balance"] = units
                            result["quote_value"] = value

    except Exception as e:
        logger.warning(f"Error fetching token balances: {e}")

    return result


async def _fetch_router_connectors(client) -> list:
    """Fetch connectors that have 'router' trading type"""
    try:
        response = await client.gateway.list_connectors()
        connectors = response.get('connectors', [])
        # Filter to only router connectors
        router_connectors = [
            c for c in connectors
            if 'router' in c.get('trading_types', [])
        ]
        return router_connectors
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

    # Parse network_id to get chain and network
    # e.g., "solana-mainnet-beta" -> chain="solana", network="mainnet-beta"
    # e.g., "ethereum-arbitrum" -> chain="ethereum", network="arbitrum"
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


def _get_networks_for_chain(networks: list, chain: str) -> list:
    """Get networks available for a specific chain"""
    if not chain or not networks:
        return networks

    return [n for n in networks if n.get('chain', '') == chain]


# ============================================
# MENU DISPLAY
# ============================================

async def handle_swap_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle swap execute - interactive menu"""

    if "execute_swap_params" not in context.user_data:
        defaults = get_dex_swap_defaults(context.user_data)
        context.user_data["execute_swap_params"] = defaults

    context.user_data["dex_state"] = "swap_execute"

    await show_swap_execute_menu(update, context)


async def show_swap_execute_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, send_new: bool = False) -> None:
    """Display the swap execution configuration menu with interactive buttons

    Args:
        update: The update object
        context: The context object
        send_new: If True, always send a new message instead of editing
    """
    params = context.user_data.get("execute_swap_params", {})

    # Get trading pair tokens for balance display
    trading_pair = params.get('trading_pair', 'SOL-USDC')
    network = params.get('network', 'solana-mainnet-beta')

    # Parse trading pair to get base and quote tokens
    if '-' in trading_pair:
        base_token, quote_token = trading_pair.split('-', 1)
    else:
        base_token, quote_token = trading_pair, 'USDC'

    # Build header
    help_text = r"✅ *Execute Swap*" + "\n\n"

    # Use cached gateway_data from main menu (already fetched, no blocking)
    try:
        gateway_data = get_cached(context.user_data, "gateway_data", ttl=120)
        if gateway_data and gateway_data.get("balances_by_network"):
            # Find balances for current network
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
                help_text += r"━━━ Wallet Balances ━━━" + "\n"

                if balances_found.get("base_balance", 0) > 0:
                    base_bal_str = _format_number(balances_found["base_balance"])
                    base_val_str = f"${_format_number(balances_found.get('base_value', 0))}" if balances_found.get("base_value", 0) > 0 else ""
                    help_text += f"💰 `{escape_markdown_v2(base_token)}`: `{escape_markdown_v2(base_bal_str)}` {escape_markdown_v2(base_val_str)}\n"

                if balances_found.get("quote_balance", 0) > 0:
                    quote_bal_str = _format_number(balances_found["quote_balance"])
                    quote_val_str = f"${_format_number(balances_found.get('quote_value', 0))}" if balances_found.get("quote_value", 0) > 0 else ""
                    help_text += f"💵 `{escape_markdown_v2(quote_token)}`: `{escape_markdown_v2(quote_bal_str)}` {escape_markdown_v2(quote_val_str)}\n"

                context.user_data["swap_token_balances"] = balances_found
                help_text += "\n"

    except Exception as e:
        logger.warning(f"Could not get cached balances: {e}")

    help_text += r"*⌨️ Or Type Directly*" + "\n"
    help_text += r"`trading_pair side amount [slippage]`" + "\n"
    help_text += r"*Example:* `SOL\-USDC BUY 1\.5`" + "\n"

    # Build keyboard - values shown in buttons
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
            InlineKeyboardButton("✅ Execute Swap", callback_data="dex:swap_execute_confirm"),
            InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")
        ]
    ]

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
# PARAMETER HANDLERS
# ============================================

async def handle_swap_toggle_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between BUY and SELL"""
    params = context.user_data.get("execute_swap_params", {})
    current_side = params.get("side", "BUY")
    params["side"] = "SELL" if current_side == "BUY" else "BUY"
    await show_swap_execute_menu(update, context)


async def handle_swap_set_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available router connectors for selection"""
    params = context.user_data.get("execute_swap_params", {})
    network = params.get("network", "solana-mainnet-beta")

    try:
        client = await get_gateway_client()

        # Fetch router connectors (cached)
        cache_key = "router_connectors"
        connectors = get_cached(context.user_data, cache_key, ttl=300)
        if connectors is None:
            connectors = await _fetch_router_connectors(client)
            set_cached(context.user_data, cache_key, connectors)

        # Filter to connectors available for current network
        available = _get_routers_for_network(connectors, network)

        if not available:
            help_text = (
                r"🔌 *Select Connector*" + "\n\n"
                r"_No router connectors available for this network\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
        else:
            help_text = (
                r"🔌 *Select Connector*" + "\n\n"
                r"_Choose a DEX router:_"
            )

            # Build connector buttons (2 per row)
            connector_buttons = []
            row = []
            for c in available:
                name = c.get('name', 'unknown')
                chain = c.get('chain', '')
                btn_text = f"{name} ({chain})"
                row.append(InlineKeyboardButton(btn_text, callback_data=f"dex:swap_connector_{name}"))
                if len(row) == 2:
                    connector_buttons.append(row)
                    row = []
            if row:
                connector_buttons.append(row)

            keyboard = connector_buttons + [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]

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
    params = context.user_data.get("execute_swap_params", {})
    params["connector"] = connector_name

    # Auto-update network based on connector chain
    cache_key = "router_connectors"
    connectors = get_cached(context.user_data, cache_key, ttl=300)
    if connectors:
        for c in connectors:
            if c.get('name') == connector_name:
                chain = c.get('chain', '')
                networks = c.get('networks', [])
                # Set a default network for this chain
                if chain == "solana" and "mainnet-beta" in networks:
                    params["network"] = "solana-mainnet-beta"
                elif chain == "ethereum" and "mainnet" in networks:
                    params["network"] = "ethereum-mainnet"
                elif chain == "ethereum" and networks:
                    # Pick first available network
                    params["network"] = f"ethereum-{networks[0]}"
                break

    context.user_data["dex_state"] = "swap_execute"
    await show_swap_execute_menu(update, context)


async def handle_swap_set_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available networks for selection"""
    params = context.user_data.get("execute_swap_params", {})
    current_connector = params.get("connector", "jupiter")

    try:
        client = await get_gateway_client()

        # Fetch networks (cached)
        networks_cache_key = "gateway_networks"
        networks = get_cached(context.user_data, networks_cache_key, ttl=300)
        if networks is None:
            networks = await _fetch_networks(client)
            set_cached(context.user_data, networks_cache_key, networks)

        # Also get router connectors to filter networks that have routers
        connectors_cache_key = "router_connectors"
        connectors = get_cached(context.user_data, connectors_cache_key, ttl=300)
        if connectors is None:
            connectors = await _fetch_router_connectors(client)
            set_cached(context.user_data, connectors_cache_key, connectors)

        # Build set of (chain, network) pairs that have routers
        router_networks = set()
        for c in connectors:
            chain = c.get('chain', '')
            for net in c.get('networks', []):
                router_networks.add((chain, net))

        # Filter networks to only those with router connectors
        available = [
            n for n in networks
            if (n.get('chain', ''), n.get('network', '')) in router_networks
        ]

        if not available:
            help_text = (
                r"🌐 *Select Network*" + "\n\n"
                r"_No networks available\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
        else:
            help_text = (
                r"🌐 *Select Network*" + "\n\n"
                r"_Choose a network:_"
            )

            # Build network buttons (2 per row)
            network_buttons = []
            row = []
            for n in available:
                network_id = n.get('network_id', '')
                row.append(InlineKeyboardButton(network_id, callback_data=f"dex:swap_network_{network_id}"))
                if len(row) == 2:
                    network_buttons.append(row)
                    row = []
            if row:
                network_buttons.append(row)

            keyboard = network_buttons + [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]

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
    params = context.user_data.get("execute_swap_params", {})
    params["network"] = network_id

    # Auto-update connector based on network chain
    # Parse network_id to get chain (e.g., "solana-mainnet-beta" -> "solana")
    chain = network_id.split("-")[0] if network_id else ""
    network = network_id.split("-", 1)[1] if "-" in network_id else ""

    # Get cached router connectors and find one for this network
    cache_key = "router_connectors"
    connectors = get_cached(context.user_data, cache_key, ttl=300)
    if connectors:
        # Find a router connector that supports this network
        for c in connectors:
            if c.get('chain') == chain and network in c.get('networks', []):
                params["connector"] = c.get('name')
                break

    context.user_data["dex_state"] = "swap_execute"
    await show_swap_execute_menu(update, context)


async def handle_swap_set_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input trading pair"""
    help_text = (
        r"📝 *Set Trading Pair*" + "\n\n"
        r"Enter the trading pair:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`SOL\-USDC`" + "\n"
        r"`ETH\-USDT`" + "\n"
        r"`BTC\-USDC`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_pair"
    context.user_data["dex_previous_state"] = "swap_execute"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input amount"""
    help_text = (
        r"📝 *Set Amount*" + "\n\n"
        r"Enter the amount to swap:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`1\.5` \- Swap 1\.5 tokens" + "\n"
        r"`0\.01` \- Swap 0\.01 tokens"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_amount"
    context.user_data["dex_previous_state"] = "swap_execute"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_swap_set_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input slippage"""
    help_text = (
        r"📝 *Set Slippage*" + "\n\n"
        r"Enter the slippage percentage:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`1\.0` \- 1% slippage" + "\n"
        r"`2\.5` \- 2\.5% slippage"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:swap_execute")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "swap_set_slippage"
    context.user_data["dex_previous_state"] = "swap_execute"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# SWAP EXECUTION
# ============================================

async def handle_swap_execute_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the swap with current parameters"""
    try:
        params = context.user_data.get("execute_swap_params", {})

        connector = params.get("connector")
        network = params.get("network")
        trading_pair = params.get("trading_pair")
        side = params.get("side")
        amount = params.get("amount")
        slippage = params.get("slippage")

        if not all([connector, network, trading_pair, side, amount]):
            raise ValueError("Missing required parameters")

        # Validate amount > 0
        try:
            amount_val = Decimal(str(amount))
            if amount_val <= 0:
                raise ValueError("Amount must be greater than 0")
        except Exception:
            raise ValueError("Amount must be a valid number greater than 0")

        # Validate slippage > 0%
        try:
            slippage_str = str(slippage).rstrip('%').strip()
            slippage_val = Decimal(slippage_str)
            if slippage_val <= 0:
                raise ValueError("Slippage must be greater than 0%")
        except Exception:
            raise ValueError("Slippage must be a valid number greater than 0%")

        client = await get_gateway_client()

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
            raise ValueError("Gateway returned no response. The swap execution may have failed.")

        # Save parameters
        set_dex_last_swap(context.user_data, {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "slippage": slippage
        })

        context.user_data["execute_swap_params"] = {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "amount": amount,
            "slippage": slippage,
        }

        swap_info = escape_markdown_v2(
            f"✅ Swap executed successfully!\n\n"
            f"Connector: {connector}\n"
            f"Network: {network}\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount}\n"
            f"Slippage: {slippage}%"
        )

        if isinstance(result, dict):
            if 'tx_hash' in result:
                swap_info += escape_markdown_v2(f"\nTx: {result['tx_hash'][:16]}...")
            if 'status' in result:
                swap_info += escape_markdown_v2(f"\nStatus: {result['status']}")

        keyboard = [[InlineKeyboardButton("« Back to DEX Trading", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            swap_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error executing swap: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to execute swap: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


# ============================================
# QUICK SWAP
# ============================================

async def handle_quick_swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quick swap with last used parameters"""
    help_text = (
        r"⚡ *Quick Swap*" + "\n\n"
        r"Reply with: `side amount [slippage]`" + "\n\n"
        r"*Examples:*" + "\n"
        r"`BUY 1\.5` \- Buy with last params" + "\n"
        r"`SELL 0\.5 2\.0` \- Sell with 2% slippage"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "quick_swap"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def process_quick_swap(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process quick swap"""
    try:
        parts = user_input.split()
        if len(parts) < 2:
            raise ValueError("Need: side amount [slippage]")

        side = parts[0].upper()
        amount = parts[1]
        slippage = parts[2] if len(parts) > 2 else "1.0"

        last_params = get_dex_last_swap(context.user_data)
        if not last_params or "connector" not in last_params:
            raise ValueError("No previous swap parameters. Use 'Get Quote' first.")

        connector = last_params["connector"]
        network = last_params["network"]
        trading_pair = last_params["trading_pair"]

        client = await get_gateway_client()

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
            raise ValueError("Gateway returned no response.")

        swap_info = escape_markdown_v2(
            f"✅ Quick Swap Executed!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount}"
        )

        if isinstance(result, dict) and 'tx_hash' in result:
            swap_info += escape_markdown_v2(f"\nTx: {result['tx_hash'][:16]}...")

        await update.message.reply_text(swap_info, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error in quick swap: {e}", exc_info=True)
        error_message = format_error_message(f"Quick swap failed: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# TEXT INPUT PROCESSORS
# ============================================

async def process_swap_execute(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap execute from text input"""
    try:
        parts = user_input.split()

        if len(parts) < 3:
            raise ValueError("Need at least: trading_pair side amount\n"
                           "Optional: connector network trading_pair side amount [slippage]")

        if len(parts) >= 5:
            connector = parts[0]
            network = parts[1]
            trading_pair = parts[2]
            side = parts[3].upper()
            amount = parts[4]
            slippage = parts[5] if len(parts) > 5 else "1.0"
        else:
            network = DEFAULT_DEX_NETWORK
            connector = get_dex_connector(context.user_data, network)
            trading_pair = parts[0]
            side = parts[1].upper()
            amount = parts[2]
            slippage = parts[3] if len(parts) > 3 else "1.0"

        client = await get_gateway_client()

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
            raise ValueError("Gateway returned no response.")

        set_dex_last_swap(context.user_data, {
            "connector": connector,
            "network": network,
            "trading_pair": trading_pair,
            "side": side,
            "slippage": slippage
        })

        swap_info = escape_markdown_v2(
            f"✅ Swap Executed!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount}"
        )

        if isinstance(result, dict):
            if 'tx_hash' in result:
                swap_info += escape_markdown_v2(f"\nTx: {result['tx_hash'][:16]}...")
            if 'status' in result:
                swap_info += escape_markdown_v2(f"\nStatus: {result['status']}")

        keyboard = [[InlineKeyboardButton("« Back to DEX Trading", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            swap_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error executing swap: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to execute swap: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_connector(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set connector input"""
    try:
        params = context.user_data.get("execute_swap_params", {})
        params["connector"] = user_input.strip()

        connector = params["connector"]
        if connector in ["jupiter", "meteora", "raydium"]:
            params["network"] = "solana-mainnet-beta"
        elif connector == "uniswap":
            params["network"] = "ethereum-mainnet"

        context.user_data["dex_state"] = "swap_execute"

        success_msg = escape_markdown_v2(f"✅ Connector set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_execute_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting connector: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set connector: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_network(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set network input"""
    try:
        params = context.user_data.get("execute_swap_params", {})
        network = user_input.strip()
        params["network"] = network
        params["connector"] = get_dex_connector(context.user_data, network)

        context.user_data["dex_state"] = "swap_execute"

        success_msg = escape_markdown_v2(f"✅ Network set to: {network}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_execute_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting network: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set network: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_pair(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set trading pair input"""
    try:
        params = context.user_data.get("execute_swap_params", {})
        params["trading_pair"] = user_input.strip()

        context.user_data["dex_state"] = "swap_execute"

        success_msg = escape_markdown_v2(f"✅ Trading pair set to: {user_input}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_execute_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting trading pair: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set trading pair: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set amount input"""
    try:
        logger.info(f"Processing swap set amount: {user_input}")
        amount_str = user_input.strip()

        # Validate amount > 0
        try:
            amount_val = Decimal(amount_str)
            if amount_val <= 0:
                raise ValueError("Amount must be greater than 0")
        except Exception:
            raise ValueError("Amount must be a valid number greater than 0")

        params = context.user_data.get("execute_swap_params", {})
        params["amount"] = amount_str

        context.user_data["dex_state"] = "swap_execute"
        logger.info(f"Updated params: {params}, restored state to: swap_execute")

        success_msg = escape_markdown_v2(f"✅ Amount set to: {amount_str}")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_execute_menu(update, context, send_new=True)
        logger.info("Showed swap execute menu")

    except Exception as e:
        logger.error(f"Error setting amount: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set amount: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_swap_set_slippage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process swap set slippage input"""
    try:
        # Strip any % sign and validate
        slippage_str = user_input.strip().rstrip('%').strip()

        # Validate slippage > 0%
        try:
            slippage_val = Decimal(slippage_str)
            if slippage_val <= 0:
                raise ValueError("Slippage must be greater than 0%")
        except Exception:
            raise ValueError("Slippage must be a valid number greater than 0%")

        params = context.user_data.get("execute_swap_params", {})
        params["slippage"] = slippage_str

        context.user_data["dex_state"] = "swap_execute"

        success_msg = escape_markdown_v2(f"✅ Slippage set to: {slippage_str}%")
        await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
        await show_swap_execute_menu(update, context, send_new=True)

    except Exception as e:
        logger.error(f"Error setting slippage: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set slippage: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
