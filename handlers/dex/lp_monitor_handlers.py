"""
LP Monitor Alert Handlers

Handles user interactions with LP monitor out-of-range alerts:
- Navigation between positions
- Position detail views
- Fee collection
- Position rebalancing (close + reopen)
- Out-of-range position filtering
"""

import logging
from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, resolve_token_symbol

logger = logging.getLogger(__name__)


# ============================================
# POSITION FORMATTING HELPERS
# ============================================


def _format_price(value: float | str, decimals: int | None = None) -> str:
    """Format a price value with appropriate decimal places."""
    try:
        float_val = float(value)
        if decimals is None:
            decimals = 2 if float_val >= 1 else (6 if float_val >= 0.001 else 8)
        return f"{float_val:.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


def _get_position_tokens(pos: dict, token_cache: dict) -> tuple[str, str, str]:
    """Extract and resolve token symbols from position data."""
    base_token = pos.get("base_token", pos.get("token_a", ""))
    quote_token = pos.get("quote_token", pos.get("token_b", ""))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"
    return base_symbol, quote_symbol, pair


def _get_price(symbol: str, prices: dict, default: float = 0) -> float:
    if symbol in prices:
        return prices[symbol]
    for k, v in prices.items():
        if k.lower() == symbol.lower():
            return v
    variants = {"sol": "wsol", "wsol": "sol", "eth": "weth", "weth": "eth"}
    alt = variants.get(symbol.lower())
    if alt:
        for k, v in prices.items():
            if k.lower() == alt:
                return v
    return default


def format_position_detail_view(
    pos: dict,
    token_cache: dict,
    token_prices: dict,
    index: int,
    total: int,
    instance_id: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Format a single position detail view with navigation + action buttons."""
    base_sym, quote_sym, pair = _get_position_tokens(pos, token_cache)
    connector = pos.get("connector", "unknown")
    fee = pos.get("fee_tier", pos.get("fee", ""))
    fee_str = f" {fee}%" if fee else ""

    in_range = pos.get("in_range", "")
    status = (
        "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else "⚪"
    )

    lower = float(pos.get("lower_price", pos.get("price_lower", 0)) or 0)
    upper = float(pos.get("upper_price", pos.get("price_upper", 0)) or 0)
    current = float(pos.get("current_price", 0) or 0)
    width_pct = ((upper - lower) / lower * 100) if lower > 0 else 0

    quote_price = _get_price(quote_sym, token_prices, 1.0)
    base_price = _get_price(base_sym, token_prices, 0)

    pnl_summary = pos.get("pnl_summary", {})
    pnl_usd = float(pnl_summary.get("total_pnl_quote", 0) or 0) * quote_price
    value_usd = float(pnl_summary.get("current_lp_value_quote", 0) or 0) * quote_price

    base_fee = float(pos.get("base_fee_pending", 0) or 0)
    quote_fee = float(pos.get("quote_fee_pending", 0) or 0)
    fees_usd = (base_fee * base_price) + (quote_fee * quote_price)

    pnl_sign = "\\+" if pnl_usd >= 0 else "\\-"

    lines = [
        f"📍 *Position {index + 1}/{total}*",
        "",
        f"*{escape_markdown_v2(pair)}{escape_markdown_v2(fee_str)}* \\({escape_markdown_v2(connector.capitalize())}\\)",
        f"{status} \\[{escape_markdown_v2(_format_price(lower))} \\- {escape_markdown_v2(_format_price(upper))}\\] \\({escape_markdown_v2(f'{width_pct:.0f}')}% width\\)",
        f"Price: {escape_markdown_v2(_format_price(current))}",
        f"💰 ${escape_markdown_v2(f'{value_usd:.2f}')} \\| PnL: {pnl_sign}${escape_markdown_v2(f'{abs(pnl_usd):.2f}')} \\| 🎁 ${escape_markdown_v2(f'{fees_usd:.2f}')}",
    ]
    text = "\n".join(lines)

    cache_key = f"lpm_{instance_id}_{index}"
    keyboard = []

    if total > 1:
        nav_row = []
        if index > 0:
            nav_row.append(
                InlineKeyboardButton(
                    "◀️ Prev",
                    callback_data=f"dex:lpm_detail:{instance_id}:{index - 1}",
                )
            )
        nav_row.append(
            InlineKeyboardButton(f"{index + 1}/{total}", callback_data="dex:lpm_noop")
        )
        if index < total - 1:
            nav_row.append(
                InlineKeyboardButton(
                    "Next ▶️",
                    callback_data=f"dex:lpm_detail:{instance_id}:{index + 1}",
                )
            )
        keyboard.append(nav_row)

    keyboard.append(
        [
            InlineKeyboardButton(
                "💰 Collect Fees", callback_data=f"dex:lpm_collect:{cache_key}"
            ),
            InlineKeyboardButton(
                "❌ Close", callback_data=f"dex:pos_close:{cache_key}"
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "🔄 Rebalance", callback_data=f"dex:lpm_rebalance:{cache_key}"
            ),
            InlineKeyboardButton(
                "✅ Dismiss", callback_data=f"dex:lpm_dismiss:{instance_id}"
            ),
        ]
    )

    return text, InlineKeyboardMarkup(keyboard)


def _get_positions_for_instance(positions_cache: dict, instance_id: str) -> list[dict]:
    """Get all cached positions for a given LP monitor instance."""
    positions = []
    i = 0
    while True:
        cache_key = f"lpm_{instance_id}_{i}"
        if cache_key in positions_cache:
            positions.append(positions_cache[cache_key])
            i += 1
        else:
            break
    return positions


# ============================================
# NAVIGATION HANDLERS
# ============================================


async def handle_lpm_navigation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, instance_id: str, new_index: int
) -> None:
    """Handle navigation in LP monitor alert message."""
    query = update.callback_query
    positions_cache = context.user_data.get("positions_cache", {})
    token_cache = context.user_data.get("token_cache", {})

    positions = _get_positions_for_instance(positions_cache, instance_id)
    if not positions:
        await query.answer("Positions not found")
        return

    # Clamp index to valid range
    new_index = max(0, min(new_index, len(positions) - 1))
    pos = positions[new_index]

    # Get token info
    base_symbol, quote_symbol, pair = _get_position_tokens(pos, token_cache)
    connector = pos.get("connector", "unknown")

    # Price info
    lower = pos.get("lower_price", pos.get("price_lower", ""))
    upper = pos.get("upper_price", pos.get("price_upper", ""))
    current = pos.get("current_price", "")

    # Format range
    range_str = ""
    if lower and upper:
        try:
            lower_f = float(lower)
            upper_f = float(upper)
            decimals = 2 if lower_f >= 1 else (6 if lower_f >= 0.001 else 8)
            range_str = f"Range: {lower_f:.{decimals}f} - {upper_f:.{decimals}f}"
        except (ValueError, TypeError):
            range_str = f"Range: {lower} - {upper}"

    # Format current price and direction
    current_str = ""
    direction = ""
    if current:
        try:
            current_f = float(current)
            lower_f = float(lower) if lower else 0
            upper_f = float(upper) if upper else 0
            decimals = 2 if current_f >= 1 else (6 if current_f >= 0.001 else 8)
            current_str = f"Current: {current_f:.{decimals}f}"
            if current_f < lower_f:
                direction = "▼ Below range"
            elif current_f > upper_f:
                direction = "▲ Above range"
        except (ValueError, TypeError):
            current_str = f"Current: {current}"

    # Format value
    pnl_summary = pos.get("pnl_summary", {})
    value = pnl_summary.get("current_lp_value_quote", 0)
    value_str = ""
    if value:
        try:
            value_str = f"Value: {float(value):.2f} {quote_symbol}"
        except (ValueError, TypeError):
            pass

    # Build message
    total = len(positions)
    header = (
        f"🚨 *Out of Range* \\({new_index + 1}/{total}\\)"
        if total > 1
        else "🚨 *Position Out of Range*"
    )
    lines = [
        header,
        "",
        f"*{escape_markdown_v2(pair)}* \\({escape_markdown_v2(connector)}\\)",
    ]

    if direction:
        lines.append(f"_{escape_markdown_v2(direction)}_")
    if range_str:
        lines.append(escape_markdown_v2(range_str))
    if current_str:
        lines.append(escape_markdown_v2(current_str))
    if value_str:
        lines.append(escape_markdown_v2(value_str))

    text = "\n".join(lines)

    # Build keyboard
    cache_key = f"lpm_{instance_id}_{new_index}"
    keyboard = []

    if total > 1:
        nav_row = []
        if new_index > 0:
            nav_row.append(
                InlineKeyboardButton(
                    "◀️ Prev",
                    callback_data=f"dex:lpm_nav:{instance_id}:{new_index - 1}",
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                f"{new_index + 1}/{total}", callback_data="dex:lpm_noop"
            )
        )
        if new_index < total - 1:
            nav_row.append(
                InlineKeyboardButton(
                    "Next ▶️",
                    callback_data=f"dex:lpm_nav:{instance_id}:{new_index + 1}",
                )
            )
        keyboard.append(nav_row)

    keyboard.append(
        [
            InlineKeyboardButton(
                "❌ Close", callback_data=f"dex:pos_close:{cache_key}"
            ),
            InlineKeyboardButton("⏭ Skip", callback_data=f"dex:lpm_skip:{cache_key}"),
            InlineKeyboardButton(
                "✅ Dismiss", callback_data=f"dex:lpm_dismiss:{instance_id}"
            ),
        ]
    )

    try:
        await query.message.edit_text(
            text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning(f"Failed to update LPM navigation: {e}")


async def handle_lpm_oor_navigation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, instance_id: str, index: int
) -> None:
    """Navigate only out-of-range positions."""
    query = update.callback_query
    positions_cache = context.user_data.get("positions_cache", {})
    token_cache = context.user_data.get("token_cache", {})
    token_prices = context.user_data.get("token_prices", {})

    # Find all positions and filter to out-of-range only
    all_positions = []
    i = 0
    while True:
        cache_key = f"lpm_{instance_id}_{i}"
        if cache_key in positions_cache:
            all_positions.append((i, positions_cache[cache_key]))
            i += 1
        else:
            break

    oor_positions = [
        (orig_idx, pos)
        for orig_idx, pos in all_positions
        if pos.get("in_range") == "OUT_OF_RANGE"
    ]

    if not oor_positions:
        await query.answer("No out-of-range positions")
        return

    # Clamp index
    index = max(0, min(index, len(oor_positions) - 1))
    orig_idx, pos = oor_positions[index]

    text, _ = format_position_detail_view(
        pos, token_cache, token_prices, index, len(oor_positions), instance_id
    )

    # Custom keyboard for OOR navigation
    cache_key = f"lpm_{instance_id}_{orig_idx}"
    keyboard = []

    nav_row = []
    if index > 0:
        nav_row.append(
            InlineKeyboardButton(
                "◀️ Prev", callback_data=f"dex:lpm_oor:{instance_id}:{index - 1}"
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            f"⚠️ {index + 1}/{len(oor_positions)}", callback_data="dex:noop"
        )
    )
    if index < len(oor_positions) - 1:
        nav_row.append(
            InlineKeyboardButton(
                "Next ▶️", callback_data=f"dex:lpm_oor:{instance_id}:{index + 1}"
            )
        )
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append(
        [
            InlineKeyboardButton(
                "💰 Collect Fees", callback_data=f"dex:lpm_collect:{cache_key}"
            ),
            InlineKeyboardButton(
                "❌ Close", callback_data=f"dex:pos_close:{cache_key}"
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "🔄 Rebalance", callback_data=f"dex:lpm_rebalance:{cache_key}"
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "« Back to List", callback_data=f"dex:lpm_dismiss:{instance_id}"
            ),
        ]
    )

    try:
        await query.message.edit_text(
            text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning(f"Failed to update OOR navigation: {e}")


# ============================================
# DETAIL VIEW HANDLER
# ============================================


async def handle_lpm_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, instance_id: str, index: int
) -> None:
    """Handle position detail view with actions."""
    query = update.callback_query
    positions_cache = context.user_data.get("positions_cache", {})
    token_cache = context.user_data.get("token_cache", {})
    token_prices = context.user_data.get("token_prices", {})

    positions = _get_positions_for_instance(positions_cache, instance_id)
    if not positions:
        await query.answer("Positions not found")
        return

    # Clamp index
    index = max(0, min(index, len(positions) - 1))
    pos = positions[index]

    text, reply_markup = format_position_detail_view(
        pos, token_cache, token_prices, index, len(positions), instance_id
    )

    try:
        await query.message.edit_text(
            text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning(f"Failed to update position detail: {e}")


# ============================================
# FEE COLLECTION HANDLER
# ============================================


async def handle_lpm_collect_fees(
    update: Update, context: ContextTypes.DEFAULT_TYPE, cache_key: str
) -> None:
    """Collect fees for a position."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    positions_cache = context.user_data.get("positions_cache", {})

    pos = positions_cache.get(cache_key)
    if not pos:
        await query.answer("Position not found")
        return

    await query.answer("Collecting fees...")

    try:
        from config_manager import get_client

        client = await get_client(chat_id, context=context)
        if not client or not hasattr(client, "gateway_clmm"):
            await query.message.reply_text("❌ Gateway not available")
            return

        # Get position details
        position_address = pos.get(
            "position_address", pos.get("nft_id", pos.get("address", ""))
        )
        connector = pos.get("connector", "meteora")
        network = pos.get("network", "solana-mainnet-beta")

        result = await client.gateway_clmm.collect_fees(
            connector=connector, network=network, position_address=position_address
        )

        if result:
            tx_hash = (result.get("tx_hash", "") or "N/A")[:16]
            await query.message.reply_text(
                f"✅ *Fees collected*\nTx: `{escape_markdown_v2(tx_hash)}...`",
                parse_mode="MarkdownV2",
            )
        else:
            await query.message.reply_text("❌ Failed: No response from gateway")

    except Exception as e:
        logger.error(f"Failed to collect fees: {e}")
        await query.message.reply_text(f"❌ Error: {str(e)[:100]}")


# ============================================
# REBALANCE HANDLERS
# ============================================


async def handle_lpm_rebalance(
    update: Update, context: ContextTypes.DEFAULT_TYPE, cache_key: str
) -> None:
    """Start rebalance flow: show confirmation before close + reopen."""
    query = update.callback_query
    positions_cache = context.user_data.get("positions_cache", {})
    token_cache = context.user_data.get("token_cache", {})

    pos = positions_cache.get(cache_key)
    if not pos:
        await query.answer("Position not found")
        return

    # Store position info for rebalance flow
    context.user_data["rebalance_position"] = pos
    context.user_data["rebalance_cache_key"] = cache_key

    # Get position details for confirmation
    _, _, pair = _get_position_tokens(pos, token_cache)
    lower = pos.get("lower_price", pos.get("price_lower", 0))
    upper = pos.get("upper_price", pos.get("price_upper", 0))

    text = (
        f"🔄 *Rebalance Position*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"*{escape_markdown_v2(pair)}*\n\n"
        f"This will:\n"
        f"1\\. Close the current position\n"
        f"2\\. Open a new position with the same range\n"
        f"   \\({escape_markdown_v2(str(lower))} \\- {escape_markdown_v2(str(upper))}\\)\n"
        f"3\\. Use Bid\\-Ask strategy \\(type 2\\)\n\n"
        f"⚠️ *Are you sure?*"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Confirm Rebalance",
                callback_data=f"dex:lpm_rebalance_confirm:{cache_key}",
            ),
            InlineKeyboardButton(
                "❌ Cancel", callback_data=f"dex:lpm_dismiss:{cache_key.split('_')[1]}"
            ),
        ]
    ]

    try:
        await query.message.edit_text(
            text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.warning(f"Failed to show rebalance confirmation: {e}")


async def handle_lpm_rebalance_execute(
    update: Update, context: ContextTypes.DEFAULT_TYPE, cache_key: str
) -> None:
    """Execute the rebalance: close position and open new one with same range."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    positions_cache = context.user_data.get("positions_cache", {})
    token_cache = context.user_data.get("token_cache", {})

    pos = positions_cache.get(cache_key)
    if not pos:
        await query.answer("Position not found")
        return

    await query.answer("Rebalancing position...")

    # Update message to show progress
    await query.message.edit_text(
        "🔄 *Rebalancing\\.\\.\\.*\n\nStep 1/3: Closing position\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        from config_manager import get_client

        client = await get_client(chat_id, context=context)
        if not client or not hasattr(client, "gateway_clmm"):
            await query.message.edit_text("❌ Gateway not available")
            return

        # Get position details
        position_address = pos.get(
            "position_address", pos.get("nft_id", pos.get("address", ""))
        )
        connector = pos.get("connector", "meteora")
        network = pos.get("network", "solana-mainnet-beta")
        pool_address = pos.get("pool_id", pos.get("pool_address", ""))
        lower_price = pos.get("lower_price", pos.get("price_lower", 0))
        upper_price = pos.get("upper_price", pos.get("price_upper", 0))

        # Step 1: Close the position
        close_result = await client.gateway_clmm.close_position(
            connector=connector, network=network, position_address=position_address
        )

        if not close_result:
            await query.message.edit_text(
                "❌ Failed to close position: No response from gateway"
            )
            return

        logger.info(f"Close position result: {close_result}")

        # Extract tx hash from various possible field names
        close_tx = None
        if isinstance(close_result, dict):
            close_tx = (
                close_result.get("tx_hash")
                or close_result.get("txHash")
                or close_result.get("signature")
                or close_result.get("txSignature")
            )
        close_tx_display = (
            f"`{escape_markdown_v2(close_tx[:20])}...`" if close_tx else "_pending_"
        )

        # Update progress
        await query.message.edit_text(
            f"🔄 *Rebalancing\\.\\.\\.*\n\n"
            f"✅ Step 1/3: Position closed\n"
            f"   Tx: {close_tx_display}\n\n"
            f"Step 2/3: Getting withdrawn amounts\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        # Get the withdrawn amounts from the close result
        base_withdrawn = close_result.get(
            "base_amount", close_result.get("amount_base", 0)
        )
        quote_withdrawn = close_result.get(
            "quote_amount", close_result.get("amount_quote", 0)
        )

        # Fallback to original position amounts if not in close result
        if not base_withdrawn:
            base_withdrawn = pos.get("base_token_amount", pos.get("amount_a", 0))
        if not quote_withdrawn:
            quote_withdrawn = pos.get("quote_token_amount", pos.get("amount_b", 0))

        # Update progress
        await query.message.edit_text(
            f"🔄 *Rebalancing\\.\\.\\.*\n\n"
            f"✅ Step 1/3: Position closed\n"
            f"✅ Step 2/3: Amounts ready\n\n"
            f"Step 3/3: Opening new position\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        # Step 3: Open new position with same range using bid-ask strategy (type 2)
        extra_params = {"strategyType": 2}  # Bid-Ask strategy

        open_result = await client.gateway_clmm.open_position(
            connector=connector,
            network=network,
            pool_address=pool_address,
            lower_price=Decimal(str(lower_price)),
            upper_price=Decimal(str(upper_price)),
            base_token_amount=float(base_withdrawn) if base_withdrawn else 0,
            quote_token_amount=float(quote_withdrawn) if quote_withdrawn else 0,
            extra_params=extra_params,
        )

        if not open_result:
            await query.message.edit_text(
                f"⚠️ *Partial Rebalance*\n\n"
                f"✅ Position closed\n"
                f"❌ Failed to open new position: No response from gateway\n\n"
                f"Your funds are in your wallet\\.",
                parse_mode="MarkdownV2",
            )
            return

        logger.info(f"Open position result: {open_result}")

        # Extract tx hash
        open_tx = None
        if isinstance(open_result, dict):
            open_tx = (
                open_result.get("tx_hash")
                or open_result.get("txHash")
                or open_result.get("signature")
                or open_result.get("txSignature")
            )
        open_tx_display = (
            f"`{escape_markdown_v2(open_tx[:20])}...`" if open_tx else "_pending_"
        )

        # Get token symbols for display
        _, quote_symbol, pair = _get_position_tokens(pos, token_cache)

        # Format price range for display
        try:
            lower_f = float(lower_price)
            upper_f = float(upper_price)
            decimals = 2 if lower_f >= 1 else 6 if lower_f >= 0.001 else 8
            lower_esc = escape_markdown_v2(f"{lower_f:.{decimals}f}")
            upper_esc = escape_markdown_v2(f"{upper_f:.{decimals}f}")
            range_display = f"{lower_esc} \\- {upper_esc}"
        except (ValueError, TypeError):
            range_display = f"{escape_markdown_v2(str(lower_price))} \\- {escape_markdown_v2(str(upper_price))}"

        # Build success message
        lines = [
            f"✅ *Rebalance Complete*",
            f"━━━━━━━━━━━━━━━━━━━━━",
            f"*{escape_markdown_v2(pair)}*",
            "",
            f"✅ Old position closed",
            f"✅ New position opened",
            "",
            f"Range: {range_display}",
            f"Strategy: Bid\\-Ask",
        ]

        if close_tx or open_tx:
            lines.append("")
            if close_tx:
                lines.append(f"Close Tx: {close_tx_display}")
            if open_tx:
                lines.append(f"Open Tx: {open_tx_display}")

        await query.message.edit_text("\n".join(lines), parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Failed to rebalance position: {e}", exc_info=True)
        await query.message.edit_text(f"❌ Error: {str(e)[:200]}")


# ============================================
# SKIP AND DISMISS HANDLERS
# ============================================


async def handle_lpm_skip(
    update: Update, context: ContextTypes.DEFAULT_TYPE, cache_key: str
) -> None:
    """Skip a position alert (remove from cache and dismiss)."""
    query = update.callback_query
    await query.answer("Skipped")

    # Remove position from cache
    positions_cache = context.user_data.get("positions_cache", {})
    if cache_key in positions_cache:
        del positions_cache[cache_key]

    try:
        await query.message.edit_text("⏭ _Position skipped_", parse_mode="MarkdownV2")
    except Exception:
        pass


async def handle_lpm_dismiss(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Dismiss the LP monitor alert message."""
    query = update.callback_query
    await query.answer("Dismissed")

    try:
        await query.message.delete()
    except Exception:
        try:
            await query.message.edit_text(
                "✅ _Alert dismissed_", parse_mode="MarkdownV2"
            )
        except Exception:
            pass


# ============================================
# COUNTDOWN HANDLERS
# ============================================


async def handle_lpm_cancel_countdown(
    update: Update, context: ContextTypes.DEFAULT_TYPE, instance_id: str, pos_id: str
) -> None:
    """Cancel an active auto-close countdown."""
    query = update.callback_query
    await query.answer("Countdown cancelled")

    # Signal cancellation via user_data
    # The countdown task will check this flag and abort
    cancel_key = f"lpm_countdown_{instance_id}_{pos_id}"
    context.user_data[cancel_key] = "cancelled"

    try:
        await query.message.edit_text(
            "⏹ *Auto\\-close cancelled*\n\nPosition will remain open\\.",
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.warning(f"Could not update countdown message: {e}")
