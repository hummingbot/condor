"""Agent chat handler -- /agent command, callback router, message handler."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers import clear_all_input_states
from utils.auth import restricted

from ._shared import (
    AGENT_OPTIONS,
    COMPACT_CONTEXT_TEMPLATE,
    COMPACT_PROMPT_AUTO,
    COMPACT_PROMPT_CUSTOM_TEMPLATE,
    DEFAULT_AGENT,
    get_project_dir,
)
from .confirmation import resolve_confirmation
from .menu import show_agent_menu
from condor.acp import PromptDone
from .session import destroy_session, get_or_create_session, get_session
from .stream import TelegramStreamer

log = logging.getLogger(__name__)


@restricted
async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /agent command."""
    # Block agent mode in group chats
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text(
            "Agent mode is only available in private chats."
        )
        return

    clear_all_input_states(context)
    context.user_data["agent_state"] = "active"
    await show_agent_menu(update, context)


@restricted
async def agent_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Route agent:* callbacks."""
    query = update.callback_query
    await query.answer()

    # Block agent mode in group chats
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await query.message.edit_text("Agent mode is only available in private chats.")
        return

    data = query.data
    action = data.split(":", 1)[1] if ":" in data else data

    if action.startswith("select:"):
        agent_key = action.split(":", 1)[1]
        await _handle_select(update, context, agent_key)
    elif action == "stop":
        await _handle_stop(update, context)
    elif action == "close":
        await _handle_close(update, context)
    elif action == "menu":
        await show_agent_menu(update, context)
    elif action == "compact":
        await _handle_compact_menu(update, context)
    elif action == "compact_auto":
        await _handle_compact(update, context, custom_instructions=None)
    elif action == "compact_custom":
        await _handle_compact_custom_prompt(update, context)
    elif action == "new":
        await _handle_new_session(update, context)
    elif action.startswith("confirm_trade:"):
        request_id = action.split(":", 1)[1]
        resolved = resolve_confirmation(request_id, approved=True)
        text = "Approved." if resolved else "Request expired."
        await query.message.edit_text(text)
    elif action.startswith("reject_trade:"):
        request_id = action.split(":", 1)[1]
        resolved = resolve_confirmation(request_id, approved=False)
        text = "Rejected." if resolved else "Request expired."
        await query.message.edit_text(text)
    elif action.startswith("w:"):
        parts = action.split(":")
        request_id, btn_idx = parts[1], int(parts[2])
        from condor.widget_bridge import get_widget_bridge

        resolved = get_widget_bridge().resolve(request_id, btn_idx)
        if not resolved:
            await query.message.edit_text("Session expired.")


async def _handle_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, agent_key: str
) -> None:
    """Start a new agent session."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    if agent_key not in AGENT_OPTIONS:
        await query.message.edit_text("Unknown agent.")
        return

    label = AGENT_OPTIONS[agent_key]["label"]
    await query.message.edit_text(f"Starting {label} session...")

    context.user_data["agent_state"] = "active"
    context.user_data["agent_selected"] = agent_key

    try:
        bot = context.bot
        user_id = update.effective_user.id

        # Create permission callback bound to this bot/chat
        async def _perm_cb(tool_call, options):
            from .confirmation import permission_callback

            return await permission_callback(bot, chat_id, tool_call, options)

        await get_or_create_session(
            chat_id=chat_id,
            agent_key=agent_key,
            permission_callback=_perm_cb,
            user_id=user_id,
        )
        await query.message.edit_text(
            f"{label} is ready. Send a message to start chatting.\n\nUse /agent to see options or any other command to exit."
        )
    except Exception as e:
        log.exception("Failed to start agent session")
        await query.message.edit_text(f"Failed to start agent: {e}")
        context.user_data.pop("agent_state", None)
        context.user_data.pop("agent_selected", None)


async def _handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop the active agent session."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    destroyed = await destroy_session(chat_id)
    context.user_data.pop("agent_state", None)
    context.user_data.pop("agent_selected", None)

    if destroyed:
        await query.message.edit_text("Agent session stopped.")
    else:
        await query.message.edit_text("No active session.")


async def _handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close the agent menu (keep session alive if running).

    Only clears agent_state if there is no live session -- otherwise
    messages would stop routing to the agent handler while the subprocess
    is still running.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        # No live session -- safe to fully deactivate
        context.user_data.pop("agent_state", None)

    await query.message.delete()


async def _handle_compact_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show compact options sub-menu."""
    from .menu import _compact_menu_keyboard

    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session to compact.")
        return

    await query.message.edit_text(
        "How would you like to compact context?\n\n"
        "Auto - summarize everything\n"
        "Custom - specify what to keep",
        reply_markup=_compact_menu_keyboard(),
    )


async def _handle_compact(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    custom_instructions: str | None = None,
) -> None:
    """Compact: summarize context → destroy session → recreate with summary."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session to compact.")
        return

    if session.is_busy:
        await query.message.edit_text("Agent is busy. Wait for it to finish first.")
        return

    await query.message.edit_text("Compacting context...")

    # Build the summary prompt
    if custom_instructions:
        prompt = COMPACT_PROMPT_CUSTOM_TEMPLATE.format(instructions=custom_instructions)
    else:
        prompt = COMPACT_PROMPT_AUTO

    # Get summary from current session (non-streaming)
    try:
        summary = await session.client.prompt(prompt)
    except Exception as e:
        log.exception("Failed to get compact summary")
        await query.message.edit_text(f"Compact failed: {e}")
        return

    if not summary or not summary.strip():
        await query.message.edit_text("Agent returned empty summary. Compact aborted.")
        return

    # Destroy old session
    agent_key = session.agent_key
    await destroy_session(chat_id)

    # Recreate session
    try:
        user_id = update.effective_user.id
        bot = context.bot

        async def _perm_cb(tool_call, options):
            from .confirmation import permission_callback

            return await permission_callback(bot, chat_id, tool_call, options)

        new_session = await get_or_create_session(
            chat_id=chat_id,
            agent_key=agent_key,
            permission_callback=_perm_cb,
            user_id=user_id,
        )

        # Inject the summary as initial context
        compact_context = COMPACT_CONTEXT_TEMPLATE.format(summary=summary)
        await new_session.client.prompt(compact_context)

    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await query.message.edit_text(f"Compact failed during session reset: {e}")
        context.user_data.pop("agent_state", None)
        context.user_data.pop("agent_selected", None)
        return

    word_count = len(summary.split())
    await query.message.edit_text(
        f"Context compacted ({word_count} words carried over).\n\n"
        "Send a message to continue chatting."
    )


async def _handle_compact_custom_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Prompt user to type custom compact instructions."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session to compact.")
        return

    context.user_data["agent_compact_custom"] = True
    await query.message.edit_text(
        "What should I keep in the summary?\n\n"
        'Type your instructions (e.g. "keep the portfolio analysis and SOL trade setup"):'
    )


async def _handle_new_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Destroy current session and start a fresh one."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session.")
        return

    agent_key = session.agent_key
    label = AGENT_OPTIONS.get(agent_key, {}).get("label", agent_key)

    await destroy_session(chat_id)
    await query.message.edit_text(f"Starting fresh {label} session...")

    try:
        user_id = update.effective_user.id
        bot = context.bot

        async def _perm_cb(tool_call, options):
            from .confirmation import permission_callback

            return await permission_callback(bot, chat_id, tool_call, options)

        await get_or_create_session(
            chat_id=chat_id,
            agent_key=agent_key,
            permission_callback=_perm_cb,
            user_id=user_id,
        )
        await query.message.edit_text(
            f"Fresh {label} session ready.\n\nSend a message to start chatting."
        )
    except Exception as e:
        log.exception("Failed to create new session")
        await query.message.edit_text(f"Failed to start new session: {e}")
        context.user_data.pop("agent_state", None)
        context.user_data.pop("agent_selected", None)


async def _do_compact_from_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, instructions: str
) -> None:
    """Execute custom compact from user's text input."""
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await update.message.reply_text("No active session to compact.")
        return

    if session.is_busy:
        await update.message.reply_text("Agent is busy. Wait for it to finish first.")
        context.user_data["agent_compact_custom"] = True  # re-set state
        return

    placeholder = await update.message.reply_text("Compacting context...")

    # Get summary with custom instructions
    prompt = COMPACT_PROMPT_CUSTOM_TEMPLATE.format(instructions=instructions)
    try:
        summary = await session.client.prompt(prompt)
    except Exception as e:
        log.exception("Failed to get compact summary")
        await placeholder.edit_text(f"Compact failed: {e}")
        return

    if not summary or not summary.strip():
        await placeholder.edit_text("Agent returned empty summary. Compact aborted.")
        return

    # Destroy old session and recreate
    agent_key = session.agent_key
    await destroy_session(chat_id)

    try:
        user_id = update.effective_user.id
        bot = context.bot

        async def _perm_cb(tool_call, options):
            from .confirmation import permission_callback

            return await permission_callback(bot, chat_id, tool_call, options)

        new_session = await get_or_create_session(
            chat_id=chat_id,
            agent_key=agent_key,
            permission_callback=_perm_cb,
            user_id=user_id,
        )
        compact_context = COMPACT_CONTEXT_TEMPLATE.format(summary=summary)
        await new_session.client.prompt(compact_context)
    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await placeholder.edit_text(f"Compact failed during session reset: {e}")
        context.user_data.pop("agent_state", None)
        context.user_data.pop("agent_selected", None)
        return

    word_count = len(summary.split())
    await placeholder.edit_text(
        f"Context compacted ({word_count} words carried over).\n\n"
        "Send a message to continue chatting."
    )


async def agent_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle text messages while agent is active."""
    # Block agent mode in group chats
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        context.user_data.pop("agent_state", None)
        return

    chat_id = update.effective_chat.id
    text = update.message.text

    if not text:
        return

    # Handle custom compact input
    if context.user_data.pop("agent_compact_custom", None):
        await _do_compact_from_message(update, context, text)
        return

    session = get_session(chat_id)

    # Auto-create session if agent_state is active but no session exists
    if not session or not session.client.alive:
        agent_key = context.user_data.get("agent_selected", DEFAULT_AGENT)
        user_id = update.effective_user.id if update.effective_user else None
        try:
            bot = context.bot

            async def _perm_cb(tool_call, options):
                from .confirmation import permission_callback

                return await permission_callback(bot, chat_id, tool_call, options)

            session = await get_or_create_session(
                chat_id=chat_id,
                agent_key=agent_key,
                permission_callback=_perm_cb,
                user_id=user_id,
            )
        except Exception as e:
            log.exception("Failed to create agent session")
            await update.message.reply_text(f"Failed to start agent: {e}")
            context.user_data.pop("agent_state", None)
            return

    # Check if busy
    if session.is_busy:
        await update.message.reply_text(
            r"⏳ Still working on the previous request\.\.\." "\n"
            r"Your message will be queued — or wait for it to finish\.",
            parse_mode="MarkdownV2",
        )
        return

    # Send placeholder
    placeholder = await update.message.reply_text("Thinking...")

    # Create streamer and start edit loop
    streamer = TelegramStreamer(
        bot=context.bot,
        chat_id=chat_id,
        initial_message_id=placeholder.message_id,
    )
    edit_task = streamer.start_edit_loop()

    last_event = None
    try:
        async for event in session.prompt_stream(text):
            await streamer.process_event(event)
            last_event = event
    except Exception as e:
        log.exception("Agent prompt error")
        await streamer.finalize()
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=placeholder.message_id,
            text=f"Agent error: {e}",
        )
        # Destroy broken session so next message recreates it
        await destroy_session(chat_id)
        return

    await streamer.finalize()

    # Detect subprocess death mid-stream
    if isinstance(last_event, PromptDone) and last_event.stop_reason == "disconnected":
        await destroy_session(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Agent session disconnected. Send a message to start a new session.",
        )
    elif isinstance(last_event, PromptDone) and last_event.stop_reason == "timeout":
        await destroy_session(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Agent timed out (took too long). Send a message to start a new session.",
        )
