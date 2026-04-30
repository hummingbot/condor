"""
Auto-update handler for Condor and Hummingbot API.

Provides /update command (admin-only) and periodic update checks.
"""

import asyncio
import logging
from datetime import datetime, timezone

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
    """Check for updates on both repos and display result."""
    from utils.updater import check_for_updates, check_hb_api_updates

    is_callback = hasattr(message_or_query, "edit_message_text")
    if is_callback:
        await message_or_query.edit_message_text("Checking for updates...")
    else:
        msg = await message_or_query.reply_text("Checking for updates...")

    # Check both repos in parallel
    condor_info, hb_info = await asyncio.gather(
        check_for_updates(),
        check_hb_api_updates(),
    )

    # --- Build Condor section ---
    sections = []
    condor_has_update = False
    hb_has_update = False

    if condor_info["error"]:
        sections.append(
            f"*Condor*\n"
            f"Error: `{escape_markdown_v2(condor_info['error'])}`"
        )
    else:
        local = escape_markdown_v2(condor_info["local_commit"])
        branch = escape_markdown_v2(condor_info["branch"])
        condor_has_update = not condor_info["up_to_date"]

        if condor_info["up_to_date"]:
            sections.append(
                f"*Condor*\n"
                f"Branch: `{branch}` \\| Version: `{local}`\n"
                f"Status: Up to date"
            )
        else:
            remote = escape_markdown_v2(condor_info["remote_commit"])
            behind = condor_info["commits_behind"]
            log_lines = condor_info["commit_log"].split("\n")[:5]
            log_display = "\n".join(escape_markdown_v2(line) for line in log_lines)
            if behind > 5:
                log_display += f"\n_\\.\\.\\.and {behind - 5} more_"
            sections.append(
                f"*Condor*\n"
                f"Branch: `{branch}` \\| Version: `{local}`\n"
                f"Status: *{behind} commit{'s' if behind != 1 else ''} behind*\n"
                f"```\n{log_display}\n```"
            )

    # --- Build HB API section ---
    if hb_info["available"]:
        hb_git = hb_info["git_info"]
        docker = hb_info["docker"]

        if hb_git["error"]:
            sections.append(
                f"\n*Hummingbot API*\n"
                f"Error: `{escape_markdown_v2(hb_git['error'])}`"
            )
        else:
            hb_local = escape_markdown_v2(hb_git["local_commit"])
            hb_branch = escape_markdown_v2(hb_git["branch"])
            hb_has_update = not hb_git["up_to_date"]

            docker_line = ""
            if docker:
                status = docker["status"]
                started = docker.get("started_at", "")
                age = _format_docker_age(started)
                docker_line = f"\nDocker: {escape_markdown_v2(status)}"
                if age:
                    docker_line += f" \\(started {escape_markdown_v2(age)}\\)"

            if hb_git["up_to_date"]:
                sections.append(
                    f"\n*Hummingbot API*\n"
                    f"Branch: `{hb_branch}` \\| Version: `{hb_local}`\n"
                    f"Status: Up to date{docker_line}"
                )
            else:
                hb_remote = escape_markdown_v2(hb_git["remote_commit"])
                hb_behind = hb_git["commits_behind"]
                hb_log_lines = hb_git["commit_log"].split("\n")[:5]
                hb_log_display = "\n".join(escape_markdown_v2(l) for l in hb_log_lines)
                if hb_behind > 5:
                    hb_log_display += f"\n_\\.\\.\\.and {hb_behind - 5} more_"
                sections.append(
                    f"\n*Hummingbot API*\n"
                    f"Branch: `{hb_branch}` \\| Version: `{hb_local}`\n"
                    f"Status: *{hb_behind} commit{'s' if hb_behind != 1 else ''} behind*{docker_line}\n"
                    f"```\n{hb_log_display}\n```"
                )

    text = "\n".join(sections)

    # --- Build keyboard ---
    keyboard = []

    if condor_has_update and hb_has_update:
        keyboard.append([InlineKeyboardButton("Update All", callback_data="admin:update_all")])
        keyboard.append([
            InlineKeyboardButton("Update Condor", callback_data="admin:update_pull"),
            InlineKeyboardButton("Update HB API", callback_data="admin:update_hb"),
        ])
    elif condor_has_update:
        keyboard.append([InlineKeyboardButton("Update Condor & Restart", callback_data="admin:update_pull")])
    elif hb_has_update:
        keyboard.append([InlineKeyboardButton("Update HB API", callback_data="admin:update_hb")])

    keyboard.append([InlineKeyboardButton("Refresh", callback_data="admin:update_check")])
    keyboard.append([InlineKeyboardButton("Force Restart", callback_data="admin:update_restart")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="admin:back")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if is_callback:
        await message_or_query.edit_message_text(
            text, parse_mode="MarkdownV2", reply_markup=reply_markup,
        )
    else:
        await msg.edit_text(
            text, parse_mode="MarkdownV2", reply_markup=reply_markup,
        )


def _format_docker_age(started_at: str) -> str:
    """Format a Docker StartedAt timestamp as a human-readable age."""
    if not started_at:
        return ""
    try:
        # Docker uses ISO 8601 format
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - started
        total_secs = int(delta.total_seconds())
        if total_secs < 60:
            return f"{total_secs}s ago"
        elif total_secs < 3600:
            return f"{total_secs // 60}m ago"
        elif total_secs < 86400:
            return f"{total_secs // 3600}h ago"
        else:
            return f"{total_secs // 86400}d ago"
    except Exception:
        return ""


async def handle_update_callback(
    query, context: ContextTypes.DEFAULT_TYPE, action: str
) -> None:
    """Handle update-related callbacks."""
    if action == "update_check":
        await _check_and_show(query, context)
    elif action == "update_pull":
        await _do_update(query, context)
    elif action == "update_hb":
        await _do_update_hb(query, context)
    elif action == "update_all":
        await _do_update_all(query, context)
    elif action == "update_restart":
        await _do_restart(query, context)


async def _do_update(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pull Condor updates, install deps, and restart."""
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


async def _do_update_hb(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update hummingbot-api: git pull + docker compose rebuild."""
    from utils.updater import update_hb_api

    await query.edit_message_text("Pulling hummingbot\\-api...")

    success, msg = await update_hb_api()

    if success:
        text = f"*Hummingbot API updated\\!*\n\n`{escape_markdown_v2(msg)}`"
    else:
        text = f"*HB API update failed*\n\n`{escape_markdown_v2(msg)}`"

    keyboard = [
        [InlineKeyboardButton("Back", callback_data="admin:update_check")],
    ]
    await query.edit_message_text(
        text, parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _do_update_all(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update hummingbot-api first, then Condor + restart."""
    from utils.updater import (
        install_dependencies,
        pull_updates,
        restart_process,
        update_hb_api,
    )

    # 1. Update hb-api
    await query.edit_message_text("Updating hummingbot\\-api...")
    hb_ok, hb_msg = await update_hb_api()

    if not hb_ok:
        text = f"*HB API update failed*\n\n`{escape_markdown_v2(hb_msg)}`\n\nCondor update skipped\\."
        keyboard = [
            [InlineKeyboardButton("Back", callback_data="admin:update_check")],
        ]
        await query.edit_message_text(
            text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # 2. Update Condor
    await query.edit_message_text("Pulling Condor updates...")
    success, msg = await pull_updates()
    if not success:
        text = (
            f"*HB API updated, but Condor pull failed*\n\n"
            f"`{escape_markdown_v2(msg)}`"
        )
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
        text = (
            f"*Both repos pulled, but deps failed*\n\n"
            f"`{escape_markdown_v2(dep_msg)}`\n\n"
            f"Fix manually and restart\\."
        )
        keyboard = [
            [InlineKeyboardButton("Retry Restart", callback_data="admin:update_restart")],
        ]
        await query.edit_message_text(
            text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    await query.edit_message_text("Restarting Condor...")
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
    from utils.updater import check_for_updates, check_hb_api_updates

    if not ADMIN_USER_ID:
        return

    condor_info, hb_info = await asyncio.gather(
        check_for_updates(),
        check_hb_api_updates(),
    )

    condor_update = not condor_info.get("error") and not condor_info["up_to_date"]
    hb_update = (
        hb_info["available"]
        and not hb_info["git_info"].get("error")
        and not hb_info["git_info"]["up_to_date"]
    )

    if not condor_update and not hb_update:
        return

    parts = []
    if condor_update:
        behind = condor_info["commits_behind"]
        local = escape_markdown_v2(condor_info["local_commit"])
        remote = escape_markdown_v2(condor_info["remote_commit"])
        parts.append(
            f"*Condor*: `{local}` → `{remote}` "
            f"\\(*{behind} commit{'s' if behind != 1 else ''}*\\)"
        )
    if hb_update:
        hb_git = hb_info["git_info"]
        hb_behind = hb_git["commits_behind"]
        hb_local = escape_markdown_v2(hb_git["local_commit"])
        hb_remote = escape_markdown_v2(hb_git["remote_commit"])
        parts.append(
            f"*HB API*: `{hb_local}` → `{hb_remote}` "
            f"\\(*{hb_behind} commit{'s' if hb_behind != 1 else ''}*\\)"
        )

    text = (
        f"*Updates available\\!*\n\n"
        + "\n".join(parts)
        + f"\n\nUse /update to review and install\\."
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
