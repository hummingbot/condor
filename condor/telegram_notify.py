"""Helpers for Telegram push messages driven by Condor MCP / agents."""

from __future__ import annotations

import re

_TELEGRAM_MD_V2_ESCAPABLE = frozenset("_[]()~`>#+-=|{}.!")
_TELEGRAM_MAX_MESSAGE_LEN = 4096


def strip_telegram_markdown_v2_escapes(text: str) -> str:
    """Remove MarkdownV2-style backslash escapes LLMs often emit for plain Telegram text.

    When parse_mode is off, sequences like '\\|' and '\\$' show up literally; stripping
    them restores normal punctuation for human readers.
    """
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
            if nxt in _TELEGRAM_MD_V2_ESCAPABLE or nxt == "@":
                out.append(nxt)
                i += 2
                continue
        out.append(text[i])
        i += 1
    joined = "".join(out)
    # LLMs often escape `$` even though it is optional in MarkdownV2; normalize for plain text.
    joined = re.sub(r"\\\$", "$", joined)
    return joined


def compact_notification_whitespace(text: str) -> str:
    """Collapse excessive blank lines; trim edges."""
    if not text:
        return ""
    lines = [ln.rstrip() for ln in text.splitlines()]
    compact: list[str] = []
    prev_blank = True
    for ln in lines:
        blank = not ln.strip()
        if blank:
            if not prev_blank:
                compact.append("")
            prev_blank = True
        else:
            compact.append(ln)
            prev_blank = False
    return "\n".join(compact).strip()


def prepare_agent_notification_text(text: str, max_chars: int | None = 3500) -> str:
    """Normalize agent-authored notification bodies for Telegram (plain rendering)."""
    cleaned = compact_notification_whitespace(strip_telegram_markdown_v2_escapes(text))
    limit = max_chars or _TELEGRAM_MAX_MESSAGE_LEN
    limit = min(limit, _TELEGRAM_MAX_MESSAGE_LEN)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"

