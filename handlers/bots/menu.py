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
from typing import Dict, Any, Optional, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_active_bots, format_error_message, escape_markdown_v2, format_number
from ._shared import get_bots_client, clear_bots_state

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

    # Action buttons
    keyboard.append([
        InlineKeyboardButton("➕ New Grid Strike", callback_data="bots:new_grid_strike"),
        InlineKeyboardButton("Deploy", callback_data="bots:deploy_menu"),
    ])

    keyboard.append([
        InlineKeyboardButton("📁 Configs", callback_data="bots:controller_configs"),
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

    if not msg:
        logger.error("No message object available for show_bots_menu")
        return

    try:
        client = await get_bots_client()
        bots_data = await client.bot_orchestration.get_active_bots_status()

        # Extract bots dictionary for building keyboard
        if isinstance(bots_data, dict) and "data" in bots_data:
            bots_dict = bots_data.get("data", {})
            if isinstance(bots_dict, list):
                bots_dict = {str(i): b for i, b in enumerate(bots_dict)}
        else:
            bots_dict = {}

        # Store bots data for later use
        context.user_data["active_bots_data"] = bots_data

        # Format the bot status message
        status_message = format_active_bots(bots_data)

        # Build the menu with bot buttons
        reply_markup = _build_main_menu_keyboard(bots_dict)

        # Add header
        header = r"*Bots Dashboard*" + "\n\n"
        full_message = header + status_message

        if query:
            try:
                await query.message.edit_text(
                    full_message,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            except BadRequest as e:
                if "Message is not modified" not in str(e):
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
            await query.message.edit_text(
                error_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
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

    try:
        # Try to get bot info from cached data first
        bots_data = context.user_data.get("active_bots_data", {})

        if isinstance(bots_data, dict) and "data" in bots_data:
            bot_info = bots_data.get("data", {}).get(bot_name)
        else:
            bot_info = None

        # If not in cache, fetch fresh data
        if not bot_info:
            client = await get_bots_client()
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

        lines = [
            f"*Bot Details*",
            "",
            f"{status_emoji} `{escape_markdown_v2(display_name)}`",
            "",
        ]

        # Controllers and performance - COMPACT FORMAT with numbers
        performance = bot_info.get("performance", {})
        controller_names = list(performance.keys())

        # Store controller list for index-based callbacks
        context.user_data["current_controllers"] = controller_names

        if performance:
            total_pnl = 0
            total_volume = 0

            lines.append("```")

            for idx, (ctrl_name, ctrl_info) in enumerate(performance.items()):
                if isinstance(ctrl_info, dict):
                    ctrl_status = ctrl_info.get("status", "unknown")
                    ctrl_perf = ctrl_info.get("performance", {})

                    realized = ctrl_perf.get("realized_pnl_quote", 0) or 0
                    unrealized = ctrl_perf.get("unrealized_pnl_quote", 0) or 0
                    volume = ctrl_perf.get("volume_traded", 0) or 0
                    pnl = realized + unrealized

                    total_pnl += pnl
                    total_volume += volume

                    # Shortened name with number
                    short_name = _shorten_controller_name(ctrl_name, 26)

                    # Format numbers compactly
                    pnl_str = f"{pnl:+.1f}"
                    vol_str = f"{volume/1000:.1f}k" if volume >= 1000 else f"{volume:.0f}"

                    lines.append(f"{idx+1}.{short_name} {pnl_str} v:{vol_str}")

            if len(performance) > 1:
                vol_total = f"{total_volume/1000:.1f}k" if total_volume >= 1000 else f"{total_volume:.0f}"
                lines.append(f"{'─'*40}")
                lines.append(f"TOTAL: {total_pnl:+.2f} v:{vol_total}")

            lines.append("```")

        # Error summary
        error_logs = bot_info.get("error_logs", [])
        if error_logs:
            lines.append(f"\n⚠️ *{len(error_logs)} error\\(s\\)*")

        # Build keyboard - numbered buttons for controllers (4 per row)
        keyboard = []

        # Controller buttons - up to 8 in 4-column layout
        ctrl_buttons = []
        for idx in range(min(len(controller_names), 8)):
            ctrl_buttons.append(
                InlineKeyboardButton(f"⚙️{idx+1}", callback_data=f"bots:ctrl_idx:{idx}")
            )

        # Add controller buttons in rows of 4
        for i in range(0, len(ctrl_buttons), 4):
            keyboard.append(ctrl_buttons[i:i+4])

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

        try:
            await query.message.edit_text(
                "\n".join(lines),
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
        await query.message.edit_text(
            error_message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


def _shorten_controller_name(name: str, max_len: int = 28) -> str:
    """Shorten controller name intelligently

    Example: grid_strike_binance_perpetual_SOL-FDUSD_long_0.0001_0.0002_1
    Result:  binance_SOL-FDUSD_L
    """
    if len(name) <= max_len:
        return name

    parts = name.split("_")
    connector = ""
    pair = ""
    side = ""

    for p in parts:
        p_lower = p.lower()
        p_upper = p.upper()
        if p_upper in ("LONG", "SHORT"):
            side = "L" if p_upper == "LONG" else "S"
        elif "-" in p:
            pair = p.upper()
        elif p_lower in ("binance", "hyperliquid", "kucoin", "okx", "bybit", "gate", "mexc"):
            connector = p_lower[:7]

    if pair:
        if connector and side:
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
    """Show controller detail with edit/stop options (using index)"""
    query = update.callback_query

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

    pnl_emoji = "📈" if pnl >= 0 else "📉"
    status_emoji = "🟢" if ctrl_status == "running" else "🔴"

    short_name = _shorten_controller_name(controller_name, 35)

    lines = [
        "*Controller Details*",
        "",
        f"⚙️ `{escape_markdown_v2(short_name)}`",
        f"{status_emoji} Status: `{escape_markdown_v2(ctrl_status)}`",
        "",
        f"{pnl_emoji} *PnL:* `{pnl:+.2f}` \\| 📊 *Vol:* `{volume:.0f}`",
        f"  Realized: `{realized:+.2f}`",
        f"  Unrealized: `{unrealized:+.2f}`",
    ]

    keyboard = [
        [
            InlineKeyboardButton("🛑 Stop Controller", callback_data="bots:stop_ctrl"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:back_to_bot"),
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


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

    await query.message.edit_text(
        f"*Stop Controller?*\n\n"
        f"`{escape_markdown_v2(short_name)}`\n\n"
        f"This will stop the controller\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_confirm_stop_controller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Actually stop the controller"""
    query = update.callback_query

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
        client = await get_bots_client()

        # Stop controller by setting manual_kill_switch=True
        result = await client.controllers.update_bot_controller_config(
            bot_name=bot_name,
            controller_name=controller_name,
            config={"manual_kill_switch": True}
        )

        keyboard = [[InlineKeyboardButton("⬅️ Back to Bot", callback_data="bots:back_to_bot")]]

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
        client = await get_bots_client()

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
        await show_bot_detail(update, context, bot_name)
    else:
        await show_bots_menu(update, context)


# ============================================
# VIEW LOGS
# ============================================

def _format_log_entry(log, max_msg_len: int = 55) -> str:
    """Format a log entry with timestamp"""
    if isinstance(log, dict):
        timestamp = log.get("timestamp", log.get("time", log.get("ts", "")))
        msg = log.get("msg", log.get("message", str(log)))
    else:
        timestamp = ""
        msg = str(log)

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

    # Truncate message
    msg = msg[:max_msg_len] if len(msg) > max_msg_len else msg

    if time_str:
        return f"[{time_str}] {msg}"
    return msg


async def show_bot_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent logs for current bot with timestamps"""
    query = update.callback_query

    bot_name = context.user_data.get("current_bot_name")
    bot_info = context.user_data.get("current_bot_info", {})

    if not bot_name:
        await query.answer("No bot selected", show_alert=True)
        return

    general_logs = bot_info.get("general_logs", [])
    error_logs = bot_info.get("error_logs", [])

    display_name = bot_name[:25] + "..." if len(bot_name) > 25 else bot_name

    lines = [
        f"*Logs: `{escape_markdown_v2(display_name)}`*",
        "",
    ]

    # Show errors first (separated section)
    if error_logs:
        lines.append("*🔴 Errors:*")
        lines.append("```")
        for log in error_logs[:5]:
            entry = _format_log_entry(log, 50)
            lines.append(entry)
        lines.append("```")
        lines.append("")

    # Show recent general logs
    if general_logs:
        lines.append("*📋 Recent Activity:*")
        lines.append("```")
        for log in general_logs[-10:]:
            entry = _format_log_entry(log, 50)
            lines.append(entry)
        lines.append("```")
    else:
        lines.append("_No logs available_")

    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:back_to_bot")]]

    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:4000] + "\n\\.\\.\\."

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
