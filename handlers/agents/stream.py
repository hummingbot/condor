"""Telegram streaming via edit_message_text on a placeholder message."""

import asyncio
import logging
import re
import time

from telegram import Bot, InlineKeyboardMarkup
from telegram.error import BadRequest, RetryAfter, TimedOut

from condor.runtime.events import EventType, RuntimeEvent
from utils.telegram_formatters import escape_markdown_v2

from .menu import stop_generating_keyboard

log = logging.getLogger(__name__)

EDIT_INTERVAL = 0.5
MAX_MESSAGE_LEN = 4096

TOOL_RUNNING = "\u2699\ufe0f"
TOOL_DONE = "\u2705"
TOOL_FAILED = "\u274c"
REASONING = "\U0001f4ad"  # \ud83d\udcad

_THINKING_FRAMES = ["Thinking.", "Thinking..", "Thinking..."]
_DOT_FRAMES = ["", ".", "..", "..."]

# Shown instead of the Thinking pulse while the turn waits its place in the
# session queue. Static on purpose — see _edit_loop.
QUEUED_LABEL = (
    "⏳ Queued — waiting for the current answer to finish.\n/stop interrupts it."
)
STOPPED_LABEL = r"⏹ _Stopped\._"

# How much of the live reasoning to keep on screen (tail chars). Keeps the
# streamed message small while still showing the agent's current train of thought.
MAX_REASONING_LEN = 600

# Markdown conversion patterns
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$", re.MULTILINE)
_HR_RE = re.compile(r"^-{3,}$", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\|[-| :]+\|$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$", re.MULTILINE)


def _convert_table_row(m: re.Match) -> str:
    cells = [c.strip() for c in m.group(1).split("|")]
    return "  ".join(cells)


_LINK_RE = re.compile(r"\[[^\]\n]+\]\(https?://[^)\s]+\)")
_ENTITY_RE = re.compile(r"\*[^*\n]+\*|_[^_\n]+_")
_PLACEHOLDER_RE = re.compile(r"\x00\d+\x00")
_BOLD_ITALIC_RE = re.compile(
    r"\*\*(.+?)\*\*"  # bold
    r"|(?<!\*)\*([^*\n]+?)\*(?!\*)"  # single-star italic
    r"|(?<![_*])_([^_\n]+?)_(?![_*])"  # underscore italic
)


def _to_telegram_markdown(text: str) -> str:
    """Convert standard Markdown to Telegram MarkdownV2.

    Links, code and the converted entities are kept; the text between them is
    escaped so the message parses. An unescaped special char makes Telegram
    reject the whole message, and the caller then falls back to plain text,
    which shows raw markdown (e.g. ``[label](url)`` instead of a link).
    """
    protected: list[str] = []

    def _protect(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    def _conv(m: re.Match) -> str:
        inner = next(g for g in m.groups() if g is not None)
        # A link inside bold/italic: keep the link, drop the markers —
        # Telegram MarkdownV2 does not nest entities.
        if _PLACEHOLDER_RE.fullmatch(inner):
            return inner
        marker = "*" if m.group(1) is not None else "_"
        return f"{marker}{escape_markdown_v2(inner)}{marker}"

    # 1. Atomic entities that must survive verbatim: code and links.
    result = re.sub(r"```[\s\S]*?```|`[^`\n]+`|" + _LINK_RE.pattern, _protect, text)

    # 2. Convert markdown, escaping the inner content of each entity.
    result = _HEADER_RE.sub(_conv, result)
    result = _BLOCKQUOTE_RE.sub(r"\1", result)
    result = _HR_RE.sub("", result)
    result = _TABLE_SEP_RE.sub("", result)
    result = _TABLE_ROW_RE.sub(_convert_table_row, result)
    result = _BOLD_ITALIC_RE.sub(_conv, result)

    # 3. Protect the entities just produced, then escape the gaps.
    result = _ENTITY_RE.sub(_protect, result)
    result = escape_markdown_v2(result)

    # 4. Collapse blank lines and restore protected entities (inner first).
    result = re.sub(r"(\n[ \t]*){3,}", "\n\n", result)
    result = result.lstrip("\n")
    for i in range(len(protected) - 1, -1, -1):
        result = result.replace(f"\x00{i}\x00", protected[i])

    return result


def _mdv2_to_plain(text: str) -> str:
    """Turn escaped MarkdownV2 back into readable text.

    A parse rejection is retried without parse_mode. Reusing the escaped text
    there shows every backslash to the user, so unescape the punctuation and
    drop the entity markers the converter added.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if c not in "*_~`":
            out.append(c)
        i += 1
    return "".join(out)


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
    """Streams RuntimeEvents by editing a placeholder Telegram message."""

    def __init__(self, bot: Bot, chat_id: int, message_id: int, prefix: str = ""):
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = message_id
        self._prefix = prefix
        self._buffer = ""
        self._thoughts = ""
        self._active_tools: dict[str, str] = {}
        self._tool_start_times: dict[str, float] = {}
        self._finished_tools: list[str] = []
        self._needs_edit = False
        self._edit_task: asyncio.Task | None = None
        self._done = False
        self._queued = False
        self._stop_reason: str | None = None
        self._tick = 0
        self._continuation_ids: list[int] = []
        # message_id -> (text, parse_mode, reply_markup) last known to be on
        # screen. Guards against re-sending an edit Telegram would reject as
        # "not modified". The markup belongs in the key: the final flush of a
        # long answer often changes nothing but the button's absence.
        self._last_sent: dict[int, tuple[str, str | None, object]] = {}
        # Built once so every flush passes the same object — cheap, and the
        # dedupe compare above short-circuits on identity.
        self._stop_markup = stop_generating_keyboard()

    # --- Event processing ---

    async def process_event(self, event: RuntimeEvent) -> None:
        # Emitted before the turn blocks on the session lock, so the placeholder
        # can say "waiting its turn" instead of pulsing "Thinking" at a user
        # whose message has not reached the agent yet.
        if event.type == EventType.QUEUED:
            self._queued = True
            self._needs_edit = True
            return
        # Anything else means the lock was won: this turn is live now.
        self._queued = False

        if event.type == EventType.TEXT:
            self._buffer += event.text
            self._needs_edit = True
        elif event.type == EventType.THOUGHT:
            self._thoughts += event.text
            self._needs_edit = True
        elif event.type == EventType.TOOL_CALL:
            tc_id = event.field("tool_call_id")
            self._active_tools[tc_id] = self._format_tool_title(
                event.field("title") or "tool"
            )
            self._tool_start_times[tc_id] = time.monotonic()
            self._needs_edit = True
        elif event.type == EventType.TOOL_UPDATE:
            self._handle_tool_update(event)
        elif event.type == EventType.HEARTBEAT:
            self._needs_edit = True
        elif event.type == EventType.ERROR:
            # Surface the failure in the message body rather than silently
            # ending the turn; DONE always follows, which stops the loop.
            self._buffer += f"\n\n{event.field('message', 'Stream error')}"
            self._needs_edit = True
        elif event.type == EventType.DONE:
            self._stop_reason = event.stop_reason
            self._done = True

    def _handle_tool_update(self, event: RuntimeEvent) -> None:
        tc_id = event.field("tool_call_id")
        status = event.field("status")
        title = event.field("title")
        if status in ("completed", "failed"):
            label = self._active_tools.pop(
                tc_id, self._format_tool_title(title or "tool")
            )
            icon = TOOL_DONE if status == "completed" else TOOL_FAILED
            elapsed = ""
            start = self._tool_start_times.pop(tc_id, None)
            if start is not None:
                elapsed = f" ({self._format_elapsed(time.monotonic() - start)})"
            self._finished_tools.append(f"{icon} {label}{elapsed}")
            self._needs_edit = True
        elif title and tc_id in self._active_tools:
            self._active_tools[tc_id] = self._format_tool_title(title)
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
                if self._queued:
                    # A queued turn has nothing to animate, and several can be
                    # stacked behind one answer: flushing on change only keeps
                    # N idle placeholders from each editing twice a second.
                    if self._needs_edit:
                        await self._flush(final=False)
                elif self._needs_edit or not self._buffer or force:
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
            parts.append(escape_markdown_v2(self._prefix) if final else self._prefix)

        buf = self._buffer.strip()
        stopped = final and self._stop_reason == "cancelled"

        # While streaming, before the answer starts, surface the live reasoning
        # so the user can tell the agent is thinking (and about what) rather than
        # stalled. Dropped from the final message — the answer stands on its own.
        if not final and not buf:
            reasoning = self._build_reasoning_block()
            if reasoning:
                parts.append(reasoning)

        tool_block = self._build_tool_block()
        if tool_block:
            # The tool block is plain text (icons, titles, elapsed) and rides
            # along in the final MarkdownV2 message, so its parens and dashes
            # must be escaped or the whole message fails to parse and falls
            # back to plain text, showing raw markdown.
            parts.append(escape_markdown_v2(tool_block) if final else tool_block)

        if buf:
            if final:
                parts.append(_to_telegram_markdown(buf))
                parse_mode = "MarkdownV2"
            else:
                parts.append(buf)
        elif not final:
            # Only fall back to the bare "Thinking…" pulse when nothing else is
            # on screen yet (no reasoning, no active or finished tools).
            base = 1 if self._prefix else 0
            if len(parts) == base:
                parts.append(
                    QUEUED_LABEL
                    if self._queued
                    else _THINKING_FRAMES[self._tick % len(_THINKING_FRAMES)]
                )
        elif stopped:
            parts.append(r"_\(stopped before answering\)_")
            parse_mode = "MarkdownV2"
        elif self._finished_tools:
            parts.append(r"_\(done\)_")
            parse_mode = "MarkdownV2"
        else:
            parts.append(r"_\(no response\)_")

        # Say why the answer ends where it does, so a /stop that lands mid
        # sentence does not read as the agent giving up on its own.
        if stopped and buf:
            parts.append(STOPPED_LABEL)
            parse_mode = "MarkdownV2"

        return "\n\n".join(parts), parse_mode

    def _build_reasoning_block(self) -> str:
        """Live 💭 reasoning block: animated header + a tail of the thoughts.

        Only the trailing slice is kept so the block reads as the agent's
        *current* train of thought (and stays small) rather than a growing wall
        of text. Rendered as a blockquote in plain text (no parse mode while
        streaming), so raw ``> `` prefixes are fine.
        """
        text = self._thoughts.strip()
        if not text:
            return ""
        if len(text) > MAX_REASONING_LEN:
            text = "…" + text[-MAX_REASONING_LEN:]
        quoted = "\n".join(f"> {ln}" for ln in text.splitlines() if ln.strip())
        dots = _DOT_FRAMES[self._tick % len(_DOT_FRAMES)]
        return f"{REASONING} Reasoning{dots}\n{quoted}"

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

        # Only the placeholder carries the Stop button, and only while the turn
        # is still open: a queued turn counts, since stopping the answer ahead
        # is exactly what its notice tells the user /stop does. `final` passes
        # None, which is how editMessageText drops an inline keyboard.
        markup = None if final else self._stop_markup

        # Edit the main placeholder message
        await self._edit(self._message_id, chunks[0], parse_mode, markup)

        # Handle overflow chunks
        for i, chunk in enumerate(chunks[1:]):
            if i < len(self._continuation_ids):
                await self._edit(self._continuation_ids[i], chunk, parse_mode)
            else:
                msg_id = await self._send(chunk, parse_mode)
                if msg_id:
                    self._continuation_ids.append(msg_id)

    # --- Telegram I/O ---

    async def _edit(
        self,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        # A long answer splits at a paragraph boundary that stays put as the
        # buffer grows, so every chunk but the last is byte-identical on each
        # tick. Those edits only ever come back "not modified" — and each one
        # spends per-chat quota that the chunk which *did* change then waits
        # for. Skip what is already on screen.
        if self._last_sent.get(message_id) == (text, parse_mode, reply_markup):
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                if parse_mode:
                    # Drop the entry rather than let the cache mistake the
                    # fallback for a no-op, and unescape: raw MarkdownV2 sent
                    # without parse_mode shows every backslash.
                    self._last_sent.pop(message_id, None)
                    log.warning("MarkdownV2 rejected, resending plain: %s", e)
                    await self._edit(
                        message_id,
                        _mdv2_to_plain(text),
                        parse_mode=None,
                        reply_markup=reply_markup,
                    )
                else:
                    log.warning("Failed to edit message: %s", e)
                return
            # Telegram itself says the text is already there: remember it.
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            except Exception:
                return
        except TimedOut:
            return
        except Exception:
            log.exception("Unexpected error editing message")
            return
        self._last_sent[message_id] = (text, parse_mode, reply_markup)

    async def _send(self, text: str, parse_mode: str | None = None) -> int | None:
        try:
            msg = await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            self._last_sent[msg.message_id] = (text, parse_mode, None)
            return msg.message_id
        except BadRequest as e:
            if parse_mode:
                log.warning("MarkdownV2 rejected, sending plain: %s", e)
                return await self._send(_mdv2_to_plain(text), parse_mode=None)
            return None
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                msg = await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                )
                self._last_sent[msg.message_id] = (text, None, None)
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
