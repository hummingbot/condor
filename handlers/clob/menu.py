"""
CLOB Trading main menu
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2
from handlers.config.user_preferences import get_clob_account

logger = logging.getLogger(__name__)


async def show_clob_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display main CLOB trading menu with quick trading options and overview"""
    from utils.telegram_formatters import format_perpetual_positions, format_active_orders

    account = get_clob_account(context.user_data)

    # Build header with account info
    header = f"🏦 *CLOB Trading*\n\n"
    header += f"📋 Account: `{escape_markdown_v2(account)}`\n\n"

    # Try to fetch quick overview of positions and orders
    positions = []
    orders = []
    try:
        from servers import server_manager

        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if enabled_servers:
            server_name = enabled_servers[0]
            client = await server_manager.get_client(server_name)

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

            orders_data = {"orders": orders, "total": len(orders)}
            header += format_active_orders(orders_data)

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
            InlineKeyboardButton("❌ Close", callback_data="clob:close")
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


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle closing the CLOB trading interface"""
    # Clear CLOB state
    context.user_data.pop("clob_state", None)
    context.user_data.pop("place_order_params", None)
    context.user_data.pop("current_positions", None)

    await update.callback_query.message.edit_text(
        "CLOB Trading closed\\.",
        parse_mode="MarkdownV2"
    )
