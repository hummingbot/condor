"""The ``/update`` screen: a view over :mod:`condor.updates`.

There is no git, no docker and no build in this file, and that is the point.
Every question it asks — what version is this, what is in the way, what will
happen, how is it going — is answered by the engine, which is headless. That is
what lets the dashboard render the same update without reimplementing a step.

What this module does own is Telegram: the cards, the buttons, and an observer
that edits one message as the run walks its plan.
"""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor import updates
from utils.auth import admin_required
from utils.telegram_formatters import escape_markdown_v2, escape_markdown_v2_code

logger = logging.getLogger(__name__)

# Job name for the periodic check
UPDATE_CHECK_JOB = "update_check"

# All of it, for the "Update all" button.
ALL = "all"

_STEP_GLYPH = {
    "pending": "·",
    "running": "»",
    "ok": "✓",
    "failed": "✗",
    "skipped": "–",
}


@admin_required
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /update command - check for updates."""
    from handlers import clear_all_input_states

    clear_all_input_states(context)
    await _show_status(update.message, context)


# ---------------------------------------------------------------------------
# Screen 1: what is running, and what is available
# ---------------------------------------------------------------------------


def _facet_line(label: str, facet) -> str:
    """One version, and where it could go. Never a bare "up to date" on an error."""
    current = f"`{escape_markdown_v2_code(facet.current)}`"
    if facet.error:
        return f"{label}: {current}\n" f"  {escape_markdown_v2('⚠ ' + facet.error)}"
    if facet.up_to_date:
        return f"{label}: {current} — up to date"

    line = f"{label}: {current}"
    if facet.available:
        line += f" → `{escape_markdown_v2_code(facet.available)}`"
    if facet.behind > 1:
        line += f" \\({facet.behind} commits behind\\)"
    elif facet.kind == "image":
        line += " \\(newer available\\)"
    return line


def _status_text(statuses) -> str:
    sections = []
    for status in statuses:
        header = f"*{escape_markdown_v2(status.name)}*"
        if status.mode and status.mode != "unknown":
            header += f" _\\({escape_markdown_v2(status.mode)} mode\\)_"
        lines = [header]

        for key in ("image", "repo"):
            facet = status.facets.get(key)
            if facet is None:
                continue
            lines.append(_facet_line("Image" if key == "image" else "Repo", facet))
            if facet.detail and not facet.up_to_date:
                body = "\n".join(escape_markdown_v2_code(line) for line in facet.detail)
                lines.append(f"```\n{body}\n```")

        sections.append("\n".join(lines))

    body = "\n\n".join(sections) or escape_markdown_v2("No components to update.")

    # An update that has landed but not been applied is the first thing this
    # screen should say: every version line above it is about the checkout, not
    # about what this process is actually running.
    pending = updates.relaunch_pending()
    if pending is not None:
        notice = escape_markdown_v2(
            f"⚠ Updated to {pending['target_commit'][:7]}. Condor is still "
            f"running {pending['from_commit'][:7]} — relaunch to apply."
        )
        return f"{notice}\n\n{body}"

    return body


def _status_keyboard(statuses) -> InlineKeyboardMarkup:
    stale = [s for s in statuses if not s.up_to_date]
    rows = []

    if len(stale) > 1:
        rows.append(
            [InlineKeyboardButton("Update All", callback_data=f"admin:update_go:{ALL}")]
        )
    for status in stale:
        rows.append(
            [
                InlineKeyboardButton(
                    f"Update {status.name}",
                    callback_data=f"admin:update_go:{status.key}",
                )
            ]
        )

    rows.append([InlineKeyboardButton("Refresh", callback_data="admin:update_refresh")])
    rows.append(
        [InlineKeyboardButton("Force Restart", callback_data="admin:update_restart")]
    )
    rows.append([InlineKeyboardButton("Back", callback_data="admin:back")])
    return InlineKeyboardMarkup(rows)


async def _show_status(target, context, *, force: bool = False) -> None:
    """Render the per-component card. ``target`` is a message or a callback query."""
    is_callback = hasattr(target, "edit_message_text")
    if is_callback:
        await target.edit_message_text("Checking for updates...")
        editor = target.edit_message_text
    else:
        message = await target.reply_text("Checking for updates...")
        editor = message.edit_text

    statuses = await updates.check(force=force)
    await editor(
        _status_text(statuses),
        parse_mode="MarkdownV2",
        reply_markup=_status_keyboard(statuses),
    )


# ---------------------------------------------------------------------------
# Screen 2: what it will do, and what is in the way
# ---------------------------------------------------------------------------


def _selection(key: str) -> list[str]:
    return list(updates.keys()) if key == ALL else [key]


def _preflight_text(preflight) -> str:
    parts = []

    if preflight.blocks:
        parts.append(f"*{escape_markdown_v2('Blocked')}*")
        for block in preflight.blocks:
            body = escape_markdown_v2(block.message)
            if block.paths:
                shown = block.paths[:10]
                listing = "\n".join(escape_markdown_v2_code(p) for p in shown)
                if len(block.paths) > len(shown):
                    listing += f"\n…and {len(block.paths) - len(shown)} more"
                body += f"\n```\n{listing}\n```"
            parts.append(body)

    for warning in preflight.warnings:
        parts.append(escape_markdown_v2(f"⚠ {warning.message}"))

    if preflight.steps:
        plan = "\n".join(
            escape_markdown_v2_code(f"{i}. {step}")
            for i, step in enumerate(preflight.steps, 1)
        )
        parts.append(f"*Plan*\n```\n{plan}\n```")

    return "\n\n".join(parts)


def _preflight_keyboard(key: str, preflight) -> InlineKeyboardMarkup:
    rows = []

    # One button per (component, action), even when two blocks offer the same
    # one: the resolution acts on the whole component's conflict set at once.
    offered: list[tuple[str, str]] = []
    for block in preflight.blocks:
        for action in block.resolutions:
            if action == "cancel":
                continue
            pair = (block.component, action)
            if pair not in offered:
                offered.append(pair)

    # The scope rides along as one character: callback_data caps at 64 bytes,
    # and spelling a component key out twice was already within 3 of it.
    scope = "a" if key == ALL else "s"
    for component, action in offered:
        label = f"{action.title()} conflicts in {component}"
        # Discarding destroys work, so it gets its own confirm screen.
        prefix = "update_cfix" if action == "discard" else "update_fix"
        rows.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"admin:{prefix}:{component}:{action}:{scope}"
                )
            ]
        )

    if preflight.ok and preflight.steps:
        rows.append(
            [InlineKeyboardButton("Confirm", callback_data=f"admin:update_run:{key}")]
        )

    rows.append([InlineKeyboardButton("Cancel", callback_data="admin:update_refresh")])
    return InlineKeyboardMarkup(rows)


async def _show_preflight(query, key: str) -> None:
    await query.edit_message_text("Checking what this would do...")
    preflight = await updates.preflight(_selection(key))
    await query.edit_message_text(
        _preflight_text(preflight) or escape_markdown_v2("Nothing to do."),
        parse_mode="MarkdownV2",
        reply_markup=_preflight_keyboard(key, preflight),
    )


async def _confirm_fix(query, component: str, action: str, key: str) -> None:
    """Discarding is unrecoverable, so it is asked twice."""
    preflight = await updates.preflight(_selection(key))
    paths = sorted(
        {
            p
            for block in preflight.blocks
            if block.component == component and action in block.resolutions
            for p in block.paths
        }
    )
    listing = "\n".join(escape_markdown_v2_code(p) for p in paths[:20]) or "(none)"
    text = (
        f"*{escape_markdown_v2('Discard local changes?')}*\n\n"
        + escape_markdown_v2(
            f"This permanently throws away local work on {len(paths)} "
            f"file{'s' if len(paths) != 1 else ''} in {component}. It cannot be undone."
        )
        + f"\n```\n{listing}\n```"
    )
    keyboard = [
        [
            InlineKeyboardButton(
                "Yes, discard",
                callback_data=(
                    f"admin:update_fix:{component}:{action}:"
                    f"{'a' if key == ALL else 's'}"
                ),
            )
        ],
        [InlineKeyboardButton("Keep them", callback_data=f"admin:update_go:{key}")],
    ]
    await query.edit_message_text(
        text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _apply_fix(query, component: str, action: str, key: str) -> None:
    await query.edit_message_text(f"Resolving conflicts in {component}...")
    ok, message = await updates.resolve(component, action)
    if not ok:
        await query.edit_message_text(
            f"Could not {action}:\n\n{message}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data="admin:update_refresh")]]
            ),
        )
        return
    # A stash ref is the one thing the admin has to be told, so it leads.
    await query.edit_message_text(message)
    await asyncio.sleep(1.5)
    await _show_preflight(query, key)


# ---------------------------------------------------------------------------
# Screen 3: the run
# ---------------------------------------------------------------------------


def _run_text(run) -> str:
    """The plan with its progress. Plain text: no escaping to get wrong mid-flow."""
    header = {
        "running": "Updating...",
        "restarting": "Restarting Condor...",
        "succeeded": "Update complete.",
        "failed": "Update failed.",
    }.get(run.state, "Updating...")

    lines = [header, ""]
    for step in run.steps:
        lines.append(f"{_STEP_GLYPH.get(step.state, '·')} {step.label}")

    # The last step of a Condor update belongs to a human: the new code is on
    # disk, this process is still the old one, and nothing applies until it is
    # relaunched. Saying so here is the whole point of not exec'ing.
    pending = updates.relaunch_pending()
    if pending is not None and not run.live:
        lines += [
            "",
            f"Relaunch Condor to apply {pending['target_commit'][:7]} — "
            "it is still running the old code until you do.",
        ]

    if run.error:
        lines += ["", run.error]

    failed = next((s for s in run.steps if s.state == "failed"), None)
    if failed is not None and failed.output_tail:
        lines += ["", failed.output_tail]

    return "\n".join(lines)


def _run_keyboard(run) -> InlineKeyboardMarkup | None:
    if run.live:
        return None
    rows = []
    # Offered, never taken automatically: an in-process restart is an ``execv``
    # that can race whatever started Condor into a second copy of it, so it is
    # only ever something the admin chooses (see :mod:`condor.updates.run`).
    if run.state == "failed" or updates.relaunch_pending() is not None:
        rows.append(
            [InlineKeyboardButton("Restart Now", callback_data="admin:update_restart")]
        )
    rows.append([InlineKeyboardButton("Back", callback_data="admin:update_refresh")])
    return InlineKeyboardMarkup(rows)


async def _start_run(query, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Hand the run to the engine and watch it through an observer.

    The observer is how a surface that is already in-process gets zero-latency
    transitions; the journal is what a surface that is not (or that outlives the
    restart) reads instead.
    """

    async def render(run) -> None:
        try:
            await query.edit_message_text(
                _run_text(run), reply_markup=_run_keyboard(run)
            )
        except Exception as e:  # noqa: BLE001
            # A failed progress edit (unchanged text, message too old) must
            # never abort an update that is otherwise going fine.
            logger.debug("Could not render update progress: %s", e)
        if not run.live:
            updates.unregister_observer(render)

    updates.register_observer(render)
    run = await updates.start(
        _selection(key),
        actor_user_id=query.from_user.id if query.from_user else None,
        actor_chat_id=query.message.chat_id if query.message else None,
    )
    await render(run)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


async def handle_update_callback(
    query, context: ContextTypes.DEFAULT_TYPE, action: str
) -> None:
    """Handle update-related callbacks."""
    head, _, rest = action.partition(":")

    if head in ("update_check", "update_refresh"):
        await _show_status(query, context, force=head == "update_refresh")
    elif head == "update_go":
        await _show_preflight(query, rest or ALL)
    elif head in ("update_fix", "update_cfix"):
        component, _, tail = rest.partition(":")
        fix, _, scope = tail.partition(":")
        key = ALL if scope == "a" else component
        if head == "update_cfix":
            await _confirm_fix(query, component, fix, key)
        else:
            await _apply_fix(query, component, fix, key)
    elif head == "update_run":
        await _start_run(query, context, rest or ALL)
    elif head == "update_restart":
        await _do_restart(query)


async def _do_restart(query) -> None:
    """Force a restart without updating anything. Reachable from every screen."""
    from utils.updater import request_restart

    await query.edit_message_text("Restarting Condor...")
    # Let Telegram flush the edit before the shutdown starts.
    await asyncio.sleep(1)
    request_restart()


# ---------------------------------------------------------------------------
# The hourly notice
# ---------------------------------------------------------------------------


async def _periodic_update_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job that checks for updates and notifies admin."""
    from condor import notifications
    from utils.config import ADMIN_USER_ID

    if not ADMIN_USER_ID:
        return

    stale = [s for s in await updates.check() if not s.up_to_date]
    if not stale:
        return

    lines = []
    for status in stale:
        for facet in status.facets.values():
            if facet.up_to_date or facet.error:
                continue
            if facet.behind > 1:
                lines.append(
                    f"{status.name}: {facet.current} → {facet.available} "
                    f"({facet.behind} commits)"
                )
            else:
                lines.append(f"{status.name}: {facet.current} → {facet.available}")

    if not lines:
        return

    body = "Updates available\n\n" + "\n".join(lines)

    # announce() reaches Telegram *and* the dashboard bell in one call, which is
    # what makes the update panel discoverable without any new badge plumbing.
    # The two surfaces read differently: /update is a Telegram command, and
    # telling a dashboard reader to type it is advice they cannot follow —
    # their entry is already a link into the update panel.
    await notifications.announce(
        int(ADMIN_USER_ID),
        int(ADMIN_USER_ID),
        body + "\n\nUse /update to install.",
        kind="system",
        bot=context.bot,
        title="Updates available",
        link="/settings?tab=updates",
        # The bell already carries "Updates available" as the title and the
        # entry itself is the link into the panel, so its body is just the
        # versions — which is all the three-line preview has room for anyway.
        bell_text="\n".join(lines),
    )


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
    logger.info("Scheduled update checks every %ds", UPDATE_CHECK_INTERVAL)
