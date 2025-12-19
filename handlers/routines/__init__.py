"""
Routines Handler - Run configurable Python scripts via Telegram.

Features:
- Auto-discovery of routines from routines/ folder
- Text-based config editing (key=value)
- Interval routines: run repeatedly at configurable interval
- One-shot routines: run once (foreground or background)
- Multi-instance support for different configs
"""

import asyncio
import hashlib
import logging
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackContext

from handlers import clear_all_input_states
from routines.base import discover_routines, get_routine
from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

# Job metadata: {job_name: {start_time, config, routine_name}}
_job_info: dict[str, dict] = {}

# Last results: {chat_id: {key: {result, duration, end_time}}}
_last_results: dict[int, dict[str, dict]] = {}


# =============================================================================
# Utility Functions
# =============================================================================


def _generate_instance_id(routine_name: str, config_dict: dict) -> str:
    """Generate unique instance ID from routine name and config."""
    data = f"{routine_name}:{sorted(config_dict.items())}"
    return hashlib.md5(data.encode()).hexdigest()[:8]


def _job_name(chat_id: int, routine_name: str, instance_id: str) -> str:
    """Build job name for JobQueue."""
    return f"routine_{chat_id}_{routine_name}_{instance_id}"


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


# =============================================================================
# Instance Management
# =============================================================================


def _get_instances(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    routine_name: str | None = None,
) -> list[dict]:
    """Get running instances for a chat, optionally filtered by routine."""
    prefix = f"routine_{chat_id}_"
    instances = []

    for job in context.job_queue.jobs():
        if not job.name or not job.name.startswith(prefix):
            continue

        parts = job.name.split("_")
        if len(parts) < 4:
            continue

        rname = "_".join(parts[2:-1])
        inst_id = parts[-1]

        if routine_name and rname != routine_name:
            continue

        info = _job_info.get(job.name, {})
        instances.append({
            "job_name": job.name,
            "routine_name": rname,
            "instance_id": inst_id,
            "config": info.get("config", {}),
            "start_time": info.get("start_time", time.time()),
        })

    return instances


def _stop_instance(context: ContextTypes.DEFAULT_TYPE, chat_id: int, job_name: str) -> bool:
    """Stop a running instance. Returns True if stopped."""
    jobs = context.job_queue.get_jobs_by_name(job_name)
    if not jobs:
        return False

    info = _job_info.pop(job_name, {})
    start_time = info.get("start_time", time.time())
    routine_name = info.get("routine_name", "unknown")
    instance_id = job_name.split("_")[-1]

    jobs[0].schedule_removal()

    # Store result
    _store_result(chat_id, routine_name, "(stopped)", time.time() - start_time, instance_id)

    # Clean up state
    try:
        user_data = context.application.user_data.get(chat_id, {})
        user_data.pop(f"{routine_name}_state_{chat_id}_{instance_id}", None)
    except Exception:
        pass

    return True


def _stop_all(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    routine_name: str | None = None,
) -> int:
    """Stop all instances, optionally filtered by routine. Returns count."""
    instances = _get_instances(context, chat_id, routine_name)
    return sum(1 for i in instances if _stop_instance(context, chat_id, i["job_name"]))


# =============================================================================
# Result Storage
# =============================================================================


def _store_result(
    chat_id: int,
    routine_name: str,
    result: str,
    duration: float,
    instance_id: str | None = None,
) -> None:
    """Store execution result."""
    if chat_id not in _last_results:
        _last_results[chat_id] = {}

    # Always store under routine_name for easy retrieval
    _last_results[chat_id][routine_name] = {
        "result": result,
        "duration": duration,
        "end_time": time.time(),
        "instance_id": instance_id,
    }


def _get_result(chat_id: int, routine_name: str) -> dict | None:
    """Get last result for a routine."""
    return _last_results.get(chat_id, {}).get(routine_name)


# =============================================================================
# Job Callbacks
# =============================================================================


async def _interval_callback(context: CallbackContext) -> None:
    """Execute one iteration of an interval routine."""
    data = context.job.data or {}
    routine_name = data["routine_name"]
    chat_id = data["chat_id"]
    config_dict = data["config_dict"]
    instance_id = data["instance_id"]

    routine = get_routine(routine_name)
    if not routine:
        return

    # Prepare context for routine
    user_data = context.application.user_data.get(chat_id, {})
    context._chat_id = chat_id
    context._instance_id = instance_id
    context._user_data = user_data

    job_name = context.job.name
    info = _job_info.get(job_name, {})
    start_time = info.get("start_time", time.time())

    try:
        config = routine.config_class(**config_dict)
        result = await routine.run_fn(config, context)
        result_text = str(result)[:500] if result else "Running..."
        logger.debug(f"{routine_name}[{instance_id}]: {result_text[:50]}")
    except Exception as e:
        result_text = f"Error: {e}"
        logger.error(f"{routine_name}[{instance_id}] error: {e}")

    # Store result for display in detail view
    _store_result(chat_id, routine_name, result_text, time.time() - start_time)


async def _oneshot_callback(context: CallbackContext) -> None:
    """Execute a one-shot routine and update UI or send message."""
    data = context.job.data or {}
    routine_name = data["routine_name"]
    chat_id = data["chat_id"]
    config_dict = data["config_dict"]
    instance_id = data["instance_id"]
    msg_id = data.get("msg_id")
    background = data.get("background", False)

    job_name = context.job.name
    info = _job_info.get(job_name, {})
    start_time = info.get("start_time", time.time())

    routine = get_routine(routine_name)
    if not routine:
        return

    # Prepare context
    user_data = context.application.user_data.get(chat_id, {})
    context._chat_id = chat_id
    context._instance_id = instance_id
    context._user_data = user_data

    try:
        config = routine.config_class(**config_dict)
        result = await routine.run_fn(config, context)
        result_text = str(result)[:500] if result else "Completed"
        status = "completed"
    except Exception as e:
        result_text = f"Error: {e}"
        status = "error"
        logger.error(f"{routine_name}[{instance_id}] failed: {e}")

    duration = time.time() - start_time
    _job_info.pop(job_name, None)
    _store_result(chat_id, routine_name, result_text, duration, instance_id)

    if background:
        # Send result as new message
        icon = "✅" if status == "completed" else "❌"
        text = (
            f"{icon} *{escape_markdown_v2(_display_name(routine_name))}*\n"
            f"Duration: {escape_markdown_v2(_format_duration(duration))}\n\n"
            f"```\n{result_text[:400]}\n```"
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            logger.error(f"Failed to send background result: {e}")
    else:
        # Update existing message
        await _update_after_run(context, routine_name, chat_id, msg_id, config_dict, result_text, status)


async def _update_after_run(
    context: CallbackContext,
    routine_name: str,
    chat_id: int,
    msg_id: int | None,
    config_dict: dict,
    result_text: str,
    status: str,
) -> None:
    """Update the routine detail message after execution."""
    if not msg_id:
        return

    routine = get_routine(routine_name)
    if not routine:
        return

    fields = routine.get_fields()
    config_lines = [f"{k}={config_dict.get(k, v['default'])}" for k, v in fields.items()]

    icon = "✅" if status == "completed" else "❌"
    result_info = _get_result(chat_id, routine_name)
    duration_str = _format_duration(result_info["duration"]) if result_info else ""

    text = (
        f"⚡ *{escape_markdown_v2(_display_name(routine_name).upper())}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{escape_markdown_v2(routine.description)}_\n\n"
        f"Status: ⚪ Ready\n\n"
        f"┌─ Config ─────────────────\n"
        f"```\n{chr(10).join(config_lines)}\n```\n"
        f"└─ _✏️ send key\\=value to edit_\n\n"
        f"┌─ {icon} Result ─ {escape_markdown_v2(duration_str)} ────\n"
        f"```\n{result_text[:300]}\n```\n"
        f"└─────────────────────────"
    )

    keyboard = [
        [
            InlineKeyboardButton("▶️ Run", callback_data=f"routines:run:{routine_name}"),
            InlineKeyboardButton("🔄 Background", callback_data=f"routines:bg:{routine_name}"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data=f"routines:help:{routine_name}"),
            InlineKeyboardButton("« Back", callback_data="routines:menu"),
        ],
    ]

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
            logger.debug(f"Could not update message: {e}")


# =============================================================================
# UI Display Functions
# =============================================================================


async def _show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show main routines menu."""
    chat_id = update.effective_chat.id
    routines = discover_routines(force_reload=True)
    all_instances = _get_instances(context, chat_id)

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

        if all_instances:
            keyboard.append([
                InlineKeyboardButton(f"📋 Running ({len(all_instances)})", callback_data="routines:tasks")
            ])

        for name in sorted(routines.keys()):
            routine = routines[name]
            count = len(_get_instances(context, chat_id, name))

            if count > 0:
                label = f"🟢 {_display_name(name)} ({count})"
            else:
                icon = "🔄" if routine.is_interval else "⚡"
                label = f"{icon} {_display_name(name)}"

            keyboard.append([InlineKeyboardButton(label, callback_data=f"routines:select:{name}")])

        keyboard.append([InlineKeyboardButton("🔄 Reload", callback_data="routines:reload")])

        running = len(all_instances)
        status = f"🟢 {running} running" if running else "All idle"

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
    instances = _get_instances(context, chat_id)

    if not instances:
        text = (
            "⚡ *RUNNING TASKS*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No tasks running\\."
        )
        keyboard = [[InlineKeyboardButton("« Back", callback_data="routines:menu")]]
    else:
        lines = ["⚡ *RUNNING TASKS*", "━━━━━━━━━━━━━━━━━━━━━\n"]
        keyboard = []

        for inst in instances:
            name = inst["routine_name"]
            inst_id = inst["instance_id"]
            duration = _format_duration(time.time() - inst["start_time"])
            config = inst["config"]

            lines.append(f"🟢 *{escape_markdown_v2(_display_name(name))}* `{inst_id}`")
            lines.append(f"   {escape_markdown_v2(duration)}")

            if config:
                preview = ", ".join(f"{k}\\={v}" for k, v in list(config.items())[:2])
                lines.append(f"   `{preview}`")
            lines.append("")

            keyboard.append([
                InlineKeyboardButton(f"⏹ {_display_name(name)[:10]}[{inst_id}]",
                                     callback_data=f"routines:stop:{inst['job_name']}")
            ])

        keyboard.append([InlineKeyboardButton("⏹ Stop All", callback_data="routines:stopall")])
        keyboard.append([InlineKeyboardButton("« Back", callback_data="routines:menu")])
        text = "\n".join(lines)

    await _edit_or_send(update, text, InlineKeyboardMarkup(keyboard))


async def _show_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Show routine configuration and controls."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    # Get or initialize config
    config_key = f"routine_config_{routine_name}"
    if config_key not in context.user_data:
        context.user_data[config_key] = routine.get_default_config().model_dump()

    config = context.user_data[config_key]
    fields = routine.get_fields()
    instances = _get_instances(context, chat_id, routine_name)

    # Build config display
    config_lines = [f"{k}={config.get(k, v['default'])}" for k, v in fields.items()]

    # Status
    if instances:
        status = f"🟢 {len(instances)} running"
    else:
        status = "⚪ Ready"

    # Instances section
    inst_section = ""
    if instances:
        inst_lines = ["\n┌─ Running ─────────────────"]
        for inst in instances[:5]:
            dur = _format_duration(time.time() - inst["start_time"])
            cfg = ", ".join(f"{k}={v}" for k, v in list(inst["config"].items())[:2])
            inst_lines.append(f"│ `{inst['instance_id']}` {escape_markdown_v2(cfg)} \\({escape_markdown_v2(dur)}\\)")
        if len(instances) > 5:
            inst_lines.append(f"│ _\\+{len(instances) - 5} more_")
        inst_lines.append("└───────────────────────────")
        inst_section = "\n".join(inst_lines)

    # Result section
    result_section = ""
    last = _get_result(chat_id, routine_name)
    if last:
        icon = "❌" if last["result"].startswith("Error") else "✅"
        dur = _format_duration(last["duration"])
        result_section = (
            f"\n\n┌─ {icon} Last ─ {escape_markdown_v2(dur)} ────\n"
            f"```\n{last['result'][:250]}\n```\n"
            f"└───────────────────────────"
        )

    # Type indicator
    type_str = "🔄 Interval" if routine.is_interval else "⚡ One\\-shot"

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
        f"{result_section}"
    )

    # Build keyboard
    if routine.is_interval:
        keyboard = [
            [InlineKeyboardButton("▶️ Start", callback_data=f"routines:start:{routine_name}")],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Run", callback_data=f"routines:run:{routine_name}"),
                InlineKeyboardButton("🔄 Background", callback_data=f"routines:bg:{routine_name}"),
            ],
        ]

    if instances:
        keyboard.append([InlineKeyboardButton(f"⏹ Stop All ({len(instances)})",
                                              callback_data=f"routines:stopall:{routine_name}")])

    keyboard.append([
        InlineKeyboardButton("❓ Help", callback_data=f"routines:help:{routine_name}"),
        InlineKeyboardButton("« Back", callback_data="routines:menu"),
    ])

    # Store state for config editing
    context.user_data["routines_state"] = "editing"
    context.user_data["routines_editing"] = {
        "routine": routine_name,
        "fields": fields,
        "config_key": config_key,
    }

    msg = update.callback_query.message if update.callback_query else None
    if msg:
        context.user_data["routines_msg_id"] = msg.message_id
        context.user_data["routines_chat_id"] = msg.chat_id

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


# =============================================================================
# Actions
# =============================================================================


async def _run_oneshot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    routine_name: str,
    background: bool = False,
) -> None:
    """Run a one-shot routine."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    config_key = f"routine_config_{routine_name}"
    config_dict = context.user_data.get(config_key, {})

    try:
        routine.config_class(**config_dict)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    instance_id = _generate_instance_id(routine_name, config_dict)
    job = _job_name(chat_id, routine_name, instance_id)
    msg_id = context.user_data.get("routines_msg_id")

    _job_info[job] = {
        "start_time": time.time(),
        "config": config_dict,
        "routine_name": routine_name,
    }

    context.job_queue.run_once(
        _oneshot_callback,
        when=0.1,
        data={
            "routine_name": routine_name,
            "chat_id": chat_id,
            "config_dict": config_dict,
            "instance_id": instance_id,
            "msg_id": msg_id,
            "background": background,
        },
        name=job,
        chat_id=chat_id,
    )

    if background:
        await update.callback_query.answer("🔄 Running in background...")
    else:
        await update.callback_query.answer("▶️ Running...")

    await _show_detail(update, context, routine_name)


async def _start_interval(update: Update, context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Start an interval routine."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    config_key = f"routine_config_{routine_name}"
    config_dict = context.user_data.get(config_key, {})

    try:
        config = routine.config_class(**config_dict)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    instance_id = _generate_instance_id(routine_name, config_dict)
    job = _job_name(chat_id, routine_name, instance_id)

    # Check duplicate
    if context.job_queue.get_jobs_by_name(job):
        await update.callback_query.answer("⚠️ Already running with this config")
        await _show_detail(update, context, routine_name)
        return

    interval = getattr(config, "interval_sec", 5)
    msg_id = context.user_data.get("routines_msg_id")

    _job_info[job] = {
        "start_time": time.time(),
        "config": config_dict,
        "routine_name": routine_name,
    }

    context.job_queue.run_repeating(
        _interval_callback,
        interval=interval,
        first=0.1,
        data={
            "routine_name": routine_name,
            "chat_id": chat_id,
            "config_dict": config_dict,
            "instance_id": instance_id,
            "msg_id": msg_id,
        },
        name=job,
        chat_id=chat_id,
    )

    await update.callback_query.answer(f"🔄 Started (every {interval}s)")
    await _show_detail(update, context, routine_name)


# =============================================================================
# Config Input Processing
# =============================================================================


async def _process_config(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Process key=value config input."""
    editing = context.user_data.get("routines_editing", {})
    routine_name = editing.get("routine")
    fields = editing.get("fields", {})
    config_key = editing.get("config_key")

    if not routine_name or not config_key:
        return

    # Delete user message
    try:
        await update.message.delete()
    except Exception:
        pass

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

    if config_key not in context.user_data:
        routine = get_routine(routine_name)
        context.user_data[config_key] = routine.get_default_config().model_dump()

    context.user_data[config_key].update(updates)

    msg = await update.message.reply_text(
        f"✅ {', '.join(f'`{k}={v}`' for k, v in updates.items())}",
        parse_mode="Markdown",
    )
    asyncio.create_task(_delete_after(msg, 2))

    await _refresh_detail(context, routine_name)


async def _refresh_detail(context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Refresh routine detail after config update."""
    msg_id = context.user_data.get("routines_msg_id")
    chat_id = context.user_data.get("routines_chat_id")

    if not msg_id or not chat_id:
        return

    routine = get_routine(routine_name)
    if not routine:
        return

    config_key = f"routine_config_{routine_name}"
    config = context.user_data.get(config_key, {})
    fields = routine.get_fields()
    instances = _get_instances(context, chat_id, routine_name)

    config_lines = [f"{k}={config.get(k, v['default'])}" for k, v in fields.items()]

    status = f"🟢 {len(instances)} running" if instances else "⚪ Ready"
    type_str = "🔄 Interval" if routine.is_interval else "⚡ One\\-shot"

    # Result section
    result_section = ""
    last = _get_result(chat_id, routine_name)
    if last:
        icon = "❌" if last["result"].startswith("Error") else "✅"
        dur = _format_duration(last["duration"])
        result_section = (
            f"\n\n┌─ {icon} Last ─ {escape_markdown_v2(dur)} ────\n"
            f"```\n{last['result'][:250]}\n```\n"
            f"└───────────────────────────"
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

    if routine.is_interval:
        keyboard = [
            [InlineKeyboardButton("▶️ Start", callback_data=f"routines:start:{routine_name}")],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Run", callback_data=f"routines:run:{routine_name}"),
                InlineKeyboardButton("🔄 Background", callback_data=f"routines:bg:{routine_name}"),
            ],
        ]

    if instances:
        keyboard.append([InlineKeyboardButton(f"⏹ Stop All ({len(instances)})",
                                              callback_data=f"routines:stopall:{routine_name}")])

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
# Helpers
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
        await _run_oneshot(update, context, parts[2], background=False)

    elif action == "bg" and len(parts) >= 3:
        await _run_oneshot(update, context, parts[2], background=True)

    elif action == "start" and len(parts) >= 3:
        await _start_interval(update, context, parts[2])

    elif action == "stop" and len(parts) >= 3:
        job_name = ":".join(parts[2:])
        if _stop_instance(context, chat_id, job_name):
            await query.answer("⏹ Stopped")
        else:
            await query.answer("Not found")
        await _show_tasks(update, context)

    elif action == "stopall" and len(parts) >= 3:
        count = _stop_all(context, chat_id, parts[2])
        await query.answer(f"⏹ Stopped {count}")
        await _show_detail(update, context, parts[2])

    elif action == "stopall":
        count = _stop_all(context, chat_id)
        await query.answer(f"⏹ Stopped {count}")
        await _show_tasks(update, context)

    elif action == "help" and len(parts) >= 3:
        await query.answer()
        await _show_help(update, context, parts[2])

    else:
        await query.answer()


async def routines_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input for config editing."""
    if context.user_data.get("routines_state") == "editing":
        await _process_config(update, context, update.message.text.strip())
        return True
    return False


__all__ = ["routines_command", "routines_callback_handler", "routines_message_handler"]
