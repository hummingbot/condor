"""Monitor LP positions and alert when they go out of range."""

import asyncio
import logging
import time
from pydantic import BaseModel, Field
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config_manager import get_client
from utils.telegram_formatters import escape_markdown_v2, resolve_token_symbol, KNOWN_TOKENS

logger = logging.getLogger(__name__)


async def _fetch_token_prices(client) -> dict:
    """Fetch token prices from portfolio state."""
    token_prices = {}
    try:
        if hasattr(client, 'portfolio'):
            result = await client.portfolio.get_state()
            if result:
                for account_data in result.values():
                    for balances in account_data.values():
                        if balances:
                            for balance in balances:
                                token = balance.get("token", "")
                                price = balance.get("price", 0)
                                if token and price:
                                    token_prices[token] = price
    except Exception as e:
        logger.debug(f"Could not fetch token prices: {e}")
    return token_prices


def _get_price(symbol: str, token_prices: dict, default: float = 0) -> float:
    """Get token price with fallbacks for wrapped variants."""
    if symbol in token_prices:
        return token_prices[symbol]
    # Case-insensitive match
    symbol_lower = symbol.lower()
    for key, price in token_prices.items():
        if key.lower() == symbol_lower:
            return price
    # Wrapped variants
    variants = {
        "sol": ["wsol"], "wsol": ["sol"],
        "eth": ["weth"], "weth": ["eth"],
    }
    for variant in variants.get(symbol_lower, []):
        for key, price in token_prices.items():
            if key.lower() == variant:
                return price
    return default


def _format_compact_position(
    pos: dict,
    token_cache: dict,
    token_prices: dict,
    index: int = None,
    initial_prices: dict = None
) -> str:
    """Format position like: 1. WETZ-SOL (met) 🟢 [0.001-0.002] [█░░░░]
       PnL: -$5.79 | Value: $270.28 | 🎁 $2.09 | Δ: +25%
    """
    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')[:3]

    # In-range status
    in_range = pos.get('in_range', '')
    status_emoji = "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else "⚪"

    # Price range and position indicator
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))
    current = pos.get('current_price', '')

    range_str = ""
    price_indicator = ""
    if lower and upper:
        try:
            lower_f = float(lower)
            upper_f = float(upper)
            decimals = 2 if lower_f >= 1 else 6 if lower_f >= 0.001 else 8
            # Escape the numbers and use escaped dash
            lower_esc = escape_markdown_v2(f"{lower_f:.{decimals}f}")
            upper_esc = escape_markdown_v2(f"{upper_f:.{decimals}f}")
            range_str = f"\\[{lower_esc}\\-{upper_esc}\\]"

            if current:
                current_f = float(current)
                if current_f < lower_f:
                    price_indicator = "▼"
                elif current_f > upper_f:
                    price_indicator = "▲"
                else:
                    pct = (current_f - lower_f) / (upper_f - lower_f)
                    bar_len = 5
                    filled = int(pct * bar_len)
                    price_indicator = f"[{'█' * filled}{'░' * (bar_len - filled)}]"
        except (ValueError, TypeError):
            range_str = f"\\[{escape_markdown_v2(str(lower))}\\-{escape_markdown_v2(str(upper))}\\]"

    # Build first line
    prefix = f"{index}\\. " if index is not None else "• "
    range_with_indicator = f"{range_str} {price_indicator}" if price_indicator else range_str  # Already escaped
    line1 = f"{prefix}{escape_markdown_v2(pair)} \\({escape_markdown_v2(connector)}\\) {status_emoji} {range_with_indicator}"

    # Get prices for USD conversion
    quote_price = _get_price(quote_symbol, token_prices, 1.0)
    base_price = _get_price(base_symbol, token_prices, 0)

    # Get PnL data
    pnl_summary = pos.get('pnl_summary', {})
    total_pnl_quote = pnl_summary.get('total_pnl_quote', 0)
    current_lp_value_quote = pnl_summary.get('current_lp_value_quote', 0)

    # Get fees
    base_fee_pending = float(pos.get('base_fee_pending', 0) or 0)
    quote_fee_pending = float(pos.get('quote_fee_pending', 0) or 0)

    try:
        pnl_usd = float(total_pnl_quote or 0) * quote_price
        value_usd = float(current_lp_value_quote or 0) * quote_price
        pending_fees_usd = (base_fee_pending * base_price) + (quote_fee_pending * quote_price)

        parts = []
        if pnl_usd >= 0:
            parts.append(f"PnL: \\+${escape_markdown_v2(f'{pnl_usd:.2f}')}")
        else:
            parts.append(f"PnL: \\-${escape_markdown_v2(f'{abs(pnl_usd):.2f}')}")
        parts.append(f"Value: ${escape_markdown_v2(f'{value_usd:.2f}')}")
        if pending_fees_usd > 0.01:
            parts.append(f"🎁 ${escape_markdown_v2(f'{pending_fees_usd:.2f}')}")

        # Add price change if initial prices available
        if initial_prices and current:
            pos_id = pos.get('id') or pos.get('position_id') or pos.get('address', '')
            initial_price = initial_prices.get(pos_id)
            if initial_price:
                try:
                    current_f = float(current)
                    pct_change = ((current_f - initial_price) / initial_price) * 100
                    sign = "\\+" if pct_change >= 0 else ""
                    parts.append(f"Δ: {sign}{escape_markdown_v2(f'{pct_change:.1f}')}%")
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

        line2 = "   " + " \\| ".join(parts)
        return f"{line1}\n{line2}"
    except (ValueError, TypeError):
        return line1

def _capture_initial_prices(state: dict, positions: list) -> None:
    """Capture initial prices for positions not yet tracked."""
    for pos in positions:
        pos_id = pos.get('id') or pos.get('position_id') or pos.get('address', '')
        if pos_id and pos_id not in state["initial_prices"]:
            current = pos.get('current_price')
            if current:
                try:
                    state["initial_prices"][pos_id] = float(current)
                except (ValueError, TypeError):
                    pass


def _check_price_triggers(
    state: dict,
    positions: list,
    threshold_pct: float
) -> list:
    """Check positions against price change threshold. Returns list of triggered positions."""
    triggered = []
    for pos in positions:
        pos_id = pos.get('id') or pos.get('position_id') or pos.get('address', '')
        if not pos_id or pos_id in state["triggered_positions"]:
            continue  # Already triggered

        initial = state["initial_prices"].get(pos_id)
        current = pos.get('current_price')
        if not initial or not current:
            continue

        try:
            current_f = float(current)
            pct_change = ((current_f - initial) / initial) * 100
            if abs(pct_change) >= threshold_pct:
                triggered.append({
                    "position": pos,
                    "pos_id": pos_id,
                    "initial_price": initial,
                    "current_price": current_f,
                    "pct_change": pct_change,
                })
        except (ValueError, TypeError, ZeroDivisionError):
            pass
    return triggered


async def _send_trigger_alert(
    context,
    chat_id: int,
    trigger_info: dict,
    token_cache: dict,
    token_prices: dict,
    instance_id: str,
    user_data: dict
) -> None:
    """Send alert message for a triggered position."""
    pos = trigger_info["position"]
    pct_change = trigger_info["pct_change"]
    initial_price = trigger_info["initial_price"]
    current_price = trigger_info["current_price"]

    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')

    # In-range status
    in_range = pos.get('in_range', '')
    status_emoji = "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else "⚪"

    # Price range
    lower = pos.get('lower_price', pos.get('price_lower', 0))
    upper = pos.get('upper_price', pos.get('price_upper', 0))

    range_str = ""
    width_str = ""
    try:
        lower_f = float(lower)
        upper_f = float(upper)
        decimals = 2 if lower_f >= 1 else 6 if lower_f >= 0.001 else 8
        range_str = f"{lower_f:.{decimals}f} - {upper_f:.{decimals}f}"
        if lower_f > 0:
            width_pct = ((upper_f - lower_f) / lower_f) * 100
            width_str = f" ({width_pct:.0f}% Width)"
    except (ValueError, TypeError):
        range_str = f"{lower} - {upper}"

    # Price change direction
    sign = "+" if pct_change >= 0 else ""

    # Format prices
    decimals = 2 if current_price >= 1 else 6 if current_price >= 0.001 else 8
    current_str = f"{current_price:.{decimals}f}"
    initial_str = f"{initial_price:.{decimals}f}"

    # Get prices for USD conversion
    quote_price = _get_price(quote_symbol, token_prices, 1.0)
    base_price = _get_price(base_symbol, token_prices, 0)

    # Get values
    pnl_summary = pos.get('pnl_summary', {})
    total_pnl = float(pnl_summary.get('total_pnl_quote', 0) or 0)
    lp_value = float(pnl_summary.get('current_lp_value_quote', 0) or 0)
    pnl_usd = total_pnl * quote_price
    value_usd = lp_value * quote_price

    # Fees
    base_fee = float(pos.get('base_fee_pending', 0) or 0)
    quote_fee = float(pos.get('quote_fee_pending', 0) or 0)
    fees_usd = (base_fee * base_price) + (quote_fee * quote_price)

    # Build message
    pnl_sign = "\\+" if pnl_usd >= 0 else "\\-"
    lines = [
        "⚠️ *Price Change Trigger*",
        "",
        f"*{escape_markdown_v2(pair)}* \\({escape_markdown_v2(connector)}\\)",
        f"Position: {status_emoji} \\[{escape_markdown_v2(range_str)}\\]{escape_markdown_v2(width_str)}",
        f"Current Price: {escape_markdown_v2(current_str)}",
        f"Price Change: {escape_markdown_v2(f'{sign}{pct_change:.1f}%')} vs Initial {escape_markdown_v2(initial_str)}",
        f"Value: ${escape_markdown_v2(f'{value_usd:.2f}')}",
        f"PnL: {pnl_sign}${escape_markdown_v2(f'{abs(pnl_usd):.2f}')}",
        f"Fees: ${escape_markdown_v2(f'{fees_usd:.2f}')}",
    ]

    text = "\n".join(lines)

    # Store position in cache for close handler
    pos_id = trigger_info["pos_id"]
    cache_key = f"lpm_{instance_id}_{pos_id[:8]}"
    if "positions_cache" not in user_data:
        user_data["positions_cache"] = {}
    user_data["positions_cache"][cache_key] = pos

    # Build keyboard
    keyboard = [[
        InlineKeyboardButton("❌ Close", callback_data=f"dex:pos_close:{cache_key}"),
        InlineKeyboardButton("✅ Dismiss", callback_data=f"dex:lpm_dismiss:{instance_id}"),
    ]]

    for attempt in range(3):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error(f"Failed to send trigger alert: {e}")


def _format_countdown_message(trigger_info: dict, remaining_sec: int, token_cache: dict, action: str = "close") -> str:
    """Format countdown message for auto action."""
    pos = trigger_info["position"]
    pct_change = trigger_info["pct_change"]
    initial_price = trigger_info["initial_price"]

    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')
    sign = "+" if pct_change >= 0 else ""
    decimals = 2 if initial_price >= 1 else 6 if initial_price >= 0.001 else 8

    if action == "rebalance":
        title = f"⏱️ *Auto\\-Rebalance in {remaining_sec}s*"
        desc = "Position will close and reopen at current price\\."
    else:
        title = f"⏱️ *Auto\\-Close in {remaining_sec}s*"
        desc = "Position will close \\(liquidity withdrawn to wallet\\)\\."

    lines = [
        title,
        "",
        f"*{escape_markdown_v2(pair)}* \\({escape_markdown_v2(connector)}\\)",
        f"Price changed {escape_markdown_v2(f'{sign}{pct_change:.1f}%')} from initial {escape_markdown_v2(f'{initial_price:.{decimals}f}')}",
        "",
        desc,
    ]
    return "\n".join(lines)


async def _start_auto_action_countdown(
    context,
    chat_id: int,
    trigger_info: dict,
    countdown_sec: int,
    instance_id: str,
    user_data: dict,
    client,
    token_cache: dict,
    action: str = "close"
) -> None:
    """Start a countdown before auto action (close or rebalance)."""
    pos = trigger_info["position"]
    pos_id = trigger_info["pos_id"]

    # Cache position for handler
    cache_key = f"lpm_{instance_id}_{pos_id[:8]}"
    if "positions_cache" not in user_data:
        user_data["positions_cache"] = {}
    user_data["positions_cache"][cache_key] = pos

    # Cancellation key
    cancel_key = f"lpm_countdown_{instance_id}_{pos_id[:16]}"

    # Build keyboard with cancel button
    keyboard = [[
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data=f"dex:lpm_cancel_countdown:{instance_id}:{pos_id[:16]}"
        )
    ]]

    # Send countdown message
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=_format_countdown_message(trigger_info, countdown_sec, token_cache, action),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Failed to send countdown message: {e}")
        return

    # Countdown loop (update every 5s)
    remaining = countdown_sec
    while remaining > 0:
        wait_time = min(5, remaining)
        await asyncio.sleep(wait_time)
        remaining -= wait_time

        # Check for cancellation
        if user_data.get(cancel_key) == "cancelled":
            user_data.pop(cancel_key, None)
            logger.info(f"Auto-{action} countdown cancelled for {pos_id[:8]}")
            return

        # Update message with new countdown
        if remaining > 0:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    text=_format_countdown_message(trigger_info, remaining, token_cache, action),
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception:
                pass  # Ignore edit failures

    # Final cancellation check
    if user_data.get(cancel_key) == "cancelled":
        user_data.pop(cancel_key, None)
        return

    # Execute the action
    if action == "rebalance":
        await _execute_auto_rebalance(context, chat_id, pos, msg.message_id, client, token_cache)
    else:
        await _execute_auto_close(context, chat_id, pos, msg.message_id, client, token_cache)
    user_data.pop(cancel_key, None)


async def _execute_auto_close(
    context,
    chat_id: int,
    pos: dict,
    message_id: int,
    client,
    token_cache: dict
) -> None:
    """Execute auto-close on a position."""
    # Resolve symbols for display
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'meteora')
    network = pos.get('network', 'solana-mainnet-beta')
    position_address = pos.get('position_address', pos.get('nft_id', ''))

    if not position_address:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ *Auto\\-close failed*\n\nPosition address not found\\.",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass
        return

    # Update message to show closing
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏳ *Closing position\\.\\.\\.*\n\n{escape_markdown_v2(pair)}",
            parse_mode="MarkdownV2"
        )
    except Exception:
        pass

    try:
        result = await client.gateway_clmm.close_position(
            connector=connector,
            network=network,
            position_address=position_address
        )

        if result:
            tx_hash = result.get('tx_hash', 'N/A')
            base_amt = result.get('base_amount', result.get('amount_base', 0))
            quote_amt = result.get('quote_amount', result.get('amount_quote', 0))

            lines = [
                "✅ *Position Closed*",
                "",
                f"*{escape_markdown_v2(pair)}*",
                f"Received: {escape_markdown_v2(f'{float(base_amt):.6f}')} {escape_markdown_v2(base_symbol)}",
                f"Received: {escape_markdown_v2(f'{float(quote_amt):.6f}')} {escape_markdown_v2(quote_symbol)}",
                "",
                f"Tx: `{escape_markdown_v2(tx_hash[:16])}...`",
            ]

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="\n".join(lines),
                parse_mode="MarkdownV2"
            )
            logger.info(f"Auto-closed position {position_address[:8]}: {tx_hash}")
        else:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ *Auto\\-close failed*\n\nNo response from gateway\\.",
                parse_mode="MarkdownV2"
            )
    except Exception as e:
        logger.error(f"Auto-close failed for {position_address[:8]}: {e}")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ *Auto\\-close failed*\n\n{escape_markdown_v2(str(e)[:100])}",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass


async def _execute_auto_rebalance(
    context,
    chat_id: int,
    pos: dict,
    message_id: int,
    client,
    token_cache: dict
) -> None:
    """Execute auto-rebalance: close position and reopen at current price."""
    from decimal import Decimal

    # Resolve symbols for display
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'meteora')
    network = pos.get('network', 'solana-mainnet-beta')
    position_address = pos.get('position_address', pos.get('nft_id', ''))
    pool_address = pos.get('pool_id', pos.get('pool_address', ''))
    lower_price = pos.get('lower_price', pos.get('price_lower', 0))
    upper_price = pos.get('upper_price', pos.get('price_upper', 0))

    if not position_address:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ *Auto\\-rebalance failed*\n\nPosition address not found\\.",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass
        return

    # Step 1: Close position
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"🔄 *Rebalancing\\.\\.\\.*\n\n*{escape_markdown_v2(pair)}*\n\nStep 1/3: Closing position\\.\\.\\.",
            parse_mode="MarkdownV2"
        )
    except Exception:
        pass

    try:
        close_result = await client.gateway_clmm.close_position(
            connector=connector,
            network=network,
            position_address=position_address
        )

        if not close_result:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ *Auto\\-rebalance failed*\n\nFailed to close position\\.",
                parse_mode="MarkdownV2"
            )
            return

        # Get withdrawn amounts
        base_withdrawn = close_result.get('base_amount', close_result.get('amount_base', 0))
        quote_withdrawn = close_result.get('quote_amount', close_result.get('amount_quote', 0))

        # Fallback to position amounts
        if not base_withdrawn:
            base_withdrawn = pos.get('base_token_amount', pos.get('amount_a', 0))
        if not quote_withdrawn:
            quote_withdrawn = pos.get('quote_token_amount', pos.get('amount_b', 0))

        # Step 2: Update progress
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"🔄 *Rebalancing\\.\\.\\.*\n\n*{escape_markdown_v2(pair)}*\n\n"
                 f"✅ Step 1/3: Position closed\n"
                 f"✅ Step 2/3: Amounts ready\n"
                 f"Step 3/3: Opening new position\\.\\.\\.",
            parse_mode="MarkdownV2"
        )

        # Step 3: Open new position with same range
        extra_params = {"strategyType": 2}  # Bid-Ask strategy

        open_result = await client.gateway_clmm.open_position(
            connector=connector,
            network=network,
            pool_address=pool_address,
            lower_price=Decimal(str(lower_price)),
            upper_price=Decimal(str(upper_price)),
            base_token_amount=float(base_withdrawn) if base_withdrawn else 0,
            quote_token_amount=float(quote_withdrawn) if quote_withdrawn else 0,
            extra_params=extra_params
        )

        if not open_result:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"⚠️ *Partial Rebalance*\n\n"
                     f"*{escape_markdown_v2(pair)}*\n\n"
                     f"✅ Position closed\n"
                     f"❌ Failed to reopen: No response\n\n"
                     f"Funds are in your wallet\\.",
                parse_mode="MarkdownV2"
            )
            return

        # Success
        open_tx = None
        if isinstance(open_result, dict):
            open_tx = open_result.get('tx_hash') or open_result.get('txHash') or \
                      open_result.get('signature') or open_result.get('txSignature')
        open_tx_display = f"`{escape_markdown_v2(open_tx[:16])}...`" if open_tx else "_pending_"

        # Format range
        try:
            lower_f = float(lower_price)
            upper_f = float(upper_price)
            decimals = 2 if lower_f >= 1 else 6 if lower_f >= 0.001 else 8
            range_display = f"{lower_f:.{decimals}f} \\- {upper_f:.{decimals}f}"
        except (ValueError, TypeError):
            range_display = f"{lower_price} \\- {upper_price}"

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"✅ *Rebalance Complete*\n\n"
                 f"*{escape_markdown_v2(pair)}*\n"
                 f"Range: {range_display}\n\n"
                 f"New position opened\\!\n"
                 f"Tx: {open_tx_display}",
            parse_mode="MarkdownV2"
        )
        logger.info(f"Auto-rebalanced position {position_address[:8]}")

    except Exception as e:
        logger.error(f"Auto-rebalance failed for {position_address[:8]}: {e}")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ *Auto\\-rebalance failed*\n\n{escape_markdown_v2(str(e)[:100])}",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass


# Mark as continuous routine - has internal loop
CONTINUOUS = True


class Config(BaseModel):
    """Alerts on out-of-range and price changes. Optional auto-close or auto-rebalance when price moves X%."""

    check_interval_sec: int = Field(
        default=60,
        description="How often to check positions (seconds)"
    )
    status_report_hours: float = Field(
        default=4,
        description="Send full status report every N hours (0=off)"
    )

    # Price Change Trigger (set > 0 to enable)
    price_trigger_pct: float = Field(
        default=0,
        description="Alert when price moves N% from start (0=off)"
    )

    # Auto Actions (require price_trigger_pct > 0)
    auto_close: bool = Field(
        default=False,
        description="Auto-CLOSE: withdraw liquidity to wallet on trigger"
    )
    auto_rebalance: bool = Field(
        default=False,
        description="Auto-REBALANCE: close + reopen position at current price"
    )
    auto_action_delay_sec: int = Field(
        default=30,
        description="Seconds before auto action (can cancel)"
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Monitor LP positions and send alerts when they go out of range.

    - Fetches current active LP positions
    - Compares with previous state to detect newly out-of-range positions
    - Sends a single navigable message with Close/Skip buttons
    - Shows summary of all currently out-of-range positions
    """
    logger.info(f"LP Monitor starting with config: interval={config.check_interval_sec}s, report_hours={config.status_report_hours}")

    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    instance_id = getattr(context, '_instance_id', 'default')

    if not chat_id:
        return "No chat_id available"

    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    if not hasattr(client, 'gateway_clmm'):
        return "Gateway CLMM not available"

    # State for tracking
    state = {
        "previous_out_of_range": set(),  # Set of position IDs that were out of range
        "alerts_sent": 0,
        "checks": 0,
        "start_time": time.time(),
        "last_report_time": time.time(),  # Track when last status report was sent
        "alert_message_id": None,  # Track the single alert message for updates
        # Price change trigger tracking
        "initial_prices": {},  # pos_id -> float (captured on first detection)
        "triggered_positions": set(),  # pos_ids that have fired trigger (avoid duplicates)
    }

    # Get user_data reference for storing positions
    user_data = None
    if hasattr(context, 'application') and context.application:
        # application.user_data is a dict-like object keyed by chat_id
        app_user_data = context.application.user_data
        if chat_id in app_user_data:
            user_data = app_user_data[chat_id]
        else:
            # Create new dict and assign it
            app_user_data[chat_id] = {}
            user_data = app_user_data[chat_id]
    elif hasattr(context, '_user_data') and context._user_data is not None:
        user_data = context._user_data

    if user_data is None:
        logger.warning("Could not get user_data reference, positions may not persist")
        user_data = {}

    # Send start notification with retry
    report_info = ""
    if config.status_report_hours > 0:
        if config.status_report_hours >= 1:
            report_info = f"\nStatus report every {config.status_report_hours:.0f}h"
        else:
            mins = int(config.status_report_hours * 60)
            report_info = f"\nStatus report every {mins}m"

    trigger_info = ""
    if config.price_trigger_pct > 0:
        trigger_info = f"\nPrice trigger: {config.price_trigger_pct:.0f}%"
        if config.auto_close:
            trigger_info += f" (auto-close in {config.auto_action_delay_sec}s)"

    for attempt in range(3):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🟢 *LP Monitor Started*\n"
                    f"Checking every {config.check_interval_sec}s for out\\-of\\-range positions"
                    f"{escape_markdown_v2(report_info)}"
                    f"{escape_markdown_v2(trigger_info)}"
                ),
                parse_mode="MarkdownV2"
            )
            break
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error(f"Failed to send start message: {e}")

    try:
        # Main monitoring loop
        while True:
            try:
                state["checks"] += 1

                # Get fresh client each iteration (handles reconnection)
                try:
                    client = await get_client(chat_id, context=context)
                    if not client or not hasattr(client, 'gateway_clmm'):
                        logger.warning("LP Monitor: Client not available, retrying...")
                        await asyncio.sleep(config.check_interval_sec)
                        continue
                except Exception as e:
                    logger.warning(f"LP Monitor: Failed to get client: {e}")
                    await asyncio.sleep(config.check_interval_sec)
                    continue

                # Fetch current positions
                result = await client.gateway_clmm.search_positions(
                    limit=100,
                    offset=0,
                    status="OPEN",
                    refresh=True
                )

                if not result:
                    await asyncio.sleep(config.check_interval_sec)
                    continue

                positions = result.get("data", [])

                # Filter to active positions with liquidity
                active_positions = []
                for pos in positions:
                    if pos.get('status') == 'CLOSED':
                        continue
                    liq = pos.get('liquidity') or pos.get('current_liquidity')
                    if liq is not None:
                        try:
                            if float(liq) <= 0:
                                continue
                        except (ValueError, TypeError):
                            pass
                    active_positions.append(pos)

                # Categorize positions
                current_out_of_range = set()
                newly_out_of_range = []
                still_out_of_range = []
                returned_to_range = []

                # Build a map of position IDs to positions for return detection
                current_positions_map = {}
                for pos in active_positions:
                    pos_id = pos.get('id') or pos.get('position_id') or pos.get('address', '')
                    current_positions_map[pos_id] = pos
                    in_range = pos.get('in_range', '')

                    if in_range == "OUT_OF_RANGE":
                        current_out_of_range.add(pos_id)

                        if pos_id in state["previous_out_of_range"]:
                            still_out_of_range.append(pos)
                        else:
                            newly_out_of_range.append(pos)

                # Check for positions that returned to range
                for pos_id in state["previous_out_of_range"]:
                    if pos_id not in current_out_of_range and pos_id in current_positions_map:
                        returned_to_range.append(current_positions_map[pos_id])

                # Get token cache for symbol resolution
                token_cache = dict(KNOWN_TOKENS)

                # Fetch network tokens for better symbol resolution
                networks = list(set(pos.get('network', 'solana-mainnet-beta') for pos in active_positions))
                if networks and hasattr(client, 'gateway'):
                    for network in networks:
                        try:
                            resp = await client.gateway.get_network_tokens(network)
                            tokens = resp.get('tokens', []) if resp else []
                            for token in tokens:
                                addr = token.get('address', '')
                                symbol = token.get('symbol', '')
                                if addr and symbol:
                                    token_cache[addr] = symbol
                        except Exception:
                            pass

                # Store token cache for callback handlers
                user_data["token_cache"] = token_cache

                # Combine all out-of-range positions
                all_oor = newly_out_of_range + still_out_of_range

                # If there are newly out-of-range positions, send/update alert
                if newly_out_of_range:
                    state["alerts_sent"] += len(newly_out_of_range)

                    # Store all positions in cache for callback handler
                    _store_positions_in_cache(user_data, all_oor, instance_id)

                    # Send or update the alert message
                    state["alert_message_id"] = await _send_or_update_alert(
                        context, chat_id, all_oor, 0, token_cache, instance_id,
                        state["alert_message_id"]
                    )

                # Send alerts for positions that returned to range
                if returned_to_range:
                    for pos in returned_to_range:
                        await _send_return_to_range_alert(
                            context, chat_id, pos, token_cache
                        )

                # Price change trigger logic
                if config.price_trigger_pct > 0:
                    # Capture initial prices for new positions
                    _capture_initial_prices(state, active_positions)

                    # Store initial prices in user_data for status report formatting
                    user_data["initial_prices"] = state["initial_prices"]

                    # Fetch token prices for USD conversion (needed for alerts)
                    token_prices = await _fetch_token_prices(client)

                    # Check for price change triggers
                    triggered = _check_price_triggers(
                        state, active_positions, config.price_trigger_pct
                    )

                    for t in triggered:
                        state["triggered_positions"].add(t["pos_id"])
                        state["alerts_sent"] += 1

                        if config.auto_rebalance:
                            # Start rebalance countdown (close + reopen)
                            asyncio.create_task(_start_auto_action_countdown(
                                context, chat_id, t, config.auto_action_delay_sec,
                                instance_id, user_data, client, token_cache,
                                action="rebalance"
                            ))
                        elif config.auto_close:
                            # Start close countdown (withdraw to wallet)
                            asyncio.create_task(_start_auto_action_countdown(
                                context, chat_id, t, config.auto_action_delay_sec,
                                instance_id, user_data, client, token_cache,
                                action="close"
                            ))
                        else:
                            # Just send trigger alert (manual action)
                            await _send_trigger_alert(
                                context, chat_id, t, token_cache, token_prices,
                                instance_id, user_data
                            )

                # Send periodic status report
                report_interval_sec = config.status_report_hours * 3600
                time_since_report = time.time() - state["last_report_time"]

                # Log every 10 checks to track progress
                if state["checks"] % 10 == 0:
                    logger.info(
                        f"LP Monitor check #{state['checks']}: "
                        f"{len(active_positions)} positions, "
                        f"report in {int(report_interval_sec - time_since_report)}s"
                    )

                if config.status_report_hours > 0 and time_since_report >= report_interval_sec:
                    logger.info(f"LP Monitor: Sending status report ({len(active_positions)} positions)")
                    state["last_report_time"] = time.time()
                    await _send_status_report(
                        context, chat_id, active_positions, token_cache, instance_id, user_data, client
                    )

                # Update state
                state["previous_out_of_range"] = current_out_of_range

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"LP monitor error: {e}", exc_info=True)

            await asyncio.sleep(config.check_interval_sec)

    except asyncio.CancelledError:
        elapsed = int(time.time() - state["start_time"])
        mins, secs = divmod(elapsed, 60)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔴 *LP Monitor Stopped*\n"
                    f"Duration: {mins}m {secs}s \\| Checks: {state['checks']} \\| Alerts: {state['alerts_sent']}"
                ),
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass

        return f"Stopped after {mins}m {secs}s, {state['checks']} checks, {state['alerts_sent']} alerts"


def _store_positions_in_cache(user_data: dict, positions: list, instance_id: str) -> None:
    """Store positions in user_data cache for callback handler access."""
    if "positions_cache" not in user_data:
        user_data["positions_cache"] = {}

    # Store each position with a unique key
    for i, pos in enumerate(positions):
        cache_key = f"lpm_{instance_id}_{i}"
        user_data["positions_cache"][cache_key] = pos


async def _send_or_update_alert(
    context,
    chat_id: int,
    positions: list,
    current_index: int,
    token_cache: dict,
    instance_id: str,
    existing_message_id: int = None
) -> int:
    """Send or update a single alert message with navigation for multiple positions."""
    if not positions:
        return existing_message_id

    # Clamp index to valid range
    current_index = max(0, min(current_index, len(positions) - 1))
    pos = positions[current_index]

    # Build the message for current position
    text = _format_position_alert(pos, token_cache, current_index, len(positions))

    # Build keyboard with navigation and actions
    cache_key = f"lpm_{instance_id}_{current_index}"
    keyboard = []

    # Navigation row (if multiple positions)
    if len(positions) > 1:
        nav_row = []
        if current_index > 0:
            nav_row.append(InlineKeyboardButton(
                "◀️ Prev",
                callback_data=f"dex:lpm_nav:{instance_id}:{current_index - 1}"
            ))
        nav_row.append(InlineKeyboardButton(
            f"{current_index + 1}/{len(positions)}",
            callback_data="dex:lpm_noop"
        ))
        if current_index < len(positions) - 1:
            nav_row.append(InlineKeyboardButton(
                "Next ▶️",
                callback_data=f"dex:lpm_nav:{instance_id}:{current_index + 1}"
            ))
        keyboard.append(nav_row)

    # Action row
    keyboard.append([
        InlineKeyboardButton("❌ Close", callback_data=f"dex:pos_close:{cache_key}"),
        InlineKeyboardButton("⏭ Skip", callback_data=f"dex:lpm_skip:{cache_key}"),
        InlineKeyboardButton("✅ Dismiss", callback_data=f"dex:lpm_dismiss:{instance_id}"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Try to update existing message first
    if existing_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=existing_message_id,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            return existing_message_id
        except Exception as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str:
                return existing_message_id  # No change needed
            elif "message to edit not found" not in err_str:
                logger.debug(f"Could not edit message: {e}")
            # Fall through to send new message

    # Send new message with retry
    for attempt in range(3):
        try:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            return msg.message_id
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error(f"Failed to send position alert: {e}")
                return existing_message_id

    return existing_message_id


def _format_position_alert(pos: dict, token_cache: dict, index: int, total: int) -> str:
    """Format a position for the alert message."""
    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')

    # Get price info
    lower = pos.get('lower_price', pos.get('price_lower', ''))
    upper = pos.get('upper_price', pos.get('price_upper', ''))
    current = pos.get('current_price', '')

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
            range_str = f"Range: {lower_f:.{decimals}f} - {upper_f:.{decimals}f}"
        except (ValueError, TypeError):
            range_str = f"Range: {lower} - {upper}"

    current_str = ""
    direction = ""
    if current:
        try:
            current_f = float(current)
            lower_f = float(lower) if lower else 0
            upper_f = float(upper) if upper else 0

            if current_f >= 1:
                current_str = f"Current: {current_f:.2f}"
            elif current_f >= 0.001:
                current_str = f"Current: {current_f:.6f}"
            else:
                current_str = f"Current: {current_f:.8f}"

            # Determine direction
            if current_f < lower_f:
                direction = "▼ Below range"
            elif current_f > upper_f:
                direction = "▲ Above range"
        except (ValueError, TypeError):
            current_str = f"Current: {current}"

    # Get position value
    pnl_summary = pos.get('pnl_summary', {})
    value = pnl_summary.get('current_lp_value_quote', 0)
    value_str = ""
    if value:
        try:
            value_f = float(value)
            value_str = f"Value: {value_f:.2f} {quote_symbol}"
        except (ValueError, TypeError):
            pass

    # Build message
    header = f"🚨 *Out of Range* \\({index + 1}/{total}\\)" if total > 1 else "🚨 *Position Out of Range*"
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

    return "\n".join(lines)


async def _send_return_to_range_alert(
    context,
    chat_id: int,
    pos: dict,
    token_cache: dict
) -> None:
    """Send an alert for a position that returned to range."""
    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')

    # Get price info
    current = pos.get('current_price', '')

    current_str = ""
    if current:
        try:
            current_f = float(current)
            if current_f >= 1:
                current_str = f"Current: {current_f:.2f}"
            elif current_f >= 0.001:
                current_str = f"Current: {current_f:.6f}"
            else:
                current_str = f"Current: {current_f:.8f}"
        except (ValueError, TypeError):
            current_str = f"Current: {current}"

    # Build message
    lines = [
        "🟢 *Position Back in Range*",
        "",
        f"*{escape_markdown_v2(pair)}* \\({escape_markdown_v2(connector)}\\)",
    ]

    if current_str:
        lines.append(escape_markdown_v2(current_str))

    text = "\n".join(lines)

    for attempt in range(3):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="MarkdownV2"
            )
            return
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error(f"Failed to send return alert: {e}")


async def _send_status_report(
    context,
    chat_id: int,
    positions: list,
    token_cache: dict,
    instance_id: str,
    user_data: dict,
    client=None
) -> None:
    """Send a periodic status report of all positions with navigation."""
    if not positions:
        for attempt in range(3):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="📊 *LP Monitor Status*\n\nNo active positions found\\.",
                    parse_mode="MarkdownV2"
                )
                return
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    logger.error(f"Failed to send status report: {e}")
        return

    # Fetch token prices for USD conversion
    token_prices = {}
    if client:
        token_prices = await _fetch_token_prices(client)

    # Categorize positions
    in_range = []
    out_of_range = []
    for pos in positions:
        if pos.get('in_range', '') == "OUT_OF_RANGE":
            out_of_range.append(pos)
        else:
            in_range.append(pos)

    # Calculate totals
    total_value_usd = 0
    total_pnl_usd = 0
    total_fees_usd = 0
    for pos in positions:
        base_token = pos.get('base_token', pos.get('token_a', ''))
        quote_token = pos.get('quote_token', pos.get('token_b', ''))
        base_symbol = resolve_token_symbol(base_token, token_cache)
        quote_symbol = resolve_token_symbol(quote_token, token_cache)
        quote_price = _get_price(quote_symbol, token_prices, 1.0)
        base_price = _get_price(base_symbol, token_prices, 0)

        pnl_summary = pos.get('pnl_summary', {})
        total_pnl_usd += float(pnl_summary.get('total_pnl_quote', 0) or 0) * quote_price
        total_value_usd += float(pnl_summary.get('current_lp_value_quote', 0) or 0) * quote_price

        base_fee = float(pos.get('base_fee_pending', 0) or 0)
        quote_fee = float(pos.get('quote_fee_pending', 0) or 0)
        total_fees_usd += (base_fee * base_price) + (quote_fee * quote_price)

    # Build header
    pnl_sign = "\\+" if total_pnl_usd >= 0 else "\\-"
    lines = [
        f"📊 *LP Monitor Status* \\({len(positions)} positions\\)",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Value: ${escape_markdown_v2(f'{total_value_usd:.2f}')} \\| "
        f"PnL: {pnl_sign}${escape_markdown_v2(f'{abs(total_pnl_usd):.2f}')} \\| "
        f"🎁 ${escape_markdown_v2(f'{total_fees_usd:.2f}')}",
        f"✅ {len(in_range)} in range \\| ⚠️ {len(out_of_range)} out of range",
        "",
    ]

    # Format each position
    initial_prices = user_data.get("initial_prices", {})
    for i, pos in enumerate(positions, 1):
        line = _format_compact_position(pos, token_cache, token_prices, index=i, initial_prices=initial_prices)
        lines.append(line)

    text = "\n".join(lines)

    # Store ALL positions in cache for navigation
    _store_positions_in_cache(user_data, positions, instance_id)
    user_data["token_prices"] = token_prices

    # Build keyboard with navigation to first position
    keyboard = []
    if positions:
        keyboard.append([
            InlineKeyboardButton(
                f"📋 Manage Positions ({len(positions)})",
                callback_data=f"dex:lpm_detail:{instance_id}:0"
            )
        ])

    if out_of_range:
        keyboard.append([
            InlineKeyboardButton(
                f"⚠️ View {len(out_of_range)} Out of Range",
                callback_data=f"dex:lpm_oor:{instance_id}:0"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("💰 Collect All Fees", callback_data=f"dex:lpm_collect_all:{instance_id}"),
        InlineKeyboardButton("✅ Dismiss", callback_data=f"dex:lpm_dismiss:{instance_id}"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Retry up to 3 times with backoff
    for attempt in range(3):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            logger.info(f"LP Monitor: Status report sent successfully ({len(positions)} positions)")
            return  # Success
        except Exception as e:
            if attempt < 2:
                logger.warning(f"Status report attempt {attempt + 1} failed, retrying: {e}")
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error(f"Failed to send status report after 3 attempts: {e}")


def format_position_detail_view(
    pos: dict,
    token_cache: dict,
    token_prices: dict,
    index: int,
    total: int,
    instance_id: str
) -> tuple[str, InlineKeyboardMarkup]:
    """Format detailed position view with action buttons.

    Returns (text, reply_markup) tuple.
    """
    # Resolve token symbols
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    connector = pos.get('connector', 'unknown')
    pool_id = pos.get('pool_id', pos.get('pool_address', ''))[:8] if pos.get('pool_id') or pos.get('pool_address') else ''

    # In-range status
    in_range = pos.get('in_range', '')
    if in_range == "IN_RANGE":
        status = "🟢 In Range"
    elif in_range == "OUT_OF_RANGE":
        status = "🔴 Out of Range"
    else:
        status = "⚪ Unknown"

    # Price range
    lower = pos.get('lower_price', pos.get('price_lower', 0))
    upper = pos.get('upper_price', pos.get('price_upper', 0))
    current = pos.get('current_price', 0)

    try:
        lower_f = float(lower)
        upper_f = float(upper)
        current_f = float(current) if current else 0
        decimals = 2 if lower_f >= 1 else 6 if lower_f >= 0.001 else 8
        # Format numbers and escape them
        lower_str = escape_markdown_v2(f"{lower_f:.{decimals}f}")
        upper_str = escape_markdown_v2(f"{upper_f:.{decimals}f}")
        range_str = f"{lower_str} \\- {upper_str}"
        current_str = escape_markdown_v2(f"{current_f:.{decimals}f}") if current_f else "N/A"

        # Position indicator
        if current_f and lower_f and upper_f:
            if current_f < lower_f:
                indicator = "▼ Below"
            elif current_f > upper_f:
                indicator = "▲ Above"
            else:
                pct = (current_f - lower_f) / (upper_f - lower_f)
                bar_len = 10
                filled = int(pct * bar_len)
                indicator = f"[{'█' * filled}{'░' * (bar_len - filled)}]"
        else:
            indicator = ""
    except (ValueError, TypeError):
        range_str = f"{escape_markdown_v2(str(lower))} \\- {escape_markdown_v2(str(upper))}"
        current_str = escape_markdown_v2(str(current)) if current else "N/A"
        indicator = ""

    # Get prices
    quote_price = _get_price(quote_symbol, token_prices, 1.0)
    base_price = _get_price(base_symbol, token_prices, 0)

    # PnL and value
    pnl_summary = pos.get('pnl_summary', {})
    total_pnl = float(pnl_summary.get('total_pnl_quote', 0) or 0)
    lp_value = float(pnl_summary.get('current_lp_value_quote', 0) or 0)

    pnl_usd = total_pnl * quote_price
    value_usd = lp_value * quote_price

    # Amounts
    base_amount = float(pos.get('base_token_amount', pos.get('amount_a', 0)) or 0)
    quote_amount = float(pos.get('quote_token_amount', pos.get('amount_b', 0)) or 0)

    # Fees
    base_fee = float(pos.get('base_fee_pending', 0) or 0)
    quote_fee = float(pos.get('quote_fee_pending', 0) or 0)
    fees_usd = (base_fee * base_price) + (quote_fee * quote_price)

    # Build message
    pnl_sign = "\\+" if pnl_usd >= 0 else "\\-"
    lines = [
        f"📊 *Position {index + 1}/{total}*",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"*{escape_markdown_v2(pair)}* \\| {escape_markdown_v2(connector)}",
        f"Status: {status}",
        "",
        f"📍 *Price Range*",
        f"   {range_str}",  # Already escaped
        f"   Current: {current_str} {indicator}",  # Already escaped
        "",
        f"💰 *Value & PnL*",
        f"   Value: ${escape_markdown_v2(f'{value_usd:.2f}')}",
        f"   PnL: {pnl_sign}${escape_markdown_v2(f'{abs(pnl_usd):.2f}')}",
        "",
        f"📦 *Amounts*",
        f"   {escape_markdown_v2(f'{base_amount:.6f}')} {escape_markdown_v2(base_symbol)}",
        f"   {escape_markdown_v2(f'{quote_amount:.6f}')} {escape_markdown_v2(quote_symbol)}",
        "",
        f"🎁 *Pending Fees*: ${escape_markdown_v2(f'{fees_usd:.2f}')}",
        f"   {escape_markdown_v2(f'{base_fee:.6f}')} {escape_markdown_v2(base_symbol)}",
        f"   {escape_markdown_v2(f'{quote_fee:.6f}')} {escape_markdown_v2(quote_symbol)}",
    ]

    text = "\n".join(lines)

    # Cache key for this position
    cache_key = f"lpm_{instance_id}_{index}"

    # Build keyboard
    keyboard = []

    # Navigation row
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"dex:lpm_detail:{instance_id}:{index - 1}"))
    nav_row.append(InlineKeyboardButton(f"{index + 1}/{total}", callback_data="dex:noop"))
    if index < total - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"dex:lpm_detail:{instance_id}:{index + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    # Action buttons
    keyboard.append([
        InlineKeyboardButton("💰 Collect Fees", callback_data=f"dex:lpm_collect:{cache_key}"),
        InlineKeyboardButton("❌ Close", callback_data=f"dex:pos_close:{cache_key}"),
    ])

    keyboard.append([
        InlineKeyboardButton("🔄 Rebalance", callback_data=f"dex:lpm_rebalance:{cache_key}"),
    ])

    keyboard.append([
        InlineKeyboardButton("« Back to List", callback_data=f"dex:lpm_dismiss:{instance_id}"),
    ])

    return text, InlineKeyboardMarkup(keyboard)
