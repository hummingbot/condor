"""
Streaming utilities for LLM responses via Telegram Bot API 9.3+

This module provides utilities to stream LLM responses to users using
Telegram's sendMessageDraft API (requires bot to have Threaded Mode enabled
via @BotFather).

For chats without topic mode, falls back to progressive message editing.
"""

import asyncio
import logging
from typing import AsyncIterator, Optional, Callable, Any
from dataclasses import dataclass, field

from telegram import Bot, Message, Update
from telegram.error import TelegramError, BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


@dataclass
class StreamConfig:
    """Configuration for streaming behavior."""

    # Minimum interval between draft updates (seconds)
    # Telegram may rate limit if updates are too frequent
    min_update_interval: float = 0.3

    # Maximum interval to wait before forcing an update (seconds)
    max_update_interval: float = 2.0

    # Minimum characters to accumulate before updating draft
    min_chunk_size: int = 10

    # Whether to show typing indicator while streaming
    show_typing: bool = True

    # Fallback to edit_message_text if send_message_draft fails
    fallback_to_edit: bool = True

    # Parse mode for final message
    parse_mode: Optional[str] = None

    # Prefix to show while streaming (e.g., "🤔 " or "")
    streaming_prefix: str = ""

    # Suffix to show while streaming (e.g., " ▌" cursor)
    streaming_suffix: str = " ▌"


@dataclass
class StreamState:
    """Internal state for tracking a streaming session."""

    chat_id: int
    message_thread_id: Optional[int] = None
    accumulated_text: str = ""
    last_update_time: float = 0.0
    draft_supported: bool = True
    placeholder_message: Optional[Message] = None
    final_message: Optional[Message] = None
    is_complete: bool = False
    error: Optional[str] = None


class StreamingResponse:
    """
    Manages streaming an LLM response to a Telegram chat.

    Usage:
        async with StreamingResponse(bot, chat_id, message_thread_id) as stream:
            async for chunk in llm_stream:
                await stream.update(chunk)
        # Final message is sent automatically on exit

    Or manually:
        stream = StreamingResponse(bot, chat_id, message_thread_id)
        await stream.start()
        async for chunk in llm_stream:
            await stream.update(chunk)
        message = await stream.finish()
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        message_thread_id: Optional[int] = None,
        config: Optional[StreamConfig] = None,
        reply_to_message_id: Optional[int] = None,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.message_thread_id = message_thread_id
        self.config = config or StreamConfig()
        self.reply_to_message_id = reply_to_message_id
        self.state = StreamState(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )
        self._update_lock = asyncio.Lock()

    async def __aenter__(self) -> "StreamingResponse":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            await self.finish()
        else:
            # On error, try to update with error message
            await self._send_error_message(str(exc_val))

    async def start(self) -> None:
        """Initialize the streaming session."""
        self.state.last_update_time = asyncio.get_event_loop().time()

        # Check if we can use send_message_draft (requires message_thread_id)
        if self.message_thread_id is None:
            self.state.draft_supported = False
            logger.debug("No message_thread_id - will use edit fallback")

        # Send typing indicator
        if self.config.show_typing:
            try:
                await self.bot.send_chat_action(
                    chat_id=self.chat_id,
                    action="typing",
                    message_thread_id=self.message_thread_id,
                )
            except TelegramError as e:
                logger.warning(f"Failed to send typing action: {e}")

    async def update(self, chunk: str) -> None:
        """
        Add a chunk of text to the stream.

        Args:
            chunk: Text chunk to append to the response
        """
        if self.state.is_complete:
            logger.warning("Attempted to update completed stream")
            return

        async with self._update_lock:
            self.state.accumulated_text += chunk

            current_time = asyncio.get_event_loop().time()
            time_since_update = current_time - self.state.last_update_time
            text_length = len(self.state.accumulated_text)

            # Decide whether to send an update
            should_update = (
                time_since_update >= self.config.max_update_interval or
                (time_since_update >= self.config.min_update_interval and
                 text_length >= self.config.min_chunk_size)
            )

            if should_update:
                await self._send_draft_update()
                self.state.last_update_time = current_time

    async def _send_draft_update(self) -> None:
        """Send a draft update to the chat."""
        display_text = (
            self.config.streaming_prefix +
            self.state.accumulated_text +
            self.config.streaming_suffix
        )

        if self.state.draft_supported:
            try:
                await self._send_message_draft(display_text)
                return
            except BadRequest as e:
                if "TOPIC_CLOSED" in str(e) or "MESSAGE_THREAD" in str(e):
                    logger.info("Draft not supported for this chat, falling back to edit")
                    self.state.draft_supported = False
                else:
                    raise
            except TelegramError as e:
                logger.warning(f"send_message_draft failed: {e}")
                if self.config.fallback_to_edit:
                    self.state.draft_supported = False
                else:
                    raise

        # Fallback: use edit_message_text
        if self.config.fallback_to_edit:
            await self._edit_placeholder_message(display_text)

    async def _send_message_draft(self, text: str) -> None:
        """
        Send a message draft using Bot API 9.3+ method.

        Note: This requires the bot to have Threaded Mode enabled via @BotFather
        and the chat to be in topic mode.
        """
        # The send_message_draft method should be available in python-telegram-bot
        # after the API 9.3 support was merged (PR #5078)
        try:
            await self.bot.send_message_draft(
                chat_id=self.chat_id,
                message_thread_id=self.message_thread_id,
                text=text,
            )
        except AttributeError:
            # Method not available - library version doesn't support it yet
            logger.warning(
                "send_message_draft not available. "
                "Update python-telegram-bot to a version with Bot API 9.3 support."
            )
            self.state.draft_supported = False
            if self.config.fallback_to_edit:
                await self._edit_placeholder_message(text)
            else:
                raise NotImplementedError(
                    "send_message_draft requires python-telegram-bot with Bot API 9.3 support"
                )

    async def _edit_placeholder_message(self, text: str) -> None:
        """Edit the placeholder message with updated text (fallback method)."""
        try:
            if self.state.placeholder_message is None:
                # Send initial placeholder
                self.state.placeholder_message = await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    message_thread_id=self.message_thread_id,
                    reply_to_message_id=self.reply_to_message_id,
                )
            else:
                # Edit existing message
                await self.state.placeholder_message.edit_text(text)
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                logger.warning(f"Failed to edit placeholder: {e}")
        except TelegramError as e:
            logger.warning(f"Failed to update placeholder message: {e}")

    async def finish(self) -> Optional[Message]:
        """
        Complete the streaming session and send the final message.

        Returns:
            The final Message object, or None if sending failed
        """
        if self.state.is_complete:
            return self.state.final_message

        self.state.is_complete = True
        final_text = self.state.accumulated_text

        if not final_text.strip():
            final_text = "(No response generated)"

        try:
            if self.state.placeholder_message is not None:
                # We were using edit fallback - do final edit
                self.state.final_message = await self.state.placeholder_message.edit_text(
                    final_text,
                    parse_mode=self.config.parse_mode,
                )
            else:
                # Send the final message normally
                self.state.final_message = await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=final_text,
                    message_thread_id=self.message_thread_id,
                    reply_to_message_id=self.reply_to_message_id,
                    parse_mode=self.config.parse_mode,
                )

            return self.state.final_message

        except TelegramError as e:
            logger.error(f"Failed to send final message: {e}")
            self.state.error = str(e)
            return None

    async def _send_error_message(self, error: str) -> None:
        """Send an error message when streaming fails."""
        self.state.is_complete = True
        self.state.error = error

        error_text = f"❌ Error: {error}"

        try:
            if self.state.placeholder_message is not None:
                await self.state.placeholder_message.edit_text(error_text)
            else:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=error_text,
                    message_thread_id=self.message_thread_id,
                )
        except TelegramError as e:
            logger.error(f"Failed to send error message: {e}")

    @property
    def text(self) -> str:
        """Get the accumulated text so far."""
        return self.state.accumulated_text


async def stream_llm_response(
    bot: Bot,
    chat_id: int,
    stream: AsyncIterator[str],
    message_thread_id: Optional[int] = None,
    reply_to_message_id: Optional[int] = None,
    config: Optional[StreamConfig] = None,
) -> Optional[Message]:
    """
    Convenience function to stream an LLM response to a Telegram chat.

    Args:
        bot: The Telegram bot instance
        chat_id: Target chat ID
        stream: Async iterator yielding text chunks
        message_thread_id: Topic thread ID (required for draft mode)
        reply_to_message_id: Message to reply to
        config: Streaming configuration

    Returns:
        The final Message object, or None if failed

    Example:
        from pydantic_ai import Agent

        agent = Agent('openai:gpt-4')

        async with agent.run_stream("Hello!") as result:
            message = await stream_llm_response(
                bot=context.bot,
                chat_id=update.effective_chat.id,
                stream=result.stream_text(),
                message_thread_id=update.message.message_thread_id,
            )
    """
    streaming = StreamingResponse(
        bot=bot,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        reply_to_message_id=reply_to_message_id,
        config=config,
    )

    async with streaming:
        async for chunk in stream:
            await streaming.update(chunk)

    return streaming.state.final_message


def get_message_thread_id(update: Update) -> Optional[int]:
    """
    Extract the message_thread_id from an update.

    This handles both forum topics and private chat topics (Threaded Mode).

    Args:
        update: Telegram Update object

    Returns:
        The message_thread_id if available, None otherwise
    """
    message = update.effective_message
    if message is None:
        return None

    # Check for message_thread_id (forum topics and private topics)
    if hasattr(message, 'message_thread_id') and message.message_thread_id:
        return message.message_thread_id

    return None


def is_topic_chat(update: Update) -> bool:
    """
    Check if the current chat supports topic mode.

    Args:
        update: Telegram Update object

    Returns:
        True if the chat is in topic mode
    """
    message = update.effective_message
    if message is None:
        return False

    # Check is_topic_message flag
    if hasattr(message, 'is_topic_message') and message.is_topic_message:
        return True

    # Check if message has a thread ID
    if hasattr(message, 'message_thread_id') and message.message_thread_id:
        return True

    return False
