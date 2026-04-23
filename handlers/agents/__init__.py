"""Agent chat handler -- /agent command, callback router, message handler."""

import logging
import shutil

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
    build_trading_context,
    get_project_dir,
)
from .confirmation import resolve_confirmation
from .menu import show_agent_menu
from condor.acp import ACP_COMMANDS, PromptDone
from condor.acp.pydantic_ai_client import is_pydantic_ai_model
from .session import destroy_session, get_or_create_session, get_session
from .stream import TelegramStreamer

log = logging.getLogger(__name__)

# Cache CLI availability checks so we only hit the filesystem once per key
_cli_available_cache: dict[str, bool] = {}


def _is_agent_available(agent_key: str) -> bool:
    """Check if the agent backend is available.

    For ACP agents (claude-code, gemini): checks if the CLI binary is in PATH.
    For pydantic-ai agents (ollama:*, openai:*, etc.): always available
    (pydantic-ai handles connection errors at runtime).
    """
    # Pydantic-ai models don't need a CLI binary
    if is_pydantic_ai_model(agent_key):
        return True

    if agent_key in _cli_available_cache:
        return _cli_available_cache[agent_key]

    cmd = ACP_COMMANDS.get(agent_key, ACP_COMMANDS.get("claude-code", ""))
    # The command may have flags (e.g. "gemini --experimental-acp"), check the binary
    binary = cmd.split()[0] if cmd else ""
    available = shutil.which(binary) is not None
    _cli_available_cache[agent_key] = available

    if not available:
        log.warning("Agent CLI %r not found in PATH (agent_key=%s)", binary, agent_key)

    return available


@restricted
async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /agent command — manage agent settings, mode, and session."""
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text(
            "Agent mode is only available in private chats."
        )
        return

    clear_all_input_states(context)

    # Ensure defaults are set
    context.user_data.setdefault("agent_mode", DEFAULT_MODE)
    context.user_data.setdefault("agent_llm", DEFAULT_AGENT)

    # Warn if no agent CLI is available
    agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
    if not _is_agent_available(agent_key):
        available = [k for k in AGENT_OPTIONS if _is_agent_available(k)]
        if not available:
            await update.message.reply_text(
                "No agent CLI found.\n\n"
                "Install one of:\n"
                "• claude-agent-acp (Claude Agent)\n"
                "• gemini (Gemini CLI)\n"
                "• npx @zed-industries/codex-acp (ChatGPT Codex ACP bridge)\n\n"
                "Then restart the bot."
            )
            return
        # Auto-switch to an available one
        context.user_data["agent_llm"] = available[0]

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
        await _handle_mode_start(update, context, mode)
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



async def _handle_mode_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    mode: str,
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

    context.user_data["agent_mode"] = mode

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

        if extra_context:
            try:
                await session.client.prompt(extra_context)
            except Exception:
                log.warning("Failed to inject %s context for chat %d", mode, chat_id)

        await message.edit_text(
            f"{mode_label} is ready. Send a message to start chatting.\n\n"
            "Use /agent to see options or any other command to exit."
        )
    except Exception as e:
        log.exception("Failed to start agent session")
        await message.edit_text(f"Failed to start agent: {e}")


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

    if destroyed:
        await query.message.edit_text("Agent session stopped.")
    else:
        await query.message.edit_text("No active session.")


async def _handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close the agent menu (keep session alive if running)."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

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

    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await query.message.edit_text(f"Compact failed during session reset: {e}")
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
    """Destroy current session and start a fresh one in the same mode."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session.")
        return

    mode = session.mode
    await _handle_mode_start(update, context, mode)


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
    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await placeholder.edit_text(f"Compact failed during session reset: {e}")
        return

    word_count = len(summary.split())
    await placeholder.edit_text(
        f"Context compacted ({word_count} words carried over).\n\n"
        "Send a message to continue chatting."
    )


async def agent_voice_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle voice messages — transcribe and forward as text to the always-on agent."""
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        return

    # Auth check — only approved users
    from config_manager import UserRole, get_config_manager

    user_id = update.effective_user.id
    cm = get_config_manager()
    role = cm.get_user_role(user_id)
    if role not in (UserRole.ADMIN, UserRole.USER):
        return

    # Skip if no agent CLI available
    agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
    if not get_session(update.effective_chat.id) and not _is_agent_available(agent_key):
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
    await status_msg.edit_text(
        f"🎙 _{escaped}_\n\nThinking\\.\\.\\.", parse_mode="MarkdownV2"
    )

    # Store the status message so agent_message_handler reuses it as placeholder
    context.chat_data["_voice_placeholder"] = status_msg
    context.chat_data["_voice_transcription"] = text

    # Forward to agent handler — pass transcribed text via chat_data
    # (Message.text is read-only in python-telegram-bot)
    await agent_message_handler(update, context)


async def agent_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle text messages — always-on agent fallback.

    This is called for any text that doesn't match a specific handler state.
    Auto-creates an agent session with the user's preferred LLM if none exists.
    """
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        return

    # Auth check — only approved users can use the agent
    from config_manager import UserRole, get_config_manager

    user_id = update.effective_user.id
    cm = get_config_manager()
    role = cm.get_user_role(user_id)
    if role not in (UserRole.ADMIN, UserRole.USER):
        return

    chat_id = update.effective_chat.id
    text = context.chat_data.pop("_voice_transcription", None) or update.message.text

    if not text:
        return

    # "-" resets the ACP session: destroy current and let the next block auto-create a new one
    if text.strip() == "-":
        session = get_session(chat_id)
        if session:
            await destroy_session(chat_id)
        await update.message.reply_text("Session reset. Send a message to start fresh.")
        return

    # Handle custom compact input
    if context.user_data.pop("agent_compact_custom", None):
        await _do_compact_from_message(update, context, text)
        return

    # Backward compat
    mode = context.user_data.get("agent_mode", DEFAULT_MODE)
    if mode == "trading":
        mode = "agent_builder"

    session = get_session(chat_id)

    # Auto-create session if none exists (always-on agent)
    if not session or not session.client.alive:
        agent_key = context.user_data.get("agent_llm", context.user_data.get("agent_selected", DEFAULT_AGENT))
        context.user_data.setdefault("agent_llm", agent_key)

        # Always start in condor mode when auto-creating a session (e.g. after restart).
        # Users can switch to agent_builder via /agent menu.
        mode = DEFAULT_MODE
        context.user_data["agent_mode"] = mode

        # Check if the CLI binary is installed before attempting to spawn
        if not _is_agent_available(agent_key):
            log.debug("Agent CLI for %s not found, skipping auto-create", agent_key)
            return

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

            if extra_context:
                try:
                    await session.client.prompt(extra_context)
                except Exception:
                    log.warning("Failed to inject %s context for chat %d", mode, chat_id)

        except Exception as e:
            log.exception("Failed to create agent session")
            await update.message.reply_text(f"Failed to start agent: {e}")
            return

    # Check if busy
    if session.is_busy:
        await update.message.reply_text(
            r"⏳ Still working on the previous request\.\.\." "\n"
            r"Your message will be queued — or wait for it to finish\.",
            parse_mode="MarkdownV2",
        )
        return

    # Create streamer prefix
    prefix = ""
    mode_label = AGENT_MODES.get(mode, {}).get("label", "")
    if mode != DEFAULT_MODE and mode_label:
        prefix = f"{mode_label}\n\n"

    # Fetch voice data if this was a transcription
    voice_placeholder = context.chat_data.pop("_voice_placeholder", None)
    voice_transcription = context.chat_data.pop("_voice_transcription", None)

    if voice_transcription:
        voice_prefix = f"🎙 {voice_transcription}"
        prefix = f"{prefix}{voice_prefix}" if prefix else voice_prefix

    # Send or reuse placeholder message
    if voice_placeholder:
        placeholder = voice_placeholder
    else:
        placeholder = await update.message.reply_text("Thinking...")

    streamer = TelegramStreamer(
        bot=context.bot,
        chat_id=chat_id,
        message_id=placeholder.message_id,
        prefix=prefix,
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