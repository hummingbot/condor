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
            logger.info(f"Converted DataFrame with {len(pools)} rows, columns: {list(result.columns)[:10]}")
            if pools:
                logger.debug(f"First pool keys: {list(pools[0].keys())[:15]}")
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

    logger.debug(f"Extracted {len(pools)} pools from response")
    return pools[:limit] if pools else []


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

    # For flattened DataFrames, try direct keys first
    return {
        "id": pool.get("id", ""),
        "name": name,
        "address": _get_nested_value(attrs, "address") or "",
        "base_token_symbol": base_symbol,
        "quote_token_symbol": quote_symbol,
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
# MAIN EXPLORE MENU
# ============================================

async def show_gecko_explore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the main GeckoTerminal explore menu"""
    keyboard = [
        [
            InlineKeyboardButton("🔥 Trending", callback_data="dex:gecko_trending"),
            InlineKeyboardButton("📈 Top Pools", callback_data="dex:gecko_top"),
        ],
        [
            InlineKeyboardButton("🆕 New Pools", callback_data="dex:gecko_new"),
            InlineKeyboardButton("🔍 Search Token", callback_data="dex:gecko_search"),
        ],
        [
            InlineKeyboardButton("🌐 By Network", callback_data="dex:gecko_networks"),
            InlineKeyboardButton("📋 Meteora Pools", callback_data="dex:pool_list"),
        ],
        [
            InlineKeyboardButton("« Back", callback_data="dex:main_menu"),
        ],
    ]

    message = (
        r"🦎 *GeckoTerminal Explorer*" + "\n\n"
        "Explore pools across all DEXes:\n\n"
        "• 🔥 *Trending* \\- Hot pools by activity\n"
        "• 📈 *Top Pools* \\- Highest volume pools\n"
        "• 🆕 *New Pools* \\- Recently created\n"
        "• 🔍 *Search* \\- Find by token\n"
        "• 🌐 *By Network* \\- Filter by chain\n"
        "• 📋 *Meteora Pools* \\- List Meteora CLMM pools\n"
    )

    if update.callback_query:
        await update.callback_query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


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

    # Show loading
    await query.message.edit_text(
        r"🔥 *Trending Pools*" + "\n\n" + r"_Loading\.\.\._",
        parse_mode="MarkdownV2"
    )

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

        # Store pools for selection
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "trending"
        context.user_data["gecko_network"] = network

        # Build message
        network_name = NETWORK_NAMES.get(network, network.title()) if network else "All Networks"
        lines = [f"🔥 *Trending Pools \\- {escape_markdown_v2(network_name)}*\n"]

        if not pools:
            lines.append("\n_No trending pools found_")
        else:
            lines.append("")
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
            InlineKeyboardButton("🔄 Refresh", callback_data=f"dex:gecko_trending_{network or 'all'}"),
            InlineKeyboardButton("« Back", callback_data="dex:gecko_trending"),
        ])

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error fetching trending pools: {e}", exc_info=True)
        await query.message.edit_text(
            f"❌ Error fetching trending pools: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data="dex:gecko_trending")]
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

    # Show loading
    await query.message.edit_text(
        r"📈 *Top Pools*" + "\n\n" + r"_Loading\.\.\._",
        parse_mode="MarkdownV2"
    )

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

        # Store pools for selection
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "top"
        context.user_data["gecko_network"] = network

        # Build message
        network_name = NETWORK_NAMES.get(network, network.title())
        lines = [f"📈 *Top Pools \\- {escape_markdown_v2(network_name)}*\n"]

        if not pools:
            lines.append("\n_No pools found_")
        else:
            lines.append("")
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
            InlineKeyboardButton("🔄 Refresh", callback_data=f"dex:gecko_top_{network}"),
            InlineKeyboardButton("« Back", callback_data="dex:gecko_top"),
        ])

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error fetching top pools: {e}", exc_info=True)
        await query.message.edit_text(
            f"❌ Error fetching top pools: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data="dex:gecko_top")]
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

    # Show loading
    await query.message.edit_text(
        r"🆕 *New Pools*" + "\n\n" + r"_Loading\.\.\._",
        parse_mode="MarkdownV2"
    )

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

        # Store pools for selection
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "new"
        context.user_data["gecko_network"] = network

        # Build message
        network_name = NETWORK_NAMES.get(network, network.title()) if network else "All Networks"
        lines = [f"🆕 *New Pools \\- {escape_markdown_v2(network_name)}*\n"]

        if not pools:
            lines.append("\n_No new pools found_")
        else:
            lines.append("")
            for i, pool in enumerate(pools, 1):
                attrs = pool.get("attributes", pool)
                line = _format_pool_line(pool, i)
                # Add creation time if available
                created = attrs.get("pool_created_at")
                if created:
                    try:
                        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        age = datetime.now(created_dt.tzinfo) - created_dt
                        if age.days > 0:
                            age_str = f"{age.days}d ago"
                        elif age.seconds >= 3600:
                            age_str = f"{age.seconds // 3600}h ago"
                        else:
                            age_str = f"{age.seconds // 60}m ago"
                        line += f" | {age_str}"
                    except Exception:
                        pass
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
            InlineKeyboardButton("🔄 Refresh", callback_data=f"dex:gecko_new_{network or 'all'}"),
            InlineKeyboardButton("« Back", callback_data="dex:gecko_new"),
        ])

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error fetching new pools: {e}", exc_info=True)
        await query.message.edit_text(
            f"❌ Error fetching new pools: {escape_markdown_v2(str(e))}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Back", callback_data="dex:gecko_new")]
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

    keyboard = [
        [InlineKeyboardButton("« Cancel", callback_data="dex:gecko_explore")]
    ]

    message = (
        r"🔍 *Token Search*" + "\n\n"
        "Enter a token address to find pools:\n\n"
        "_Example: Enter a Solana or Ethereum token address_"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def process_gecko_search(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process token search input"""
    context.user_data.pop("dex_state", None)

    # Show loading message
    loading_msg = await update.message.reply_text(
        r"🔍 *Searching\.\.\.*",
        parse_mode="MarkdownV2"
    )

    try:
        # Determine network from address format
        token_address = user_input.strip()

        # Try to detect network
        if token_address.startswith("0x"):
            networks = ["eth", "base", "arbitrum", "bsc", "polygon_pos"]
        else:
            networks = ["solana"]  # Assume Solana for non-0x addresses

        pools = []
        found_network = None

        client = GeckoTerminalAsyncClient()
        for network in networks:
            try:
                result = await client.get_top_pools_by_network_token(network, token_address)
                data = _extract_pools_from_response(result, 10)
                if data:
                    pools = data
                    found_network = network
                    break
            except Exception:
                continue

        if not pools:
            await loading_msg.edit_text(
                r"❌ *No pools found*" + "\n\n"
                "Could not find any pools for this token address\\.",
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
        context.user_data["gecko_network"] = found_network
        context.user_data["gecko_search_token"] = token_address

        # Build message
        network_name = NETWORK_NAMES.get(found_network, found_network)
        lines = [f"🔍 *Pools for Token \\- {escape_markdown_v2(network_name)}*\n"]
        lines.append(f"Token: `{escape_markdown_v2(token_address[:20])}...`\n")

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


# ============================================
# POOL DETAIL VIEW
# ============================================

async def show_pool_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, pool_index: int) -> None:
    """Show detailed information for a selected pool"""
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

    # Build detailed view
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

    # Transactions
    txns = pool_data.get("transactions_24h", {})
    if txns:
        buys = txns.get("buys", 0)
        sells = txns.get("sells", 0)
        if buys or sells:
            lines.append("")
            lines.append(r"🔄 *24h Transactions:*")
            lines.append(f"• Buys: {buys} | Sells: {sells}")

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
    keyboard = [
        [
            InlineKeyboardButton("📈 Candles 1h", callback_data="dex:gecko_ohlcv:1m"),
            InlineKeyboardButton("📈 Candles 1d", callback_data="dex:gecko_ohlcv:1h"),
            InlineKeyboardButton("📈 Candles 7d", callback_data="dex:gecko_ohlcv:1d"),
        ],
        [
            InlineKeyboardButton("📜 Trades", callback_data="dex:gecko_trades"),
        ],
    ]

    # Add liquidity button if DEX supports it (Meteora, Raydium, Orca on Solana)
    dex_id = pool_data.get("dex_id", "")
    network = pool_data.get("network", "")
    if can_fetch_liquidity(dex_id, network):
        keyboard.append([
            InlineKeyboardButton("📊 Liquidity", callback_data="dex:gecko_liquidity"),
            InlineKeyboardButton("📊 Combined", callback_data="dex:gecko_combined:1h"),
        ])

    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"dex:gecko_pool:{pool_index}"),
        InlineKeyboardButton("« Back", callback_data="dex:gecko_back_to_list"),
    ])

    # Handle case when returning from photo (OHLCV chart) - can't edit photo to text
    if query.message.photo:
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
    if query.message.photo:
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
                    [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
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
                    [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
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
                InlineKeyboardButton("Candles 1h" if timeframe != "1m" else "• 1h •", callback_data="dex:gecko_ohlcv:1m"),
                InlineKeyboardButton("Candles 1d" if timeframe != "1h" else "• 1d •", callback_data="dex:gecko_ohlcv:1h"),
                InlineKeyboardButton("Candles 7d" if timeframe != "1d" else "• 7d •", callback_data="dex:gecko_ohlcv:1d"),
            ],
        ]

        if can_fetch_liquidity(dex_id, network):
            keyboard.append([
                InlineKeyboardButton("📊 Combined View", callback_data=f"dex:gecko_combined:{timeframe}"),
            ])

        keyboard.append([
            InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}"),
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
                [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
            ])
        )


def _format_timeframe_label(timeframe: str) -> str:
    """Convert API timeframe to display label"""
    labels = {
        "1m": "1 Hour (1m candles)",
        "5m": "5 Hours (5m candles)",
        "15m": "15 Hours (15m candles)",
        "1h": "1 Day (1h candles)",
        "4h": "4 Days (4h candles)",
        "1d": "7 Days (1d candles)",
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
    if query.message.photo:
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
        bins, pool_info, error = await fetch_liquidity_bins(
            pool_address=address,
            connector=connector,
            user_data=context.user_data
        )

        if error or not bins:
            await loading_msg.edit_text(
                f"📊 *Liquidity Distribution*\n\n_No liquidity data available_\n\n{escape_markdown_v2(error or 'No bins found')}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
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
                    [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
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
                InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}"),
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
                [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
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

    # Show loading
    if query.message.photo:
        await query.message.delete()
        loading_msg = await query.message.chat.send_message(
            f"📊 *Combined View*\n\n_Loading OHLCV \\+ Liquidity\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
    else:
        await query.message.edit_text(
            f"📊 *Combined View*\n\n_Loading OHLCV \\+ Liquidity\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
        loading_msg = query.message

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
        bins, pool_info, _ = await fetch_liquidity_bins(
            pool_address=address,
            connector=connector,
            user_data=context.user_data
        )

        if not ohlcv_data and not bins:
            await loading_msg.edit_text(
                "📊 *Combined View*\n\n_No data available_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
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
                    [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
                ])
            )
            return

        # Build caption
        caption = f"📊 *{escape_markdown_v2(pair_name)}* \\- Combined View\n"
        caption += f"_OHLCV \\({escape_markdown_v2(_format_timeframe_label(timeframe))}\\) \\+ Liquidity_"

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton("Candles 1h" if timeframe != "1m" else "• 1h •", callback_data="dex:gecko_combined:1m"),
                InlineKeyboardButton("Candles 1d" if timeframe != "1h" else "• 1d •", callback_data="dex:gecko_combined:1h"),
                InlineKeyboardButton("Candles 7d" if timeframe != "1d" else "• 7d •", callback_data="dex:gecko_combined:1d"),
            ],
            [
                InlineKeyboardButton("📈 Candles Only", callback_data=f"dex:gecko_ohlcv:{timeframe}"),
                InlineKeyboardButton("📊 Liquidity Only", callback_data="dex:gecko_liquidity"),
            ],
            [
                InlineKeyboardButton("« Back to Pool", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}"),
            ],
        ]

        # Delete loading and send photo
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
                [InlineKeyboardButton("« Back", callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}")]
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

    # Show loading
    await query.message.edit_text(
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
            await query.message.edit_text(
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

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error fetching trades: {e}", exc_info=True)
        await query.message.edit_text(
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
# EXPORTS
# ============================================

__all__ = [
    'show_gecko_explore_menu',
    'handle_gecko_trending',
    'show_trending_pools',
    'handle_gecko_top',
    'show_top_pools',
    'handle_gecko_new',
    'show_new_pools',
    'handle_gecko_networks',
    'show_network_menu',
    'handle_gecko_search',
    'process_gecko_search',
    'show_pool_detail',
    'show_ohlcv_chart',
    'show_recent_trades',
    'handle_copy_address',
    'handle_back_to_list',
]
