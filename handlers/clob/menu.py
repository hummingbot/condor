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


def _format_amount(value: float) -> str:
    """Format token amounts with appropriate precision"""
    if value == 0:
        return "0"
    elif abs(value) >= 1000000:
        return f"{value/1000000:.2f}M"
    elif abs(value) >= 1000:
        return f"{value/1000:.1f}K"
    elif abs(value) < 0.0001:
        return f"{value:.2e}"
    elif abs(value) < 1:
        return f"{value:.6f}".rstrip('0').rstrip('.')
    else:
        formatted = f"{value:.4f}".rstrip('0').rstrip('.')
        return formatted if '.' in f"{value:.4f}" else f"{value:.0f}"


def _format_value(value: float) -> str:
    """Format USD values"""
    if value >= 1000000:
        return f"${value/1000000:.2f}M"
    elif value >= 1000:
        return f"${value/1000:.2f}K"
    else:
        return f"${value:.2f}"


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

    # Calculate grand total across all connectors
    grand_total = 0
    for connector_balances in balances.values():
        if connector_balances:
            grand_total += sum(float(b.get("value", 0)) for b in connector_balances)

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

        # Calculate percentages for each balance
        for bal in significant_balances:
            bal["percentage"] = (float(bal.get("value", 0)) / grand_total * 100) if grand_total > 0 else 0

        # Connector header with total
        total_str = _format_value(total_value)
        message += f"🏦 *{escape_markdown_v2(connector_name)}* \\- `{escape_markdown_v2(total_str)}`\n"

        # Build table with percentage column
        table = "```\n"
        table += f"{'Token':<10} {'Amount':<12} {'Value':<10} {'%':>6}\n"
        table += f"{'─'*10} {'─'*12} {'─'*10} {'─'*6}\n"

        for balance in significant_balances[:5]:  # Show top 5
            token = balance.get("token", "???")
            units = float(balance.get("units", 0))
            value = float(balance.get("value", 0))
            pct = balance.get("percentage", 0)

            # Truncate token name
            token_display = token[:9] if len(token) > 9 else token

            # Format units
            units_str = _format_amount(units)[:11]

            # Format value (without $ for table alignment)
            value_str = _format_value(value).replace('$', '')[:9]

            table += f"{token_display:<10} {units_str:<12} {value_str:<10} {pct:>5.1f}%\n"

        if len(significant_balances) > 5:
            table += f"... +{len(significant_balances) - 5} more\n"

        table += "```\n"
        message += table

    # Show grand total if multiple connectors
    if len(balances) > 1 and grand_total > 0:
        total_str = _format_value(grand_total)
        message += f"💵 *Total:* `{escape_markdown_v2(total_str)}`\n"

    return message


# Key for storing the background loading task
CLOB_LOADING_TASK_KEY = "_clob_menu_loading_task"


def cancel_clob_loading_task(context) -> None:
    """Cancel any pending CLOB menu loading task"""
    task = context.user_data.get(CLOB_LOADING_TASK_KEY)
    if task and not task.done():
        task.cancel()
        logger.debug("Cancelled pending CLOB menu loading task")
    context.user_data.pop(CLOB_LOADING_TASK_KEY, None)


def _build_clob_keyboard() -> InlineKeyboardMarkup:
    """Build the CLOB menu keyboard"""
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
    return InlineKeyboardMarkup(keyboard)


def _build_clob_loading_message(account: str, server_name: str) -> str:
    """Build the initial loading message"""
    header = f"🏦 *CLOB Trading* \\| _Server: {escape_markdown_v2(server_name)}_\n\n"
    header += f"📋 Account: `{escape_markdown_v2(account)}`\n\n"
    header += r"💰 *CEX Balances*" + "\n"
    header += r"⏳ _Loading\.\.\._" + "\n\n"
    header += "Select an action:"
    return header


def _build_clob_menu_with_data(
    account: str,
    server_name: str,
    cex_balances: dict,
    positions: list,
    orders: list
) -> str:
    """Build the menu message with fetched data"""
    from utils.telegram_formatters import format_perpetual_positions, format_active_orders

    header = f"🏦 *CLOB Trading* \\| _Server: {escape_markdown_v2(server_name)}_\n\n"
    header += f"📋 Account: `{escape_markdown_v2(account)}`\n\n"

    # Display CEX balances section
    header += "💰 *CEX Balances*\n"
    header += format_cex_balances_compact(cex_balances)
    header += "\n"

    # Use shared formatters from portfolio (same style)
    perp_data = {"positions": positions, "total": len(positions)}
    header += format_perpetual_positions(perp_data)

    header += "\n"  # Extra spacing between sections

    orders_data = {"orders": orders, "total": len(orders)}
    header += format_active_orders(orders_data)

    header += "\nSelect an action:"
    return header


async def _load_clob_menu_data_background(
    message,
    context,
    reply_markup,
    account: str,
    server_name: str
) -> None:
    """Background task to load CLOB data and update the menu."""
    import asyncio
    from servers import get_client
    from ._shared import get_cex_balances, get_trading_rules, get_available_cex_connectors

    cex_balances = {}
    positions = []
    orders = []

    try:
        client = await get_client()

        # Fetch CEX balances with caching
        cex_balances = await get_cex_balances(
            context.user_data,
            client,
            account
        )

        # Update UI with balances immediately
        balances_message = _build_clob_menu_with_data(
            account, server_name, cex_balances, [], []
        )
        balances_message = balances_message.replace(
            "Select an action:",
            "_Loading positions & orders\\.\\.\\._\n\nSelect an action:"
        )
        try:
            await message.edit_text(
                balances_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception:
            pass

        # Fetch trading rules for available connectors (cache for order validation later)
        available_connectors = await get_available_cex_connectors(context.user_data, client)
        for connector_name in available_connectors[:3]:
            await get_trading_rules(context.user_data, client, connector_name)

        # Get positions and orders in parallel
        positions_task = asyncio.create_task(client.trading.get_positions(limit=5))
        orders_task = asyncio.create_task(client.trading.get_active_orders(limit=5))

        positions_result, orders_result = await asyncio.gather(positions_task, orders_task)

        positions = positions_result.get("data", [])
        orders = orders_result.get("data", [])

        # Store positions in context for later use
        context.user_data["current_positions"] = positions

    except asyncio.CancelledError:
        logger.debug("CLOB menu data loading was cancelled")
        return
    except Exception as e:
        logger.error(f"Error fetching CLOB overview data: {e}", exc_info=True)

    # Final update with all data
    final_message = _build_clob_menu_with_data(
        account, server_name, cex_balances, positions, orders
    )

    try:
        await message.edit_text(
            final_message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except asyncio.CancelledError:
        logger.debug("CLOB menu update was cancelled")
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning(f"Failed to update CLOB menu: {e}")
    finally:
        context.user_data.pop(CLOB_LOADING_TASK_KEY, None)


async def show_clob_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display main CLOB trading menu with quick trading options and overview.

    Uses progressive loading: shows menu immediately, then loads data in background.
    """
    import asyncio
    from servers import server_manager

    # Cancel any existing loading task
    cancel_clob_loading_task(context)

    account = get_clob_account(context.user_data)
    server_name = server_manager.default_server or "unknown"

    reply_markup = _build_clob_keyboard()
    loading_message = _build_clob_loading_message(account, server_name)

    # Show menu immediately with loading indicator
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                loading_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            message = update.callback_query.message
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise
            message = update.callback_query.message
    else:
        message = await update.message.reply_text(
            loading_message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    # Spawn background task to load data - user can interact immediately
    task = asyncio.create_task(
        _load_clob_menu_data_background(message, context, reply_markup, account, server_name)
    )
    context.user_data[CLOB_LOADING_TASK_KEY] = task


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle closing the CLOB trading interface"""
    # Cancel any pending loading task
    cancel_clob_loading_task(context)

    # Clear CLOB state
    context.user_data.pop("clob_state", None)
    context.user_data.pop("place_order_params", None)
    context.user_data.pop("current_positions", None)

    # Delete the message instead of editing it
    await update.callback_query.message.delete()
