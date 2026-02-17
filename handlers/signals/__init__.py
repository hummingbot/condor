"""
Signals Handler - ML prediction pipelines via Telegram.

Features:
- Auto-discovery of signals from signals/ folder
- Training and prediction pipelines
- Text-based config editing (key=value)
- Background execution for training
- Scheduling for predictions
- SQLite storage for prediction history
"""

import asyncio
import hashlib
import logging
import time
from datetime import time as dt_time

from signals.base import discover_signals, get_latest_model_path, get_signal
from signals.db import get_signals_db
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, ContextTypes

from handlers import clear_all_input_states
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

# Global storage for running tasks (not persisted)
_running_tasks: dict[str, asyncio.Task] = {}  # instance_id -> Task


# =============================================================================
# Storage Helpers
# =============================================================================


def _get_drafts(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Get draft configs dict."""
    if "signals_drafts" not in context.user_data:
        context.user_data["signals_drafts"] = {}
    return context.user_data["signals_drafts"]


def _get_instances(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Get all instances dict."""
    if "signals_instances" not in context.user_data:
        context.user_data["signals_instances"] = {}
    return context.user_data["signals_instances"]


def _get_draft(
    context: ContextTypes.DEFAULT_TYPE, signal_name: str, pipeline: str
) -> dict:
    """Get draft config for a signal pipeline, initializing from defaults if needed."""
    drafts = _get_drafts(context)
    key = f"{signal_name}:{pipeline}"
    if key not in drafts:
        signal = get_signal(signal_name)
        if signal:
            pipe = (
                signal.train_pipeline
                if pipeline == "train"
                else signal.predict_pipeline
            )
            if pipe:
                drafts[key] = pipe.get_default_config().model_dump()
            else:
                drafts[key] = {}
        else:
            drafts[key] = {}
    return drafts[key]


def _set_draft(
    context: ContextTypes.DEFAULT_TYPE, signal_name: str, pipeline: str, config: dict
) -> None:
    """Update draft config for a signal pipeline."""
    drafts = _get_drafts(context)
    key = f"{signal_name}:{pipeline}"
    drafts[key] = config


def _get_signal_instances(
    context: ContextTypes.DEFAULT_TYPE, signal_name: str
) -> list[tuple[str, dict]]:
    """Get all instances for a specific signal."""
    instances = _get_instances(context)
    return [
        (iid, inst)
        for iid, inst in instances.items()
        if inst.get("signal_name") == signal_name
    ]


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
    return f"signal_{chat_id}_{instance_id}"


def _find_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, instance_id: str):
    """Find a job by instance ID."""
    name = _job_name(chat_id, instance_id)
    jobs = context.job_queue.get_jobs_by_name(name)
    return jobs[0] if jobs else None


def _stop_instance(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, instance_id: str
) -> bool:
    """Stop a job/task and remove instance. Returns True if found."""
    # Try to stop JobQueue job
    job = _find_job(context, chat_id, instance_id)
    if job:
        job.schedule_removal()
        logger.info(f"Removed scheduled job for instance {instance_id}")

    # Try to cancel asyncio task
    task = _running_tasks.pop(instance_id, None)
    if task and not task.done():
        task.cancel()
        logger.info(f"Cancelled task for instance {instance_id}")

    # Remove from instances
    instances = _get_instances(context)
    if instance_id in instances:
        signal_name = instances[instance_id].get("signal_name", "unknown")
        del instances[instance_id]
        logger.info(f"Stopped instance {instance_id} ({signal_name})")
        return True
    return False


# =============================================================================
# Pipeline Execution
# =============================================================================


async def _execute_pipeline(
    context: ContextTypes.DEFAULT_TYPE,
    instance_id: str,
    signal_name: str,
    pipeline: str,
    config_dict: dict,
    chat_id: int,
) -> tuple[str, float]:
    """Execute a pipeline and return (result, duration)."""
    signal = get_signal(signal_name)
    if not signal:
        return f"Signal {signal_name} not found", 0

    pipe = signal.train_pipeline if pipeline == "train" else signal.predict_pipeline
    if not pipe:
        return f"Pipeline {pipeline} not found for {signal_name}", 0

    # Prepare context
    context._chat_id = chat_id
    context._instance_id = instance_id
    context._user_data = context.user_data if hasattr(context, "user_data") else {}

    start = time.time()
    try:
        config = pipe.config_class(**config_dict)
        result = await pipe.run_fn(config, context)
        result_text = str(result)[:2000]  # Truncate long results
    except Exception as e:
        logger.error(f"Pipeline {pipeline} failed: {e}", exc_info=True)
        result_text = f"Error: {e}"

    duration = time.time() - start
    return result_text, duration


async def _run_pipeline_background(
    application,
    instance_id: str,
    signal_name: str,
    pipeline: str,
    config_dict: dict,
    chat_id: int,
) -> None:
    """Run a pipeline as a background task."""

    # Create a mock context
    class MockContext:
        def __init__(self):
            self._chat_id = chat_id
            self._instance_id = instance_id
            self._user_data = application.user_data.get(chat_id, {})
            self.bot = application.bot
            self.application = application
            self.user_data = self._user_data

    context = MockContext()

    try:
        result, duration = await _execute_pipeline(
            context, instance_id, signal_name, pipeline, config_dict, chat_id
        )

        # Update instance
        instances = application.user_data.get(chat_id, {}).get("signals_instances", {})
        if instance_id in instances:
            instances[instance_id]["last_result"] = result
            instances[instance_id]["last_duration"] = duration
            instances[instance_id]["last_run_at"] = time.time()
            instances[instance_id]["run_count"] = (
                instances[instance_id].get("run_count", 0) + 1
            )

        # Send result message
        result_preview = result[:500] if len(result) > 500 else result
        await application.bot.send_message(
            chat_id,
            f"*{escape_markdown_v2(signal_name.upper())} \\- {pipeline.upper()}*\n\n"
            f"```\n{escape_markdown_v2(result_preview)}\n```\n\n"
            f"Duration: {escape_markdown_v2(_format_duration(duration))}",
            parse_mode="MarkdownV2",
        )

    except asyncio.CancelledError:
        logger.info(f"Pipeline {signal_name}:{pipeline} cancelled")
    except Exception as e:
        logger.error(f"Background pipeline failed: {e}", exc_info=True)
        try:
            await application.bot.send_message(
                chat_id, f"Pipeline {signal_name}:{pipeline} failed: {e}"
            )
        except Exception:
            pass


def _create_background_instance(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    signal_name: str,
    pipeline: str,
    config_dict: dict,
) -> str:
    """Create a background execution instance. Returns instance_id."""
    instance_id = _generate_instance_id()
    instances = _get_instances(context)

    instances[instance_id] = {
        "signal_name": signal_name,
        "pipeline": pipeline,
        "config": config_dict.copy(),
        "schedule": {"type": "once"},
        "status": "running",
        "created_at": time.time(),
        "run_count": 0,
    }

    # Create task
    task = asyncio.create_task(
        _run_pipeline_background(
            context.application,
            instance_id,
            signal_name,
            pipeline,
            config_dict,
            chat_id,
        )
    )
    _running_tasks[instance_id] = task

    return instance_id


# =============================================================================
# Scheduled Execution
# =============================================================================


async def _interval_job_callback(context: CallbackContext) -> None:
    """Callback for interval-scheduled pipelines."""
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    instance_id = job_data["instance_id"]
    signal_name = job_data["signal_name"]
    pipeline = job_data["pipeline"]
    config_dict = job_data["config"]

    instances = context.application.user_data.get(chat_id, {}).get(
        "signals_instances", {}
    )
    if instance_id not in instances:
        context.job.schedule_removal()
        return

    # Run pipeline
    result, duration = await _execute_pipeline(
        context, instance_id, signal_name, pipeline, config_dict, chat_id
    )

    # Update instance
    instances[instance_id]["last_result"] = result
    instances[instance_id]["last_duration"] = duration
    instances[instance_id]["last_run_at"] = time.time()
    instances[instance_id]["run_count"] = instances[instance_id].get("run_count", 0) + 1

    # Send result
    result_preview = result[:300] if len(result) > 300 else result
    try:
        await context.bot.send_message(
            chat_id,
            f"*{escape_markdown_v2(signal_name.upper())} \\- {pipeline.upper()}*\n\n"
            f"```\n{escape_markdown_v2(result_preview)}\n```",
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"Failed to send result: {e}")


def _create_scheduled_instance(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    signal_name: str,
    pipeline: str,
    config_dict: dict,
    schedule: dict,
) -> str:
    """Create a scheduled instance. Returns instance_id."""
    instance_id = _generate_instance_id()
    instances = _get_instances(context)

    instances[instance_id] = {
        "signal_name": signal_name,
        "pipeline": pipeline,
        "config": config_dict.copy(),
        "schedule": schedule,
        "status": "running",
        "created_at": time.time(),
        "run_count": 0,
    }

    job_data = {
        "chat_id": chat_id,
        "instance_id": instance_id,
        "signal_name": signal_name,
        "pipeline": pipeline,
        "config": config_dict.copy(),
    }

    job_name = _job_name(chat_id, instance_id)
    stype = schedule.get("type")

    if stype == "interval":
        interval = schedule.get("interval_sec", 60)
        context.job_queue.run_repeating(
            _interval_job_callback,
            interval=interval,
            first=interval,
            data=job_data,
            name=job_name,
            chat_id=chat_id,
        )
    elif stype == "daily":
        time_str = schedule.get("daily_time", "09:00")
        hour, minute = map(int, time_str.split(":"))
        context.job_queue.run_daily(
            _interval_job_callback,
            time=dt_time(hour=hour, minute=minute),
            data=job_data,
            name=job_name,
            chat_id=chat_id,
        )

    return instance_id


# =============================================================================
# UI Display
# =============================================================================


async def _edit_or_send(
    update: Update, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
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
    """Show main signals menu."""
    signals = discover_signals(force_reload=True)
    instances = _get_instances(context)
    db = get_signals_db()

    # Count running instances
    running_count = sum(
        1 for inst in instances.values() if inst.get("status") == "running"
    )

    if not signals:
        text = (
            "📊 *SIGNALS*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No signals found\\.\n\n"
            "Add folders to `signals/` with pipelines\\."
        )
        keyboard = [[InlineKeyboardButton("🔄 Reload", callback_data="signals:reload")]]
    else:
        keyboard = []

        for name in sorted(signals.keys()):
            signal = signals[name]
            pred_count = db.get_count(name)
            model_path = get_latest_model_path(name)

            # Build label
            icons = []
            if signal.has_train:
                icons.append("🔧")
            if signal.has_predict:
                icons.append("📊")

            model_info = "✓" if model_path else ""
            label = f"{''.join(icons)} {_display_name(name)} {model_info}"

            keyboard.append(
                [InlineKeyboardButton(label, callback_data=f"signals:select:{name}")]
            )

        # Footer buttons
        footer = []
        if running_count > 0:
            footer.append(
                InlineKeyboardButton(
                    f"Running ({running_count})", callback_data="signals:tasks"
                )
            )
        footer.append(InlineKeyboardButton("🔄 Reload", callback_data="signals:reload"))
        keyboard.append(footer)

        text = (
            "📊 *SIGNALS*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Select a signal to train or predict\\.\n\n"
            f"🔧 Train \\| 📊 Predict \\| ✓ Has model"
        )

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, signal_name: str
) -> None:
    """Show signal detail view."""
    signal = get_signal(signal_name)
    if not signal:
        if update.callback_query:
            await update.callback_query.answer("Signal not found")
        return

    db = get_signals_db()
    pred_count = db.get_count(signal_name)
    model_path = get_latest_model_path(signal_name)
    instances = _get_signal_instances(context, signal_name)
    running = [
        (iid, inst) for iid, inst in instances if inst.get("status") == "running"
    ]

    # Model info
    if model_path:
        model_info = f"Latest model: `{escape_markdown_v2(model_path.name)}`"
    else:
        model_info = "_No trained model yet_"

    text = (
        f"📊 *{escape_markdown_v2(_display_name(signal_name).upper())}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{escape_markdown_v2(signal.description)}_\n\n"
        f"{model_info}\n"
        f"Predictions: {pred_count} total\n"
    )

    if running:
        text += f"\n🟢 {len(running)} running\n"

    keyboard = []

    # Pipeline buttons
    row = []
    if signal.has_train:
        row.append(
            InlineKeyboardButton(
                "🔧 Train/Eval", callback_data=f"signals:train:{signal_name}"
            )
        )
    if signal.has_predict:
        row.append(
            InlineKeyboardButton(
                "📊 Predict", callback_data=f"signals:predict:{signal_name}"
            )
        )
    if row:
        keyboard.append(row)

    # History and stop buttons
    row2 = []
    if pred_count > 0:
        row2.append(
            InlineKeyboardButton(
                "📜 History", callback_data=f"signals:history:{signal_name}"
            )
        )
    if running:
        row2.append(
            InlineKeyboardButton(
                f"⏹ Stop ({len(running)})",
                callback_data=f"signals:stopall:{signal_name}",
            )
        )
    if row2:
        keyboard.append(row2)

    keyboard.append([InlineKeyboardButton("« Back", callback_data="signals:menu")])

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_pipeline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    signal_name: str,
    pipeline: str,
) -> None:
    """Show pipeline config editor."""
    signal = get_signal(signal_name)
    if not signal:
        return

    pipe = signal.train_pipeline if pipeline == "train" else signal.predict_pipeline
    if not pipe:
        return

    # Set editing state
    context.user_data["signals_state"] = "editing"
    context.user_data["signals_editing"] = {"signal": signal_name, "pipeline": pipeline}

    draft = _get_draft(context, signal_name, pipeline)
    fields = pipe.get_fields()

    # Build config display
    config_lines = [f"{k}={draft.get(k, v['default'])}" for k, v in fields.items()]

    pipeline_label = "TRAIN" if pipeline == "train" else "PREDICT"
    text = (
        f"📊 *{escape_markdown_v2(_display_name(signal_name).upper())} \\- {pipeline_label}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{escape_markdown_v2(pipe.description)}_\n\n"
        f"```\n{chr(10).join(config_lines)}\n```\n\n"
        f"_✏️ Send key\\=value to edit_"
    )

    keyboard = []

    if pipeline == "train":
        # Training always runs in background
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🚀 Start Training",
                    callback_data=f"signals:run_train:{signal_name}",
                )
            ]
        )
    else:
        # Predict can run immediately or be scheduled
        keyboard.append(
            [
                InlineKeyboardButton(
                    "▶️ Run", callback_data=f"signals:run_predict:{signal_name}"
                ),
                InlineKeyboardButton(
                    "🔄 Background", callback_data=f"signals:bg_predict:{signal_name}"
                ),
            ]
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    "⏱️ Schedule", callback_data=f"signals:sched:{signal_name}"
                ),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "❓ Help", callback_data=f"signals:help:{signal_name}:{pipeline}"
            ),
            InlineKeyboardButton(
                "« Back", callback_data=f"signals:select:{signal_name}"
            ),
        ]
    )

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_schedule_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, signal_name: str
) -> None:
    """Show schedule options for prediction."""
    text = (
        f"⏱️ *SCHEDULE: {escape_markdown_v2(_display_name(signal_name).upper())}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose interval or daily time\\."
    )

    keyboard = []

    # Interval presets
    row = []
    for label, secs in SCHEDULE_PRESETS:
        row.append(
            InlineKeyboardButton(
                label, callback_data=f"signals:interval:{signal_name}:{secs}"
            )
        )
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Daily option
    keyboard.append(
        [
            InlineKeyboardButton(
                "📅 Daily...", callback_data=f"signals:daily:{signal_name}"
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                "« Cancel", callback_data=f"signals:predict:{signal_name}"
            )
        ]
    )

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_daily_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, signal_name: str
) -> None:
    """Show daily time selection."""
    text = (
        f"📅 *DAILY SCHEDULE*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select time or send custom \\(HH:MM\\)\\."
    )

    context.user_data["signals_state"] = "daily_time"
    context.user_data["signals_editing"] = {
        "signal": signal_name,
        "pipeline": "predict",
    }

    keyboard = []
    row = []
    for time_str in DAILY_PRESETS:
        row.append(
            InlineKeyboardButton(
                time_str, callback_data=f"signals:dailyat:{signal_name}:{time_str}"
            )
        )
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append(
        [InlineKeyboardButton("« Cancel", callback_data=f"signals:sched:{signal_name}")]
    )

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_history(
    update: Update, context: ContextTypes.DEFAULT_TYPE, signal_name: str
) -> None:
    """Show prediction history."""
    db = get_signals_db()
    predictions = db.get_predictions(signal_name, limit=10)
    total = db.get_count(signal_name)

    if not predictions:
        text = (
            f"📜 *{escape_markdown_v2(_display_name(signal_name).upper())} \\- HISTORY*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No predictions yet\\."
        )
    else:
        lines = [
            f"📜 *{escape_markdown_v2(_display_name(signal_name).upper())} \\- HISTORY*",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"_{total} predictions total_\n",
        ]

        for pred in predictions:
            time_str = pred.created_at.strftime("%m/%d %H:%M")
            result_preview = pred.result[:50].replace("\n", " ")
            lines.append(
                f"• `{escape_markdown_v2(time_str)}` {escape_markdown_v2(result_preview)}"
            )

        text = "\n".join(lines)

    keyboard = [
        [InlineKeyboardButton("« Back", callback_data=f"signals:select:{signal_name}")]
    ]
    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    signal_name: str,
    pipeline: str,
) -> None:
    """Show field descriptions for a pipeline."""
    signal = get_signal(signal_name)
    if not signal:
        return

    pipe = signal.train_pipeline if pipeline == "train" else signal.predict_pipeline
    if not pipe:
        return

    fields = pipe.get_fields()
    lines = [
        f"❓ *{escape_markdown_v2(_display_name(signal_name).upper())} \\- HELP*",
        "━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    for name, info in fields.items():
        lines.append(f"• `{name}` _{escape_markdown_v2(info['type'])}_")
        lines.append(f"  {escape_markdown_v2(info['description'])}")
        lines.append(f"  Default: `{escape_markdown_v2(str(info['default']))}`\n")

    text = "\n".join(lines)
    keyboard = [
        [
            InlineKeyboardButton(
                "« Back", callback_data=f"signals:{pipeline}:{signal_name}"
            )
        ]
    ]
    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all running tasks."""
    instances = _get_instances(context)
    running = [
        (iid, inst)
        for iid, inst in instances.items()
        if inst.get("status") == "running"
    ]

    if not running:
        text = "📋 *RUNNING TASKS*\n" "━━━━━━━━━━━━━━━━━━━━━\n\n" "No tasks running\\."
        keyboard = [[InlineKeyboardButton("« Back", callback_data="signals:menu")]]
    else:
        lines = ["📋 *RUNNING TASKS*", "━━━━━━━━━━━━━━━━━━━━━\n"]
        keyboard = []

        for iid, inst in running:
            name = inst["signal_name"]
            pipeline = inst.get("pipeline", "predict")
            schedule = inst.get("schedule", {})
            created = inst.get("created_at", time.time())
            run_count = inst.get("run_count", 0)

            lines.append(f"🟢 *{escape_markdown_v2(_display_name(name))}* `{iid}`")
            lines.append(
                f"   {pipeline} \\| {escape_markdown_v2(_format_schedule(schedule))} \\| {run_count} runs"
            )
            lines.append("")

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"⏹ {_display_name(name)[:12]}[{iid}]",
                        callback_data=f"signals:stop:{iid}",
                    )
                ]
            )

        keyboard.append(
            [InlineKeyboardButton("⏹ Stop All", callback_data="signals:stopall")]
        )
        keyboard.append([InlineKeyboardButton("« Back", callback_data="signals:menu")])
        text = "\n".join(lines)

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


# =============================================================================
# Action Handlers
# =============================================================================


async def _run_train(
    update: Update, context: ContextTypes.DEFAULT_TYPE, signal_name: str
) -> None:
    """Start training in background."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    draft = _get_draft(context, signal_name, "train")

    await query.answer("🚀 Training started...")

    instance_id = _create_background_instance(
        context, chat_id, signal_name, "train", draft
    )

    await query.message.reply_text(
        f"🚀 *Training Started*\n\n"
        f"Signal: {escape_markdown_v2(signal_name)}\n"
        f"Instance: `{instance_id}`\n\n"
        f"_This may take a while\\. You'll receive a message when complete\\._",
        parse_mode="MarkdownV2",
    )


async def _run_predict(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    signal_name: str,
    background: bool,
) -> None:
    """Run prediction."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    draft = _get_draft(context, signal_name, "predict")

    if background:
        await query.answer("🔄 Running in background...")
        instance_id = _create_background_instance(
            context, chat_id, signal_name, "predict", draft
        )
        await query.message.reply_text(
            f"🔄 *Prediction Running*\n\n" f"Instance: `{instance_id}`",
            parse_mode="MarkdownV2",
        )
    else:
        await query.answer("▶️ Running...")

        # Run directly
        result, duration = await _execute_pipeline(
            context, "direct", signal_name, "predict", draft, chat_id
        )

        result_preview = result[:1000] if len(result) > 1000 else result
        await query.message.reply_text(
            f"📊 *{escape_markdown_v2(signal_name.upper())} \\- PREDICTION*\n\n"
            f"```\n{escape_markdown_v2(result_preview)}\n```\n\n"
            f"Duration: {escape_markdown_v2(_format_duration(duration))}",
            parse_mode="MarkdownV2",
        )


async def _start_interval(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    signal_name: str,
    interval_sec: int,
) -> None:
    """Start interval-scheduled prediction."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    draft = _get_draft(context, signal_name, "predict")
    schedule = {"type": "interval", "interval_sec": interval_sec}

    instance_id = _create_scheduled_instance(
        context, chat_id, signal_name, "predict", draft, schedule
    )

    await query.answer(f"⏱️ Scheduled every {_format_schedule(schedule)}")
    await _show_detail(update, context, signal_name)


async def _start_daily(
    update: Update, context: ContextTypes.DEFAULT_TYPE, signal_name: str, time_str: str
) -> None:
    """Start daily-scheduled prediction."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    draft = _get_draft(context, signal_name, "predict")
    schedule = {"type": "daily", "daily_time": time_str}

    instance_id = _create_scheduled_instance(
        context, chat_id, signal_name, "predict", draft, schedule
    )

    await query.answer(f"📅 Scheduled daily at {time_str}")
    await _show_detail(update, context, signal_name)


async def _process_config(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    """Process key=value config input."""
    editing = context.user_data.get("signals_editing", {})
    signal_name = editing.get("signal")
    pipeline = editing.get("pipeline")

    if not signal_name or not pipeline:
        return

    signal = get_signal(signal_name)
    if not signal:
        return

    pipe = signal.train_pipeline if pipeline == "train" else signal.predict_pipeline
    if not pipe:
        return

    draft = _get_draft(context, signal_name, pipeline)
    fields = pipe.get_fields()

    # Parse input lines
    for line in text.strip().split("\n"):
        line = line.strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if key not in fields:
            continue

        # Type conversion
        field_type = fields[key]["type"]
        try:
            if field_type == "int":
                value = int(value)
            elif field_type == "float":
                value = float(value)
            elif field_type == "bool":
                value = value.lower() in ("true", "1", "yes")
            draft[key] = value
        except ValueError:
            pass

    _set_draft(context, signal_name, pipeline, draft)

    # Delete user message and refresh view
    try:
        await update.message.delete()
    except Exception:
        pass

    await _show_pipeline(update, context, signal_name, pipeline)


async def _process_daily_time(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    """Process custom daily time input."""
    editing = context.user_data.get("signals_editing", {})
    signal_name = editing.get("signal")

    if not signal_name:
        return

    # Validate time format
    try:
        hour, minute = map(int, text.strip().split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        time_str = f"{hour:02d}:{minute:02d}"
    except ValueError:
        await update.message.reply_text("Invalid time format. Use HH:MM (e.g., 09:30)")
        return

    context.user_data.pop("signals_state", None)
    context.user_data.pop("signals_editing", None)

    # Delete user message
    try:
        await update.message.delete()
    except Exception:
        pass

    await _start_daily(update, context, signal_name, time_str)


# =============================================================================
# Command and Callback Handlers
# =============================================================================


@restricted
async def signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /signals command."""
    clear_all_input_states(context)
    await _show_menu(update, context)


@restricted
async def signals_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        context.user_data.pop("signals_state", None)
        context.user_data.pop("signals_editing", None)
        await _show_menu(update, context)

    elif action == "reload":
        await query.answer("Reloading...")
        discover_signals(force_reload=True)
        await _show_menu(update, context)

    elif action == "tasks":
        await query.answer()
        await _show_tasks(update, context)

    elif action == "select" and len(parts) >= 3:
        await query.answer()
        context.user_data.pop("signals_state", None)
        context.user_data.pop("signals_editing", None)
        await _show_detail(update, context, parts[2])

    elif action == "train" and len(parts) >= 3:
        await query.answer()
        await _show_pipeline(update, context, parts[2], "train")

    elif action == "predict" and len(parts) >= 3:
        await query.answer()
        await _show_pipeline(update, context, parts[2], "predict")

    elif action == "run_train" and len(parts) >= 3:
        await _run_train(update, context, parts[2])

    elif action == "run_predict" and len(parts) >= 3:
        await _run_predict(update, context, parts[2], background=False)

    elif action == "bg_predict" and len(parts) >= 3:
        await _run_predict(update, context, parts[2], background=True)

    elif action == "sched" and len(parts) >= 3:
        await query.answer()
        await _show_schedule_menu(update, context, parts[2])

    elif action == "interval" and len(parts) >= 4:
        await _start_interval(update, context, parts[2], int(parts[3]))

    elif action == "daily" and len(parts) >= 3:
        await query.answer()
        await _show_daily_menu(update, context, parts[2])

    elif action == "dailyat" and len(parts) >= 4:
        await _start_daily(update, context, parts[2], parts[3])

    elif action == "history" and len(parts) >= 3:
        await query.answer()
        await _show_history(update, context, parts[2])

    elif action == "help" and len(parts) >= 4:
        await query.answer()
        await _show_help(update, context, parts[2], parts[3])

    elif action == "stop" and len(parts) >= 3:
        instance_id = parts[2]
        if _stop_instance(context, chat_id, instance_id):
            await query.answer("⏹ Stopped")
        else:
            await query.answer("Not found")
        await _show_tasks(update, context)

    elif action == "stopall" and len(parts) >= 3:
        signal_name = parts[2]
        instances = _get_signal_instances(context, signal_name)
        count = 0
        for iid, _ in instances:
            if _stop_instance(context, chat_id, iid):
                count += 1
        await query.answer(f"⏹ Stopped {count}")
        await _show_detail(update, context, signal_name)

    elif action == "stopall":
        instances = _get_instances(context)
        count = 0
        for iid in list(instances.keys()):
            if _stop_instance(context, chat_id, iid):
                count += 1
        await query.answer(f"⏹ Stopped {count}")
        await _show_tasks(update, context)

    else:
        await query.answer()


async def signals_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Handle text input for config editing or daily time."""
    state = context.user_data.get("signals_state")

    if state == "editing":
        await _process_config(update, context, update.message.text.strip())
        return True
    elif state == "daily_time":
        await _process_daily_time(update, context, update.message.text.strip())
        return True

    return False


async def restore_signal_jobs(application) -> int:
    """
    Restore scheduled jobs from persisted instances after bot restart.
    Call this during application startup (post_init).
    Returns count of restored jobs.
    """
    restored = 0

    for chat_id, user_data in application.user_data.items():
        instances = user_data.get("signals_instances", {})
        if not instances:
            continue

        to_remove = []

        for instance_id, inst in instances.items():
            if inst.get("status") != "running":
                continue

            signal_name = inst.get("signal_name")
            pipeline = inst.get("pipeline", "predict")
            config_dict = inst.get("config", {})
            schedule = inst.get("schedule", {})
            stype = schedule.get("type", "once")

            # Check if signal still exists
            signal = get_signal(signal_name)
            if not signal:
                logger.warning(
                    f"Signal {signal_name} no longer exists, removing instance {instance_id}"
                )
                to_remove.append(instance_id)
                continue

            # Only restore scheduled jobs (not one-time)
            if stype == "once":
                to_remove.append(instance_id)
                continue

            # Create mock context for job creation
            class MockContext:
                def __init__(self):
                    self.job_queue = application.job_queue
                    self.user_data = user_data

            mock_ctx = MockContext()

            job_data = {
                "chat_id": chat_id,
                "instance_id": instance_id,
                "signal_name": signal_name,
                "pipeline": pipeline,
                "config": config_dict,
            }

            job_name = _job_name(chat_id, instance_id)

            if stype == "interval":
                interval = schedule.get("interval_sec", 60)
                application.job_queue.run_repeating(
                    _interval_job_callback,
                    interval=interval,
                    first=interval,
                    data=job_data,
                    name=job_name,
                    chat_id=chat_id,
                )
                restored += 1
                logger.info(
                    f"Restored interval job for {signal_name}:{pipeline} [{instance_id}]"
                )

            elif stype == "daily":
                time_str = schedule.get("daily_time", "09:00")
                hour, minute = map(int, time_str.split(":"))
                application.job_queue.run_daily(
                    _interval_job_callback,
                    time=dt_time(hour=hour, minute=minute),
                    data=job_data,
                    name=job_name,
                    chat_id=chat_id,
                )
                restored += 1
                logger.info(
                    f"Restored daily job for {signal_name}:{pipeline} [{instance_id}]"
                )

        # Clean up old instances
        for iid in to_remove:
            del instances[iid]

    logger.info(f"Restored {restored} signal jobs")
    return restored
