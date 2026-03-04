"""Agent chat handler -- /agent command, callback router, message handler."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers import clear_all_input_states
from utils.auth import restricted

from ._shared import AGENT_OPTIONS, DEFAULT_AGENT, get_project_dir
from .confirmation import resolve_confirmation
from .menu import show_agent_menu
from .session import destroy_session, get_or_create_session, get_session
from .stream import TelegramStreamer

log = logging.getLogger(__name__)


@restricted
async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /agent command."""
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

    data = query.data
    action = data.split(":", 1)[1] if ":" in data else data

    if action.startswith("select:"):
        agent_key = action.split(":", 1)[1]
        await _handle_select(update, context, agent_key)
    elif action == "stop":
        await _handle_stop(update, context)
    elif action == "close":
        await _handle_close(update, context)
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
    """Close the agent menu (keep session alive if running)."""
    query = update.callback_query
    context.user_data.pop("agent_state", None)
    await query.message.delete()


async def agent_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle text messages while agent is active."""
    chat_id = update.effective_chat.id
    text = update.message.text

    if not text:
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
            "\u23f3 Still working on the previous request\.\.\.\n"
            "Your message will be queued — or wait for it to finish\.",
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

    try:
        async for event in session.prompt_stream(text):
            await streamer.process_event(event)
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
