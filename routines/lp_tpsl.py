"""LP Position TP/SL Monitor - Multi-position Take Profit and Stop Loss with runtime editing."""

import asyncio
import logging
import re
import time
from typing import NamedTuple, Literal
from pydantic import BaseModel, Field
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config_manager import get_client
from utils.telegram_formatters import escape_markdown_v2, resolve_token_symbol, KNOWN_TOKENS

logger = logging.getLogger(__name__)

# Mark as continuous routine - has internal loop
CONTINUOUS = True

# Message states this routine handles (for generic routing)
MESSAGE_STATES = ["tpsl_interactive"]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

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


def _calculate_position_value_usd(pos: dict, token_cache: dict, token_prices: dict) -> float:
    """Calculate position value in USD."""
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    quote_symbol = resolve_token_symbol(quote_token, token_cache)

    quote_price = _get_price(quote_symbol, token_prices, 1.0)

    pnl_summary = pos.get('pnl_summary', {})
    current_lp_value_quote = float(pnl_summary.get('current_lp_value_quote', 0) or 0)

    return current_lp_value_quote * quote_price


def _get_user_data(context) -> dict:
    """Get user_data from MockContext or regular context."""
    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    if hasattr(context, 'application') and context.application:
        app_user_data = context.application.user_data
        if chat_id not in app_user_data:
            app_user_data[chat_id] = {}
        return app_user_data[chat_id]
    return getattr(context, '_user_data', {})


def _get_state(context, instance_id: str) -> dict:
    """Get or initialize the TP/SL state for this instance."""
    user_data = _get_user_data(context)
    key = f"lp_tpsl_{instance_id}"
    if key not in user_data:
        user_data[key] = {
            "mode": "setup",
            "available_positions": [],
            "tracked_positions": {},
            "global_defaults": {"tp_pct": 10.0, "sl_pct": 10.0},
            "token_cache": {},
            "checks": 0,
            "start_time": time.time(),
            "last_check": None,
            "status_msg_id": None,
        }
    return user_data[key]


async def _delete_after(msg, seconds: int):
    """Delete a message after a delay."""
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception:
        pass


# =============================================================================
# COMMAND PARSING
# =============================================================================

class ParsedCommand(NamedTuple):
    type: Literal["select", "tp", "sl", "remove", "add", "status", "help", "unknown"]
    position_num: int | None
    value: float | None
    value_type: str | None  # "pct" or "usd"


def parse_tpsl_command(text: str) -> ParsedCommand:
    """Parse runtime TP/SL command from user message."""
    text = text.strip().lower()

    # Position selection: just a number like "1" or "2"
    if text.isdigit():
        return ParsedCommand("select", int(text), None, None)

    # Status command
    if text in ("status", "s", "list", "ls"):
        return ParsedCommand("status", None, None, None)

    # Add command
    if text in ("add", "a", "+"):
        return ParsedCommand("add", None, None, None)

    # Help command
    if text in ("help", "h", "?"):
        return ParsedCommand("help", None, None, None)

    # Remove command: "remove 2" or "rm 2" or "del 2" or "-2"
    remove_match = re.match(r"(?:remove|rm|del|-)\s*(\d+)", text)
    if remove_match:
        return ParsedCommand("remove", int(remove_match.group(1)), None, None)

    # TP/SL with optional position: "1 tp=25%" or "tp=15%" or "sl=$50"
    tpsl_match = re.match(
        r"(?:(\d+)\s+)?(tp|sl)\s*=\s*(\$?)(\d+(?:\.\d+)?)(%?)",
        text
    )
    if tpsl_match:
        pos_num = int(tpsl_match.group(1)) if tpsl_match.group(1) else None
        cmd_type = tpsl_match.group(2)  # "tp" or "sl"
        is_usd = tpsl_match.group(3) == "$"
        value = float(tpsl_match.group(4))
        is_pct = tpsl_match.group(5) == "%"

        value_type = "usd" if is_usd else "pct"
        return ParsedCommand(cmd_type, pos_num, value, value_type)

    return ParsedCommand("unknown", None, None, None)


# =============================================================================
# MESSAGE HANDLER (called from handlers/routines/__init__.py)
# =============================================================================

async def handle_tpsl_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process interactive commands while routine is running."""
    text = update.message.text.strip()
    instance_id = context.user_data.get("tpsl_active_instance")

    if not instance_id:
        return

    state = _get_state(context, instance_id)
    cmd = parse_tpsl_command(text)

    # Delete user message for cleaner interface
    try:
        await update.message.delete()
    except Exception:
        pass

    chat_id = update.effective_chat.id

    if cmd.type == "select":
        await _handle_position_select(context, chat_id, state, cmd.position_num, instance_id)
    elif cmd.type == "tp":
        await _handle_set_tp(context, chat_id, state, cmd, instance_id)
    elif cmd.type == "sl":
        await _handle_set_sl(context, chat_id, state, cmd, instance_id)
    elif cmd.type == "remove":
        await _handle_remove_position(context, chat_id, state, cmd.position_num, instance_id)
    elif cmd.type == "add":
        await _show_available_positions(context, chat_id, state, instance_id)
    elif cmd.type == "status":
        await _show_status(context, chat_id, state, instance_id)
    elif cmd.type == "help":
        await _show_help(context, chat_id)
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="Unknown command\\. Send `help` for available commands\\.",
            parse_mode="MarkdownV2"
        )
        asyncio.create_task(_delete_after(msg, 5))


# =============================================================================
# EXPORTED HANDLERS (for generic routing)
# =============================================================================


async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    params: list[str],
) -> None:
    """
    Handle routine-specific callbacks.

    Callback patterns:
    - routines:lp_tpsl:continue:{instance_id}:{pos_id_short}
    - routines:lp_tpsl:remove:{instance_id}:{pos_id_short}
    """
    query = update.callback_query

    if action == "continue" and len(params) >= 2:
        instance_id, pos_id_short = params[0], params[1]
        await query.answer("Continuing to monitor...")
        state_key = f"lp_tpsl_{instance_id}"
        if state_key in context.user_data:
            state = context.user_data[state_key]
            for pid, pdata in state.get("tracked_positions", {}).items():
                if pid.startswith(pos_id_short) or pid[:8] == pos_id_short:
                    pdata["triggered"] = None
                    break
        try:
            await query.message.delete()
        except Exception:
            pass

    elif action == "remove" and len(params) >= 2:
        instance_id, pos_id_short = params[0], params[1]
        await query.answer("Removed from monitor")
        state_key = f"lp_tpsl_{instance_id}"
        if state_key in context.user_data:
            state = context.user_data[state_key]
            tracked = state.get("tracked_positions", {})
            for pid in list(tracked.keys()):
                if pid.startswith(pos_id_short) or pid[:8] == pos_id_short:
                    del tracked[pid]
                    break
        try:
            await query.message.delete()
        except Exception:
            pass

    else:
        await query.answer()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle messages when routines_state is 'tpsl_interactive'."""
    instance_id = context.user_data.get("tpsl_active_instance")
    if not instance_id:
        return False

    await handle_tpsl_message(update, context)
    return True


async def cleanup(context: ContextTypes.DEFAULT_TYPE, instance_id: str, chat_id: int) -> None:
    """Clean up routine state when instance stops."""
    user_data = _get_user_data(context)

    # Clear interactive state if this instance was active
    if user_data.get("tpsl_active_instance") == instance_id:
        user_data.pop("routines_state", None)
        user_data.pop("tpsl_active_instance", None)

    # Clear instance-specific state
    user_data.pop(f"lp_tpsl_{instance_id}", None)


async def _handle_position_select(context, chat_id: int, state: dict, pos_num: int, instance_id: str):
    """Handle position selection by number."""
    available = state.get("available_positions", [])

    if pos_num < 1 or pos_num > len(available):
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"Invalid position number\\. Choose 1\\-{len(available)}\\.",
            parse_mode="MarkdownV2"
        )
        asyncio.create_task(_delete_after(msg, 5))
        return

    pos = available[pos_num - 1]
    pos_id = pos.get('id') or pos.get('position_id') or pos.get('address', '')

    # Check if already tracked
    if pos_id in state["tracked_positions"]:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="Position already being tracked\\!",
            parse_mode="MarkdownV2"
        )
        asyncio.create_task(_delete_after(msg, 3))
        return

    # Get position info
    token_cache = state.get("token_cache", {})
    token_prices = state.get("token_prices", {})
    base_token = pos.get('base_token', pos.get('token_a', ''))
    quote_token = pos.get('quote_token', pos.get('token_b', ''))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"
    connector = pos.get('connector', 'unknown')
    network = pos.get('network', 'solana-mainnet-beta')

    entry_value = _calculate_position_value_usd(pos, token_cache, token_prices)

    # Get defaults
    defaults = state.get("global_defaults", {})
    tp_pct = defaults.get("tp_pct", 10.0)
    sl_pct = defaults.get("sl_pct", 10.0)

    # Add to tracked positions
    state["tracked_positions"][pos_id] = {
        "position_id": pos_id,
        "pair": pair,
        "connector": connector,
        "network": network,
        "base_token": base_token,
        "quote_token": quote_token,
        "entry_value_usd": entry_value,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "triggered": None,
        "current_value_usd": entry_value,
        "high_value_usd": entry_value,
        "low_value_usd": entry_value,
        "added_at": time.time(),
        "display_num": len(state["tracked_positions"]) + 1,
    }

    # Calculate TP/SL values
    tp_value = entry_value * (1 + tp_pct / 100)
    sl_value = entry_value * (1 - sl_pct / 100)

    # Store position for close button
    user_data = _get_user_data(context)
    if "positions_cache" not in user_data:
        user_data["positions_cache"] = {}
    cache_key = f"tpsl_{instance_id}_{pos_id[:8]}"
    user_data["positions_cache"][cache_key] = pos

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"\\#️⃣ *Added Position \\#{len(state['tracked_positions'])}*\n\n"
            f"*{escape_markdown_v2(pair)}* \\({escape_markdown_v2(connector)}\\)\n"
            f"Entry: ${escape_markdown_v2(f'{entry_value:.2f}')}\n\n"
            f"📈 TP: \\+{escape_markdown_v2(f'{tp_pct:.0f}')}% \\(${escape_markdown_v2(f'{tp_value:.2f}')}\\)\n"
            f"📉 SL: \\-{escape_markdown_v2(f'{sl_pct:.0f}')}% \\(${escape_markdown_v2(f'{sl_value:.2f}')}\\)\n\n"
            f"_Send another number to add more, or `status` to view all_"
        ),
        parse_mode="MarkdownV2"
    )


async def _handle_set_tp(context, chat_id: int, state: dict, cmd: ParsedCommand, instance_id: str):
    """Handle setting take profit."""
    tracked = state.get("tracked_positions", {})

    if not tracked:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="No positions tracked yet\\. Add one first\\!",
            parse_mode="MarkdownV2"
        )
        asyncio.create_task(_delete_after(msg, 5))
        return

    value = cmd.value
    value_type = cmd.value_type

    if cmd.position_num:
        # Update specific position
        pos_list = list(tracked.values())
        if cmd.position_num < 1 or cmd.position_num > len(pos_list):
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"Invalid position number\\. Choose 1\\-{len(pos_list)}\\.",
                parse_mode="MarkdownV2"
            )
            asyncio.create_task(_delete_after(msg, 5))
            return

        pos_data = pos_list[cmd.position_num - 1]
        if value_type == "usd":
            # Convert USD to percentage
            entry = pos_data["entry_value_usd"]
            if entry > 0:
                pos_data["tp_pct"] = ((value - entry) / entry) * 100
        else:
            pos_data["tp_pct"] = value

        tp_value = pos_data["entry_value_usd"] * (1 + pos_data["tp_pct"] / 100)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"\\#️⃣{cmd.position_num} *{escape_markdown_v2(pos_data['pair'])}* TP updated to \\+{escape_markdown_v2(f'{pos_data['tp_pct']:.1f}')}% \\(${escape_markdown_v2(f'{tp_value:.2f}')}\\)",
            parse_mode="MarkdownV2"
        )
    else:
        # Update all positions
        for pos_data in tracked.values():
            if value_type == "usd":
                entry = pos_data["entry_value_usd"]
                if entry > 0:
                    pos_data["tp_pct"] = ((value - entry) / entry) * 100
            else:
                pos_data["tp_pct"] = value

        # Also update default
        if value_type == "pct":
            state["global_defaults"]["tp_pct"] = value

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"All positions TP updated to \\+{escape_markdown_v2(f'{value:.1f}')}{'%' if value_type == 'pct' else ''}",
            parse_mode="MarkdownV2"
        )


async def _handle_set_sl(context, chat_id: int, state: dict, cmd: ParsedCommand, instance_id: str):
    """Handle setting stop loss."""
    tracked = state.get("tracked_positions", {})

    if not tracked:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="No positions tracked yet\\. Add one first\\!",
            parse_mode="MarkdownV2"
        )
        asyncio.create_task(_delete_after(msg, 5))
        return

    value = cmd.value
    value_type = cmd.value_type

    if cmd.position_num:
        # Update specific position
        pos_list = list(tracked.values())
        if cmd.position_num < 1 or cmd.position_num > len(pos_list):
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"Invalid position number\\. Choose 1\\-{len(pos_list)}\\.",
                parse_mode="MarkdownV2"
            )
            asyncio.create_task(_delete_after(msg, 5))
            return

        pos_data = pos_list[cmd.position_num - 1]
        if value_type == "usd":
            # Convert USD to percentage (SL is a loss, so negative)
            entry = pos_data["entry_value_usd"]
            if entry > 0:
                pos_data["sl_pct"] = ((entry - value) / entry) * 100
        else:
            pos_data["sl_pct"] = value

        sl_value = pos_data["entry_value_usd"] * (1 - pos_data["sl_pct"] / 100)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"\\#️⃣{cmd.position_num} *{escape_markdown_v2(pos_data['pair'])}* SL updated to \\-{escape_markdown_v2(f'{pos_data['sl_pct']:.1f}')}% \\(${escape_markdown_v2(f'{sl_value:.2f}')}\\)",
            parse_mode="MarkdownV2"
        )
    else:
        # Update all positions
        for pos_data in tracked.values():
            if value_type == "usd":
                entry = pos_data["entry_value_usd"]
                if entry > 0:
                    pos_data["sl_pct"] = ((entry - value) / entry) * 100
            else:
                pos_data["sl_pct"] = value

        # Also update default
        if value_type == "pct":
            state["global_defaults"]["sl_pct"] = value

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"All positions SL updated to \\-{escape_markdown_v2(f'{value:.1f}')}{'%' if value_type == 'pct' else ''}",
            parse_mode="MarkdownV2"
        )


async def _handle_remove_position(context, chat_id: int, state: dict, pos_num: int, instance_id: str):
    """Handle removing a position from tracking."""
    tracked = state.get("tracked_positions", {})

    if not tracked:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="No positions tracked\\.",
            parse_mode="MarkdownV2"
        )
        asyncio.create_task(_delete_after(msg, 3))
        return

    pos_list = list(tracked.items())
    if pos_num < 1 or pos_num > len(pos_list):
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"Invalid position number\\. Choose 1\\-{len(pos_list)}\\.",
            parse_mode="MarkdownV2"
        )
        asyncio.create_task(_delete_after(msg, 5))
        return

    pos_id, pos_data = pos_list[pos_num - 1]
    pair = pos_data.get("pair", "Unknown")

    del state["tracked_positions"][pos_id]

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Removed *{escape_markdown_v2(pair)}* from tracking\\.",
        parse_mode="MarkdownV2"
    )


async def _show_available_positions(context, chat_id: int, state: dict, instance_id: str):
    """Show available positions for adding."""
    available = state.get("available_positions", [])
    tracked = state.get("tracked_positions", {})
    token_cache = state.get("token_cache", {})
    token_prices = state.get("token_prices", {})

    if not available:
        await context.bot.send_message(
            chat_id=chat_id,
            text="No LP positions available\\.",
            parse_mode="MarkdownV2"
        )
        return

    lines = ["📋 *Available Positions*\n"]

    for i, pos in enumerate(available, 1):
        pos_id = pos.get('id') or pos.get('position_id') or pos.get('address', '')
        is_tracked = pos_id in tracked

        base_token = pos.get('base_token', pos.get('token_a', ''))
        quote_token = pos.get('quote_token', pos.get('token_b', ''))
        base_symbol = resolve_token_symbol(base_token, token_cache)
        quote_symbol = resolve_token_symbol(quote_token, token_cache)
        pair = f"{base_symbol}-{quote_symbol}"

        connector = pos.get('connector', 'unknown')[:3]
        in_range = pos.get('in_range', '')
        status_emoji = "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else "⚪"

        value = _calculate_position_value_usd(pos, token_cache, token_prices)

        tracked_mark = " ✓" if is_tracked else ""
        line = f"{i}\\. {escape_markdown_v2(pair)} \\({escape_markdown_v2(connector)}\\) {status_emoji} ${escape_markdown_v2(f'{value:.2f}')}{tracked_mark}"
        lines.append(line)

    lines.append("")
    lines.append("_Send position number to add \\(e\\.g\\. '1'\\)_")

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="MarkdownV2"
    )


async def _show_status(context, chat_id: int, state: dict, instance_id: str):
    """Show current status of tracked positions."""
    tracked = state.get("tracked_positions", {})

    if not tracked:
        await context.bot.send_message(
            chat_id=chat_id,
            text="No positions being tracked\\. Send a position number to add one\\.",
            parse_mode="MarkdownV2"
        )
        return

    lines = [f"🎯 *Tracking {len(tracked)} position{'s' if len(tracked) > 1 else ''}*\n"]

    for i, (pos_id, pos_data) in enumerate(tracked.items(), 1):
        pair = pos_data.get("pair", "Unknown")
        connector = pos_data.get("connector", "?")[:3]
        entry = pos_data.get("entry_value_usd", 0)
        current = pos_data.get("current_value_usd", entry)
        tp_pct = pos_data.get("tp_pct", 0)
        sl_pct = pos_data.get("sl_pct", 0)
        triggered = pos_data.get("triggered")

        change_pct = ((current - entry) / entry * 100) if entry > 0 else 0
        change_str = f"+{change_pct:.1f}%" if change_pct >= 0 else f"{change_pct:.1f}%"

        tp_value = entry * (1 + tp_pct / 100)
        sl_value = entry * (1 - sl_pct / 100)

        status = ""
        if triggered == "TP":
            status = " 🎯 TP HIT"
        elif triggered == "SL":
            status = " 🚨 SL HIT"

        lines.append(f"*{i}\\. {escape_markdown_v2(pair)}* \\({escape_markdown_v2(connector)}\\){status}")
        lines.append(f"   Entry: ${escape_markdown_v2(f'{entry:.2f}')} → ${escape_markdown_v2(f'{current:.2f}')} \\({escape_markdown_v2(change_str)}\\)")
        lines.append(f"   📈 TP: \\+{escape_markdown_v2(f'{tp_pct:.0f}')}% \\(${escape_markdown_v2(f'{tp_value:.2f}')}\\)")
        lines.append(f"   📉 SL: \\-{escape_markdown_v2(f'{sl_pct:.0f}')}% \\(${escape_markdown_v2(f'{sl_value:.2f}')}\\)")
        lines.append("")

    # Show commands reminder
    lines.append("_Commands: `tp=15%`, `1 sl=$50`, `remove 1`, `add`_")

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="MarkdownV2"
    )


async def _show_help(context, chat_id: int):
    """Show help message."""
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🎯 *LP TP/SL Commands*\n\n"
            "`1`, `2`, `3` \\- Add position by number\n"
            "`tp=15%` \\- Set TP for all positions\n"
            "`sl=10%` \\- Set SL for all positions\n"
            "`sl=$50` \\- Set SL to $50 for all\n"
            "`1 tp=25%` \\- Set TP for position \\#1\n"
            "`2 sl=$100` \\- Set SL for position \\#2\n"
            "`remove 1` \\- Stop tracking position \\#1\n"
            "`add` \\- Show available positions\n"
            "`status` \\- Show current status\n"
        ),
        parse_mode="MarkdownV2"
    )


# =============================================================================
# MAIN ROUTINE
# =============================================================================

class Config(BaseModel):
    """Multi-position LP TP/SL Monitor with interactive control."""

    default_tp_pct: float = Field(default=10.0, description="Default take profit % (can override per position)")
    default_sl_pct: float = Field(default=10.0, description="Default stop loss % (can override per position)")
    interval_sec: int = Field(default=30, description="Check interval in seconds")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Multi-position LP TP/SL Monitor with interactive control.

    - Start with no positions tracked
    - Send position numbers to add positions
    - Send 'tp=15%' or 'sl=$50' to modify TP/SL
    - Alerts when TP or SL is hit
    """
    logger.info(f"LP TP/SL starting: TP={config.default_tp_pct}%, SL={config.default_sl_pct}%")

    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
    instance_id = getattr(context, '_instance_id', 'default')

    if not chat_id:
        return "No chat_id available"

    # Get user_data and initialize state
    user_data = _get_user_data(context)
    state = _get_state(context, instance_id)
    state["global_defaults"]["tp_pct"] = config.default_tp_pct
    state["global_defaults"]["sl_pct"] = config.default_sl_pct

    # Enable interactive mode - this routes messages to our handler
    user_data["routines_state"] = "tpsl_interactive"
    user_data["tpsl_active_instance"] = instance_id

    # Get client
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    if not hasattr(client, 'gateway_clmm'):
        return "Gateway CLMM not available"

    # Fetch available positions
    await _refresh_available_positions(context, chat_id, state, client)

    if not state.get("available_positions"):
        user_data.pop("routines_state", None)
        user_data.pop("tpsl_active_instance", None)
        await context.bot.send_message(
            chat_id=chat_id,
            text="No active LP positions found\\.",
            parse_mode="MarkdownV2"
        )
        return "No active positions"

    # Show initial setup message
    await _show_setup_message(context, chat_id, state, config)

    try:
        # Main monitoring loop
        while True:
            state["checks"] += 1
            state["last_check"] = time.time()

            # Re-read state (may have been modified by message handler)
            state = _get_state(context, instance_id)

            # Check positions if any are tracked
            if state["tracked_positions"]:
                try:
                    client = await get_client(chat_id, context=context)
                    if client and hasattr(client, 'gateway_clmm'):
                        await _check_positions(context, chat_id, state, client, instance_id)
                except Exception as e:
                    logger.error(f"Error checking positions: {e}")

            # Log periodically
            if state["checks"] % 20 == 0:
                tracked_count = len(state.get("tracked_positions", {}))
                logger.info(f"LP TP/SL check #{state['checks']}: tracking {tracked_count} positions")

            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        # Cleanup interactive state
        user_data.pop("routines_state", None)
        user_data.pop("tpsl_active_instance", None)

        # Build stop summary
        elapsed = int(time.time() - state.get("start_time", time.time()))
        mins, secs = divmod(elapsed, 60)
        tracked_count = len(state.get("tracked_positions", {}))
        triggers = sum(1 for p in state.get("tracked_positions", {}).values() if p.get("triggered"))

        # Clean up state
        user_data.pop(f"lp_tpsl_{instance_id}", None)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔴 *LP TP/SL Monitor Stopped*\n\n"
                    f"Duration: {mins}m {secs}s\n"
                    f"Positions tracked: {tracked_count}\n"
                    f"Triggers: {triggers}\n"
                    f"Checks: {state.get('checks', 0)}"
                ),
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass

        return f"Stopped after {mins}m {secs}s, {tracked_count} positions, {triggers} triggers"


async def _refresh_available_positions(context, chat_id: int, state: dict, client):
    """Fetch available LP positions from Gateway."""
    try:
        result = await client.gateway_clmm.search_positions(
            limit=100,
            offset=0,
            status="OPEN",
            refresh=True
        )

        if not result:
            state["available_positions"] = []
            return

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

        state["available_positions"] = active_positions

        # Build token cache
        token_cache = dict(KNOWN_TOKENS)
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

        state["token_cache"] = token_cache

        # Fetch token prices
        state["token_prices"] = await _fetch_token_prices(client)

    except Exception as e:
        logger.error(f"Error fetching positions: {e}")
        state["available_positions"] = []


async def _show_setup_message(context, chat_id: int, state: dict, config: Config):
    """Show initial setup message with available positions."""
    available = state.get("available_positions", [])
    token_cache = state.get("token_cache", {})
    token_prices = state.get("token_prices", {})

    lines = [
        "🎯 *LP TP/SL Monitor Started*\n",
        f"Default: TP \\+{config.default_tp_pct:.0f}% \\| SL \\-{config.default_sl_pct:.0f}%\n",
        "📋 *Available Positions:*"
    ]

    for i, pos in enumerate(available, 1):
        base_token = pos.get('base_token', pos.get('token_a', ''))
        quote_token = pos.get('quote_token', pos.get('token_b', ''))
        base_symbol = resolve_token_symbol(base_token, token_cache)
        quote_symbol = resolve_token_symbol(quote_token, token_cache)
        pair = f"{base_symbol}-{quote_symbol}"

        connector = pos.get('connector', 'unknown')[:3]
        in_range = pos.get('in_range', '')
        status_emoji = "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else "⚪"

        value = _calculate_position_value_usd(pos, token_cache, token_prices)

        line = f"{i}\\. {escape_markdown_v2(pair)} \\({escape_markdown_v2(connector)}\\) {status_emoji} ${escape_markdown_v2(f'{value:.2f}')}"
        lines.append(line)

    lines.append("")
    lines.append("_Send position number to add \\(e\\.g\\. '1'\\)_")
    lines.append("_Send `help` for all commands_")

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="MarkdownV2"
    )


async def _check_positions(context, chat_id: int, state: dict, client, instance_id: str):
    """Check all tracked positions for TP/SL triggers."""
    tracked = state.get("tracked_positions", {})
    if not tracked:
        return

    # Refresh token prices
    token_prices = await _fetch_token_prices(client)
    state["token_prices"] = token_prices

    # Fetch current positions
    try:
        result = await client.gateway_clmm.search_positions(
            limit=100,
            offset=0,
            status="OPEN",
            refresh=True
        )
        current_positions = {
            (pos.get('id') or pos.get('position_id') or pos.get('address', '')): pos
            for pos in result.get("data", []) if result
        }
    except Exception as e:
        logger.error(f"Error fetching positions for check: {e}")
        return

    token_cache = state.get("token_cache", {})
    user_data = _get_user_data(context)

    for pos_id, pos_data in list(tracked.items()):
        # Skip already triggered
        if pos_data.get("triggered"):
            continue

        # Check if position still exists
        current_pos = current_positions.get(pos_id)
        if not current_pos:
            # Position was closed externally
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Position *{escape_markdown_v2(pos_data.get('pair', 'Unknown'))}* was closed externally\\.",
                parse_mode="MarkdownV2"
            )
            del tracked[pos_id]
            continue

        # Calculate current value
        current_value = _calculate_position_value_usd(current_pos, token_cache, token_prices)
        pos_data["current_value_usd"] = current_value
        pos_data["high_value_usd"] = max(pos_data.get("high_value_usd", current_value), current_value)
        pos_data["low_value_usd"] = min(pos_data.get("low_value_usd", current_value), current_value)

        entry_value = pos_data.get("entry_value_usd", 0)
        if entry_value <= 0:
            continue

        change_pct = ((current_value - entry_value) / entry_value) * 100
        tp_pct = pos_data.get("tp_pct", 0)
        sl_pct = pos_data.get("sl_pct", 0)

        # Check TP
        if tp_pct > 0 and change_pct >= tp_pct:
            pos_data["triggered"] = "TP"
            await _send_trigger_alert(context, chat_id, pos_data, "TP", instance_id, user_data, current_pos)

        # Check SL
        elif sl_pct > 0 and change_pct <= -sl_pct:
            pos_data["triggered"] = "SL"
            await _send_trigger_alert(context, chat_id, pos_data, "SL", instance_id, user_data, current_pos)


async def _send_trigger_alert(context, chat_id: int, pos_data: dict, trigger_type: str, instance_id: str, user_data: dict, current_pos: dict):
    """Send TP/SL trigger alert with action buttons."""
    pair = pos_data.get("pair", "Unknown")
    entry = pos_data.get("entry_value_usd", 0)
    current = pos_data.get("current_value_usd", 0)
    change_pct = ((current - entry) / entry * 100) if entry > 0 else 0
    target_pct = pos_data.get("tp_pct" if trigger_type == "TP" else "sl_pct", 0)

    # Store position for close button
    pos_id = pos_data.get("position_id", "")
    cache_key = f"tpsl_{instance_id}_{pos_id[:8]}"
    if "positions_cache" not in user_data:
        user_data["positions_cache"] = {}
    user_data["positions_cache"][cache_key] = current_pos

    if trigger_type == "TP":
        header = "🎯 *TAKE PROFIT HIT\\!*"
        emoji = "🚀"
        target_str = f"\\+{target_pct:.0f}%"
    else:
        header = "🚨 *STOP LOSS HIT\\!*"
        emoji = "⚠️"
        target_str = f"\\-{target_pct:.0f}%"

    change_str = f"+{change_pct:.1f}%" if change_pct >= 0 else f"{change_pct:.1f}%"

    keyboard = [[
        InlineKeyboardButton("✅ Close Position", callback_data=f"dex:pos_close:{cache_key}"),
        InlineKeyboardButton("🔄 Continue", callback_data=f"routines:lp_tpsl:continue:{instance_id}:{pos_id[:8]}"),
    ], [
        InlineKeyboardButton("❌ Remove from Monitor", callback_data=f"routines:lp_tpsl:remove:{instance_id}:{pos_id[:8]}"),
    ]]

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{header}\n\n"
            f"*{escape_markdown_v2(pair)}*\n"
            f"Entry: ${escape_markdown_v2(f'{entry:.2f}')}\n"
            f"Current: ${escape_markdown_v2(f'{current:.2f}')}\n"
            f"Change: {escape_markdown_v2(change_str)}\n\n"
            f"{emoji} Target {target_str} reached\\!"
        ),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
