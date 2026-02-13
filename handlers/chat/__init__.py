"""
LLM Chat handler with streaming support.

This module provides a chat interface that uses pydantic-ai for LLM responses
and streams them to users via Telegram's sendMessageDraft API.

Supports multiple LLM providers:
- OpenAI (GPT-4o, GPT-4o-mini)
- Anthropic (Claude 3.5 Sonnet, Claude 3 Haiku)
- Google (Gemini 2.0 Flash, Gemini 1.5 Pro)

Requirements:
- Bot must have Threaded Mode enabled via @BotFather for streaming
- At least one LLM API key must be configured (OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY)
- Users must start a topic thread for optimal streaming experience
"""

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)

from pydantic_ai import Agent

from utils.auth import restricted
from utils.config import OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY
from utils.streaming import (
    StreamingResponse,
    StreamConfig,
    get_message_thread_id,
    is_topic_chat,
)
from handlers import clear_all_input_states

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for an LLM model."""
    provider: str
    model_id: str
    display_name: str
    api_key_env: str
    description: str


# Available models configuration
AVAILABLE_MODELS: Dict[str, ModelConfig] = {
    # OpenAI models
    "gpt-4o": ModelConfig(
        provider="openai",
        model_id="openai:gpt-4o",
        display_name="GPT-4o",
        api_key_env="OPENAI_API_KEY",
        description="Most capable OpenAI model",
    ),
    "gpt-4o-mini": ModelConfig(
        provider="openai",
        model_id="openai:gpt-4o-mini",
        display_name="GPT-4o Mini",
        api_key_env="OPENAI_API_KEY",
        description="Fast and cost-effective",
    ),
    # Anthropic models
    "claude-3-5-sonnet": ModelConfig(
        provider="anthropic",
        model_id="anthropic:claude-3-5-sonnet-latest",
        display_name="Claude 3.5 Sonnet",
        api_key_env="ANTHROPIC_API_KEY",
        description="Best for complex tasks",
    ),
    "claude-3-5-haiku": ModelConfig(
        provider="anthropic",
        model_id="anthropic:claude-3-5-haiku-latest",
        display_name="Claude 3.5 Haiku",
        api_key_env="ANTHROPIC_API_KEY",
        description="Fast and efficient",
    ),
    # Google models
    "gemini-2.0-flash": ModelConfig(
        provider="google",
        model_id="google-gla:gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        api_key_env="GOOGLE_API_KEY",
        description="Google's latest fast model",
    ),
    "gemini-1.5-pro": ModelConfig(
        provider="google",
        model_id="google-gla:gemini-1.5-pro",
        display_name="Gemini 1.5 Pro",
        api_key_env="GOOGLE_API_KEY",
        description="Best for long context",
    ),
}

# Map of API keys
API_KEYS = {
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GOOGLE_API_KEY": GOOGLE_API_KEY,
}

# System prompt for the assistant
SYSTEM_PROMPT = """You are a helpful trading assistant for the Condor Telegram bot.
You help users with cryptocurrency trading, portfolio management, and market analysis.
Keep responses concise and actionable. Use markdown formatting when helpful."""

# Chat state keys
CHAT_STATE_KEY = "chat_state"
CHAT_HISTORY_KEY = "chat_history"
CHAT_MODEL_KEY = "chat_model"

# Cache for agents by model
_agents: Dict[str, Agent] = {}


def get_available_models() -> Dict[str, ModelConfig]:
    """Get models that have their API keys configured."""
    available = {}
    for model_key, config in AVAILABLE_MODELS.items():
        api_key = API_KEYS.get(config.api_key_env)
        if api_key:
            available[model_key] = config
    return available


def get_default_model() -> Optional[str]:
    """Get the default model (first available)."""
    available = get_available_models()
    if not available:
        return None
    # Prefer Claude, then GPT-4o-mini, then Gemini
    preferred_order = ["claude-3-5-sonnet", "gpt-4o-mini", "gemini-2.0-flash"]
    for model in preferred_order:
        if model in available:
            return model
    return next(iter(available.keys()))


def get_agent(model_key: str) -> Agent:
    """Get or create a pydantic-ai agent for the specified model."""
    if model_key in _agents:
        return _agents[model_key]

    config = AVAILABLE_MODELS.get(model_key)
    if not config:
        raise ValueError(f"Unknown model: {model_key}")

    api_key = API_KEYS.get(config.api_key_env)
    if not api_key:
        raise ValueError(f"API key not configured for {config.display_name}")

    # Create agent with the model ID (pydantic-ai format: "provider:model")
    agent = Agent(config.model_id, system_prompt=SYSTEM_PROMPT)
    _agents[model_key] = agent
    return agent


def _get_model_keyboard(current_model: Optional[str] = None) -> InlineKeyboardMarkup:
    """Build keyboard for model selection."""
    available = get_available_models()
    keyboard = []

    # Group by provider
    providers = {}
    for model_key, config in available.items():
        if config.provider not in providers:
            providers[config.provider] = []
        providers[config.provider].append((model_key, config))

    for provider, models in providers.items():
        row = []
        for model_key, config in models:
            icon = "✓ " if model_key == current_model else ""
            row.append(
                InlineKeyboardButton(
                    f"{icon}{config.display_name}",
                    callback_data=f"chat:model:{model_key}",
                )
            )
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("🗑️ Clear History", callback_data="chat:clear"),
        InlineKeyboardButton("❌ End Chat", callback_data="chat:cancel"),
    ])

    return InlineKeyboardMarkup(keyboard)


def _get_help_keyboard() -> InlineKeyboardMarkup:
    """Build help keyboard for chat commands."""
    keyboard = [
        [
            InlineKeyboardButton("🔄 Change Model", callback_data="chat:select_model"),
            InlineKeyboardButton("🗑️ Clear History", callback_data="chat:clear"),
        ],
        [InlineKeyboardButton("❌ End Chat", callback_data="chat:cancel")],
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

    # Check for available models
    available = get_available_models()
    if not available:
        await update.message.reply_text(
            "❌ No LLM API keys configured\\.\n\n"
            "Please set at least one of these environment variables:\n"
            "• `OPENAI_API_KEY`\n"
            "• `ANTHROPIC_API_KEY`\n"
            "• `GOOGLE_API_KEY`",
            parse_mode="MarkdownV2",
        )
        return

    # Get or set default model
    current_model = context.user_data.get(CHAT_MODEL_KEY)
    if not current_model or current_model not in available:
        current_model = get_default_model()
        context.user_data[CHAT_MODEL_KEY] = current_model

    model_config = AVAILABLE_MODELS[current_model]

    # Set chat state
    context.user_data[CHAT_STATE_KEY] = "active"

    # Check if we're in a topic chat for streaming
    is_topic = is_topic_chat(update)

    if is_topic:
        await update.message.reply_text(
            f"💬 *Chat Mode Active*\n\n"
            f"Model: *{model_config.display_name}*\n"
            f"Streaming: ✅ Enabled\n\n"
            f"Send me any message and I'll respond\\.\n"
            f"Use /endchat to exit\\.",
            parse_mode="MarkdownV2",
            reply_markup=_get_help_keyboard(),
        )
    else:
        await update.message.reply_text(
            f"💬 *Chat Mode Active*\n\n"
            f"Model: *{model_config.display_name}*\n\n"
            f"Send me any message and I'll respond\\.\n\n"
            f"⚠️ *For streaming responses:*\n"
            f"Start a topic thread to see live streaming\\.\n\n"
            f"Use /endchat to exit\\.",
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

    # Get current model
    current_model = context.user_data.get(CHAT_MODEL_KEY)
    if not current_model:
        current_model = get_default_model()
        if not current_model:
            await message.reply_text("❌ No LLM configured. Use /chat to set up.")
            return True
        context.user_data[CHAT_MODEL_KEY] = current_model

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
        agent = get_agent(current_model)

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

    data = query.data
    parts = data.split(":")

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "clear":
        # Clear chat history
        context.user_data.pop(CHAT_HISTORY_KEY, None)
        await query.message.reply_text("🗑️ Chat history cleared.")

    elif action == "cancel":
        # Exit chat mode
        context.user_data.pop(CHAT_STATE_KEY, None)
        context.user_data.pop(CHAT_HISTORY_KEY, None)
        await query.message.edit_text("Chat mode ended.")

    elif action == "select_model":
        # Show model selection
        current_model = context.user_data.get(CHAT_MODEL_KEY)
        await query.message.edit_text(
            "🤖 *Select LLM Model*\n\nChoose the model you want to chat with:",
            parse_mode="MarkdownV2",
            reply_markup=_get_model_keyboard(current_model),
        )

    elif action == "model" and len(parts) >= 3:
        # Set model
        model_key = parts[2]
        available = get_available_models()

        if model_key not in available:
            await query.message.reply_text("❌ Model not available.")
            return

        context.user_data[CHAT_MODEL_KEY] = model_key
        context.user_data.pop(CHAT_HISTORY_KEY, None)  # Clear history on model change

        model_config = AVAILABLE_MODELS[model_key]
        await query.message.edit_text(
            f"✅ Model changed to *{model_config.display_name}*\n\n"
            f"_{model_config.description}_\n\n"
            f"Chat history cleared\\. Send a message to start\\!",
            parse_mode="MarkdownV2",
            reply_markup=_get_help_keyboard(),
        )

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

Once in a topic, you'll see responses stream in real\\-time\\!"""

        await query.message.reply_text(help_text, parse_mode="MarkdownV2")

    elif action == "continue":
        current_model = context.user_data.get(CHAT_MODEL_KEY, get_default_model())
        model_config = AVAILABLE_MODELS.get(current_model)
        model_name = model_config.display_name if model_config else "Unknown"

        await query.message.edit_text(
            f"💬 Chat mode active with *{model_name}*\n\n"
            f"Send me a message\\!\n"
            f"_Note: Responses won't stream in real\\-time outside of topics\\._",
            parse_mode="MarkdownV2",
            reply_markup=_get_help_keyboard(),
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
