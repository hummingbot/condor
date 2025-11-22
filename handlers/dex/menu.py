"""
DEX Trading main menu

Provides:
- Main DEX trading menu display with balances and positions
- Close functionality
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2
from handlers.config.user_preferences import get_dex_last_swap
from ._shared import get_gateway_client

logger = logging.getLogger(__name__)


def _format_balance_line(token: str, units: float, value: float) -> str:
    """Format a single balance line"""
    if units >= 1000:
        units_str = f"{units:,.0f}"
    elif units >= 1:
        units_str = f"{units:.2f}"
    else:
        units_str = f"{units:.4f}"

    if value >= 1000:
        value_str = f"${value:,.0f}"
    elif value >= 1:
        value_str = f"${value:.2f}"
    else:
        value_str = f"${value:.4f}"

    return f"  • {token}: {units_str} ({value_str})"


def _format_position_line(pos: dict) -> str:
    """Format a single LP position line"""
    pair = pos.get('trading_pair', pos.get('pool_name', 'Unknown'))
    connector = pos.get('connector', 'unknown')

    # Get amounts
    amount_a = pos.get('amount_a', pos.get('token_a_amount', 0))
    amount_b = pos.get('amount_b', pos.get('token_b_amount', 0))
    token_a = pos.get('token_a', pos.get('base_token', ''))
    token_b = pos.get('token_b', pos.get('quote_token', ''))

    # Get price range
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))

    # Format amounts
    if amount_a and amount_b:
        amounts = f"{_format_amount(amount_a)} {token_a} / {_format_amount(amount_b)} {token_b}"
    else:
        amounts = "N/A"

    # Format range
    if lower and upper:
        range_str = f"[{_format_price(lower)}-{_format_price(upper)}]"
    else:
        range_str = ""

    return f"  • {pair} ({connector}): {amounts} {range_str}"


def _format_amount(amount) -> str:
    """Format amount for display"""
    try:
        num = float(amount)
        if num >= 1000:
            return f"{num:,.0f}"
        elif num >= 1:
            return f"{num:.2f}"
        elif num >= 0.0001:
            return f"{num:.4f}"
        else:
            return f"{num:.6f}"
    except (ValueError, TypeError):
        return str(amount)


def _format_price(price) -> str:
    """Format price for display"""
    try:
        num = float(price)
        if num >= 1:
            return f"{num:.2f}"
        elif num >= 0.0001:
            return f"{num:.4f}"
        else:
            return f"{num:.6f}"
    except (ValueError, TypeError):
        return str(price)


async def _fetch_gateway_data(client) -> dict:
    """Fetch gateway balances and LP positions"""
    import asyncio

    data = {
        "balances": [],
        "lp_positions": [],
        "total_value": 0
    }

    try:
        # Fetch portfolio state and LP positions in parallel
        tasks = []

        # Portfolio state for gateway balances
        if hasattr(client, 'portfolio'):
            tasks.append(("state", client.portfolio.get_state()))

        # LP positions
        if hasattr(client, 'gateway_clmm'):
            tasks.append(("lp", client.gateway_clmm.search_positions(
                limit=100,
                offset=0,
                status="OPEN"
            )))

        if not tasks:
            return data

        task_names = [t[0] for t in tasks]
        task_coros = [t[1] for t in tasks]

        results = await asyncio.gather(*task_coros, return_exceptions=True)

        for i, name in enumerate(task_names):
            result = results[i]
            if isinstance(result, Exception):
                logger.warning(f"Failed to fetch {name}: {result}")
                continue

            if name == "state" and result:
                # Extract gateway balances from portfolio state
                for account_name, account_data in result.items():
                    for connector_name, balances in account_data.items():
                        # Only show gateway connectors (solana, etc)
                        if "gateway" in connector_name.lower() or connector_name.lower() in ["solana", "ethereum"]:
                            if balances:
                                for balance in balances:
                                    token = balance.get("token", "???")
                                    units = balance.get("units", 0)
                                    value = balance.get("value", 0)
                                    if value > 0.01:  # Show balances > $0.01
                                        data["balances"].append({
                                            "token": token,
                                            "units": units,
                                            "value": value
                                        })
                                        data["total_value"] += value

                # Sort by value descending
                data["balances"].sort(key=lambda x: x["value"], reverse=True)

            elif name == "lp" and result:
                positions = result.get("data", [])
                data["lp_positions"] = positions[:5]  # Show top 5

    except Exception as e:
        logger.error(f"Error fetching gateway data: {e}", exc_info=True)

    return data


async def show_dex_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display main DEX trading menu with balances and positions"""

    # Start building message
    header = r"🔄 *DEX Trading*" + "\n\n"

    # Try to fetch gateway data
    try:
        client = await get_gateway_client()
        gateway_data = await _fetch_gateway_data(client)

        # Show gateway balances if available
        if gateway_data["balances"]:
            header += r"💰 *Gateway Balances:*" + "\n"
            for bal in gateway_data["balances"][:5]:  # Top 5 balances
                line = _format_balance_line(bal["token"], bal["units"], bal["value"])
                header += escape_markdown_v2(line) + "\n"

            if gateway_data["total_value"] > 0:
                total_str = f"${gateway_data['total_value']:,.2f}"
                header += f"  _Total: {escape_markdown_v2(total_str)}_\n"
            header += "\n"

        # Show active LP positions if available
        if gateway_data["lp_positions"]:
            header += r"📍 *Active LP Positions:*" + "\n"
            for pos in gateway_data["lp_positions"]:
                line = _format_position_line(pos)
                header += escape_markdown_v2(line) + "\n"
            header += "\n"

    except Exception as e:
        logger.warning(f"Could not fetch gateway data for menu: {e}")
        # Show last swap info as fallback
        last_swap = get_dex_last_swap(context.user_data)
        if last_swap and "connector" in last_swap:
            header += r"⚡ *Last Swap:*" + "\n"
            header += f"• Connector: {escape_markdown_v2(last_swap['connector'])}\n"
            if "trading_pair" in last_swap:
                header += f"• Pair: {escape_markdown_v2(last_swap['trading_pair'])}\n"
            header += "\n"

    header += "Select operation:"

    keyboard = [
        [
            InlineKeyboardButton("💰 Quote", callback_data="dex:swap_quote"),
            InlineKeyboardButton("✅ Swap", callback_data="dex:swap_execute"),
            InlineKeyboardButton("🔍 History", callback_data="dex:swap_search")
        ],
        [
            InlineKeyboardButton("📋 List Pools", callback_data="dex:pool_list"),
            InlineKeyboardButton("🔍 Pool Info", callback_data="dex:pool_info"),
            InlineKeyboardButton("📍 Positions", callback_data="dex:manage_positions")
        ],
        [
            InlineKeyboardButton("✖️ Close", callback_data="dex:close")
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
        except Exception as e:
            # Ignore "message is not modified" errors
            if "not modified" not in str(e).lower():
                logger.warning(f"Failed to edit menu message: {e}")
    else:
        await update.message.reply_text(
            header,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle close button - delete the menu message"""
    query = update.callback_query
    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"Failed to delete message: {e}")
        await query.answer("Menu closed")
