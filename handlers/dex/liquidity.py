"""
DEX Unified Liquidity Pools functionality

Provides:
- Combined liquidity pools menu with balances and positions
- Active positions display with quick actions
- Position history (closed positions)
- Explore pools sub-menu (Gecko, Pool Info, Meteora)
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message, resolve_token_symbol, format_amount, KNOWN_TOKENS
from servers import get_client
from ._shared import (
    get_cached,
    set_cached,
    cached_call,
    invalidate_cache,
    get_explorer_url,
    format_relative_time,
    get_history_filters,
    set_history_filters,
    HistoryFilters,
    build_filter_buttons,
    build_pagination_buttons,
    build_filter_selection_keyboard,
    HISTORY_FILTERS,
)

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


def _format_value(value: float) -> str:
    """Format USD values"""
    if value >= 1000000:
        return f"${value/1000000:.2f}M"
    elif value >= 1000:
        return f"${value/1000:.2f}K"
    else:
        return f"${value:.2f}"


def _format_token_amount(value) -> str:
    """Format token amounts with appropriate precision"""
    if value is None or value == 0:
        return "0"
    try:
        num = float(value)
        if abs(num) < 0.0001:
            return f"{num:.2e}"
        elif abs(num) < 1:
            return f"{num:.6f}".rstrip('0').rstrip('.')
        elif abs(num) < 1000:
            return f"{num:.4f}".rstrip('0').rstrip('.')
        else:
            return f"{num:,.2f}"
    except (ValueError, TypeError):
        return str(value)


async def _fetch_gateway_balances(client) -> dict:
    """Fetch gateway/DEX balances (blockchain wallets)"""
    from collections import defaultdict

    GATEWAY_KEYWORDS = ["solana", "ethereum", "polygon", "arbitrum", "base", "avalanche", "optimism"]

    data = {
        "balances_by_network": defaultdict(list),
        "total_value": 0,
    }

    try:
        if not hasattr(client, 'portfolio'):
            return data

        result = await client.portfolio.get_state()
        if not result:
            return data

        for account_name, account_data in result.items():
            for connector_name, balances in account_data.items():
                connector_lower = connector_name.lower()

                is_gateway = any(keyword in connector_lower for keyword in GATEWAY_KEYWORDS)
                if not is_gateway:
                    continue

                if balances:
                    network = connector_lower
                    for balance in balances:
                        token = balance.get("token", "???")
                        units = balance.get("units", 0)
                        value = balance.get("value", 0)
                        if value > 0.01:
                            data["balances_by_network"][network].append({
                                "token": token,
                                "units": units,
                                "value": value
                            })
                            data["total_value"] += value

        # Sort by value
        for network in data["balances_by_network"]:
            for balance in data["balances_by_network"][network]:
                balance["percentage"] = (balance["value"] / data["total_value"] * 100) if data["total_value"] > 0 else 0
            data["balances_by_network"][network].sort(key=lambda x: x["value"], reverse=True)

    except Exception as e:
        logger.error(f"Error fetching balances: {e}", exc_info=True)

    return data


async def _fetch_lp_positions(client, status: str = "OPEN") -> dict:
    """Fetch LP positions by status"""
    data = {
        "positions": [],
        "token_cache": dict(KNOWN_TOKENS)
    }

    try:
        if not hasattr(client, 'gateway_clmm'):
            return data

        result = await client.gateway_clmm.search_positions(
            limit=100,
            offset=0,
            status=status
        )

        if not result:
            return data

        positions = result.get("data", [])

        # For OPEN status, filter to only show active positions with liquidity
        if status == "OPEN":
            def has_liquidity(pos):
                liq = pos.get('liquidity') or pos.get('current_liquidity')
                if liq is not None:
                    try:
                        return float(liq) > 0
                    except (ValueError, TypeError):
                        pass
                base = pos.get('base_token_amount') or pos.get('amount_base')
                quote = pos.get('quote_token_amount') or pos.get('amount_quote')
                if base is not None or quote is not None:
                    try:
                        return float(base or 0) > 0 or float(quote or 0) > 0
                    except (ValueError, TypeError):
                        pass
                return True

            positions = [p for p in positions if has_liquidity(p)]

        data["positions"] = positions

        # Fetch tokens for symbol resolution
        networks = list(set(pos.get('network', 'solana-mainnet-beta') for pos in positions))
        if networks and hasattr(client, 'gateway'):
            for network in networks:
                try:
                    tokens = []
                    if hasattr(client.gateway, 'get_network_tokens'):
                        resp = await client.gateway.get_network_tokens(network)
                        tokens = resp.get('tokens', []) if resp else []
                    elif hasattr(client.gateway, 'get_network_config'):
                        resp = await client.gateway.get_network_config(network)
                        tokens = resp.get('tokens', []) if resp else []
                    for token in tokens:
                        addr = token.get('address', '')
                        symbol = token.get('symbol', '')
                        if addr and symbol:
                            data["token_cache"][addr] = symbol
                except Exception as e:
                    logger.debug(f"Failed to fetch tokens for {network}: {e}")

    except Exception as e:
        logger.error(f"Error fetching LP positions: {e}", exc_info=True)

    return data


def _format_compact_position_line(pos: dict, token_cache: dict = None, index: int = None) -> str:
    """Format a single position as a compact line for display

    Returns: "1. SOL-USDC (meteora) 🟢 [0.89-1.47] | 10.5 SOL / 123 USDC"
    """
    token_cache = token_cache or {}

    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')[:3]  # Abbreviate

    # Get price range
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))

    # Get in-range status
    in_range = pos.get('in_range', '')
    status_emoji = "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else "⚪"

    # Format range
    range_str = ""
    if lower and upper:
        try:
            lower_f = float(lower)
            upper_f = float(upper)
            if lower_f >= 1:
                range_str = f"[{lower_f:.2f}-{upper_f:.2f}]"
            else:
                range_str = f"[{lower_f:.4f}-{upper_f:.4f}]"
        except (ValueError, TypeError):
            range_str = f"[{lower}-{upper}]"

    # Get current amounts
    base_amount = pos.get('base_token_amount', pos.get('amount_a', pos.get('token_a_amount', 0)))
    quote_amount = pos.get('quote_token_amount', pos.get('amount_b', pos.get('token_b_amount', 0)))

    # Build line
    prefix = f"{index}. " if index is not None else "• "
    line = f"{prefix}{pair} ({connector}) {status_emoji} {range_str}"

    # Add amounts if available
    try:
        base_amt = float(base_amount) if base_amount else 0
        quote_amt = float(quote_amount) if quote_amount else 0
        if base_amt > 0 or quote_amt > 0:
            line += f"\n   💰 {_format_token_amount(base_amt)} {base_symbol} / {_format_token_amount(quote_amt)} {quote_symbol}"
    except (ValueError, TypeError):
        pass

    # Add pending fees if any
    base_fee = pos.get('base_fee_pending', pos.get('unclaimed_fee_a', 0))
    quote_fee = pos.get('quote_fee_pending', pos.get('unclaimed_fee_b', 0))
    try:
        base_fee_f = float(base_fee) if base_fee else 0
        quote_fee_f = float(quote_fee) if quote_fee else 0
        if base_fee_f > 0 or quote_fee_f > 0:
            line += f"\n   🎁 Fees: {_format_token_amount(base_fee_f)} {base_symbol} / {_format_token_amount(quote_fee_f)} {quote_symbol}"
    except (ValueError, TypeError):
        pass

    return line


def _format_closed_position_line(pos: dict, token_cache: dict = None) -> str:
    """Format a closed position as a compact line

    Shows:
    - Pair & connector
    - Price direction: 📈 price went up (ended with more quote), 📉 price went down (ended with more base)
    - Fees earned (actual profit)
    - Age

    Returns: "ORE-SOL (met) 📈 Fees: 0.013 ORE  3d"
    """
    token_cache = token_cache or {}

    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')[:3]

    # Determine price direction based on position changes
    # If you end with more quote than you started with, price went UP (you sold base for quote)
    # If you end with more base than you started with, price went DOWN (you bought base with quote)
    pnl_summary = pos.get('pnl_summary', {})
    base_pnl = pnl_summary.get('base_pnl', 0) or 0
    quote_pnl = pnl_summary.get('quote_pnl', 0) or 0

    try:
        base_pnl_f = float(base_pnl)
        quote_pnl_f = float(quote_pnl)

        # If quote increased significantly, price went up
        # If base increased significantly, price went down
        if abs(quote_pnl_f) > 0.001 or abs(base_pnl_f) > 0.001:
            if quote_pnl_f > base_pnl_f:
                direction_emoji = "📈"  # Price went up, you have more quote
            else:
                direction_emoji = "📉"  # Price went down, you have more base
        else:
            direction_emoji = "➡️"  # Price stayed in range
    except (ValueError, TypeError):
        direction_emoji = ""

    # Fees collected (actual profit!)
    base_fee = pos.get('base_fee_collected', 0) or 0
    quote_fee = pos.get('quote_fee_collected', 0) or 0
    fees_str = ""

    try:
        base_fee_f = float(base_fee)
        quote_fee_f = float(quote_fee)

        fee_parts = []
        if base_fee_f > 0.0001:
            fee_parts.append(f"{_format_token_amount(base_fee_f)} {base_symbol}")
        if quote_fee_f > 0.0001:
            fee_parts.append(f"{_format_token_amount(quote_fee_f)} {quote_symbol}")

        if fee_parts:
            fees_str = f"💰 {' + '.join(fee_parts)}"
        else:
            fees_str = "💰 0"
    except (ValueError, TypeError):
        pass

    # Get close timestamp
    closed_at = pos.get('closed_at', pos.get('updated_at', ''))
    age = format_relative_time(closed_at) if closed_at else ""

    # Build line: "ORE-SOL (met) 📈 💰 0.013 ORE  3d"
    parts = [f"{pair} ({connector})"]
    if direction_emoji:
        parts.append(direction_emoji)
    if fees_str:
        parts.append(fees_str)
    if age:
        parts.append(f" {age}")

    return " ".join(parts)


# ============================================
# MENU DISPLAY
# ============================================

async def handle_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle liquidity pools - unified menu"""
    context.user_data["dex_state"] = "liquidity"
    await show_liquidity_menu(update, context)


async def show_liquidity_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, send_new: bool = False) -> None:
    """Display the unified liquidity pools menu with balances and positions

    Shows:
    - Wallet balances (compact)
    - Active LP positions with quick actions
    - Recent closed positions (history)
    - Explore pools button
    """
    help_text = r"💧 *Liquidity Pools*" + "\n\n"

    try:
        client = await get_client()

        # Fetch balances (cached)
        gateway_data = await cached_call(
            context.user_data,
            "gateway_balances",
            _fetch_gateway_balances,
            120,
            client
        )

        # Show compact balances
        if gateway_data.get("balances_by_network"):
            help_text += r"━━━ Wallet ━━━" + "\n"

            # Show Solana balances primarily (for LP)
            for network, balances in gateway_data["balances_by_network"].items():
                if "solana" in network.lower():
                    for bal in balances[:5]:  # Top 5 tokens
                        token = bal["token"]
                        units = _format_token_amount(bal["units"])
                        value = _format_value(bal["value"])
                        help_text += f"💰 `{escape_markdown_v2(token)}`: `{escape_markdown_v2(units)}` {escape_markdown_v2(value)}\n"
                    if len(balances) > 5:
                        help_text += f"   _\\.\\.\\. and {len(balances) - 5} more_\n"
                    break

            if gateway_data["total_value"] > 0:
                help_text += f"💵 Total: `{escape_markdown_v2(_format_value(gateway_data['total_value']))}`\n"

            help_text += "\n"

        # Fetch active positions (cached)
        lp_data = await cached_call(
            context.user_data,
            "gateway_lp_positions",
            _fetch_lp_positions,
            60,
            client,
            "OPEN"
        )

        positions = lp_data.get("positions", [])
        token_cache = lp_data.get("token_cache", {})
        context.user_data["token_cache"] = token_cache

        # Show active positions
        if positions:
            help_text += rf"━━━ Active Positions \({len(positions)}\) ━━━" + "\n"
            for i, pos in enumerate(positions[:5], 1):  # Show max 5
                line = _format_compact_position_line(pos, token_cache, index=i)
                help_text += escape_markdown_v2(line) + "\n"

            if len(positions) > 5:
                help_text += escape_markdown_v2(f"   ... and {len(positions) - 5} more") + "\n"

            help_text += "\n"
        else:
            help_text += r"📍 _No active positions_" + "\n\n"

        # Store positions for action buttons
        context.user_data["lp_positions_cache"] = positions

        # Fetch closed positions (history) - cached separately
        closed_data = await cached_call(
            context.user_data,
            "gateway_closed_positions",
            _fetch_lp_positions,
            120,
            client,
            "CLOSED"
        )

        closed_positions = closed_data.get("positions", [])

        # Sort by closed_at date (most recent first)
        def get_closed_time(pos):
            closed_at = pos.get('closed_at', pos.get('updated_at', ''))
            if closed_at:
                try:
                    from datetime import datetime
                    # Parse ISO format
                    if '+' in closed_at:
                        closed_at = closed_at.split('+')[0]
                    return datetime.fromisoformat(closed_at.replace('Z', ''))
                except (ValueError, TypeError):
                    pass
            return None

        closed_positions = sorted(
            closed_positions,
            key=lambda p: get_closed_time(p) or "",
            reverse=True
        )[:5]  # Most recent 5

        if closed_positions:
            help_text += r"━━━ Closed Positions \(fees earned\) ━━━" + "\n"
            for pos in closed_positions:
                line = _format_closed_position_line(pos, token_cache)
                help_text += escape_markdown_v2(line) + "\n"
            help_text += "\n"

    except Exception as e:
        logger.warning(f"Could not fetch data: {e}")
        help_text += r"⚠️ _Could not load data_" + "\n\n"

    # Build keyboard
    keyboard = []

    # Position action buttons (if positions exist)
    positions = context.user_data.get("lp_positions_cache", [])
    if positions:
        # Row of position numbers (1-5)
        pos_buttons = []
        for i in range(min(5, len(positions))):
            pos_buttons.append(
                InlineKeyboardButton(f"#{i+1}", callback_data=f"dex:lp_pos_view:{i}")
            )
        if pos_buttons:
            keyboard.append(pos_buttons)

        # Quick actions row
        keyboard.append([
            InlineKeyboardButton("💰 Collect All Fees", callback_data="dex:lp_collect_all"),
            InlineKeyboardButton("📊 View All", callback_data="dex:manage_positions"),
        ])

    # Utility buttons - Explore, History, Refresh
    keyboard.append([
        InlineKeyboardButton("🔍 Explore", callback_data="dex:explore_pools"),
        InlineKeyboardButton("📜 History", callback_data="dex:lp_history"),
        InlineKeyboardButton("🔄 Refresh", callback_data="dex:lp_refresh"),
    ])

    keyboard.append([
        InlineKeyboardButton("« Back", callback_data="dex:main_menu")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if send_new or not update.callback_query:
        if update.message:
            await update.message.reply_text(
                help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
        elif update.callback_query:
            await update.callback_query.message.reply_text(
                help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
    else:
        try:
            await update.callback_query.message.edit_text(
                help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.warning(f"Failed to edit liquidity menu: {e}")


async def handle_lp_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle refresh button - clear cache and reload"""
    query = update.callback_query
    await query.answer("Refreshing...")

    # Invalidate position and balance caches
    invalidate_cache(context.user_data, "balances", "positions")

    await show_liquidity_menu(update, context)


async def handle_lp_pos_view(update: Update, context: ContextTypes.DEFAULT_TYPE, pos_index: int) -> None:
    """Handle position view button - shows detailed position info"""
    from .pools import handle_pos_view

    # Convert to string index as expected by handle_pos_view
    await handle_pos_view(update, context, str(pos_index))


async def handle_lp_collect_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle collect all fees button - collects fees from all positions"""
    query = update.callback_query

    positions = context.user_data.get("lp_positions_cache", [])

    if not positions:
        await query.answer("No active positions", show_alert=True)
        return

    # Check which positions have fees to collect
    positions_with_fees = []
    for i, pos in enumerate(positions):
        base_fee = pos.get('base_fee_pending', pos.get('unclaimed_fee_a', 0))
        quote_fee = pos.get('quote_fee_pending', pos.get('unclaimed_fee_b', 0))
        try:
            if float(base_fee or 0) > 0 or float(quote_fee or 0) > 0:
                positions_with_fees.append((i, pos))
        except (ValueError, TypeError):
            pass

    if not positions_with_fees:
        await query.answer("No fees to collect", show_alert=True)
        return

    # Show confirmation
    await query.answer(f"Collecting fees from {len(positions_with_fees)} positions...")

    # TODO: Implement batch fee collection
    # For now, redirect to manage positions
    from .pools import handle_manage_positions
    await handle_manage_positions(update, context)


def _format_detailed_position_line(pos: dict, token_cache: dict = None) -> str:
    """Format a position with detailed info for history view

    Shows:
    - Pair (connector) with status
    - Price range
    - Initial -> Final amounts with PnL
    - Fees earned
    - Duration

    Returns formatted line (not escaped)
    """
    token_cache = token_cache or {}

    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')[:3]
    status = pos.get('status', 'CLOSED')

    # Price range
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))
    range_str = ""
    if lower and upper:
        try:
            lower_f = float(lower)
            upper_f = float(upper)
            if lower_f >= 1:
                range_str = f"[{lower_f:.2f} - {upper_f:.2f}]"
            else:
                range_str = f"[{lower_f:.4f} - {upper_f:.4f}]"
        except (ValueError, TypeError):
            pass

    # Get PnL summary
    pnl = pos.get('pnl_summary', {})
    initial_base = pnl.get('initial_base', pos.get('initial_base_token_amount', 0)) or 0
    initial_quote = pnl.get('initial_quote', pos.get('initial_quote_token_amount', 0)) or 0
    final_base = pnl.get('current_base_total', pos.get('base_token_amount', 0)) or 0
    final_quote = pnl.get('current_quote_total', pos.get('quote_token_amount', 0)) or 0
    base_pnl = pnl.get('base_pnl', 0) or 0
    quote_pnl = pnl.get('quote_pnl', 0) or 0

    # Fees collected
    base_fee = pos.get('base_fee_collected', 0) or 0
    quote_fee = pos.get('quote_fee_collected', 0) or 0

    # Duration
    opened_at = pos.get('opened_at', pos.get('created_at', ''))
    closed_at = pos.get('closed_at', pos.get('updated_at', ''))
    duration_str = ""
    if opened_at and closed_at:
        try:
            from datetime import datetime
            open_dt = datetime.fromisoformat(opened_at.replace('Z', '+00:00').split('+')[0])
            close_dt = datetime.fromisoformat(closed_at.replace('Z', '+00:00').split('+')[0])
            duration = close_dt - open_dt
            days = duration.days
            hours = duration.seconds // 3600
            if days > 0:
                duration_str = f"{days}d {hours}h"
            else:
                duration_str = f"{hours}h"
        except (ValueError, TypeError):
            pass

    # Age since close
    age = format_relative_time(closed_at) if closed_at else ""

    # Build multi-line output
    lines = []

    # Header: pair (connector) [range] - closed Xd ago
    header = f"📊 {pair} ({connector})"
    if range_str:
        header += f" {range_str}"
    lines.append(header)

    # Initial amounts
    try:
        init_base_f = float(initial_base)
        init_quote_f = float(initial_quote)
        if init_base_f > 0 or init_quote_f > 0:
            lines.append(f"   📥 Initial: {_format_token_amount(init_base_f)} {base_symbol} + {_format_token_amount(init_quote_f)} {quote_symbol}")
    except (ValueError, TypeError):
        pass

    # Final amounts with PnL indicators
    try:
        final_base_f = float(final_base)
        final_quote_f = float(final_quote)
        base_pnl_f = float(base_pnl)
        quote_pnl_f = float(quote_pnl)

        # Format PnL with +/- signs
        base_pnl_str = f"+{_format_token_amount(base_pnl_f)}" if base_pnl_f >= 0 else f"{_format_token_amount(base_pnl_f)}"
        quote_pnl_str = f"+{_format_token_amount(quote_pnl_f)}" if quote_pnl_f >= 0 else f"{_format_token_amount(quote_pnl_f)}"

        lines.append(f"   📤 Final: {_format_token_amount(final_base_f)} {base_symbol} ({base_pnl_str}) + {_format_token_amount(final_quote_f)} {quote_symbol} ({quote_pnl_str})")
    except (ValueError, TypeError):
        pass

    # Fees earned
    try:
        base_fee_f = float(base_fee)
        quote_fee_f = float(quote_fee)
        fee_parts = []
        if base_fee_f > 0.0001:
            fee_parts.append(f"{_format_token_amount(base_fee_f)} {base_symbol}")
        if quote_fee_f > 0.0001:
            fee_parts.append(f"{_format_token_amount(quote_fee_f)} {quote_symbol}")
        if fee_parts:
            lines.append(f"   🎁 Fees earned: {' + '.join(fee_parts)}")
        else:
            lines.append(f"   🎁 Fees earned: 0")
    except (ValueError, TypeError):
        pass

    # Duration and age
    meta = []
    if duration_str:
        meta.append(f"⏱️ {duration_str}")
    if age:
        meta.append(f"Closed {age} ago")
    if meta:
        lines.append(f"   {' | '.join(meta)}")

    return "\n".join(lines)


async def handle_lp_history(update: Update, context: ContextTypes.DEFAULT_TYPE, reset_filters: bool = False) -> None:
    """Show position history with filters and pagination"""
    from datetime import datetime

    try:
        # Get or initialize filters
        if reset_filters:
            filters = HistoryFilters(history_type="position")
        else:
            filters = get_history_filters(context.user_data, "position")

        client = await get_client()

        if not hasattr(client, 'gateway_clmm'):
            error_message = format_error_message("Gateway CLMM not available")
            await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        # Build search params from filters
        search_params = {
            "limit": filters.limit,
            "offset": filters.offset,
            "status": filters.status or "CLOSED",  # Default to CLOSED for history
        }
        if filters.trading_pair:
            search_params["trading_pair"] = filters.trading_pair
        if filters.connector:
            search_params["connector"] = filters.connector

        result = await client.gateway_clmm.search_positions(**search_params)

        positions = result.get("data", []) if result else []
        pagination = result.get("pagination", {}) if result else {}
        total_count = pagination.get("total_count", len(positions))

        # Update filters with total count
        filters.total_count = total_count
        set_history_filters(context.user_data, filters)

        if not positions and filters.offset == 0:
            message = r"📜 *Position History*" + "\n\n" + r"_No positions found with current filters\._"

            # Build keyboard with filters
            keyboard = build_filter_buttons(filters, "dex:lp_hist")
            keyboard.append([InlineKeyboardButton("🔄 Clear Filters", callback_data="dex:lp_hist_clear")])
            keyboard.append([InlineKeyboardButton("« Back", callback_data="dex:lp_refresh")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.callback_query.message.edit_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            return

        # Sort by closed_at date (most recent first)
        def get_closed_time(pos):
            closed_at = pos.get('closed_at', pos.get('updated_at', ''))
            if closed_at:
                try:
                    if '+' in closed_at:
                        closed_at = closed_at.split('+')[0]
                    return datetime.fromisoformat(closed_at.replace('Z', ''))
                except (ValueError, TypeError):
                    pass
            return None

        positions = sorted(
            positions,
            key=lambda p: get_closed_time(p) or "",
            reverse=True
        )

        token_cache = context.user_data.get("token_cache", {})

        # Build header with filter summary
        filter_parts = []
        if filters.trading_pair:
            filter_parts.append(filters.trading_pair)
        if filters.connector:
            filter_parts.append(filters.connector)
        if filters.status:
            filter_parts.append(filters.status)

        if filter_parts:
            filter_summary = escape_markdown_v2(f" [{', '.join(filter_parts)}]")
        else:
            filter_summary = ""

        message = rf"📜 *Position History*{filter_summary}" + "\n"
        message += rf"_Showing {len(positions)} of {total_count}_" + "\n\n"

        for pos in positions:
            line = _format_detailed_position_line(pos, token_cache)
            message += escape_markdown_v2(line) + "\n\n"

        # Build keyboard
        keyboard = build_filter_buttons(filters, "dex:lp_hist")

        # Pagination row
        if total_count > filters.limit:
            keyboard.append(build_pagination_buttons(filters, "dex:lp_hist"))

        # Action buttons - use lp_refresh to ensure fresh data when going back
        keyboard.append([
            InlineKeyboardButton("🔄 Clear Filters", callback_data="dex:lp_hist_clear"),
            InlineKeyboardButton("« Back", callback_data="dex:lp_refresh")
        ])

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
# LP HISTORY FILTER HANDLERS
# ============================================

async def handle_lp_hist_filter_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show trading pair filter options - dynamically from history data"""
    filters = get_history_filters(context.user_data, "position")

    # Get unique pairs from cached history data or use defaults
    cached_positions = context.user_data.get("_cache", {}).get("lp_history_data", ([], 0))[0]
    if cached_positions:
        pairs = set()
        token_cache = context.user_data.get("token_cache", {})
        for pos in cached_positions:
            base = resolve_token_symbol(pos.get('base_token', ''), token_cache)
            quote = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
            if base and quote:
                pairs.add(f"{base}-{quote}")
        options = ["All"] + sorted(list(pairs))
    else:
        options = HISTORY_FILTERS["position"]["trading_pair"]

    message = r"💱 *Filter by Trading Pair*"
    reply_markup = build_filter_selection_keyboard(
        options,
        filters.trading_pair,
        "dex:lp_hist_set_pair",
        "dex:lp_history"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_lp_hist_filter_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show connector filter options - dynamically from history data"""
    filters = get_history_filters(context.user_data, "position")

    # Get unique connectors from cached data or use defaults
    cached_positions = context.user_data.get("_cache", {}).get("lp_history_data", ([], 0))[0]
    if cached_positions:
        connectors = set(pos.get('connector', '') for pos in cached_positions if pos.get('connector'))
        options = ["All"] + sorted(list(connectors))
    else:
        options = HISTORY_FILTERS["position"]["connector"]

    message = r"🔌 *Filter by DEX/Connector*"
    reply_markup = build_filter_selection_keyboard(
        options,
        filters.connector,
        "dex:lp_hist_set_connector",
        "dex:lp_history"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_lp_hist_filter_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show status filter options"""
    filters = get_history_filters(context.user_data, "position")
    options = HISTORY_FILTERS["position"]["status"]

    message = r"📊 *Filter by Status*"
    reply_markup = build_filter_selection_keyboard(
        options,
        filters.status,
        "dex:lp_hist_set_status",
        "dex:lp_history"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_lp_hist_set_filter(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    filter_type: str,
    value: str
) -> None:
    """Set a filter value and refresh history"""
    filters = get_history_filters(context.user_data, "position")

    # Convert empty string to None (for "All" option)
    actual_value = value if value else None

    if filter_type == "pair":
        filters.trading_pair = actual_value
    elif filter_type == "connector":
        filters.connector = actual_value
    elif filter_type == "status":
        filters.status = actual_value

    # Reset pagination when filter changes
    filters.reset_pagination()
    set_history_filters(context.user_data, filters)

    # Refresh history view
    await handle_lp_history(update, context)


async def handle_lp_hist_page(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: str) -> None:
    """Handle pagination for LP history"""
    filters = get_history_filters(context.user_data, "position")

    if direction == "next" and filters.has_next:
        filters.offset += filters.limit
    elif direction == "prev" and filters.has_prev:
        filters.offset -= filters.limit

    set_history_filters(context.user_data, filters)
    await handle_lp_history(update, context)


async def handle_lp_hist_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all filters and refresh"""
    await handle_lp_history(update, context, reset_filters=True)


# ============================================
# EXPLORE POOLS SUB-MENU
# ============================================

async def handle_explore_pools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show explore pools sub-menu with all pool discovery options"""
    help_text = (
        r"🔍 *Explore Pools*" + "\n\n"
        r"Discover and analyze liquidity pools:" + "\n\n"
        r"🦎 *Gecko* \- Trending, top, new pools" + "\n"
        r"🔍 *Pool Info* \- Look up pool by address" + "\n"
        r"📋 *Meteora* \- Search Meteora DLMM pools" + "\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("🦎 Gecko", callback_data="dex:gecko_explore"),
            InlineKeyboardButton("🔍 Pool Info", callback_data="dex:pool_info"),
            InlineKeyboardButton("📋 Meteora", callback_data="dex:pool_list"),
        ],
        [
            InlineKeyboardButton("« Back", callback_data="dex:liquidity")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )
