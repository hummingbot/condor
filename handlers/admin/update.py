"""
Auto-update handler for Condor.

Provides /update command (admin-only) and periodic update checks.
"""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.auth import admin_required
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

# Job name for the periodic check
UPDATE_CHECK_JOB = "update_check"


@admin_required
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /update command - check for updates."""
    from handlers import clear_all_input_states

    clear_all_input_states(context)
    await _check_and_show(update.message, context)


async def _check_and_show(message_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check for updates and display result."""
    from utils.updater import check_for_updates, get_local_commit

    # Send a "checking..." message
    is_callback = hasattr(message_or_query, "edit_message_text")
    if is_callback:
        await message_or_query.edit_message_text("Checking for updates...")
    else:
        msg = await message_or_query.reply_text("Checking for updates...")

    info = await check_for_updates()

    if info["error"]:
        text = f"Failed to check for updates:\n`{escape_markdown_v2(info['error'])}`"
        keyboard = [[InlineKeyboardButton("Retry", callback_data="admin:update_check")]]
        if is_callback:
            await message_or_query.edit_message_text(
                text, parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await msg.edit_text(
                text, parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        return

    local = escape_markdown_v2(info["local_commit"])
    branch = escape_markdown_v2(info["branch"])

    if info["up_to_date"]:
        text = (
            f"*Condor is up to date*\n\n"
            f"Branch: `{branch}`\n"
            f"Version: `{local}`"
        )
        keyboard = [
            [InlineKeyboardButton("Refresh", callback_data="admin:update_check")],
            [InlineKeyboardButton("Force Restart", callback_data="admin:update_restart")],
        ]
    else:
        remote = escape_markdown_v2(info["remote_commit"])
        behind = info["commits_behind"]
        log_lines = info["commit_log"].split("\n")[:5]
        log_display = "\n".join(escape_markdown_v2(line) for line in log_lines)
        if info["commits_behind"] > 5:
            log_display += f"\n_\\.\\.\\.and {behind - 5} more_"

        text = (
            f"*Update Available\\!*\n\n"
            f"Branch: `{branch}`\n"
            f"Current: `{local}`\n"
            f"Latest: `{remote}`\n"
            f"Behind: *{behind} commit{'s' if behind != 1 else ''}*\n\n"
            f"*New commits:*\n```\n{log_display}\n```"
        )
        keyboard = [
            [InlineKeyboardButton("Update & Restart", callback_data="admin:update_pull")],
            [InlineKeyboardButton("Refresh", callback_data="admin:update_check")],
        ]

    keyboard.append([InlineKeyboardButton("Back", callback_data="admin:back")])

    if is_callback:
        await message_or_query.edit_message_text(
            text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await msg.edit_text(
            text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_update_callback(
    query, context: ContextTypes.DEFAULT_TYPE, action: str
) -> None:
    """Handle update-related callbacks."""
    if action == "update_check":
        await _check_and_show(query, context)

    elif action == "update_pull":
        await _do_update(query, context)

    elif action == "update_restart":
        await _do_restart(query, context)


async def _do_update(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pull updates, install deps, and restart."""
    from utils.updater import install_dependencies, pull_updates, restart_process

    await query.edit_message_text("Pulling latest changes...")

    success, msg = await pull_updates()
    if not success:
        text = f"*Update failed*\n\n`{escape_markdown_v2(msg)}`"
        keyboard = [
            [InlineKeyboardButton("Back", callback_data="admin:update_check")],
        ]
        await query.edit_message_text(
            text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    await query.edit_message_text("Installing dependencies...")

    success, dep_msg = await install_dependencies()
    if not success:
        text = f"*Dependencies failed*\n\n`{escape_markdown_v2(dep_msg)}`\n\nCode was pulled but deps failed\\. Fix manually and restart\\."
        keyboard = [
            [InlineKeyboardButton("Retry Restart", callback_data="admin:update_restart")],
        ]
        await query.edit_message_text(
            text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    await query.edit_message_text("Restarting Condor...")

    # Small delay so the message is sent before restart
    await asyncio.sleep(1)
    restart_process()


async def _do_restart(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force restart without pulling."""
    from utils.updater import restart_process

    await query.edit_message_text("Restarting Condor...")
    await asyncio.sleep(1)
    restart_process()


async def _periodic_update_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job that checks for updates and notifies admin."""
    from utils.config import ADMIN_USER_ID
    from utils.updater import check_for_updates

    if not ADMIN_USER_ID:
        return

    info = await check_for_updates()

    if info["error"] or info["up_to_date"]:
        return

    behind = info["commits_behind"]
    local = escape_markdown_v2(info["local_commit"])
    remote = escape_markdown_v2(info["remote_commit"])

    text = (
        f"*New Condor update available\\!*\n\n"
        f"Current: `{local}` \\| Latest: `{remote}`\n"
        f"*{behind} new commit{'s' if behind != 1 else ''}*\n\n"
        f"Use /update to review and install\\."
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=text,
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.warning("Failed to send update notification: %s", e)


def schedule_update_checks(application) -> None:
    """Schedule periodic update checks. Call from post_init."""
    from utils.updater import UPDATE_CHECK_INTERVAL

    if UPDATE_CHECK_INTERVAL <= 0:
        logger.info("Update checks disabled (UPDATE_CHECK_INTERVAL=0)")
        return

    # Remove existing job if any
    existing = application.job_queue.get_jobs_by_name(UPDATE_CHECK_JOB)
    for job in existing:
        job.schedule_removal()

    application.job_queue.run_repeating(
        _periodic_update_check,
        interval=UPDATE_CHECK_INTERVAL,
        first=30,  # first check 30s after startup
        name=UPDATE_CHECK_JOB,
    )
    logger.info(
        "Scheduled update checks every %ds", UPDATE_CHECK_INTERVAL
    )
