"""Telegram streaming via buffered message edits."""

import asyncio
import logging
import re

from telegram import Bot
from telegram.error import BadRequest, RetryAfter, TimedOut

from condor.acp import (
    ACPEvent,
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
)

log = logging.getLogger(__name__)

EDIT_INTERVAL = 0.5  # seconds between message edits
MAX_MESSAGE_LEN = 4096

# Tool status icons
TOOL_RUNNING = "\u2699\ufe0f"   # gear
TOOL_DONE = "\u2705"            # checkmark
TOOL_FAILED = "\u274c"          # X

# Thinking animation frames
_THINKING_FRAMES = ["Thinking.", "Thinking..", "Thinking..."]

# Convert standard Markdown **bold** / *italic* to Telegram Markdown v1 *bold* / _italic_
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")


def _to_telegram_markdown(text: str) -> str:
    """Convert standard Markdown to Telegram Markdown v1.

    Telegram Markdown v1 uses:  *bold*  _italic_  `code`  ```code blocks```  [text](url)
    Standard Markdown uses:     **bold**  *italic*  `code`  ```code blocks```  [text](url)

    This converts **bold** → *bold* and *italic* → _italic_ while leaving
    code spans, code blocks, and links untouched.
    """
    # Protect code blocks and inline code from transformation
    protected: list[str] = []

    def _protect(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    result = re.sub(r"```[\s\S]*?```|`[^`\n]+`", _protect, text)

    # Convert formatting in non-code text
    result = _BOLD_RE.sub(r"*\1*", result)
    result = _ITALIC_RE.sub(r"_\1_", result)

    # Restore protected code spans
    for i, original in enumerate(protected):
        result = result.replace(f"\x00{i}\x00", original)

    return result


class TelegramStreamer:
    """Consumes ACPEvents and progressively edits a Telegram message."""

    def __init__(self, bot: Bot, chat_id: int, initial_message_id: int):
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = initial_message_id
        self._buffer = ""
        self._active_tools: dict[str, str] = {}     # tool_call_id -> "title..."
        self._finished_tools: list[str] = []         # display lines for done tools
        self._needs_edit = False
        self._edit_task: asyncio.Task | None = None
        self._done = False
        self._tick = 0
        self._continuation_ids: list[int] = []

    async def process_event(self, event: ACPEvent) -> None:
        """Process a single ACP event."""
        if isinstance(event, TextChunk):
            self._buffer += event.text
            self._needs_edit = True
        elif isinstance(event, ThoughtChunk):
            pass
        elif isinstance(event, ToolCallEvent):
            self._active_tools[event.tool_call_id] = event.title
            self._needs_edit = True
        elif isinstance(event, ToolCallUpdate):
            tc_id = event.tool_call_id
            if event.status in ("completed", "failed"):
                title = self._active_tools.pop(tc_id, event.title or "tool")
                icon = TOOL_DONE if event.status == "completed" else TOOL_FAILED
                self._finished_tools.append(f"{icon} {title}")
                self._needs_edit = True
            elif event.title and tc_id in self._active_tools:
                self._active_tools[tc_id] = event.title
                self._needs_edit = True
        elif isinstance(event, PromptDone):
            self._done = True

    def start_edit_loop(self) -> asyncio.Task:
        """Start background task that flushes edits periodically."""
        self._edit_task = asyncio.create_task(self._edit_loop())
        return self._edit_task

    async def _edit_loop(self) -> None:
        try:
            while not self._done:
                self._tick += 1
                # Always edit while thinking (for animation), or when content changed
                if self._needs_edit or not self._buffer:
                    await self._flush_edit()
                await asyncio.sleep(EDIT_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def finalize(self) -> None:
        """Final edit with complete response."""
        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()
            try:
                await self._edit_task
            except asyncio.CancelledError:
                pass

        # Move any remaining active tools to finished
        for tc_id, title in self._active_tools.items():
            self._finished_tools.append(f"{TOOL_DONE} {title}")
        self._active_tools.clear()

        await self._flush_edit(final=True)

    def _build_tool_block(self) -> str:
        """Build the tool calls display block."""
        lines = list(self._finished_tools)
        for title in self._active_tools.values():
            lines.append(f"{TOOL_RUNNING} {title}...")
        return "\n".join(lines)

    async def _flush_edit(self, final: bool = False) -> None:
        """Edit the message with current buffer content."""
        self._needs_edit = False

        parts = []

        # Tool block (always at top)
        tool_block = self._build_tool_block()
        if tool_block:
            parts.append(tool_block)

        # Main text content
        if self._buffer:
            parts.append(self._buffer)
        elif not final:
            # Thinking animation
            frame = _THINKING_FRAMES[self._tick % len(_THINKING_FRAMES)]
            parts.append(frame)
        elif self._finished_tools:
            # Tools ran but no text response
            parts.append("_(done — no additional response)_")
        else:
            parts.append("_(no response)_")

        raw_text = "\n\n".join(parts)

        # Convert to Telegram Markdown when we have actual content
        if self._buffer:
            text = _to_telegram_markdown(raw_text)
            parse_mode = "Markdown"
        else:
            text = raw_text
            parse_mode = None

        if len(text) > MAX_MESSAGE_LEN:
            await self._handle_overflow(text, final, parse_mode)
            return

        await self._safe_edit(self._message_id, text, parse_mode=parse_mode)

    async def _handle_overflow(
        self, text: str, final: bool, parse_mode: str | None = None
    ) -> None:
        """Split long messages across multiple Telegram messages."""
        chunks = self._split_text(text, MAX_MESSAGE_LEN)
        await self._safe_edit(self._message_id, chunks[0], parse_mode=parse_mode)

        for i, chunk in enumerate(chunks[1:]):
            if i < len(self._continuation_ids):
                await self._safe_edit(
                    self._continuation_ids[i], chunk, parse_mode=parse_mode
                )
            else:
                msg_id = await self._safe_send(chunk, parse_mode=parse_mode)
                if msg_id:
                    self._continuation_ids.append(msg_id)

    @staticmethod
    def _split_text(text: str, max_len: int) -> list[str]:
        """Split text at paragraph boundaries."""
        chunks = []
        while len(text) > max_len:
            split_at = text.rfind("\n\n", 0, max_len)
            if split_at == -1:
                split_at = text.rfind("\n", 0, max_len)
            if split_at == -1:
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        if text:
            chunks.append(text)
        return chunks

    async def _safe_edit(
        self, message_id: int, text: str, parse_mode: str | None = None
    ) -> None:
        """Edit a message with retry-after handling."""
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
            )
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                if parse_mode:
                    # Markdown parse failed — fall back to plain text
                    await self._safe_edit(message_id, text, parse_mode=None)
                else:
                    log.warning("Failed to edit message: %s", e)
        except RetryAfter as e:
            log.warning("Rate limited, waiting %s seconds", e.retry_after)
            await asyncio.sleep(e.retry_after)
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=parse_mode,
                )
            except Exception:
                pass
        except TimedOut:
            pass
        except Exception:
            log.exception("Unexpected error editing message")

    async def _safe_send(self, text: str, parse_mode: str | None = None) -> int | None:
        """Send a new message, return message_id."""
        try:
            msg = await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            return msg.message_id
        except BadRequest:
            if parse_mode:
                return await self._safe_send(text, parse_mode=None)
            return None
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                msg = await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode=parse_mode,
                )
                return msg.message_id
            except Exception:
                return None
        except Exception:
            log.exception("Failed to send continuation message")
            return None
