"""Monitor LP positions and alert when they go out of range."""

import asyncio
import logging
import time
from pydantic import BaseModel, Field
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from servers import get_client
from utils.telegram_formatters import escape_markdown_v2, resolve_token_symbol, KNOWN_TOKENS

logger = logging.getLogger(__name__)

# Mark as continuous routine - has internal loop
CONTINUOUS = True


class Config(BaseModel):
    """Monitor LP positions for out-of-range alerts."""

    interval_sec: int = Field(default=60, description="Check interval in seconds")
    alert_on_return: bool = Field(default=True, description="Alert when position returns to range")
    summary_interval: int = Field(default=10, description="Show summary every N checks (0=disabled)")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Monitor LP positions and send alerts when they go out of range.

    - Fetches current active LP positions
    - Compares with previous state to detect newly out-of-range positions
    - Sends a single navigable message with Close/Skip buttons
    - Shows summary of all currently out-of-range positions
    """
    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    instance_id = getattr(context, '_instance_id', 'default')

    if not chat_id:
        return "No chat_id available"

    client = await get_client(chat_id)
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
        "alert_message_id": None,  # Track the single alert message for updates
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

    # Send start notification
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🟢 *LP Monitor Started*\n"
                f"Checking every {config.interval_sec}s for out\\-of\\-range positions"
            ),
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Failed to send start message: {e}")

    try:
        # Main monitoring loop
        while True:
            try:
                state["checks"] += 1

                # Fetch current positions
                result = await client.gateway_clmm.search_positions(
                    limit=100,
                    offset=0,
                    status="OPEN",
                    refresh=True
                )

                if not result:
                    await asyncio.sleep(config.interval_sec)
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
                if config.alert_on_return and returned_to_range:
                    for pos in returned_to_range:
                        await _send_return_to_range_alert(
                            context, chat_id, pos, token_cache
                        )

                # Periodically show summary of all out-of-range positions
                if config.summary_interval > 0 and state["checks"] % config.summary_interval == 0:
                    if all_oor and not newly_out_of_range:  # Don't duplicate if we just sent an alert
                        _store_positions_in_cache(user_data, all_oor, instance_id)
                        state["alert_message_id"] = await _send_or_update_alert(
                            context, chat_id, all_oor, 0, token_cache, instance_id,
                            state["alert_message_id"]
                        )

                # Update state
                state["previous_out_of_range"] = current_out_of_range

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"LP monitor error: {e}", exc_info=True)

            await asyncio.sleep(config.interval_sec)

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

    try:
        if existing_message_id:
            # Try to update existing message
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
                # Message might have been deleted or not modified
                if "message to edit not found" in err_str:
                    pass  # Will send new message below
                elif "message is not modified" in err_str:
                    return existing_message_id  # No change needed
                else:
                    logger.debug(f"Could not edit message: {e}")

        # Send new message
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        return msg.message_id

    except Exception as e:
        logger.error(f"Failed to send position alert: {e}")
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

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Failed to send return alert: {e}")
