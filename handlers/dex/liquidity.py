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
from utils.auth import gateway_required
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
        "token_prices": {},  # token symbol -> USD price
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
                        price = balance.get("price", 0)
                        if value > 0.01:
                            data["balances_by_network"][network].append({
                                "token": token,
                                "units": units,
                                "value": value,
                                "price": price
                            })
                            data["total_value"] += value
                            # Store token price for PnL conversion
                            if token and price:
                                data["token_prices"][token] = price

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
            status=status,
            refresh=True
        )

        if not result:
            return data

        positions = result.get("data", [])

        # For OPEN status, filter to only show active positions with liquidity
        if status == "OPEN":
            def is_active_with_liquidity(pos):
                # Must not be closed
                if pos.get('status') == 'CLOSED':
                    return False
                # Check liquidity
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

            positions = [p for p in positions if is_active_with_liquidity(p)]

        # For CLOSED status, only include positions that are actually closed
        if status == "CLOSED":
            positions = [p for p in positions if p.get('status') == 'CLOSED' or p.get('closed_at')]

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


def _format_compact_position_line(pos: dict, token_cache: dict = None, index: int = None, token_prices: dict = None) -> str:
    """Format a single position as a compact line for display

    Returns: "1. SOL-USDC (meteora) 🟢 [0.89-1.47] | PnL: -$25 | Value: $63"

    Args:
        token_prices: dict mapping token symbol -> USD price (e.g. {"SOL": 138.82})
    """
    token_cache = token_cache or {}
    token_prices = token_prices or {}

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

    # Format range with enough decimals to show the full price
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))
    current = pos.get('current_price', '')

    range_str = ""
    price_indicator = ""
    if lower and upper:
        try:
            lower_f = float(lower)
            upper_f = float(upper)

            # Determine decimal places needed based on magnitude
            if lower_f >= 1:
                decimals = 2
            elif lower_f >= 0.001:
                decimals = 6
            else:
                decimals = 8

            range_str = f"[{lower_f:.{decimals}f}-{upper_f:.{decimals}f}]"

            # Add price position indicator if we have current price
            if current:
                current_f = float(current)
                if current_f < lower_f:
                    # Price below range - show how far below
                    price_indicator = "▼"  # Below range
                elif current_f > upper_f:
                    # Price above range
                    price_indicator = "▲"  # Above range
                else:
                    # In range - show position with bar
                    pct = (current_f - lower_f) / (upper_f - lower_f)
                    bar_len = 5
                    filled = int(pct * bar_len)
                    price_indicator = f"[{'█' * filled}{'░' * (bar_len - filled)}]"
        except (ValueError, TypeError):
            range_str = f"[{lower}-{upper}]"

    # Get current amounts
    base_amount = pos.get('base_token_amount', pos.get('amount_a', pos.get('token_a_amount', 0)))
    quote_amount = pos.get('quote_token_amount', pos.get('amount_b', pos.get('token_b_amount', 0)))

    # Get position value from pnl_summary
    pnl_summary = pos.get('pnl_summary', {})
    position_value_quote = pnl_summary.get('current_total_value_quote')

    # Get values from pnl_summary (all values are in quote token units)
    total_pnl_quote = pnl_summary.get('total_pnl_quote', 0)
    current_lp_value_quote = pnl_summary.get('current_lp_value_quote', 0)

    # Get PENDING fees (fees available to collect) and COLLECTED fees
    base_fee_pending = pos.get('base_fee_pending', 0) or 0
    quote_fee_pending = pos.get('quote_fee_pending', 0) or 0
    base_fee_collected = pos.get('base_fee_collected', 0) or 0
    quote_fee_collected = pos.get('quote_fee_collected', 0) or 0

    # Build line with price indicator next to range
    prefix = f"{index}. " if index is not None else "• "
    range_with_indicator = f"{range_str} {price_indicator}" if price_indicator else range_str
    line = f"{prefix}{pair} ({connector}) {status_emoji} {range_with_indicator}"

    # Add PnL + value + pending fees, converted to USD
    try:
        pnl_f = float(total_pnl_quote) if total_pnl_quote else 0
        lp_value_f = float(current_lp_value_quote) if current_lp_value_quote else 0

        # Get token prices for USD conversion (try exact match, then variants)
        def get_price(symbol, default=0):
            if symbol in token_prices:
                return token_prices[symbol]
            # Try case-insensitive match
            symbol_lower = symbol.lower()
            for key, price in token_prices.items():
                if key.lower() == symbol_lower:
                    return price
            # Try common variants (WSOL <-> SOL, WETH <-> ETH, etc.)
            variants = {
                "sol": ["wsol", "wrapped sol"],
                "wsol": ["sol"],
                "eth": ["weth", "wrapped eth"],
                "weth": ["eth"],
            }
            for variant in variants.get(symbol_lower, []):
                for key, price in token_prices.items():
                    if key.lower() == variant:
                        return price
            return default

        quote_price = get_price(quote_symbol, 1.0)
        base_price = get_price(base_symbol, 0)

        # Convert PnL and value from quote token to USD
        pnl_usd = pnl_f * quote_price
        value_usd = lp_value_f * quote_price

        # Calculate pending fees in USD (fees available to collect)
        base_pending_f = float(base_fee_pending) if base_fee_pending else 0
        quote_pending_f = float(quote_fee_pending) if quote_fee_pending else 0
        pending_fees_usd = (base_pending_f * base_price) + (quote_pending_f * quote_price)

        # Calculate collected fees in USD (fees already claimed)
        base_collected_f = float(base_fee_collected) if base_fee_collected else 0
        quote_collected_f = float(quote_fee_collected) if quote_fee_collected else 0
        collected_fees_usd = (base_collected_f * base_price) + (quote_collected_f * quote_price)

        # Debug logging
        logger.info(f"Position {index}: {base_symbol}@${base_price:.4f}, {quote_symbol}@${quote_price:.2f} | pending=${pending_fees_usd:.2f}, collected=${collected_fees_usd:.2f}")

        if value_usd > 0 or pnl_f != 0:
            # Format: PnL: -$25.12 | Value: $63.45 | 🎁 $3.70 | 💰 $1.20
            parts = []
            if pnl_usd >= 0:
                parts.append(f"PnL: +${pnl_usd:.2f}")
            else:
                parts.append(f"PnL: -${abs(pnl_usd):.2f}")
            parts.append(f"Value: ${value_usd:.2f}")
            if pending_fees_usd > 0.01:
                parts.append(f"🎁 ${pending_fees_usd:.2f}")
            if collected_fees_usd > 0.01:
                parts.append(f"💰 ${collected_fees_usd:.2f}")
            line += "\n   " + " | ".join(parts)
    except (ValueError, TypeError):
        pass

    return line


def _format_closed_position_line(pos: dict, token_cache: dict = None, token_prices: dict = None) -> str:
    """Format a closed position with same format as active positions

    Shows: Pair (connector) ✓ [range] | PnL: +$2.88 | 💰 $1.40 | 1d
    """
    token_cache = token_cache or {}
    token_prices = token_prices or {}

    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')[:3]

    # Get price range
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))
    range_str = ""
    if lower and upper:
        try:
            lower_f = float(lower)
            upper_f = float(upper)
            if lower_f >= 1:
                decimals = 2
            elif lower_f >= 0.001:
                decimals = 6
            else:
                decimals = 8
            range_str = f"[{lower_f:.{decimals}f}-{upper_f:.{decimals}f}]"
        except (ValueError, TypeError):
            pass

    # Get PnL data - use pre-calculated total_pnl_quote
    pnl_summary = pos.get('pnl_summary', {})
    total_pnl_quote = pnl_summary.get('total_pnl_quote', 0) or 0
    total_fees_value = pnl_summary.get('total_fees_value_quote', 0) or 0

    try:
        pnl_f = float(total_pnl_quote)
        fees_f = float(total_fees_value)
    except (ValueError, TypeError):
        pnl_f = 0
        fees_f = 0

    # Get quote token price for USD conversion
    quote_price = token_prices.get(quote_symbol, 1.0)

    # Convert to USD
    pnl_usd = pnl_f * quote_price
    fees_usd = fees_f * quote_price

    # Get close timestamp
    closed_at = pos.get('closed_at', pos.get('updated_at', ''))
    age = format_relative_time(closed_at) if closed_at else ""

    # Build line: "MET-USDC (met) ✓ [0.31-0.32]"
    line = f"{pair} ({connector}) ✓ {range_str}"

    # Add PnL and fees on second line in USD
    parts = []
    if pnl_usd >= 0:
        parts.append(f"PnL: +${pnl_usd:.2f}")
    else:
        parts.append(f"PnL: -${abs(pnl_usd):.2f}")
    if fees_usd > 0.01:
        parts.append(f"💰 ${fees_usd:.2f}")
    if age:
        parts.append(age)
    line += "\n   " + " | ".join(parts)

    return line


# ============================================
# MENU DISPLAY
# ============================================

@gateway_required
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
    chat_id = update.effective_chat.id
    help_text = r"💧 *Liquidity Pools*" + "\n\n"

    try:
        client = await get_client(chat_id)

        # Fetch balances (cached)
        gateway_data = await cached_call(
            context.user_data,
            "gateway_balances",
            _fetch_gateway_balances,
            120,
            client
        )

        # Show compact balances - vertical format with columns
        if gateway_data.get("balances_by_network"):
            # Show Solana balances primarily (for LP)
            for network, balances in gateway_data["balances_by_network"].items():
                if "solana" in network.lower():
                    # Filter tokens with value >= $0.5
                    tokens = [(bal["token"], _format_value(bal["value"])) for bal in balances if bal["value"] >= 0.5]

                    if tokens:
                        # Determine columns based on count: 1-5 = 1col, 6-10 = 2col, 11+ = 3col
                        num_tokens = len(tokens)
                        if num_tokens <= 5:
                            cols = 1
                        elif num_tokens <= 10:
                            cols = 2
                        else:
                            cols = 3

                        # Calculate rows needed
                        rows = (num_tokens + cols - 1) // cols

                        # Build grid
                        lines = []
                        for row in range(rows):
                            row_parts = []
                            for col in range(cols):
                                idx = row + col * rows
                                if idx < num_tokens:
                                    token, value = tokens[idx]
                                    row_parts.append(f"{token} {value}")
                            lines.append(" · ".join(row_parts))

                        help_text += r"💰 *Wallet*" + "\n"
                        for line in lines:
                            help_text += escape_markdown_v2(line) + "\n"

                        if gateway_data["total_value"] > 0:
                            help_text += rf"*Total: {escape_markdown_v2(_format_value(gateway_data['total_value']))}*" + "\n"
                        help_text += "\n"
                    break

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
        token_prices = gateway_data.get("token_prices", {})
        context.user_data["token_cache"] = token_cache
        context.user_data["token_prices"] = token_prices

        # Show active positions
        if positions:
            help_text += rf"━━━ Active Positions \({len(positions)}\) ━━━" + "\n"
            for i, pos in enumerate(positions[:5], 1):  # Show max 5
                line = _format_compact_position_line(pos, token_cache, index=i, token_prices=token_prices)
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

        # Merge token cache from closed positions (in case no open positions exist)
        closed_token_cache = closed_data.get("token_cache", {})
        token_cache = {**token_cache, **closed_token_cache}
        context.user_data["token_cache"] = token_cache

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
            help_text += r"━━━ Closed Positions ━━━" + "\n"
            for pos in closed_positions:
                line = _format_closed_position_line(pos, token_cache, token_prices)
                help_text += escape_markdown_v2(line) + "\n"
            help_text += "\n"

        # Add explore pools section
        help_text += r"━━━ 🔍 Explore Pools ━━━" + "\n"
        help_text += r"🦎 Gecko \- Trending, top, new pools" + "\n"
        help_text += r"🔍 Pool Info \- Look up pool by address" + "\n"
        help_text += r"📋 Meteora \- Search Meteora DLMM pools" + "\n"

    except Exception as e:
        logger.warning(f"Could not fetch data: {e}")
        help_text += r"⚠️ _Could not load data_" + "\n\n"

    # Build keyboard
    keyboard = []

    # Position action buttons (if positions exist)
    positions = context.user_data.get("lp_positions_cache", [])
    token_cache = context.user_data.get("token_cache", {})
    if positions:
        # Initialize positions_cache for action handlers
        if "positions_cache" not in context.user_data:
            context.user_data["positions_cache"] = {}

        # Each position gets its own row: [Pair | 💰 Fees | ❌ Close]
        for i, pos in enumerate(positions[:5]):
            # Store position for action handlers
            context.user_data["positions_cache"][str(i)] = pos

            # Get pair name for button label
            base_token = pos.get('base_token', pos.get('token_a', ''))
            quote_token = pos.get('quote_token', pos.get('token_b', ''))
            base_sym = resolve_token_symbol(base_token, token_cache)[:5] if base_token else '?'
            quote_sym = resolve_token_symbol(quote_token, token_cache)[:5] if quote_token else '?'
            pair_label = f"{i+1}. {base_sym}-{quote_sym}"

            keyboard.append([
                InlineKeyboardButton(pair_label, callback_data=f"dex:lp_pos_view:{i}"),
                InlineKeyboardButton("🎁", callback_data=f"dex:pos_collect:{i}"),
                InlineKeyboardButton("❌", callback_data=f"dex:pos_close:{i}"),
            ])

        # Quick actions row (only if more than shown)
        if len(positions) > 5:
            keyboard.append([
                InlineKeyboardButton("🎁 Collect All", callback_data="dex:lp_collect_all"),
                InlineKeyboardButton("📊 View All", callback_data="dex:manage_positions"),
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("🎁 Collect All Fees", callback_data="dex:lp_collect_all"),
            ])

    # Explore pools row - direct access to pool discovery
    keyboard.append([
        InlineKeyboardButton("🦎 Gecko", callback_data="dex:gecko_explore"),
        InlineKeyboardButton("🔍 Pool Info", callback_data="dex:pool_info"),
        InlineKeyboardButton("📋 Meteora", callback_data="dex:pool_list"),
    ])

    # Utility buttons - History, Refresh
    keyboard.append([
        InlineKeyboardButton("📜 History", callback_data="dex:lp_history"),
        InlineKeyboardButton("🔄 Refresh", callback_data="dex:lp_refresh"),
    ])

    keyboard.append([
        InlineKeyboardButton("✕ Close", callback_data="dex:close")
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
        msg = update.callback_query.message
        try:
            # If message is a photo, delete it and send new text message
            if msg.photo:
                try:
                    await msg.delete()
                except Exception:
                    pass
                await msg.chat.send_message(
                    help_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
            else:
                await msg.edit_text(
                    help_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.warning(f"Failed to edit liquidity menu: {e}")
                # Fallback: send new message
                try:
                    await msg.reply_text(
                        help_text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup,
                        disable_web_page_preview=True
                    )
                except Exception:
                    pass


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

    # Get position from lp_positions_cache (list) and copy to positions_cache (dict)
    positions = context.user_data.get("lp_positions_cache", [])
    if pos_index < len(positions):
        pos = positions[pos_index]
        # Copy to positions_cache dict for handle_pos_view
        if "positions_cache" not in context.user_data:
            context.user_data["positions_cache"] = {}
        context.user_data["positions_cache"][str(pos_index)] = pos

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

    # Fees earned (collected)
    try:
        base_fee_f = float(base_fee)
        quote_fee_f = float(quote_fee)
        fee_parts = []
        if base_fee_f > 0.0001:
            fee_parts.append(f"{_format_token_amount(base_fee_f)} {base_symbol}")
        if quote_fee_f > 0.0001:
            fee_parts.append(f"{_format_token_amount(quote_fee_f)} {quote_symbol}")
        if fee_parts:
            lines.append(f"   💰 Fees earned: {' + '.join(fee_parts)}")
        else:
            lines.append(f"   💰 Fees earned: 0")
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

    chat_id = update.effective_chat.id

    try:
        # Get or initialize filters
        if reset_filters:
            filters = HistoryFilters(history_type="position")
        else:
            filters = get_history_filters(context.user_data, "position")

        client = await get_client(chat_id)

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


