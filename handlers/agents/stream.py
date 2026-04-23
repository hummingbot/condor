"""Telegram streaming via edit_message_text on a placeholder message."""

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

EDIT_INTERVAL = 0.5
MAX_MESSAGE_LEN = 4096

TOOL_RUNNING = "\u2699\ufe0f"
TOOL_DONE = "\u2705"
TOOL_FAILED = "\u274c"

_THINKING_FRAMES = ["Thinking.", "Thinking..", "Thinking..."]

# Markdown conversion patterns
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$", re.MULTILINE)
_HR_RE = re.compile(r"^-{3,}$", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\|[-| :]+\|$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$", re.MULTILINE)


def _convert_table_row(m: re.Match) -> str:
    cells = [c.strip() for c in m.group(1).split("|")]
    return "  ".join(cells)


def _to_telegram_markdown(text: str) -> str:
    """Convert standard Markdown to Telegram Markdown v1."""
    protected: list[str] = []

    def _protect(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    result = re.sub(r"```[\s\S]*?```|`[^`\n]+`", _protect, text)

    result = _HEADER_RE.sub(r"*\1*", result)
    result = _BLOCKQUOTE_RE.sub(r"\1", result)
    result = _HR_RE.sub("", result)
    result = _TABLE_SEP_RE.sub("", result)
    result = _TABLE_ROW_RE.sub(_convert_table_row, result)
    result = _BOLD_RE.sub(r"*\1*", result)
    result = _ITALIC_RE.sub(r"_\1_", result)

    result = re.sub(r"(\n[ \t]*){3,}", "\n\n", result)
    result = result.lstrip("\n")

    for i, original in enumerate(protected):
        result = result.replace(f"\x00{i}\x00", original)

    return result


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


class TelegramStreamer:
    """Streams ACPEvents by editing a placeholder Telegram message."""

    def __init__(self, bot: Bot, chat_id: int, message_id: int, prefix: str = ""):
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = message_id
        self._prefix = prefix
        self._buffer = ""
        self._active_tools: dict[str, str] = {}
        self._tool_start_times: dict[str, float] = {}
        self._finished_tools: list[str] = []
        self._needs_edit = False
        self._edit_task: asyncio.Task | None = None
        self._done = False
        self._stop_reason: str | None = None
        self._tick = 0
        self._continuation_ids: list[int] = []

    # --- Event processing ---

    async def process_event(self, event: ACPEvent) -> None:
        if isinstance(event, TextChunk):
            self._buffer += event.text
            self._needs_edit = True
        elif isinstance(event, ToolCallEvent):
            self._active_tools[event.tool_call_id] = self._format_tool_title(event.title)
            self._tool_start_times[event.tool_call_id] = time.monotonic()
            self._needs_edit = True
        elif isinstance(event, ToolCallUpdate):
            self._handle_tool_update(event)
        elif isinstance(event, Heartbeat):
            self._needs_edit = True
        elif isinstance(event, PromptDone):
            self._stop_reason = event.stop_reason
            self._done = True

    def _handle_tool_update(self, event: ToolCallUpdate) -> None:
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

    # --- Edit loop ---

    def start_edit_loop(self) -> asyncio.Task:
        self._edit_task = asyncio.create_task(self._edit_loop())
        return self._edit_task

    async def _edit_loop(self) -> None:
        try:
            while not self._done:
                self._tick += 1
                force = self._active_tools and self._tick % 10 == 0
                if self._needs_edit or not self._buffer or force:
                    await self._flush(final=False)
                await asyncio.sleep(EDIT_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def finalize(self) -> None:
        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()
            try:
                await self._edit_task
            except asyncio.CancelledError:
                pass

        for tc_id, title in self._active_tools.items():
            self._finished_tools.append(f"{TOOL_DONE} {title}")
        self._active_tools.clear()

        await self._flush(final=True)

    # --- Build & flush ---

    def _build_text(self, final: bool) -> tuple[str, str | None]:
        parts: list[str] = []
        parse_mode = None

        if self._prefix:
            parts.append(self._prefix)

        tool_block = self._build_tool_block()
        if tool_block:
            parts.append(tool_block)

        buf = self._buffer.strip()
        if buf:
            if final:
                parts.append(_to_telegram_markdown(buf))
                parse_mode = "Markdown"
            else:
                parts.append(buf)
        elif not final:
            parts.append(_THINKING_FRAMES[self._tick % len(_THINKING_FRAMES)])
        elif self._finished_tools:
            parts.append("_(done)_")
            parse_mode = "Markdown"
        else:
            parts.append("_(no response)_")

        return "\n\n".join(parts), parse_mode

    def _build_tool_block(self) -> str:
        now = time.monotonic()
        lines = list(self._finished_tools)
        for tc_id, title in self._active_tools.items():
            start = self._tool_start_times.get(tc_id)
            elapsed = f" ({self._format_elapsed(now - start)})" if start else ""
            lines.append(f"{TOOL_RUNNING} {title}...{elapsed}")
        return "\n".join(lines)

    async def _flush(self, final: bool) -> None:
        self._needs_edit = False
        text, parse_mode = self._build_text(final)
        chunks = _split_text(text, MAX_MESSAGE_LEN)

        # Edit the main placeholder message
        await self._edit(self._message_id, chunks[0], parse_mode)

        # Handle overflow chunks
        for i, chunk in enumerate(chunks[1:]):
            if i < len(self._continuation_ids):
                await self._edit(self._continuation_ids[i], chunk, parse_mode)
            else:
                msg_id = await self._send(chunk, parse_mode)
                if msg_id:
                    self._continuation_ids.append(msg_id)

    # --- Telegram I/O ---

    async def _edit(self, message_id: int, text: str, parse_mode: str | None = None) -> None:
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
                    await self._edit(message_id, text, parse_mode=None)
                else:
                    log.warning("Failed to edit message: %s", e)
        except RetryAfter as e:
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

    async def _send(self, text: str, parse_mode: str | None = None) -> int | None:
        try:
            msg = await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            return msg.message_id
        except BadRequest:
            if parse_mode:
                return await self._send(text, parse_mode=None)
            return None
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                msg = await self._bot.send_message(
                    chat_id=self._chat_id, text=text,
                )
                return msg.message_id
            except Exception:
                return None
        except Exception:
            log.exception("Failed to send message")
            return None

    # --- Helpers ---

    @staticmethod
    def _format_tool_title(title: str) -> str:
        if title.startswith("mcp__"):
            parts = title.split("__", 2)
            if len(parts) == 3:
                return f"{parts[1]}: {parts[2].replace('_', ' ')}"
        return title.replace("_", " ")

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        return f"{m}m{s:02d}s"
