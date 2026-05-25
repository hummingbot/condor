"""Convert LLM-style Markdown to Telegram MarkdownV2 with proper escaping."""

from __future__ import annotations

import html
import re

from utils.telegram_formatters import escape_markdown_v2

_PLACEHOLDER = "\x00{}\x00"

_CODE_BLOCK_RE = re.compile(r"```(?:([\w.+-]*)\n)?([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_BOLD_UNDER_RE = re.compile(r"(?<![_*])__(?!_)(.+?)(?<![_*])__(?!_)")
_ITALIC_UNDER_RE = re.compile(r"(?<![\w])_([^_\n]+?)_(?![\w])")
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$", re.MULTILINE)
_HR_RE = re.compile(r"^-{3,}$", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\|[-| :]+\|$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$", re.MULTILINE)


def _escape_code_content(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _convert_table_row(match: re.Match[str]) -> str:
    cells = [c.strip() for c in match.group(1).split("|")]
    return "  ".join(cells)


def _escape_preserving_placeholders(text: str) -> str:
    parts = re.split(r"\x00(\d+)\x00", text)
    out: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            out.append(_PLACEHOLDER.format(part))
        else:
            out.append(escape_markdown_v2(part))
    return "".join(out)


def _resolve_placeholders(text: str, formatted: list[str]) -> str:
    while True:
        match = re.search(r"\x00(\d+)\x00", text)
        if not match:
            return text
        idx = int(match.group(1))
        text = text.replace(match.group(0), formatted[idx], 1)


def _wrap(formatted: list[str], value: str) -> str:
    formatted.append(value)
    return _PLACEHOLDER.format(len(formatted) - 1)


def _restore_plain_segments(text: str, formatted: list[str]) -> str:
    parts = re.split(r"\x00(\d+)\x00", text)
    out: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            out.append(formatted[int(part)])
        else:
            out.append(escape_markdown_v2(part))
    return "".join(out)


def markdown_to_telegram_v2(text: str) -> str:
    """Convert standard Markdown to Telegram MarkdownV2."""
    if not text:
        return ""

    formatted: list[str] = []
    result = html.unescape(text.strip())

    def _code_block(match: re.Match[str]) -> str:
        lang = (match.group(1) or "").strip()
        body = _escape_code_content(match.group(2) or "")
        if lang:
            block = f"```{lang}\n{body}```"
        else:
            block = f"```{body}```"
        return _wrap(formatted, block)

    result = _CODE_BLOCK_RE.sub(_code_block, result)
    result = _INLINE_CODE_RE.sub(
        lambda m: _wrap(formatted, f"`{_escape_code_content(m.group(1))}`"),
        result,
    )
    result = _LINK_RE.sub(
        lambda m: _wrap(
            formatted,
            f"[{escape_markdown_v2(m.group(1))}]({escape_markdown_v2(m.group(2))})",
        ),
        result,
    )
    result = _BOLD_RE.sub(
        lambda m: _wrap(formatted, f"*{_escape_preserving_placeholders(m.group(1))}*"),
        result,
    )
    result = _BOLD_UNDER_RE.sub(
        lambda m: _wrap(formatted, f"*{_escape_preserving_placeholders(m.group(1))}*"),
        result,
    )
    result = _STRIKE_RE.sub(
        lambda m: _wrap(formatted, f"~{_escape_preserving_placeholders(m.group(1))}~"),
        result,
    )
    result = _ITALIC_UNDER_RE.sub(
        lambda m: _wrap(formatted, f"_{_escape_preserving_placeholders(m.group(1))}_"),
        result,
    )
    result = _ITALIC_STAR_RE.sub(
        lambda m: _wrap(formatted, f"_{_escape_preserving_placeholders(m.group(1))}_"),
        result,
    )
    result = _HEADER_RE.sub(
        lambda m: _wrap(formatted, f"*{escape_markdown_v2(m.group(1))}*"),
        result,
    )
    result = _BLOCKQUOTE_RE.sub(r"\1", result)
    result = _HR_RE.sub("", result)
    result = _TABLE_SEP_RE.sub("", result)
    result = _TABLE_ROW_RE.sub(_convert_table_row, result)
    result = re.sub(r"(\n[ \t]*){3,}", "\n\n", result)

    result = _restore_plain_segments(result, formatted)
    return _resolve_placeholders(result, formatted)


def plain_text_from_agent_markdown(text: str) -> str:
    """Readable plain-text fallback when MarkdownV2 rendering fails."""
    from condor.telegram_notify import compact_notification_whitespace, strip_telegram_markdown_v2_escapes

    cleaned = html.unescape(text or "")
    cleaned = _CODE_BLOCK_RE.sub(lambda m: m.group(2) or "", cleaned)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<![\w])_([^_\n]+?)_(?![\w])", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\1", cleaned)
    cleaned = re.sub(r"~~(.+?)~~", r"\1", cleaned)
    cleaned = _HEADER_RE.sub(r"\1", cleaned)
    cleaned = _BLOCKQUOTE_RE.sub(r"\1", cleaned)
    cleaned = _HR_RE.sub("", cleaned)
    cleaned = _TABLE_SEP_RE.sub("", cleaned)
    cleaned = _TABLE_ROW_RE.sub(_convert_table_row, cleaned)
    return compact_notification_whitespace(strip_telegram_markdown_v2_escapes(cleaned))
