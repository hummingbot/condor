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

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from handlers.config.user_preferences import set_dex_last_pool, get_dex_last_pool
from ._shared import get_gateway_client

logger = logging.getLogger(__name__)


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

async def handle_pool_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle CLMM pool list"""
    help_text = (
        r"📋 *List CLMM Pools*" + "\n\n"
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

    Shows: #, Pair, TVL, Fee, APR, Vol, Bin

    Args:
        pools: List of pool data dictionaries

    Returns:
        Formatted table string (not escaped)
    """
    if not pools:
        return "No pools found"

    lines = []

    # Header - balanced for mobile (~45 chars)
    lines.append("```")
    lines.append(f"{'#':>2} {'Pair':<10} {'TVL':>5} {'Fee':>4} {'APR %':>6} {'Vol':>4} {'Bin':>3}")
    lines.append("─" * 45)

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

        lines.append(f"{idx:>2} {pair:<10} {tvl:>5} {fee_str:>4} {apr_str:>6} {vol_24h:>4} {bin_step:>3}")

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


def _build_pool_selection_keyboard(pools: list, search_term: str = None) -> InlineKeyboardMarkup:
    """Build keyboard with numbered buttons for pool selection"""
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

            # Cache pools for selection (with search term for back navigation)
            context.user_data["pool_list_cache"] = display_pools
            context.user_data["pool_list_search_term"] = search_term
            context.user_data["pool_list_limit"] = display_limit

            total = result.get("total", len(pools))
            search_info = f" for '{search_term}'" if search_term else ""

            header = rf"📋 *CLMM Pools*{escape_markdown_v2(search_info)} \({len(display_pools)} of {total}\)" + "\n\n"

            table = _format_pool_table(display_pools)
            message = header + table + "\n\n_Select pool number:_"

            # Build keyboard with numbered buttons
            reply_markup = _build_pool_selection_keyboard(display_pools, search_term)

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

    # Fetch additional pool info with bins
    client = await get_gateway_client()
    pool_info = await _fetch_pool_info(client, pool_address, connector)

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


async def handle_pool_list_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back button to return to pool list"""
    cached_pools = context.user_data.get("pool_list_cache", [])
    search_term = context.user_data.get("pool_list_search_term")

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

    reply_markup = _build_pool_selection_keyboard(cached_pools, search_term)

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

def _format_position_detail(pos: dict) -> str:
    """Format a single position for the manage view"""
    pair = pos.get('trading_pair', pos.get('pool_name', 'Unknown'))
    connector = pos.get('connector', 'unknown')
    pool_address = pos.get('pool_address', '')

    # Get amounts
    amount_a = pos.get('amount_a', pos.get('token_a_amount', 0))
    amount_b = pos.get('amount_b', pos.get('token_b_amount', 0))
    token_a = pos.get('token_a', pos.get('base_token', ''))
    token_b = pos.get('token_b', pos.get('quote_token', ''))

    # Get price range
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))

    # Get fees if available
    unclaimed_a = pos.get('unclaimed_fee_a', pos.get('fees_a', 0))
    unclaimed_b = pos.get('unclaimed_fee_b', pos.get('fees_b', 0))

    lines = []
    lines.append(f"🏊 {pair} ({connector})")

    if pool_address:
        lines.append(f"   Pool: {pool_address[:12]}...")

    if lower and upper:
        lines.append(f"   Range: [{lower} - {upper}]")

    if amount_a or amount_b:
        lines.append(f"   Amounts: {amount_a} {token_a} / {amount_b} {token_b}")

    if unclaimed_a or unclaimed_b:
        lines.append(f"   Fees: {unclaimed_a} {token_a} / {unclaimed_b} {token_b}")

    return "\n".join(lines)


async def handle_manage_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display manage positions menu with all active LP positions"""
    try:
        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        # Fetch all open positions
        result = await client.gateway_clmm.search_positions(
            limit=50,
            offset=0,
            status="OPEN"
        )

        positions = result.get("data", []) if result else []

        # Build message
        if positions:
            header = rf"📍 *Manage LP Positions* \({len(positions)} active\)" + "\n\n"

            for i, pos in enumerate(positions[:10]):  # Show top 10
                pos_detail = _format_position_detail(pos)
                header += escape_markdown_v2(pos_detail) + "\n\n"

            if len(positions) > 10:
                header += escape_markdown_v2(f"... and {len(positions) - 10} more positions")
        else:
            header = r"📍 *Manage LP Positions*" + "\n\n"
            header += r"_No active positions found\._" + "\n\n"
            header += r"Use ➕ *New Position* to add liquidity to a pool\."

        # Build keyboard with position actions
        keyboard = []

        # Add buttons for each position (max 5 for manageability)
        for i, pos in enumerate(positions[:5]):
            pool_addr = pos.get('pool_address', '')
            pair = pos.get('trading_pair', pos.get('pool_name', 'Unknown'))[:12]
            pos_id = pos.get('position_id', pos.get('nft_id', i))

            # Store position info in context for later use
            if "positions_cache" not in context.user_data:
                context.user_data["positions_cache"] = {}
            context.user_data["positions_cache"][str(i)] = pos

            keyboard.append([
                InlineKeyboardButton(f"📍 {pair}", callback_data=f"dex:pos_view:{i}"),
                InlineKeyboardButton("💰 Fees", callback_data=f"dex:pos_collect:{i}"),
                InlineKeyboardButton("❌ Close", callback_data=f"dex:pos_close:{i}")
            ])

        # Add new position and back buttons
        keyboard.append([
            InlineKeyboardButton("➕ New Position", callback_data="dex:add_position"),
            InlineKeyboardButton("« Back", callback_data="dex:main_menu")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.message.edit_text(
            header,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

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

        # Format detailed view
        detail = _format_position_detail(pos)
        message = r"📍 *Position Details*" + "\n\n"
        message += escape_markdown_v2(detail)

        keyboard = [
            [
                InlineKeyboardButton("💰 Collect Fees", callback_data=f"dex:pos_collect:{pos_index}"),
                InlineKeyboardButton("❌ Close Position", callback_data=f"dex:pos_close:{pos_index}")
            ],
            [InlineKeyboardButton("« Back", callback_data="dex:manage_positions")]
        ]
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
    """Collect fees from a position"""
    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await update.callback_query.answer("Position not found. Please refresh.")
            return

        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        # Get position details
        connector = pos.get('connector', 'meteora')
        network = pos.get('network', 'solana-mainnet-beta')
        pool_address = pos.get('pool_address', '')
        position_address = pos.get('position_address', pos.get('nft_id', ''))

        await update.callback_query.answer("Collecting fees...")

        # Call collect fees
        result = await client.gateway_clmm.collect_fees(
            connector=connector,
            network=network,
            pool_address=pool_address,
            position_address=position_address
        )

        if result:
            pair = pos.get('trading_pair', 'Unknown')
            success_msg = escape_markdown_v2(f"✅ Fees collected from {pair}!")

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
            await update.callback_query.answer("No fees to collect")

    except Exception as e:
        logger.error(f"Error collecting fees: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to collect fees: {str(e)}")
        await update.callback_query.message.reply_text(error_message, parse_mode="MarkdownV2")


async def handle_pos_close_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, pos_index: str) -> None:
    """Show confirmation for closing a position"""
    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await update.callback_query.answer("Position not found. Please refresh.")
            return

        pair = pos.get('trading_pair', pos.get('pool_name', 'Unknown'))
        detail = _format_position_detail(pos)

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

        client = await get_gateway_client()

        if not hasattr(client, 'gateway_clmm'):
            raise ValueError("Gateway CLMM not available")

        # Get position details
        connector = pos.get('connector', 'meteora')
        network = pos.get('network', 'solana-mainnet-beta')
        pool_address = pos.get('pool_address', '')
        position_address = pos.get('position_address', pos.get('nft_id', ''))

        await update.callback_query.answer("Closing position...")

        # Call remove liquidity (100% = close position)
        result = await client.gateway_clmm.remove_liquidity(
            connector=connector,
            network=network,
            pool_address=pool_address,
            position_address=position_address,
            percentage=Decimal("100")  # Remove 100% = close
        )

        if result:
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


def _calculate_max_range(current_price: float, bin_step: int, max_bins: int = 69) -> tuple:
    """Calculate max price range for 69 bins (Meteora limit)

    Args:
        current_price: Current pool price
        bin_step: Pool bin step in basis points (e.g., 100 = 1%)
        max_bins: Maximum number of bins (default 69 for Meteora)

    Returns:
        Tuple of (lower_price, upper_price)
    """
    if not current_price or not bin_step:
        return None, None

    try:
        # Bin step is in basis points (100 = 1% = 0.01)
        step_multiplier = 1 + (bin_step / 10000)

        # Calculate range: 34 bins below, 34 bins above (+ active bin = 69)
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
                          current_price: float, width: int = 20) -> str:
    """Generate improved ASCII visualization of liquidity with selected range markers

    Args:
        bins: List of bin data with price and liquidity
        lower_price: Selected lower bound
        upper_price: Selected upper bound
        current_price: Current pool price
        width: Width of the bar chart

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

    # Sort and filter to bins with liquidity
    bin_data.sort(key=lambda x: x['price'])
    active_bins = [b for b in bin_data if b['liq'] > 0]

    if not active_bins:
        return ""

    # Get price range
    min_price = min(b['price'] for b in active_bins)
    max_price = max(b['price'] for b in active_bins)
    price_range = max_price - min_price

    if price_range <= 0:
        return ""

    # Find max liquidity for scaling
    max_liq = max(b['liq'] for b in active_bins)

    # Build histogram
    lines = []

    # Sample to ~8 price points for compact display
    if len(active_bins) > 8:
        step = len(active_bins) // 8
        sampled = active_bins[::step][:8]
    else:
        sampled = active_bins

    for b in sampled:
        price = b['price']
        liq = b['liq']

        # Calculate bar length
        bar_len = int((liq / max_liq) * width) if max_liq > 0 else 0

        # Determine if in range and marker
        in_range = lower_price <= price <= upper_price if lower_price and upper_price else False
        is_current = abs(price - current_price) / current_price < 0.03 if current_price else False
        near_lower = lower_price and abs(price - lower_price) / max(lower_price, 0.0001) < 0.06
        near_upper = upper_price and abs(price - upper_price) / max(upper_price, 0.0001) < 0.06

        # Build bar with different characters for in/out of range
        bar = "█" * bar_len if in_range else "░" * bar_len

        # Marker column
        if is_current:
            marker = "◄"
        elif near_lower:
            marker = "L"
        elif near_upper:
            marker = "U"
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

    # Get current price and bin step for range calculation
    current_price = pool_info.get('price') or selected_pool.get('current_price')
    bin_step = selected_pool.get('bin_step') or pool_info.get('bin_step')
    bins = pool_info.get('bins', [])
    pair = selected_pool.get('trading_pair', 'Pool')
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
        except (ValueError, TypeError):
            pass

    # Get current range values for visualization
    try:
        lower_val = float(params.get('lower_price', 0)) if params.get('lower_price') else None
        upper_val = float(params.get('upper_price', 0)) if params.get('upper_price') else None
        current_val = float(current_price) if current_price else None
    except (ValueError, TypeError):
        lower_val, upper_val, current_val = None, None, None

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

        help_text += r"• `█` \- In your selected range" + "\n"
        help_text += r"• `░` \- Outside your range" + "\n"
        help_text += r"• `◄` \- Current price" + "\n"
        help_text += r"• `L` \- Lower bound" + "\n"
        help_text += r"• `U` \- Upper bound" + "\n"

    else:
        # ========== MAIN VIEW ==========
        help_text = r"➕ *Add CLMM Position*" + "\n\n"

        # Pool info header
        help_text += f"🏊 *Pool:* `{escape_markdown_v2(pair)}`\n"
        if current_price:
            help_text += f"💱 *Price:* `{escape_markdown_v2(str(current_price)[:10])}`\n"
        if bin_step:
            help_text += f"📊 *Bin Step:* `{escape_markdown_v2(str(bin_step))}` _\\(max 69 bins\\)_\n"

        # Fetch and display token balances
        try:
            client = await get_gateway_client()
            balances = await _fetch_token_balances(client, network, base_token, quote_token)

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

        # Add ASCII range visualization if we have bins
        if bins and lower_val and upper_val:
            ascii_lines = _generate_range_ascii(bins, lower_val, upper_val, current_val)
            if ascii_lines:
                help_text += "\n```\n" + ascii_lines + "\n```\n"
                help_text += r"_█\=in range, ░\=out, ◄\=current, L/U\=bounds_" + "\n"

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
        help_button,
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
        msg = update.callback_query.message
        try:
            if msg.photo:
                # It's a photo, edit caption
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
                # Delete and send new with chart
                chat = msg.chat
                try:
                    await msg.delete()
                except Exception:
                    pass
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
                    except Exception:
                        await chat.send_message(text=help_text, parse_mode="MarkdownV2", reply_markup=reply_markup)
                else:
                    await chat.send_message(text=help_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


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


async def handle_pos_use_max_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-fill the max range (69 bins) based on current price and bin step"""
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

            await update.callback_query.answer("Max range (69 bins) applied!")
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
