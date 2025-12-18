"""
Routines handler - Terminal-like interface for running Python scripts.

Features:
- Auto-discovery of routines in routines/ folder
- Text-based config editing (key=value)
- Background execution support
- Task management (run/stop)
"""

import asyncio
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2
from handlers import clear_all_input_states
from routines.base import discover_routines, get_routine

logger = logging.getLogger(__name__)

# Store running tasks per chat: {chat_id: {routine_name: {"task": Task, "start_time": float, "config": dict}}}
_running_tasks: dict[int, dict[str, dict]] = {}

# Store last result per chat: {chat_id: {routine_name: {"result": str, "end_time": float, "duration": float}}}
_last_results: dict[int, dict[str, dict]] = {}


def _format_routine_name(name: str) -> str:
    """Convert snake_case to Title Case."""
    return name.replace("_", " ").title()


def _format_duration(seconds: float) -> str:
    """Format duration in human-readable form."""
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


def _get_running_duration(chat_id: int, routine_name: str) -> str:
    """Get how long a routine has been running."""
    chat_tasks = _running_tasks.get(chat_id, {})
    task_info = chat_tasks.get(routine_name, {})
    start_time = task_info.get("start_time")
    if start_time:
        return _format_duration(time.time() - start_time)
    return ""


async def _delete_after(message, seconds: float) -> None:
    """Delete a message after a delay."""
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except Exception:
        pass


def _get_task_status(chat_id: int, routine_name: str) -> str:
    """Get status of a routine task for a specific chat."""
    chat_tasks = _running_tasks.get(chat_id, {})
    task_info = chat_tasks.get(routine_name)
    if task_info is None:
        return "idle"
    task = task_info.get("task")
    if task is None or task.done():
        # Clean up completed task
        if routine_name in chat_tasks:
            del chat_tasks[routine_name]
        return "idle"
    return "running"


def _get_running_tasks_for_chat(chat_id: int) -> dict[str, dict]:
    """Get all running tasks for a chat."""
    chat_tasks = _running_tasks.get(chat_id, {})
    # Clean up completed tasks
    running = {}
    for name, info in list(chat_tasks.items()):
        task = info.get("task")
        if task and not task.done():
            running[name] = info
        elif name in chat_tasks:
            del chat_tasks[name]
    return running


def _stop_task(chat_id: int, routine_name: str) -> bool:
    """Stop a running task. Returns True if stopped."""
    chat_tasks = _running_tasks.get(chat_id, {})
    task_info = chat_tasks.get(routine_name)
    if task_info:
        task = task_info.get("task")
        if task and not task.done():
            task.cancel()
        if routine_name in chat_tasks:
            del chat_tasks[routine_name]
        return True
    return False


def _get_last_result(chat_id: int, routine_name: str) -> dict | None:
    """Get last result for a routine in a chat."""
    chat_results = _last_results.get(chat_id, {})
    return chat_results.get(routine_name)


def _set_last_result(chat_id: int, routine_name: str, result: str, duration: float) -> None:
    """Store last result for a routine in a chat."""
    if chat_id not in _last_results:
        _last_results[chat_id] = {}
    _last_results[chat_id][routine_name] = {
        "result": result,
        "end_time": time.time(),
        "duration": duration,
    }


@restricted
async def routines_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /routines command - Show available routines."""
    clear_all_input_states(context)
    await _show_routines_menu(update, context)


async def _show_routines_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display list of available routines."""
    chat_id = update.effective_chat.id
    routines = discover_routines(force_reload=True)
    running_tasks = _get_running_tasks_for_chat(chat_id)

    if not routines:
        text = (
            "⚡ *ROUTINES*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No routines found\\.\n\n"
            "Add Python files to `routines/` folder:\n"
            "```\n"
            "class Config(BaseModel):\n"
            "    param: str = \"value\"\n\n"
            "async def run(config, ctx):\n"
            "    return \"result\"\n"
            "```"
        )
        keyboard = [[InlineKeyboardButton("🔄 Reload", callback_data="routines:reload")]]
    else:
        keyboard = []

        # Show running tasks first if any
        if running_tasks:
            keyboard.append([InlineKeyboardButton("📋 Running Tasks", callback_data="routines:running")])

        # Build menu with status indicators
        for name in sorted(routines.keys()):
            status = _get_task_status(chat_id, name)

            if status == "running":
                duration = _get_running_duration(chat_id, name)
                label = f"🟢 {_format_routine_name(name)} ({duration})"
            else:
                label = f"   {_format_routine_name(name)}"

            keyboard.append([InlineKeyboardButton(label, callback_data=f"routines:select:{name}")])

        keyboard.append([InlineKeyboardButton("🔄 Reload", callback_data="routines:reload")])

        # Count running
        running_count = len(running_tasks)
        status_line = f"🟢 {running_count} running" if running_count else "All idle"

        text = (
            "⚡ *ROUTINES*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Status: {escape_markdown_v2(status_line)}\n\n"
            "Select a routine to configure and run:"
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = update.message or update.callback_query.message

    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(text, parse_mode="MarkdownV2", reply_markup=reply_markup)
        except Exception:
            await msg.reply_text(text, parse_mode="MarkdownV2", reply_markup=reply_markup)
    else:
        await msg.reply_text(text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def _show_running_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all running tasks for this chat."""
    chat_id = update.effective_chat.id
    running_tasks = _get_running_tasks_for_chat(chat_id)

    if not running_tasks:
        text = (
            "⚡ *RUNNING TASKS*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No tasks currently running\\."
        )
        keyboard = [[InlineKeyboardButton("« Back", callback_data="routines:menu")]]
    else:
        lines = [
            "⚡ *RUNNING TASKS*",
            "━━━━━━━━━━━━━━━━━━━━━\n",
        ]

        keyboard = []
        for name, info in running_tasks.items():
            duration = _format_duration(time.time() - info.get("start_time", time.time()))
            config = info.get("config", {})

            # Show task info
            display_name = escape_markdown_v2(_format_routine_name(name))
            lines.append(f"🟢 *{display_name}*")
            lines.append(f"   Running for {escape_markdown_v2(duration)}")

            # Show key config values
            if config:
                config_preview = ", ".join(f"{k}\\={v}" for k, v in list(config.items())[:2])
                lines.append(f"   `{config_preview}`")
            lines.append("")

            # Add stop button for each task
            keyboard.append([
                InlineKeyboardButton(f"⏹ Stop {_format_routine_name(name)}", callback_data=f"routines:stop:{name}")
            ])

        keyboard.append([InlineKeyboardButton("⏹ Stop All", callback_data="routines:stopall")])
        keyboard.append([InlineKeyboardButton("« Back", callback_data="routines:menu")])

        text = "\n".join(lines)

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def _show_routine_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Show routine config in terminal-style (copyable key=value)."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)
    if not routine:
        await update.callback_query.edit_message_text("Routine not found.")
        return

    # Get or create config
    config_key = f"routine_config_{routine_name}"
    if config_key not in context.user_data:
        context.user_data[config_key] = routine.get_default_config().model_dump()

    current_config = context.user_data[config_key]
    fields = routine.get_fields()
    status = _get_task_status(chat_id, routine_name)

    # Build copyable config block
    config_lines = []
    for field_name, field_info in fields.items():
        value = current_config.get(field_name, field_info["default"])
        config_lines.append(f"{field_name}={value}")
    config_text = "\n".join(config_lines)

    # Header
    display_name = _format_routine_name(routine_name)
    escaped_name = escape_markdown_v2(display_name.upper())
    escaped_desc = escape_markdown_v2(routine.description)

    # Status line
    if status == "running":
        duration = _get_running_duration(chat_id, routine_name)
        status_line = f"🟢 Running \\({escape_markdown_v2(duration)}\\)"
    else:
        status_line = "⚪ Ready"

    # Result section (shown below config)
    result_section = ""
    last_result_info = _get_last_result(chat_id, routine_name)
    if last_result_info:
        last_result = last_result_info.get("result", "")
        result_duration = last_result_info.get("duration", 0)

        # Determine result icon
        if last_result.startswith("Error:"):
            result_icon = "❌"
        elif last_result == "(stopped by user)":
            result_icon = "⏹"
        else:
            result_icon = "✅"

        # Duration in header
        duration_text = ""
        if result_duration:
            duration_text = f" {escape_markdown_v2(_format_duration(result_duration))} "

        result_section = (
            f"\n\n┌─ {result_icon} Result ─{duration_text}─────────\n"
            f"```\n{last_result[:300]}\n```\n"
            f"└─────────────────────────"
        )

    text = (
        f"⚡ *{escaped_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{escaped_desc}_\n\n"
        f"Status: {status_line}\n\n"
        f"┌─ Config ─────────────────\n"
        f"```\n{config_text}\n```\n"
        f"└─ _✏️ send key\\=value to edit_"
        f"{result_section}"
    )

    # Store editing state
    context.user_data["routines_state"] = "editing"
    context.user_data["routines_editing"] = {
        "routine": routine_name,
        "fields": fields,
        "config_key": config_key,
    }

    # Build keyboard based on status
    if status == "running":
        keyboard = [
            [
                InlineKeyboardButton("⏹ Stop", callback_data=f"routines:stop:{routine_name}"),
                InlineKeyboardButton("❓ Help", callback_data=f"routines:help:{routine_name}"),
            ],
            [InlineKeyboardButton("« Back", callback_data="routines:menu")],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Run", callback_data=f"routines:run:{routine_name}"),
                InlineKeyboardButton("⏳ Background", callback_data=f"routines:bg:{routine_name}"),
            ],
            [
                InlineKeyboardButton("❓ Help", callback_data=f"routines:help:{routine_name}"),
                InlineKeyboardButton("« Back", callback_data="routines:menu"),
            ],
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Store message info for updates
    msg = update.callback_query.message if update.callback_query else None
    if msg:
        context.user_data["routines_msg_id"] = msg.message_id
        context.user_data["routines_chat_id"] = msg.chat_id

    try:
        await update.callback_query.message.edit_text(text, parse_mode="MarkdownV2", reply_markup=reply_markup)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning(f"Could not edit message: {e}")


async def _show_routine_help(update: Update, context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Show field descriptions for a routine."""
    routine = get_routine(routine_name)
    if not routine:
        return

    fields = routine.get_fields()
    display_name = _format_routine_name(routine_name)
    escaped_name = escape_markdown_v2(display_name.upper())

    lines = [
        f"❓ *{escaped_name}*",
        "━━━━━━━━━━━━━━━━━━━━━\n",
        "*Configuration Parameters:*\n"
    ]

    for field_name, field_info in fields.items():
        escaped_field = escape_markdown_v2(field_name)
        escaped_type = escape_markdown_v2(field_info["type"])
        escaped_desc = escape_markdown_v2(field_info["description"])
        escaped_default = escape_markdown_v2(str(field_info["default"]))

        lines.append(f"• `{escaped_field}` _{escaped_type}_")
        lines.append(f"  {escaped_desc}")
        lines.append(f"  Default: `{escaped_default}`\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Copy values and send `key=value` to edit_")

    text = "\n".join(lines)

    keyboard = [[InlineKeyboardButton("« Back", callback_data=f"routines:select:{routine_name}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.edit_text(text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def _run_routine(update: Update, context: ContextTypes.DEFAULT_TYPE, routine_name: str, background: bool = False) -> None:
    """Execute a routine (foreground or background)."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)
    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    # Get config
    config_key = f"routine_config_{routine_name}"
    config_dict = context.user_data.get(config_key, {})

    try:
        config = routine.config_class(**config_dict)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    display_name = _format_routine_name(routine_name)
    msg_id = context.user_data.get("routines_msg_id")
    start_time = time.time()

    async def run_task():
        result_text = None
        status = "completed"
        try:
            context._chat_id = chat_id

            if background:
                # No timeout for background tasks
                result = await routine.run_fn(config, context)
            else:
                # 60 second timeout for foreground tasks
                result = await asyncio.wait_for(
                    routine.run_fn(config, context),
                    timeout=60.0
                )

            result_text = str(result)[:500]

        except asyncio.TimeoutError:
            status = "failed"
            result_text = "Timeout after 60s. Use Background for long tasks."
        except asyncio.CancelledError:
            status = "stopped"
            result_text = "(stopped by user)"
        except Exception as e:
            logger.error(f"Routine {routine_name} failed: {e}")
            status = "failed"
            result_text = f"Error: {str(e)[:300]}"
        finally:
            # Calculate duration and store result
            duration = time.time() - start_time
            if result_text:
                _set_last_result(chat_id, routine_name, result_text, duration)

            # Clean up task reference
            chat_tasks = _running_tasks.get(chat_id, {})
            if routine_name in chat_tasks:
                del chat_tasks[routine_name]

            # Update the menu message to show completion
            try:
                await _update_routine_after_background(
                    context, routine_name, msg_id, chat_id, status, result_text
                )
            except Exception as e:
                logger.debug(f"Could not update menu after run: {e}")

    # Cancel existing task for this routine if any
    _stop_task(chat_id, routine_name)

    # Start task and track it
    task = asyncio.create_task(run_task())

    # Store task info
    if chat_id not in _running_tasks:
        _running_tasks[chat_id] = {}
    _running_tasks[chat_id][routine_name] = {
        "task": task,
        "start_time": start_time,
        "config": config_dict,
    }

    # Show feedback
    if background:
        await update.callback_query.answer(f"⏳ Started in background")
    else:
        await update.callback_query.answer("▶️ Running...")

    # Show running state with Stop button
    await _show_routine_detail(update, context, routine_name)


async def _process_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process key=value config updates (like networks.py pattern)."""
    editing = context.user_data.get("routines_editing", {})
    routine_name = editing.get("routine")
    fields = editing.get("fields", {})
    config_key = editing.get("config_key")

    if not routine_name or not config_key:
        return

    # Delete user's message for clean chat
    try:
        await update.message.delete()
    except Exception:
        pass

    # Parse key=value lines
    updates = {}
    errors = []

    for line in user_input.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Validate key exists
        if key not in fields:
            errors.append(f"Unknown: {key}")
            continue

        # Convert value based on type
        field_type = fields[key].get("type", "str")
        try:
            if field_type == "int":
                value = int(value)
            elif field_type == "float":
                value = float(value)
            elif field_type == "bool":
                value = value.lower() in ("true", "yes", "1", "on")
            # else keep as string
        except ValueError:
            errors.append(f"Invalid {key}")
            continue

        updates[key] = value

    # Show errors if any
    if errors:
        error_msg = "⚠️ " + ", ".join(errors)
        try:
            msg = await update.message.reply_text(error_msg)
            # Auto-delete error after 3 seconds
            asyncio.create_task(_delete_after(msg, 3))
        except Exception:
            pass

    if not updates:
        try:
            msg = await update.message.reply_text("❌ Use format: `key=value`", parse_mode="Markdown")
            asyncio.create_task(_delete_after(msg, 3))
        except Exception:
            pass
        return

    # Apply updates
    if config_key not in context.user_data:
        routine = get_routine(routine_name)
        context.user_data[config_key] = routine.get_default_config().model_dump()

    context.user_data[config_key].update(updates)

    # Brief confirmation (will be deleted)
    try:
        update_summary = ", ".join(f"`{k}={v}`" for k, v in updates.items())
        msg = await update.message.reply_text(f"✅ {update_summary}", parse_mode="Markdown")
        asyncio.create_task(_delete_after(msg, 2))
    except Exception:
        pass

    # Refresh the detail view
    await _refresh_routine_detail(context, routine_name)


async def _update_routine_after_background(
    context: ContextTypes.DEFAULT_TYPE,
    routine_name: str,
    msg_id: int,
    chat_id: int,
    status: str,
    result_text: str = None
) -> None:
    """Update the routine menu after background task completes."""
    routine = get_routine(routine_name)
    if not routine or not msg_id or not chat_id:
        return

    display_name = _format_routine_name(routine_name)
    escaped_name = escape_markdown_v2(display_name.upper())
    escaped_desc = escape_markdown_v2(routine.description)

    # Status icon
    if status == "completed":
        status_icon = "✅"
    elif status == "stopped":
        status_icon = "⏹"
    else:
        status_icon = "❌"

    # Duration for result header (from stored result)
    last_result_info = _get_last_result(chat_id, routine_name)
    duration_text = ""
    if last_result_info and last_result_info.get("duration"):
        duration_text = f" {escape_markdown_v2(_format_duration(last_result_info['duration']))} "

    # Get current config
    config_key = f"routine_config_{routine_name}"
    current_config = context.user_data.get(config_key, {})
    fields = routine.get_fields()

    config_lines = []
    for field_name, field_info in fields.items():
        value = current_config.get(field_name, field_info["default"])
        config_lines.append(f"{field_name}={value}")
    config_text = "\n".join(config_lines)

    # Build result section
    result_section = ""
    if result_text:
        result_section = (
            f"\n\n┌─ {status_icon} Result ─{duration_text}─────────\n"
            f"```\n{result_text[:300]}\n```\n"
            f"└─────────────────────────"
        )

    text = (
        f"⚡ *{escaped_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{escaped_desc}_\n\n"
        f"Status: ⚪ Ready\n\n"
        f"┌─ Config ─────────────────\n"
        f"```\n{config_text}\n```\n"
        f"└─ _✏️ send key\\=value to edit_"
        f"{result_section}"
    )

    keyboard = [
        [
            InlineKeyboardButton("▶️ Run", callback_data=f"routines:run:{routine_name}"),
            InlineKeyboardButton("⏳ Background", callback_data=f"routines:bg:{routine_name}"),
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
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.debug(f"Could not update after background: {e}")


async def _refresh_routine_detail(context: ContextTypes.DEFAULT_TYPE, routine_name: str) -> None:
    """Refresh the routine detail message after config update."""
    msg_id = context.user_data.get("routines_msg_id")
    chat_id = context.user_data.get("routines_chat_id")

    if not msg_id or not chat_id:
        return

    routine = get_routine(routine_name)
    if not routine:
        return

    config_key = f"routine_config_{routine_name}"
    current_config = context.user_data.get(config_key, {})
    fields = routine.get_fields()
    status = _get_task_status(chat_id, routine_name)

    # Build config block
    config_lines = []
    for field_name, field_info in fields.items():
        value = current_config.get(field_name, field_info["default"])
        config_lines.append(f"{field_name}={value}")
    config_text = "\n".join(config_lines)

    display_name = _format_routine_name(routine_name)
    escaped_name = escape_markdown_v2(display_name.upper())
    escaped_desc = escape_markdown_v2(routine.description)

    # Status line
    if status == "running":
        duration = _get_running_duration(chat_id, routine_name)
        status_line = f"🟢 Running \\({escape_markdown_v2(duration)}\\)"
    else:
        status_line = "⚪ Ready"

    # Result section (shown below config)
    result_section = ""
    last_result_info = _get_last_result(chat_id, routine_name)
    if last_result_info:
        last_result = last_result_info.get("result", "")
        result_duration = last_result_info.get("duration", 0)

        if last_result.startswith("Error:"):
            result_icon = "❌"
        elif last_result == "(stopped by user)":
            result_icon = "⏹"
        else:
            result_icon = "✅"

        duration_text = ""
        if result_duration:
            duration_text = f" {escape_markdown_v2(_format_duration(result_duration))} "

        result_section = (
            f"\n\n┌─ {result_icon} Result ─{duration_text}─────────\n"
            f"```\n{last_result[:300]}\n```\n"
            f"└─────────────────────────"
        )

    text = (
        f"⚡ *{escaped_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{escaped_desc}_\n\n"
        f"Status: {status_line}\n\n"
        f"┌─ Config ─────────────────\n"
        f"```\n{config_text}\n```\n"
        f"└─ _✏️ send key\\=value to edit_"
        f"{result_section}"
    )

    # Build keyboard
    if status == "running":
        keyboard = [
            [
                InlineKeyboardButton("⏹ Stop", callback_data=f"routines:stop:{routine_name}"),
                InlineKeyboardButton("❓ Help", callback_data=f"routines:help:{routine_name}"),
            ],
            [InlineKeyboardButton("« Back", callback_data="routines:menu")],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Run", callback_data=f"routines:run:{routine_name}"),
                InlineKeyboardButton("⏳ Background", callback_data=f"routines:bg:{routine_name}"),
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
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.debug(f"Could not refresh routine detail: {e}")


@restricted
async def routines_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries for routines."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    data = query.data
    parts = data.split(":")

    if len(parts) < 2:
        await query.answer()
        return

    action = parts[1]

    if action == "menu":
        await query.answer()
        context.user_data.pop("routines_state", None)
        context.user_data.pop("routines_editing", None)
        await _show_routines_menu(update, context)

    elif action == "reload":
        await query.answer("Reloading...")
        discover_routines(force_reload=True)
        await _show_routines_menu(update, context)

    elif action == "running":
        await query.answer()
        await _show_running_tasks(update, context)

    elif action == "select" and len(parts) >= 3:
        await query.answer()
        routine_name = parts[2]
        await _show_routine_detail(update, context, routine_name)

    elif action == "run" and len(parts) >= 3:
        routine_name = parts[2]
        await _run_routine(update, context, routine_name, background=False)

    elif action == "bg" and len(parts) >= 3:
        routine_name = parts[2]
        await _run_routine(update, context, routine_name, background=True)

    elif action == "stop" and len(parts) >= 3:
        routine_name = parts[2]
        if _stop_task(chat_id, routine_name):
            await query.answer(f"Stopped {_format_routine_name(routine_name)}")
        else:
            await query.answer("Task not running")
        await _show_routine_detail(update, context, routine_name)

    elif action == "stopall":
        running = _get_running_tasks_for_chat(chat_id)
        count = 0
        for name in list(running.keys()):
            if _stop_task(chat_id, name):
                count += 1
        await query.answer(f"Stopped {count} tasks")
        await _show_running_tasks(update, context)

    elif action == "help" and len(parts) >= 3:
        await query.answer()
        routine_name = parts[2]
        await _show_routine_help(update, context, routine_name)

    else:
        await query.answer()


async def routines_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input for routines config editing."""
    state = context.user_data.get("routines_state")

    if state == "editing":
        await _process_config_input(update, context, update.message.text.strip())
        return True

    return False


__all__ = [
    "routines_command",
    "routines_callback_handler",
    "routines_message_handler",
]
