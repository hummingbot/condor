"""
GeckoTerminal Pool Explorer

Provides comprehensive pool exploration using GeckoTerminal API:
- Trending pools (all networks / by network)
- Top pools by volume
- New pools discovery
- Pool details with OHLCV charts
- Token search
- Recent trades
"""

import asyncio
import io
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from geckoterminal_py import GeckoTerminalAsyncClient
from utils.telegram_formatters import escape_markdown_v2
from ._shared import cached_call, set_cached, get_cached, clear_cache
from .visualizations import generate_ohlcv_chart, generate_liquidity_chart, generate_combined_chart
from .pool_data import can_fetch_liquidity, get_connector_for_dex, fetch_liquidity_bins

logger = logging.getLogger(__name__)

# Cache TTLs
TRENDING_CACHE_TTL = 120  # 2 minutes for trending data
POOL_CACHE_TTL = 60  # 1 minute for pool details
OHLCV_CACHE_TTL = 300  # 5 minutes for OHLCV data

# Network display names
NETWORK_NAMES = {
    "solana": "Solana",
    "eth": "Ethereum",
    "arbitrum": "Arbitrum",
    "base": "Base",
    "bsc": "BNB Chain",
    "polygon_pos": "Polygon",
    "avalanche": "Avalanche",
    "optimism": "Optimism",
    "sui-network": "Sui",
}

# Popular networks for quick access
POPULAR_NETWORKS = ["solana", "eth", "base", "arbitrum", "bsc"]


# ============================================
# UTILITY FUNCTIONS
# ============================================

def _format_price(price: float) -> str:
    """Format price with appropriate precision"""
    if price is None:
        return "N/A"
    if price == 0:
        return "0"
    if abs(price) >= 1000:
        return f"${price:,.2f}"
    elif abs(price) >= 1:
        return f"${price:.4f}"
    elif abs(price) >= 0.0001:
        return f"${price:.6f}"
    else:
        return f"${price:.10f}"


def _format_volume(volume: float) -> str:
    """Format volume with K/M/B suffixes"""
    if volume is None:
        return "N/A"
    if volume >= 1_000_000_000:
        return f"${volume/1_000_000_000:.2f}B"
    elif volume >= 1_000_000:
        return f"${volume/1_000_000:.2f}M"
    elif volume >= 1_000:
        return f"${volume/1_000:.2f}K"
    else:
        return f"${volume:.2f}"


def _format_change(change: float) -> str:
    """Format price change with emoji"""
    if change is None:
        return "N/A"
    emoji = "🟢" if change >= 0 else "🔴"
    return f"{emoji} {change:+.2f}%"


def _get_nested_value(data: dict, *keys, default=None):
    """Get a value from dict trying multiple key patterns.

    Handles both nested dicts and flattened DataFrame columns.
    Example keys: ("volume_usd", "h24") will try:
    - data["volume_usd"]["h24"]
    - data["volume_usd.h24"]
    - data["volume_usd_h24"]
    """
    # Try nested access
    try:
        result = data
        for key in keys:
            if isinstance(result, dict):
                result = result.get(key)
            else:
                result = None
                break
        if result is not None:
            return result
    except (KeyError, TypeError):
        pass

    # Try dot-notation flattened key
    dot_key = ".".join(str(k) for k in keys)
    if dot_key in data:
        return data[dot_key]

    # Try underscore-notation flattened key
    underscore_key = "_".join(str(k) for k in keys)
    if underscore_key in data:
        return data[underscore_key]

    return default


def _parse_symbols_from_name(name: str) -> tuple:
    """Parse base and quote symbols from pool name like 'PENGU / SOL'"""
    if not name or "/" not in name:
        return "?", "?"
    parts = name.split("/")
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return "?", "?"


def _get_pool_symbols(pool: dict) -> tuple:
    """Get base and quote symbols from pool data.

    Tries direct columns first, falls back to parsing from name.
    Returns (base_symbol, quote_symbol)
    """
    attrs = pool.get("attributes", pool) if isinstance(pool, dict) else {}

    base = _get_nested_value(attrs, "base_token_symbol")
    quote = _get_nested_value(attrs, "quote_token_symbol")

    # Fallback: parse from name
    if not base or not quote:
        name = _get_nested_value(attrs, "name") or ""
        parsed_base, parsed_quote = _parse_symbols_from_name(name)
        base = base or parsed_base
        quote = quote or parsed_quote

    return base, quote


def _format_pool_line(pool: dict, index: int = None) -> str:
    """Format a single pool line for list display"""
    # Handle both nested API response and flattened DataFrame
    attrs = pool.get("attributes", pool) if isinstance(pool, dict) else pool

    name = attrs.get("name", "Unknown") if isinstance(attrs, dict) else "Unknown"

    # Try multiple key patterns for symbols
    base_symbol = (
        _get_nested_value(attrs, "base_token_symbol") or
        _get_nested_value(attrs, "base_token", "symbol")
    )
    quote_symbol = (
        _get_nested_value(attrs, "quote_token_symbol") or
        _get_nested_value(attrs, "quote_token", "symbol")
    )

    # Fallback: parse from name (e.g., "PENGU / SOL")
    if not base_symbol or not quote_symbol:
        parsed_base, parsed_quote = _parse_symbols_from_name(name)
        base_symbol = base_symbol or parsed_base
        quote_symbol = quote_symbol or parsed_quote

    # Get price - try multiple patterns
    price = _get_nested_value(attrs, "base_token_price_usd")
    if price:
        try:
            price = float(price)
        except (ValueError, TypeError):
            price = None

    # Get volume - try nested and flattened patterns
    volume_24h = (
        _get_nested_value(attrs, "volume_usd", "h24") or
        _get_nested_value(attrs, "volume_usd_h24") or
        0
    )
    if volume_24h:
        try:
            volume_24h = float(volume_24h)
        except (ValueError, TypeError):
            volume_24h = 0

    # Get price change - try nested and flattened patterns
    change_24h = (
        _get_nested_value(attrs, "price_change_percentage", "h24") or
        _get_nested_value(attrs, "price_change_percentage_h24")
    )
    if change_24h:
        try:
            change_24h = float(change_24h)
        except (ValueError, TypeError):
            change_24h = None

    # Build the line
    prefix = f"{index}. " if index is not None else "• "
    pair = f"{base_symbol}/{quote_symbol}"

    parts = [f"{prefix}{pair}"]
    if price:
        parts.append(_format_price(price))
    if change_24h is not None:
        parts.append(_format_change(change_24h))
    if volume_24h:
        parts.append(f"Vol: {_format_volume(volume_24h)}")

    return " | ".join(parts)


def _format_compact(value) -> str:
    """Format number compactly for mobile display"""
    if value is None:
        return "—"
    try:
        num = float(value)
        if num == 0:
            return "0"
        if abs(num) >= 1_000_000:
            return f"{num/1_000_000:.1f}M"
        if abs(num) >= 1_000:
            return f"{num/1_000:.0f}K"
        if abs(num) >= 1:
            return f"{num:.0f}"
        return f"{num:.2f}"
    except (ValueError, TypeError):
        return "—"


def _get_dex_short_name(dex_id: str) -> str:
    """Convert dex_id to short display name with pool type indicator"""
    if not dex_id:
        return "?"

    dex_id = dex_id.lower()

    # CLMM/Concentrated liquidity pools
    if "clmm" in dex_id or "dlmm" in dex_id or "whirlpool" in dex_id:
        if "raydium" in dex_id:
            return "Ray-CL"
        if "meteora" in dex_id:
            return "Met-CL"
        if "orca" in dex_id:
            return "Orc-CL"
        return "CLMM"

    # AMM pools
    if "raydium" in dex_id:
        return "Ray"
    if "meteora" in dex_id:
        return "Met"
    if "orca" in dex_id:
        return "Orca"
    if "pump" in dex_id:
        return "Pump"
    if "jupiter" in dex_id:
        return "Jup"
    if "moonshot" in dex_id:
        return "Moon"

    # Truncate unknown DEX names
    return dex_id[:6].title()


def _format_gecko_pool_table(pools: list) -> str:
    """Format pools as a compact table optimized for mobile

    Shows: #, Pair, DEX, Chg%, V/TVL, TVL
    Similar to CLMM pools format.
    """
    if not pools:
        return "_No pools found_"

    lines = []

    # Header - balanced for mobile (~44 chars)
    lines.append("```")
    lines.append(f"{'#':>2} {'Pair':<10} {'DEX':<6} {'Chg%':>5} {'V/T':>5} {'TVL':>6}")
    lines.append("─" * 44)

    for i, pool in enumerate(pools, 1):
        attrs = pool.get("attributes", pool) if isinstance(pool, dict) else pool

        # Get symbols
        base_symbol = _get_nested_value(attrs, "base_token_symbol") or "?"
        quote_symbol = _get_nested_value(attrs, "quote_token_symbol") or "?"
        if not base_symbol or base_symbol == "?":
            name = attrs.get("name", "") if isinstance(attrs, dict) else ""
            parsed_base, parsed_quote = _parse_symbols_from_name(name)
            base_symbol = parsed_base or base_symbol
            quote_symbol = parsed_quote or quote_symbol

        pair = f"{base_symbol[:6]}/{quote_symbol[:3]}"[:10]

        # DEX/Pool type
        dex_id = _get_nested_value(attrs, "dex_id") or _get_nested_value(attrs, "dex", "id") or ""
        dex_str = _get_dex_short_name(dex_id)

        # Price change 24h
        change = _get_nested_value(attrs, "price_change_percentage", "h24") or _get_nested_value(attrs, "price_change_percentage_h24")
        if change is not None:
            try:
                change_val = float(change)
                change_str = f"{change_val:+.0f}" if abs(change_val) >= 10 else f"{change_val:+.1f}"
            except (ValueError, TypeError):
                change_str = "—"
        else:
            change_str = "—"

        # Volume 24h and TVL for V/TVL ratio
        vol = _get_nested_value(attrs, "volume_usd", "h24") or _get_nested_value(attrs, "volume_usd_h24") or 0
        tvl = _get_nested_value(attrs, "reserve_in_usd") or _get_nested_value(attrs, "reserve_usd") or 0

        try:
            vol_val = float(vol)
            tvl_val = float(tvl)
        except (ValueError, TypeError):
            vol_val = 0
            tvl_val = 0

        # V/TVL ratio (volume/tvl) - good proxy for activity/potential returns
        if tvl_val > 0 and vol_val > 0:
            ratio = vol_val / tvl_val
            if ratio >= 10:
                ratio_str = f"{ratio:.0f}x"
            elif ratio >= 1:
                ratio_str = f"{ratio:.1f}x"
            else:
                ratio_str = f".{int(ratio*100):02d}x"
        else:
            ratio_str = "—"

        # TVL
        tvl_str = _format_compact(tvl_val)

        lines.append(f"{i:>2} {pair:<10} {dex_str:<6} {change_str:>5} {ratio_str:>5} {tvl_str:>6}")

    lines.append("```")

    return "\n".join(lines)


def _extract_pools_from_response(result, limit: int = 10) -> list:
    """Extract pools list from various response formats.

    Handles:
    - pandas DataFrame (library default)
    - Dict with 'data' key: {"data": [...]}
    - Direct list: [...]
    - Object with 'data' attribute
    """
    pools = []

    if result is None:
        logger.warning("GeckoTerminal returned None")
        return pools

    # Check for pandas DataFrame first (library returns DataFrames)
    try:
        import pandas as pd
        if isinstance(result, pd.DataFrame):
            # Convert DataFrame rows to list of dicts
            pools = result.to_dict('records')
            # Log ALL columns for debugging
            logger.info(f"GeckoTerminal DataFrame columns ({len(result.columns)}): {list(result.columns)}")
            if pools and len(pools) > 0:
                # Log first pool's data to see available fields
                first_pool = pools[0]
                logger.info(f"GeckoTerminal first pool sample data:")
                for key, value in first_pool.items():
                    # Truncate long values
                    val_str = str(value)[:100] if value is not None else "None"
                    logger.info(f"  {key}: {val_str}")
            return pools[:limit] if pools else []
    except ImportError:
        pass

    # If it's already a list, use directly
    if isinstance(result, list):
        pools = result
    # If it's a dict with 'data' key
    elif isinstance(result, dict):
        pools = result.get("data", [])
    # If it has a 'data' attribute (object-style response)
    elif hasattr(result, 'data'):
        data = result.data
        if isinstance(data, list):
            pools = data
        elif isinstance(data, dict):
            pools = data.get("data", [])
    else:
        logger.warning(f"Unknown GeckoTerminal response type: {type(result)}")
        # Try to_dict if available (DataFrame-like)
        if hasattr(result, 'to_dict'):
            try:
                pools = result.to_dict('records')
            except Exception as e:
                logger.error(f"Failed to convert to dict: {e}")
        else:
            try:
                pools = list(result)
            except (TypeError, ValueError):
                logger.error(f"Cannot extract pools from response: {result}")

    # Log pool structure for dict/list responses
    if pools and len(pools) > 0:
        first_pool = pools[0]
        if isinstance(first_pool, dict):
            # Check if it has 'attributes' (API JSON response format)
            if 'attributes' in first_pool:
                attrs = first_pool.get('attributes', {})
                rels = first_pool.get('relationships', {})
                logger.info(f"GeckoTerminal pool attributes keys: {list(attrs.keys()) if isinstance(attrs, dict) else 'N/A'}")
                logger.info(f"GeckoTerminal pool relationships keys: {list(rels.keys()) if isinstance(rels, dict) else 'N/A'}")
                logger.info(f"GeckoTerminal first pool sample:")
                for key, value in attrs.items():
                    val_str = str(value)[:80] if value is not None else "None"
                    logger.info(f"  attr.{key}: {val_str}")
                # Log DEX info specifically (might indicate pool type)
                dex_data = rels.get('dex', {}).get('data', {})
                logger.info(f"  DEX: {dex_data}")
            else:
                # Flat dict (DataFrame-converted)
                logger.info(f"GeckoTerminal pool keys: {list(first_pool.keys())}")

    logger.debug(f"Extracted {len(pools)} pools from response")
    return pools[:limit] if pools else []


def _parse_token_address_from_id(token_id: str) -> str:
    """Parse token address from GeckoTerminal token ID.

    Token IDs are in format: network_address (e.g., solana_So11111111111111...)
    """
    if not token_id:
        return ""
    # Split on first underscore to handle addresses that might contain underscores
    parts = token_id.split("_", 1)
    return parts[1] if len(parts) > 1 else token_id


def _extract_pool_data(pool: dict) -> dict:
    """Extract relevant data from pool response.

    Handles both nested API response and flattened DataFrame columns.
    """
    if not isinstance(pool, dict):
        logger.warning(f"Pool is not a dict: {type(pool)}")
        return {}

    attrs = pool.get("attributes", pool)
    relationships = pool.get("relationships", {})

    # Get name and parse symbols as fallback
    name = _get_nested_value(attrs, "name") or "Unknown"
    base_symbol = _get_nested_value(attrs, "base_token_symbol")
    quote_symbol = _get_nested_value(attrs, "quote_token_symbol")

    # Fallback: parse from name (e.g., "PENGU / SOL")
    if not base_symbol or not quote_symbol:
        parsed_base, parsed_quote = _parse_symbols_from_name(name)
        base_symbol = base_symbol or parsed_base
        quote_symbol = quote_symbol or parsed_quote

    # Extract token addresses from relationships
    # GeckoTerminal returns token IDs in format: network_address
    base_token_id = _get_nested_value(relationships, "base_token", "data", "id") or ""
    quote_token_id = _get_nested_value(relationships, "quote_token", "data", "id") or ""
    base_token_address = _parse_token_address_from_id(base_token_id)
    quote_token_address = _parse_token_address_from_id(quote_token_id)

    # Fallback: try direct keys for flattened DataFrames
    if not base_token_address:
        base_token_address = (
            _get_nested_value(attrs, "base_token_address") or
            _get_nested_value(attrs, "base_token", "address") or
            ""
        )
    if not quote_token_address:
        quote_token_address = (
            _get_nested_value(attrs, "quote_token_address") or
            _get_nested_value(attrs, "quote_token", "address") or
            ""
        )

    # For flattened DataFrames, try direct keys first
    return {
        "id": pool.get("id", ""),
        "name": name,
        "address": _get_nested_value(attrs, "address") or "",
        "base_token_symbol": base_symbol,
        "quote_token_symbol": quote_symbol,
        "base_token_address": base_token_address,
        "quote_token_address": quote_token_address,
        "base_token_price_usd": _get_nested_value(attrs, "base_token_price_usd"),
        "quote_token_price_usd": _get_nested_value(attrs, "quote_token_price_usd"),
        "base_token_price_native": _get_nested_value(attrs, "base_token_price_native_currency"),
        "fdv_usd": _get_nested_value(attrs, "fdv_usd"),
        "market_cap_usd": _get_nested_value(attrs, "market_cap_usd"),
        "reserve_usd": _get_nested_value(attrs, "reserve_in_usd"),
        "volume_24h": _get_nested_value(attrs, "volume_usd", "h24") or _get_nested_value(attrs, "volume_usd_h24") or 0,
        "volume_6h": _get_nested_value(attrs, "volume_usd", "h6") or _get_nested_value(attrs, "volume_usd_h6") or 0,
        "volume_1h": _get_nested_value(attrs, "volume_usd", "h1") or _get_nested_value(attrs, "volume_usd_h1") or 0,
        "price_change_24h": _get_nested_value(attrs, "price_change_percentage", "h24") or _get_nested_value(attrs, "price_change_percentage_h24"),
        "price_change_6h": _get_nested_value(attrs, "price_change_percentage", "h6") or _get_nested_value(attrs, "price_change_percentage_h6"),
        "price_change_1h": _get_nested_value(attrs, "price_change_percentage", "h1") or _get_nested_value(attrs, "price_change_percentage_h1"),
        "transactions_24h": _get_nested_value(attrs, "transactions", "h24") or _get_nested_value(attrs, "transactions_h24") or {},
        "dex_id": (
            _get_nested_value(relationships, "dex", "data", "id") or
            _get_nested_value(pool, "dex_id") or
            _get_nested_value(pool, "dex") or
            ""
        ),
        "network": (
            _get_nested_value(relationships, "network", "data", "id") or
            _get_nested_value(pool, "network_id") or
            _get_nested_value(pool, "network") or
            ""
        ),
        "pool_created_at": _get_nested_value(attrs, "pool_created_at"),
    }


# ============================================
# STATE MANAGEMENT
# ============================================

DEFAULT_GECKO_NETWORK = "solana"
DEFAULT_GECKO_VIEW = "trending"  # trending, top, new

VIEW_LABELS = {
    "trending": "🔥 Trending",
    "top": "📈 Top Pools",
    "new": "🆕 New Pools",
}

VIEW_CYCLE = ["trending", "top", "new"]


def get_gecko_state(user_data: dict) -> tuple:
    """Get current network and view from user state"""
    network = user_data.get("gecko_selected_network", DEFAULT_GECKO_NETWORK)
    view = user_data.get("gecko_selected_view", DEFAULT_GECKO_VIEW)
    return network, view


def set_gecko_network(user_data: dict, network: str) -> None:
    """Set selected network"""
    user_data["gecko_selected_network"] = network


def set_gecko_view(user_data: dict, view: str) -> None:
    """Set selected view type"""
    user_data["gecko_selected_view"] = view


def cycle_gecko_view(user_data: dict) -> str:
    """Cycle to next view type and return the new view"""
    current_view = user_data.get("gecko_selected_view", DEFAULT_GECKO_VIEW)
    current_idx = VIEW_CYCLE.index(current_view) if current_view in VIEW_CYCLE else 0
    next_idx = (current_idx + 1) % len(VIEW_CYCLE)
    new_view = VIEW_CYCLE[next_idx]
    user_data["gecko_selected_view"] = new_view
    return new_view


# ============================================
# MAIN EXPLORE MENU
# ============================================

async def show_gecko_explore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display GeckoTerminal pools directly with network/view controls"""
    # Directly show pools based on current settings
    await handle_gecko_show_pools(update, context)


async def handle_gecko_toggle_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between trending/top/new views"""
    new_view = cycle_gecko_view(context.user_data)
    await update.callback_query.answer(f"Switched to {VIEW_LABELS.get(new_view, new_view)}")
    await show_gecko_explore_menu(update, context)


async def handle_gecko_select_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show network selection menu"""
    keyboard = []

    # Add popular networks in rows of 3
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(InlineKeyboardButton(display, callback_data=f"dex:gecko_set_network:{network}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Add more networks option and back
    keyboard.append([
        InlineKeyboardButton("🌍 More Networks", callback_data="dex:gecko_networks"),
    ])
    keyboard.append([InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")])

    current_network, _ = get_gecko_state(context.user_data)
    current_display = NETWORK_NAMES.get(current_network, current_network.title())

    message = (
        r"🌐 *Select Network*" + "\n\n"
        f"Current: *{escape_markdown_v2(current_display)}*\n\n"
        "_Choose a network:_"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_gecko_set_network(update: Update, context: ContextTypes.DEFAULT_TYPE, network: str) -> None:
    """Set the selected network and return to explore menu"""
    set_gecko_network(context.user_data, network)
    display = NETWORK_NAMES.get(network, network.title())
    await update.callback_query.answer(f"Network: {display}")
    await show_gecko_explore_menu(update, context)


async def handle_gecko_show_pools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show pools based on current network and view selection"""
    network, view = get_gecko_state(context.user_data)

    if view == "trending":
        await show_trending_pools(update, context, network)
    elif view == "top":
        await show_top_pools(update, context, network)
    elif view == "new":
        await show_new_pools(update, context, network)


def _build_pool_list_keyboard(pools: list, user_data: dict) -> InlineKeyboardMarkup:
    """Build keyboard with pool buttons and network/view controls"""
    network, view = get_gecko_state(user_data)
    network_display = NETWORK_NAMES.get(network, network.title())

    keyboard = []

    # Pool selection buttons (2 per row)
    row = []
    for i, pool in enumerate(pools):
        base, quote = _get_pool_symbols(pool)
        btn = InlineKeyboardButton(f"{i+1}. {base[:6]}/{quote[:4]}", callback_data=f"dex:gecko_pool:{i}")
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Network and View controls
    keyboard.append([
        InlineKeyboardButton(f"🌐 {network_display}", callback_data="dex:gecko_select_network"),
        InlineKeyboardButton(VIEW_LABELS.get(view, "🔥 Trending"), callback_data="dex:gecko_toggle_view"),
    ])

    # Search and Refresh
    keyboard.append([
        InlineKeyboardButton("🔍 Search", callback_data="dex:gecko_search"),
        InlineKeyboardButton("🔄 Refresh", callback_data="dex:gecko_refresh"),
    ])

    # Back button
    keyboard.append([
        InlineKeyboardButton("« LP Menu", callback_data="dex:liquidity"),
    ])

    return InlineKeyboardMarkup(keyboard)


async def handle_gecko_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh current pool list by invalidating cache"""
    network, view = get_gecko_state(context.user_data)

    # Invalidate cache for current view
    cache_key = f"gecko_{view}_{network or 'all'}"
    if cache_key in context.user_data:
        del context.user_data[cache_key]

    await update.callback_query.answer("Refreshing...")
    await handle_gecko_show_pools(update, context)


# ============================================
# TRENDING POOLS
# ============================================

async def handle_gecko_trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show trending pools menu"""
    keyboard = [
        [
            InlineKeyboardButton("🌍 All Networks", callback_data="dex:gecko_trending_all"),
        ],
    ]

    # Add popular network buttons
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(InlineKeyboardButton(display, callback_data=f"dex:gecko_trending_{network}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")])

    message = (
        r"🔥 *Trending Pools*" + "\n\n"
        "Select network to view trending pools:"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_trending_pools(update: Update, context: ContextTypes.DEFAULT_TYPE, network: str = None) -> None:
    """Fetch and display trending pools"""
    query = update.callback_query

    network_name = NETWORK_NAMES.get(network, network.title()) if network else "All Networks"
    chat = query.message.chat

    # Show loading - handle photo messages
    if getattr(query.message, 'photo', None):
        try:
            await query.message.delete()
        except Exception:
            pass
        loading_msg = await chat.send_message(
            f"🔥 *Trending \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
    else:
        await query.message.edit_text(
            f"🔥 *Trending \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
        loading_msg = query.message

    try:
        cache_key = f"gecko_trending_{network or 'all'}"

        async def fetch_trending():
            client = GeckoTerminalAsyncClient()
            if network and network != "all":
                result = await client.get_trending_pools_by_network(network)
            else:
                result = await client.get_trending_pools()
            logger.info(f"GeckoTerminal trending response type: {type(result)}")
            return _extract_pools_from_response(result, 10)

        pools = await cached_call(
            context.user_data,
            cache_key,
            fetch_trending,
            TRENDING_CACHE_TTL
        )

        # Store pools for selection and update state
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "trending"
        set_gecko_view(context.user_data, "trending")
        if network:
            set_gecko_network(context.user_data, network)

        # Build message with table format
        header = f"🔥 *Trending \\- {escape_markdown_v2(network_name)}* \\({len(pools)}\\)\n"
        table = _format_gecko_pool_table(pools)
        footer = "\n_Select pool number:_"

        # Build keyboard with network/view controls
        reply_markup = _build_pool_list_keyboard(pools, context.user_data)

        await loading_msg.edit_text(
            header + table + footer,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error fetching trending pools: {e}", exc_info=True)
        try:
            await loading_msg.edit_text(
                f"❌ Error fetching trending pools: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data="dex:gecko_refresh")],
                    [InlineKeyboardButton("« LP Menu", callback_data="dex:liquidity")]
                ])
            )
        except Exception:
            await chat.send_message(
                f"❌ Error fetching trending pools: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data="dex:gecko_refresh")],
                    [InlineKeyboardButton("« LP Menu", callback_data="dex:liquidity")]
                ])
            )


# ============================================
# TOP POOLS
# ============================================

async def handle_gecko_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show top pools menu"""
    keyboard = []

    # Add popular network buttons
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(InlineKeyboardButton(display, callback_data=f"dex:gecko_top_{network}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")])

    message = (
        r"📈 *Top Pools by Volume*" + "\n\n"
        "Select network to view top pools:"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_top_pools(update: Update, context: ContextTypes.DEFAULT_TYPE, network: str) -> None:
    """Fetch and display top pools by volume"""
    query = update.callback_query

    network_name = NETWORK_NAMES.get(network, network.title())
    chat = query.message.chat

    # Show loading - handle photo messages
    if getattr(query.message, 'photo', None):
        try:
            await query.message.delete()
        except Exception:
            pass
        loading_msg = await chat.send_message(
            f"📈 *Top Pools \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
    else:
        await query.message.edit_text(
            f"📈 *Top Pools \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
        loading_msg = query.message

    try:
        cache_key = f"gecko_top_{network}"

        async def fetch_top():
            client = GeckoTerminalAsyncClient()
            result = await client.get_top_pools_by_network(network)
            return _extract_pools_from_response(result, 10)

        pools = await cached_call(
            context.user_data,
            cache_key,
            fetch_top,
            TRENDING_CACHE_TTL
        )

        # Store pools for selection and update state
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "top"
        set_gecko_view(context.user_data, "top")
        set_gecko_network(context.user_data, network)

        # Build message with table format
        header = f"📈 *Top Pools \\- {escape_markdown_v2(network_name)}* \\({len(pools)}\\)\n"
        table = _format_gecko_pool_table(pools)
        footer = "\n_Select pool number:_"

        # Build keyboard with network/view controls
        reply_markup = _build_pool_list_keyboard(pools, context.user_data)

        await loading_msg.edit_text(
            header + table + footer,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error fetching top pools: {e}", exc_info=True)
        try:
            await loading_msg.edit_text(
                f"❌ Error fetching top pools: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data="dex:gecko_refresh")],
                    [InlineKeyboardButton("« LP Menu", callback_data="dex:liquidity")]
                ])
            )
        except Exception:
            await chat.send_message(
                f"❌ Error fetching top pools: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data="dex:gecko_refresh")],
                    [InlineKeyboardButton("« LP Menu", callback_data="dex:liquidity")]
                ])
            )


# ============================================
# NEW POOLS
# ============================================

async def handle_gecko_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show new pools menu"""
    keyboard = [
        [
            InlineKeyboardButton("🌍 All Networks", callback_data="dex:gecko_new_all"),
        ],
    ]

    # Add popular network buttons
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(InlineKeyboardButton(display, callback_data=f"dex:gecko_new_{network}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")])

    message = (
        r"🆕 *New Pools*" + "\n\n"
        "Select network to view recently created pools:"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_new_pools(update: Update, context: ContextTypes.DEFAULT_TYPE, network: str = None) -> None:
    """Fetch and display new pools"""
    query = update.callback_query
    chat = query.message.chat

    network_name = NETWORK_NAMES.get(network, network.title()) if network else "All Networks"

    # Show loading - handle photo messages
    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        loading_msg = await chat.send_message(
            f"🆕 *New Pools \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
    else:
        await query.message.edit_text(
            f"🆕 *New Pools \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
        loading_msg = query.message

    try:
        cache_key = f"gecko_new_{network or 'all'}"

        async def fetch_new():
            client = GeckoTerminalAsyncClient()
            if network and network != "all":
                result = await client.get_new_pools_by_network(network)
            else:
                result = await client.get_new_pools_all_networks()
            return _extract_pools_from_response(result, 10)

        pools = await cached_call(
            context.user_data,
            cache_key,
            fetch_new,
            TRENDING_CACHE_TTL
        )

        # Store pools for selection and update state
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "new"
        set_gecko_view(context.user_data, "new")
        if network:
            set_gecko_network(context.user_data, network)

        # Build message with table format
        header = f"🆕 *New Pools \\- {escape_markdown_v2(network_name)}* \\({len(pools)}\\)\n"
        table = _format_gecko_pool_table(pools)
        footer = "\n_Select pool number:_"

        # Build keyboard with network/view controls
        reply_markup = _build_pool_list_keyboard(pools, context.user_data)

        await loading_msg.edit_text(
            header + table + footer,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error fetching new pools: {e}", exc_info=True)
        try:
            await loading_msg.edit_text(
                f"❌ Error fetching new pools: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data="dex:gecko_refresh")],
                    [InlineKeyboardButton("« LP Menu", callback_data="dex:liquidity")]
                ])
            )
        except Exception:
            await chat.send_message(
                f"❌ Error fetching new pools: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data="dex:gecko_refresh")],
                    [InlineKeyboardButton("« LP Menu", callback_data="dex:liquidity")]
                ])
            )


# ============================================
# NETWORK SELECTION
# ============================================

async def handle_gecko_networks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all available networks"""
    query = update.callback_query

    # Show loading
    await query.message.edit_text(
        r"🌐 *Networks*" + "\n\n" + r"_Loading\.\.\._",
        parse_mode="MarkdownV2"
    )

    try:
        cache_key = "gecko_networks"

        async def fetch_networks():
            client = GeckoTerminalAsyncClient()
            result = await client.get_networks()
            return _extract_pools_from_response(result, 100)

        networks = await cached_call(
            context.user_data,
            cache_key,
            fetch_networks,
            600  # Cache for 10 minutes
        )

        # Store networks and build keyboard (show first 20 most popular)
        keyboard = []
        row = []
        for network in networks[:20]:
            attrs = network.get("attributes", network)
            network_id = network.get("id", "")
            name = attrs.get("name", network_id)[:12]
            row.append(InlineKeyboardButton(name, callback_data=f"dex:gecko_net_{network_id}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")])

        message = (
            r"🌐 *Select Network*" + "\n\n"
            "Choose a network to explore pools:"
        )

        await query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error fetching networks: {e}", exc_info=True)
        await query.message.edit_text(
            f"❌ Error fetching networks: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")]
            ])
        )


async def show_network_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, network: str) -> None:
    """Show options for a specific network"""
    network_name = NETWORK_NAMES.get(network, network.title())

    keyboard = [
        [
            InlineKeyboardButton("🔥 Trending", callback_data=f"dex:gecko_trending_{network}"),
            InlineKeyboardButton("📈 Top Pools", callback_data=f"dex:gecko_top_{network}"),
        ],
        [
            InlineKeyboardButton("🆕 New Pools", callback_data=f"dex:gecko_new_{network}"),
        ],
        [
            InlineKeyboardButton("« Back", callback_data="dex:gecko_networks"),
        ],
    ]

    message = (
        f"🌐 *{escape_markdown_v2(network_name)}*\n\n"
        "Select what to explore:"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ============================================
# TOKEN SEARCH
# ============================================

async def handle_gecko_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to enter token address for search"""
    context.user_data["dex_state"] = "gecko_search"

    # Get selected network
    network, _ = get_gecko_state(context.user_data)
    network_display = NETWORK_NAMES.get(network, network.title())

    keyboard = [
        [InlineKeyboardButton(f"🌐 {network_display}", callback_data="dex:gecko_search_network")],
        [InlineKeyboardButton("« Cancel", callback_data="dex:gecko_explore")]
    ]

    message = (
        r"🔍 *Token Search*" + "\n\n"
        f"Network: *{escape_markdown_v2(network_display)}*\n\n"
        "Enter a token address to find pools:\n\n"
        "_Tap network button to change_"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_gecko_search_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show network selection for token search"""
    keyboard = []

    # Add popular networks in rows of 3
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(InlineKeyboardButton(display, callback_data=f"dex:gecko_search_set_net:{network}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("« Back", callback_data="dex:gecko_search")])

    current_network, _ = get_gecko_state(context.user_data)
    current_display = NETWORK_NAMES.get(current_network, current_network.title())

    message = (
        r"🌐 *Select Network for Search*" + "\n\n"
        f"Current: *{escape_markdown_v2(current_display)}*\n\n"
        "_Choose a network:_"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_gecko_search_set_network(update: Update, context: ContextTypes.DEFAULT_TYPE, network: str) -> None:
    """Set network for search and return to search prompt"""
    set_gecko_network(context.user_data, network)
    display = NETWORK_NAMES.get(network, network.title())
    await update.callback_query.answer(f"Network: {display}")
    await handle_gecko_search(update, context)


async def process_gecko_search(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process token search input - uses selected network"""
    context.user_data.pop("dex_state", None)

    # Get the selected network
    selected_network, _ = get_gecko_state(context.user_data)
    network_display = NETWORK_NAMES.get(selected_network, selected_network.title())

    # Show loading message
    loading_msg = await update.message.reply_text(
        f"🔍 *Searching on {escape_markdown_v2(network_display)}\\.\\.\\.*",
        parse_mode="MarkdownV2"
    )

    try:
        token_address = user_input.strip()

        # Use the selected network directly
        client = GeckoTerminalAsyncClient()
        result = await client.get_top_pools_by_network_token(selected_network, token_address)
        pools = _extract_pools_from_response(result, 10)

        if not pools:
            await loading_msg.edit_text(
                r"❌ *No pools found*" + "\n\n"
                f"No pools found for this token on {escape_markdown_v2(network_display)}\\.\n\n"
                "_Try a different network or token address_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔍 Try Again", callback_data="dex:gecko_search")],
                    [InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")]
                ])
            )
            return

        # Store pools for selection
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "search"
        context.user_data["gecko_network"] = selected_network
        context.user_data["gecko_search_token"] = token_address

        # Build message
        lines = [f"🔍 *Pools for Token \\- {escape_markdown_v2(network_display)}*\n"]
        # Show truncated or full address
        if len(token_address) > 24:
            lines.append(f"Token: `{escape_markdown_v2(token_address[:12])}...{escape_markdown_v2(token_address[-8:])}`\n")
        else:
            lines.append(f"Token: `{escape_markdown_v2(token_address)}`\n")

        for i, pool in enumerate(pools, 1):
            line = _format_pool_line(pool, i)
            lines.append(escape_markdown_v2(line))

        lines.append("\n_Select a pool for details:_")

        # Build keyboard with pool buttons
        keyboard = []
        row = []
        for i, pool in enumerate(pools):
            base, quote = _get_pool_symbols(pool)
            btn = InlineKeyboardButton(f"{i+1}. {base[:6]}/{quote[:4]}", callback_data=f"dex:gecko_pool:{i}")
            row.append(btn)
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append([
            InlineKeyboardButton("🔍 New Search", callback_data="dex:gecko_search"),
            InlineKeyboardButton("« Back", callback_data="dex:gecko_explore"),
        ])

        await loading_msg.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error searching token: {e}", exc_info=True)
        await loading_msg.edit_text(
            f"❌ Error searching: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")]
            ])
        )


# GeckoTerminal to Gateway network mapping
GECKO_TO_GATEWAY_NETWORK = {
    "solana": "solana-mainnet-beta",
    "eth": "ethereum-mainnet",
    "base": "base-mainnet",
    "arbitrum": "arbitrum-one",
    "bsc": "bsc-mainnet",
    "polygon_pos": "polygon-mainnet",
    "avalanche": "avalanche-mainnet",
    "optimism": "optimism-mainnet",
}

# Default connectors by network chain
NETWORK_DEFAULT_CONNECTOR = {
    "solana": "jupiter",
    "eth": "uniswap",
    "base": "uniswap",
    "arbitrum": "uniswap",
    "bsc": "pancakeswap",
    "polygon_pos": "quickswap",
    "avalanche": "traderjoe",
    "optimism": "uniswap",
}


# ============================================
# POOL DETAIL VIEW
# ============================================

async def show_pool_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, pool_index: int) -> None:
    """Show OHLCV chart automatically when selecting a pool with action buttons"""
    query = update.callback_query

    pools = context.user_data.get("gecko_pools", [])
    if pool_index >= len(pools):
        await query.answer("Pool not found")
        return

    pool = pools[pool_index]
    pool_data = _extract_pool_data(pool)

    # Store current pool for OHLCV/trades
    context.user_data["gecko_selected_pool"] = pool_data
    context.user_data["gecko_selected_pool_index"] = pool_index

    # Default timeframe for initial view
    default_timeframe = "1h"  # 1h candles showing 1 day of data

    # Show OHLCV chart automatically
    await _show_pool_chart(update, context, pool_data, default_timeframe)


async def _show_pool_chart(update: Update, context: ContextTypes.DEFAULT_TYPE, pool_data: dict, timeframe: str) -> None:
    """Internal function to show pool OHLCV chart with action buttons"""
    query = update.callback_query

    await query.answer("Loading chart...")

    chat = query.message.chat

    # Show loading - delete current message and send loading text
    try:
        await query.message.delete()
    except Exception:
        pass  # Message may already be deleted

    loading_msg = await chat.send_message(
        f"📈 *{escape_markdown_v2(pool_data['name'])}*\n\n_Loading chart\\.\\.\\._",
        parse_mode="MarkdownV2"
    )

    try:
        network = pool_data["network"]
        address = pool_data["address"]

        client = GeckoTerminalAsyncClient()
        result = await client.get_ohlcv(network, address, timeframe)

        logger.info(f"OHLCV raw response type: {type(result)}")

        # Handle various response formats for OHLCV
        ohlcv_data = []

        # Check for pandas DataFrame first (library often returns DataFrames)
        try:
            import pandas as pd
            if isinstance(result, pd.DataFrame):
                logger.info(f"OHLCV DataFrame columns: {list(result.columns)}, shape: {result.shape}")
                if not result.empty:
                    if result.index.name == 'datetime' or 'datetime' not in result.columns:
                        result = result.reset_index()
                    ohlcv_data = result.values.tolist()
                    logger.info(f"Converted OHLCV DataFrame with {len(ohlcv_data)} rows")
        except ImportError:
            pass

        # If not a DataFrame, try other formats
        if not ohlcv_data:
            if isinstance(result, dict):
                ohlcv_data = result.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
            elif hasattr(result, 'data'):
                data = result.data
                if isinstance(data, dict):
                    ohlcv_data = data.get("attributes", {}).get("ohlcv_list", [])
                elif hasattr(data, 'attributes'):
                    ohlcv_data = getattr(data.attributes, 'ohlcv_list', [])
            elif isinstance(result, list):
                ohlcv_data = result

        if not ohlcv_data:
            # Fall back to text view if no chart data
            await _show_pool_text_detail(loading_msg, context, pool_data)
            return

        # Generate chart image using visualization module
        pair_name = pool_data.get('name', 'Pool')
        base_symbol = pool_data.get('base_token_symbol')
        quote_symbol = pool_data.get('quote_token_symbol')

        chart_buffer = generate_ohlcv_chart(
            ohlcv_data=ohlcv_data,
            pair_name=pair_name,
            timeframe=_format_timeframe_label(timeframe),
            base_symbol=base_symbol,
            quote_symbol=quote_symbol
        )

        if not chart_buffer:
            await _show_pool_text_detail(loading_msg, context, pool_data)
            return

        # Build caption with detailed info
        pool_index = context.user_data.get("gecko_selected_pool_index", 0)
        dex_id = pool_data.get("dex_id", "")
        network = pool_data.get("network", "")
        network_name = NETWORK_NAMES.get(network, network)

        caption_lines = [f"📈 *{escape_markdown_v2(pool_data['name'])}*"]

        # Price line
        price_parts = []
        if pool_data.get("base_token_price_usd"):
            try:
                price = float(pool_data["base_token_price_usd"])
                price_parts.append(f"💰 {escape_markdown_v2(_format_price(price))}")
            except (ValueError, TypeError):
                pass

        change_24h = pool_data.get("price_change_24h")
        if change_24h is not None:
            try:
                change = float(change_24h)
                emoji = "🟢" if change >= 0 else "🔴"
                price_parts.append(f"{emoji} {escape_markdown_v2(_format_change(change))} 24h")
            except (ValueError, TypeError):
                pass

        if price_parts:
            caption_lines.append(" • ".join(price_parts))

        # Network/DEX line
        caption_lines.append(f"🌐 {escape_markdown_v2(network_name)} • 🏦 {escape_markdown_v2(dex_id)}")

        # Price changes line
        changes = []
        for period, key in [("1h", "price_change_1h"), ("6h", "price_change_6h"), ("24h", "price_change_24h")]:
            change = pool_data.get(key)
            if change is not None:
                try:
                    changes.append(f"{period}: {_format_change(float(change))}")
                except (ValueError, TypeError):
                    pass
        if changes:
            caption_lines.append(f"📊 {escape_markdown_v2(' | '.join(changes))}")

        # Volume line
        vols = []
        for period, key in [("1h", "volume_1h"), ("6h", "volume_6h"), ("24h", "volume_24h")]:
            vol = pool_data.get(key)
            if vol:
                try:
                    vols.append(f"{period}: {_format_volume(float(vol))}")
                except (ValueError, TypeError):
                    pass
        if vols:
            caption_lines.append(f"📈 Vol {escape_markdown_v2(' | '.join(vols))}")

        # Market metrics line
        metrics = []
        if pool_data.get("reserve_usd"):
            try:
                metrics.append(f"Liq: {_format_volume(float(pool_data['reserve_usd']))}")
            except (ValueError, TypeError):
                pass
        if pool_data.get("fdv_usd"):
            try:
                metrics.append(f"FDV: {_format_volume(float(pool_data['fdv_usd']))}")
            except (ValueError, TypeError):
                pass
        if pool_data.get("market_cap_usd"):
            try:
                metrics.append(f"MC: {_format_volume(float(pool_data['market_cap_usd']))}")
            except (ValueError, TypeError):
                pass
        if metrics:
            caption_lines.append(f"💎 {escape_markdown_v2(' | '.join(metrics))}")

        # Transactions line
        txns = pool_data.get("transactions_24h", {})
        if txns:
            buys = txns.get("buys", 0)
            sells = txns.get("sells", 0)
            if buys or sells:
                caption_lines.append(f"🔄 24h Txns: {buys} buys / {sells} sells")

        caption = "\n".join(caption_lines)

        # Build keyboard - reorganized layout
        supports_liquidity = can_fetch_liquidity(dex_id, network)

        keyboard = [
            # Row 1: Timeframe
            [
                InlineKeyboardButton("1h" if timeframe != "1m" else "• 1h •", callback_data="dex:gecko_pool_tf:1m"),
                InlineKeyboardButton("1d" if timeframe != "1h" else "• 1d •", callback_data="dex:gecko_pool_tf:1h"),
                InlineKeyboardButton("7d" if timeframe != "1d" else "• 7d •", callback_data="dex:gecko_pool_tf:1d"),
            ],
            # Row 2: Swap | LP
            [
                InlineKeyboardButton("💱 Swap", callback_data="dex:gecko_swap"),
                InlineKeyboardButton("➕ Add LP", callback_data="dex:gecko_add_liquidity"),
            ],
            # Row 3: Trades | Liquidity Distribution
            [
                InlineKeyboardButton("📜 Trades", callback_data="dex:gecko_trades"),
                InlineKeyboardButton("📊 Liquidity", callback_data="dex:gecko_liquidity"),
            ],
            # Row 4: Add to Gateway
            [
                InlineKeyboardButton("🔗 Add to Gateway", callback_data="dex:gecko_add_tokens"),
            ],
            # Row 5: Refresh | Back
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"dex:gecko_pool:{pool_index}"),
                InlineKeyboardButton("« Back", callback_data="dex:gecko_back_to_list"),
            ],
        ]

        # Delete loading message and send photo
        try:
            await loading_msg.delete()
        except Exception:
            pass  # Message may already be deleted

        await chat.send_photo(
            photo=chart_buffer,
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error generating pool chart: {e}", exc_info=True)
        # Fall back to text view on error
        try:
            await _show_pool_text_detail(loading_msg, context, pool_data)
        except Exception:
            # If loading_msg was deleted, send new error message
            await chat.send_message(
                f"❌ Error loading chart: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data="dex:gecko_back_to_list")]
                ])
            )


async def _show_pool_text_detail(message, context: ContextTypes.DEFAULT_TYPE, pool_data: dict) -> None:
    """Show pool details as text (fallback when chart unavailable)"""
    pool_index = context.user_data.get("gecko_selected_pool_index", 0)
    network_name = NETWORK_NAMES.get(pool_data["network"], pool_data["network"])

    lines = [
        f"📊 *{escape_markdown_v2(pool_data['name'])}*\n",
        f"🌐 Network: {escape_markdown_v2(network_name)}",
        f"🏦 DEX: {escape_markdown_v2(pool_data['dex_id'])}",
        "",
        r"💰 *Price Info:*",
    ]

    if pool_data["base_token_price_usd"]:
        try:
            price = float(pool_data["base_token_price_usd"])
            symbol = escape_markdown_v2(pool_data['base_token_symbol'])
            lines.append(f"• {symbol}: {escape_markdown_v2(_format_price(price))}")
        except (ValueError, TypeError):
            pass

    if pool_data["quote_token_price_usd"]:
        try:
            price = float(pool_data["quote_token_price_usd"])
            symbol = escape_markdown_v2(pool_data['quote_token_symbol'])
            lines.append(f"• {symbol}: {escape_markdown_v2(_format_price(price))}")
        except (ValueError, TypeError):
            pass

    lines.append("")
    lines.append(r"📈 *Price Changes:*")

    for period, key in [("1h", "price_change_1h"), ("6h", "price_change_6h"), ("24h", "price_change_24h")]:
        change = pool_data.get(key)
        if change is not None:
            try:
                change = float(change)
                lines.append(f"• {period}: {escape_markdown_v2(_format_change(change))}")
            except (ValueError, TypeError):
                pass

    lines.append("")
    lines.append(r"📊 *Volume:*")

    for period, key in [("1h", "volume_1h"), ("6h", "volume_6h"), ("24h", "volume_24h")]:
        vol = pool_data.get(key)
        if vol:
            try:
                vol = float(vol)
                lines.append(f"• {period}: {escape_markdown_v2(_format_volume(vol))}")
            except (ValueError, TypeError):
                pass

    # Market cap and FDV
    lines.append("")
    if pool_data.get("market_cap_usd"):
        try:
            mc = float(pool_data["market_cap_usd"])
            lines.append(f"💎 Market Cap: {escape_markdown_v2(_format_volume(mc))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("fdv_usd"):
        try:
            fdv = float(pool_data["fdv_usd"])
            lines.append(f"📈 FDV: {escape_markdown_v2(_format_volume(fdv))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("reserve_usd"):
        try:
            reserve = float(pool_data["reserve_usd"])
            lines.append(f"💧 Liquidity: {escape_markdown_v2(_format_volume(reserve))}")
        except (ValueError, TypeError):
            pass

    # Pool address and link
    lines.append("")
    addr = pool_data.get("address", "")
    network = pool_data.get("network", "")
    if addr:
        lines.append(f"📍 Address:\n`{addr}`")
        if network:
            gecko_url = f"https://www.geckoterminal.com/{network}/pools/{addr}"
            lines.append(f"\n🦎 [View on GeckoTerminal]({escape_markdown_v2(gecko_url)})")

    # Build keyboard
    dex_id = pool_data.get("dex_id", "")
    supports_liquidity = can_fetch_liquidity(dex_id, network)

    keyboard = [
        [
            InlineKeyboardButton("📈 Charts", callback_data="dex:gecko_charts"),
            InlineKeyboardButton("💱 Swap", callback_data="dex:gecko_swap"),
            InlineKeyboardButton("📜 Trades", callback_data="dex:gecko_trades"),
        ],
    ]

    if supports_liquidity:
        keyboard.append([
            InlineKeyboardButton("➕ Add Liquidity", callback_data="dex:gecko_add_liquidity"),
        ])

    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"dex:gecko_pool:{pool_index}"),
        InlineKeyboardButton("« Back", callback_data="dex:gecko_back_to_list"),
    ])

    await message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True
    )


# ============================================
# CHARTS SUB-MENU
# ============================================

async def show_gecko_charts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show charts sub-menu with all chart options (candles, liquidity, combined)"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer()

    dex_id = pool_data.get("dex_id", "")
    network = pool_data.get("network", "")
    pool_name = pool_data.get("name", "Pool")
    supports_liquidity = can_fetch_liquidity(dex_id, network)

    # Build charts menu
    lines = [
        f"📈 *Charts for {escape_markdown_v2(pool_name)}*",
        "",
        "Select a chart type:",
    ]

    keyboard = [
        # Candle timeframes row
        [
            InlineKeyboardButton("📈 1h", callback_data="dex:gecko_ohlcv:1m"),
            InlineKeyboardButton("📈 1d", callback_data="dex:gecko_ohlcv:1h"),
            InlineKeyboardButton("📈 7d", callback_data="dex:gecko_ohlcv:1d"),
        ],
    ]

    # Add liquidity and combined buttons only for supported DEXes
    if supports_liquidity:
        keyboard.append([
            InlineKeyboardButton("📊 Liquidity", callback_data="dex:gecko_liquidity"),
        ])
        keyboard.append([
            InlineKeyboardButton("📊 Combined 1h", callback_data="dex:gecko_combined:1m"),
            InlineKeyboardButton("📊 Combined 1d", callback_data="dex:gecko_combined:1h"),
        ])

    # Back to pool detail
    pool_index = context.user_data.get("gecko_selected_pool_index", 0)
    keyboard.append([
        InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{pool_index}"),
    ])

    # Handle photo messages - can't edit photo to text
    if getattr(query.message, 'photo', None):
        await query.message.delete()
        await query.message.chat.send_message(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ============================================
# OHLCV CHART
# ============================================

async def show_ohlcv_chart(update: Update, context: ContextTypes.DEFAULT_TYPE, timeframe: str) -> None:
    """Fetch OHLCV data and display as chart"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer("Loading chart...")

    # Show loading - handle photo messages (can't edit photo to text)
    if getattr(query.message, 'photo', None):
        await query.message.delete()
        loading_msg = await query.message.chat.send_message(
            f"📈 *OHLCV Chart*\n\n_Loading {timeframe} data\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
    else:
        await query.message.edit_text(
            f"📈 *OHLCV Chart*\n\n_Loading {timeframe} data\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
        loading_msg = query.message

    try:
        network = pool_data["network"]
        address = pool_data["address"]

        client = GeckoTerminalAsyncClient()
        result = await client.get_ohlcv(network, address, timeframe)

        logger.info(f"OHLCV raw response type: {type(result)}")

        # Handle various response formats for OHLCV
        ohlcv_data = []

        # Check for pandas DataFrame first (library often returns DataFrames)
        try:
            import pandas as pd
            if isinstance(result, pd.DataFrame):
                # DataFrame columns: typically datetime, open, high, low, close, volume
                logger.info(f"OHLCV DataFrame columns: {list(result.columns)}, shape: {result.shape}")
                if not result.empty:
                    # Convert to list of lists, handling index if it's the timestamp
                    if result.index.name == 'datetime' or 'datetime' not in result.columns:
                        # Index is the timestamp, reset it to include in data
                        result = result.reset_index()
                    ohlcv_data = result.values.tolist()
                    logger.info(f"Converted OHLCV DataFrame with {len(ohlcv_data)} rows")
                    if ohlcv_data:
                        logger.debug(f"First OHLCV row: {ohlcv_data[0]}")
        except ImportError:
            pass

        # If not a DataFrame, try other formats
        if not ohlcv_data:
            if isinstance(result, dict):
                logger.debug(f"OHLCV dict keys: {result.keys() if result else 'empty'}")
                ohlcv_data = result.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
            elif hasattr(result, 'data'):
                data = result.data
                if isinstance(data, dict):
                    ohlcv_data = data.get("attributes", {}).get("ohlcv_list", [])
                elif hasattr(data, 'attributes'):
                    ohlcv_data = getattr(data.attributes, 'ohlcv_list', [])
            # Try as a list directly
            elif isinstance(result, list):
                ohlcv_data = result
                logger.info(f"OHLCV is a direct list with {len(ohlcv_data)} items")

        logger.info(f"OHLCV final: type={type(result)}, extracted {len(ohlcv_data)} candles")

        if not ohlcv_data:
            await loading_msg.edit_text(
                "📈 *OHLCV Chart*\n\n_No data available for this timeframe_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data="dex:gecko_charts")]
                ])
            )
            return

        # Generate chart image using visualization module
        pair_name = pool_data.get('name', 'Pool')
        base_symbol = pool_data.get('base_token_symbol')
        quote_symbol = pool_data.get('quote_token_symbol')

        chart_buffer = generate_ohlcv_chart(
            ohlcv_data=ohlcv_data,
            pair_name=pair_name,
            timeframe=_format_timeframe_label(timeframe),
            base_symbol=base_symbol,
            quote_symbol=quote_symbol
        )

        if not chart_buffer:
            await loading_msg.edit_text(
                "📈 *OHLCV Chart*\n\n_Failed to generate chart_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data="dex:gecko_charts")]
                ])
            )
            return

        # Build caption
        caption_lines = [
            f"📈 *{escape_markdown_v2(pool_data['name'])}*",
            f"Timeframe: {escape_markdown_v2(_format_timeframe_label(timeframe))}",
            f"Data points: {len(ohlcv_data)}",
        ]

        # Get latest price info
        if ohlcv_data:
            latest = ohlcv_data[0]  # Most recent candle
            if len(latest) >= 5:
                timestamp, open_p, high_p, low_p, close_p = latest[:5]
                caption_lines.append("")
                caption_lines.append(f"Latest: {escape_markdown_v2(_format_price(close_p))}")
                caption_lines.append(f"High: {escape_markdown_v2(_format_price(high_p))}")
                caption_lines.append(f"Low: {escape_markdown_v2(_format_price(low_p))}")

        caption = "\n".join(caption_lines)

        # Build keyboard - add combined view if supported DEX
        dex_id = pool_data.get("dex_id", "")
        network = pool_data.get("network", "")

        keyboard = [
            [
                InlineKeyboardButton("1h" if timeframe != "1m" else "• 1h •", callback_data="dex:gecko_ohlcv:1m"),
                InlineKeyboardButton("1d" if timeframe != "1h" else "• 1d •", callback_data="dex:gecko_ohlcv:1h"),
                InlineKeyboardButton("7d" if timeframe != "1d" else "• 7d •", callback_data="dex:gecko_ohlcv:1d"),
            ],
        ]

        if can_fetch_liquidity(dex_id, network):
            keyboard.append([
                InlineKeyboardButton("📊 + Liquidity", callback_data=f"dex:gecko_combined:{timeframe}"),
            ])

        keyboard.append([
            InlineKeyboardButton("« Back", callback_data="dex:gecko_charts"),
        ])

        # Delete loading message and send photo
        await loading_msg.delete()
        await loading_msg.chat.send_photo(
            photo=chart_buffer,
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error generating OHLCV chart: {e}", exc_info=True)
        await loading_msg.edit_text(
            f"❌ Error loading chart: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data="dex:gecko_charts")]
            ])
        )


def _format_timeframe_label(timeframe: str) -> str:
    """Convert API timeframe to display label"""
    labels = {
        "1m": "1m candles",
        "5m": "5m candles",
        "15m": "15m candles",
        "1h": "1h candles",
        "4h": "4h candles",
        "1d": "1d candles",
    }
    return labels.get(timeframe, timeframe)


# ============================================
# LIQUIDITY CHART (for supported DEXes)
# ============================================

async def show_gecko_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show liquidity distribution chart for selected pool (Meteora/Raydium/Orca only)"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    dex_id = pool_data.get("dex_id", "")
    network = pool_data.get("network", "")

    if not can_fetch_liquidity(dex_id, network):
        await query.answer(f"Liquidity data not available for {dex_id}")
        return

    await query.answer("Loading liquidity chart...")

    # Show loading
    if getattr(query.message, 'photo', None):
        await query.message.delete()
        loading_msg = await query.message.chat.send_message(
            f"📊 *Liquidity Distribution*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
    else:
        await query.message.edit_text(
            f"📊 *Liquidity Distribution*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
        loading_msg = query.message

    try:
        address = pool_data["address"]
        connector = get_connector_for_dex(dex_id)

        # Fetch liquidity bins via gateway
        chat_id = update.effective_chat.id
        bins, pool_info, error = await fetch_liquidity_bins(
            pool_address=address,
            connector=connector,
            user_data=context.user_data,
            chat_id=chat_id
        )

        if error or not bins:
            await loading_msg.edit_text(
                f"📊 *Liquidity Distribution*\n\n_No liquidity data available_\n\n{escape_markdown_v2(error or 'No bins found')}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data="dex:gecko_charts")]
                ])
            )
            return

        # Get current price
        current_price = None
        if pool_info:
            current_price = pool_info.get('price') or pool_info.get('current_price')
            if current_price:
                current_price = float(current_price)

        # Generate chart
        pair_name = pool_data.get('name', 'Pool')
        chart_bytes = generate_liquidity_chart(
            bins=bins,
            current_price=current_price,
            pair_name=pair_name
        )

        if not chart_bytes:
            await loading_msg.edit_text(
                "📊 *Liquidity Distribution*\n\n_Failed to generate chart_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data="dex:gecko_charts")]
                ])
            )
            return

        # Build caption
        caption = f"📊 *{escape_markdown_v2(pair_name)}* \\- Liquidity Distribution\n"
        caption += f"_{escape_markdown_v2(f'{len(bins)} bins')}_"

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton("📊 Combined 1h", callback_data="dex:gecko_combined:1m"),
                InlineKeyboardButton("📊 Combined 1d", callback_data="dex:gecko_combined:1h"),
            ],
            [
                InlineKeyboardButton("« Back", callback_data="dex:gecko_charts"),
            ],
        ]

        # Delete loading and send photo
        await loading_msg.delete()
        await loading_msg.chat.send_photo(
            photo=io.BytesIO(chart_bytes),
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error generating liquidity chart: {e}", exc_info=True)
        await loading_msg.edit_text(
            f"❌ Error: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data="dex:gecko_charts")]
            ])
        )


async def show_gecko_combined(update: Update, context: ContextTypes.DEFAULT_TYPE, timeframe: str) -> None:
    """Show combined OHLCV + Liquidity chart for selected pool"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    dex_id = pool_data.get("dex_id", "")
    network = pool_data.get("network", "")

    if not can_fetch_liquidity(dex_id, network):
        await query.answer(f"Combined view not available for {dex_id}")
        return

    await query.answer("Loading combined chart...")

    # Show loading - keep the message reference for editing
    loading_msg = query.message
    if getattr(query.message, 'photo', None):
        # Edit photo caption to show loading (keeps the existing photo)
        try:
            await query.message.edit_caption(
                caption=f"📊 *Combined View*\n\n_Loading OHLCV \\+ Liquidity\\.\\.\\._",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass  # Caption might be the same, ignore
    else:
        await query.message.edit_text(
            f"📊 *Combined View*\n\n_Loading OHLCV \\+ Liquidity\\.\\.\\._",
            parse_mode="MarkdownV2"
        )

    try:
        address = pool_data["address"]
        connector = get_connector_for_dex(dex_id)

        # Fetch OHLCV data
        client = GeckoTerminalAsyncClient()
        ohlcv_result = await client.get_ohlcv(network, address, timeframe)

        # Parse OHLCV response
        ohlcv_data = []
        try:
            import pandas as pd
            if isinstance(ohlcv_result, pd.DataFrame) and not ohlcv_result.empty:
                if ohlcv_result.index.name == 'datetime' or 'datetime' not in ohlcv_result.columns:
                    ohlcv_result = ohlcv_result.reset_index()
                ohlcv_data = ohlcv_result.values.tolist()
        except ImportError:
            pass

        if not ohlcv_data:
            if isinstance(ohlcv_result, list):
                ohlcv_data = ohlcv_result
            elif isinstance(ohlcv_result, dict):
                ohlcv_data = ohlcv_result.get("data", {}).get("attributes", {}).get("ohlcv_list", [])

        # Fetch liquidity bins
        chat_id = update.effective_chat.id
        bins, pool_info, _ = await fetch_liquidity_bins(
            pool_address=address,
            connector=connector,
            user_data=context.user_data,
            chat_id=chat_id
        )

        if not ohlcv_data and not bins:
            await loading_msg.edit_text(
                "📊 *Combined View*\n\n_No data available_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data="dex:gecko_charts")]
                ])
            )
            return

        # Get current price
        current_price = None
        if pool_info:
            current_price = pool_info.get('price') or pool_info.get('current_price')
            if current_price:
                current_price = float(current_price)

        # Generate combined chart
        pair_name = pool_data.get('name', 'Pool')
        base_symbol = pool_data.get('base_token_symbol')
        quote_symbol = pool_data.get('quote_token_symbol')

        chart_buf = generate_combined_chart(
            ohlcv_data=ohlcv_data or [],
            bins=bins or [],
            pair_name=pair_name,
            timeframe=_format_timeframe_label(timeframe),
            current_price=current_price,
            base_symbol=base_symbol,
            quote_symbol=quote_symbol
        )

        if not chart_buf:
            await loading_msg.edit_text(
                "📊 *Combined View*\n\n_Failed to generate chart_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data="dex:gecko_charts")]
                ])
            )
            return

        # Build caption
        caption = f"📊 *{escape_markdown_v2(pair_name)}* \\- Combined View\n"
        caption += f"_OHLCV \\({escape_markdown_v2(_format_timeframe_label(timeframe))}\\) \\+ Liquidity_"

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton("1h" if timeframe != "1m" else "• 1h •", callback_data="dex:gecko_combined:1m"),
                InlineKeyboardButton("1d" if timeframe != "1h" else "• 1d •", callback_data="dex:gecko_combined:1h"),
                InlineKeyboardButton("7d" if timeframe != "1d" else "• 7d •", callback_data="dex:gecko_combined:1d"),
            ],
            [
                InlineKeyboardButton("📈 Candles Only", callback_data=f"dex:gecko_ohlcv:{timeframe}"),
                InlineKeyboardButton("📊 Liquidity Only", callback_data="dex:gecko_liquidity"),
            ],
            [
                InlineKeyboardButton("« Back", callback_data="dex:gecko_charts"),
            ],
        ]

        # Edit or send photo
        from telegram import InputMediaPhoto
        if loading_msg.photo:
            # Edit existing photo message
            await loading_msg.edit_media(
                media=InputMediaPhoto(media=chart_buf, caption=caption, parse_mode="MarkdownV2"),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Replace text with photo
            await loading_msg.delete()
            await loading_msg.chat.send_photo(
                photo=chart_buf,
                caption=caption,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Error generating combined chart: {e}", exc_info=True)
        await loading_msg.edit_text(
            f"❌ Error: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data="dex:gecko_charts")]
            ])
        )


# ============================================
# RECENT TRADES
# ============================================

async def show_recent_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent trades for selected pool"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer("Loading trades...")

    chat = query.message.chat

    # Show loading - delete current message (may be photo) and send text
    try:
        await query.message.delete()
    except Exception:
        pass

    loading_msg = await chat.send_message(
        r"📜 *Recent Trades*" + "\n\n" + r"_Loading\.\.\._",
        parse_mode="MarkdownV2"
    )

    try:
        network = pool_data["network"]
        address = pool_data["address"]

        client = GeckoTerminalAsyncClient()
        result = await client.get_trades(network, address, 20)

        logger.info(f"Trades raw response type: {type(result)}")

        # Handle DataFrame response
        trades = []
        try:
            import pandas as pd
            if isinstance(result, pd.DataFrame):
                logger.info(f"Trades DataFrame columns: {list(result.columns)}")
                if not result.empty:
                    trades = result.to_dict('records')
                    if trades:
                        logger.debug(f"First trade keys: {list(trades[0].keys())}")
        except ImportError:
            pass

        if not trades:
            trades = _extract_pools_from_response(result, 20)

        if not trades:
            await loading_msg.edit_text(
                r"📜 *Recent Trades*" + "\n\n" + "_No recent trades found_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
                ])
            )
            return

        # Build trades message
        pair = f"{pool_data['base_token_symbol']}/{pool_data['quote_token_symbol']}"
        lines = [f"📜 *Recent Trades \\- {escape_markdown_v2(pair)}*\n"]

        lines.append("```")
        lines.append(f"{'Type':<6} {'Amount':<12} {'Price':<14} {'Time':<10}")
        lines.append("-" * 44)

        for trade in trades[:15]:
            attrs = trade.get("attributes", trade)

            # Get trade type - 'side' contains buy/sell, 'type' is always "trade"
            trade_type = (
                attrs.get("side") or
                attrs.get("kind") or
                attrs.get("trade_type") or
                "?"
            )
            if isinstance(trade_type, str):
                trade_type = trade_type[:4]  # "buy" or "sell"
            else:
                trade_type = "?"

            # Get amount - 'volume_usd' is the correct field from the API
            amount = (
                attrs.get("volume_usd") or
                attrs.get("volume_in_usd") or
                attrs.get("amount_usd")
            )
            if amount:
                try:
                    amount = float(amount)
                    amount_str = _format_volume(amount).replace("$", "")[:11]
                except (ValueError, TypeError):
                    amount_str = "?"
            else:
                amount_str = "?"

            # Get price - show base token price based on trade side
            # buy: swapping quote→base, price_to = base price
            # sell: swapping base→quote, price_from = base price
            if trade_type.lower() == "buy":
                price = attrs.get("price_to_in_usd")
            else:
                price = attrs.get("price_from_in_usd")

            # Fallback to any available price
            if not price:
                price = attrs.get("price_to_in_usd") or attrs.get("price_from_in_usd")

            if price:
                try:
                    price = float(price)
                    price_str = f"${price:.6f}"[:13]
                except (ValueError, TypeError):
                    price_str = "?"
            else:
                price_str = "?"

            # Get time - try multiple field names
            timestamp = (
                attrs.get("block_timestamp") or
                attrs.get("timestamp") or
                attrs.get("datetime") or
                attrs.get("time")
            )
            if timestamp:
                try:
                    # Handle pandas Timestamp
                    if hasattr(timestamp, 'strftime'):
                        time_str = timestamp.strftime("%H:%M:%S")
                    elif isinstance(timestamp, (int, float)):
                        dt = datetime.fromtimestamp(timestamp)
                        time_str = dt.strftime("%H:%M:%S")
                    else:
                        dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                        time_str = dt.strftime("%H:%M:%S")
                except Exception:
                    time_str = "?"
            else:
                time_str = "?"

            emoji = "🟢" if str(trade_type).lower() == "buy" else "🔴"
            lines.append(f"{emoji}{trade_type:<5} {amount_str:<12} {price_str:<14} {time_str:<10}")

        lines.append("```")

        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="dex:gecko_trades"),
                InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}"),
            ],
        ]

        await loading_msg.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error fetching trades: {e}", exc_info=True)
        try:
            await loading_msg.edit_text(
                f"❌ Error loading trades: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
                ])
            )
        except Exception:
            await chat.send_message(
                f"❌ Error loading trades: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
                ])
            )


# ============================================
# UTILITY HANDLERS
# ============================================

async def handle_copy_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send pool address as a copyable message"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    address = pool_data.get("address", "N/A")
    name = pool_data.get("name", "Pool")

    await query.answer()
    # Send address as monospace - easy to tap and copy
    await query.message.reply_text(
        f"📋 *{escape_markdown_v2(name)}*\n\n`{address}`",
        parse_mode="MarkdownV2"
    )


# ============================================
# TOKEN INFO
# ============================================

async def handle_gecko_token_info(update: Update, context: ContextTypes.DEFAULT_TYPE, token_type: str) -> None:
    """Show detailed token info using get_specific_token_on_network"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    # Get token info based on type
    if token_type == "base":
        token_address = pool_data.get("base_token_address", "")
        token_symbol = pool_data.get("base_token_symbol", "Unknown")
    else:
        token_address = pool_data.get("quote_token_address", "")
        token_symbol = pool_data.get("quote_token_symbol", "Unknown")

    network = pool_data.get("network", "")

    if not token_address or not network:
        await query.answer("Token address not available")
        return

    await query.answer("Loading token info...")

    # Show loading
    await query.message.edit_text(
        f"🪙 *Loading {escape_markdown_v2(token_symbol)} info\\.\\.\\.*",
        parse_mode="MarkdownV2"
    )

    try:
        client = GeckoTerminalAsyncClient()
        result = await client.get_specific_token_on_network(network, token_address)

        # Extract token data
        token_data = {}
        if isinstance(result, dict):
            data = result.get("data", result)
            if isinstance(data, dict):
                token_data = data.get("attributes", data)
            elif isinstance(data, list) and data:
                token_data = data[0].get("attributes", data[0])
        elif hasattr(result, 'data'):
            attrs = getattr(result.data, 'attributes', result.data)
            if hasattr(attrs, '__dict__'):
                token_data = attrs.__dict__
            else:
                token_data = attrs

        # Check for pandas DataFrame
        try:
            import pandas as pd
            if isinstance(result, pd.DataFrame) and not result.empty:
                token_data = result.to_dict('records')[0]
        except ImportError:
            pass

        if not token_data:
            await query.message.edit_text(
                f"❌ *Token Info Not Found*\n\n"
                f"Could not fetch info for `{escape_markdown_v2(token_address[:20])}...`",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
                ])
            )
            return

        # Store token data for add-to-gateway
        context.user_data["gecko_current_token"] = {
            "address": token_address,
            "symbol": token_data.get("symbol", token_symbol),
            "name": token_data.get("name", ""),
            "decimals": token_data.get("decimals"),
            "network": network,
        }

        # Build token info display
        name = token_data.get("name", "Unknown")
        symbol = token_data.get("symbol", token_symbol)
        decimals = token_data.get("decimals", "N/A")

        lines = [
            f"🪙 *{escape_markdown_v2(symbol)}* \\- {escape_markdown_v2(name)}\n",
            f"🌐 Network: {escape_markdown_v2(NETWORK_NAMES.get(network, network))}",
        ]

        # Price info
        price_usd = token_data.get("price_usd")
        if price_usd:
            try:
                price = float(price_usd)
                lines.append(f"💰 Price: {escape_markdown_v2(_format_price(price))}")
            except (ValueError, TypeError):
                pass

        # Market data
        fdv = token_data.get("fdv_usd")
        if fdv:
            try:
                lines.append(f"📈 FDV: {escape_markdown_v2(_format_volume(float(fdv)))}")
            except (ValueError, TypeError):
                pass

        market_cap = token_data.get("market_cap_usd")
        if market_cap:
            try:
                lines.append(f"💎 Market Cap: {escape_markdown_v2(_format_volume(float(market_cap)))}")
            except (ValueError, TypeError):
                pass

        total_supply = token_data.get("total_supply")
        if total_supply:
            try:
                lines.append(f"📊 Total Supply: {escape_markdown_v2(_format_volume(float(total_supply)))}")
            except (ValueError, TypeError):
                pass

        # Volume
        vol_24h = token_data.get("volume_usd", {})
        if isinstance(vol_24h, dict):
            h24 = vol_24h.get("h24")
        else:
            h24 = vol_24h
        if h24:
            try:
                lines.append(f"📈 Vol 24h: {escape_markdown_v2(_format_volume(float(h24)))}")
            except (ValueError, TypeError):
                pass

        lines.append("")
        lines.append(f"🔢 Decimals: {escape_markdown_v2(str(decimals))}")
        lines.append(f"\n📍 Address:\n`{token_address}`")

        # GeckoTerminal link
        gecko_url = f"https://www.geckoterminal.com/{network}/tokens/{token_address}"
        lines.append(f"\n🦎 [View on GeckoTerminal]({escape_markdown_v2(gecko_url)})")

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton("🔍 Search Pools", callback_data="dex:gecko_token_search"),
                InlineKeyboardButton("➕ Add to Gateway", callback_data="dex:gecko_token_add"),
            ],
            [
                InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}"),
            ],
        ]

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"Error fetching token info: {e}", exc_info=True)
        await query.message.edit_text(
            f"❌ Error: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
            ])
        )


async def handle_gecko_token_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Search for pools using the current token"""
    query = update.callback_query

    token_info = context.user_data.get("gecko_current_token")
    if not token_info:
        await query.answer("No token selected")
        return

    # Set the network and trigger search with the token address
    network = token_info.get("network", "solana")
    set_gecko_network(context.user_data, network)

    # Store token address for search
    context.user_data["gecko_search_token"] = token_info.get("address", "")
    context.user_data["gecko_view"] = "search"

    # Simulate search
    await query.answer("Searching pools...")

    try:
        client = GeckoTerminalAsyncClient()
        result = await client.get_top_pools_by_network_token(network, token_info.get("address", ""))
        pools = _extract_pools_from_response(result, 10)

        if not pools:
            await query.message.edit_text(
                r"❌ *No pools found*" + "\n\n"
                f"No pools found for this token on {escape_markdown_v2(NETWORK_NAMES.get(network, network))}\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")]
                ])
            )
            return

        # Store pools for selection
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_network"] = network

        # Build message
        symbol = token_info.get("symbol", "Token")
        lines = [f"🔍 *Pools for {escape_markdown_v2(symbol)}*\n"]

        for i, pool in enumerate(pools, 1):
            line = _format_pool_line(pool, i)
            lines.append(escape_markdown_v2(line))

        lines.append("\n_Select a pool for details:_")

        # Build keyboard
        keyboard = []
        row = []
        for i, pool in enumerate(pools):
            base, quote_sym = _get_pool_symbols(pool)
            btn = InlineKeyboardButton(f"{i+1}. {base[:6]}/{quote_sym[:4]}", callback_data=f"dex:gecko_pool:{i}")
            row.append(btn)
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append([
            InlineKeyboardButton("« Back", callback_data="dex:gecko_explore"),
        ])

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error searching token pools: {e}", exc_info=True)
        await query.message.edit_text(
            f"❌ Error: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data="dex:gecko_explore")]
            ])
        )


async def handle_gecko_token_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add token to Gateway"""
    query = update.callback_query

    token_info = context.user_data.get("gecko_current_token")
    if not token_info:
        await query.answer("No token selected")
        return

    # Map GeckoTerminal network to Gateway network
    network_mapping = {
        "solana": "solana-mainnet-beta",
        "eth": "ethereum-mainnet",
        "base": "base-mainnet",
        "arbitrum": "arbitrum-one",
        "bsc": "bsc-mainnet",
        "polygon_pos": "polygon-mainnet",
    }

    gecko_network = token_info.get("network", "solana")
    gateway_network = network_mapping.get(gecko_network, gecko_network)
    symbol = token_info.get("symbol", "")
    name = token_info.get("name", "")
    address = token_info.get("address", "")
    decimals = token_info.get("decimals")

    # If decimals is missing, we need to prompt or use default
    if decimals is None:
        # Default decimals based on network
        if gecko_network == "solana":
            decimals = 9  # Most SPL tokens
        else:
            decimals = 18  # Most ERC20 tokens

    await query.answer("Adding token to Gateway...")

    try:
        from config_manager import get_config_manager

        client = await get_config_manager().get_default_client()
        await client.gateway.add_token(
            network_id=gateway_network,
            address=address,
            symbol=symbol,
            decimals=decimals,
            name=name if name else None
        )

        success_text = (
            f"✅ *Token Added*\n\n"
            f"*{escape_markdown_v2(symbol)}* added to {escape_markdown_v2(gateway_network)}\n\n"
            "⚠️ _Restart Gateway for changes to take effect\\._"
        )

        keyboard = [
            [InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")],
        ]

        await query.message.edit_text(
            success_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error adding token to Gateway: {e}", exc_info=True)

        error_msg = str(e)
        if "already exists" in error_msg.lower():
            error_text = f"⚠️ *Token Already Exists*\n\n{escape_markdown_v2(symbol)} is already in Gateway\\."
        else:
            error_text = f"❌ Error adding token: {escape_markdown_v2(error_msg)}"

        await query.message.edit_text(
            error_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
            ])
        )


async def handle_back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return to the previous pool list"""
    view = context.user_data.get("gecko_view", "trending")
    network = context.user_data.get("gecko_network")

    if view == "trending":
        await show_trending_pools(update, context, network)
    elif view == "top":
        await show_top_pools(update, context, network)
    elif view == "new":
        await show_new_pools(update, context, network)
    elif view == "search":
        # Re-run search
        token = context.user_data.get("gecko_search_token")
        if token:
            await show_gecko_explore_menu(update, context)
        else:
            await show_gecko_explore_menu(update, context)
    else:
        await show_gecko_explore_menu(update, context)


# ============================================
# ADD LIQUIDITY FROM GECKOTERMINAL
# ============================================

async def handle_gecko_add_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle add liquidity from GeckoTerminal pool detail - bridges to pools.py flow"""
    from .pools import _show_pool_detail
    from .pool_data import get_connector_for_dex

    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    dex_id = pool_data.get("dex_id", "")
    network = pool_data.get("network", "")

    # Verify DEX supports liquidity
    if not can_fetch_liquidity(dex_id, network):
        await query.answer(f"Liquidity not supported for {dex_id}")
        return

    await query.answer("Loading pool for liquidity...")

    # Get the connector name (e.g., "meteora", "raydium", "orca")
    connector = get_connector_for_dex(dex_id)
    if not connector:
        await query.answer(f"Unknown connector for {dex_id}")
        return

    # Convert GeckoTerminal pool data to pools.py format
    pool = {
        "pool_address": pool_data.get("address", ""),
        "address": pool_data.get("address", ""),
        "connector": connector,
        "network": "solana-mainnet-beta",  # Currently all supported DEXes are Solana
        "trading_pair": pool_data.get("name", ""),
        "name": pool_data.get("name", ""),
        "mint_x": pool_data.get("base_token_address", ""),
        "mint_y": pool_data.get("quote_token_address", ""),
        "base_token_symbol": pool_data.get("base_token_symbol", ""),
        "quote_token_symbol": pool_data.get("quote_token_symbol", ""),
        # Pool metrics from GeckoTerminal
        "liquidity": pool_data.get("reserve_usd"),
        "volume_24h": pool_data.get("volume_24h"),
    }

    # Store for pools.py flow
    context.user_data["selected_pool"] = pool
    context.user_data["selected_pool_info"] = {}  # Will be fetched by _show_pool_detail
    context.user_data["add_position_params"] = {
        "connector": connector,
        "network": "solana-mainnet-beta",
        "pool_address": pool_data.get("address", ""),
    }

    # Store for back navigation
    context.user_data["gecko_add_liquidity_source"] = True

    # Delete the current message and show the pool detail with add liquidity controls
    try:
        if getattr(query.message, 'photo', None):
            await query.message.delete()
        else:
            await query.message.delete()
    except Exception:
        pass

    # Call pools.py flow
    await _show_pool_detail(update, context, pool, from_callback=True)


# ============================================
# SWAP INTEGRATION
# ============================================

async def handle_gecko_swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set up swap params from GeckoTerminal pool and redirect to swap menu"""
    from .swap import show_swap_menu

    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer("Opening swap...")

    # Get trading pair from pool
    base_symbol = pool_data.get("base_token_symbol", "")
    quote_symbol = pool_data.get("quote_token_symbol", "")

    if not base_symbol or not quote_symbol:
        await query.answer("Token symbols not available")
        return

    # Format trading pair as BASE-QUOTE
    trading_pair = f"{base_symbol}-{quote_symbol}"

    # Map GeckoTerminal network to Gateway network
    gecko_network = pool_data.get("network", "solana")
    gateway_network = GECKO_TO_GATEWAY_NETWORK.get(gecko_network, "solana-mainnet-beta")

    # Get default connector for this network
    connector = NETWORK_DEFAULT_CONNECTOR.get(gecko_network, "jupiter")

    # Set up swap params
    context.user_data["swap_params"] = {
        "connector": connector,
        "network": gateway_network,
        "trading_pair": trading_pair,
        "side": "BUY",
        "amount": "1.0",
        "slippage": "1.0",
    }

    context.user_data["dex_state"] = "swap"

    # Store source for back navigation
    context.user_data["swap_from_gecko"] = True
    context.user_data["swap_gecko_pool_index"] = context.user_data.get("gecko_selected_pool_index", 0)

    # Delete current message (might be a photo)
    try:
        await query.message.delete()
    except Exception:
        pass

    # Show swap menu - send_new=True since we deleted the message
    await show_swap_menu(update, context, send_new=True)


async def show_gecko_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed pool info as text (accessed via Info button)"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer()

    pool_index = context.user_data.get("gecko_selected_pool_index", 0)
    network_name = NETWORK_NAMES.get(pool_data["network"], pool_data["network"])

    lines = [
        f"📊 *{escape_markdown_v2(pool_data['name'])}*\n",
        f"🌐 Network: {escape_markdown_v2(network_name)}",
        f"🏦 DEX: {escape_markdown_v2(pool_data['dex_id'])}",
        "",
        r"💰 *Price Info:*",
    ]

    if pool_data.get("base_token_price_usd"):
        try:
            price = float(pool_data["base_token_price_usd"])
            symbol = escape_markdown_v2(pool_data['base_token_symbol'])
            lines.append(f"• {symbol}: {escape_markdown_v2(_format_price(price))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("quote_token_price_usd"):
        try:
            price = float(pool_data["quote_token_price_usd"])
            symbol = escape_markdown_v2(pool_data['quote_token_symbol'])
            lines.append(f"• {symbol}: {escape_markdown_v2(_format_price(price))}")
        except (ValueError, TypeError):
            pass

    lines.append("")
    lines.append(r"📈 *Price Changes:*")

    for period, key in [("1h", "price_change_1h"), ("6h", "price_change_6h"), ("24h", "price_change_24h")]:
        change = pool_data.get(key)
        if change is not None:
            try:
                change = float(change)
                lines.append(f"• {period}: {escape_markdown_v2(_format_change(change))}")
            except (ValueError, TypeError):
                pass

    lines.append("")
    lines.append(r"📊 *Volume:*")

    for period, key in [("1h", "volume_1h"), ("6h", "volume_6h"), ("24h", "volume_24h")]:
        vol = pool_data.get(key)
        if vol:
            try:
                vol = float(vol)
                lines.append(f"• {period}: {escape_markdown_v2(_format_volume(vol))}")
            except (ValueError, TypeError):
                pass

    # Market cap and FDV
    lines.append("")
    if pool_data.get("market_cap_usd"):
        try:
            mc = float(pool_data["market_cap_usd"])
            lines.append(f"💎 Market Cap: {escape_markdown_v2(_format_volume(mc))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("fdv_usd"):
        try:
            fdv = float(pool_data["fdv_usd"])
            lines.append(f"📈 FDV: {escape_markdown_v2(_format_volume(fdv))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("reserve_usd"):
        try:
            reserve = float(pool_data["reserve_usd"])
            lines.append(f"💧 Liquidity: {escape_markdown_v2(_format_volume(reserve))}")
        except (ValueError, TypeError):
            pass

    # Transactions
    txns = pool_data.get("transactions_24h", {})
    if txns:
        buys = txns.get("buys", 0)
        sells = txns.get("sells", 0)
        if buys or sells:
            lines.append("")
            lines.append(r"🔄 *24h Transactions:*")
            lines.append(f"• Buys: {buys} • Sells: {sells}")

    # Pool address and link
    lines.append("")
    addr = pool_data.get("address", "")
    network = pool_data.get("network", "")
    if addr:
        lines.append(f"📍 Address:\n`{addr}`")
        if network:
            gecko_url = f"https://www.geckoterminal.com/{network}/pools/{addr}"
            lines.append(f"\n🦎 [View on GeckoTerminal]({escape_markdown_v2(gecko_url)})")

    # Token info buttons
    base_addr = pool_data.get("base_token_address", "")
    quote_addr = pool_data.get("quote_token_address", "")
    base_sym = pool_data.get("base_token_symbol", "Base")[:6]
    quote_sym = pool_data.get("quote_token_symbol", "Quote")[:6]

    # Build keyboard
    dex_id = pool_data.get("dex_id", "")
    supports_liquidity = can_fetch_liquidity(dex_id, network)

    keyboard = []

    # Token info buttons if addresses are available
    if base_addr or quote_addr:
        token_row = []
        if base_addr:
            token_row.append(InlineKeyboardButton(f"🪙 {base_sym}", callback_data="dex:gecko_token:base"))
        if quote_addr:
            token_row.append(InlineKeyboardButton(f"🪙 {quote_sym}", callback_data="dex:gecko_token:quote"))
        if token_row:
            keyboard.append(token_row)

    keyboard.append([
        InlineKeyboardButton("📈 Chart", callback_data=f"dex:gecko_pool:{pool_index}"),
        InlineKeyboardButton("💱 Swap", callback_data="dex:gecko_swap"),
        InlineKeyboardButton("📜 Trades", callback_data="dex:gecko_trades"),
    ])

    if supports_liquidity:
        keyboard.append([
            InlineKeyboardButton("➕ Add Liquidity", callback_data="dex:gecko_add_liquidity"),
        ])

    keyboard.append([
        InlineKeyboardButton("« Back", callback_data="dex:gecko_back_to_list"),
    ])

    # Handle case when returning from photo (OHLCV chart) - can't edit photo to text
    if getattr(query.message, 'photo', None):
        await query.message.delete()
        await query.message.chat.send_message(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
    else:
        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )


# ============================================
# POOL DETAIL TIMEFRAME SWITCHING
# ============================================

async def handle_gecko_pool_tf(update: Update, context: ContextTypes.DEFAULT_TYPE, timeframe: str) -> None:
    """Handle timeframe switching in pool detail view - maintains action buttons"""
    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await update.callback_query.answer("No pool selected")
        return

    await _show_pool_chart(update, context, pool_data, timeframe)


# ============================================
# ADD TOKENS TO GATEWAY
# ============================================

async def handle_gecko_add_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add pool tokens to Gateway configuration"""
    import httpx
    from geckoterminal_py import GeckoTerminalAsyncClient
    from config_manager import get_config_manager

    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    # Get token addresses
    base_addr = pool_data.get("base_token_address", "")
    quote_addr = pool_data.get("quote_token_address", "")
    gecko_network = pool_data.get("network", "solana")
    pool_address = pool_data.get("address", "")

    # If no addresses in pool_data, fetch from GeckoTerminal API directly
    if not base_addr and not quote_addr and pool_address:
        await query.answer("Fetching token info...")
        try:
            # Use direct API call since geckoterminal_py doesn't have get_pool method
            url = f"https://api.geckoterminal.com/api/v2/networks/{gecko_network}/pools/{pool_address}"
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params={"include": "base_token,quote_token"})
                if response.status_code == 200:
                    result = response.json()
                    data = result.get('data', {})
                    relationships = data.get('relationships', {})
                    base_token_id = relationships.get('base_token', {}).get('data', {}).get('id', '')
                    quote_token_id = relationships.get('quote_token', {}).get('data', {}).get('id', '')
                    base_addr = _parse_token_address_from_id(base_token_id)
                    quote_addr = _parse_token_address_from_id(quote_token_id)
        except Exception as e:
            logger.warning(f"Failed to fetch pool info for tokens: {e}")

    if not base_addr and not quote_addr:
        await query.answer("No token addresses available", show_alert=True)
        return

    gecko_client = GeckoTerminalAsyncClient()

    # Map GeckoTerminal network to Gateway network
    network_mapping = {
        "solana": "solana-mainnet-beta",
        "eth": "ethereum-mainnet",
        "base": "base-mainnet",
        "arbitrum": "arbitrum-one",
        "bsc": "bsc-mainnet",
        "polygon_pos": "polygon-mainnet",
    }
    gateway_network = network_mapping.get(gecko_network, gecko_network)

    await query.answer("Adding tokens to Gateway...")

    chat = query.message.chat

    # Show loading - delete current message and send loading text
    try:
        await query.message.delete()
    except Exception:
        pass

    loading_msg = await chat.send_message(
        r"🔄 *Adding tokens to Gateway\.\.\.*",
        parse_mode="MarkdownV2"
    )

    added_tokens = []
    errors = []

    async def add_token_to_gateway(token_address: str) -> str:
        """Fetch token info and add to gateway. Returns symbol or error indicator."""
        try:
            # Fetch from GeckoTerminal
            result = await gecko_client.get_specific_token_on_network(gecko_network, token_address)

            token_data = {}
            if isinstance(result, dict):
                data = result.get('data', result) if 'data' in result else result
                token_data = data.get('attributes', data) if isinstance(data, dict) else {}
            else:
                # Try pandas DataFrame
                try:
                    import pandas as pd
                    if isinstance(result, pd.DataFrame) and not result.empty:
                        token_data = result.to_dict('records')[0]
                except ImportError:
                    pass

            if not token_data:
                return None

            symbol = token_data.get('symbol', '???')
            decimals = token_data.get('decimals', 9 if gecko_network == "solana" else 18)
            name = token_data.get('name')

            # Add to gateway
            client = await get_config_manager().get_default_client()
            await client.gateway.add_token(
                network_id=gateway_network,
                address=token_address,
                symbol=symbol,
                decimals=decimals,
                name=name
            )
            return symbol

        except Exception as e:
            error_str = str(e).lower()
            if "already exists" in error_str or "duplicate" in error_str:
                return "exists"
            logger.warning(f"Failed to add token {token_address[:12]}...: {e}")
            return None

    # Add both tokens
    if base_addr:
        result = await add_token_to_gateway(base_addr)
        if result and result != "exists":
            added_tokens.append(result)
        elif result is None:
            errors.append(f"base ({base_addr[:8]}...)")

    if quote_addr:
        result = await add_token_to_gateway(quote_addr)
        if result and result != "exists":
            added_tokens.append(result)
        elif result is None:
            errors.append(f"quote ({quote_addr[:8]}...)")

    # Build result message
    if added_tokens:
        result_msg = f"✅ *Added:* {escape_markdown_v2(', '.join(added_tokens))}\n\n"
    else:
        result_msg = "ℹ️ _Tokens already in Gateway_\n\n"

    if errors:
        result_msg += f"⚠️ Failed: {escape_markdown_v2(', '.join(errors))}\n\n"

    result_msg += r"⚠️ _Restart Gateway for changes to take effect_"

    # Add restart button
    keyboard = [
        [InlineKeyboardButton("🔄 Restart Gateway", callback_data="dex:gecko_restart_gateway")],
        [InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")],
    ]

    try:
        await loading_msg.edit_text(
            result_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception:
        await chat.send_message(
            result_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_gecko_restart_gateway(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart Gateway after adding tokens"""
    from config_manager import get_config_manager

    query = update.callback_query
    await query.answer("Restarting Gateway...")

    chat = query.message.chat

    try:
        await query.message.delete()
    except Exception:
        pass

    loading_msg = await chat.send_message(
        r"🔄 *Restarting Gateway\.\.\.*",
        parse_mode="MarkdownV2"
    )

    try:
        client = await get_config_manager().get_default_client()
        await client.gateway.restart()

        # Wait a moment for restart
        import asyncio
        await asyncio.sleep(2)

        await loading_msg.edit_text(
            r"✅ *Gateway restarted*" + "\n\n_New tokens are now available_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
            ])
        )

    except Exception as e:
        logger.error(f"Failed to restart gateway: {e}", exc_info=True)
        await loading_msg.edit_text(
            f"❌ Failed to restart Gateway: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
            ])
        )


# ============================================
# EXPORTS
# ============================================

__all__ = [
    'show_gecko_explore_menu',
    'handle_gecko_toggle_view',
    'handle_gecko_select_network',
    'handle_gecko_set_network',
    'handle_gecko_show_pools',
    'handle_gecko_trending',
    'show_trending_pools',
    'handle_gecko_top',
    'show_top_pools',
    'handle_gecko_new',
    'show_new_pools',
    'handle_gecko_networks',
    'show_network_menu',
    'handle_gecko_search',
    'handle_gecko_search_network',
    'handle_gecko_search_set_network',
    'process_gecko_search',
    'handle_gecko_refresh',
    'show_pool_detail',
    'show_gecko_charts_menu',
    'show_ohlcv_chart',
    'show_gecko_liquidity',
    'show_gecko_combined',
    'show_recent_trades',
    'handle_copy_address',
    'handle_gecko_token_info',
    'handle_gecko_token_search',
    'handle_gecko_token_add',
    'handle_back_to_list',
    'handle_gecko_add_liquidity',
    'handle_gecko_swap',
    'show_gecko_info',
    'handle_gecko_pool_tf',
    'handle_gecko_add_tokens',
    'handle_gecko_restart_gateway',
]
