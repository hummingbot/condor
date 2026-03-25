"""Agent chat handler -- /agent command, callback router, message handler."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers import clear_all_input_states
from utils.auth import restricted

from ._shared import (
    AGENT_MODES,
    AGENT_OPTIONS,
    COMPACT_CONTEXT_TEMPLATE,
    COMPACT_PROMPT_AUTO,
    COMPACT_PROMPT_CUSTOM_TEMPLATE,
    DEFAULT_AGENT,
    DEFAULT_MODE,
    build_agent_chat_context,
    build_trading_context,
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
    """Handle /agent command — auto-start Condor or show active session menu."""
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text(
            "Agent mode is only available in private chats."
        )
        return

    clear_all_input_states(context)

    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if session and session.client.alive:
        # Active session exists — show menu
        context.user_data["agent_state"] = "active"
        await show_agent_menu(update, context)
    else:
        # No session — show menu with start/settings options
        context.user_data["agent_state"] = "active"
        context.user_data.setdefault("agent_mode", DEFAULT_MODE)
        context.user_data.setdefault("agent_llm", DEFAULT_AGENT)
        await show_agent_menu(update, context)


@restricted
async def agent_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Route agent:* callbacks."""
    query = update.callback_query
    await query.answer()

    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await query.message.edit_text("Agent mode is only available in private chats.")
        return

    data = query.data
    action = data.split(":", 1)[1] if ":" in data else data

    # Mode switching
    if action.startswith("mode:"):
        mode = action.split(":", 1)[1]
        if mode == "chat_with_agent":
            await _handle_chat_with_agent_menu(update, context)
        else:
            await _handle_mode_start(update, context, mode)
    elif action.startswith("chat_target:"):
        agent_id = action.split(":", 1)[1]
        await _handle_mode_start(update, context, "chat_with_agent", agent_id=agent_id)
    elif action == "switch_mode":
        await _handle_switch_mode_menu(update, context)

    # Settings
    elif action == "settings":
        await _handle_settings(update, context)
    elif action.startswith("set_llm:"):
        llm_key = action.split(":", 1)[1]
        await _handle_set_llm(update, context, llm_key)

    # Session management
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
    elif action == "context":
        await _handle_context_dump(update, context)
    elif action == "new":
        await _handle_new_session(update, context)

    # Trade confirmations
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

    # Widget callbacks
    elif action.startswith("w:"):
        parts = action.split(":")
        request_id, btn_idx = parts[1], int(parts[2])
        from condor.widget_bridge import get_widget_bridge

        resolved = get_widget_bridge().resolve(request_id, btn_idx)
        if not resolved:
            await query.message.edit_text("Session expired.")


async def _handle_mode_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    mode: str,
    agent_id: str | None = None,
) -> None:
    """Start a session in the given mode."""
    query = update.callback_query
    message = query.message if query else update.message
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Backward compat: treat "trading" as "agent_builder"
    if mode == "trading":
        mode = "agent_builder"

    agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
    mode_label = AGENT_MODES.get(mode, {}).get("label", mode)
    llm_label = AGENT_OPTIONS.get(agent_key, {}).get("label", agent_key)

    status_text = f"Starting {mode_label} session ({llm_label})..."
    if query:
        await message.edit_text(status_text)
    else:
        message = await message.reply_text(status_text)

    # Destroy existing session
    await destroy_session(chat_id)

    context.user_data["agent_state"] = "active"
    context.user_data["agent_mode"] = mode
    context.user_data["agent_selected"] = agent_key
    if agent_id:
        context.user_data["agent_chat_target"] = agent_id

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
            user_data=context.user_data,
            mode=mode,
        )

        # Inject mode-specific context
        extra_context = None
        if mode == "agent_builder":
            extra_context = build_trading_context()
        elif mode == "chat_with_agent" and agent_id:
            extra_context = build_agent_chat_context(agent_id)

        if extra_context:
            try:
                await session.client.prompt(extra_context)
                if session.client.last_usage:
                    u = session.client.last_usage
                    if u.used >= session.tokens_used:
                        session.tokens_used = u.used
                    session.context_window = u.size
                    if u.cost_usd >= session.cost_usd:
                        session.cost_usd = u.cost_usd
            except Exception:
                log.warning("Failed to inject %s context for chat %d", mode, chat_id)

        await message.edit_text(
            f"{mode_label} is ready. Send a message to start chatting.\n\n"
            "Use /agent to see options or any other command to exit."
        )
    except Exception as e:
        log.exception("Failed to start agent session")
        await message.edit_text(f"Failed to start agent: {e}")
        context.user_data.pop("agent_state", None)
        context.user_data.pop("agent_mode", None)
        context.user_data.pop("agent_selected", None)
        context.user_data.pop("agent_chat_target", None)


async def _handle_switch_mode_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show mode selection menu."""
    from .menu import _mode_selection_keyboard

    query = update.callback_query
    lines = ["Select a mode:\n"]
    for key, info in AGENT_MODES.items():
        lines.append(f"• {info['label']} — {info['description']}")
    await query.message.edit_text(
        "\n".join(lines), reply_markup=_mode_selection_keyboard()
    )


async def _handle_chat_with_agent_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show list of running agents to chat with."""
    from .menu import _running_agents_keyboard

    query = update.callback_query
    await query.message.edit_text(
        "Select a running agent to chat with:",
        reply_markup=_running_agents_keyboard(),
    )


async def _handle_settings(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show settings sub-menu with LLM picker."""
    from .menu import _settings_keyboard

    query = update.callback_query
    current_llm = context.user_data.get("agent_llm", DEFAULT_AGENT)
    await query.message.edit_text(
        "Select the LLM for new sessions:",
        reply_markup=_settings_keyboard(current_llm),
    )


async def _handle_set_llm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, llm_key: str
) -> None:
    """Update the preferred LLM."""
    query = update.callback_query
    if llm_key not in AGENT_OPTIONS:
        await query.message.edit_text("Unknown LLM option.")
        return

    context.user_data["agent_llm"] = llm_key
    label = AGENT_OPTIONS[llm_key]["label"]
    await query.message.edit_text(
        f"LLM set to {label}. New sessions will use this model.\n\n"
        "Use /agent to continue."
    )


async def _handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop the active agent session."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    destroyed = await destroy_session(chat_id)
    context.user_data.pop("agent_state", None)
    context.user_data.pop("agent_selected", None)
    context.user_data.pop("agent_mode", None)
    context.user_data.pop("agent_chat_target", None)

    if destroyed:
        await query.message.edit_text("Agent session stopped.")
    else:
        await query.message.edit_text("No active session.")


async def _handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close the agent menu (keep session alive if running)."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        context.user_data.pop("agent_state", None)

    await query.message.delete()


CONTEXT_DUMP_PROMPT = (
    "List ALL context you currently have loaded. Include:\n"
    "1. System prompt / CLAUDE.md instructions (summarize key points)\n"
    "2. Initial context injected at session start\n"
    "3. Every user message and your response (with approximate token count per turn)\n"
    "4. All tool calls made and their results (summarized)\n"
    "5. Any MCP servers connected and tools available\n\n"
    "Format as a numbered conversation log. Be concise but complete. "
    "This is for debugging context usage - show everything."
)


async def _handle_context_dump(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Ask the agent to dump its full loaded context."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session.")
        return

    if session.is_busy:
        await query.message.edit_text("Agent is busy. Wait for it to finish first.")
        return

    await query.message.edit_text("Fetching context dump...")

    streamer = TelegramStreamer(context.bot, chat_id, reply_to=query.message.message_id)
    async for event in session.prompt_stream(CONTEXT_DUMP_PROMPT):
        await streamer.process_event(event)
    await streamer.finalize()


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

    if custom_instructions:
        prompt = COMPACT_PROMPT_CUSTOM_TEMPLATE.format(instructions=custom_instructions)
    else:
        prompt = COMPACT_PROMPT_AUTO

    try:
        summary = await session.client.prompt(prompt)
    except Exception as e:
        log.exception("Failed to get compact summary")
        await query.message.edit_text(f"Compact failed: {e}")
        return

    if not summary or not summary.strip():
        await query.message.edit_text("Agent returned empty summary. Compact aborted.")
        return

    agent_key = session.agent_key
    mode = session.mode
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
            user_data=context.user_data,
            mode=mode,
        )

        compact_context = COMPACT_CONTEXT_TEMPLATE.format(summary=summary)
        await new_session.client.prompt(compact_context)

        if new_session.client.last_usage:
            u = new_session.client.last_usage
            if u.used >= new_session.tokens_used:
                new_session.tokens_used = u.used
            new_session.context_window = u.size
            if u.cost_usd >= new_session.cost_usd:
                new_session.cost_usd = u.cost_usd

    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await query.message.edit_text(f"Compact failed during session reset: {e}")
        context.user_data.pop("agent_state", None)
        context.user_data.pop("agent_selected", None)
        context.user_data.pop("agent_mode", None)
        return

    word_count = len(summary.split())
    pct = round(new_session.tokens_used / new_session.context_window * 100) if new_session.context_window > 0 and new_session.tokens_used > 0 else 0
    await query.message.edit_text(
        f"Context compacted ({word_count} words carried over).\n"
        f"Context: {round(new_session.tokens_used / 1000)}k / {round(new_session.context_window / 1000)}k ({pct}%)\n\n"
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
    """Destroy current session and start a fresh one in the same mode."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session.")
        return

    mode = session.mode
    agent_id = context.user_data.get("agent_chat_target")
    await _handle_mode_start(update, context, mode, agent_id=agent_id)


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
        context.user_data["agent_compact_custom"] = True
        return

    placeholder = await update.message.reply_text("Compacting context...")

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

    agent_key = session.agent_key
    mode = session.mode
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
            user_data=context.user_data,
            mode=mode,
        )
        compact_context = COMPACT_CONTEXT_TEMPLATE.format(summary=summary)
        await new_session.client.prompt(compact_context)

        if new_session.client.last_usage:
            u = new_session.client.last_usage
            if u.used >= new_session.tokens_used:
                new_session.tokens_used = u.used
            new_session.context_window = u.size
            if u.cost_usd >= new_session.cost_usd:
                new_session.cost_usd = u.cost_usd
    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await placeholder.edit_text(f"Compact failed during session reset: {e}")
        context.user_data.pop("agent_state", None)
        context.user_data.pop("agent_selected", None)
        context.user_data.pop("agent_mode", None)
        return

    word_count = len(summary.split())
    pct = round(new_session.tokens_used / new_session.context_window * 100) if new_session.context_window > 0 and new_session.tokens_used > 0 else 0
    await placeholder.edit_text(
        f"Context compacted ({word_count} words carried over).\n"
        f"Context: {round(new_session.tokens_used / 1000)}k / {round(new_session.context_window / 1000)}k ({pct}%)\n\n"
        "Send a message to continue chatting."
    )


async def agent_voice_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle voice messages while agent is active — transcribe and forward as text."""
    if context.user_data.get("agent_state") != "active":
        return

    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        return

    voice = update.message.voice
    if not voice:
        return

    # Download the voice file
    status_msg = await update.message.reply_text("🎙 Transcribing voice...")
    try:
        tg_file = await voice.get_file()
        file_bytes = await tg_file.download_as_bytearray()

        from utils.transcribe import transcribe_voice

        text = await transcribe_voice(bytes(file_bytes))
    except Exception as e:
        log.exception("Voice transcription failed")
        await status_msg.edit_text(f"Transcription failed: {e}")
        return

    if not text or not text.strip():
        await status_msg.edit_text("Could not transcribe any speech from the voice message.")
        return

    # Show the transcribed text
    from utils.telegram_formatters import escape_markdown_v2

    escaped = escape_markdown_v2(text)
    await status_msg.edit_text(f"🎙 _{escaped}_", parse_mode="MarkdownV2")

    # Inject the transcribed text as if the user typed it
    update.message.text = text
    await agent_message_handler(update, context)


async def agent_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle text messages while agent is active."""
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

    # Directive injection for Chat with Agent mode
    mode = context.user_data.get("agent_mode", DEFAULT_MODE)
    # Backward compat
    if mode == "trading":
        mode = "agent_builder"

    if mode == "chat_with_agent" and text.startswith("!"):
        agent_id = context.user_data.get("agent_chat_target")
        if agent_id:
            from condor.trading_agent.engine import get_engine

            engine = get_engine(agent_id)
            if engine:
                directive = text[1:].strip()
                engine.inject_directive(directive)
                await update.message.reply_text(
                    f"Directive injected for agent {agent_id}. "
                    "It will be applied on the next tick."
                )
                return
            else:
                await update.message.reply_text(f"Agent {agent_id} is no longer running.")
                return

    session = get_session(chat_id)

    # Auto-create session if agent_state is active but no session exists
    if not session or not session.client.alive:
        agent_key = context.user_data.get("agent_llm", context.user_data.get("agent_selected", DEFAULT_AGENT))
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
                user_data=context.user_data,
                mode=mode,
            )

            # Inject mode-specific context for non-condor modes
            extra_context = None
            if mode == "agent_builder":
                extra_context = build_trading_context()
            elif mode == "chat_with_agent":
                target = context.user_data.get("agent_chat_target")
                if target:
                    extra_context = build_agent_chat_context(target)

            if extra_context:
                try:
                    await session.client.prompt(extra_context)
                    if session.client.last_usage:
                        u = session.client.last_usage
                        if u.used >= session.tokens_used:
                            session.tokens_used = u.used
                        session.context_window = u.size
                        if u.cost_usd >= session.cost_usd:
                            session.cost_usd = u.cost_usd
                except Exception:
                    log.warning("Failed to inject %s context for chat %d", mode, chat_id)

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
