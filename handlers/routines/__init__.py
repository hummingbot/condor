"""
Routines Handler - Run configurable Python scripts via Telegram.

Features:
- Auto-discovery of routines from routines/ folder
- Text-based config editing (key=value)
- Instance-based execution (each run has frozen config)
- One-shot routines: Run once, can be scheduled (interval/daily)
- Continuous routines: Run forever with internal loop until stopped
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime, time as dt_time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackContext

from handlers import clear_all_input_states
from routines.base import discover_routines, get_routine
from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

SCHEDULE_PRESETS = [
    ("30s", 30),
    ("1m", 60),
    ("5m", 300),
    ("15m", 900),
    ("30m", 1800),
    ("1h", 3600),
]

DAILY_PRESETS = ["06:00", "09:00", "12:00", "18:00", "21:00"]

# Global storage for continuous routine tasks (not persisted)
_continuous_tasks: dict[str, asyncio.Task] = {}  # instance_id -> Task


# =============================================================================
# Storage Helpers
# =============================================================================


def _get_drafts(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Get draft configs dict."""
    if "routine_drafts" not in context.user_data:
        context.user_data["routine_drafts"] = {}
    return context.user_data["routine_drafts"]


def _get_instances(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Get all instances dict."""
    if "routine_instances" not in context.user_data:
        context.user_data["routine_instances"] = {}
    return context.user_data["routine_instances"]


def _get_draft(context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> dict:
    """Get draft config for a routine, initializing from defaults if needed."""
    drafts = _get_drafts(context)
    if routine_name not in drafts:
        routine = get_routine(routine_name)
        if routine:
            drafts[routine_name] = routine.get_default_config().model_dump()
        else:
            drafts[routine_name] = {}
    return drafts[routine_name]


def _set_draft(context: ContextTypes.DEFAULT_TYPE, routine_name: str, config: dict) -> None:
    """Update draft config for a routine."""
    drafts = _get_drafts(context)
    drafts[routine_name] = config


def _get_routine_instances(context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> list[tuple[str, dict]]:
    """Get all instances for a specific routine."""
    instances = _get_instances(context)
    return [(iid, inst) for iid, inst in instances.items() if inst.get("routine_name") == routine_name]


def _generate_instance_id() -> str:
    """Generate a short unique instance ID."""
    return hashlib.md5(f"{time.time()}{id(object())}".encode()).hexdigest()[:6]


# =============================================================================
# Formatting Helpers
# =============================================================================


def _display_name(name: str) -> str:
    """Convert snake_case to Title Case."""
    return name.replace("_", " ").title()


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def _format_schedule(schedule: dict) -> str:
    """Format schedule as human-readable string."""
    stype = schedule.get("type", "once")
    if stype == "once":
        return "One-time"
    elif stype == "interval":
        secs = schedule.get("interval_sec", 60)
        if secs < 60:
            return f"Every {secs}s"
        elif secs < 3600:
            return f"Every {secs // 60}m"
        else:
            return f"Every {secs // 3600}h"
    elif stype == "continuous":
        return "Running"
    elif stype == "daily":
        return f"Daily @ {schedule.get('daily_time', '09:00')}"
    return "Unknown"


def _format_ago(timestamp: float) -> str:
    """Format timestamp as 'X ago' string."""
    diff = time.time() - timestamp
    if diff < 60:
        return f"{int(diff)}s ago"
    elif diff < 3600:
        return f"{int(diff // 60)}m ago"
    elif diff < 86400:
        return f"{int(diff // 3600)}h ago"
    return f"{int(diff // 86400)}d ago"


def _config_preview(config: dict, max_items: int = 2) -> str:
    """Get short config preview."""
    items = list(config.items())[:max_items]
    return ", ".join(f"{k}={v}" for k, v in items)


# =============================================================================
# Job Management
# =============================================================================


def _job_name(chat_id: int, instance_id: str) -> str:
    """Build job name for JobQueue."""
    return f"routine_{chat_id}_{instance_id}"


def _find_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, instance_id: str):
    """Find a job by instance ID."""
    name = _job_name(chat_id, instance_id)
    jobs = context.job_queue.get_jobs_by_name(name)
    return jobs[0] if jobs else None


def _stop_instance(context: ContextTypes.DEFAULT_TYPE, chat_id: int, instance_id: str) -> bool:
    """Stop a job/task and remove instance. Returns True if found."""
    # Try to stop JobQueue job (for scheduled one-shots)
    job = _find_job(context, chat_id, instance_id)
    if job:
        job.schedule_removal()
        logger.info(f"Removed scheduled job for instance {instance_id}")

    # Try to cancel asyncio task (for continuous routines)
    task = _continuous_tasks.pop(instance_id, None)
    if task and not task.done():
        task.cancel()
        logger.info(f"Cancelled continuous task for instance {instance_id}")

    # Remove from instances
    instances = _get_instances(context)
    if instance_id in instances:
        routine_name = instances[instance_id].get("routine_name", "unknown")
        del instances[instance_id]
        logger.info(f"Stopped instance {instance_id} ({routine_name})")
        return True
    return False


def _stop_all_routine(context: ContextTypes.DEFAULT_TYPE, chat_id: int, routine_name: str) -> int:
    """Stop all instances of a routine. Returns count."""
    instances = _get_routine_instances(context, routine_name)
    count = 0
    for iid, _ in instances:
        if _stop_instance(context, chat_id, iid):
            count += 1
    return count


async def _execute_routine(
    context: CallbackContext,
    instance_id: str,
    routine_name: str,
    config_dict: dict,
    chat_id: int,
) -> tuple[str, float]:
    """Execute a routine and return (result, duration)."""
    routine = get_routine(routine_name)
    if not routine:
        return "Routine not found", 0

    start = time.time()

    # Prepare context for routine
    context._chat_id = chat_id
    context._instance_id = instance_id
    context._user_data = context.application.user_data.get(chat_id, {})

    try:
        config = routine.config_class(**config_dict)
        result = await routine.run_fn(config, context)
        result_text = str(result)[:500] if result else "Completed"
    except Exception as e:
        result_text = f"Error: {e}"
        logger.error(f"Routine {routine_name}[{instance_id}] failed: {e}")

    duration = time.time() - start
    return result_text, duration


async def _run_continuous_routine(
    application,
    instance_id: str,
    routine_name: str,
    config_dict: dict,
    chat_id: int,
) -> None:
    """Run a continuous routine as an asyncio task."""
    routine = get_routine(routine_name)
    if not routine:
        logger.error(f"Routine {routine_name} not found")
        return

    # Create a mock context for the routine
    class MockContext:
        def __init__(self):
            self._chat_id = chat_id
            self._instance_id = instance_id
            self._user_data = application.user_data.get(chat_id, {})
            self.bot = application.bot
            self.application = application

    context = MockContext()

    try:
        config = routine.config_class(**config_dict)
        logger.info(f"Starting continuous routine {routine_name}[{instance_id}]")
        result = await routine.run_fn(config, context)
        logger.info(f"Continuous routine {routine_name}[{instance_id}] ended: {result}")
    except asyncio.CancelledError:
        logger.info(f"Continuous routine {routine_name}[{instance_id}] cancelled")
    except Exception as e:
        logger.error(f"Continuous routine {routine_name}[{instance_id}] error: {e}")

    # Clean up instance when task ends
    instances = application.user_data.get(chat_id, {}).get("routine_instances", {})
    if instance_id in instances:
        del instances[instance_id]


async def _interval_job_callback(context: CallbackContext) -> None:
    """Job callback for interval-scheduled one-shot routines. Sends message each run."""
    data = context.job.data or {}
    instance_id = data["instance_id"]
    routine_name = data["routine_name"]
    config_dict = data["config_dict"]
    chat_id = data["chat_id"]

    # Check if instance still exists (may have been stopped)
    instances = context.application.user_data.get(chat_id, {}).get("routine_instances", {})
    if instance_id not in instances:
        logger.warning(f"Instance {instance_id} no longer exists, skipping execution")
        return

    result, duration = await _execute_routine(context, instance_id, routine_name, config_dict, chat_id)

    # Re-check instance exists after execution (may have been stopped during run)
    instances = context.application.user_data.get(chat_id, {}).get("routine_instances", {})
    if instance_id not in instances:
        logger.warning(f"Instance {instance_id} was removed during execution")
        return

    instances[instance_id]["last_run_at"] = time.time()
    instances[instance_id]["last_result"] = result
    instances[instance_id]["last_duration"] = duration
    run_count = instances[instance_id].get("run_count", 0) + 1
    instances[instance_id]["run_count"] = run_count

    # Send result message for scheduled one-shot routines
    schedule = instances[instance_id].get("schedule", {})
    interval_str = _format_schedule(schedule)
    icon = "✅" if not result.startswith("Error") else "❌"
    text = (
        f"{icon} *{escape_markdown_v2(_display_name(routine_name))}* `{instance_id}`\n"
        f"⏱️ {escape_markdown_v2(interval_str)} \\| Run \\#{run_count} \\| {escape_markdown_v2(_format_duration(duration))}\n\n"
        f"```\n{result[:400]}\n```"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="MarkdownV2")
    except Exception as e:
        logger.error(f"Failed to send interval result: {e}")

    logger.info(f"Routine {routine_name}[{instance_id}] run #{run_count}: {result[:50]}...")


async def _oneshot_job_callback(context: CallbackContext) -> None:
    """Job callback for one-time runs."""
    data = context.job.data or {}
    instance_id = data["instance_id"]
    routine_name = data["routine_name"]
    config_dict = data["config_dict"]
    chat_id = data["chat_id"]
    msg_id = data.get("msg_id")
    background = data.get("background", False)

    result, duration = await _execute_routine(context, instance_id, routine_name, config_dict, chat_id)

    # Remove one-shot instance after completion
    instances = context.application.user_data.get(chat_id, {}).get("routine_instances", {})
    if instance_id in instances:
        del instances[instance_id]

    if background:
        # Send result as new message
        icon = "✅" if not result.startswith("Error") else "❌"
        text = (
            f"{icon} *{escape_markdown_v2(_display_name(routine_name))}*\n"
            f"Duration: {escape_markdown_v2(_format_duration(duration))}\n\n"
            f"```\n{result[:400]}\n```"
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="MarkdownV2")
        except Exception as e:
            logger.error(f"Failed to send result: {e}")
    elif msg_id:
        # Update the detail view
        await _refresh_detail_msg(context, chat_id, msg_id, routine_name, result, duration)


async def _daily_job_callback(context: CallbackContext) -> None:
    """Job callback for daily-scheduled routines."""
    data = context.job.data or {}
    instance_id = data["instance_id"]
    routine_name = data["routine_name"]
    config_dict = data["config_dict"]
    chat_id = data["chat_id"]

    result, duration = await _execute_routine(context, instance_id, routine_name, config_dict, chat_id)

    # Update instance state
    instances = context.application.user_data.get(chat_id, {}).get("routine_instances", {})
    if instance_id in instances:
        instances[instance_id]["last_run_at"] = time.time()
        instances[instance_id]["last_result"] = result
        instances[instance_id]["last_duration"] = duration
        instances[instance_id]["run_count"] = instances[instance_id].get("run_count", 0) + 1

    # Send notification
    icon = "✅" if not result.startswith("Error") else "❌"
    text = (
        f"{icon} *Daily: {escape_markdown_v2(_display_name(routine_name))}*\n"
        f"```\n{result[:400]}\n```"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="MarkdownV2")
    except Exception as e:
        logger.error(f"Failed to send daily result: {e}")


def _create_scheduled_instance(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    routine_name: str,
    config_dict: dict,
    schedule: dict,
    msg_id: int | None = None,
    background: bool = False,
) -> str:
    """Create a scheduled one-shot instance. Returns instance_id."""
    instance_id = _generate_instance_id()
    job_name_str = _job_name(chat_id, instance_id)

    # Store instance
    instances = _get_instances(context)
    instances[instance_id] = {
        "routine_name": routine_name,
        "config": config_dict.copy(),
        "schedule": schedule.copy(),
        "status": "running",
        "created_at": time.time(),
        "last_run_at": None,
        "last_result": None,
        "last_duration": None,
        "run_count": 0,
    }

    job_data = {
        "instance_id": instance_id,
        "routine_name": routine_name,
        "config_dict": config_dict,
        "chat_id": chat_id,
        "msg_id": msg_id,
        "background": background,
    }

    stype = schedule.get("type", "once")

    if stype == "once":
        context.job_queue.run_once(
            _oneshot_job_callback,
            when=0.1,
            data=job_data,
            name=job_name_str,
            chat_id=chat_id,
        )
    elif stype == "interval":
        interval = schedule.get("interval_sec", 60)
        context.job_queue.run_repeating(
            _interval_job_callback,
            interval=interval,
            first=0.5,
            data=job_data,
            name=job_name_str,
            chat_id=chat_id,
        )
    elif stype == "daily":
        time_str = schedule.get("daily_time", "09:00")
        hour, minute = map(int, time_str.split(":"))
        context.job_queue.run_daily(
            _daily_job_callback,
            time=dt_time(hour=hour, minute=minute),
            data=job_data,
            name=job_name_str,
            chat_id=chat_id,
        )

    return instance_id


def _create_continuous_instance(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    routine_name: str,
    config_dict: dict,
) -> str:
    """Create a continuous routine instance. Returns instance_id."""
    instance_id = _generate_instance_id()

    # Store instance
    instances = _get_instances(context)
    instances[instance_id] = {
        "routine_name": routine_name,
        "config": config_dict.copy(),
        "schedule": {"type": "continuous"},
        "status": "running",
        "created_at": time.time(),
        "last_run_at": None,
        "last_result": None,
        "last_duration": None,
        "run_count": 0,
    }

    # Create and store asyncio task
    task = asyncio.create_task(
        _run_continuous_routine(
            context.application,
            instance_id,
            routine_name,
            config_dict,
            chat_id,
        )
    )
    _continuous_tasks[instance_id] = task

    return instance_id


# =============================================================================
# UI Display Functions
# =============================================================================


async def _edit_or_send(update: Update, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    """Edit message if callback, otherwise send new."""
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.warning(f"Edit failed: {e}")
    else:
        msg = update.message or update.callback_query.message
        await msg.reply_text(text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def _show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show main routines menu."""
    chat_id = update.effective_chat.id
    routines = discover_routines(force_reload=True)
    all_instances = _get_instances(context)

    # Count running instances per routine
    running_counts = {}
    for inst in all_instances.values():
        rname = inst.get("routine_name")
        if inst.get("status") == "running":
            running_counts[rname] = running_counts.get(rname, 0) + 1

    total_running = sum(running_counts.values())

    if not routines:
        text = (
            "⚡ *ROUTINES*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No routines found\\.\n\n"
            "Add Python files to `routines/` folder\\."
        )
        keyboard = [[InlineKeyboardButton("🔄 Reload", callback_data="routines:reload")]]
    else:
        keyboard = []

        if total_running > 0:
            keyboard.append([
                InlineKeyboardButton(f"📋 Running ({total_running})", callback_data="routines:tasks")
            ])

        for name in sorted(routines.keys()):
            routine = routines[name]
            count = running_counts.get(name, 0)

            if count > 0:
                label = f"🟢 {_display_name(name)} ({count})"
            else:
                icon = "♾️" if routine.is_continuous else "⚡"
                label = f"{icon} {_display_name(name)}"

            keyboard.append([InlineKeyboardButton(label, callback_data=f"routines:select:{name}")])

        keyboard.append([InlineKeyboardButton("🔄 Reload", callback_data="routines:reload")])

        status = f"🟢 {total_running} running" if total_running else "All idle"
        text = (
            "⚡ *ROUTINES*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Status: {escape_markdown_v2(status)}\n\n"
            "Select a routine to configure and run\\."
        )

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all running tasks."""
    chat_id = update.effective_chat.id
    instances = _get_instances(context)

    running = [(iid, inst) for iid, inst in instances.items() if inst.get("status") == "running"]

    if not running:
        text = (
            "📋 *RUNNING TASKS*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No tasks running\\."
        )
        keyboard = [[InlineKeyboardButton("« Back", callback_data="routines:menu")]]
    else:
        lines = ["📋 *RUNNING TASKS*", "━━━━━━━━━━━━━━━━━━━━━\n"]
        keyboard = []

        for iid, inst in running:
            name = inst["routine_name"]
            schedule = inst.get("schedule", {})
            created = inst.get("created_at", time.time())
            config = inst.get("config", {})
            run_count = inst.get("run_count", 0)

            lines.append(f"🟢 *{escape_markdown_v2(_display_name(name))}* `{iid}`")
            lines.append(f"   {escape_markdown_v2(_format_schedule(schedule))} \\| {escape_markdown_v2(_format_ago(created))}")
            lines.append(f"   Runs: {run_count} \\| `{escape_markdown_v2(_config_preview(config))}`")

            if inst.get("last_result"):
                result_preview = inst["last_result"][:40].replace("\n", " ")
                lines.append(f"   └ {escape_markdown_v2(result_preview)}\\.\\.\\.")
            lines.append("")

            keyboard.append([
                InlineKeyboardButton(f"⏹ {_display_name(name)[:12]}[{iid}]",
                                     callback_data=f"routines:stop:{iid}")
            ])

        keyboard.append([InlineKeyboardButton("⏹ Stop All", callback_data="routines:stopall")])
        keyboard.append([InlineKeyboardButton("« Back", callback_data="routines:menu")])
        text = "\n".join(lines)

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Show routine detail with draft config and running instances."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        if update.callback_query:
            await update.callback_query.answer("Routine not found")
        return

    # Get draft config
    draft = _get_draft(context, routine_name)
    fields = routine.get_fields()

    # Get running instances for this routine
    instances = _get_routine_instances(context, routine_name)
    running = [(iid, inst) for iid, inst in instances if inst.get("status") == "running"]

    # Build config display
    config_lines = [f"{k}={draft.get(k, v['default'])}" for k, v in fields.items()]

    # Status line
    if running:
        status = f"🟢 {len(running)} running"
    else:
        status = "⚪ Ready"

    # Type indicator
    type_str = "♾️ Continuous" if routine.is_continuous else "⚡ One\\-shot"

    # Build instances section
    inst_section = ""
    if running:
        inst_lines = ["\n┌─ Running Instances ────────"]
        for iid, inst in running[:5]:
            sched = _format_schedule(inst.get("schedule", {}))
            ago = _format_ago(inst.get("created_at", time.time()))
            cfg_prev = _config_preview(inst.get("config", {}), 1)
            runs = inst.get("run_count", 0)
            inst_lines.append(f"│ `{iid}` {escape_markdown_v2(cfg_prev)}")
            inst_lines.append(f"│   {escape_markdown_v2(sched)} \\| {runs} runs \\| {escape_markdown_v2(ago)}")
        if len(running) > 5:
            inst_lines.append(f"│ _\\+{len(running) - 5} more_")
        inst_lines.append("└────────────────────────────")
        inst_section = "\n".join(inst_lines)

    text = (
        f"⚡ *{escape_markdown_v2(_display_name(routine_name).upper())}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{escape_markdown_v2(routine.description)}_\n"
        f"{type_str}\n\n"
        f"Status: {escape_markdown_v2(status)}\n\n"
        f"┌─ Config ─────────────────\n"
        f"```\n{chr(10).join(config_lines)}\n```\n"
        f"└─ _✏️ send key\\=value to edit_"
        f"{inst_section}"
    )

    # Build keyboard based on routine type
    if routine.is_continuous:
        # Continuous routine - just start/stop
        keyboard = [
            [
                InlineKeyboardButton("▶️ Start", callback_data=f"routines:start:{routine_name}"),
            ],
        ]
    else:
        # One-shot routine - can be scheduled
        keyboard = [
            [
                InlineKeyboardButton("▶️ Run", callback_data=f"routines:run:{routine_name}"),
                InlineKeyboardButton("🔄 Background", callback_data=f"routines:bg:{routine_name}"),
            ],
            [
                InlineKeyboardButton("⏱️ Schedule", callback_data=f"routines:sched:{routine_name}"),
            ],
        ]

    if running:
        keyboard.append([
            InlineKeyboardButton(f"⏹ Stop All ({len(running)})", callback_data=f"routines:stopall:{routine_name}")
        ])

    keyboard.append([
        InlineKeyboardButton("❓ Help", callback_data=f"routines:help:{routine_name}"),
        InlineKeyboardButton("« Back", callback_data="routines:menu"),
    ])

    # Store editing state
    context.user_data["routines_state"] = "editing"
    context.user_data["routines_editing"] = {
        "routine": routine_name,
        "fields": fields,
    }

    msg = update.callback_query.message if update.callback_query else None
    if msg:
        context.user_data["routines_msg_id"] = msg.message_id
        context.user_data["routines_chat_id"] = msg.chat_id

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_schedule_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Show schedule options menu."""
    routine = get_routine(routine_name)
    if not routine:
        return

    text = (
        f"⏱️ *Schedule: {escape_markdown_v2(_display_name(routine_name))}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Choose how often to run this routine\\.\n"
        f"Config will be frozen at schedule time\\.\n"
        f"Results will be sent as messages\\."
    )

    # Interval buttons - 3 per row
    row1 = [
        InlineKeyboardButton(label, callback_data=f"routines:interval:{routine_name}:{secs}")
        for label, secs in SCHEDULE_PRESETS[:3]
    ]
    row2 = [
        InlineKeyboardButton(label, callback_data=f"routines:interval:{routine_name}:{secs}")
        for label, secs in SCHEDULE_PRESETS[3:6]
    ]

    keyboard = [
        row1,
        row2,
        [InlineKeyboardButton("📅 Daily...", callback_data=f"routines:daily:{routine_name}")],
        [InlineKeyboardButton("« Cancel", callback_data=f"routines:select:{routine_name}")],
    ]

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_daily_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Show daily schedule time options."""
    text = (
        f"📅 *Daily Schedule: {escape_markdown_v2(_display_name(routine_name))}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Choose a time \\(server timezone\\)\\.\n"
        f"Or send custom time as `HH:MM`\\."
    )

    # Time preset buttons
    row1 = [
        InlineKeyboardButton(t, callback_data=f"routines:dailyat:{routine_name}:{t}")
        for t in DAILY_PRESETS[:3]
    ]
    row2 = [
        InlineKeyboardButton(t, callback_data=f"routines:dailyat:{routine_name}:{t}")
        for t in DAILY_PRESETS[3:]
    ]

    keyboard = [
        row1,
        row2,
        [InlineKeyboardButton("« Back", callback_data=f"routines:sched:{routine_name}")],
    ]

    # Store state for custom time input
    context.user_data["routines_state"] = "daily_time"
    context.user_data["routines_editing"] = {"routine": routine_name}

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_help(update: Update, context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Show field descriptions."""
    routine = get_routine(routine_name)
    if not routine:
        return

    lines = [
        f"❓ *{escape_markdown_v2(_display_name(routine_name).upper())}*",
        "━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    for name, info in routine.get_fields().items():
        lines.append(f"• `{escape_markdown_v2(name)}` _{escape_markdown_v2(info['type'])}_")
        lines.append(f"  {escape_markdown_v2(info['description'])}")
        lines.append(f"  Default: `{escape_markdown_v2(str(info['default']))}`\n")

    keyboard = [[InlineKeyboardButton("« Back", callback_data=f"routines:select:{routine_name}")]]
    await _edit_or_send(update, "\n".join(lines), InlineKeyboardMarkup(keyboard))


async def _refresh_detail_msg(
    context: CallbackContext,
    chat_id: int,
    msg_id: int,
    routine_name: str,
    result: str | None = None,
    duration: float | None = None,
) -> None:
    """Refresh the routine detail message after execution."""
    routine = get_routine(routine_name)
    if not routine:
        return

    user_data = context.application.user_data.get(chat_id, {})
    drafts = user_data.get("routine_drafts", {})
    draft = drafts.get(routine_name, {})

    if not draft:
        draft = routine.get_default_config().model_dump()

    fields = routine.get_fields()
    config_lines = [f"{k}={draft.get(k, v['default'])}" for k, v in fields.items()]

    # Get instances
    instances = user_data.get("routine_instances", {})
    running = [(iid, inst) for iid, inst in instances.items()
               if inst.get("routine_name") == routine_name and inst.get("status") == "running"]

    status = f"🟢 {len(running)} running" if running else "⚪ Ready"
    type_str = "♾️ Continuous" if routine.is_continuous else "⚡ One\\-shot"

    # Result section
    result_section = ""
    if result is not None:
        icon = "❌" if result.startswith("Error") else "✅"
        dur_str = _format_duration(duration) if duration else ""
        result_section = (
            f"\n\n┌─ {icon} Result ─ {escape_markdown_v2(dur_str)} ────\n"
            f"```\n{result[:250]}\n```\n"
            f"└────────────────────────────"
        )

    text = (
        f"⚡ *{escape_markdown_v2(_display_name(routine_name).upper())}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{escape_markdown_v2(routine.description)}_\n"
        f"{type_str}\n\n"
        f"Status: {escape_markdown_v2(status)}\n\n"
        f"┌─ Config ─────────────────\n"
        f"```\n{chr(10).join(config_lines)}\n```\n"
        f"└─ _✏️ send key\\=value to edit_"
        f"{result_section}"
    )

    # Build keyboard based on routine type
    if routine.is_continuous:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Start", callback_data=f"routines:start:{routine_name}"),
            ],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Run", callback_data=f"routines:run:{routine_name}"),
                InlineKeyboardButton("🔄 Background", callback_data=f"routines:bg:{routine_name}"),
            ],
            [
                InlineKeyboardButton("⏱️ Schedule", callback_data=f"routines:sched:{routine_name}"),
            ],
        ]

    if running:
        keyboard.append([
            InlineKeyboardButton(f"⏹ Stop All ({len(running)})", callback_data=f"routines:stopall:{routine_name}")
        ])

    keyboard.append([
        InlineKeyboardButton("❓ Help", callback_data=f"routines:help:{routine_name}"),
        InlineKeyboardButton("« Back", callback_data="routines:menu"),
    ])

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.debug(f"Could not refresh: {e}")


# =============================================================================
# Actions
# =============================================================================


async def _run_once(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    routine_name: str,
    background: bool = False,
) -> None:
    """Run one-shot routine once with current draft config."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    if routine.is_continuous:
        await update.callback_query.answer("Use Start for continuous routines")
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    msg_id = context.user_data.get("routines_msg_id")
    schedule = {"type": "once"}

    _create_scheduled_instance(context, chat_id, routine_name, draft, schedule, msg_id, background)

    if background:
        await update.callback_query.answer("🔄 Running in background...")
    else:
        await update.callback_query.answer("▶️ Running...")

    await _show_detail(update, context, routine_name)


async def _start_continuous(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    routine_name: str,
) -> None:
    """Start a continuous routine."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    if not routine.is_continuous:
        await update.callback_query.answer("Not a continuous routine")
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    instance_id = _create_continuous_instance(context, chat_id, routine_name, draft)
    logger.info(f"Started continuous routine {instance_id}: {routine_name}")

    await update.callback_query.answer("▶️ Started")
    await _show_detail(update, context, routine_name)


async def _start_interval(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    routine_name: str,
    interval_sec: int,
) -> None:
    """Start one-shot routine with interval schedule."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    schedule = {"type": "interval", "interval_sec": interval_sec}
    instance_id = _create_scheduled_instance(context, chat_id, routine_name, draft, schedule)
    logger.info(f"Created interval schedule {instance_id} for {routine_name}: every {interval_sec}s")

    await update.callback_query.answer(f"⏱️ Scheduled every {_format_schedule(schedule)}")
    await _show_detail(update, context, routine_name)


async def _start_daily(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    routine_name: str,
    time_str: str,
) -> None:
    """Start routine with daily schedule."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    # Validate time format
    try:
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError()
    except (ValueError, AttributeError):
        await update.callback_query.answer(f"Invalid time: {time_str}")
        return

    schedule = {"type": "daily", "daily_time": time_str}
    instance_id = _create_scheduled_instance(context, chat_id, routine_name, draft, schedule)
    logger.info(f"Created daily schedule {instance_id} for {routine_name}: at {time_str}")

    await update.callback_query.answer(f"📅 Scheduled daily at {time_str}")
    await _show_detail(update, context, routine_name)


# =============================================================================
# Config Input Processing
# =============================================================================


async def _process_config(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Process key=value config input."""
    editing = context.user_data.get("routines_editing", {})
    routine_name = editing.get("routine")
    fields = editing.get("fields", {})

    if not routine_name:
        return

    # Delete user message
    try:
        await update.message.delete()
    except Exception:
        pass

    routine = get_routine(routine_name)
    if not routine:
        return

    if not fields:
        fields = routine.get_fields()

    draft = _get_draft(context, routine_name)
    updates = {}
    errors = []

    for line in text.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()

        if key not in fields:
            errors.append(f"Unknown: {key}")
            continue

        field_type = fields[key]["type"]
        try:
            if field_type == "int":
                value = int(value)
            elif field_type == "float":
                value = float(value)
            elif field_type == "bool":
                value = value.lower() in ("true", "yes", "1", "on")
        except ValueError:
            errors.append(f"Invalid: {key}")
            continue

        updates[key] = value

    if errors:
        msg = await update.message.reply_text(f"⚠️ {', '.join(errors)}")
        asyncio.create_task(_delete_after(msg, 3))

    if not updates:
        msg = await update.message.reply_text("❌ Use: `key=value`", parse_mode="Markdown")
        asyncio.create_task(_delete_after(msg, 3))
        return

    draft.update(updates)
    _set_draft(context, routine_name, draft)

    msg = await update.message.reply_text(
        f"✅ {', '.join(f'`{k}={v}`' for k, v in updates.items())}",
        parse_mode="Markdown",
    )
    asyncio.create_task(_delete_after(msg, 2))

    # Refresh detail view
    msg_id = context.user_data.get("routines_msg_id")
    chat_id = context.user_data.get("routines_chat_id")
    if msg_id and chat_id:
        await _refresh_detail_msg(context, chat_id, msg_id, routine_name)


async def _process_daily_time(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Process custom daily time input (HH:MM)."""
    editing = context.user_data.get("routines_editing", {})
    routine_name = editing.get("routine")

    if not routine_name:
        return

    # Delete user message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Validate time format
    text = text.strip()
    try:
        hour, minute = map(int, text.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError()
        time_str = f"{hour:02d}:{minute:02d}"
    except (ValueError, AttributeError):
        msg = await update.message.reply_text(f"❌ Invalid time. Use `HH:MM` format.", parse_mode="Markdown")
        asyncio.create_task(_delete_after(msg, 3))
        return

    # Create daily schedule
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)
    if not routine:
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        msg = await update.message.reply_text(f"❌ Config error: {e}")
        asyncio.create_task(_delete_after(msg, 3))
        return

    schedule = {"type": "daily", "daily_time": time_str}
    _create_scheduled_instance(context, chat_id, routine_name, draft, schedule)

    msg = await update.message.reply_text(f"📅 Scheduled daily at {time_str}")
    asyncio.create_task(_delete_after(msg, 2))

    context.user_data["routines_state"] = "editing"

    # Refresh detail
    msg_id = context.user_data.get("routines_msg_id")
    if msg_id:
        await _refresh_detail_msg(context, chat_id, msg_id, routine_name)


# =============================================================================
# Helpers
# =============================================================================


async def _delete_after(message, seconds: float) -> None:
    """Delete message after delay."""
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except Exception:
        pass


# =============================================================================
# Handlers
# =============================================================================


@restricted
async def routines_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /routines command."""
    clear_all_input_states(context)
    await _show_menu(update, context)


@restricted
async def routines_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    parts = query.data.split(":")

    if len(parts) < 2:
        await query.answer()
        return

    action = parts[1]

    if action == "menu":
        await query.answer()
        context.user_data.pop("routines_state", None)
        context.user_data.pop("routines_editing", None)
        await _show_menu(update, context)

    elif action == "reload":
        await query.answer("Reloading...")
        discover_routines(force_reload=True)
        await _show_menu(update, context)

    elif action == "tasks":
        await query.answer()
        await _show_tasks(update, context)

    elif action == "select" and len(parts) >= 3:
        await query.answer()
        await _show_detail(update, context, parts[2])

    elif action == "run" and len(parts) >= 3:
        await _run_once(update, context, parts[2], background=False)

    elif action == "bg" and len(parts) >= 3:
        await _run_once(update, context, parts[2], background=True)

    elif action == "start" and len(parts) >= 3:
        # Start continuous routine
        await _start_continuous(update, context, parts[2])

    elif action == "sched" and len(parts) >= 3:
        await query.answer()
        await _show_schedule_menu(update, context, parts[2])

    elif action == "interval" and len(parts) >= 4:
        routine_name = parts[2]
        interval_sec = int(parts[3])
        await _start_interval(update, context, routine_name, interval_sec)

    elif action == "daily" and len(parts) >= 3:
        await query.answer()
        await _show_daily_menu(update, context, parts[2])

    elif action == "dailyat" and len(parts) >= 4:
        routine_name = parts[2]
        time_str = parts[3]
        await _start_daily(update, context, routine_name, time_str)

    elif action == "stop" and len(parts) >= 3:
        instance_id = parts[2]
        logger.info(f"User {chat_id} stopping instance {instance_id}")
        if _stop_instance(context, chat_id, instance_id):
            await query.answer("⏹ Stopped")
        else:
            await query.answer("Not found")
        await _show_tasks(update, context)

    elif action == "stopall" and len(parts) >= 3:
        routine_name = parts[2]
        logger.info(f"User {chat_id} stopping all instances of {routine_name}")
        count = _stop_all_routine(context, chat_id, routine_name)
        await query.answer(f"⏹ Stopped {count}")
        await _show_detail(update, context, routine_name)

    elif action == "stopall":
        logger.info(f"User {chat_id} stopping ALL instances")
        instances = _get_instances(context)
        count = 0
        for iid in list(instances.keys()):
            if _stop_instance(context, chat_id, iid):
                count += 1
        await query.answer(f"⏹ Stopped {count}")
        await _show_tasks(update, context)

    elif action == "help" and len(parts) >= 3:
        await query.answer()
        await _show_help(update, context, parts[2])

    else:
        await query.answer()


async def routines_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input for config editing or daily time."""
    state = context.user_data.get("routines_state")

    if state == "editing":
        await _process_config(update, context, update.message.text.strip())
        return True
    elif state == "daily_time":
        await _process_daily_time(update, context, update.message.text.strip())
        return True

    return False


async def restore_scheduled_jobs(application) -> int:
    """
    Restore scheduled jobs from persisted instances after bot restart.
    Call this during application startup (post_init).
    Returns count of restored jobs.
    """
    restored = 0
    removed = 0

    for chat_id, user_data in application.user_data.items():
        instances = user_data.get("routine_instances", {})
        if not instances:
            continue

        to_remove = []

        for instance_id, inst in instances.items():
            if inst.get("status") != "running":
                continue

            routine_name = inst.get("routine_name")
            config_dict = inst.get("config", {})
            schedule = inst.get("schedule", {})
            stype = schedule.get("type", "once")

            # Check if routine still exists
            routine = get_routine(routine_name)
            if not routine:
                logger.warning(f"Routine {routine_name} no longer exists, removing instance {instance_id}")
                to_remove.append(instance_id)
                continue

            # One-time jobs that didn't complete - remove them
            if stype == "once":
                to_remove.append(instance_id)
                continue

            # Continuous routines need to be restarted as asyncio tasks
            if stype == "continuous":
                try:
                    task = asyncio.create_task(
                        _run_continuous_routine(
                            application,
                            instance_id,
                            routine_name,
                            config_dict,
                            chat_id,
                        )
                    )
                    _continuous_tasks[instance_id] = task
                    restored += 1
                    logger.info(f"Restored continuous routine {instance_id}: {routine_name}")
                except Exception as e:
                    logger.error(f"Failed to restore continuous routine {instance_id}: {e}")
                    to_remove.append(instance_id)
                continue

            # Re-create scheduled jobs
            job_name_str = _job_name(chat_id, instance_id)
            job_data = {
                "instance_id": instance_id,
                "routine_name": routine_name,
                "config_dict": config_dict,
                "chat_id": chat_id,
            }

            try:
                if stype == "interval":
                    interval = schedule.get("interval_sec", 60)
                    application.job_queue.run_repeating(
                        _interval_job_callback,
                        interval=interval,
                        first=min(interval, 10),
                        data=job_data,
                        name=job_name_str,
                        chat_id=chat_id,
                    )
                    restored += 1
                    logger.info(f"Restored interval job {instance_id} for {routine_name} (every {interval}s)")

                elif stype == "daily":
                    time_str = schedule.get("daily_time", "09:00")
                    hour, minute = map(int, time_str.split(":"))
                    application.job_queue.run_daily(
                        _daily_job_callback,
                        time=dt_time(hour=hour, minute=minute),
                        data=job_data,
                        name=job_name_str,
                        chat_id=chat_id,
                    )
                    restored += 1
                    logger.info(f"Restored daily job {instance_id} for {routine_name} (at {time_str})")

                else:
                    to_remove.append(instance_id)

            except Exception as e:
                logger.error(f"Failed to restore job {instance_id}: {e}")
                to_remove.append(instance_id)

        # Clean up stale instances
        for instance_id in to_remove:
            del instances[instance_id]
            removed += 1

    if restored > 0 or removed > 0:
        logger.info(f"Routine jobs: restored {restored}, removed {removed} stale")

    return restored


__all__ = ["routines_command", "routines_callback_handler", "routines_message_handler", "restore_scheduled_jobs"]
