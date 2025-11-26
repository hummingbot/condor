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
from ._shared import get_gateway_client, cached_call

logger = logging.getLogger(__name__)


def _format_amount(value: float) -> str:
    """Format token amounts with appropriate precision"""
    if value == 0:
        return "0"
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
        amounts = f"{_format_amount(float(amount_a))} {token_a} / {_format_amount(float(amount_b))} {token_b}"
    else:
        amounts = "N/A"

    # Format range
    if lower and upper:
        range_str = f"[{_format_price(lower)}-{_format_price(upper)}]"
    else:
        range_str = ""

    return f"  • {pair} ({connector}): {amounts} {range_str}"


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
    """Fetch gateway balances and LP positions, organized by network"""
    import asyncio
    from collections import defaultdict

    data = {
        "balances_by_network": defaultdict(list),  # network -> list of balances
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
                # Extract gateway balances from portfolio state, organized by network
                logger.info(f"Processing portfolio state with {len(result)} accounts")
                for account_name, account_data in result.items():
                    logger.info(f"Account: {account_name}, connectors: {list(account_data.keys())}")
                    for connector_name, balances in account_data.items():
                        # Only show gateway connectors (look for gateway prefix or known chains)
                        is_gateway = (
                            "gateway" in connector_name.lower() or
                            connector_name.lower().startswith("solana") or
                            connector_name.lower().startswith("ethereum") or
                            connector_name.lower() in ["polygon", "arbitrum", "optimism", "base", "avalanche"]
                        )

                        if is_gateway and balances:
                            # Extract network name from connector
                            # e.g., "gateway_solana" -> "solana", "solana_gateway" -> "solana"
                            network = connector_name.lower().replace("gateway_", "").replace("_gateway", "").replace("gateway", "")
                            if not network:
                                network = connector_name.lower()

                            logger.info(f"Found gateway connector: {connector_name} -> network: {network}, balances: {len(balances)}")

                            for balance in balances:
                                token = balance.get("token", "???")
                                units = balance.get("units", 0)
                                value = balance.get("value", 0)
                                if value > 0.01:  # Show balances > $0.01
                                    data["balances_by_network"][network].append({
                                        "token": token,
                                        "units": units,
                                        "value": value
                                    })
                                    data["total_value"] += value

                # Calculate percentages and sort within each network
                for network in data["balances_by_network"]:
                    for balance in data["balances_by_network"][network]:
                        balance["percentage"] = (balance["value"] / data["total_value"] * 100) if data["total_value"] > 0 else 0
                    # Sort by value descending
                    data["balances_by_network"][network].sort(key=lambda x: x["value"], reverse=True)

            elif name == "lp" and result:
                positions = result.get("data", [])
                data["lp_positions"] = positions[:5]  # Show top 5

    except Exception as e:
        logger.error(f"Error fetching gateway data: {e}", exc_info=True)

    return data


def _build_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the DEX menu keyboard"""
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
    return InlineKeyboardMarkup(keyboard)


def _build_loading_message() -> str:
    """Build the initial loading message"""
    return (
        r"🔄 *DEX Trading*" + "\n\n"
        r"💰 *Gateway Balances:*" + "\n"
        r"⏳ _Loading\.\.\._" + "\n\n"
        "Select operation:"
    )


def _build_menu_with_data(gateway_data: dict, last_swap: dict = None) -> str:
    """Build the menu message with gateway data"""
    header = r"🔄 *DEX Trading*" + "\n\n"

    # Show gateway balances organized by network if available
    if gateway_data.get("balances_by_network") and len(gateway_data["balances_by_network"]) > 0:
        header += r"💰 *Gateway Balances:*" + "\n\n"

        # Display each network with its balances in a table
        for network, balances in gateway_data["balances_by_network"].items():
            # Calculate network total
            network_total = sum(bal["value"] for bal in balances)
            network_total_str = _format_value(network_total)

            header += f"  🌐 *{escape_markdown_v2(network.upper())}* \\- `{escape_markdown_v2(network_total_str)}`\n\n"

            # Create table for this network's balances
            table = "```\n"
            table += f"{'Token':<10} {'Amount':<12} {'Value':<10} {'%':>6}\n"
            table += f"{'─'*10} {'─'*12} {'─'*10} {'─'*6}\n"

            # Show top 5 tokens per network
            for bal in balances[:5]:
                token = bal["token"][:9]  # Truncate if needed
                units_str = _format_amount(bal["units"])[:11]
                value_str = _format_value(bal["value"]).replace('$', '')[:9]
                pct = bal["percentage"]

                table += f"{token:<10} {units_str:<12} {value_str:<10} {pct:>5.1f}%\n"

            table += "```\n"
            header += table

        # Show total portfolio value
        if gateway_data["total_value"] > 0:
            total_str = _format_value(gateway_data["total_value"])
            header += f"💵 *Total:* `{escape_markdown_v2(total_str)}`\n\n"

    # Show active LP positions if available
    if gateway_data.get("lp_positions"):
        header += r"📍 *Active LP Positions:*" + "\n"
        for pos in gateway_data["lp_positions"]:
            line = _format_position_line(pos)
            header += escape_markdown_v2(line) + "\n"
        header += "\n"

    # Show last swap info as fallback if no balances
    if not gateway_data.get("balances_by_network") and last_swap and "connector" in last_swap:
        header += r"⚡ *Last Swap:*" + "\n"
        header += f"• Connector: {escape_markdown_v2(last_swap['connector'])}\n"
        if "trading_pair" in last_swap:
            header += f"• Pair: {escape_markdown_v2(last_swap['trading_pair'])}\n"
        header += "\n"

    header += "Select operation:"
    return header


async def show_dex_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display main DEX trading menu with balances and positions

    Uses progressive loading: shows menu immediately with loading indicator,
    then updates with actual balances when data is fetched.
    """
    reply_markup = _build_menu_keyboard()

    # Step 1: Show menu immediately with loading indicator
    loading_message = _build_loading_message()

    if update.callback_query:
        query_message = update.callback_query.message

        # Check if the current message is a photo (can't edit_text on photos)
        if query_message.photo:
            # Delete the photo message and send a new text message
            try:
                await query_message.delete()
            except Exception:
                pass
            message = await query_message.chat.send_message(
                loading_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            # Regular text message - edit it
            try:
                await query_message.edit_text(
                    loading_message,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
                message = query_message
            except Exception as e:
                if "not modified" not in str(e).lower():
                    logger.warning(f"Failed to edit menu message: {e}")
                message = query_message
    else:
        message = await update.message.reply_text(
            loading_message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    # Step 2: Fetch gateway data (with 60s cache)
    gateway_data = {"balances_by_network": {}, "lp_positions": [], "total_value": 0}
    last_swap = get_dex_last_swap(context.user_data)

    try:
        client = await get_gateway_client()
        gateway_data = await cached_call(
            context.user_data,
            "gateway_data",
            _fetch_gateway_data,
            60,  # Cache for 60 seconds
            client
        )
        logger.debug(f"Gateway data: {len(gateway_data.get('balances_by_network', {}))} networks, total_value={gateway_data.get('total_value', 0)}")
    except Exception as e:
        logger.warning(f"Could not fetch gateway data for menu: {e}")

    # Step 3: Update message with actual data
    final_message = _build_menu_with_data(gateway_data, last_swap)

    try:
        await message.edit_text(
            final_message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        # Ignore "message is not modified" errors
        if "not modified" not in str(e).lower():
            logger.warning(f"Failed to update menu with data: {e}")


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle close button - delete the menu message"""
    query = update.callback_query
    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"Failed to delete message: {e}")
        await query.answer("Menu closed")
