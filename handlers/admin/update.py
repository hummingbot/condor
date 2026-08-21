"""
Auto-update handler for Condor and Hummingbot API.

Provides /update command (admin-only) and periodic update checks.
"""

import asyncio
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.scheduler import JobContext, get_scheduler
from utils.auth import admin_required
from utils.telegram_formatters import escape_markdown_v2, escape_markdown_v2_code

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
            f"*Condor*\n" f"Error: `{escape_markdown_v2(condor_info['error'])}`"
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
            log_display = "\n".join(escape_markdown_v2_code(line) for line in log_lines)
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
                hb_log_display = "\n".join(
                    escape_markdown_v2_code(l) for l in hb_log_lines
                )
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
        keyboard.append(
            [InlineKeyboardButton("Update All", callback_data="admin:update_all")]
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    "Update Condor", callback_data="admin:update_pull"
                ),
                InlineKeyboardButton("Update HB API", callback_data="admin:update_hb"),
            ]
        )
    elif condor_has_update:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "Update Condor & Restart", callback_data="admin:update_pull"
                )
            ]
        )
    elif hb_has_update:
        keyboard.append(
            [InlineKeyboardButton("Update HB API", callback_data="admin:update_hb")]
        )

    keyboard.append(
        [InlineKeyboardButton("Refresh", callback_data="admin:update_check")]
    )
    keyboard.append(
        [InlineKeyboardButton("Force Restart", callback_data="admin:update_restart")]
    )
    keyboard.append([InlineKeyboardButton("Back", callback_data="admin:back")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if is_callback:
        await message_or_query.edit_message_text(
            text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )
    else:
        await msg.edit_text(
            text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
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


async def _progress(query, text: str) -> None:
    """Show the current step. Plain text: no escaping to get wrong mid-flow."""
    try:
        await query.edit_message_text(text)
    except Exception as e:
        # A failed progress edit (message unchanged, too old) must never abort
        # an update that is otherwise going fine.
        logger.debug("Could not update progress message: %s", e)


def _tail(output: str, max_lines: int = 15, max_chars: int = 1500) -> str:
    """Last few lines of command output — build logs are far past Telegram's limit."""
    text = (output or "").strip() or "(no output)"
    lines = text.split("\n")
    if len(lines) > max_lines:
        lines = ["..."] + lines[-max_lines:]
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = "..." + text[-max_chars:]
    return text


async def _fail(
    query, title: str, detail: str, note: str | None = None, retry_restart: bool = False
) -> None:
    """Render a failed step, with the tail of whatever the command printed."""
    text = f"*{escape_markdown_v2(title)}*\n\n```\n{escape_markdown_v2_code(_tail(detail))}\n```"
    if note:
        text += f"\n{escape_markdown_v2(note)}"

    keyboard = []
    if retry_restart:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "Restart Anyway", callback_data="admin:update_restart"
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("Back", callback_data="admin:update_check")])

    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _update_condor(query) -> bool:
    """Pull Condor, sync deps, rebuild the dashboard if the pull touched it.

    Returns True when the process is ready to restart. On failure it renders the
    error itself and returns False.
    """
    from utils.updater import (
        build_frontend,
        frontend_needs_build,
        get_local_commit_full,
        install_dependencies,
        pull_updates,
    )

    before = await get_local_commit_full()

    await _progress(query, "Pulling Condor updates...")
    success, msg = await pull_updates()
    if not success:
        await _fail(query, "Condor pull failed", msg)
        return False

    after = await get_local_commit_full()

    await _progress(query, "Installing dependencies...")
    success, dep_msg = await install_dependencies()
    if not success:
        await _fail(
            query,
            "Dependencies failed",
            dep_msg,
            note="Code was pulled but deps failed. Fix it manually before restarting.",
            retry_restart=True,
        )
        return False

    # The Makefile builds the frontend before starting; an in-place update has
    # to do it here or the dashboard keeps serving the previous bundle.
    if await frontend_needs_build(before, after):
        await _progress(query, "Building the dashboard (this can take a minute)...")
        success, build_msg = await build_frontend()
        if not success:
            await _fail(
                query,
                "Dashboard build failed",
                build_msg,
                note=(
                    "Code and deps are updated, but the dashboard would come back "
                    "on the previous bundle."
                ),
                retry_restart=True,
            )
            return False

    return True


async def _restart_now(query) -> None:
    """Send the last message, then hand the process over to a clean restart."""
    from utils.updater import request_restart

    await _progress(query, "Restarting Condor...")
    # Let Telegram flush the edit before the shutdown starts.
    await asyncio.sleep(1)
    request_restart()


async def _do_update(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pull Condor updates, install deps, rebuild the dashboard, and restart."""
    if await _update_condor(query):
        await _restart_now(query)


async def _do_update_hb(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update hummingbot-api: git pull + docker compose rebuild."""
    from utils.updater import update_hb_api

    await _progress(query, "Updating hummingbot-api...")

    success, msg = await update_hb_api()

    if not success:
        await _fail(query, "HB API update failed", msg)
        return

    text = (
        f"*Hummingbot API updated*\n\n"
        f"```\n{escape_markdown_v2_code(_tail(msg))}\n```"
    )
    keyboard = [
        [InlineKeyboardButton("Back", callback_data="admin:update_check")],
    ]
    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _do_update_all(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update hummingbot-api first, then Condor + restart."""
    from utils.updater import update_hb_api

    # HB API first: it restarts its own containers, and Condor reconnects to it
    # on the way back up.
    await _progress(query, "Updating hummingbot-api...")
    hb_ok, hb_msg = await update_hb_api()
    if not hb_ok:
        await _fail(
            query, "HB API update failed", hb_msg, note="Condor update skipped."
        )
        return

    if await _update_condor(query):
        await _restart_now(query)


async def _do_restart(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force restart without pulling."""
    await _restart_now(query)


async def _periodic_update_check(context: JobContext) -> None:
    """Periodic job that checks for updates and notifies admin.

    ``context.bot`` resolves to whatever this install can actually reach, so an
    install with no Telegram gets the notice on the dashboard bell instead of
    handing it to a sender that drops it.
    """
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


def schedule_update_checks() -> None:
    """Schedule periodic update checks. Call from startup()."""
    from utils.updater import UPDATE_CHECK_INTERVAL

    if UPDATE_CHECK_INTERVAL <= 0:
        logger.info("Update checks disabled (UPDATE_CHECK_INTERVAL=0)")
        return

    scheduler = get_scheduler()
    # Remove existing job if any
    scheduler.remove_by_name(UPDATE_CHECK_JOB)

    scheduler.run_repeating(
        _periodic_update_check,
        interval=UPDATE_CHECK_INTERVAL,
        first=30,  # first check 30s after startup
        name=UPDATE_CHECK_JOB,
    )
    logger.info("Scheduled update checks every %ds", UPDATE_CHECK_INTERVAL)
