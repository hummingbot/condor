"""
LLM Chat handler with streaming support.

This module provides a chat interface that uses pydantic-ai for LLM responses
and streams them to users via Telegram's sendMessageDraft API.

Requirements:
- Bot must have Threaded Mode enabled via @BotFather for streaming
- OPENAI_API_KEY environment variable must be set
- Users must start a topic thread for optimal streaming experience
"""

import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from utils.auth import restricted
from utils.config import OPENAI_API_KEY
from utils.streaming import (
    StreamingResponse,
    StreamConfig,
    stream_llm_response,
    get_message_thread_id,
    is_topic_chat,
)
from handlers import clear_all_input_states

logger = logging.getLogger(__name__)

# Initialize the LLM agent
# You can customize this with system prompts, tools, etc.
SYSTEM_PROMPT = """You are a helpful trading assistant for the Condor Telegram bot.
You help users with cryptocurrency trading, portfolio management, and market analysis.
Keep responses concise and actionable. Use markdown formatting when helpful."""

_agent: Optional[Agent] = None


def get_agent() -> Agent:
    """Get or create the pydantic-ai agent."""
    global _agent

    if _agent is None:
        if not OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY environment variable is required for chat functionality"
            )

        model = OpenAIModel("gpt-4o-mini", api_key=OPENAI_API_KEY)
        _agent = Agent(model, system_prompt=SYSTEM_PROMPT)

    return _agent


# Chat state keys
CHAT_STATE_KEY = "chat_state"
CHAT_HISTORY_KEY = "chat_history"


def _get_help_keyboard() -> InlineKeyboardMarkup:
    """Build help keyboard for chat commands."""
    keyboard = [
        [
            InlineKeyboardButton("🗑️ Clear History", callback_data="chat:clear"),
            InlineKeyboardButton("❌ Cancel", callback_data="chat:cancel"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def _get_topic_prompt_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard prompting user to use topics."""
    keyboard = [
        [
            InlineKeyboardButton(
                "📖 How to enable streaming", callback_data="chat:help_streaming"
            ),
        ],
        [InlineKeyboardButton("💬 Continue anyway", callback_data="chat:continue")],
    ]
    return InlineKeyboardMarkup(keyboard)


@restricted
async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /chat command.

    Starts an LLM chat session with streaming responses.
    """
    clear_all_input_states(context)

    # Check for OpenAI API key
    if not OPENAI_API_KEY:
        await update.message.reply_text(
            "❌ Chat functionality requires OPENAI_API_KEY to be configured.\n\n"
            "Please set this environment variable and restart the bot."
        )
        return

    # Set chat state
    context.user_data[CHAT_STATE_KEY] = "active"

    # Check if we're in a topic chat for streaming
    is_topic = is_topic_chat(update)
    thread_id = get_message_thread_id(update)

    if is_topic:
        await update.message.reply_text(
            "💬 *Chat Mode Active*\n\n"
            "I'm ready to chat\\! Streaming is enabled in this topic\\.\n\n"
            "Send me any message and I'll respond with live streaming\\.\n"
            "Use /endchat to exit chat mode\\.",
            parse_mode="MarkdownV2",
            reply_markup=_get_help_keyboard(),
        )
    else:
        await update.message.reply_text(
            "💬 *Chat Mode Active*\n\n"
            "Send me any message and I'll respond\\.\n\n"
            "⚠️ *For streaming responses:*\n"
            "Start a topic thread in this chat to see responses as they're generated\\.\n\n"
            "Use /endchat to exit chat mode\\.",
            parse_mode="MarkdownV2",
            reply_markup=_get_topic_prompt_keyboard(),
        )


@restricted
async def endchat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /endchat command - exit chat mode."""
    context.user_data.pop(CHAT_STATE_KEY, None)
    context.user_data.pop(CHAT_HISTORY_KEY, None)

    await update.message.reply_text(
        "Chat mode ended. Use /chat to start a new session."
    )


async def handle_chat_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """
    Handle incoming chat messages when in chat mode.

    Returns:
        True if the message was handled, False otherwise
    """
    # Check if we're in chat mode
    if context.user_data.get(CHAT_STATE_KEY) != "active":
        return False

    message = update.effective_message
    if not message or not message.text:
        return False

    # Skip commands
    if message.text.startswith("/"):
        return False

    user_message = message.text.strip()
    if not user_message:
        return False

    # Get conversation history
    history = context.user_data.get(CHAT_HISTORY_KEY, [])

    # Add user message to history
    history.append({"role": "user", "content": user_message})

    # Keep last 20 messages to avoid context overflow
    if len(history) > 20:
        history = history[-20:]

    context.user_data[CHAT_HISTORY_KEY] = history

    # Stream the response
    try:
        agent = get_agent()

        # Build context from history
        chat_context = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
            for m in history[:-1]  # Exclude current message
        )

        prompt = user_message
        if chat_context:
            prompt = f"Previous conversation:\n{chat_context}\n\nUser: {user_message}"

        # Get streaming config
        config = StreamConfig(
            min_update_interval=0.3,
            max_update_interval=1.5,
            min_chunk_size=15,
            streaming_suffix=" ▌",
            fallback_to_edit=True,
        )

        # Get thread ID for streaming
        thread_id = get_message_thread_id(update)

        # Create streaming response
        streaming = StreamingResponse(
            bot=context.bot,
            chat_id=update.effective_chat.id,
            message_thread_id=thread_id,
            reply_to_message_id=message.message_id,
            config=config,
        )

        async with streaming:
            # Use pydantic-ai streaming
            async with agent.run_stream(prompt) as result:
                async for chunk in result.stream_text():
                    await streaming.update(chunk)

        # Add assistant response to history
        if streaming.text:
            history.append({"role": "assistant", "content": streaming.text})
            context.user_data[CHAT_HISTORY_KEY] = history

        return True

    except Exception as e:
        logger.exception("Error in chat handler")
        await message.reply_text(f"❌ Error generating response: {str(e)}")
        return True


@restricted
async def chat_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle callback queries for chat functionality."""
    query = update.callback_query
    await query.answer()

    action = query.data.split(":")[1] if ":" in query.data else query.data

    if action == "clear":
        # Clear chat history
        context.user_data.pop(CHAT_HISTORY_KEY, None)
        await query.message.reply_text("🗑️ Chat history cleared.")

    elif action == "cancel":
        # Exit chat mode
        context.user_data.pop(CHAT_STATE_KEY, None)
        context.user_data.pop(CHAT_HISTORY_KEY, None)
        await query.message.edit_text("Chat mode ended.")

    elif action == "help_streaming":
        help_text = """*How to Enable Streaming Responses*

1\\. The bot owner must enable *Threaded Mode* via @BotFather:
   • Open @BotFather
   • Send /mybots
   • Select this bot
   • Go to *Bot Settings*
   • Toggle *Threaded Mode* ON

2\\. Start a *topic thread* in your chat:
   • Tap the topic icon \\(📑\\) in the chat
   • Create a new topic/thread
   • Send messages within the topic

Once in a topic, you'll see responses stream in real\\-time as they're generated\\!"""

        await query.message.reply_text(help_text, parse_mode="MarkdownV2")

    elif action == "continue":
        await query.message.edit_text(
            "💬 Chat mode active\\. Send me a message\\!\n\n"
            "Note: Responses won't stream in real\\-time outside of topics\\.",
            parse_mode="MarkdownV2",
        )


def get_chat_handlers() -> list:
    """Get all handlers for chat functionality."""
    return [
        CommandHandler("chat", chat_command),
        CommandHandler("endchat", endchat_command),
        CallbackQueryHandler(chat_callback_handler, pattern="^chat:"),
    ]


def get_chat_message_handler():
    """
    Get the message handler for chat mode.

    This should be integrated into the unified message handler
    in handlers/config/__init__.py
    """
    return handle_chat_message
