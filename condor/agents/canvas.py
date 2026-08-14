"""The session canvas — the agent's own running narrative (FEAT-036).

The journal answers *what happened*; the canvas answers *what the agent
currently believes*. It lives beside the journal in the session directory:

    agents/{slug}/strategies/{sslug}/sessions/session_{N}/
        canvas.md             # current narrative — one block per section
        canvas_revisions.md   # append-only; every revision ever written

The section set is **closed** so the agent cannot invent structure, the file
stays parseable, and — because the whole canvas is inlined into every tick
prompt and prompt caching cannot help (see FEAT-036) — its size stays bounded
by construction. ``MAX_SECTION_CHARS`` is the cost control, not a nicety.

Revisions are logged in full and never inlined, so the agent's belief at any
past tick stays reconstructable even though ``canvas.md`` is overwritten.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from condor.fsutil import atomic_write_text

CANVAS_FILE = "canvas.md"
REVISIONS_FILE = "canvas_revisions.md"

CANVAS_SECTIONS = ("thesis", "working", "changed", "questions")
SECTION_TITLES = {
    "thesis": "Thesis",
    "working": "What's Working",
    "changed": "What I Changed",
    "questions": "Open Questions",
}

MAX_SECTION_CHARS = 1200  # ~300 tokens; 4 sections => ~1.2k tokens/tick input
MAX_REVISIONS_PER_TICK = 4  # thrash guard: one per section
MAX_REVISION_ENTRIES = 200  # revisions log is never inlined; trimmed FIFO

_REVISION_RE = re.compile(
    r"^- #(?P<tick>\d+) \| (?P<ts>[^|]+) \| (?P<section>\w+) \| (?P<text>.*)$"
)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_canvas(session_dir: Path | None) -> str:
    """Return ``canvas.md`` verbatim, or ``""`` if the agent never wrote one."""
    path = _canvas_path(session_dir)
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def read_sections(session_dir: Path | None) -> dict[str, str]:
    """Parse ``canvas.md`` into ``{section_key: body}`` (missing keys omitted)."""
    text = read_canvas(session_dir)
    if not text:
        return {}
    title_to_key = {title: key for key, title in SECTION_TITLES.items()}
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(buf).strip()
            current = title_to_key.get(line[3:].strip())
            buf = []
        elif current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return {k: v for k, v in sections.items() if v}


def last_revised_tick(session_dir: Path | None) -> int:
    """Tick of the most recent revision, or 0 if the canvas was never written."""
    revisions = _read_revisions(session_dir)
    if not revisions:
        return 0
    return revisions[-1]["tick"]


def recent_revisions(session_dir: Path | None, limit: int = 20) -> list[dict]:
    """The last ``limit`` revisions, newest first — the belief history."""
    revisions = _read_revisions(session_dir)
    return list(reversed(revisions[-limit:]))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def write_section(
    session_dir: Path | None, section: str, text: str, *, tick: int = 0
) -> dict:
    """Replace one canvas section, leaving its siblings untouched.

    Returns ``{"written": True, ...}`` or a benign ``{"skipped": ...}`` — never
    raises for agent-supplied input, since this runs inside a live trading tick.
    """
    if session_dir is None:
        return {"skipped": "no session directory — nothing to write a canvas to"}
    key = (section or "").strip().lower()
    if key not in CANVAS_SECTIONS:
        return {
            "error": f"unknown canvas section '{section}'. "
            f"Use one of: {', '.join(CANVAS_SECTIONS)}"
        }
    body = (text or "").strip()
    if not body:
        return {"error": "text is required for a canvas section"}

    revisions = _read_revisions(session_dir)
    written_this_tick = sum(1 for r in revisions if r["tick"] == tick)
    if tick and written_this_tick >= MAX_REVISIONS_PER_TICK:
        return {
            "skipped": f"already revised {MAX_REVISIONS_PER_TICK} canvas sections "
            f"on tick #{tick} — one revision per section is enough"
        }

    truncated = len(body) > MAX_SECTION_CHARS
    if truncated:
        body = body[:MAX_SECTION_CHARS].rstrip()

    sections = read_sections(session_dir)
    sections[key] = body
    _write_canvas(session_dir, sections)
    _append_revision(session_dir, revisions, tick=tick, section=key, text=body)

    result: dict = {"written": True, "section": key}
    if truncated:
        result["truncated_to"] = MAX_SECTION_CHARS
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _canvas_path(session_dir: Path | None) -> Path | None:
    return None if session_dir is None else Path(session_dir) / CANVAS_FILE


def _revisions_path(session_dir: Path | None) -> Path | None:
    return None if session_dir is None else Path(session_dir) / REVISIONS_FILE


def _write_canvas(session_dir: Path, sections: dict[str, str]) -> None:
    """Rebuild ``canvas.md`` from the parsed sections, in canonical order.

    Rebuilding rather than patching in place is what keeps the closed section
    set an invariant: anything the file picked up that is not a known section
    simply does not survive a write.
    """
    parts = ["# Session Canvas", ""]
    for key in CANVAS_SECTIONS:
        parts.append(f"## {SECTION_TITLES[key]}")
        body = sections.get(key, "").strip()
        parts.append(body if body else "")
        parts.append("")
    _atomic_write(Path(session_dir) / CANVAS_FILE, "\n".join(parts).rstrip() + "\n")


def _read_revisions(session_dir: Path | None) -> list[dict]:
    path = _revisions_path(session_dir)
    if path is None or not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _REVISION_RE.match(line)
        if not m:
            continue
        out.append(
            {
                "tick": int(m.group("tick")),
                "timestamp": m.group("ts").strip(),
                "section": m.group("section"),
                "text": m.group("text"),
            }
        )
    return out


def _append_revision(
    session_dir: Path, revisions: list[dict], *, tick: int, section: str, text: str
) -> None:
    """Append one revision line, trimming the log FIFO past the cap."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # One line per revision: the text is flattened so the log stays parseable.
    flat = " ".join(text.split())
    lines = [
        f"- #{r['tick']} | {r['timestamp']} | {r['section']} | {r['text']}"
        for r in revisions
    ]
    lines.append(f"- #{tick} | {now} | {section} | {flat}")
    if len(lines) > MAX_REVISION_ENTRIES:
        lines = lines[-MAX_REVISION_ENTRIES:]
    _atomic_write(
        Path(session_dir) / REVISIONS_FILE,
        "# Canvas revisions\n\n" + "\n".join(lines) + "\n",
    )


def _atomic_write(path: Path, content: str) -> None:
    """tmpfile + ``os.replace``, via the shared :mod:`condor.fsutil` helper.

    A tick can be cancelled mid-write; a half-written canvas would then be
    inlined into the next prompt.
    """
    atomic_write_text(path, content)


# ---------------------------------------------------------------------------
# Staleness nudge
# ---------------------------------------------------------------------------


class NudgeTracker:
    """Decides when to tell the agent its canvas has gone out of date.

    Pure bookkeeping over values the engine already holds, so a nudge costs
    zero tokens to *decide* — only the one line it emits into the prompt.
    Rate-limited so a persistent condition (a standing position, a stuck
    error) cannot nag on every tick.
    """

    def __init__(self, *, nudge_ticks: int = 12, band_usd: float = 25.0) -> None:
        self.nudge_ticks = max(1, int(nudge_ticks))
        self.band_usd = float(band_usd)
        self._last_nudge_tick = 0
        self._prev_open_count: int | None = None
        self._pnl_anchor: float | None = None
        self._last_revised_seen = -1

    def next(
        self,
        *,
        tick: int,
        last_revised_tick: int,
        open_count: int,
        total_pnl: float,
        had_error: bool = False,
    ) -> str:
        """Return the nudge line for this tick, or ``""`` for silence."""
        # A fresh revision re-anchors the PnL band: the band measures drift
        # since the agent last said something, not since the session began.
        if last_revised_tick != self._last_revised_seen:
            self._last_revised_seen = last_revised_tick
            self._pnl_anchor = total_pnl
        if self._pnl_anchor is None:
            self._pnl_anchor = total_pnl

        prev_open = self._prev_open_count
        self._prev_open_count = open_count

        gap = max(1, self.nudge_ticks // 2)
        if self._last_nudge_tick and tick - self._last_nudge_tick < gap:
            return ""

        drift = total_pnl - self._pnl_anchor
        if prev_open is not None and (prev_open == 0) != (open_count == 0):
            reason = (
                "you now hold positions"
                if open_count
                else "your positions are all closed"
            )
        elif abs(drift) > self.band_usd:
            reason = f"PnL has moved ${drift:+.2f} since then"
        elif had_error:
            reason = "the previous tick errored"
        elif tick - last_revised_tick >= self.nudge_ticks:
            reason = "it has not changed in a while"
        else:
            return ""

        self._last_nudge_tick = tick
        if last_revised_tick <= 0:
            return (
                f"Your canvas is still empty — {reason}. Write your opening "
                "thesis this tick."
            )
        ago = tick - last_revised_tick
        return (
            f"Your canvas was last revised at tick #{last_revised_tick} "
            f"({ago} ticks ago) — {reason}. Revise the sections that are now wrong."
        )
