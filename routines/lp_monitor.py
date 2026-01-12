"""
LP Monitor - Monitor LP positions for out-of-range and rebalance opportunities.

Features:
- Alerts when positions go out of range (with Close button)
- Alerts when positions return to range
- Rebalance suggestions when base asset % drops below threshold
- Periodic status reports
- Optional auto-close or auto-rebalance with 30s countdown
"""

import asyncio
import logging
import time
from pydantic import BaseModel, Field
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config_manager import get_client
from utils.telegram_formatters import escape_markdown_v2, resolve_token_symbol, KNOWN_TOKENS

logger = logging.getLogger(__name__)

CONTINUOUS = True


class Config(BaseModel):
    """Monitor LP positions for out-of-range and rebalance opportunities."""

    check_interval_sec: int = Field(
        default=60,
        description="Check interval in seconds",
    )
    status_report_hours: float = Field(
        default=4.0,
        description="Send status report every N hours (0=off)",
    )
    rebalance_base_pct: float = Field(
        default=0.2,
        description="Suggest rebalance when base asset <= N (0.2=20%)",
    )
    auto_close_oor: bool = Field(
        default=False,
        description="Auto-close out-of-range positions (30s delay)",
    )
    auto_rebalance: bool = Field(
        default=False,
        description="Auto-rebalance when triggered (30s delay)",
    )


# =============================================================================
# Helpers
# =============================================================================

def _get_pos_id(pos: dict) -> str:
    return pos.get('id') or pos.get('position_id') or pos.get('address', '') or pos.get('position_address', '')


def _format_price(price: float) -> str:
    if price >= 1:
        return f"{price:.2f}"
    elif price >= 0.001:
        return f"{price:.4f}"
    else:
        return f"{price:.6f}"


async def _fetch_token_prices(client) -> dict:
    """Fetch token prices from portfolio."""
    prices = {}
    try:
        if hasattr(client, 'portfolio'):
            result = await client.portfolio.get_state()
            if result:
                for account_data in result.values():
                    for balances in account_data.values():
                        if balances:
                            for b in balances:
                                if b.get("token") and b.get("price"):
                                    prices[b["token"]] = b["price"]
    except Exception:
        pass
    return prices


def _get_price(symbol: str, prices: dict, default: float = 0) -> float:
    if symbol in prices:
        return prices[symbol]
    for k, v in prices.items():
        if k.lower() == symbol.lower():
            return v
    # Wrapped variants
    variants = {"sol": "wsol", "wsol": "sol", "eth": "weth", "weth": "eth"}
    alt = variants.get(symbol.lower())
    if alt:
        for k, v in prices.items():
            if k.lower() == alt:
                return v
    return default


def _calc_base_pct(pos: dict, token_prices: dict, base_symbol: str, quote_symbol: str) -> float:
    """Calculate what percentage of position value is in base asset."""
    base_amt = float(pos.get('base_token_amount', pos.get('amount_a', 0)) or 0)
    quote_amt = float(pos.get('quote_token_amount', pos.get('amount_b', 0)) or 0)

    base_price = _get_price(base_symbol, token_prices, 0)
    quote_price = _get_price(quote_symbol, token_prices, 1.0)

    base_value = base_amt * base_price
    quote_value = quote_amt * quote_price
    total = base_value + quote_value

    if total <= 0:
        return 0.5  # Default to 50% if can't calculate
    return base_value / total


def _draw_liquidity_bar(base_pct: float, width: int = 20) -> str:
    """Draw a simple liquidity distribution bar."""
    base_blocks = int(base_pct * width)
    quote_blocks = width - base_blocks
    return f"[{'█' * base_blocks}{'░' * quote_blocks}]"


# =============================================================================
# Position Formatting
# =============================================================================

def _format_position(
    pos: dict,
    token_cache: dict,
    token_prices: dict,
    index: int = None,
) -> str:
    """Format a position for display."""
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_sym = resolve_token_symbol(base_token, token_cache)
    quote_sym = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_sym}-{quote_sym}"

    connector = pos.get('connector', 'unknown')
    fee = pos.get('fee_tier', pos.get('fee', ''))
    fee_str = f" {fee}%" if fee else ""

    # Range status
    in_range = pos.get('in_range', '')
    status = "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else "⚪"

    # Prices
    lower = float(pos.get('lower_price', pos.get('price_lower', 0)) or 0)
    upper = float(pos.get('upper_price', pos.get('price_upper', 0)) or 0)
    current = float(pos.get('current_price', 0) or 0)

    width_pct = ((upper - lower) / lower * 100) if lower > 0 else 0

    # Value & PnL
    quote_price = _get_price(quote_sym, token_prices, 1.0)
    base_price = _get_price(base_sym, token_prices, 0)

    pnl_summary = pos.get('pnl_summary', {})
    pnl_usd = float(pnl_summary.get('total_pnl_quote', 0) or 0) * quote_price
    value_usd = float(pnl_summary.get('current_lp_value_quote', 0) or 0) * quote_price

    base_fee = float(pos.get('base_fee_pending', 0) or 0)
    quote_fee = float(pos.get('quote_fee_pending', 0) or 0)
    fees_usd = (base_fee * base_price) + (quote_fee * quote_price)

    # Base asset percentage
    base_pct = _calc_base_pct(pos, token_prices, base_sym, quote_sym)
    liq_bar = _draw_liquidity_bar(base_pct)

    pnl_sign = "\\+" if pnl_usd >= 0 else "\\-"
    prefix = f"{index}\\. " if index else ""

    lines = [
        f"{prefix}*{escape_markdown_v2(pair)}{escape_markdown_v2(fee_str)}* \\({escape_markdown_v2(connector.capitalize())}\\)",
        f"   {status} \\[{escape_markdown_v2(_format_price(lower))} \\- {escape_markdown_v2(_format_price(upper))}\\] \\({escape_markdown_v2(f'{width_pct:.0f}')}% width\\)",
        f"   Price: {escape_markdown_v2(_format_price(current))}",
        f"   {liq_bar} {escape_markdown_v2(f'{base_pct*100:.0f}')}% {escape_markdown_v2(base_sym)}",
        f"   💰 ${escape_markdown_v2(f'{value_usd:.2f}')} \\| PnL: {pnl_sign}${escape_markdown_v2(f'{abs(pnl_usd):.2f}')} \\| 🎁 ${escape_markdown_v2(f'{fees_usd:.2f}')}",
    ]
    return "\n".join(lines)


# =============================================================================
# Notifications
# =============================================================================

async def _notify_out_of_range(
    context, chat_id: int, pos: dict, token_cache: dict, token_prices: dict,
    instance_id: str, user_data: dict, auto_close: bool
) -> None:
    """Notify when position goes out of range."""
    base_sym = resolve_token_symbol(pos.get('base_token', ''), token_cache)
    quote_sym = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
    pair = f"{base_sym}-{quote_sym}"

    current = float(pos.get('current_price', 0) or 0)
    lower = float(pos.get('lower_price', 0) or 0)
    upper = float(pos.get('upper_price', 0) or 0)

    direction = "▼ Below" if current < lower else "▲ Above"

    # Store position for callbacks
    pos_id = _get_pos_id(pos)
    cache_key = f"lpm_{instance_id}_{pos_id[:8]}"
    user_data.setdefault("positions_cache", {})[cache_key] = pos

    text = (
        f"🔴 *Position Out of Range*\n\n"
        f"*{escape_markdown_v2(pair)}*\n"
        f"_{escape_markdown_v2(direction)} range_\n"
        f"Current: {escape_markdown_v2(_format_price(current))}\n"
        f"Range: {escape_markdown_v2(_format_price(lower))} \\- {escape_markdown_v2(_format_price(upper))}"
    )

    keyboard = [[
        InlineKeyboardButton("❌ Close Position", callback_data=f"dex:pos_close:{cache_key}"),
        InlineKeyboardButton("✅ Dismiss", callback_data=f"dex:lpm_dismiss:{instance_id}"),
    ]]

    try:
        msg = await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        if auto_close:
            asyncio.create_task(_countdown_action(
                context, chat_id, msg.message_id, pos, "close",
                cache_key, instance_id, user_data, token_cache
            ))
    except Exception as e:
        logger.error(f"Failed to send OOR notification: {e}")


async def _notify_back_in_range(context, chat_id: int, pos: dict, token_cache: dict) -> None:
    """Notify when position returns to range."""
    base_sym = resolve_token_symbol(pos.get('base_token', ''), token_cache)
    quote_sym = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
    pair = f"{base_sym}-{quote_sym}"

    current = float(pos.get('current_price', 0) or 0)

    text = (
        f"🟢 *Position Back in Range*\n\n"
        f"*{escape_markdown_v2(pair)}*\n"
        f"Current: {escape_markdown_v2(_format_price(current))}"
    )

    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="MarkdownV2")
    except Exception as e:
        logger.error(f"Failed to send back-in-range notification: {e}")


async def _notify_rebalance(
    context, chat_id: int, pos: dict, token_cache: dict, token_prices: dict,
    base_pct: float, instance_id: str, user_data: dict, auto_rebalance: bool
) -> None:
    """Notify when position should be rebalanced (base asset too low)."""
    base_sym = resolve_token_symbol(pos.get('base_token', ''), token_cache)
    quote_sym = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
    pair = f"{base_sym}-{quote_sym}"

    # Current distribution
    current_bar = _draw_liquidity_bar(base_pct)
    # Proposed bid-ask distribution (roughly 50/50 at current price)
    proposed_bar = _draw_liquidity_bar(0.5)

    lower = float(pos.get('lower_price', 0) or 0)
    upper = float(pos.get('upper_price', 0) or 0)
    current = float(pos.get('current_price', 0) or 0)

    pos_id = _get_pos_id(pos)
    cache_key = f"lpm_{instance_id}_{pos_id[:8]}"
    user_data.setdefault("positions_cache", {})[cache_key] = pos

    text = (
        f"⚖️ *Rebalance Suggestion*\n\n"
        f"*{escape_markdown_v2(pair)}*\n"
        f"Base asset dropped to {escape_markdown_v2(f'{base_pct*100:.0f}')}%\n\n"
        f"*Current Distribution:*\n"
        f"`{current_bar}` {escape_markdown_v2(f'{base_pct*100:.0f}')}% {escape_markdown_v2(base_sym)}\n\n"
        f"*Proposed \\(Bid\\-Ask\\):*\n"
        f"`{proposed_bar}` 50% {escape_markdown_v2(base_sym)}\n\n"
        f"Range: {escape_markdown_v2(_format_price(lower))} \\- {escape_markdown_v2(_format_price(upper))}\n"
        f"Price: {escape_markdown_v2(_format_price(current))}"
    )

    keyboard = [[
        InlineKeyboardButton("🔄 Rebalance", callback_data=f"dex:lpm_rebal:{cache_key}"),
        InlineKeyboardButton("✅ Dismiss", callback_data=f"dex:lpm_dismiss:{instance_id}"),
    ]]

    try:
        msg = await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        if auto_rebalance:
            asyncio.create_task(_countdown_action(
                context, chat_id, msg.message_id, pos, "rebalance",
                cache_key, instance_id, user_data, token_cache
            ))
    except Exception as e:
        logger.error(f"Failed to send rebalance notification: {e}")


# =============================================================================
# Auto Actions with Countdown
# =============================================================================

async def _countdown_action(
    context, chat_id: int, msg_id: int, pos: dict, action: str,
    cache_key: str, instance_id: str, user_data: dict, token_cache: dict
) -> None:
    """30s countdown before auto action. Can be cancelled."""
    base_sym = resolve_token_symbol(pos.get('base_token', ''), token_cache)
    quote_sym = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
    pair = f"{base_sym}-{quote_sym}"

    cancel_key = f"cancel_{cache_key}"
    action_name = "Close" if action == "close" else "Rebalance"

    for remaining in [30, 20, 10, 5]:
        if user_data.get(cancel_key):
            user_data.pop(cancel_key, None)
            return

        text = (
            f"⏱️ *Auto\\-{action_name} in {remaining}s*\n\n"
            f"*{escape_markdown_v2(pair)}*\n\n"
            f"_Press Cancel to stop_"
        )

        keyboard = [[
            InlineKeyboardButton("❌ Cancel", callback_data=f"dex:lpm_cancel:{cache_key}"),
        ]]

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=text,
                parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass

        wait = 10 if remaining > 10 else 5
        await asyncio.sleep(wait)

    # Final check
    if user_data.get(cancel_key):
        user_data.pop(cancel_key, None)
        return

    # Execute action
    client = await get_client(chat_id, context=context)
    if not client:
        return

    if action == "close":
        await _execute_close(context, chat_id, msg_id, pos, client, token_cache)
    else:
        await _execute_rebalance(context, chat_id, msg_id, pos, client, token_cache)


async def _execute_close(context, chat_id: int, msg_id: int, pos: dict, client, token_cache: dict) -> None:
    """Execute position close."""
    base_sym = resolve_token_symbol(pos.get('base_token', ''), token_cache)
    quote_sym = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
    pair = f"{base_sym}-{quote_sym}"

    connector = pos.get('connector', 'meteora')
    network = pos.get('network', 'solana-mainnet-beta')
    pos_addr = pos.get('position_address', pos.get('nft_id', ''))

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=f"⏳ *Closing\\.\\.\\.*\n\n*{escape_markdown_v2(pair)}*",
            parse_mode="MarkdownV2"
        )

        result = await client.gateway_clmm.close_position(
            connector=connector, network=network, position_address=pos_addr
        )

        if result:
            tx = result.get('tx_hash', 'N/A')[:16]
            base_amt = float(result.get('base_amount', 0) or 0)
            quote_amt = float(result.get('quote_amount', 0) or 0)

            text = (
                f"✅ *Position Closed*\n\n"
                f"*{escape_markdown_v2(pair)}*\n"
                f"Received: {escape_markdown_v2(f'{base_amt:.6f}')} {escape_markdown_v2(base_sym)}\n"
                f"Received: {escape_markdown_v2(f'{quote_amt:.6f}')} {escape_markdown_v2(quote_sym)}\n\n"
                f"Tx: `{escape_markdown_v2(tx)}...`"
            )
        else:
            text = f"❌ *Close failed*\n\nNo response"

        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id, text=text, parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Close failed: {e}")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"❌ *Close failed*\n\n{escape_markdown_v2(str(e)[:100])}",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass


async def _execute_rebalance(context, chat_id: int, msg_id: int, pos: dict, client, token_cache: dict) -> None:
    """Execute rebalance: close and reopen with bid-ask strategy."""
    from decimal import Decimal

    base_sym = resolve_token_symbol(pos.get('base_token', ''), token_cache)
    quote_sym = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
    pair = f"{base_sym}-{quote_sym}"

    connector = pos.get('connector', 'meteora')
    network = pos.get('network', 'solana-mainnet-beta')
    pos_addr = pos.get('position_address', pos.get('nft_id', ''))
    pool_addr = pos.get('pool_id', pos.get('pool_address', ''))
    lower = pos.get('lower_price', pos.get('price_lower', 0))
    upper = pos.get('upper_price', pos.get('price_upper', 0))

    try:
        # Step 1: Close
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=f"🔄 *Rebalancing\\.\\.\\.*\n\n*{escape_markdown_v2(pair)}*\n\n1/2 Closing position\\.\\.\\.",
            parse_mode="MarkdownV2"
        )

        close_result = await client.gateway_clmm.close_position(
            connector=connector, network=network, position_address=pos_addr
        )

        if not close_result:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"❌ *Rebalance failed*\n\nCould not close position",
                parse_mode="MarkdownV2"
            )
            return

        base_amt = float(close_result.get('base_amount', close_result.get('amount_base', 0)) or 0)
        quote_amt = float(close_result.get('quote_amount', close_result.get('amount_quote', 0)) or 0)

        # Fallback to position amounts
        if not base_amt:
            base_amt = float(pos.get('base_token_amount', pos.get('amount_a', 0)) or 0)
        if not quote_amt:
            quote_amt = float(pos.get('quote_token_amount', pos.get('amount_b', 0)) or 0)

        # Step 2: Reopen with bid-ask strategy
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=f"🔄 *Rebalancing\\.\\.\\.*\n\n*{escape_markdown_v2(pair)}*\n\n✅ Closed\n2/2 Opening with bid\\-ask\\.\\.\\.",
            parse_mode="MarkdownV2"
        )

        open_result = await client.gateway_clmm.open_position(
            connector=connector,
            network=network,
            pool_address=pool_addr,
            lower_price=Decimal(str(lower)),
            upper_price=Decimal(str(upper)),
            base_token_amount=base_amt,
            quote_token_amount=quote_amt,
            extra_params={"strategyType": 2}  # Bid-Ask
        )

        if open_result:
            tx = (open_result.get('tx_hash') or open_result.get('signature', 'N/A'))[:16]
            text = (
                f"✅ *Rebalance Complete*\n\n"
                f"*{escape_markdown_v2(pair)}*\n"
                f"Strategy: Bid\\-Ask\n"
                f"Range: {escape_markdown_v2(_format_price(float(lower)))} \\- {escape_markdown_v2(_format_price(float(upper)))}\n\n"
                f"Tx: `{escape_markdown_v2(tx)}...`"
            )
        else:
            text = (
                f"⚠️ *Partial Rebalance*\n\n"
                f"*{escape_markdown_v2(pair)}*\n"
                f"Position closed but failed to reopen\\.\n"
                f"Funds are in your wallet\\."
            )

        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id, text=text, parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Rebalance failed: {e}")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"❌ *Rebalance failed*\n\n{escape_markdown_v2(str(e)[:100])}",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass


# =============================================================================
# Status Report
# =============================================================================

async def _send_status_report(
    context, chat_id: int, positions: list, token_cache: dict, token_prices: dict,
    instance_id: str, user_data: dict
) -> None:
    """Send periodic status report."""
    if not positions:
        await context.bot.send_message(
            chat_id=chat_id,
            text="📊 *LP Monitor*\n\nNo active positions\\.",
            parse_mode="MarkdownV2"
        )
        return

    # Calculate totals
    total_value = 0
    total_pnl = 0
    total_fees = 0
    in_range_count = 0

    for pos in positions:
        base_sym = resolve_token_symbol(pos.get('base_token', ''), token_cache)
        quote_sym = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
        quote_price = _get_price(quote_sym, token_prices, 1.0)
        base_price = _get_price(base_sym, token_prices, 0)

        pnl_summary = pos.get('pnl_summary', {})
        total_pnl += float(pnl_summary.get('total_pnl_quote', 0) or 0) * quote_price
        total_value += float(pnl_summary.get('current_lp_value_quote', 0) or 0) * quote_price

        base_fee = float(pos.get('base_fee_pending', 0) or 0)
        quote_fee = float(pos.get('quote_fee_pending', 0) or 0)
        total_fees += (base_fee * base_price) + (quote_fee * quote_price)

        if pos.get('in_range') == "IN_RANGE":
            in_range_count += 1

    oor_count = len(positions) - in_range_count
    pnl_sign = "\\+" if total_pnl >= 0 else "\\-"

    lines = [
        f"📊 *LP Monitor Status*",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"💰 ${escape_markdown_v2(f'{total_value:.2f}')} \\| PnL: {pnl_sign}${escape_markdown_v2(f'{abs(total_pnl):.2f}')} \\| 🎁 ${escape_markdown_v2(f'{total_fees:.2f}')}",
        f"✅ {in_range_count} in range \\| 🔴 {oor_count} out of range",
        "",
    ]

    # Format each position
    for i, pos in enumerate(positions, 1):
        lines.append(_format_position(pos, token_cache, token_prices, i))
        lines.append("")

    text = "\n".join(lines)

    # Store positions for callbacks and build action buttons
    keyboard = []
    for i, pos in enumerate(positions):
        pos_id = _get_pos_id(pos)
        cache_key = f"lpm_{instance_id}_{pos_id[:8]}"
        user_data.setdefault("positions_cache", {})[cache_key] = pos

        # Get pair name for button
        base_sym = resolve_token_symbol(pos.get('base_token', ''), token_cache)
        quote_sym = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
        pair = f"{base_sym}-{quote_sym}"
        in_range = pos.get('in_range', '')
        status = "🟢" if in_range == "IN_RANGE" else "🔴"

        # Add row with close and rebalance for each position
        keyboard.append([
            InlineKeyboardButton(f"{status} {pair}", callback_data="noop"),
            InlineKeyboardButton("❌ Close", callback_data=f"dex:pos_close:{cache_key}"),
            InlineKeyboardButton("🔄 Rebalance", callback_data=f"dex:lpm_rebal:{cache_key}"),
        ])

    keyboard.append([InlineKeyboardButton("💰 Collect All Fees", callback_data=f"dex:lpm_collect_all:{instance_id}")])
    keyboard.append([InlineKeyboardButton("⏹ Stop Monitor", callback_data=f"routines:stop:{instance_id}")])

    try:
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Failed to send status report: {e}")


# =============================================================================
# Main Loop
# =============================================================================

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Monitor LP positions."""
    logger.info(f"LP Monitor starting: interval={config.check_interval_sec}s, report={config.status_report_hours}h")

    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    instance_id = getattr(context, '_instance_id', 'default')

    if not chat_id:
        return "No chat_id"

    client = await get_client(chat_id, context=context)
    if not client or not hasattr(client, 'gateway_clmm'):
        return "Gateway not available"

    user_data = context.user_data if hasattr(context, 'user_data') else {}

    # State
    state = {
        "out_of_range": set(),      # pos_ids currently OOR
        "rebalance_notified": set(), # pos_ids we've notified for rebalance
        "checks": 0,
        "alerts": 0,
        "start_time": time.time(),
        "last_report": time.time(),
    }

    # Start message
    features = []
    if config.auto_close_oor:
        features.append("Auto\\-close OOR")
    if config.auto_rebalance:
        features.append("Auto\\-rebalance")
    if config.status_report_hours > 0:
        features.append(f"Report every {config.status_report_hours}h")

    features_str = " \\| ".join(features) if features else "Manual mode"

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🟢 *LP Monitor Started*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Checking every {config.check_interval_sec}s\n"
                f"Rebalance trigger: ≤{escape_markdown_v2(f'{config.rebalance_base_pct*100:.0f}')}% base\n"
                f"{features_str}"
            ),
            parse_mode="MarkdownV2"
        )
    except Exception:
        pass

    try:
        while True:
            try:
                state["checks"] += 1

                # Get client
                client = await get_client(chat_id, context=context)
                if not client or not hasattr(client, 'gateway_clmm'):
                    await asyncio.sleep(config.check_interval_sec)
                    continue

                # Fetch positions
                result = await client.gateway_clmm.search_positions(
                    limit=100, offset=0, status="OPEN", refresh=True
                )

                if not result:
                    await asyncio.sleep(config.check_interval_sec)
                    continue

                positions = [p for p in result.get("data", [])
                            if p.get('status') != 'CLOSED' and
                            float(p.get('liquidity', p.get('current_liquidity', 1)) or 1) > 0]

                if not positions:
                    await asyncio.sleep(config.check_interval_sec)
                    continue

                # Build caches
                token_cache = dict(KNOWN_TOKENS)
                networks = set(p.get('network', 'solana-mainnet-beta') for p in positions)
                if hasattr(client, 'gateway'):
                    for net in networks:
                        try:
                            resp = await client.gateway.get_network_tokens(net)
                            for t in (resp.get('tokens', []) if resp else []):
                                if t.get('address') and t.get('symbol'):
                                    token_cache[t['address']] = t['symbol']
                        except Exception:
                            pass

                token_prices = await _fetch_token_prices(client)
                user_data["token_cache"] = token_cache
                user_data["token_prices"] = token_prices

                # Check each position
                current_oor = set()

                for pos in positions:
                    pos_id = _get_pos_id(pos)
                    in_range = pos.get('in_range', '')

                    # Out-of-range detection
                    if in_range == "OUT_OF_RANGE":
                        current_oor.add(pos_id)

                        if pos_id not in state["out_of_range"]:
                            # Newly out of range
                            state["alerts"] += 1
                            await _notify_out_of_range(
                                context, chat_id, pos, token_cache, token_prices,
                                instance_id, user_data, config.auto_close_oor
                            )

                    elif pos_id in state["out_of_range"]:
                        # Back in range
                        await _notify_back_in_range(context, chat_id, pos, token_cache)

                    # Rebalance check (only for in-range positions not already notified)
                    if in_range == "IN_RANGE" and pos_id not in state["rebalance_notified"]:
                        base_sym = resolve_token_symbol(pos.get('base_token', ''), token_cache)
                        quote_sym = resolve_token_symbol(pos.get('quote_token', ''), token_cache)
                        base_pct = _calc_base_pct(pos, token_prices, base_sym, quote_sym)

                        if base_pct <= config.rebalance_base_pct:
                            state["rebalance_notified"].add(pos_id)
                            state["alerts"] += 1
                            await _notify_rebalance(
                                context, chat_id, pos, token_cache, token_prices,
                                base_pct, instance_id, user_data, config.auto_rebalance
                            )

                state["out_of_range"] = current_oor

                # Periodic status report
                if config.status_report_hours > 0:
                    elapsed = time.time() - state["last_report"]
                    if elapsed >= config.status_report_hours * 3600:
                        state["last_report"] = time.time()
                        await _send_status_report(
                            context, chat_id, positions, token_cache, token_prices,
                            instance_id, user_data
                        )

                # Log progress
                if state["checks"] % 10 == 0:
                    logger.info(f"LP Monitor #{state['checks']}: {len(positions)} positions, {state['alerts']} alerts")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"LP Monitor error: {e}", exc_info=True)

            await asyncio.sleep(config.check_interval_sec)

    except asyncio.CancelledError:
        elapsed = int(time.time() - state["start_time"])
        mins, secs = divmod(elapsed, 60)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔴 *LP Monitor Stopped*\n{mins}m {secs}s \\| {state['checks']} checks \\| {state['alerts']} alerts",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass

        return f"Stopped: {mins}m {secs}s, {state['checks']} checks, {state['alerts']} alerts"
