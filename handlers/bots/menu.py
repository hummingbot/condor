"""
Bots menu - Main menu and bot status display

Provides:
- Main bots menu with interactive buttons
- Bot status display with per-bot selection
- Bot detail view with controllers, logs, and actions
- Stop/edit controller functionality
- Navigation helpers
"""

import logging
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_active_bots, format_error_message, escape_markdown_v2, format_uptime
from ._shared import get_bots_client, clear_bots_state, set_controller_config

logger = logging.getLogger(__name__)


# ============================================
# MENU KEYBOARD BUILDERS
# ============================================

def _build_main_menu_keyboard(bots_dict: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Build the main bots menu keyboard with bot selection buttons

    Args:
        bots_dict: Dictionary of bot_name -> bot_info

    Returns:
        InlineKeyboardMarkup with menu buttons
    """
    keyboard = []

    # Add a button for each bot (max 5)
    for bot_name in list(bots_dict.keys())[:5]:
        # Truncate name for button
        display_name = bot_name[:30] + "..." if len(bot_name) > 30 else bot_name
        keyboard.append([
            InlineKeyboardButton(f"📊 {display_name}", callback_data=f"bots:bot_detail:{bot_name}")
        ])

    # Action buttons - historical
    keyboard.append([
        InlineKeyboardButton("📜 Historical", callback_data="bots:archived"),
    ])

    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="bots:refresh"),
        InlineKeyboardButton("❌ Close", callback_data="bots:close"),
    ])

    return InlineKeyboardMarkup(keyboard)


# ============================================
# MAIN MENU DISPLAY
# ============================================

async def show_bots_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the main bots menu with status and buttons

    Args:
        update: Telegram update
        context: Telegram context
    """
    # Clear any previous bots state
    clear_bots_state(context)

    # Determine if this is a callback query or direct command
    query = update.callback_query
    msg = update.message or (query.message if query else None)
    chat_id = update.effective_chat.id

    if not msg:
        logger.error("No message object available for show_bots_menu")
        return

    try:
        from config_manager import get_config_manager

        client, server_name = await get_bots_client(chat_id, context.user_data)

        # Check server status for indicator
        try:
            server_status_info = await get_config_manager().check_server_status(server_name)
            server_status = server_status_info.get("status", "online")
        except Exception:
            server_status = "online"  # Default to online if check fails

        status_emoji = {"online": "🟢", "offline": "🔴", "auth_error": "🟠", "error": "⚠️"}.get(server_status, "🟢")

        bots_data = await client.bot_orchestration.get_active_bots_status()

        # Fetch bot runs to get deployment times
        bot_runs_map = {}
        try:
            bot_runs_data = await client.bot_orchestration.get_bot_runs()
            if isinstance(bot_runs_data, dict) and "data" in bot_runs_data:
                for run in bot_runs_data.get("data", []):
                    # Only include DEPLOYED bots (not ARCHIVED)
                    if run.get("deployment_status") == "DEPLOYED" and run.get("deployed_at"):
                        bot_runs_map[run.get("bot_name")] = run.get("deployed_at")
        except Exception as e:
            logger.debug(f"Could not fetch bot runs for uptime: {e}")

        # Extract bots dictionary for building keyboard
        if isinstance(bots_data, dict) and "data" in bots_data:
            bots_dict = bots_data.get("data", {})
            if isinstance(bots_dict, list):
                bots_dict = {str(i): b for i, b in enumerate(bots_dict)}
        else:
            bots_dict = {}

        # Store bots data for later use
        context.user_data["active_bots_data"] = bots_data
        context.user_data["bot_runs_map"] = bot_runs_map
        context.user_data["current_server_name"] = server_name

        # Format the bot status message
        status_message = format_active_bots(bots_data, bot_runs=bot_runs_map)

        # Build the menu with bot buttons
        reply_markup = _build_main_menu_keyboard(bots_dict)

        # Add header with server indicator
        header = f"*Bots Dashboard* \\| _Server: {escape_markdown_v2(server_name)} {status_emoji}_\n\n"
        full_message = header + status_message

        if query:
            try:
                await query.message.edit_text(
                    full_message,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            except BadRequest as e:
                if "no text in the message" in str(e).lower():
                    # Message is a photo/media, delete it and send new text message
                    await query.message.delete()
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=full_message,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                elif "Message is not modified" not in str(e):
                    raise
        else:
            await msg.reply_text(
                full_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error showing bots menu: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch bots status: {str(e)}")

        reply_markup = _build_main_menu_keyboard({})

        if query:
            try:
                await query.message.edit_text(
                    error_message,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            except BadRequest as edit_error:
                if "no text in the message" in str(edit_error).lower():
                    await query.message.delete()
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=error_message,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                else:
                    raise
        else:
            await msg.reply_text(
                error_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )


# ============================================
# BOT DETAIL VIEW
# ============================================

async def show_bot_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str) -> None:
    """Show detailed status for a specific bot with controllers and actions

    Args:
        update: Telegram update
        context: Telegram context
        bot_name: Name of the bot to show
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        # Try to get bot info from cached data first
        bots_data = context.user_data.get("active_bots_data", {})

        if isinstance(bots_data, dict) and "data" in bots_data:
            bot_info = bots_data.get("data", {}).get(bot_name)
        else:
            bot_info = None

        # If not in cache, fetch fresh data
        if not bot_info:
            client, _ = await get_bots_client(chat_id, context.user_data)
            fresh_data = await client.bot_orchestration.get_active_bots_status()
            if isinstance(fresh_data, dict) and "data" in fresh_data:
                bot_info = fresh_data.get("data", {}).get(bot_name)
                context.user_data["active_bots_data"] = fresh_data

        if not bot_info:
            await query.answer("Bot not found", show_alert=True)
            await show_bots_menu(update, context)
            return

        # Store current bot for actions
        context.user_data["current_bot_name"] = bot_name
        context.user_data["current_bot_info"] = bot_info

        # Build detailed message
        status = bot_info.get("status", "unknown")
        status_emoji = "🟢" if status == "running" else "🔴"

        # Truncate bot name for display
        display_name = bot_name[:45] + "..." if len(bot_name) > 45 else bot_name

        # Get uptime if available
        bot_runs_map = context.user_data.get("bot_runs_map", {})
        uptime_str = ""
        if bot_name in bot_runs_map:
            uptime = format_uptime(bot_runs_map[bot_name])
            if uptime:
                uptime_str = f" ⏱️ {uptime}"

        lines = [
            f"*Bot Details*",
            "",
            f"{status_emoji} `{escape_markdown_v2(display_name)}`{uptime_str}",
        ]

        # Controllers and performance - table format
        performance = bot_info.get("performance", {})
        controller_names = list(performance.keys())

        # Store controller list for index-based callbacks
        context.user_data["current_controllers"] = controller_names

        # Build keyboard with controller rows
        keyboard = []

        if performance:
            total_pnl = 0
            total_volume = 0
            total_realized = 0
            total_unrealized = 0

            # Collect controller data for table
            ctrl_rows = []
            all_positions = []
            all_closed = []

            for idx, (ctrl_name, ctrl_info) in enumerate(performance.items()):
                if not isinstance(ctrl_info, dict):
                    continue

                ctrl_status = ctrl_info.get("status", "unknown")
                ctrl_perf = ctrl_info.get("performance", {})

                realized = ctrl_perf.get("realized_pnl_quote", 0) or 0
                unrealized = ctrl_perf.get("unrealized_pnl_quote", 0) or 0
                volume = ctrl_perf.get("volume_traded", 0) or 0
                pnl = realized + unrealized

                total_pnl += pnl
                total_volume += volume
                total_realized += realized
                total_unrealized += unrealized

                ctrl_rows.append({
                    "idx": idx,
                    "name": ctrl_name,
                    "status": ctrl_status,
                    "pnl": pnl,
                    "realized": realized,
                    "unrealized": unrealized,
                    "volume": volume,
                })

                # Collect positions with controller info
                positions = ctrl_perf.get("positions_summary", [])
                if positions:
                    trading_pair = _extract_pair_from_name(ctrl_name)
                    for pos in positions:
                        all_positions.append({"ctrl": ctrl_name, "pair": trading_pair, "pos": pos})

                # Collect closed counts
                close_counts = ctrl_perf.get("close_type_counts", {})
                if close_counts:
                    all_closed.append({"name": ctrl_name, "counts": close_counts})

            # Build table header
            lines.append("```")
            lines.append("Controller                        PnL     Vol")
            lines.append("──────────────────────────── ──────── ───────")

            # Build table rows
            for row in ctrl_rows:
                status_char = "▶" if row["status"] == "running" else "⏸"
                # Truncate controller name to fit table (max 27 chars)
                name_display = row["name"][:27] if len(row["name"]) > 27 else row["name"]
                name_padded = f"{status_char}{name_display}".ljust(28)

                pnl_str = f"{row['pnl']:+.2f}".rjust(8)
                vol_str = f"{row['volume']/1000:.1f}k" if row["volume"] >= 1000 else f"{row['volume']:.0f}"
                vol_str = vol_str.rjust(7)

                lines.append(f"{name_padded} {pnl_str} {vol_str}")

            # Total row
            if len(ctrl_rows) > 1:
                lines.append("──────────────────────────── ──────── ───────")
                total_name = "TOTAL".ljust(28)
                pnl_str = f"{total_pnl:+.2f}".rjust(8)
                vol_str = f"{total_volume/1000:.1f}k" if total_volume >= 1000 else f"{total_volume:.0f}"
                vol_str = vol_str.rjust(7)
                lines.append(f"{total_name} {pnl_str} {vol_str}")

            lines.append("```")

            # Open Positions section (grouped by controller) - limit to avoid message too long
            MAX_POSITIONS_DISPLAY = 8
            if all_positions:
                lines.append("")
                lines.append(f"*Open Positions* \\({len(all_positions)}\\)")

                # Group positions by controller
                positions_by_ctrl = {}
                for item in all_positions:
                    ctrl = item["ctrl"]
                    if ctrl not in positions_by_ctrl:
                        positions_by_ctrl[ctrl] = []
                    positions_by_ctrl[ctrl].append(item)

                positions_shown = 0
                for ctrl_name, ctrl_positions in positions_by_ctrl.items():
                    if positions_shown >= MAX_POSITIONS_DISPLAY:
                        remaining = len(all_positions) - positions_shown
                        lines.append(f"_\\.\\.\\.and {remaining} more_")
                        break
                    # Shorten controller name for display
                    short_ctrl = ctrl_name[:25] if len(ctrl_name) > 25 else ctrl_name
                    lines.append(f"_{escape_markdown_v2(short_ctrl)}_")
                    for item in ctrl_positions:
                        if positions_shown >= MAX_POSITIONS_DISPLAY:
                            break
                        pos = item["pos"]
                        trading_pair = item["pair"]
                        side_raw = pos.get("side", "")
                        is_long = "BUY" in str(side_raw).upper()
                        side_emoji = "🟢" if is_long else "🔴"
                        side_str = "L" if is_long else "S"
                        amount = pos.get("amount", 0) or 0
                        breakeven = pos.get("breakeven_price", 0) or 0
                        pos_value = amount * breakeven
                        pos_unrealized = pos.get("unrealized_pnl_quote", 0) or 0
                        lines.append(f"  📍 {side_emoji}{side_str} `${escape_markdown_v2(f'{pos_value:.2f}')}` @ `{escape_markdown_v2(f'{breakeven:.4f}')}` \\| U: `{escape_markdown_v2(f'{pos_unrealized:+.2f}')}`")
                        positions_shown += 1

            # Closed Positions section (combined)
            if all_closed:
                total_tp = total_sl = total_hold = total_early = total_insuf = 0
                for item in all_closed:
                    counts = item["counts"]
                    total_tp += _get_close_count(counts, "TAKE_PROFIT")
                    total_sl += _get_close_count(counts, "STOP_LOSS")
                    total_hold += _get_close_count(counts, "POSITION_HOLD")
                    total_early += _get_close_count(counts, "EARLY_STOP")
                    total_insuf += _get_close_count(counts, "INSUFFICIENT_BALANCE")

                total_closed_count = total_tp + total_sl + total_hold + total_early + total_insuf
                if total_closed_count > 0:
                    lines.append("")
                    lines.append(f"*Closed Positions* \\({total_closed_count}\\)")

                    row_parts = []
                    if total_tp > 0:
                        row_parts.append(f"🎯 TP: `{total_tp}`")
                    if total_sl > 0:
                        row_parts.append(f"🛑 SL: `{total_sl}`")
                    if total_hold > 0:
                        row_parts.append(f"✋ Hold: `{total_hold}`")
                    if total_early > 0:
                        row_parts.append(f"⚡ Early: `{total_early}`")
                    if total_insuf > 0:
                        row_parts.append(f"⚠️ Insuf: `{total_insuf}`")

                    if row_parts:
                        lines.append(" \\| ".join(row_parts))

            # Add controller buttons
            for row in ctrl_rows:
                idx = row["idx"]
                ctrl_status = row["status"]
                ctrl_name = row["name"]

                toggle_emoji = "⏸" if ctrl_status == "running" else "▶️"
                toggle_action = "stop_ctrl_quick" if ctrl_status == "running" else "start_ctrl_quick"

                if idx < 8:  # Max 8 controllers with buttons
                    # Use controller name directly, truncate if needed
                    btn_name = ctrl_name[:26] if len(ctrl_name) > 26 else ctrl_name
                    keyboard.append([
                        InlineKeyboardButton(f"✏️ {btn_name}", callback_data=f"bots:ctrl_idx:{idx}"),
                        InlineKeyboardButton(toggle_emoji, callback_data=f"bots:{toggle_action}:{idx}"),
                    ])

        # Error summary at the bottom
        error_logs = bot_info.get("error_logs", [])
        if error_logs:
            lines.append("")
            lines.append(f"⚠️ *{len(error_logs)} error\\(s\\):*")
            # Show last 3 errors with truncated message
            for err in error_logs[-3:]:
                err_msg = err.get("msg", str(err)) if isinstance(err, dict) else str(err)
                # Truncate long error messages
                if len(err_msg) > 80:
                    err_msg = err_msg[:77] + "..."
                lines.append(f"  `{escape_markdown_v2(err_msg)}`")

        # Bot-level actions
        keyboard.append([
            InlineKeyboardButton("📋 Logs", callback_data="bots:view_logs"),
            InlineKeyboardButton("🛑 Stop Bot", callback_data="bots:stop_bot"),
        ])

        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu"),
            InlineKeyboardButton("🔄 Refresh", callback_data="bots:refresh_bot"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Build message and ensure it doesn't exceed Telegram's limit
        message_text = "\n".join(lines)
        MAX_MESSAGE_LENGTH = 4000  # Leave some buffer below 4096
        if len(message_text) > MAX_MESSAGE_LENGTH:
            # Truncate and add indicator
            message_text = message_text[:MAX_MESSAGE_LENGTH - 50] + "\n\n_\\.\\.\\. truncated_"

        try:
            # Check if current message is a photo (from controller detail view)
            if getattr(query.message, 'photo', None):
                # Delete photo message and send new text message
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.chat.send_message(
                    message_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            else:
                await query.message.edit_text(
                    message_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                # Message content is the same, just answer the callback
                pass
            else:
                raise

    except Exception as e:
        logger.error(f"Error showing bot detail: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch bot status: {str(e)}")
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu")]]
        try:
            if getattr(query.message, 'photo', None):
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.chat.send_message(
                    error_message,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.message.edit_text(
                    error_message,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception:
            pass


def _extract_pair_from_name(ctrl_name: str) -> str:
    """Extract trading pair from controller name

    Example: "007_gs_binance_SOL-FDUSD" -> "SOL-FDUSD"
    """
    parts = ctrl_name.split("_")
    for part in parts:
        if "-" in part and part.upper() == part:
            return part
    # Fallback: return last part with dash or truncated name
    for part in reversed(parts):
        if "-" in part:
            return part
    return ctrl_name[:20]


def _get_close_count(close_counts: dict, type_suffix: str) -> int:
    """Get count for a close type, handling the CloseType. prefix

    Args:
        close_counts: Dict of close type -> count
        type_suffix: Type name without prefix (e.g., "TAKE_PROFIT")

    Returns:
        Count for that type, or 0 if not found
    """
    for key, count in close_counts.items():
        if key.endswith(type_suffix):
            return count
    return 0


def _shorten_controller_name(name: str, max_len: int = 28) -> str:
    """Shorten controller name intelligently

    Example: gs_binance_SOL-USDT_1252
    Result:  binance_SOL-USDT_1252

    Example: grid_strike_binance_perpetual_SOL-FDUSD_long_0.0001_0.0002_1
    Result:  binance_SOL-FDUSD_L_1
    """
    if len(name) <= max_len:
        return name

    parts = name.split("_")
    connector = ""
    pair = ""
    side = ""
    seq_num = ""

    for p in parts:
        p_lower = p.lower()
        p_upper = p.upper()
        if p_upper in ("LONG", "SHORT"):
            side = "L" if p_upper == "LONG" else "S"
        elif "-" in p:
            pair = p.upper()
        elif p_lower in ("binance", "hyperliquid", "kucoin", "okx", "bybit", "gate", "mexc"):
            connector = p_lower[:7]
        elif p.isdigit() and len(p) <= 5:
            # Capture sequence number (last numeric part)
            seq_num = p

    if pair:
        if connector and side and seq_num:
            short = f"{connector}_{pair}_{side}_{seq_num}"
        elif connector and seq_num:
            short = f"{connector}_{pair}_{seq_num}"
        elif connector and side:
            short = f"{connector}_{pair}_{side}"
        elif connector:
            short = f"{connector}_{pair}"
        elif side:
            short = f"{pair}_{side}"
        else:
            short = pair

        if len(short) <= max_len:
            return short
        return short[:max_len-1] + "."

    return name[:max_len-1] + "."


# ============================================
# CONTROLLER DETAIL & ACTIONS
# ============================================

async def show_controller_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, controller_idx: int) -> None:
    """Show controller detail with editable config (like networks.py pattern)"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    bot_name = context.user_data.get("current_bot_name")
    bot_info = context.user_data.get("current_bot_info", {})
    controllers = context.user_data.get("current_controllers", [])

    if not bot_name or controller_idx >= len(controllers):
        await query.answer("Context lost", show_alert=True)
        await show_bots_menu(update, context)
        return

    controller_name = controllers[controller_idx]
    performance = bot_info.get("performance", {})
    ctrl_info = performance.get(controller_name, {})

    if not ctrl_info:
        await query.answer("Controller not found", show_alert=True)
        return

    # Store current controller index
    context.user_data["current_controller_idx"] = controller_idx

    ctrl_status = ctrl_info.get("status", "unknown")
    ctrl_perf = ctrl_info.get("performance", {})

    realized = ctrl_perf.get("realized_pnl_quote", 0) or 0
    unrealized = ctrl_perf.get("unrealized_pnl_quote", 0) or 0
    volume = ctrl_perf.get("volume_traded", 0) or 0
    pnl = realized + unrealized

    # Try to fetch controller config
    ctrl_config = None
    is_grid_strike = False
    is_pmm_mister = False

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.get_bot_controller_configs(bot_name)

        # Find the matching config
        for cfg in configs:
            if cfg.get("id") == controller_name or cfg.get("controller_id") == controller_name:
                ctrl_config = cfg
                break

        if ctrl_config:
            context.user_data["current_controller_config"] = ctrl_config
            controller_type = ctrl_config.get("controller_name", "")
            is_grid_strike = "grid_strike" in controller_type.lower()
            is_pmm_mister = "pmm_mister" in controller_type.lower()

    except Exception as e:
        logger.warning(f"Could not fetch controller config: {e}")

    # Build message with P&L summary + editable config
    status_emoji = "▶️" if ctrl_status == "running" else "⏸️"
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    vol_str = f"{volume/1000:.1f}k" if volume >= 1000 else f"{volume:.0f}"

    lines = [
        f"{status_emoji} *{escape_markdown_v2(controller_name)}*",
        "",
        f"{pnl_emoji} `{escape_markdown_v2(f'{pnl:+.2f}')}` \\| 💰 R: `{escape_markdown_v2(f'{realized:+.2f}')}` \\| 📊 U: `{escape_markdown_v2(f'{unrealized:+.2f}')}` \\| 📦 `{escape_markdown_v2(vol_str)}`",
    ]

    # Add editable config section if available
    if ctrl_config and (is_grid_strike or is_pmm_mister):
        editable_fields = _get_editable_controller_fields(ctrl_config, is_pmm_mister)

        # Store for input processing
        context.user_data["ctrl_editable_fields"] = editable_fields
        context.user_data["bots_state"] = "ctrl_bulk_edit"
        context.user_data["ctrl_edit_chat_id"] = query.message.chat_id

        # Build config text
        config_lines = []
        for key, value in editable_fields.items():
            config_lines.append(f"{key}={value}")
        config_text = "\n".join(config_lines)

        lines.append("")
        lines.append("```")
        lines.append(config_text)
        lines.append("```")
        lines.append("")
        lines.append("✏️ _Send `key=value` to update_")

    # Build keyboard - show Start or Stop based on controller status
    keyboard = []
    is_running = ctrl_status == "running"

    if is_grid_strike and ctrl_config:
        # Grid Strike: show Chart + Stop/Start
        if is_running:
            keyboard.append([
                InlineKeyboardButton("📊 Chart", callback_data="bots:ctrl_chart"),
                InlineKeyboardButton("🛑 Stop", callback_data="bots:stop_ctrl"),
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("📊 Chart", callback_data="bots:ctrl_chart"),
                InlineKeyboardButton("▶️ Start", callback_data="bots:start_ctrl"),
            ])
    elif is_pmm_mister and ctrl_config:
        # PMM Mister: Stop/Start + Clone
        if is_running:
            keyboard.append([
                InlineKeyboardButton("🛑 Stop", callback_data="bots:stop_ctrl"),
                InlineKeyboardButton("📋 Clone", callback_data="bots:clone_ctrl"),
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("▶️ Start", callback_data="bots:start_ctrl"),
                InlineKeyboardButton("📋 Clone", callback_data="bots:clone_ctrl"),
            ])
    else:
        if is_running:
            keyboard.append([
                InlineKeyboardButton("🛑 Stop Controller", callback_data="bots:stop_ctrl"),
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("▶️ Start Controller", callback_data="bots:start_ctrl"),
            ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="bots:back_to_bot"),
        InlineKeyboardButton("🔄 Refresh", callback_data=f"bots:refresh_ctrl:{controller_idx}"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text_content = "\n".join(lines)

    # Store message_id for later edits
    if getattr(query.message, 'photo', None):
        try:
            await query.message.delete()
        except Exception:
            pass
        sent_msg = await query.message.chat.send_message(
            text_content,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        context.user_data["ctrl_edit_message_id"] = sent_msg.message_id
    else:
        await query.message.edit_text(
            text_content,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        context.user_data["ctrl_edit_message_id"] = query.message.message_id


async def handle_stop_controller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop current controller - show confirmation"""
    query = update.callback_query

    bot_name = context.user_data.get("current_bot_name")
    controllers = context.user_data.get("current_controllers", [])
    controller_idx = context.user_data.get("current_controller_idx")

    if not bot_name or controller_idx is None or controller_idx >= len(controllers):
        await query.answer("Context lost", show_alert=True)
        return

    controller_name = controllers[controller_idx]
    short_name = _shorten_controller_name(controller_name, 30)

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Stop", callback_data="bots:confirm_stop_ctrl"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"bots:ctrl_idx:{controller_idx}"),
        ],
    ]

    message_text = (
        f"*Stop Controller?*\n\n"
        f"`{escape_markdown_v2(short_name)}`\n\n"
        f"This will stop the controller\\."
    )

    # Handle photo messages (from controller detail view with chart)
    if getattr(query.message, 'photo', None):
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.chat.send_message(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_confirm_stop_controller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Actually stop the controller"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    bot_name = context.user_data.get("current_bot_name")
    controllers = context.user_data.get("current_controllers", [])
    controller_idx = context.user_data.get("current_controller_idx")

    if not bot_name or controller_idx is None or controller_idx >= len(controllers):
        await query.answer("Context lost", show_alert=True)
        return

    controller_name = controllers[controller_idx]
    short_name = _shorten_controller_name(controller_name, 30)

    await query.message.edit_text(
        f"Stopping `{escape_markdown_v2(short_name)}`\\.\\.\\.",
        parse_mode="MarkdownV2"
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Stop controller by setting manual_kill_switch=True
        result = await client.controllers.update_bot_controller_config(
            bot_name=bot_name,
            controller_name=controller_name,
            config={"manual_kill_switch": True}
        )

        keyboard = [[
            InlineKeyboardButton("▶️ Restart", callback_data="bots:start_ctrl"),
            InlineKeyboardButton("⬅️ Back to Bot", callback_data="bots:back_to_bot"),
        ]]

        await query.message.edit_text(
            f"*Controller Stopped*\n\n`{escape_markdown_v2(short_name)}`",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error stopping controller: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data=f"bots:ctrl_idx:{controller_idx}")]]
        await query.message.edit_text(
            f"*Failed*\n\nError: {escape_markdown_v2(str(e)[:100])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_start_controller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start/restart current controller - show confirmation"""
    query = update.callback_query

    bot_name = context.user_data.get("current_bot_name")
    controllers = context.user_data.get("current_controllers", [])
    controller_idx = context.user_data.get("current_controller_idx")

    if not bot_name or controller_idx is None or controller_idx >= len(controllers):
        await query.answer("Context lost", show_alert=True)
        return

    controller_name = controllers[controller_idx]
    short_name = _shorten_controller_name(controller_name, 30)

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Start", callback_data="bots:confirm_start_ctrl"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"bots:ctrl_idx:{controller_idx}"),
        ],
    ]

    message_text = (
        f"*Start Controller?*\n\n"
        f"`{escape_markdown_v2(short_name)}`\n\n"
        f"This will resume the controller\\."
    )

    # Handle photo messages (from controller detail view with chart)
    if getattr(query.message, 'photo', None):
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.chat.send_message(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_confirm_start_controller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Actually start the controller by setting manual_kill_switch=False"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    bot_name = context.user_data.get("current_bot_name")
    controllers = context.user_data.get("current_controllers", [])
    controller_idx = context.user_data.get("current_controller_idx")

    if not bot_name or controller_idx is None or controller_idx >= len(controllers):
        await query.answer("Context lost", show_alert=True)
        return

    controller_name = controllers[controller_idx]
    short_name = _shorten_controller_name(controller_name, 30)

    await query.message.edit_text(
        f"Starting `{escape_markdown_v2(short_name)}`\\.\\.\\.",
        parse_mode="MarkdownV2"
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Start controller by setting manual_kill_switch=False
        result = await client.controllers.update_bot_controller_config(
            bot_name=bot_name,
            controller_name=controller_name,
            config={"manual_kill_switch": False}
        )

        keyboard = [[InlineKeyboardButton("⬅️ Back to Bot", callback_data="bots:back_to_bot")]]

        await query.message.edit_text(
            f"*Controller Started*\n\n`{escape_markdown_v2(short_name)}`",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error starting controller: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data=f"bots:ctrl_idx:{controller_idx}")]]
        await query.message.edit_text(
            f"*Failed*\n\nError: {escape_markdown_v2(str(e)[:100])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_clone_controller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clone current controller config - opens PMM wizard in review mode"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    ctrl_config = context.user_data.get("current_controller_config")
    if not ctrl_config:
        await query.answer("No config to clone", show_alert=True)
        return

    controller_type = ctrl_config.get("controller_name", "")
    if "pmm_mister" not in controller_type.lower():
        await query.answer("Clone only supported for PMM Mister", show_alert=True)
        return

    await query.answer("Cloning config...")

    try:
        # Fetch existing configs to generate new ID (use get_all to find max number)
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.get_all_controller_configs()
        context.user_data["controller_configs_list"] = configs

        # Import generate_id from pmm_mister
        from .controllers.pmm_mister import generate_id as pmm_generate_id

        # Create a copy of the config
        new_config = dict(ctrl_config)

        # Generate new ID
        new_config["id"] = pmm_generate_id(new_config, configs)

        # Set the config for the wizard
        set_controller_config(context, new_config)

        # Set up wizard state for review mode
        context.user_data["bots_state"] = "pmm_wizard"
        context.user_data["pmm_wizard_step"] = "review"
        context.user_data["pmm_wizard_message_id"] = query.message.message_id
        context.user_data["pmm_wizard_chat_id"] = query.message.chat_id

        # Import and show the review step
        from .controller_handlers import _pmm_show_review
        await _pmm_show_review(context, chat_id, query.message.message_id, new_config)

    except Exception as e:
        logger.error(f"Error cloning controller: {e}", exc_info=True)
        await query.answer(f"Error: {str(e)[:50]}", show_alert=True)


async def handle_quick_stop_controller(update: Update, context: ContextTypes.DEFAULT_TYPE, controller_idx: int) -> None:
    """Quick stop controller from bot detail view (no confirmation)"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    bot_name = context.user_data.get("current_bot_name")
    controllers = context.user_data.get("current_controllers", [])

    if not bot_name or controller_idx >= len(controllers):
        await query.answer("Context lost", show_alert=True)
        return

    controller_name = controllers[controller_idx]
    short_name = _shorten_controller_name(controller_name, 20)

    await query.answer(f"Stopping {short_name}...")

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Stop controller by setting manual_kill_switch=True
        await client.controllers.update_bot_controller_config(
            bot_name=bot_name,
            controller_name=controller_name,
            config={"manual_kill_switch": True}
        )

        # Clear caches to force fresh data fetch
        context.user_data.pop("current_bot_info", None)
        context.user_data.pop("active_bots_data", None)
        await show_bot_detail(update, context, bot_name)

    except Exception as e:
        logger.error(f"Error stopping controller: {e}", exc_info=True)
        await query.answer(f"Failed: {str(e)[:50]}", show_alert=True)


async def handle_quick_start_controller(update: Update, context: ContextTypes.DEFAULT_TYPE, controller_idx: int) -> None:
    """Quick start/resume controller from bot detail view"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    bot_name = context.user_data.get("current_bot_name")
    controllers = context.user_data.get("current_controllers", [])

    if not bot_name or controller_idx >= len(controllers):
        await query.answer("Context lost", show_alert=True)
        return

    controller_name = controllers[controller_idx]
    short_name = _shorten_controller_name(controller_name, 20)

    await query.answer(f"Starting {short_name}...")

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Start controller by setting manual_kill_switch=False
        await client.controllers.update_bot_controller_config(
            bot_name=bot_name,
            controller_name=controller_name,
            config={"manual_kill_switch": False}
        )

        # Clear caches to force fresh data fetch
        context.user_data.pop("current_bot_info", None)
        context.user_data.pop("active_bots_data", None)
        await show_bot_detail(update, context, bot_name)

    except Exception as e:
        logger.error(f"Error starting controller: {e}", exc_info=True)
        await query.answer(f"Failed: {str(e)[:50]}", show_alert=True)


# ============================================
# CONTROLLER CHART & EDIT
# ============================================

async def show_controller_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and show OHLC chart for controller"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    controller_idx = context.user_data.get("current_controller_idx", 0)
    ctrl_config = context.user_data.get("current_controller_config")

    if not ctrl_config:
        await query.answer("Config not found", show_alert=True)
        return

    # Detect controller type
    controller_type = ctrl_config.get("controller_name", "")
    is_pmm_mister = "pmm_mister" in controller_type.lower()

    # Show loading message
    short_name = _shorten_controller_name(ctrl_config.get("id", ""), 30)
    loading_text = f"⏳ *Generating chart\\.\\.\\.*"

    try:
        await query.message.edit_text(loading_text, parse_mode="MarkdownV2")
    except Exception:
        pass

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        connector = ctrl_config.get("connector_name", "")
        pair = ctrl_config.get("trading_pair", "")

        # Fetch candles and current price
        candles = await client.market_data.get_candles(
            connector_name=connector,
            trading_pair=pair,
            interval="1h",
            max_records=420
        )
        prices = await client.market_data.get_prices(
            connector_name=connector,
            trading_pairs=pair
        )
        current_price = prices.get("prices", {}).get(pair)

        # Generate chart based on controller type
        if is_pmm_mister:
            from .controllers.pmm_mister import generate_chart
        else:
            from .controllers.grid_strike import generate_chart
        chart_bytes = generate_chart(ctrl_config, candles, current_price)

        if chart_bytes:
            # Build caption based on controller type
            leverage = ctrl_config.get("leverage", 1)

            if is_pmm_mister:
                buy_spreads = ctrl_config.get("buy_spreads", "0.0002,0.001")
                sell_spreads = ctrl_config.get("sell_spreads", "0.0002,0.001")
                take_profit = ctrl_config.get("take_profit", 0.0001)
                caption = (
                    f"📊 *{escape_markdown_v2(pair)}* \\| PMM {leverage}x\n"
                    f"Buy: `{escape_markdown_v2(buy_spreads)}` \\| Sell: `{escape_markdown_v2(sell_spreads)}`\n"
                    f"TP: `{escape_markdown_v2(f'{take_profit:.4%}')}`"
                )
            else:
                side_val = ctrl_config.get("side", 1)
                side_str = "LONG" if side_val == 1 else "SHORT"
                start_p = ctrl_config.get("start_price", 0)
                end_p = ctrl_config.get("end_price", 0)
                limit_p = ctrl_config.get("limit_price", 0)
                caption = (
                    f"📊 *{escape_markdown_v2(pair)}* \\| {escape_markdown_v2(side_str)} {leverage}x\n"
                    f"Grid: `{escape_markdown_v2(f'{start_p:.6g}')}` → `{escape_markdown_v2(f'{end_p:.6g}')}`\n"
                    f"Limit: `{escape_markdown_v2(f'{limit_p:.6g}')}`"
                )

            keyboard = [[
                InlineKeyboardButton("⬅️ Back", callback_data=f"bots:ctrl_idx:{controller_idx}"),
                InlineKeyboardButton("🔄 Refresh", callback_data="bots:ctrl_chart"),
            ]]

            # Delete text message and send photo
            try:
                await query.message.delete()
            except Exception:
                pass

            await query.message.chat.send_photo(
                photo=chart_bytes,
                caption=caption,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.message.edit_text(
                "❌ Could not generate chart",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Back", callback_data=f"bots:ctrl_idx:{controller_idx}")
                ]])
            )

    except Exception as e:
        logger.error(f"Error generating chart: {e}", exc_info=True)
        await query.message.edit_text(
            f"❌ Error: {escape_markdown_v2(str(e)[:100])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back", callback_data=f"bots:ctrl_idx:{controller_idx}")
            ]])
        )


async def show_controller_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show editable parameters for grid strike controller in bulk edit format"""
    query = update.callback_query

    bot_name = context.user_data.get("current_bot_name")
    ctrl_config = context.user_data.get("current_controller_config")
    controller_idx = context.user_data.get("current_controller_idx")

    if not bot_name or not ctrl_config:
        await query.answer("Context lost", show_alert=True)
        return

    controller_name = ctrl_config.get("id", "")
    controller_type = ctrl_config.get("controller_name", "")
    is_pmm_mister = "pmm_mister" in controller_type.lower()

    # Define editable fields with their current values
    editable_fields = _get_editable_controller_fields(ctrl_config, is_pmm_mister)

    # Store editable fields in context for input processing
    context.user_data["ctrl_editable_fields"] = editable_fields
    context.user_data["bots_state"] = "ctrl_bulk_edit"
    context.user_data["ctrl_edit_message_id"] = query.message.message_id if not getattr(query.message, 'photo', None) else None
    context.user_data["ctrl_edit_chat_id"] = query.message.chat_id

    # Build config text for display
    config_lines = []
    for key, value in editable_fields.items():
        config_lines.append(f"{key}={value}")
    config_text = "\n".join(config_lines)

    lines = [
        f"✏️ *Edit Controller*",
        "",
        f"`{escape_markdown_v2(controller_name)}`",
        "",
        f"```",
        f"{config_text}",
        f"```",
        "",
        "_Send only the fields you want to change\\._",
        "_Format: `key=value` \\(one per line\\)_",
    ]

    keyboard = [
        [InlineKeyboardButton("❌ Cancel", callback_data=f"bots:ctrl_idx:{controller_idx}")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    text_content = "\n".join(lines)

    # Handle photo messages (from controller detail view with chart)
    if getattr(query.message, 'photo', None):
        try:
            await query.message.delete()
        except Exception:
            pass
        sent_msg = await query.message.chat.send_message(
            text_content,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        context.user_data["ctrl_edit_message_id"] = sent_msg.message_id
    else:
        await query.message.edit_text(
            text_content,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        context.user_data["ctrl_edit_message_id"] = query.message.message_id


def _get_editable_controller_fields(ctrl_config: Dict[str, Any], is_pmm_mister: bool = False) -> Dict[str, Any]:
    """Extract editable fields from controller config"""
    if is_pmm_mister:
        # PMM Mister editable fields - match review step order
        return {
            # Identification fields
            "id": ctrl_config.get("id", ""),
            "connector_name": ctrl_config.get("connector_name", ""),
            "trading_pair": ctrl_config.get("trading_pair", ""),
            "leverage": ctrl_config.get("leverage", 20),
            "position_mode": ctrl_config.get("position_mode", "HEDGE"),
            # Amount settings
            "total_amount_quote": ctrl_config.get("total_amount_quote", 100),
            "portfolio_allocation": ctrl_config.get("portfolio_allocation", 0.05),
            # Base percentages
            "target_base_pct": ctrl_config.get("target_base_pct", 0.5),
            "min_base_pct": ctrl_config.get("min_base_pct", 0.4),
            "max_base_pct": ctrl_config.get("max_base_pct", 0.6),
            # Spreads and amounts
            "buy_spreads": ctrl_config.get("buy_spreads", "0.0002,0.001"),
            "sell_spreads": ctrl_config.get("sell_spreads", "0.0002,0.001"),
            "buy_amounts_pct": ctrl_config.get("buy_amounts_pct", "1,2"),
            "sell_amounts_pct": ctrl_config.get("sell_amounts_pct", "1,2"),
            # Take profit settings
            "take_profit": ctrl_config.get("take_profit", 0.0001),
            "take_profit_order_type": ctrl_config.get("take_profit_order_type", "LIMIT_MAKER"),
            "open_order_type": ctrl_config.get("open_order_type", "LIMIT"),
            # Timing settings
            "executor_refresh_time": ctrl_config.get("executor_refresh_time", 30),
            "buy_cooldown_time": ctrl_config.get("buy_cooldown_time", 15),
            "sell_cooldown_time": ctrl_config.get("sell_cooldown_time", 15),
            "buy_position_effectivization_time": ctrl_config.get("buy_position_effectivization_time", 3600),
            "sell_position_effectivization_time": ctrl_config.get("sell_position_effectivization_time", 3600),
            # Distance settings
            "min_buy_price_distance_pct": ctrl_config.get("min_buy_price_distance_pct", 0.003),
            "min_sell_price_distance_pct": ctrl_config.get("min_sell_price_distance_pct", 0.003),
            # Executor settings
            "max_active_executors_by_level": ctrl_config.get("max_active_executors_by_level", 4),
            "tick_mode": ctrl_config.get("tick_mode", False),
        }
    else:
        # Grid Strike editable fields
        tp_cfg = ctrl_config.get("triple_barrier_config", {})
        take_profit = tp_cfg.get("take_profit", 0.0001) if isinstance(tp_cfg, dict) else 0.0001

        return {
            "start_price": ctrl_config.get("start_price", 0),
            "end_price": ctrl_config.get("end_price", 0),
            "limit_price": ctrl_config.get("limit_price", 0),
            "total_amount_quote": ctrl_config.get("total_amount_quote", 0),
            "max_open_orders": ctrl_config.get("max_open_orders", 3),
            "max_orders_per_batch": ctrl_config.get("max_orders_per_batch", 1),
            "min_spread_between_orders": ctrl_config.get("min_spread_between_orders", 0.0001),
            "take_profit": take_profit,
        }


async def handle_controller_set_field(update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str) -> None:
    """Handle setting a controller field - show input prompt"""
    query = update.callback_query

    bot_name = context.user_data.get("current_bot_name")
    ctrl_config = context.user_data.get("current_controller_config")
    controller_idx = context.user_data.get("current_controller_idx")

    if not bot_name or not ctrl_config:
        await query.answer("Context lost", show_alert=True)
        return

    # Special handling for manual_kill_switch (toggle)
    if field_name == "manual_kill_switch":
        current_val = ctrl_config.get("manual_kill_switch", False)
        new_val = not current_val

        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Pause" if new_val else "✅ Yes, Resume", callback_data=f"bots:ctrl_confirm_set:{field_name}:{new_val}"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:ctrl_edit"),
            ],
        ]

        action = "pause" if new_val else "resume"
        await query.message.edit_text(
            f"*{action.capitalize()} Controller?*\n\n"
            f"This will {'stop' if new_val else 'resume'} the controller from placing orders\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Get current value and format hint
    field_labels = {
        "start_price": ("Start Price", "Entry zone start price"),
        "end_price": ("End Price", "Entry zone end price"),
        "limit_price": ("Limit Price", "Stop loss level"),
        "total_amount_quote": ("Total Amount", "Total quote amount to trade"),
        "max_open_orders": ("Max Open Orders", "Maximum concurrent orders"),
        "take_profit": ("Take Profit", "Take profit percentage (e.g. 0.01 = 1%)"),
        "min_spread_between_orders": ("Min Spread", "Minimum spread between orders"),
    }

    label, hint = field_labels.get(field_name, (field_name, ""))

    # Get current value
    if field_name == "take_profit":
        tp_cfg = ctrl_config.get("triple_barrier_config", {})
        current_val = tp_cfg.get("take_profit", 0.0001) if isinstance(tp_cfg, dict) else 0.0001
    else:
        current_val = ctrl_config.get(field_name, 0)

    # Store state for input processing
    context.user_data["bots_state"] = f"ctrl_set:{field_name}"
    context.user_data["editing_ctrl_field"] = field_name

    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="bots:ctrl_edit")]]

    await query.message.edit_text(
        f"*Edit {escape_markdown_v2(label)}*\n\n"
        f"Current: `{escape_markdown_v2(str(current_val))}`\n\n"
        f"_{escape_markdown_v2(hint)}_\n\n"
        f"Enter new value:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_controller_confirm_set(update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str, value: str) -> None:
    """Confirm and apply a controller field change"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    bot_name = context.user_data.get("current_bot_name")
    ctrl_config = context.user_data.get("current_controller_config")
    controllers = context.user_data.get("current_controllers", [])
    controller_idx = context.user_data.get("current_controller_idx")

    if not bot_name or not ctrl_config or controller_idx is None:
        await query.answer("Context lost", show_alert=True)
        return

    controller_name = controllers[controller_idx]

    # Parse value
    if value.lower() in ("true", "false"):
        parsed_value = value.lower() == "true"
    else:
        try:
            parsed_value = float(value)
        except ValueError:
            await query.answer("Invalid value", show_alert=True)
            return

    await query.answer("Updating...")

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Build config update
        if field_name == "take_profit":
            update_config = {
                "triple_barrier_config": {
                    "take_profit": parsed_value
                }
            }
        else:
            update_config = {field_name: parsed_value}

        # Validate first
        validation = await client.controllers.validate_controller_config(
            controller_type="generic",
            controller_name="grid_strike",
            config={**ctrl_config, **update_config}
        )

        if validation.get("status") != "success" and validation.get("valid") is not True:
            error_msg = validation.get("message", validation.get("error", "Validation failed"))
            await query.message.edit_text(
                f"*Validation Failed*\n\n{escape_markdown_v2(str(error_msg)[:200])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Back", callback_data="bots:ctrl_edit")
                ]])
            )
            return

        # Apply the update
        result = await client.controllers.update_bot_controller_config(
            bot_name=bot_name,
            controller_name=controller_name,
            config=update_config
        )

        if result.get("status") == "success":
            # Update local config cache
            if field_name == "take_profit":
                if "triple_barrier_config" not in ctrl_config:
                    ctrl_config["triple_barrier_config"] = {}
                ctrl_config["triple_barrier_config"]["take_profit"] = parsed_value
            else:
                ctrl_config[field_name] = parsed_value
            context.user_data["current_controller_config"] = ctrl_config

            await query.message.edit_text(
                f"*Updated*\n\n`{escape_markdown_v2(field_name)}` \\= `{escape_markdown_v2(str(parsed_value))}`",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Back to Edit", callback_data="bots:ctrl_edit"),
                    InlineKeyboardButton("⬅️ Controller", callback_data=f"bots:ctrl_idx:{controller_idx}"),
                ]])
            )
        else:
            error_msg = result.get("message", "Update failed")
            await query.message.edit_text(
                f"*Update Failed*\n\n{escape_markdown_v2(str(error_msg)[:200])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Back", callback_data="bots:ctrl_edit")
                ]])
            )

    except Exception as e:
        logger.error(f"Error updating controller config: {e}", exc_info=True)
        await query.message.edit_text(
            f"*Error*\n\n{escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back", callback_data="bots:ctrl_edit")
            ]])
        )


async def process_controller_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process user input for controller bulk edit - parses key=value lines"""
    chat_id = update.effective_chat.id
    bot_name = context.user_data.get("current_bot_name")
    ctrl_config = context.user_data.get("current_controller_config")
    controllers = context.user_data.get("current_controllers", [])
    controller_idx = context.user_data.get("current_controller_idx")
    editable_fields = context.user_data.get("ctrl_editable_fields", {})
    message_id = context.user_data.get("ctrl_edit_message_id")

    if not bot_name or not ctrl_config or controller_idx is None:
        await update.message.reply_text("Context lost. Please start over.")
        return

    controller_name = controllers[controller_idx]

    # Delete user's input message for clean chat
    try:
        await update.message.delete()
    except Exception:
        pass

    # Parse key=value lines
    updates = {}
    errors = []

    for line in user_input.split('\n'):
        line = line.strip()
        if not line or '=' not in line:
            continue

        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()

        # Validate key exists in editable fields
        if key not in editable_fields:
            errors.append(f"Unknown: {key}")
            continue

        # Convert value to appropriate type
        current_val = editable_fields.get(key)
        try:
            if isinstance(current_val, bool):
                parsed_value = value.lower() in ['true', '1', 'yes', 'y', 'on']
            elif isinstance(current_val, int):
                parsed_value = int(value)
            elif isinstance(current_val, float):
                parsed_value = float(value)
            else:
                parsed_value = value
            updates[key] = parsed_value
        except ValueError:
            errors.append(f"Invalid: {key}={value}")

    if errors:
        error_msg = "⚠️ " + ", ".join(errors)
        await update.get_bot().send_message(chat_id=chat_id, text=error_msg)

    if not updates:
        await update.get_bot().send_message(
            chat_id=chat_id,
            text="❌ No valid updates found. Use format: key=value"
        )
        return

    # Clear state
    context.user_data.pop("bots_state", None)
    context.user_data.pop("ctrl_editable_fields", None)

    # Show saving message
    saving_text = f"💾 Saving configuration\\.\\.\\."
    try:
        if message_id:
            await update.get_bot().edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=saving_text,
                parse_mode="MarkdownV2"
            )
    except Exception:
        pass

    # Build config update - handle take_profit specially
    update_config = {}
    for key, value in updates.items():
        if key == "take_profit":
            update_config["triple_barrier_config"] = {"take_profit": value}
        else:
            update_config[key] = value

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Apply the update
        result = await client.controllers.update_bot_controller_config(
            bot_name=bot_name,
            controller_name=controller_name,
            config=update_config
        )

        # Check for success - API may return status="success" or message containing "successfully"
        result_status = result.get("status", "")
        result_message = result.get("message", "")
        is_success = (
            result_status == "success" or
            "successfully" in str(result_message).lower() or
            "updated" in str(result_message).lower()
        )

        if is_success:
            # Update local config cache
            for key, value in updates.items():
                if key == "take_profit":
                    if "triple_barrier_config" not in ctrl_config:
                        ctrl_config["triple_barrier_config"] = {}
                    ctrl_config["triple_barrier_config"]["take_profit"] = value
                else:
                    ctrl_config[key] = value
            context.user_data["current_controller_config"] = ctrl_config

            # Format updated fields
            updated_lines = [f"`{escape_markdown_v2(k)}` \\= `{escape_markdown_v2(str(v))}`" for k, v in updates.items()]

            keyboard = [[
                InlineKeyboardButton("⬅️ Controller", callback_data=f"bots:ctrl_idx:{controller_idx}"),
                InlineKeyboardButton("⬅️ Bot", callback_data="bots:back_to_bot"),
            ]]

            success_text = f"✅ *Configuration Updated*\n\n" + "\n".join(updated_lines)

            if message_id:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=success_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.get_bot().send_message(
                    chat_id=chat_id,
                    text=success_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        else:
            error_msg = result_message or "Update failed"
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data=f"bots:ctrl_idx:{controller_idx}")]]

            if message_id:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"❌ *Update Failed*\n\n{escape_markdown_v2(str(error_msg)[:200])}",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.get_bot().send_message(
                    chat_id=chat_id,
                    text=f"❌ *Update Failed*\n\n{escape_markdown_v2(str(error_msg)[:200])}",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

    except Exception as e:
        logger.error(f"Error updating controller config: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data=f"bots:ctrl_idx:{controller_idx}")]]
        await update.get_bot().send_message(
            chat_id=chat_id,
            text=f"❌ *Error*\n\n{escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ============================================
# BOT ACTIONS
# ============================================

async def handle_stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show confirmation for stopping current bot"""
    query = update.callback_query

    bot_name = context.user_data.get("current_bot_name")
    if not bot_name:
        await query.answer("No bot selected", show_alert=True)
        return

    display_name = bot_name[:40] + "..." if len(bot_name) > 40 else bot_name

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Stop", callback_data="bots:confirm_stop_bot"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="bots:back_to_bot"),
        ],
    ]

    await query.message.edit_text(
        f"*Stop Bot?*\n\n"
        f"`{escape_markdown_v2(display_name)}`\n\n"
        f"• Stop all trading\n"
        f"• Archive data locally\n"
        f"• Remove container",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_confirm_stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Actually stop and archive the bot"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    bot_name = context.user_data.get("current_bot_name")
    if not bot_name:
        await query.answer("No bot selected", show_alert=True)
        return

    display_name = bot_name[:35] + "..." if len(bot_name) > 35 else bot_name

    await query.message.edit_text(
        f"Stopping `{escape_markdown_v2(display_name)}`\\.\\.\\.",
        parse_mode="MarkdownV2"
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        result = await client.bot_orchestration.stop_and_archive_bot(
            bot_name=bot_name,
            skip_order_cancellation=True,
            archive_locally=True
        )

        status = result.get("status", "unknown")

        keyboard = [[InlineKeyboardButton("⬅️ Dashboard", callback_data="bots:main_menu")]]

        if status == "success":
            await query.message.edit_text(
                f"*Bot Stopped*\n\n"
                f"`{escape_markdown_v2(display_name)}`\n\n"
                f"_Archiving in background\\._",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            message = result.get("message", "Unknown")
            await query.message.edit_text(
                f"*Result: {escape_markdown_v2(status)}*\n\n{escape_markdown_v2(message[:100])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Error stopping bot: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:back_to_bot")]]
        await query.message.edit_text(
            f"*Failed*\n\nError: {escape_markdown_v2(str(e)[:150])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_back_to_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Go back to bot detail view"""
    bot_name = context.user_data.get("current_bot_name")
    if bot_name:
        await show_bot_detail(update, context, bot_name)
    else:
        await show_bots_menu(update, context)


async def handle_refresh_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh current bot detail"""
    bot_name = context.user_data.get("current_bot_name")
    if bot_name:
        # Clear cache to force refresh
        context.user_data.pop("current_bot_info", None)
        context.user_data.pop("active_bots_data", None)
        await show_bot_detail(update, context, bot_name)
    else:
        await show_bots_menu(update, context)


async def handle_refresh_controller(update: Update, context: ContextTypes.DEFAULT_TYPE, controller_idx: int) -> None:
    """Refresh controller detail - clears cache and reloads"""
    # Clear cache to force fresh data fetch
    context.user_data.pop("current_bot_info", None)
    context.user_data.pop("active_bots_data", None)
    context.user_data.pop("current_controller_config", None)

    # Reload bot info first to get fresh performance data
    bot_name = context.user_data.get("current_bot_name")
    chat_id = update.effective_chat.id

    if bot_name:
        try:
            client, _ = await get_bots_client(chat_id, context.user_data)
            fresh_data = await client.bot_orchestration.get_active_bots_status()
            if isinstance(fresh_data, dict) and "data" in fresh_data:
                bot_info = fresh_data.get("data", {}).get(bot_name)
                if bot_info:
                    context.user_data["active_bots_data"] = fresh_data
                    context.user_data["current_bot_info"] = bot_info
                    # Update controllers list
                    performance = bot_info.get("performance", {})
                    context.user_data["current_controllers"] = list(performance.keys())
        except Exception as e:
            logger.warning(f"Error refreshing bot data: {e}")

    await show_controller_detail(update, context, controller_idx)


# ============================================
# VIEW LOGS
# ============================================

def _format_log_entry(log) -> str:
    """Format a log entry with timestamp - full message, no truncation"""
    if isinstance(log, dict):
        timestamp = log.get("timestamp", log.get("time", log.get("ts", "")))
        msg = log.get("msg", log.get("message", str(log)))
    else:
        timestamp = ""
        msg = str(log)

    # Escape backticks in log messages to prevent breaking code blocks
    msg = str(msg).replace("`", "'")

    # Extract time portion (HH:MM:SS) from timestamp
    time_str = ""
    if timestamp:
        ts = str(timestamp)
        # Try to extract time from various formats
        if "T" in ts:
            # ISO format: 2024-11-28T16:42:04
            time_part = ts.split("T")[1][:8]
            time_str = time_part
        elif " " in ts:
            # Space separated: 2024-11-28 16:42:04
            time_part = ts.split(" ")[1][:8] if len(ts.split(" ")) > 1 else ""
            time_str = time_part
        elif len(ts) >= 8 and ":" in ts:
            time_str = ts[:8]

    if time_str:
        return f"[{time_str}] {msg}"
    return msg


async def show_bot_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent logs for current bot with timestamps - full messages"""
    query = update.callback_query

    bot_name = context.user_data.get("current_bot_name")
    bot_info = context.user_data.get("current_bot_info", {})

    if not bot_name:
        await query.answer("No bot selected", show_alert=True)
        return

    general_logs = bot_info.get("general_logs", [])
    error_logs = bot_info.get("error_logs", [])

    lines = [
        f"*Logs: `{escape_markdown_v2(bot_name)}`*",
        "",
    ]

    # Show errors first (separated section)
    if error_logs:
        lines.append("*🔴 Errors:*")
        lines.append("```")
        for log in error_logs[:10]:
            entry = _format_log_entry(log)
            lines.append(entry)
        lines.append("```")
        lines.append("")

    # Show recent general logs
    if general_logs:
        lines.append("*📋 Recent Activity:*")
        lines.append("```")
        for log in general_logs[-20:]:
            entry = _format_log_entry(log)
            lines.append(entry)
        lines.append("```")
    else:
        lines.append("_No logs available_")

    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:back_to_bot")]]

    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:4000]
        # Check if we have an unclosed code block (odd number of ```)
        if message.count("```") % 2 == 1:
            message += "\n```"
        message += "\n\\.\\.\\."

    await query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ============================================
# MENU ACTIONS
# ============================================

async def handle_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle refresh button - reload bot status

    Args:
        update: Telegram update
        context: Telegram context
    """
    await show_bots_menu(update, context)


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle close button - delete the menu message

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    clear_bots_state(context)

    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete message: {e}")
