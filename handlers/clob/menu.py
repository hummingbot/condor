"""
CLOB Trading main menu
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from utils.telegram_formatters import escape_markdown_v2, format_number
from handlers.config.user_preferences import get_clob_account

logger = logging.getLogger(__name__)


def format_cex_balances_compact(balances: dict) -> str:
    """Format CEX balances in a compact table format.

    Args:
        balances: Dict of connector_name -> list of balance dicts

    Returns:
        Formatted string for display
    """
    if not balances:
        return "_No CEX balances found_\n"

    message = ""

    for connector_name, connector_balances in balances.items():
        if not connector_balances:
            continue

        # Calculate total value for this connector
        total_value = sum(float(b.get("value", 0)) for b in connector_balances)

        # Filter balances > $1 and sort by value
        significant_balances = [
            b for b in connector_balances
            if float(b.get("value", 0)) >= 1
        ]
        significant_balances.sort(key=lambda x: float(x.get("value", 0)), reverse=True)

        if not significant_balances:
            continue

        # Connector header with total
        total_str = format_number(total_value)
        message += f"🏦 *{escape_markdown_v2(connector_name)}* \\- `{escape_markdown_v2(total_str)}`\n"

        # Build compact table
        table = "```\n"
        table += f"{'Token':<6} {'Amount':<10} {'Value':>8}\n"
        table += f"{'─'*6} {'─'*10} {'─'*8}\n"

        for balance in significant_balances[:5]:  # Show top 5
            token = balance.get("token", "???")
            units = float(balance.get("units", 0))
            value = float(balance.get("value", 0))

            # Truncate token name
            token_display = token[:5] if len(token) > 5 else token

            # Format units
            if units >= 1000:
                units_str = f"{units:,.0f}"[:9]
            elif units >= 1:
                units_str = f"{units:.2f}"[:9]
            elif units >= 0.0001:
                units_str = f"{units:.4f}"[:9]
            else:
                units_str = f"{units:.2e}"[:9]

            # Format value
            if value >= 1000:
                value_str = f"${value/1000:.1f}K"
            else:
                value_str = f"${value:.2f}"
            value_str = value_str[:8]

            table += f"{token_display:<6} {units_str:<10} {value_str:>8}\n"

        if len(significant_balances) > 5:
            table += f"... +{len(significant_balances) - 5} more\n"

        table += "```\n"
        message += table

    return message


async def show_clob_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display main CLOB trading menu with quick trading options and overview"""
    from utils.telegram_formatters import format_perpetual_positions, format_active_orders
    from ._shared import get_cex_balances, get_trading_rules, get_available_cex_connectors

    account = get_clob_account(context.user_data)

    # Build header with account info
    header = f"🏦 *CLOB Trading*\n\n"
    header += f"📋 Account: `{escape_markdown_v2(account)}`\n\n"

    # Try to fetch quick overview of balances, positions and orders
    positions = []
    orders = []
    try:
        from servers import server_manager

        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if enabled_servers:
            server_name = enabled_servers[0]
            client = await server_manager.get_client(server_name)

            # Fetch CEX balances with caching
            cex_balances = await get_cex_balances(
                context.user_data,
                client,
                account
            )

            # Display CEX balances section
            header += "💰 *CEX Balances*\n"
            header += format_cex_balances_compact(cex_balances)
            header += "\n"

            # Fetch trading rules for available connectors (cache for order validation later)
            available_connectors = await get_available_cex_connectors(context.user_data, client)
            for connector_name in available_connectors[:3]:  # Pre-cache top 3 connectors
                await get_trading_rules(context.user_data, client, connector_name)

            # Get positions and orders in parallel
            positions_result = await client.trading.get_positions(limit=5)
            orders_result = await client.trading.get_active_orders(limit=5)

            positions = positions_result.get("data", [])
            orders = orders_result.get("data", [])

            # Store positions in context for later use
            context.user_data["current_positions"] = positions

            # Use shared formatters from portfolio (same style)
            perp_data = {"positions": positions, "total": len(positions)}
            header += format_perpetual_positions(perp_data)

            header += "\n"  # Extra spacing between sections

            orders_data = {"orders": orders, "total": len(orders)}
            header += format_active_orders(orders_data)

    except Exception as e:
        logger.error(f"Error fetching overview data: {e}", exc_info=True)
        header += "_Could not fetch overview data_\n\n"

    header += "\nSelect an action:"

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
            InlineKeyboardButton("❌ Close", callback_data="clob:close")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                header,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise
    else:
        await update.message.reply_text(
            header,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle closing the CLOB trading interface"""
    # Clear CLOB state
    context.user_data.pop("clob_state", None)
    context.user_data.pop("place_order_params", None)
    context.user_data.pop("current_positions", None)

    # Delete the message instead of editing it
    await update.callback_query.message.delete()
