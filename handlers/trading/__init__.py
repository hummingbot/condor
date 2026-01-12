"""
Unified Trading Entry Point

Routes to DEX (swap.py) or CEX (trade.py) based on connector type.
Provides a single /trade command that works with both CEX and DEX connectors.
Uses portfolio connectors (ones with API keys configured).
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.auth import restricted, hummingbot_api_required
from utils.telegram_formatters import escape_markdown_v2
from handlers import clear_all_input_states, is_gateway_network
from handlers.config.user_preferences import (
    get_last_trade_connector,
    set_last_trade_connector,
    get_clob_order_defaults,
    get_dex_swap_defaults,
)
from handlers.cex.trade import handle_trade as cex_handle_trade
from handlers.dex.swap import handle_swap as dex_handle_swap

logger = logging.getLogger(__name__)


def _format_network_display(network_id: str) -> str:
    """Format network ID for button display.

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


async def _get_portfolio_connectors(client) -> tuple:
    """Get connectors from portfolio state, split by type.

    Returns:
        (cex_connectors, gateway_networks) - both from portfolio.get_state()
    """
    try:
        state = await client.portfolio.get_state()
        # state = {account_name: {connector_name: [balances]}}

        cex = set()
        gateway = set()

        for account_data in state.values():
            if isinstance(account_data, dict):
                for connector_name in account_data.keys():
                    if is_gateway_network(connector_name):
                        gateway.add(connector_name)
                    else:
                        cex.add(connector_name)

        return sorted(cex), sorted(gateway)
    except Exception as e:
        logger.warning(f"Error fetching portfolio connectors: {e}")
        return [], []


@restricted
@hummingbot_api_required
async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unified /trade command - routes to CEX or DEX based on last-used connector"""
    clear_all_input_states(context)

    # Get last-used connector type and name
    connector_type, connector_name = get_last_trade_connector(context.user_data)

    if connector_type == "cex" and connector_name:
        # Route to CEX trade
        defaults = get_clob_order_defaults(context.user_data)
        defaults["connector"] = connector_name
        context.user_data["trade_params"] = defaults
        await cex_handle_trade(update, context)
    elif connector_type == "dex" and connector_name:
        # Route to DEX swap with network pre-set
        # For DEX, connector_name is actually the network (e.g., solana-mainnet-beta)
        defaults = get_dex_swap_defaults(context.user_data)
        defaults["network"] = connector_name
        context.user_data["swap_params"] = defaults
        await dex_handle_swap(update, context)
    else:
        # First time or no preference - show connector selector
        await handle_unified_connector_select(update, context)


async def handle_unified_connector_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all connectors (CEX + DEX networks) from portfolio for selection"""
    chat_id = update.effective_chat.id

    try:
        from config_manager import get_client
        client = await get_client(chat_id, context=context)

        # Fetch connectors from portfolio (ones with API keys/wallets configured)
        cex_connectors, gateway_networks = await _get_portfolio_connectors(client)

        # Build keyboard with groups
        keyboard = []

        # CEX section - connector names (binance, bybit_perpetual, etc.)
        if cex_connectors:
            keyboard.append([InlineKeyboardButton("━━ CEX ━━", callback_data="trade:noop")])
            row = []
            for connector in cex_connectors:
                row.append(InlineKeyboardButton(
                    connector,
                    callback_data=f"trade:select_cex:{connector}"
                ))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

        # DEX section - network names (solana-mainnet-beta, ethereum-mainnet, etc.)
        if gateway_networks:
            keyboard.append([InlineKeyboardButton("━━ DEX ━━", callback_data="trade:noop")])
            row = []
            for network in gateway_networks:
                display = _format_network_display(network)
                row.append(InlineKeyboardButton(
                    display,
                    callback_data=f"trade:select_dex:{network}"
                ))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

        if not cex_connectors and not gateway_networks:
            help_text = escape_markdown_v2(
                "🔄 Select Connector\n\n"
                "No connectors found in portfolio.\n"
                "Add API keys via /config to get started."
            )
        else:
            help_text = r"🔄 *Select Connector*" + "\n\n" + r"Choose a trading connector:"

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.message.edit_text(
                help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error showing connector selector: {e}", exc_info=True)
        error_text = escape_markdown_v2(f"Error loading connectors: {str(e)}")
        if update.callback_query:
            await update.callback_query.message.edit_text(error_text, parse_mode="MarkdownV2")
        else:
            await update.message.reply_text(error_text, parse_mode="MarkdownV2")


async def handle_select_cex_connector(update: Update, context: ContextTypes.DEFAULT_TYPE, connector_name: str) -> None:
    """Handle CEX connector selection from unified selector"""
    # Save preference
    set_last_trade_connector(context.user_data, "cex", connector_name)

    # Pre-set connector and delegate to CEX trade
    defaults = get_clob_order_defaults(context.user_data)
    defaults["connector"] = connector_name
    context.user_data["trade_params"] = defaults

    await cex_handle_trade(update, context)


async def handle_select_dex_network(update: Update, context: ContextTypes.DEFAULT_TYPE, network: str) -> None:
    """Handle DEX network selection from unified selector"""
    # Save preference - for DEX we store the network (e.g., solana-mainnet-beta)
    set_last_trade_connector(context.user_data, "dex", network)

    # Pre-set network and delegate to DEX swap
    defaults = get_dex_swap_defaults(context.user_data)
    defaults["network"] = network
    context.user_data["swap_params"] = defaults

    await dex_handle_swap(update, context)


async def handle_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back from connector selector - go to last used connector"""
    connector_type, connector_name = get_last_trade_connector(context.user_data)

    if connector_type == "cex":
        await cex_handle_trade(update, context)
    else:
        await dex_handle_swap(update, context)
