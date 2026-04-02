"""Telegram streaming via buffered message edits."""

import asyncio
import logging
import re
import time

from telegram import Bot
from telegram.error import BadRequest, RetryAfter, TimedOut

from condor.acp import (
    ACPEvent,
    Heartbeat,
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

# Patterns for standard Markdown → Telegram Markdown v1 conversion
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$", re.MULTILINE)
_HR_RE = re.compile(r"^-{3,}$", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\|[-| :]+\|$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$", re.MULTILINE)


def _convert_table_row(m: re.Match) -> str:
    """Convert a Markdown table row to a clean aligned line."""
    cells = [c.strip() for c in m.group(1).split("|")]
    return "  ".join(cells)


def _to_telegram_markdown(text: str) -> str:
    """Convert standard Markdown to Telegram Markdown v1.

    Telegram Markdown v1 only supports:
      *bold*  _italic_  `code`  ```code blocks```  [text](url)

    This converts unsupported elements (headers, tables, blockquotes, HRs)
    into clean plain text, and adapts **bold**/`*italic*` syntax.
    """
    # Protect code blocks and inline code from transformation
    protected: list[str] = []

    def _protect(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    result = re.sub(r"```[\s\S]*?```|`[^`\n]+`", _protect, text)

    # --- Convert unsupported Markdown elements ---

    # Headers → *bold text*
    result = _HEADER_RE.sub(r"*\1*", result)

    # Blockquotes → plain text
    result = _BLOCKQUOTE_RE.sub(r"\1", result)

    # Horizontal rules → empty line
    result = _HR_RE.sub("", result)

    # Tables: remove separator rows, convert data rows to spaced text
    result = _TABLE_SEP_RE.sub("", result)
    result = _TABLE_ROW_RE.sub(_convert_table_row, result)

    # --- Convert formatting ---
    result = _BOLD_RE.sub(r"*\1*", result)
    result = _ITALIC_RE.sub(r"_\1_", result)

    # Clean up excessive blank lines (including lines with only whitespace)
    result = re.sub(r"(\n[ \t]*){3,}", "\n\n", result)
    # Strip leading blank lines
    result = result.lstrip("\n")

    # Restore protected code spans
    for i, original in enumerate(protected):
        result = result.replace(f"\x00{i}\x00", original)

    return result


class TelegramStreamer:
    """Consumes ACPEvents and progressively edits a Telegram message."""

    def __init__(self, bot: Bot, chat_id: int, initial_message_id: int, prefix: str = ""):
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = initial_message_id
        self._prefix = prefix
        self._buffer = ""
        self._active_tools: dict[str, str] = {}     # tool_call_id -> "title..."
        self._tool_start_times: dict[str, float] = {}  # tool_call_id -> monotonic time
        self._finished_tools: list[str] = []         # display lines for done tools
        self._needs_edit = False
        self._edit_task: asyncio.Task | None = None
        self._done = False
        self._stop_reason: str | None = None
        self._tick = 0
        self._continuation_ids: list[int] = []

    @staticmethod
    def _format_tool_title(title: str) -> str:
        """Format tool titles for readable Telegram display.

        MCP tool names (mcp__server__tool_name) → 'server: tool name'
        Underscores replaced with spaces to avoid Telegram Markdown issues.
        """
        if title.startswith("mcp__"):
            parts = title.split("__", 2)
            if len(parts) == 3:
                server = parts[1]
                tool = parts[2].replace("_", " ")
                return f"{server}: {tool}"
        return title.replace("_", " ")

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """Format elapsed seconds as a compact string."""
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        return f"{m}m{s:02d}s"

    async def process_event(self, event: ACPEvent) -> None:
        """Process a single ACP event."""
        if isinstance(event, TextChunk):
            self._buffer += event.text
            self._needs_edit = True
        elif isinstance(event, ThoughtChunk):
            pass
        elif isinstance(event, ToolCallEvent):
            self._active_tools[event.tool_call_id] = self._format_tool_title(event.title)
            self._tool_start_times[event.tool_call_id] = time.monotonic()
            self._needs_edit = True
        elif isinstance(event, ToolCallUpdate):
            tc_id = event.tool_call_id
            if event.status in ("completed", "failed"):
                title = self._active_tools.pop(tc_id, self._format_tool_title(event.title or "tool"))
                icon = TOOL_DONE if event.status == "completed" else TOOL_FAILED
                elapsed = ""
                start = self._tool_start_times.pop(tc_id, None)
                if start is not None:
                    elapsed = f" ({self._format_elapsed(time.monotonic() - start)})"
                self._finished_tools.append(f"{icon} {title}{elapsed}")
                self._needs_edit = True
            elif event.title and tc_id in self._active_tools:
                self._active_tools[tc_id] = self._format_tool_title(event.title)
                self._needs_edit = True
        elif isinstance(event, Heartbeat):
            self._needs_edit = True
        elif isinstance(event, PromptDone):
            self._stop_reason = event.stop_reason
            self._done = True

    def start_edit_loop(self) -> asyncio.Task:
        """Start background task that flushes edits periodically."""
        self._edit_task = asyncio.create_task(self._edit_loop())
        return self._edit_task

    async def _edit_loop(self) -> None:
        try:
            while not self._done:
                self._tick += 1
                # Force re-render every ~5s when tools are active (to update elapsed timers)
                force_tool_update = self._active_tools and self._tick % 10 == 0
                # Always edit while thinking (for animation), or when content changed
                if self._needs_edit or not self._buffer or force_tool_update:
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
        now = time.monotonic()
        lines = list(self._finished_tools)
        for tc_id, title in self._active_tools.items():
            start = self._tool_start_times.get(tc_id)
            elapsed = f" ({self._format_elapsed(now - start)})" if start else ""
            lines.append(f"{TOOL_RUNNING} {title}...{elapsed}")
        return "\n".join(lines)

    @staticmethod
    def _buffer_looks_stable(buf: str) -> bool:
        """Check if the buffer ends at a natural boundary for Markdown rendering.

        During streaming, incomplete Markdown (e.g. a lone `**`) causes Telegram
        parse errors → fallback to plain text → next edit succeeds → flicker.
        Only apply Markdown when the buffer looks "complete".
        """
        stripped = buf.rstrip()
        if not stripped:
            return False
        # Natural sentence/paragraph boundaries
        if stripped[-1] in '.!?:\n)"]':
            return True
        # End of a code block
        if stripped.endswith("```"):
            return True
        # End of a list item line
        if re.search(r"\n[-*\d]\.\s.+$", stripped):
            return True
        return False

    async def _flush_edit(self, final: bool = False) -> None:
        """Edit the message with current buffer content."""
        self._needs_edit = False

        parts: list[str] = []
        parse_mode: str | None = None

        if self._prefix:
            parts.append(self._prefix)

        # Tool block (always at top, never Markdown-converted —
        # _format_tool_title already strips underscores)
        tool_block = self._build_tool_block()
        if tool_block:
            parts.append(tool_block)

        # Main text content — strip leading/trailing whitespace to avoid gaps
        buf = self._buffer.strip()
        if buf:
            # On final flush or when buffer looks stable → apply Markdown
            if final or self._buffer_looks_stable(buf):
                converted = _to_telegram_markdown(buf)
                parse_mode = "Markdown"
            else:
                converted = buf
            parts.append(converted)
        elif not final:
            # Thinking animation
            frame = _THINKING_FRAMES[self._tick % len(_THINKING_FRAMES)]
            parts.append(frame)
        elif self._finished_tools:
            # Tools ran but no text response
            reason = f" [{self._stop_reason}]" if self._stop_reason else ""
            parts.append(f"_(done — no additional response{reason})_")
            parse_mode = "Markdown"
        else:
            reason = f" (stop reason: {self._stop_reason})" if self._stop_reason else ""
            parts.append(f"_(no response{reason})_")
            log.warning(
                "Empty agent response for chat %s — stop_reason=%s, "
                "tools_ran=%d, buffer_len=%d",
                self._chat_id,
                self._stop_reason,
                len(self._finished_tools),
                len(self._buffer),
            )

        text = "\n\n".join(parts)

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
