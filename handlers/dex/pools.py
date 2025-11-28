"""
DEX Pool and Position Management

Provides:
- CLMM pool listing with LP metrics
- Position management (list, add)
"""

import logging
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from utils.telegram_formatters import escape_markdown_v2, format_error_message, resolve_token_symbol, format_amount, KNOWN_TOKENS
from handlers.config.user_preferences import set_dex_last_pool, get_dex_last_pool
from ._shared import get_gateway_client, get_cached, set_cached, cached_call, DEFAULT_CACHE_TTL

logger = logging.getLogger(__name__)


# ============================================
# TOKEN CACHE HELPERS
# ============================================

async def get_token_cache_from_gateway(network: str = "solana-mainnet-beta") -> dict:
    """
    Fetch tokens from Gateway and build address->symbol cache.

    Args:
        network: Network ID (default: solana-mainnet-beta)

    Returns:
        Dict mapping token addresses to symbols
    """
    token_cache = dict(KNOWN_TOKENS)  # Start with known tokens

    try:
        client = await get_gateway_client()

        # Try to get tokens from Gateway
        if hasattr(client, 'gateway'):
            try:
                if hasattr(client.gateway, 'get_network_tokens') and callable(client.gateway.get_network_tokens):
                    response = await client.gateway.get_network_tokens(network)
                    tokens = response.get('tokens', []) if response else []
                else:
                    # Fallback: get tokens from network config
                    config_response = await client.gateway.get_network_config(network)
                    tokens = config_response.get('tokens', []) if config_response else []

                # Build cache from Gateway tokens
                for token in tokens:
                    address = token.get('address', '')
                    symbol = token.get('symbol', '')
                    if address and symbol:
                        token_cache[address] = symbol

            except Exception as e:
                logger.debug(f"Failed to fetch tokens from Gateway: {e}")

    except Exception as e:
        logger.debug(f"Failed to get Gateway client for token cache: {e}")

    return token_cache


def format_pair_from_addresses(base_token: str, quote_token: str, token_cache: dict = None) -> str:
    """Format a trading pair from token addresses using symbols."""
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    return f"{base_symbol}-{quote_symbol}"


def get_dex_pool_url(connector: str, pool_address: str) -> str:
    """
    Generate the DEX web app URL for a pool.

    Args:
        connector: DEX connector name (meteora, raydium, orca, etc.)
        pool_address: Pool address

    Returns:
        URL to the pool on the DEX web app, or empty string if unknown
    """
    connector_lower = connector.lower()

    if connector_lower == "meteora":
        return f"https://app.meteora.ag/dlmm/{pool_address}?referrer=hummingbot"
    elif connector_lower == "raydium":
        return f"https://raydium.io/clmm/pool/{pool_address}"
    elif connector_lower == "orca":
        return f"https://www.orca.so/pools/{pool_address}"

    return ""


# ============================================
# POOL INFO (by address - supports meteora + raydium)
# ============================================

async def handle_pool_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle CLMM pool info lookup by address"""
    help_text = (
        r"🔍 *Pool Info*" + "\n\n"
        r"Reply with:" + "\n\n"
        r"`connector pool_address`" + "\n\n"
        r"*Examples:*" + "\n"
        r"`meteora 5Q5...abc`" + "\n"
        r"`raydium 7Xy...def`"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pool_info"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


def _format_pool_info(pool: dict) -> str:
    """Format detailed pool information

    Args:
        pool: Pool data dictionary

    Returns:
        Formatted pool info string (not escaped)
    """
    lines = []

    pair = pool.get('trading_pair', pool.get('name', 'N/A'))
    lines.append(f"🏊 Pool: {pair}")
    lines.append("")

    # Basic info
    if pool.get('pool_address') or pool.get('address'):
        addr = pool.get('pool_address') or pool.get('address')
        lines.append(f"📍 Address: {addr[:12]}...{addr[-8:]}")

    if pool.get('bin_step'):
        lines.append(f"📊 Bin Step: {pool.get('bin_step')}")

    if pool.get('fee') is not None:
        fee_pct = float(pool.get('fee', 0)) * 100 if float(pool.get('fee', 0)) < 1 else pool.get('fee')
        lines.append(f"💸 Fee: {fee_pct:.2f}%")

    lines.append("")

    # TVL and volume
    tvl = pool.get('liquidity') or pool.get('tvl')
    if tvl is not None:
        lines.append(f"💰 TVL: ${_format_number(tvl)}")

    vol_24h = pool.get('volume_24h')
    if vol_24h is not None:
        lines.append(f"📈 Volume 24h: ${_format_number(vol_24h)}")

    # APR/Fees
    apr = pool.get('apr')
    if apr is not None:
        lines.append(f"📊 APR: {_format_percent(apr)}")

    fee_tvl = pool.get('fee_tvl_ratio', {})
    if isinstance(fee_tvl, dict) and fee_tvl.get('hour_24'):
        lines.append(f"💵 Fee/TVL 24h: {_format_percent(fee_tvl.get('hour_24'))}")

    lines.append("")

    # Prices
    current_price = pool.get('current_price') or pool.get('price')
    if current_price is not None:
        lines.append(f"💱 Current Price: {current_price}")

    # Token info
    base_token = pool.get('base_token') or pool.get('token_a')
    quote_token = pool.get('quote_token') or pool.get('token_b')
    if base_token:
        lines.append(f"🪙 Base: {base_token}")
    if quote_token:
        lines.append(f"💵 Quote: {quote_token}")

    return "\n".join(lines)


async def process_pool_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process pool info lookup by address - shows full pool details with chart"""
    try:
        parts = user_input.split()
        if len(parts) < 2:
            raise ValueError("Need: connector pool_address\n\nExample: meteora 5Q5...abc")

        connector = parts[0].lower()
        pool_address = parts[1]

        # Validate connector
        if connector not in ["meteora", "raydium"]:
            raise ValueError(f"Unsupported connector '{connector}'. Use 'meteora' or 'raydium'.")

        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        # Send loading message
        loading_msg = await update.message.reply_text("🔄 Loading pool details...")

        # Fetch pool info
        result = await client.gateway_clmm.get_pool_info(
            connector=connector,
            network="solana-mainnet-beta",
            pool_address=pool_address
        )

        # Delete loading message
        try:
            await loading_msg.delete()
        except Exception:
            pass

        if not result:
            message = escape_markdown_v2(f"❌ Pool not found: {pool_address[:16]}...")
            keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            return

        # Construct pool dict with connector info for _show_pool_detail
        pool = {
            'pool_address': pool_address,
            'address': pool_address,
            'connector': connector,
            'trading_pair': result.get('trading_pair', result.get('name', 'N/A')),
            # Copy over any available data from result
            'liquidity': result.get('liquidity') or result.get('tvl'),
            'volume_24h': result.get('volume_24h'),
            'fees_24h': result.get('fees_24h'),
            'base_fee_percentage': result.get('base_fee_percentage') or result.get('fee'),
            'max_fee_percentage': result.get('max_fee_percentage'),
            'apr': result.get('apr'),
            'apy': result.get('apy'),
            'bin_step': result.get('bin_step'),
            'current_price': result.get('current_price') or result.get('price'),
            'mint_x': result.get('mint_x') or result.get('base_token') or result.get('token_a'),
            'mint_y': result.get('mint_y') or result.get('quote_token') or result.get('token_b'),
        }

        # Use the rich pool detail display with chart and add liquidity button
        # has_list_context=False since there's no list to go back to
        await _show_pool_detail(update, context, pool, from_callback=False, has_list_context=False)

    except Exception as e:
        logger.error(f"Error getting pool info: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get pool info: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# POOL LIST (meteora only)
# ============================================

def _build_balance_table_compact(gateway_data: dict) -> str:
    """Build a compact balance table for display in pool list prompt"""
    if not gateway_data or not gateway_data.get("balances_by_network"):
        return ""

    lines = [r"💰 *Your Tokens:*" + "\n"]

    for network, balances in gateway_data["balances_by_network"].items():
        if not balances:
            continue

        # Create compact table for this network
        lines.append(f"```")
        lines.append(f"{'Token':<8} {'Amount':<12} {'Value':>8}")
        lines.append(f"{'─'*8} {'─'*12} {'─'*8}")

        # Show top 5 tokens per network
        for bal in balances[:5]:
            token = bal["token"][:7]
            units = bal["units"]
            value = bal["value"]

            # Format units compactly
            if units >= 1000:
                units_str = f"{units/1000:.1f}K"
            elif units >= 1:
                units_str = f"{units:.2f}"
            else:
                units_str = f"{units:.4f}"
            units_str = units_str[:11]

            # Format value
            if value >= 1000:
                value_str = f"${value/1000:.1f}K"
            else:
                value_str = f"${value:.0f}"
            value_str = value_str[:8]

            lines.append(f"{token:<8} {units_str:<12} {value_str:>8}")

        lines.append(f"```\n")

    return "\n".join(lines)


async def handle_pool_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle CLMM pool list"""
    # Get cached gateway data to show balances
    gateway_data = get_cached(context.user_data, "gateway_data", ttl=120)
    balance_table = _build_balance_table_compact(gateway_data)

    help_text = (
        r"📋 *List CLMM Pools*" + "\n\n" +
        balance_table +
        r"Reply with:" + "\n\n"
        r"`[search_term] [limit]`" + "\n\n"
        r"*Examples:*" + "\n"
        r"`SOL 10`" + "\n"
        r"`USDC 5`" + "\n\n"
        r"_\(Uses Meteora connector\)_"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="dex:main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pool_list"
    # Store message for later editing with results
    context.user_data["pool_list_message_id"] = update.callback_query.message.message_id
    context.user_data["pool_list_chat_id"] = update.callback_query.message.chat_id

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


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


def _format_percent(value, decimals: int = 2) -> str:
    """Format percentage value"""
    if value is None:
        return "—"
    try:
        num = float(value)
        if num == 0:
            return "0%"
        if num >= 100:
            return f"{num:.0f}%"
        return f"{num:.{decimals}f}%"
    except (ValueError, TypeError):
        return "—"


def _format_pool_table(pools: list) -> str:
    """Format pools as a compact table optimized for mobile

    Shows: #, Pair, APR%, Bin, Fee, TVL, Vol

    Args:
        pools: List of pool data dictionaries

    Returns:
        Formatted table string (not escaped)
    """
    if not pools:
        return "No pools found"

    lines = []

    # Header - balanced for mobile (~40 chars)
    lines.append("```")
    lines.append(f"{'#':>2} {'Pair':<10} {'APR%':>6} {'Bin':>3} {'Fee':>4} {'TVL':>5} {'Vol':>4}")
    lines.append("─" * 40)

    for i, pool in enumerate(pools):
        idx = str(i + 1)
        # Truncate pair to 10 chars (fits AVICI-USDC)
        pair = pool.get('trading_pair', 'N/A')[:10]

        # Compact TVL
        tvl = _format_compact(pool.get('liquidity', 0))

        # Base fee percentage - compact
        base_fee = pool.get('base_fee_percentage')
        if base_fee:
            try:
                fee_val = float(base_fee)
                fee_str = f"{fee_val:.1f}" if fee_val >= 1 else f"{fee_val:.1f}"
            except (ValueError, TypeError):
                fee_str = "—"
        else:
            fee_str = "—"

        # APR percentage - always 2 decimals
        apr = pool.get('apr')
        if apr:
            try:
                apr_val = float(apr)
                apr_str = f"{apr_val:.2f}"
            except (ValueError, TypeError):
                apr_str = "—"
        else:
            apr_str = "—"

        # Volume 24h - compact
        vol_24h = _format_compact(pool.get('volume_24h', 0))

        # Bin step
        bin_step = pool.get('bin_step', '—')

        lines.append(f"{idx:>2} {pair:<10} {apr_str:>6} {bin_step:>3} {fee_str:>4} {tvl:>5} {vol_24h:>4}")

    lines.append("```")

    return "\n".join(lines)


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


def _build_pool_selection_keyboard(pools: list, search_term: str = None, is_pair_search: bool = False) -> InlineKeyboardMarkup:
    """Build keyboard with numbered buttons for pool selection

    Args:
        pools: List of pools to select from
        search_term: Original search term
        is_pair_search: Whether this is a BASE-QUOTE pair search (e.g., "ORE-SOL")
    """
    keyboard = []

    # Create rows of 5 buttons each for pool selection
    row = []
    for i, pool in enumerate(pools):
        btn = InlineKeyboardButton(str(i + 1), callback_data=f"dex:pool_select:{i}")
        row.append(btn)
        if len(row) == 5:
            keyboard.append(row)
            row = []

    # Add remaining buttons
    if row:
        keyboard.append(row)

    # Add Plot Liquidity buttons for pair searches (BASE-QUOTE format)
    if is_pair_search and len(pools) > 1:
        keyboard.append([
            InlineKeyboardButton("📊 Plot Top 50%", callback_data="dex:plot_liquidity:50"),
            InlineKeyboardButton("📊 Top 75%", callback_data="dex:plot_liquidity:75"),
            InlineKeyboardButton("📊 Top 90%", callback_data="dex:plot_liquidity:90"),
        ])

    # Add search again and back buttons
    keyboard.append([
        InlineKeyboardButton("🔍 New Search", callback_data="dex:pool_list"),
        InlineKeyboardButton("« Back", callback_data="dex:main_menu")
    ])

    return InlineKeyboardMarkup(keyboard)


async def process_pool_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process pool list (meteora only) or select a pool by number"""
    try:
        user_input = user_input.strip()

        # Check if user is selecting a pool by number
        if user_input.isdigit():
            pool_index = int(user_input) - 1  # Convert to 0-based index
            cached_pools = context.user_data.get("pool_list_cache", [])

            if 0 <= pool_index < len(cached_pools):
                pool = cached_pools[pool_index]
                await _show_pool_detail(update, context, pool)
                return
            else:
                raise ValueError(f"Invalid pool number. Choose 1-{len(cached_pools)}")

        # Otherwise, search for pools
        parts = user_input.split()

        # Always use meteora - only connector that supports pool listing
        connector = "meteora"
        search_term = parts[0] if len(parts) > 0 and parts[0] != "_" else None
        # Parse limit from user input (default 15, max 30 for display)
        requested_limit = int(parts[1]) if len(parts) > 1 else 15
        display_limit = min(requested_limit, 30)  # Cap display at 30
        # Request more from API to have enough after filtering
        api_limit = max(requested_limit * 3, 100)

        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        result = await client.gateway_clmm.get_pools(
            connector=connector,
            page=0,
            limit=api_limit,
            search_term=search_term
        )

        pools = result.get("pools", [])

        if not pools:
            message = escape_markdown_v2("📋 No pools found")
            context.user_data["pool_list_cache"] = []
            keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            # Sort by APR% descending, filter out zero TVL
            active_pools = [p for p in pools if float(p.get('liquidity', 0)) > 0]
            active_pools.sort(key=lambda x: float(x.get('apr', 0) or 0), reverse=True)

            # If no active pools, show all
            display_pools = active_pools[:display_limit] if active_pools else pools[:display_limit]

            # Detect if this is a BASE-QUOTE pair search (e.g., "ORE-SOL", "BTC-USDC")
            is_pair_search = bool(search_term and '-' in search_term)

            # Cache pools for selection (with search term for back navigation)
            context.user_data["pool_list_cache"] = display_pools
            context.user_data["pool_list_search_term"] = search_term
            context.user_data["pool_list_limit"] = display_limit
            context.user_data["pool_list_is_pair_search"] = is_pair_search

            total = result.get("total", len(pools))
            search_info = f" for '{search_term}'" if search_term else ""

            header = rf"📋 *CLMM Pools*{escape_markdown_v2(search_info)} \({len(display_pools)} of {total}\)" + "\n\n"

            table = _format_pool_table(display_pools)
            message = header + table + "\n\n_Select pool number:_"

            # Build keyboard with numbered buttons (add Plot Liquidity for pair searches)
            reply_markup = _build_pool_selection_keyboard(display_pools, search_term, is_pair_search)

        # Keep state for pool selection
        context.user_data["dex_state"] = "pool_list"

        # Try to edit the original message, fall back to reply
        message_id = context.user_data.get("pool_list_message_id")
        chat_id = context.user_data.get("pool_list_chat_id")

        if message_id and chat_id:
            try:
                # Delete user's input message to keep chat clean
                await update.message.delete()
            except Exception:
                pass

            try:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=message,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.warning(f"Failed to edit message, sending new: {e}")
                await update.message.reply_text(
                    message,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
        else:
            await update.message.reply_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error listing pools: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to list pools: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


def _generate_liquidity_chart(
    bins: list,
    active_bin_id: int = None,
    current_price: float = None,
    pair_name: str = "Pool"
) -> bytes:
    """Generate liquidity distribution chart image using Plotly

    Args:
        bins: List of bin data with bin_id, base_token_amount, quote_token_amount, price
        active_bin_id: The current active bin ID
        current_price: Current pool price for vertical line
        pair_name: Trading pair name for title

    Returns:
        PNG image bytes or None if failed
    """
    try:
        import plotly.graph_objects as go
        from io import BytesIO

        if not bins:
            return None

        # Process bin data - convert base token to quote value for comparison
        bin_data = []
        for b in bins:
            base = float(b.get('base_token_amount', 0) or 0)
            quote = float(b.get('quote_token_amount', 0) or 0)
            price = float(b.get('price', 0) or 0)
            bin_id = b.get('bin_id')

            if price > 0:
                # Convert base token amount to quote token value
                base_value_in_quote = base * price
                bin_data.append({
                    'bin_id': bin_id,
                    'base_value': base_value_in_quote,  # Base token value in quote terms
                    'quote': quote,
                    'price': price,
                    'is_active': bin_id == active_bin_id
                })

        if not bin_data:
            return None

        # Sort by price
        bin_data.sort(key=lambda x: x['price'])

        # Extract data for plotting (both now in quote token value)
        prices = [b['price'] for b in bin_data]
        base_values = [b['base_value'] for b in bin_data]  # Base value in quote terms
        quote_amounts = [b['quote'] for b in bin_data]

        # Create figure with stacked bars
        fig = go.Figure()

        # Quote token bars (bottom)
        fig.add_trace(go.Bar(
            x=prices,
            y=quote_amounts,
            name='Quote Token',
            marker_color='#22c55e',  # Green
            hovertemplate='Price: %{x:.6f}<br>Quote Value: %{y:,.2f}<extra></extra>'
        ))

        # Base token bars (top) - now showing value in quote terms
        fig.add_trace(go.Bar(
            x=prices,
            y=base_values,
            name='Base Token (in Quote)',
            marker_color='#3b82f6',  # Blue
            hovertemplate='Price: %{x:.6f}<br>Base Value: %{y:,.2f}<extra></extra>'
        ))

        # Add current price line
        if current_price:
            fig.add_vline(
                x=current_price,
                line_dash="dash",
                line_color="#ef4444",
                line_width=2,
                annotation_text=f"Current: {current_price:.6f}",
                annotation_position="top",
                annotation_font_color="#ef4444"
            )

        # Update layout
        fig.update_layout(
            title=dict(
                text=f"📊 {pair_name} Liquidity Distribution",
                font=dict(size=16, color='white'),
                x=0.5
            ),
            xaxis_title="Price",
            yaxis_title="Liquidity (Quote Value)",
            barmode='stack',
            template='plotly_dark',
            paper_bgcolor='#1a1a2e',
            plot_bgcolor='#16213e',
            font=dict(color='white'),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            ),
            margin=dict(l=60, r=40, t=80, b=60),
            width=800,
            height=500
        )

        # Update axes
        fig.update_xaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(255,255,255,0.1)',
            tickformat='.4f'
        )
        fig.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(255,255,255,0.1)'
        )

        # Export to bytes
        img_bytes = fig.to_image(format="png", scale=2)
        return img_bytes

    except ImportError as e:
        logger.warning(f"Plotly not available for chart generation: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating liquidity chart: {e}", exc_info=True)
        return None


def _generate_aggregated_liquidity_chart(
    pools_data: list,
    pair_name: str = "Aggregated"
) -> bytes:
    """Generate aggregated liquidity distribution chart from multiple pools

    Collects all bins from all pools, buckets them into price ranges,
    and creates a stacked bar chart showing liquidity distribution.

    Args:
        pools_data: List of dicts with 'pool', 'pool_info', 'bins' data
        pair_name: Trading pair name for title

    Returns:
        PNG image bytes or None if failed
    """
    try:
        import plotly.graph_objects as go
        from io import BytesIO
        from collections import defaultdict
        import numpy as np

        if not pools_data:
            logger.warning("No pools_data provided to aggregated chart")
            return None

        # Filter pools that have bins data
        valid_pools = [p for p in pools_data if p.get('bins')]
        if not valid_pools:
            logger.warning("No valid pools with bins data")
            return None

        # Collect all bin data from all pools
        all_bins = []
        total_tvl = 0
        weighted_price_sum = 0

        for pool_data in valid_pools:
            pool = pool_data.get('pool', {})
            pool_info = pool_data.get('pool_info', {})
            bins = pool_data.get('bins', [])

            # Get pool TVL and current price for weighted average
            tvl = float(pool.get('liquidity', 0) or pool_info.get('liquidity', 0) or 0)
            current_price = float(pool_info.get('price', 0) or pool.get('current_price', 0) or 0)

            if tvl > 0 and current_price > 0:
                total_tvl += tvl
                weighted_price_sum += current_price * tvl

            # Process each bin
            for b in bins:
                base = float(b.get('base_token_amount', 0) or 0)
                quote = float(b.get('quote_token_amount', 0) or 0)
                price = float(b.get('price', 0) or 0)

                if price > 0:
                    base_value_in_quote = base * price
                    total_value = base_value_in_quote + quote

                    if total_value > 0:  # Only include bins with liquidity
                        all_bins.append({
                            'price': price,
                            'base_value': base_value_in_quote,
                            'quote': quote,
                            'total': total_value
                        })

        if not all_bins:
            logger.warning("No bins with liquidity collected from pools")
            return None

        # Calculate weighted average current price
        avg_current_price = weighted_price_sum / total_tvl if total_tvl > 0 else None

        # Find price range where most liquidity is (trim outliers)
        prices = [b['price'] for b in all_bins]
        values = [b['total'] for b in all_bins]

        # Calculate cumulative liquidity to find range containing 95% of value
        sorted_by_price = sorted(zip(prices, values, all_bins), key=lambda x: x[0])
        total_value = sum(values)

        # Find price range containing middle 95% of liquidity by value
        cumsum = 0
        price_min = sorted_by_price[0][0]
        price_max = sorted_by_price[-1][0]

        for price, value, _ in sorted_by_price:
            cumsum += value
            if cumsum >= total_value * 0.025:  # 2.5% threshold
                price_min = price
                break

        cumsum = 0
        for price, value, _ in reversed(sorted_by_price):
            cumsum += value
            if cumsum >= total_value * 0.025:  # 2.5% threshold
                price_max = price
                break

        # Add some padding (10%)
        price_range = price_max - price_min
        price_min = max(0, price_min - price_range * 0.1)
        price_max = price_max + price_range * 0.1

        # Filter bins to this range
        filtered_bins = [b for b in all_bins if price_min <= b['price'] <= price_max]

        if not filtered_bins:
            filtered_bins = all_bins  # Fallback to all if filtering removed everything

        logger.info(f"Price range: {price_min:.6f} to {price_max:.6f}, "
                   f"filtered to {len(filtered_bins)} bins from {len(all_bins)}")

        # Create buckets for histogram-style bars (50-80 buckets for visibility)
        num_buckets = min(80, max(30, len(filtered_bins) // 5))
        bucket_size = (price_max - price_min) / num_buckets

        # Aggregate into buckets
        buckets = defaultdict(lambda: {'base_value': 0.0, 'quote': 0.0})

        for b in filtered_bins:
            bucket_idx = int((b['price'] - price_min) / bucket_size)
            bucket_idx = max(0, min(bucket_idx, num_buckets - 1))
            bucket_center = price_min + (bucket_idx + 0.5) * bucket_size

            buckets[bucket_center]['base_value'] += b['base_value']
            buckets[bucket_center]['quote'] += b['quote']

        # Sort buckets by price
        sorted_buckets = sorted(buckets.keys())
        chart_prices = sorted_buckets
        base_values = [buckets[p]['base_value'] for p in sorted_buckets]
        quote_amounts = [buckets[p]['quote'] for p in sorted_buckets]

        total_base = sum(base_values)
        total_quote = sum(quote_amounts)

        logger.info(f"Created {len(chart_prices)} buckets. "
                   f"Total base_value={total_base:.2f}, quote={total_quote:.2f}")

        if total_base == 0 and total_quote == 0:
            logger.warning("All bucket values are zero")
            return None

        # Create figure with stacked bars
        fig = go.Figure()

        # Calculate bar width based on bucket size
        bar_width = bucket_size * 0.85

        # Quote token bars (bottom) - Green
        fig.add_trace(go.Bar(
            x=chart_prices,
            y=quote_amounts,
            name='SOL (Quote)',
            marker_color='#22c55e',
            width=bar_width,
            hovertemplate='Price: %{x:.6f}<br>SOL: %{y:,.2f}<extra></extra>'
        ))

        # Base token bars (top) - Blue
        fig.add_trace(go.Bar(
            x=chart_prices,
            y=base_values,
            name='ORE in SOL (Base)',
            marker_color='#3b82f6',
            width=bar_width,
            hovertemplate='Price: %{x:.6f}<br>ORE (SOL value): %{y:,.2f}<extra></extra>'
        ))

        # Add weighted average current price line
        if avg_current_price and price_min <= avg_current_price <= price_max:
            fig.add_vline(
                x=avg_current_price,
                line_dash="dash",
                line_color="#ef4444",
                line_width=2,
                annotation_text=f"Current: {avg_current_price:.4f}",
                annotation_position="top",
                annotation_font_color="#ef4444",
                annotation_font_size=12
            )

        # Update layout
        fig.update_layout(
            title=dict(
                text=f"📊 {pair_name} Aggregated Liquidity ({len(valid_pools)} pools)",
                font=dict(size=16, color='white'),
                x=0.5
            ),
            xaxis_title="Price (ORE/SOL)",
            yaxis_title="Liquidity (SOL Value)",
            barmode='stack',
            template='plotly_dark',
            paper_bgcolor='#1a1a2e',
            plot_bgcolor='#16213e',
            font=dict(color='white'),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
                font=dict(size=11)
            ),
            margin=dict(l=70, r=40, t=80, b=60),
            width=900,
            height=500,
            bargap=0.05
        )

        # Update axes
        fig.update_xaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(255,255,255,0.1)',
            range=[price_min, price_max],
            tickformat='.4f'
        )
        fig.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(255,255,255,0.1)'
        )

        # Export to bytes
        img_bytes = fig.to_image(format="png", scale=2)
        return img_bytes

    except ImportError as e:
        logger.warning(f"Plotly not available for aggregated chart generation: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating aggregated liquidity chart: {e}", exc_info=True)
        return None


async def handle_plot_liquidity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    percentile: int = 90
) -> None:
    """Handle Plot Liquidity button - aggregates liquidity from top pools by TVL

    Args:
        update: Telegram update
        context: Bot context
        percentile: TVL percentile threshold (e.g., 90 means top 90% by TVL)
    """
    import asyncio
    from io import BytesIO

    query = update.callback_query

    # Get cached pools
    cached_pools = context.user_data.get("pool_list_cache", [])
    search_term = context.user_data.get("pool_list_search_term", "")

    if not cached_pools:
        await query.answer("No pools cached. Please search again.", show_alert=True)
        return

    # Sort pools by TVL descending
    pools_by_tvl = sorted(
        cached_pools,
        key=lambda x: float(x.get('liquidity', 0) or 0),
        reverse=True
    )

    # Calculate TVL threshold for percentile
    total_tvl = sum(float(p.get('liquidity', 0) or 0) for p in pools_by_tvl)
    if total_tvl == 0:
        await query.answer("No pools with liquidity found.", show_alert=True)
        return

    # Select pools that make up the top percentile by TVL
    target_tvl = total_tvl * (percentile / 100)
    accumulated_tvl = 0
    selected_pools = []

    for pool in pools_by_tvl:
        pool_tvl = float(pool.get('liquidity', 0) or 0)
        if pool_tvl > 0:
            selected_pools.append(pool)
            accumulated_tvl += pool_tvl
            if accumulated_tvl >= target_tvl:
                break

    if not selected_pools:
        await query.answer("No pools selected for aggregation.", show_alert=True)
        return

    # Send loading message
    loading_msg = await query.message.reply_text(
        f"🔄 Fetching liquidity data from {len(selected_pools)} pools..."
    )

    try:
        client = await get_gateway_client()

        # Fetch all pool infos in parallel
        async def fetch_pool_with_info(pool):
            """Fetch pool info and return combined data"""
            pool_address = pool.get('pool_address', pool.get('address', ''))
            connector = pool.get('connector', 'meteora')
            try:
                pool_info = await _fetch_pool_info(client, pool_address, connector)
                bins = pool_info.get('bins', [])
                bin_step = pool.get('bin_step') or pool_info.get('bin_step')

                # Log what we're getting
                logger.info(f"Pool {pool_address[:8]}... bins={len(bins)}, bin_step={bin_step}")
                if bins:
                    # Log first bin structure for debugging
                    logger.debug(f"First bin sample: {bins[0]}")

                return {
                    'pool': pool,
                    'pool_info': pool_info,
                    'bins': bins,
                    'bin_step': bin_step
                }
            except Exception as e:
                logger.warning(f"Failed to fetch pool {pool_address}: {e}")
                return None

        # Fetch all pools simultaneously
        tasks = [fetch_pool_with_info(pool) for pool in selected_pools]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter successful results
        pools_data = [r for r in results if r is not None and not isinstance(r, Exception)]

        if not pools_data:
            await loading_msg.edit_text("❌ Failed to fetch pool data.")
            return

        # Log summary of what we got
        total_bins = sum(len(p.get('bins', [])) for p in pools_data)
        logger.info(f"Fetched {len(pools_data)} pools with total {total_bins} bins")

        # Check if we have any bins at all
        if total_bins == 0:
            await loading_msg.edit_text(
                f"❌ No liquidity bin data available for the {len(pools_data)} pools. "
                "The pools may not have detailed bin data exposed via the API."
            )
            return

        # Update loading message
        await loading_msg.edit_text(
            f"📊 Generating aggregated chart for {len(pools_data)} pools ({total_bins} bins)..."
        )

        # Generate aggregated chart
        pair_name = search_term if search_term else "Multi-Pool"
        chart_bytes = _generate_aggregated_liquidity_chart(pools_data, pair_name)

        # Delete loading message
        try:
            await loading_msg.delete()
        except Exception:
            pass

        if not chart_bytes:
            # Try to give more info about what went wrong
            bins_with_liquidity = 0
            for pd in pools_data:
                for b in pd.get('bins', []):
                    base = float(b.get('base_token_amount', 0) or 0)
                    quote = float(b.get('quote_token_amount', 0) or 0)
                    if base > 0 or quote > 0:
                        bins_with_liquidity += 1

            error_detail = f"Total bins: {total_bins}, bins with liquidity: {bins_with_liquidity}"
            logger.error(f"Failed to generate aggregated chart. {error_detail}")

            await query.message.reply_text(
                escape_markdown_v2(f"❌ Failed to generate aggregated liquidity chart.\n{error_detail}"),
                parse_mode="MarkdownV2"
            )
            return

        # Build summary message
        total_tvl_selected = sum(float(p['pool'].get('liquidity', 0) or 0) for p in pools_data)
        bin_steps = [p.get('bin_step', 0) for p in pools_data if p.get('bin_step')]
        min_bin_step = min(bin_steps) if bin_steps else 'N/A'

        lines = [
            f"📊 Aggregated Liquidity: {pair_name}",
            "",
            f"📈 Pools included: {len(pools_data)}",
            f"💰 Total TVL: ${_format_number(total_tvl_selected)}",
            f"📊 Percentile: Top {percentile}%",
            f"🎯 Min bin step (resolution): {min_bin_step}",
        ]

        message = escape_markdown_v2("\n".join(lines))

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton("« Back to List", callback_data="dex:pool_list_back"),
                InlineKeyboardButton("« Main Menu", callback_data="dex:main_menu")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send chart
        photo_file = BytesIO(chart_bytes)
        photo_file.name = "aggregated_liquidity.png"

        await query.message.chat.send_photo(
            photo=photo_file,
            caption=message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error in plot_liquidity: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        error_message = format_error_message(f"Failed to plot liquidity: {str(e)}")
        await query.message.reply_text(error_message, parse_mode="MarkdownV2")


async def _fetch_pool_info(client, pool_address: str, connector: str = "meteora") -> dict:
    """Fetch detailed pool info including bins

    Args:
        client: Gateway client
        pool_address: Pool address to fetch
        connector: Connector name (meteora, raydium)

    Returns:
        Pool info dict with bins data
    """
    try:
        if hasattr(client, 'gateway_clmm'):
            result = await client.gateway_clmm.get_pool_info(
                connector=connector,
                network="solana-mainnet-beta",
                pool_address=pool_address
            )
            return result or {}
    except Exception as e:
        logger.warning(f"Failed to fetch pool info: {e}")
    return {}


async def _show_pool_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pool: dict,
    from_callback: bool = False,
    has_list_context: bool = True
) -> None:
    """Show detailed pool information with address and liquidity chart image

    Args:
        update: Telegram update
        context: Bot context
        pool: Pool data dict
        from_callback: Whether triggered from callback (button click)
        has_list_context: Whether there's a pool list to go back to
    """
    from io import BytesIO

    pool_address = pool.get('pool_address', pool.get('address', 'N/A'))
    connector = pool.get('connector', 'meteora')

    # Fetch additional pool info with bins (cached with 60s TTL)
    cache_key = f"pool_info_{connector}_{pool_address}"
    pool_info = get_cached(context.user_data, cache_key, ttl=DEFAULT_CACHE_TTL)
    if pool_info is None:
        client = await get_gateway_client()
        pool_info = await _fetch_pool_info(client, pool_address, connector)
        set_cached(context.user_data, cache_key, pool_info)

    # Try to get trading pair name from multiple sources
    pair = pool.get('trading_pair') or pool.get('name')
    if not pair or pair == 'N/A':
        # Try to construct from pool_info token symbols
        token_x = pool_info.get('token_x_symbol') or pool_info.get('base_symbol')
        token_y = pool_info.get('token_y_symbol') or pool_info.get('quote_symbol')
        if token_x and token_y:
            pair = f"{token_x}/{token_y}"
        else:
            # Try from mint addresses (truncated)
            mint_x = pool.get('mint_x') or pool_info.get('mint_x')
            mint_y = pool.get('mint_y') or pool_info.get('mint_y')
            if mint_x and mint_y:
                pair = f"{mint_x[:4]}.../{mint_y[:4]}..."
            else:
                pair = "Unknown Pair"

    lines = []
    lines.append(f"🏊 Pool: {pair}")
    lines.append("")

    # Full address - important for identification
    lines.append(f"📍 Address:")
    lines.append(f"   {pool_address}")
    lines.append("")

    # Pool metrics section - collect metrics first, then display
    tvl = pool.get('liquidity') or pool.get('tvl') or pool_info.get('liquidity') or pool_info.get('tvl')
    vol_24h = pool.get('volume_24h') or pool_info.get('volume_24h')
    fees_24h = pool.get('fees_24h') or pool_info.get('fees_24h')

    if tvl or vol_24h or fees_24h:
        lines.append("━━━ Metrics ━━━")
        if tvl:
            lines.append(f"💰 TVL: ${_format_number(tvl)}")
        if vol_24h:
            lines.append(f"📈 Volume 24h: ${_format_number(vol_24h)}")
        if fees_24h:
            lines.append(f"💵 Fees 24h: ${_format_number(fees_24h)}")
        lines.append("")

    # Fees and APR section - collect first, then display
    base_fee = pool.get('base_fee_percentage') or pool_info.get('base_fee_percentage')
    dynamic_fee = pool_info.get('dynamic_fee_pct')
    max_fee = pool.get('max_fee_percentage') or pool_info.get('max_fee_percentage')
    apr = pool.get('apr') or pool_info.get('apr')
    apy = pool.get('apy') or pool_info.get('apy')

    if base_fee or dynamic_fee or max_fee or apr or apy:
        lines.append("━━━ Fees & Yield ━━━")
        if base_fee:
            lines.append(f"💸 Base Fee: {base_fee}%")
        if dynamic_fee:
            lines.append(f"⚡ Dynamic Fee: {dynamic_fee}%")
        if max_fee:
            lines.append(f"📊 Max Fee: {max_fee}%")
        if apr:
            try:
                apr_val = float(apr)
                lines.append(f"📈 APR: {apr_val:.2f}%")
            except (ValueError, TypeError):
                pass
        if apy:
            try:
                apy_val = float(apy)
                if apy_val < 1000000:  # Reasonable APY
                    lines.append(f"📊 APY: {apy_val:.2f}%")
            except (ValueError, TypeError):
                pass
        lines.append("")

    # Pool config section
    lines.append("━━━ Pool Config ━━━")

    # Bin step
    bin_step = pool.get('bin_step') or pool_info.get('bin_step')
    if bin_step:
        lines.append(f"📊 Bin Step: {bin_step}")

    # Current price
    current_price = pool_info.get('price') or pool.get('current_price') or pool.get('price')
    if current_price:
        lines.append(f"💱 Current Price: {current_price}")

    # Active bin
    active_bin = pool_info.get('active_bin_id')
    if active_bin is not None:
        lines.append(f"🎯 Active Bin: {active_bin}")

    # Bins count
    bins = pool_info.get('bins', [])
    active_bins_count = len([b for b in bins if float(b.get('base_token_amount', 0) or 0) + float(b.get('quote_token_amount', 0) or 0) > 0])
    if bins:
        lines.append(f"📊 Active Bins: {active_bins_count}/{len(bins)}")

    # Tokens (from pool_info or pool data)
    mint_x = pool.get('mint_x', '')
    mint_y = pool.get('mint_y', '')
    if mint_x:
        lines.append(f"🪙 Base: {mint_x[:8]}...{mint_x[-4:]}")
    if mint_y:
        lines.append(f"💵 Quote: {mint_y[:8]}...{mint_y[-4:]}")

    message = r"📋 *Pool Details*" + "\n\n"
    message += escape_markdown_v2("\n".join(lines))

    # Store pool for potential use in add position
    context.user_data["selected_pool"] = pool
    context.user_data["selected_pool_info"] = pool_info

    # Build keyboard - show different back button based on context
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Liquidity", callback_data="dex:add_position_from_pool"),
            InlineKeyboardButton("🔄 Refresh", callback_data="dex:pool_detail_refresh"),
            InlineKeyboardButton("📋 Copy Address", callback_data=f"dex:copy_pool:{pool_address[:20]}")
        ]
    ]

    if has_list_context:
        keyboard.append([
            InlineKeyboardButton("« Back to List", callback_data="dex:pool_list_back"),
            InlineKeyboardButton("« Main Menu", callback_data="dex:main_menu")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("« Main Menu", callback_data="dex:main_menu")
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Generate liquidity chart if bins available
    chart_bytes = None
    if bins:
        try:
            price_float = float(current_price) if current_price else None
            chart_bytes = _generate_liquidity_chart(
                bins=bins,
                active_bin_id=active_bin,
                current_price=price_float,
                pair_name=pair
            )
        except Exception as e:
            logger.warning(f"Failed to generate chart: {e}")

    # Determine chat for sending
    if from_callback:
        chat = update.callback_query.message.chat
        # Delete the previous message
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass
    else:
        chat = update.message.chat
        # Delete user input and original message
        try:
            await update.message.delete()
        except Exception:
            pass
        message_id = context.user_data.get("pool_list_message_id")
        chat_id = context.user_data.get("pool_list_chat_id")
        if message_id and chat_id:
            try:
                await update.get_bot().delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass

    # Send chart as photo with caption, or just text if no chart
    if chart_bytes:
        try:
            photo_file = BytesIO(chart_bytes)
            photo_file.name = "liquidity_distribution.png"

            sent_msg = await chat.send_photo(
                photo=photo_file,
                caption=message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            # Store message ID for back navigation
            context.user_data["pool_detail_message_id"] = sent_msg.message_id
            context.user_data["pool_detail_chat_id"] = chat.id
        except Exception as e:
            logger.warning(f"Failed to send chart photo: {e}")
            # Fallback to text only
            await chat.send_message(
                text=message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
    else:
        await chat.send_message(
            text=message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


async def handle_pool_select(update: Update, context: ContextTypes.DEFAULT_TYPE, pool_index: int) -> None:
    """Handle pool selection from numbered button"""
    cached_pools = context.user_data.get("pool_list_cache", [])

    if 0 <= pool_index < len(cached_pools):
        pool = cached_pools[pool_index]
        await _show_pool_detail(update, context, pool, from_callback=True)
    else:
        await update.callback_query.answer("Pool not found. Please search again.")


async def handle_pool_detail_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh pool detail by clearing cache and re-fetching"""
    from ._shared import clear_cache

    selected_pool = context.user_data.get("selected_pool", {})
    if not selected_pool:
        await update.callback_query.answer("No pool selected")
        return

    pool_address = selected_pool.get('pool_address', selected_pool.get('address', ''))
    connector = selected_pool.get('connector', 'meteora')

    # Clear pool info cache
    cache_key = f"pool_info_{connector}_{pool_address}"
    clear_cache(context.user_data, cache_key)
    context.user_data.pop("selected_pool_info", None)

    await update.callback_query.answer("Refreshing...")
    await _show_pool_detail(update, context, selected_pool, from_callback=True)


async def handle_pool_list_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back button to return to pool list"""
    cached_pools = context.user_data.get("pool_list_cache", [])
    search_term = context.user_data.get("pool_list_search_term")
    is_pair_search = context.user_data.get("pool_list_is_pair_search", False)

    if not cached_pools:
        # No cached pools, go to search
        await handle_pool_list(update, context)
        return

    # Rebuild the pool list message
    total = len(cached_pools)
    search_info = f" for '{search_term}'" if search_term else ""

    header = rf"📋 *CLMM Pools*{escape_markdown_v2(search_info)} \({total}\)" + "\n\n"
    table = _format_pool_table(cached_pools)
    message = header + table + "\n\n_Select pool number:_"

    reply_markup = _build_pool_selection_keyboard(cached_pools, search_term, is_pair_search)

    # Keep state for pool selection
    context.user_data["dex_state"] = "pool_list"

    # Delete the current message (could be a photo) and send new text message
    chat = update.callback_query.message.chat
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass

    # Send new message with pool list
    sent_msg = await chat.send_message(
        text=message,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )

    # Store message ID for future navigation
    context.user_data["pool_list_message_id"] = sent_msg.message_id
    context.user_data["pool_list_chat_id"] = chat.id


# ============================================
# MANAGE POSITIONS (unified view)
# ============================================

def _format_position_detail(pos: dict, token_cache: dict = None, detailed: bool = False) -> str:
    """
    Format a single position for display.

    Args:
        pos: Position data dictionary
        token_cache: Optional token address->symbol mapping
        detailed: If True, show full details; if False, show compact summary

    Returns:
        Formatted position string (not escaped)
    """
    token_cache = token_cache or {}

    # Resolve token addresses to symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)

    connector = pos.get('connector', 'unknown')
    pool_address = pos.get('pool_address', '')

    # Get current amounts
    base_amount = pos.get('base_token_amount', pos.get('amount_a', pos.get('token_a_amount', 0)))
    quote_amount = pos.get('quote_token_amount', pos.get('amount_b', pos.get('token_b_amount', 0)))

    # Get price range
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))

    # Get in-range status
    in_range = pos.get('in_range', '')
    range_emoji = "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else "⚪"

    # Get PNL data
    pnl_summary = pos.get('pnl_summary', {})
    base_pnl = pnl_summary.get('base_pnl')
    quote_pnl = pnl_summary.get('quote_pnl')

    # Get pending fees
    base_fee = pos.get('base_fee_pending', pos.get('unclaimed_fee_a', pos.get('fees_a', 0)))
    quote_fee = pos.get('quote_fee_pending', pos.get('unclaimed_fee_b', pos.get('fees_b', 0)))

    lines = []

    # Header: pair with connector
    pair_display = f"{base_symbol}-{quote_symbol}"
    lines.append(f"🏊 {pair_display} ({connector})")

    if detailed:
        # Full detailed view
        if pool_address:
            lines.append(f"   Pool: {pool_address[:12]}...")

        # Range with status indicator
        if lower and upper:
            try:
                lower_f = float(lower)
                upper_f = float(upper)
                if lower_f >= 1:
                    range_str = f"{lower_f:.2f} - {upper_f:.2f}"
                else:
                    range_str = f"{lower_f:.4f} - {upper_f:.4f}"
                lines.append(f"   {range_emoji} Range: [{range_str}]")
            except (ValueError, TypeError):
                lines.append(f"   {range_emoji} Range: [{lower} - {upper}]")

        # Current amounts
        if base_amount or quote_amount:
            try:
                base_amt_str = format_amount(float(base_amount))
                quote_amt_str = format_amount(float(quote_amount))
                lines.append(f"   💰 Holdings: {base_amt_str} {base_symbol} / {quote_amt_str} {quote_symbol}")
            except (ValueError, TypeError):
                lines.append(f"   💰 Holdings: {base_amount} {base_symbol} / {quote_amount} {quote_symbol}")

        # PNL info
        if base_pnl is not None or quote_pnl is not None:
            pnl_parts = []
            if base_pnl is not None:
                sign = "+" if base_pnl >= 0 else ""
                pnl_parts.append(f"{sign}{format_amount(base_pnl)} {base_symbol}")
            if quote_pnl is not None:
                sign = "+" if quote_pnl >= 0 else ""
                pnl_parts.append(f"{sign}{format_amount(quote_pnl)} {quote_symbol}")
            if pnl_parts:
                lines.append(f"   📊 PNL: {' / '.join(pnl_parts)}")

        # Pending fees - always show in detailed view
        try:
            base_fee_f = float(base_fee) if base_fee else 0
            quote_fee_f = float(quote_fee) if quote_fee else 0
            if base_fee_f > 0 or quote_fee_f > 0:
                lines.append(f"   🎁 Pending Fees: {format_amount(base_fee_f)} {base_symbol} / {format_amount(quote_fee_f)} {quote_symbol}")
            else:
                lines.append(f"   🎁 Pending Fees: None")
        except (ValueError, TypeError):
            lines.append(f"   🎁 Pending Fees: N/A")

    else:
        # Compact summary view
        range_str = ""
        if lower and upper:
            try:
                lower_f = float(lower)
                upper_f = float(upper)
                if lower_f >= 1:
                    range_str = f"[{lower_f:.2f}-{upper_f:.2f}]"
                else:
                    range_str = f"[{lower_f:.3f}-{upper_f:.3f}]"
            except (ValueError, TypeError):
                range_str = f"[{lower}-{upper}]"

        # Show current holdings compactly
        try:
            base_amt = float(base_amount) if base_amount else 0
            quote_amt = float(quote_amount) if quote_amount else 0
            if base_amt > 0 or quote_amt > 0:
                lines.append(f"   {range_emoji} {range_str} | {format_amount(base_amt)} {base_symbol} / {format_amount(quote_amt)} {quote_symbol}")
            else:
                lines.append(f"   {range_emoji} Range: {range_str}")
        except (ValueError, TypeError):
            lines.append(f"   {range_emoji} Range: {range_str}")

        # Compact PNL
        if base_pnl is not None or quote_pnl is not None:
            pnl_parts = []
            if quote_pnl is not None:
                sign = "+" if quote_pnl >= 0 else ""
                pnl_parts.append(f"{sign}{format_amount(quote_pnl)} {quote_symbol}")
            if pnl_parts:
                lines.append(f"   📊 {' '.join(pnl_parts)}")

        # Compact Fees - show if any fees are pending
        try:
            base_fee_f = float(base_fee) if base_fee else 0
            quote_fee_f = float(quote_fee) if quote_fee else 0
            if base_fee_f > 0 or quote_fee_f > 0:
                fee_parts = []
                if base_fee_f > 0:
                    fee_parts.append(f"{format_amount(base_fee_f)} {base_symbol}")
                if quote_fee_f > 0:
                    fee_parts.append(f"{format_amount(quote_fee_f)} {quote_symbol}")
                lines.append(f"   🎁 Fees: {' / '.join(fee_parts)}")
        except (ValueError, TypeError):
            pass

    return "\n".join(lines)


async def handle_manage_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display manage positions menu with all active LP positions"""
    try:
        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        # Fetch token cache for symbol resolution
        token_cache = await get_token_cache_from_gateway()
        context.user_data["token_cache"] = token_cache

        # Fetch all open positions
        result = await client.gateway_clmm.search_positions(
            limit=50,
            offset=0,
            status="OPEN"
        )

        all_positions = result.get("data", []) if result else []

        # Filter out CLOSED positions and positions with 0 liquidity
        def is_active_position(pos):
            # Check status field first - exclude CLOSED positions
            status = pos.get('status', '').upper()
            if status == 'CLOSED':
                return False

            # Check liquidity
            liq = pos.get('liquidity') or pos.get('current_liquidity')
            if liq is not None:
                try:
                    return float(liq) > 0
                except (ValueError, TypeError):
                    pass
            # Check token amounts as fallback (use correct field names)
            base = pos.get('base_token_amount') or pos.get('base_amount') or pos.get('amount_base')
            quote = pos.get('quote_token_amount') or pos.get('quote_amount') or pos.get('amount_quote')
            if base is not None or quote is not None:
                try:
                    base_val = float(base) if base is not None else 0
                    quote_val = float(quote) if quote is not None else 0
                    return base_val > 0 or quote_val > 0
                except (ValueError, TypeError):
                    pass
            return True  # Assume active if we can't determine

        positions = [p for p in all_positions if is_active_position(p)]
        if len(positions) < len(all_positions):
            logger.info(f"Filtered {len(all_positions) - len(positions)} closed/empty positions")

        # Build message
        if positions:
            header = rf"📍 *Manage LP Positions* \({len(positions)} active\)" + "\n\n"

            for i, pos in enumerate(positions[:10]):  # Show top 10
                pos_detail = _format_position_detail(pos, token_cache=token_cache, detailed=False)
                # Add index number prefix to correlate with buttons
                header += escape_markdown_v2(f"#{i+1} ") + escape_markdown_v2(pos_detail) + "\n\n"

            if len(positions) > 10:
                header += escape_markdown_v2(f"... and {len(positions) - 10} more positions")
        else:
            header = r"📍 *Manage LP Positions*" + "\n\n"
            header += r"_No active positions found\._" + "\n\n"
            header += r"Use ➕ *New Position* to add liquidity to a pool\."

        # Build keyboard with position actions
        keyboard = []

        # Initialize positions cache
        if "positions_cache" not in context.user_data:
            context.user_data["positions_cache"] = {}

        # Add buttons for each position (max 5 for manageability)
        for i, pos in enumerate(positions[:5]):
            # Resolve pair name for button label
            base_token = pos.get('base_token', pos.get('token_a', ''))
            quote_token = pos.get('quote_token', pos.get('token_b', ''))
            pair = format_pair_from_addresses(base_token, quote_token, token_cache)[:10]

            # Store position info in context for later use
            context.user_data["positions_cache"][str(i)] = pos

            # Use index number to distinguish positions with same pair
            keyboard.append([
                InlineKeyboardButton(f"#{i+1} {pair}", callback_data=f"dex:pos_view:{i}"),
                InlineKeyboardButton("💰 Fees", callback_data=f"dex:pos_collect:{i}"),
                InlineKeyboardButton("❌ Close", callback_data=f"dex:pos_close:{i}")
            ])

        # Add new position and back buttons
        keyboard.append([
            InlineKeyboardButton("➕ New Position", callback_data="dex:add_position"),
            InlineKeyboardButton("« Back", callback_data="dex:main_menu")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await update.callback_query.message.edit_text(
                header,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise

    except Exception as e:
        logger.error(f"Error loading positions: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to load positions: {str(e)}")
        keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.edit_text(
            error_message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


async def handle_pos_view(update: Update, context: ContextTypes.DEFAULT_TYPE, pos_index: str) -> None:
    """View detailed info about a position"""
    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await update.callback_query.answer("Position not found. Please refresh.")
            return

        # Get token cache (fetch if not available)
        token_cache = context.user_data.get("token_cache")
        if not token_cache:
            token_cache = await get_token_cache_from_gateway()
            context.user_data["token_cache"] = token_cache

        # Format detailed view with full information
        detail = _format_position_detail(pos, token_cache=token_cache, detailed=True)
        message = r"📍 *Position Details*" + "\n\n"
        message += escape_markdown_v2(detail)

        # Build keyboard with actions
        connector = pos.get('connector', '')
        pool_address = pos.get('pool_address', '')
        dex_url = get_dex_pool_url(connector, pool_address)

        keyboard = [
            [
                InlineKeyboardButton("💰 Collect Fees", callback_data=f"dex:pos_collect:{pos_index}"),
                InlineKeyboardButton("❌ Close Position", callback_data=f"dex:pos_close:{pos_index}")
            ],
        ]

        # Add DEX link if available
        if dex_url:
            keyboard.append([InlineKeyboardButton(f"🌐 View on {connector.title()}", url=dex_url)])

        keyboard.extend([
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"dex:pos_view:{pos_index}")],
            [InlineKeyboardButton("« Back", callback_data="dex:manage_positions")]
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error viewing position: {e}", exc_info=True)
        await update.callback_query.answer(f"Error: {str(e)[:100]}")


async def handle_pos_collect_fees(update: Update, context: ContextTypes.DEFAULT_TYPE, pos_index: str) -> None:
    """Collect fees from a position - shows progress inline then returns to positions"""
    import asyncio

    query = update.callback_query

    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await query.answer("Position not found. Please refresh.")
            return

        # Get token cache for better pair display
        token_cache = context.user_data.get("token_cache", {})
        base_token = pos.get('base_token', pos.get('token_a', ''))
        quote_token = pos.get('quote_token', pos.get('token_b', ''))
        pair = format_pair_from_addresses(base_token, quote_token, token_cache)

        # Edit message to show collecting status
        await query.answer()
        await query.message.edit_text(
            f"⏳ Collecting fees from {escape_markdown_v2(pair)}\\.\\.\\.",
            parse_mode="MarkdownV2",
            reply_markup=None
        )

        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        # Get position details
        connector = pos.get('connector', 'meteora')
        network = pos.get('network', 'solana-mainnet-beta')
        position_address = pos.get('position_address', pos.get('nft_id', ''))

        # Call collect fees with 10s timeout - Solana should be fast
        try:
            result = await asyncio.wait_for(
                client.gateway_clmm.collect_fees(
                    connector=connector,
                    network=network,
                    position_address=position_address
                ),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            raise TimeoutError("Operation timed out. Check your connection to the backend.")

        # Build back button
        back_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back to Positions", callback_data="dex:manage_positions")]
        ])

        if result:
            success_msg = f"✅ *Fees collected from {escape_markdown_v2(pair)}\\!*"
            if isinstance(result, dict):
                tx_hash = result.get('tx_hash') or result.get('txHash') or result.get('signature')
                if tx_hash:
                    success_msg += f"\n\nTx: `{tx_hash[:30]}...`"

            await query.message.edit_text(
                success_msg,
                parse_mode="MarkdownV2",
                reply_markup=back_keyboard
            )
        else:
            await query.message.edit_text(
                f"ℹ️ No fees to collect from {escape_markdown_v2(pair)}",
                parse_mode="MarkdownV2",
                reply_markup=back_keyboard
            )

    except Exception as e:
        logger.error(f"Error collecting fees: {e}", exc_info=True)

        # Build error message with back button
        error_msg = str(e)
        if "timeout" in error_msg.lower() or "Timeout" in error_msg:
            display_error = "Operation timed out\\. The transaction may still be processing on\\-chain\\."
        else:
            display_error = escape_markdown_v2(error_msg[:150])

        back_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Retry", callback_data=f"dex:pos_collect:{pos_index}")],
            [InlineKeyboardButton("« Back to Positions", callback_data="dex:manage_positions")]
        ])

        try:
            await query.message.edit_text(
                f"❌ *Failed to collect fees*\n\n{display_error}",
                parse_mode="MarkdownV2",
                reply_markup=back_keyboard
            )
        except Exception as edit_error:
            logger.warning(f"Could not edit message: {edit_error}")


async def handle_pos_close_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, pos_index: str) -> None:
    """Show confirmation for closing a position"""
    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await update.callback_query.answer("Position not found. Please refresh.")
            return

        # Get token cache for symbol resolution
        token_cache = context.user_data.get("token_cache") or {}
        detail = _format_position_detail(pos, token_cache=token_cache, detailed=True)

        message = r"⚠️ *Close Position?*" + "\n\n"
        message += escape_markdown_v2(detail) + "\n\n"
        message += r"_This will remove all liquidity from this position\._"

        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Close", callback_data=f"dex:pos_close_exec:{pos_index}"),
                InlineKeyboardButton("❌ Cancel", callback_data="dex:manage_positions")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing close confirmation: {e}", exc_info=True)
        await update.callback_query.answer(f"Error: {str(e)[:100]}")


async def handle_pos_close_execute(update: Update, context: ContextTypes.DEFAULT_TYPE, pos_index: str) -> None:
    """Execute closing a position (remove all liquidity)"""
    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await update.callback_query.answer("Position not found. Please refresh.")
            return

        # Get token cache for symbol resolution
        token_cache = context.user_data.get("token_cache") or {}
        detail = _format_position_detail(pos, token_cache=token_cache, detailed=True)

        # Immediately update message to show closing status (remove keyboard)
        closing_msg = r"⏳ *Closing Position\.\.\.*" + "\n\n"
        closing_msg += escape_markdown_v2(detail) + "\n\n"
        closing_msg += r"_Please wait, this may take a moment\._"

        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            closing_msg,
            parse_mode="MarkdownV2"
        )

        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        # Get position details
        connector = pos.get('connector', 'meteora')
        network = pos.get('network', 'solana-mainnet-beta')
        position_address = pos.get('position_address', pos.get('nft_id', ''))

        # Close the position completely
        result = await client.gateway_clmm.close_position(
            connector=connector,
            network=network,
            position_address=position_address
        )

        if result:
            # Clear the positions cache to force fresh fetch
            context.user_data.pop("positions_cache", None)
            context.user_data.pop("all_positions", None)

            pair = pos.get('trading_pair', 'Unknown')
            success_msg = escape_markdown_v2(f"✅ Position closed: {pair}")

            if isinstance(result, dict) and result.get('tx_hash'):
                success_msg += f"\n\nTx: `{escape_markdown_v2(result['tx_hash'][:20])}...`"

            keyboard = [[InlineKeyboardButton("« Back to Positions", callback_data="dex:manage_positions")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.callback_query.message.edit_text(
                success_msg,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            await update.callback_query.answer("Failed to close position")

    except Exception as e:
        logger.error(f"Error closing position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to close position: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# POSITION LIST (legacy - for specific pool query)
# ============================================

async def handle_position_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle CLMM position list for specific pool"""
    help_text = (
        r"📍 *Get CLMM Positions*" + "\n\n"
        r"Reply with:" + "\n\n"
        r"`connector network pool_address`" + "\n\n"
        r"*Example:*" + "\n"
        r"`meteora solana\-mainnet\-beta POOL_ADDRESS`"
    )

    keyboard = [[InlineKeyboardButton("« Cancel", callback_data="dex:manage_positions")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "position_list"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def process_position_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process position list"""
    try:
        parts = user_input.split()
        if len(parts) < 3:
            raise ValueError("Need: connector network pool_address")

        connector = parts[0]
        network = parts[1]
        pool_address = parts[2]

        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        positions = await client.gateway_clmm.get_positions_owned(
            connector=connector,
            network=network,
            pool_address=pool_address
        )

        # Save params
        set_dex_last_pool(context.user_data, {
            "connector": connector,
            "network": network,
            "pool_address": pool_address
        })

        if not positions:
            message = escape_markdown_v2("📍 No positions found")
        else:
            pos_lines = []
            for pos in positions[:5]:
                pos_id = pos.get('position_address', pos.get('nft_id', 'N/A'))
                lower = pos.get('lower_price', 'N/A')
                upper = pos.get('upper_price', 'N/A')
                pos_lines.append(f"• {pos_id[:8]}... [{lower}-{upper}]")

            pos_text = escape_markdown_v2("\n".join(pos_lines))
            message = rf"📍 *CLMM Positions* \({len(positions)} found\)" + "\n\n" + pos_text

        await update.message.reply_text(message, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error getting positions: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get positions: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# ADD POSITION
# ============================================

async def handle_add_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle add CLMM position"""

    # Initialize position params with defaults or last used
    if "add_position_params" not in context.user_data:
        last_pool = get_dex_last_pool(context.user_data)
        context.user_data["add_position_params"] = {
            "connector": last_pool.get("connector", "meteora"),
            "network": last_pool.get("network", "solana-mainnet-beta"),
            "pool_address": last_pool.get("pool_address", ""),
            "lower_price": "",
            "upper_price": "",
            "amount_base": "10%",  # Default to 10% of balance
            "amount_quote": "10%",  # Default to 10% of balance
            "strategy_type": "0",  # Default strategy type (Spot for Meteora)
        }

    context.user_data["dex_state"] = "add_position"

    await show_add_position_menu(update, context)


def _calculate_max_range(current_price: float, bin_step: int, max_bins: int = 41) -> tuple:
    """Calculate price range for liquidity position

    Args:
        current_price: Current pool price
        bin_step: Pool bin step in basis points (e.g., 100 = 1%)
        max_bins: Number of bins (default 41 = 20 bins each side + active bin)

    Returns:
        Tuple of (lower_price, upper_price)
    """
    if not current_price or not bin_step:
        return None, None

    try:
        # Bin step is in basis points (100 = 1% = 0.01)
        step_multiplier = 1 + (bin_step / 10000)

        # Calculate range: 20 bins below, 20 bins above (+ active bin = 41)
        half_bins = max_bins // 2

        lower_price = current_price / (step_multiplier ** half_bins)
        upper_price = current_price * (step_multiplier ** half_bins)

        return lower_price, upper_price
    except Exception:
        return None, None


async def _fetch_token_balances(client, network: str, base_token: str, quote_token: str) -> dict:
    """Fetch wallet balances for base and quote tokens

    Args:
        client: Gateway client
        network: Network name (e.g., 'solana-mainnet-beta')
        base_token: Base token symbol
        quote_token: Quote token symbol

    Returns:
        Dict with 'base_balance', 'quote_balance', 'base_value', 'quote_value'
    """
    result = {
        "base_balance": 0.0,
        "quote_balance": 0.0,
        "base_value": 0.0,
        "quote_value": 0.0,
    }

    try:
        if not hasattr(client, 'portfolio'):
            return result

        # Fetch portfolio state
        state = await client.portfolio.get_state()
        if not state:
            return result

        # Normalize token symbols for comparison
        base_upper = base_token.upper() if base_token else ""
        quote_upper = quote_token.upper() if quote_token else ""

        # Network name normalization for connector matching
        # e.g., 'solana-mainnet-beta' -> match 'solana', 'gateway_solana', etc.
        network_key = network.split("-")[0].lower() if network else ""

        for account_name, account_data in state.items():
            for connector_name, balances in account_data.items():
                connector_lower = connector_name.lower()
                # Check if this is a gateway connector matching our network
                is_match = (
                    network_key in connector_lower or
                    "gateway" in connector_lower and network_key in connector_lower
                )

                if is_match and balances:
                    for bal in balances:
                        token = bal.get("token", "").upper()
                        units = float(bal.get("units", 0) or 0)
                        value = float(bal.get("value", 0) or 0)

                        if token == base_upper:
                            result["base_balance"] = units
                            result["base_value"] = value
                        elif token == quote_upper:
                            result["quote_balance"] = units
                            result["quote_value"] = value

    except Exception as e:
        logger.warning(f"Error fetching token balances: {e}")

    return result


def _generate_range_ascii(bins: list, lower_price: float, upper_price: float,
                          current_price: float, width: int = 20, max_bins_per_side: int = 40,
                          bin_step: int = 2) -> str:
    """Generate improved ASCII visualization of liquidity with selected range markers

    Args:
        bins: List of bin data with price and liquidity
        lower_price: Selected lower bound
        upper_price: Selected upper bound
        current_price: Current pool price
        width: Width of the bar chart
        max_bins_per_side: Maximum bins to show on each side of current price (default 40)
        bin_step: Step between displayed bins (default 2, shows every other bin)

    Returns:
        ASCII chart string (no leading/trailing code blocks - handled by caller)
    """
    if not bins:
        return ""

    # Process bins
    bin_data = []
    for b in bins:
        base = float(b.get('base_token_amount', 0) or 0)
        quote = float(b.get('quote_token_amount', 0) or 0)
        price = float(b.get('price', 0) or 0)
        if price > 0:
            bin_data.append({'price': price, 'liq': base + quote})

    if not bin_data:
        return ""

    # Sort bins by price
    bin_data.sort(key=lambda x: x['price'])

    # Find current price index for centering
    current_idx = 0
    if current_price:
        for i, b in enumerate(bin_data):
            if b['price'] >= current_price:
                current_idx = i
                break
        else:
            current_idx = len(bin_data) - 1

    # Calculate range to display: max_bins_per_side on each side of current price
    start_idx = max(0, current_idx - max_bins_per_side)
    end_idx = min(len(bin_data), current_idx + max_bins_per_side + 1)

    # Get subset of bins in display range
    display_bins = bin_data[start_idx:end_idx]

    if not display_bins:
        return ""

    # Find max liquidity for scaling (from all bins for consistent scale)
    max_liq = max((b['liq'] for b in bin_data), default=0)
    if max_liq <= 0:
        max_liq = 1  # Avoid division by zero

    # Build histogram with step sampling
    lines = []
    for i, b in enumerate(display_bins):
        # Only show every bin_step bins, but always show bins near current price and bounds
        price = b['price']
        is_current = current_price and abs(price - current_price) / max(current_price, 0.0001) < 0.03
        near_lower = lower_price and abs(price - lower_price) / max(lower_price, 0.0001) < 0.03
        near_upper = upper_price and abs(price - upper_price) / max(upper_price, 0.0001) < 0.03

        # Skip bins not at step interval unless they're special (current, lower, upper bounds)
        if i % bin_step != 0 and not is_current and not near_lower and not near_upper:
            continue

        liq = b['liq']

        # Calculate bar length
        bar_len = int((liq / max_liq) * width) if max_liq > 0 else 0

        # Determine if in range
        in_range = lower_price <= price <= upper_price if lower_price and upper_price else False

        # Build bar with different characters for in/out of range
        bar = "█" * bar_len if in_range else "░" * bar_len

        # Marker column - use intuitive symbols
        if is_current:
            marker = "◄"  # Current price marker
        elif near_lower:
            marker = "↓"  # Lower bound marker
        elif near_upper:
            marker = "↑"  # Upper bound marker
        else:
            marker = " "

        # Format price compactly
        if price >= 1:
            p_str = f"{price:.4f}"[:7]
        else:
            p_str = f"{price:.5f}"[:7]

        lines.append(f"{p_str} |{bar:<{width}}|{marker}")

    return "\n".join(lines)


async def show_add_position_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    send_new: bool = False,
    show_help: bool = False
) -> None:
    """Display the add position configuration menu with liquidity chart

    Args:
        update: The update object
        context: The context object
        send_new: If True, always send a new message instead of editing
        show_help: If True, show detailed help instead of balances/ASCII
    """
    from io import BytesIO

    params = context.user_data.get("add_position_params", {})

    # Get pool info if available for range suggestions
    selected_pool = context.user_data.get("selected_pool", {})
    pool_info = context.user_data.get("selected_pool_info", {})

    # If we have a pool but no pool_info with bins, try to use cached or fetch
    pool_address = params.get('pool_address') or selected_pool.get('pool_address', selected_pool.get('address', ''))
    connector = params.get('connector') or selected_pool.get('connector', 'meteora')

    if pool_address and not pool_info.get('bins'):
        # Try to get from cache first
        cache_key = f"pool_info_{connector}_{pool_address}"
        cached_info = get_cached(context.user_data, cache_key, ttl=DEFAULT_CACHE_TTL)
        if cached_info and cached_info.get('bins'):
            pool_info = cached_info
            context.user_data["selected_pool_info"] = pool_info

    # Get current price and bin step for range calculation
    # Prefer pool_info (fetched data) over selected_pool (list data)
    current_price = pool_info.get('price') or selected_pool.get('current_price') or selected_pool.get('price')
    bin_step = pool_info.get('bin_step') or selected_pool.get('bin_step')
    bins = pool_info.get('bins', [])
    pair = selected_pool.get('trading_pair') or pool_info.get('trading_pair', 'Pool')
    network = params.get('network', 'solana-mainnet-beta')

    # Extract token symbols from pool info
    base_token = selected_pool.get('base_token') or pool_info.get('base_token')
    quote_token = selected_pool.get('quote_token') or pool_info.get('quote_token')
    # Fall back to parsing from pair name if not available
    if not base_token and '-' in pair:
        base_token = pair.split('-')[0]
    if not quote_token and '-' in pair:
        quote_token = pair.split('-')[1] if len(pair.split('-')) > 1 else ''

    # Calculate max range (69 bins) and auto-fill if not set
    suggested_lower, suggested_upper = None, None
    if current_price and bin_step:
        try:
            suggested_lower, suggested_upper = _calculate_max_range(
                float(current_price),
                int(bin_step)
            )
            # Auto-fill if empty
            if suggested_lower and not params.get('lower_price'):
                params['lower_price'] = f"{suggested_lower:.6f}"
            if suggested_upper and not params.get('upper_price'):
                params['upper_price'] = f"{suggested_upper:.6f}"
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to calculate range: {e}")

    # Get current range values for visualization
    try:
        lower_val = float(params.get('lower_price', 0)) if params.get('lower_price') else None
        upper_val = float(params.get('upper_price', 0)) if params.get('upper_price') else None
        current_val = float(current_price) if current_price else None
    except (ValueError, TypeError):
        lower_val, upper_val, current_val = None, None, None

    # Debug logging for ASCII visualization
    logger.info(f"Add position - bins: {len(bins)}, lower: {lower_val}, upper: {upper_val}, price: {current_val}")

    if show_help:
        # ========== HELP VIEW ==========
        help_text = r"📖 *Add Position \- Help*" + "\n\n"

        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
        help_text += r"*🎮 Button Guide*" + "\n"
        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

        help_text += r"• *Row 1:* Lower \\& Upper Price Bounds" + "\n"
        help_text += r"  _Define your position's price range_" + "\n\n"

        help_text += r"• *Row 2:* Base \\& Quote Amounts" + "\n"
        help_text += r"  _Set how much to deposit_" + "\n\n"

        help_text += r"• *Row 3:* Strategy Type" + "\n"
        help_text += r"  _Meteora liquidity distribution_" + "\n\n"

        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
        help_text += r"*💰 Amount Formats*" + "\n"
        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

        help_text += r"• `10%` \- 10% of your wallet balance" + "\n"
        help_text += r"• `100` \- Exact 100 tokens" + "\n"
        help_text += r"• `0\.5` \- Exact 0\.5 tokens" + "\n\n"

        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
        help_text += r"*🎯 Strategy Types*" + "\n"
        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

        help_text += r"• `0` \- *Spot*: Uniform distribution" + "\n"
        help_text += r"• `1` \- *Curve*: Bell curve around price" + "\n"
        help_text += r"• `2` \- *Bid Ask*: Split at current price" + "\n\n"

        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
        help_text += r"*📊 Chart Legend*" + "\n"
        help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

        help_text += r"• `█` \- Bins in your selected range" + "\n"
        help_text += r"• `░` \- Bins outside your range" + "\n"
        help_text += r"• `◄` \- Current market price" + "\n"
        help_text += r"• `↓` \- Near your lower bound" + "\n"
        help_text += r"• `↑` \- Near your upper bound" + "\n"

    else:
        # ========== MAIN VIEW ==========
        help_text = r"➕ *Add CLMM Position*" + "\n\n"

        # Pool info header
        help_text += f"🏊 *Pool:* `{escape_markdown_v2(pair)}`\n"
        if current_price:
            help_text += f"💱 *Price:* `{escape_markdown_v2(str(current_price)[:10])}`\n"
        if bin_step:
            help_text += f"📊 *Bin Step:* `{escape_markdown_v2(str(bin_step))}` _\\(default 20 bins each side\\)_\n"

        # Fetch and display token balances (cached with 60s TTL)
        try:
            balance_cache_key = f"token_balances_{network}_{base_token}_{quote_token}"
            balances = get_cached(context.user_data, balance_cache_key, ttl=DEFAULT_CACHE_TTL)
            if balances is None:
                client = await get_gateway_client()
                balances = await _fetch_token_balances(client, network, base_token, quote_token)
                set_cached(context.user_data, balance_cache_key, balances)

            if balances["base_balance"] > 0 or balances["quote_balance"] > 0:
                help_text += "\n" + r"━━━ Wallet Balances ━━━" + "\n"

                # Format base token balance
                if balances["base_balance"] > 0:
                    base_bal_str = _format_number(balances["base_balance"])
                    base_val_str = f"${_format_number(balances['base_value'])}" if balances["base_value"] > 0 else ""
                    help_text += f"💰 `{escape_markdown_v2(base_token)}`: `{escape_markdown_v2(base_bal_str)}` {escape_markdown_v2(base_val_str)}\n"

                # Format quote token balance
                if balances["quote_balance"] > 0:
                    quote_bal_str = _format_number(balances["quote_balance"])
                    quote_val_str = f"${_format_number(balances['quote_value'])}" if balances["quote_value"] > 0 else ""
                    help_text += f"💵 `{escape_markdown_v2(quote_token)}`: `{escape_markdown_v2(quote_bal_str)}` {escape_markdown_v2(quote_val_str)}\n"

                # Store balances in context for percentage calculation
                context.user_data["token_balances"] = balances

        except Exception as e:
            logger.warning(f"Could not fetch token balances: {e}")

        # NOTE: ASCII visualization is added AFTER we know if chart image is available
        # This is done below, after chart_bytes is generated

    # Build keyboard - values shown in buttons, not in message body
    lower_display = params.get('lower_price', '—')[:8] if params.get('lower_price') else '—'
    upper_display = params.get('upper_price', '—')[:8] if params.get('upper_price') else '—'
    base_display = params.get('amount_base') or '10%'
    quote_display = params.get('amount_quote') or '10%'
    strategy_display = params.get('strategy_type', '0')

    # Strategy type name mapping
    strategy_names = {'0': 'Spot', '1': 'Curve', '2': 'BidAsk'}
    strategy_name = strategy_names.get(strategy_display, 'Spot')

    keyboard = [
        [
            InlineKeyboardButton(
                f"📉 Lower: {lower_display}",
                callback_data="dex:pos_set_lower"
            ),
            InlineKeyboardButton(
                f"📈 Upper: {upper_display}",
                callback_data="dex:pos_set_upper"
            )
        ],
        [
            InlineKeyboardButton(
                f"💰 Base: {base_display}",
                callback_data="dex:pos_set_base"
            ),
            InlineKeyboardButton(
                f"💵 Quote: {quote_display}",
                callback_data="dex:pos_set_quote"
            )
        ],
        [
            InlineKeyboardButton(
                f"🎯 Strategy: {strategy_name}",
                callback_data="dex:pos_toggle_strategy"
            )
        ]
    ]

    # Help/Back toggle and action buttons
    help_button = (
        InlineKeyboardButton("« Position", callback_data="dex:add_position_from_pool")
        if show_help else
        InlineKeyboardButton("❓ Help", callback_data="dex:pos_help")
    )
    keyboard.append([
        InlineKeyboardButton("➕ Add Position", callback_data="dex:pos_add_confirm"),
        InlineKeyboardButton("🔄 Refresh", callback_data="dex:pos_refresh"),
        help_button,
    ])
    keyboard.append([
        InlineKeyboardButton("« Back", callback_data="dex:pool_list_back")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Generate chart image if bins available (only for main view, not help)
    chart_bytes = None
    if bins and not show_help:
        try:
            chart_bytes = _generate_liquidity_chart(
                bins=bins,
                active_bin_id=pool_info.get('active_bin_id'),
                current_price=current_val,
                pair_name=pair
            )
        except Exception as e:
            logger.warning(f"Failed to generate chart for add position: {e}")

    # Add ASCII visualization ONLY if there's NO chart image (ASCII is fallback)
    # This avoids caption too long error on Telegram (1024 char limit for photo captions)
    if not chart_bytes and not show_help and bins and lower_val and upper_val:
        ascii_lines = _generate_range_ascii(bins, lower_val, upper_val, current_val)
        if ascii_lines:
            help_text += "\n```\n" + ascii_lines + "\n```\n"
            help_text += r"_█ in range  ░ out  ◄ current price  ↓↑ your bounds_" + "\n"

    # Determine how to send
    if send_new or not update.callback_query:
        chat = update.message.chat if update.message else update.callback_query.message.chat
        if chart_bytes:
            try:
                photo_file = BytesIO(chart_bytes)
                photo_file.name = "liquidity.png"
                await chat.send_photo(
                    photo=photo_file,
                    caption=help_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.warning(f"Failed to send chart: {e}")
                await chat.send_message(text=help_text, parse_mode="MarkdownV2", reply_markup=reply_markup)
        else:
            await chat.send_message(text=help_text, parse_mode="MarkdownV2", reply_markup=reply_markup)
    else:
        # Try to edit caption if it's a photo, otherwise edit text
        # Prioritize editing over delete+resend to avoid message flicker
        msg = update.callback_query.message
        try:
            if msg.photo:
                # It's a photo, edit caption only (keep existing image)
                await msg.edit_caption(
                    caption=help_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            else:
                await msg.edit_text(
                    text=help_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
        except Exception as e:
            error_str = str(e).lower()
            if "not modified" in error_str:
                pass
            else:
                # Log error but don't delete message - just try updating keyboard
                logger.warning(f"Failed to edit message: {e}")
                try:
                    # Try updating just the keyboard as fallback
                    if msg.photo:
                        await msg.edit_reply_markup(reply_markup=reply_markup)
                    else:
                        await msg.edit_reply_markup(reply_markup=reply_markup)
                except Exception:
                    pass


async def handle_pos_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed help for add position"""
    await show_add_position_menu(update, context, show_help=True)


async def handle_pos_toggle_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between strategy types (0=Spot, 1=Curve, 2=BidAsk)"""
    params = context.user_data.get("add_position_params", {})
    current_strategy = params.get("strategy_type", "0")

    # Cycle through strategies: 0 -> 1 -> 2 -> 0
    if current_strategy == "0":
        params["strategy_type"] = "1"
    elif current_strategy == "1":
        params["strategy_type"] = "2"
    else:
        params["strategy_type"] = "0"

    await show_add_position_menu(update, context)


async def handle_pos_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh pool info and token balances by clearing cache"""
    from ._shared import clear_cache

    # Get current pool info to build cache keys
    params = context.user_data.get("add_position_params", {})
    selected_pool = context.user_data.get("selected_pool", {})

    pool_address = params.get("pool_address") or selected_pool.get("pool_address", "")
    connector = params.get("connector") or selected_pool.get("connector", "meteora")
    network = params.get("network", "solana-mainnet-beta")
    base_token = selected_pool.get("base_token", "")
    quote_token = selected_pool.get("quote_token", "")

    # Clear specific cache keys
    pool_cache_key = f"pool_info_{connector}_{pool_address}"
    balance_cache_key = f"token_balances_{network}_{base_token}_{quote_token}"

    clear_cache(context.user_data, pool_cache_key)
    clear_cache(context.user_data, balance_cache_key)

    # Also clear stored pool info to force refresh
    context.user_data.pop("selected_pool_info", None)

    # Refetch pool info
    if pool_address:
        client = await get_gateway_client()
        pool_info = await _fetch_pool_info(client, pool_address, connector)
        set_cached(context.user_data, pool_cache_key, pool_info)
        context.user_data["selected_pool_info"] = pool_info

    await update.callback_query.answer("Refreshed!")
    await show_add_position_menu(update, context)


async def handle_pos_use_max_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-fill the default range (41 bins = 20 each side) based on current price and bin step"""
    selected_pool = context.user_data.get("selected_pool", {})
    pool_info = context.user_data.get("selected_pool_info", {})

    current_price = pool_info.get('price') or selected_pool.get('current_price')
    bin_step = selected_pool.get('bin_step') or pool_info.get('bin_step')

    if not current_price or not bin_step:
        await update.callback_query.answer("No pool info available. Select a pool first.")
        return

    try:
        suggested_lower, suggested_upper = _calculate_max_range(
            float(current_price),
            int(bin_step)
        )

        if suggested_lower and suggested_upper:
            params = context.user_data.get("add_position_params", {})
            params["lower_price"] = f"{suggested_lower:.6f}"
            params["upper_price"] = f"{suggested_upper:.6f}"

            await update.callback_query.answer("Default range (20 bins each side) applied!")
            await show_add_position_menu(update, context)
        else:
            await update.callback_query.answer("Could not calculate range")
    except Exception as e:
        logger.error(f"Error calculating max range: {e}")
        await update.callback_query.answer(f"Error: {str(e)[:50]}")


# ============================================
# ADD POSITION - PARAMETER HANDLERS
# ============================================

async def handle_pos_set_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input connector for position"""
    help_text = (
        r"📝 *Set Connector*" + "\n\n"
        r"Enter the CLMM connector name:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`meteora` \- Solana CLMM" + "\n"
        r"`raydium` \- Solana CLMM"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:add_position")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pos_set_connector"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_pos_set_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input network for position"""
    help_text = (
        r"📝 *Set Network*" + "\n\n"
        r"Enter the network name:" + "\n\n"
        r"*Example:*" + "\n"
        r"`solana\-mainnet\-beta`"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:add_position")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pos_set_network"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_pos_set_pool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input pool address"""
    help_text = (
        r"📝 *Set Pool Address*" + "\n\n"
        r"Enter the pool address:" + "\n\n"
        r"*Tip:* Use `/dex_trading` → List Pools to find pool addresses"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:add_position")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pos_set_pool"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_pos_set_lower(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input lower price"""
    help_text = (
        r"📝 *Set Lower Price*" + "\n\n"
        r"Enter the lower price bound:" + "\n\n"
        r"*Example:*" + "\n"
        r"`0\.70` \- Lower price bound"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:add_position")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pos_set_lower"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_pos_set_upper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input upper price"""
    help_text = (
        r"📝 *Set Upper Price*" + "\n\n"
        r"Enter the upper price bound:" + "\n\n"
        r"*Example:*" + "\n"
        r"`0\.85` \- Upper price bound"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:add_position")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pos_set_upper"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_pos_set_base(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input base amount"""
    # Get balance for display
    balances = context.user_data.get("token_balances", {})
    base_bal = balances.get("base_balance", 0)
    bal_info = f"_Balance: {_format_number(base_bal)}_\n\n" if base_bal > 0 else ""

    help_text = (
        r"📝 *Set Base Token Amount*" + "\n\n" +
        bal_info +
        r"Enter the amount of base token:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`10%` \- 10% of your balance" + "\n"
        r"`100` \- Exact 100 tokens" + "\n"
        r"`0\.5` \- Exact 0\.5 tokens"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:add_position_from_pool")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pos_set_base"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_pos_set_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to input quote amount"""
    # Get balance for display
    balances = context.user_data.get("token_balances", {})
    quote_bal = balances.get("quote_balance", 0)
    bal_info = f"_Balance: {_format_number(quote_bal)}_\n\n" if quote_bal > 0 else ""

    help_text = (
        r"📝 *Set Quote Token Amount*" + "\n\n" +
        bal_info +
        r"Enter the amount of quote token:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`10%` \- 10% of your balance" + "\n"
        r"`50` \- Exact 50 tokens" + "\n"
        r"`0\.5` \- Exact 0\.5 tokens"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="dex:add_position_from_pool")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["dex_state"] = "pos_set_quote"

    await update.callback_query.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


def _parse_amount(amount_str: str, balance: float) -> Decimal:
    """Parse amount string - supports percentage (10%) or absolute values

    Args:
        amount_str: Amount string like "10%", "100", "0.5"
        balance: Wallet balance for percentage calculation

    Returns:
        Decimal amount
    """
    if not amount_str:
        return None

    amount_str = amount_str.strip()

    # Check if it's a percentage
    if amount_str.endswith('%'):
        try:
            pct = float(amount_str[:-1])
            return Decimal(str(balance * pct / 100))
        except (ValueError, TypeError):
            return None

    # Otherwise it's an absolute amount
    try:
        return Decimal(amount_str)
    except (ValueError, TypeError):
        return None


async def handle_pos_add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute adding the position"""
    try:
        params = context.user_data.get("add_position_params", {})

        connector = params.get("connector")
        network = params.get("network")
        pool_address = params.get("pool_address")
        lower_price = params.get("lower_price")
        upper_price = params.get("upper_price")
        amount_base_str = params.get("amount_base")
        amount_quote_str = params.get("amount_quote")
        strategy_type = int(params.get("strategy_type", "0"))

        # Validate required parameters
        if not all([connector, network, pool_address, lower_price, upper_price]):
            raise ValueError("Missing required parameters (connector, network, pool, prices)")

        if not amount_base_str and not amount_quote_str:
            raise ValueError("Need at least one amount (base or quote)")

        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        # Get token balances for percentage calculation
        balances = context.user_data.get("token_balances", {})
        base_balance = balances.get("base_balance", 0)
        quote_balance = balances.get("quote_balance", 0)

        # Parse amounts (handles both % and absolute values)
        amount_base = _parse_amount(amount_base_str, base_balance) if amount_base_str else None
        amount_quote = _parse_amount(amount_quote_str, quote_balance) if amount_quote_str else None

        # Validate we have at least one valid amount
        if amount_base is None and amount_quote is None:
            raise ValueError("Invalid amounts. Use '10%' for percentage or '100' for absolute value.")

        # Check if using percentage with no balance
        if amount_base_str and amount_base_str.endswith('%') and base_balance <= 0:
            raise ValueError(f"Cannot use percentage - no base token balance found")
        if amount_quote_str and amount_quote_str.endswith('%') and quote_balance <= 0:
            raise ValueError(f"Cannot use percentage - no quote token balance found")

        # Build extra_params for strategy type
        extra_params = {"strategyType": strategy_type}

        result = await client.gateway_clmm.open_position(
            connector=connector,
            network=network,
            pool_address=pool_address,
            lower_price=Decimal(lower_price),
            upper_price=Decimal(upper_price),
            base_token_amount=amount_base,
            quote_token_amount=amount_quote,
            extra_params=extra_params,
        )

        if result is None:
            raise ValueError("Gateway returned no response.")

        # Clear the positions cache to force fresh fetch
        context.user_data.pop("positions_cache", None)
        context.user_data.pop("all_positions", None)

        # Save pool params
        set_dex_last_pool(context.user_data, {
            "connector": connector,
            "network": network,
            "pool_address": pool_address
        })

        # Strategy name for display
        strategy_names = {0: 'Spot', 1: 'Curve', 2: 'BidAsk'}
        strategy_name = strategy_names.get(strategy_type, 'Spot')

        pos_info = escape_markdown_v2(
            f"✅ Position Added!\n\n"
            f"Connector: {connector}\n"
            f"Pool: {pool_address[:16]}...\n"
            f"Range: [{lower_price[:8]} - {upper_price[:8]}]\n"
            f"Strategy: {strategy_name}\n"
        )

        if amount_base:
            pos_info += escape_markdown_v2(f"Base: {float(amount_base):.6f}\n")
        if amount_quote:
            pos_info += escape_markdown_v2(f"Quote: {float(amount_quote):.6f}\n")

        if isinstance(result, dict):
            if 'tx_hash' in result:
                pos_info += escape_markdown_v2(f"\nTx: {result['tx_hash'][:16]}...")
            if 'position_address' in result:
                pos_info += escape_markdown_v2(f"\nPosition: {result['position_address'][:16]}...")

        keyboard = [[InlineKeyboardButton("« Back to DEX Trading", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.reply_text(
            pos_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error adding position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to add position: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# TEXT INPUT PROCESSORS FOR POSITION
# ============================================

async def process_add_position(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process add position from text input"""
    try:
        parts = user_input.split()

        if len(parts) < 5:
            raise ValueError("Need: pool_address lower_price upper_price amount_base amount_quote")

        params = context.user_data.get("add_position_params", {})
        params["pool_address"] = parts[0]
        params["lower_price"] = parts[1]
        params["upper_price"] = parts[2]
        params["amount_base"] = parts[3]
        params["amount_quote"] = parts[4]

        # Now execute
        connector = params.get("connector", "meteora")
        network = params.get("network", "solana-mainnet-beta")

        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        result = await client.gateway_clmm.add_liquidity(
            connector=connector,
            network=network,
            pool_address=params["pool_address"],
            lower_price=Decimal(params["lower_price"]),
            upper_price=Decimal(params["upper_price"]),
            amount_base=Decimal(params["amount_base"]) if params["amount_base"] else None,
            amount_quote=Decimal(params["amount_quote"]) if params["amount_quote"] else None,
        )

        if result is None:
            raise ValueError("Gateway returned no response.")

        set_dex_last_pool(context.user_data, {
            "connector": connector,
            "network": network,
            "pool_address": params["pool_address"]
        })

        pos_info = escape_markdown_v2(
            f"✅ Position Added!\n\n"
            f"Pool: {params['pool_address'][:16]}...\n"
            f"Range: [{params['lower_price']} - {params['upper_price']}]\n"
            f"Base: {params['amount_base']}\n"
            f"Quote: {params['amount_quote']}\n"
        )

        if isinstance(result, dict) and 'tx_hash' in result:
            pos_info += escape_markdown_v2(f"\nTx: {result['tx_hash'][:16]}...")

        keyboard = [[InlineKeyboardButton("« Back to DEX Trading", callback_data="dex:main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            pos_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error adding position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to add position: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


async def process_pos_set_connector(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process position set connector input"""
    params = context.user_data.get("add_position_params", {})
    params["connector"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Connector set to: {user_input}")
    await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)


async def process_pos_set_network(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process position set network input"""
    params = context.user_data.get("add_position_params", {})
    params["network"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Network set to: {user_input}")
    await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)


async def process_pos_set_pool(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process position set pool input"""
    params = context.user_data.get("add_position_params", {})
    params["pool_address"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Pool set to: {user_input[:16]}...")
    await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)


async def process_pos_set_lower(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process position set lower price input"""
    params = context.user_data.get("add_position_params", {})
    params["lower_price"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Lower price set to: {user_input}")
    await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)


async def process_pos_set_upper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process position set upper price input"""
    params = context.user_data.get("add_position_params", {})
    params["upper_price"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Upper price set to: {user_input}")
    await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)


async def process_pos_set_base(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process position set base amount input"""
    params = context.user_data.get("add_position_params", {})
    params["amount_base"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Base amount set to: {user_input}")
    await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)


async def process_pos_set_quote(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process position set quote amount input"""
    params = context.user_data.get("add_position_params", {})
    params["amount_quote"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Quote amount set to: {user_input}")
    await update.message.reply_text(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)
