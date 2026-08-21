"""JournalManager -- compact persistent memory for trading agents.

Data is organized by strategy (a playbook owned by an Agent) and session::

    agents/
        {agent_slug}/
            strategies/
                {strategy_slug}/
                    strategy.md        # strategy definition (tactic + config)
                    config.yml         # runtime config
                    learnings.md       # cross-session learnings
                    dry_runs/          # experiment snapshots (experiment_N.md)
                    sessions/
                        session_1/
                            journal.md  # summary + decisions + ticks + executors
                            snapshots/
                                snapshot_1.md
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from condor.fsutil import atomic_write_text

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "agents"

MAX_LEARNINGS = 20


def _strategy_base_dir(prefix: str) -> Path:
    """Resolve the per-strategy base dir from an agent_id prefix.

    New format prefixes are ``"{agent_slug}.{strategy_slug}"`` →
    ``agents/{agent_slug}/strategies/{strategy_slug}/``. Legacy flat
    prefixes (no dot) fall back to ``agents/{slug}/`` so old ids still
    resolve.
    """
    if "." in prefix:
        agent_slug, sslug = prefix.split(".", 1)
        return _DATA_ROOT / agent_slug / "strategies" / sslug
    return _DATA_ROOT / prefix


def resolve_agent_dirs(agent_id: str) -> tuple[Path | None, Path | None]:
    """Derive (session_dir, base_dir) from an agent_id.

    agent_id format: ``"{agent_slug}.{strategy_slug}_{N}"`` (session) or
    ``"..._e{N}"`` (experiment). ``base_dir`` is the strategy folder that holds
    ``sessions/`` and ``learnings.md``.

    Returns (None, None) if the path doesn't exist on disk.
    """
    last_sep = agent_id.rfind("_")
    if last_sep == -1:
        return None, None
    prefix = agent_id[:last_sep]
    num_part = agent_id[last_sep + 1 :]

    base_dir = _strategy_base_dir(prefix)
    if not base_dir.is_dir():
        return None, None

    # Experiments (e.g. "e3") are flat files, not directories
    if num_part.startswith("e"):
        return None, base_dir

    try:
        session_num = int(num_part)
    except ValueError:
        return None, None
    session_dir = base_dir / "sessions" / f"session_{session_num}"
    return session_dir, base_dir


MAX_SNAPSHOTS = 100

# Retention for the per-tick append-only sections of journal.md (PERF-136).
# Ticks and Snapshots gain one line per tick, so on a long-running session they
# grow without bound and every tick pays to rewrite them. Past the cap the
# oldest lines are *moved* -- not deleted -- to ``journal_archive.md`` next to
# journal.md, and the section keeps a marker line saying what went where.
# 1000 lines is ~17h of history at a 60s tick and ~4 days at a 5m tick. No
# reader of these two sections needs more: nothing reads a Ticks line's content
# at all (only the tick number, from the last one), and Snapshots feeds the
# drawdown peak -- carried across the trim in the marker -- plus a fallback
# equity curve. Everything else lives in Summary and Decisions, both already
# bounded and untouched here.
MAX_TICK_LINES = 1000
MAX_SNAPSHOT_LINES = 1000

# Sidecar holding everything trimmed out of journal.md. Append-only, never read
# in the trading hot path: it exists so the audit trail survives the trim and a
# human (or the agent, via the journal_read MCP tool) can still go back.
ARCHIVE_NAME = "journal_archive.md"

# Marker left in place of the archived lines. Deliberately starts with "- " so
# it reads as part of the list, and with a word that no entry parser accepts, so
# _parse_snapshots / _count_ticks skip it instead of parsing it as data.
ARCHIVE_MARKER_PREFIX = "- archived "

JOURNAL_TEMPLATE = """\
# Journal - {agent_id}

## Summary
No ticks yet.

## Decisions

## Ticks

## Executors

## Snapshots
"""

LEARNINGS_TEMPLATE = """\
# Learnings

## Market Observations

## Execution Notes

## Retired Insights
"""

LEARNING_CATEGORIES = {
    "market": "Market Observations",
    "execution": "Execution Notes",
}
DEFAULT_LEARNING_CATEGORY = "market"

SNAPSHOT_TEMPLATE = """\
# Snapshot #{tick} — {timestamp}

<details><summary>System Prompt ({prompt_len} chars)</summary>

{system_prompt}

</details>

## Executor State
{executors_data}

## Risk State
{risk_state}

## Agent Response
{response_text}

## Tool Calls ({tool_count})

{tool_calls}

## Stats
Duration: {duration:.1f}s
"""


def next_session_number(agent_dir: Path) -> int:
    """Determine the next session number by scanning existing session_* dirs."""
    # Check new location first
    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.exists():
        # Check legacy location
        legacy_dir = agent_dir / "trading_sessions"
        if legacy_dir.exists():
            sessions_dir = legacy_dir
        else:
            return 1
    existing = [
        int(d.name.split("_", 1)[1])
        for d in sessions_dir.iterdir()
        if d.is_dir() and d.name.startswith("session_")
    ]
    return max(existing, default=0) + 1


def next_experiment_number(agent_dir: Path) -> int:
    """Determine the next experiment number by scanning experiment_*.md files.

    Checks both dry_runs/ (new) and experiments/ (legacy) directories.
    """
    existing = []
    for dir_name in ("dry_runs", "experiments"):
        d = agent_dir / dir_name
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and f.suffix == ".md":
                m = re.match(r"experiment_(\d+)\.md", f.name)
                if m:
                    existing.append(int(m.group(1)))
    return max(existing, default=0) + 1


# Line format written by JournalManager.record_tick; count_journal_ticks and
# _count_ticks parse it back, so the format has a single owner here.
TICK_LINE_PREFIX = "- tick#"


def highest_tick_number(text: str) -> int:
    """Highest tick number recorded in ``text`` (0 if it holds no tick line).

    The tick *number* is read back from the line rather than derived from the
    line count, so numbering survives the retention trim (PERF-136): a journal
    whose first 1000 ticks were archived still resumes at 1001. Lines are
    appended in order, so the last one normally wins, but the max is taken
    anyway -- the MCP journal_write tool can edit the file out of band.
    """
    highest = 0
    seen = 0
    for line in text.splitlines():
        if not line.startswith(TICK_LINE_PREFIX):
            continue
        seen += 1
        head = line[len(TICK_LINE_PREFIX) :].split("|", 1)[0].strip()
        try:
            highest = max(highest, int(head))
        except ValueError:
            continue
    # A tick line with an unparseable number is not a reason to restart from
    # zero and overwrite history: fall back to counting what is there.
    return highest or seen


def count_journal_ticks(journal_path: Path) -> int:
    """Number of ticks a session has run, from its journal.md."""
    if not journal_path.exists():
        return 0
    return highest_tick_number(journal_path.read_text(errors="replace"))


EXPERIMENT_TEMPLATE = """\
# Experiment #{num} — {timestamp}
Mode: {execution_mode}
Model: {agent_key}

<details><summary>System Prompt ({prompt_len} chars)</summary>

{system_prompt}

</details>

## Executor State
{executors_data}

## Risk State
{risk_state}

## Agent Response
{response_text}

## Tool Calls ({tool_count})

{tool_calls}

## Stats
Duration: {duration:.1f}s
"""


def save_experiment_snapshot(
    agent_dir: Path,
    experiment_num: int,
    execution_mode: str,
    timestamp: str,
    system_prompt: str,
    response_text: str,
    tool_calls: list[dict],
    executors_data: str,
    risk_state: dict,
    duration: float,
    agent_key: str = "",
) -> Path:
    """Save a single experiment snapshot as a flat .md file."""
    experiments_dir = agent_dir / "dry_runs"
    experiments_dir.mkdir(parents=True, exist_ok=True)

    # Format risk state
    max_dd = risk_state.get("max_drawdown_pct", -1)
    dd_display = (
        f"{risk_state.get('drawdown_pct', 0):.1f}% / {max_dd:.1f}% limit"
        if max_dd >= 0
        else "disabled"
    )
    risk_lines = [
        f"- Position Size: ${risk_state.get('total_exposure', 0):.2f} / ${risk_state.get('max_position_size', 500):.2f} limit",
        f"- Open Executors: {risk_state.get('executor_count', 0)} / {risk_state.get('max_open_executors', 5)} limit",
        f"- Drawdown: {dd_display}",
        f"- Status: {'BLOCKED - ' + risk_state.get('block_reason', '') if risk_state.get('is_blocked') else 'ACTIVE'}",
    ]

    # Format tool calls
    import json

    tool_parts = []
    for i, tc in enumerate(tool_calls, 1):
        tc_name = tc.get("name", tc.get("title", "unknown"))
        tc_status = tc.get("status", "")
        tool_parts.append(f"### {i}. {tc_name} ({tc_status})")
        if tc.get("input"):
            input_str = (
                json.dumps(tc["input"], indent=2)
                if isinstance(tc["input"], dict)
                else str(tc["input"])
            )
            tool_parts.append(f"**Input:**\n```json\n{input_str}\n```")
        if tc.get("output"):
            output_str = str(tc["output"])[:2000]
            tool_parts.append(f"**Output:**\n```\n{output_str}\n```")
        tool_parts.append("")

    content = EXPERIMENT_TEMPLATE.format(
        num=experiment_num,
        timestamp=timestamp,
        execution_mode=execution_mode,
        agent_key=agent_key or "unknown",
        prompt_len=len(system_prompt),
        system_prompt=system_prompt,
        executors_data=executors_data or "No executors.",
        risk_state="\n".join(risk_lines),
        response_text=response_text or "No response.",
        tool_count=len(tool_calls),
        tool_calls="\n".join(tool_parts) or "No tool calls.",
        duration=duration,
    )

    path = experiments_dir / f"experiment_{experiment_num}.md"
    path.write_text(content)
    return path


class JournalManager:
    """Read/write journal + tracker for one agent session.

    Combines living memory (Summary) with execution tracking
    (Decisions, Ticks, Executors, Snapshots) in a single ``journal.md`` file.
    Learnings are stored separately in ``{agent_dir}/learnings.md``.
    Full snapshots go into ``snapshots/snapshot_N.md``.
    """

    def __init__(
        self,
        agent_id: str,
        session_dir: Path | None = None,
        agent_dir: Path | None = None,
    ):
        self.agent_id = agent_id
        if session_dir:
            self._session_dir = session_dir
        else:
            # Try to resolve from agent_id before falling back
            resolved_session, resolved_agent = resolve_agent_dirs(agent_id)
            self._session_dir = (
                resolved_session if resolved_session else _DATA_ROOT / agent_id
            )
            if not agent_dir and resolved_agent:
                agent_dir = resolved_agent
        self._agent_dir = agent_dir  # For cross-session learnings
        self._path = self._session_dir / "journal.md"
        self._snapshots_dir = self._session_dir / "snapshots"
        self._session_dir.mkdir(parents=True, exist_ok=True)

        # Also support legacy runs/ dir for reading
        self._legacy_runs_dir = self._session_dir / "runs"

        # Read cache: journal.md text keyed by (mtime_ns, size), plus parsed
        # sections derived from that text. Invalidated when the stamp changes
        # (external writers, e.g. the MCP journal_write tool) or explicitly on
        # our own writes (mtime granularity could otherwise mask them).
        self._cache_stamp: tuple[int, int] | None = None
        self._cache_text: str = ""
        self._parsed_cache: dict[str, list[dict]] = {}

        # Write batching (PERF-136): inside ``with journal.batch():`` the
        # updated document is held here and hits the disk once, on exit.
        self._batch_depth = 0
        self._pending_text: str | None = None
        # Last section this process appended to journal_archive.md, so the
        # sidecar gets one header per run of same-section lines, not per line.
        self._archive_last_section: str | None = None

        if not self._path.exists():
            atomic_write_text(self._path, JOURNAL_TEMPLATE.format(agent_id=agent_id))

        # Ensure learnings.md exists at agent level
        if self._agent_dir:
            learnings_path = self._agent_dir / "learnings.md"
            if not learnings_path.exists():
                atomic_write_text(learnings_path, LEARNINGS_TEMPLATE)

        self._tick_count = self._count_ticks()

    # ------------------------------------------------------------------
    # Learnings (cross-session, stored in agent_dir/learnings.md)
    # ------------------------------------------------------------------

    def _learnings_path(self) -> Path | None:
        """Get the learnings file path."""
        if self._agent_dir:
            return self._agent_dir / "learnings.md"
        # Fallback: try to find learnings in session dir parent
        parent = self._session_dir.parent
        if parent.name == "sessions" or parent.name == "trading_sessions":
            return parent.parent / "learnings.md"
        return None

    def read_learnings(self) -> str:
        """Return the learnings content (cross-session), grouped by category."""
        path = self._learnings_path()
        if path and path.exists():
            text = path.read_text()
            parts = []
            # Read each category section
            for cat_key, cat_header in LEARNING_CATEGORIES.items():
                m = re.search(
                    rf"^## {re.escape(cat_header)}\n(.*?)(?=^## |\Z)",
                    text,
                    re.MULTILINE | re.DOTALL,
                )
                if m and m.group(1).strip():
                    parts.append(f"**{cat_header}:**\n{m.group(1).strip()}")
            if parts:
                return "\n\n".join(parts)
            # Legacy fallback: try Active Insights
            m = re.search(
                r"^## Active Insights\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
            )
            if m:
                return m.group(1).strip()
            lines = text.strip().splitlines()
            content = [l for l in lines if not l.startswith("# ")]
            return "\n".join(content).strip()
        # Fallback: read from journal Learnings section (legacy)
        return self._get_section("Learnings")

    def append_learning(self, text_content: str, category: str = "") -> None:
        """Add a learning under a category, deduplicating against existing ones.

        Args:
            text_content: The learning text.
            category: "market" or "execution". Defaults to "market".
        """
        path = self._learnings_path()
        if not path:
            self._append_learning_to_journal(text_content)
            return

        if not path.exists():
            atomic_write_text(path, LEARNINGS_TEMPLATE)

        # Resolve category to section header
        cat = category if category in LEARNING_CATEGORIES else DEFAULT_LEARNING_CATEGORY
        section_header = LEARNING_CATEGORIES[cat]

        full_text = path.read_text()

        # Extract existing learnings from the target section
        pattern = rf"(^## {re.escape(section_header)}\n)(.*?)(?=^## |\Z)"
        m = re.search(pattern, full_text, re.MULTILINE | re.DOTALL)
        if m:
            section_content = m.group(2).strip()
            existing_lines = [
                l for l in section_content.splitlines() if l.startswith("- ")
            ]
        else:
            existing_lines = []

        # Deduplicate against ALL category sections (not just the target)
        normalized_new = _normalize(text_content)
        for cat_header in LEARNING_CATEGORIES.values():
            cat_pattern = rf"^## {re.escape(cat_header)}\n(.*?)(?=^## |\Z)"
            cm = re.search(cat_pattern, full_text, re.MULTILINE | re.DOTALL)
            if not cm:
                continue
            for line in cm.group(1).strip().splitlines():
                if not line.startswith("- "):
                    continue
                existing_text = re.sub(
                    r"^- (\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] |\[\d{2}:\d{2}\] )?",
                    "",
                    line,
                )
                if _normalize(existing_text) == normalized_new:
                    return
                if _word_overlap(normalized_new, _normalize(existing_text)) > 0.5:
                    return

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        existing_lines.append(f"- [{now}] {text_content}")

        if len(existing_lines) > MAX_LEARNINGS:
            existing_lines = existing_lines[-MAX_LEARNINGS:]

        new_section = "\n".join(existing_lines)
        if m:
            new_text = (
                full_text[: m.start(2)] + new_section + "\n\n" + full_text[m.end(2) :]
            )
        else:
            new_text = full_text.rstrip() + f"\n\n## {section_header}\n{new_section}\n"
        atomic_write_text(path, new_text)

    def _append_learning_to_journal(self, text_content: str) -> None:
        """Legacy: append learning to journal's Learnings section."""
        section = self._get_section("Learnings")
        existing_lines = [l for l in section.splitlines() if l.startswith("- ")]

        normalized_new = _normalize(text_content)
        for line in existing_lines:
            existing_text = re.sub(r"^- (\[\d{2}:\d{2}\] )?", "", line)
            if _normalize(existing_text) == normalized_new:
                return
            if _word_overlap(normalized_new, _normalize(existing_text)) > 0.5:
                return

        now = datetime.now(timezone.utc).strftime("%H:%M")
        existing_lines.append(f"- [{now}] {text_content}")

        if len(existing_lines) > MAX_LEARNINGS:
            existing_lines = existing_lines[-MAX_LEARNINGS:]

        self._replace_section("Learnings", "\n".join(existing_lines))

    # ------------------------------------------------------------------
    # Reading (journal)
    # ------------------------------------------------------------------

    def read_full(self) -> str:
        """Return the entire journal contents (cached until the file changes)."""
        if self._pending_text is not None:
            # Mid-batch: the authoritative document is the one in memory.
            return self._pending_text
        try:
            stat = self._path.stat()
        except OSError:
            return ""
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self._cache_stamp:
            self._cache_text = self._path.read_text()
            self._cache_stamp = stamp
            self._parsed_cache.clear()
        return self._cache_text

    @contextmanager
    def batch(self) -> Iterator[JournalManager]:
        """Coalesce every journal write in the block into a single file write.

        A tick writes the journal three to five times (record_tick,
        record_snapshot, write_summary, plus append_action on violations) and
        each one used to rewrite the whole file. Inside this block the updates
        are applied to an in-memory document -- readers inside the block see
        them -- and the result is written once on exit, including on error, so
        a tick that raises still journals what it had recorded.
        """
        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self.flush()

    def flush(self) -> None:
        """Write out a batched document, if any is pending."""
        text, self._pending_text = self._pending_text, None
        if text is not None:
            self._write_journal(text)

    def _write_journal(self, text: str) -> None:
        """Write journal.md and invalidate the read cache."""
        if self._batch_depth > 0:
            self._pending_text = text
            self._parsed_cache.clear()
            return
        atomic_write_text(self._path, text)
        self._cache_stamp = None
        self._parsed_cache.clear()

    def read_summary(self) -> str:
        """Return the summary section."""
        return self._get_section("Summary")

    def read_state(self) -> str:
        """Return the summary (backwards compat alias for read_summary)."""
        summary = self._get_section("Summary")
        if summary:
            return summary
        return self._get_section("State")

    def read_recent(self, max_entries: int = 10) -> str:
        """Return recent decisions from snapshots."""
        snapshots = self.list_snapshots(limit=max_entries)
        if snapshots:
            parts = []
            for snap in snapshots:
                content = self.read_snapshot(snap["tick"])
                if content:
                    m = re.search(
                        r"^## Agent Response\n(.*?)(?=^## |\Z|^<details)",
                        content,
                        re.MULTILINE | re.DOTALL,
                    )
                    if m:
                        decision = m.group(1).strip()[:200]
                        parts.append(f"- **#{snap['tick']}** {decision}")
            if parts:
                return "\n".join(parts)

        # Legacy: check runs/ and Recent Actions
        runs = self._list_legacy_runs(limit=max_entries)
        if runs:
            parts = []
            for run in runs:
                content = self._read_legacy_run(run["tick"])
                if content:
                    m = re.search(
                        r"^## Decision\n(.*?)(?=^## |\Z)",
                        content,
                        re.MULTILINE | re.DOTALL,
                    )
                    if m:
                        parts.append(f"- **#{run['tick']}** {m.group(1).strip()}")
            if parts:
                return "\n".join(parts)

        content = self._get_section("Recent Actions")
        if not content:
            content = self._get_section("Actions Log")
        return content

    # ------------------------------------------------------------------
    # Snapshots (full context dumps)
    # ------------------------------------------------------------------

    def save_full_snapshot(
        self,
        tick: int,
        timestamp: str,
        system_prompt: str,
        response_text: str,
        tool_calls: list[dict[str, Any]],
        executors_data: str,
        risk_state: dict[str, Any],
        duration: float,
    ) -> Path:
        """Write a full snapshot capturing everything."""
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)

        # Format risk state
        max_dd = risk_state.get("max_drawdown_pct", -1)
        dd_display = (
            f"{risk_state.get('drawdown_pct', 0):.1f}% / {max_dd:.1f}% limit"
            if max_dd >= 0
            else "disabled"
        )
        risk_lines = [
            f"- Position Size: ${risk_state.get('total_exposure', 0):.2f} / ${risk_state.get('max_position_size', 500):.2f} limit",
            f"- Open Executors: {risk_state.get('executor_count', 0)} / {risk_state.get('max_open_executors', 5)} limit",
            f"- Drawdown: {dd_display}",
            f"- Status: {'BLOCKED - ' + risk_state.get('block_reason', '') if risk_state.get('is_blocked') else 'ACTIVE'}",
        ]

        # Format tool calls
        tool_parts = []
        for i, tc in enumerate(tool_calls, 1):
            tc_name = tc.get("name", tc.get("title", "unknown"))
            tc_status = tc.get("status", "")
            tool_parts.append(f"### {i}. {tc_name} ({tc_status})")
            if tc.get("input"):
                input_str = (
                    json.dumps(tc["input"], indent=2)
                    if isinstance(tc["input"], dict)
                    else str(tc["input"])
                )
                tool_parts.append(f"**Input:**\n```json\n{input_str}\n```")
            if tc.get("output"):
                output_str = str(tc["output"])[:2000]
                tool_parts.append(f"**Output:**\n```\n{output_str}\n```")
            tool_parts.append("")

        content = SNAPSHOT_TEMPLATE.format(
            tick=tick,
            timestamp=timestamp,
            prompt_len=len(system_prompt),
            system_prompt=system_prompt,
            executors_data=executors_data or "No executors.",
            risk_state="\n".join(risk_lines),
            response_text=response_text or "No response.",
            tool_count=len(tool_calls),
            tool_calls="\n".join(tool_parts) or "No tool calls.",
            duration=duration,
        )

        path = self._snapshots_dir / f"snapshot_{tick}.md"
        path.write_text(content)
        self._cleanup_old_snapshots()
        return path

    def read_snapshot(self, tick: int) -> str:
        """Read a specific snapshot by tick number."""
        path = self._snapshots_dir / f"snapshot_{tick}.md"
        if path.exists():
            return path.read_text()
        # Legacy fallback
        return self._read_legacy_run(tick)

    def list_snapshots(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent snapshots, newest first."""
        results = []

        # Check new snapshots/ dir
        if self._snapshots_dir.exists():
            files = sorted(
                self._snapshots_dir.glob("snapshot_*.md"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for f in files[:limit]:
                m = re.match(r"snapshot_(\d+)\.md", f.name)
                if m:
                    results.append(
                        {
                            "tick": int(m.group(1)),
                            "file": f.name,
                            "size": f.stat().st_size,
                        }
                    )

        # If none found, check legacy runs/
        if not results:
            return self._list_legacy_runs(limit=limit)

        return results

    def get_recent_decisions(self, count: int = 3) -> str:
        """Get the last N decision entries from the Decisions section of journal.md.

        This is much cheaper than reading snapshot files and produces compact
        one-line entries that were already written by append_action().
        """
        section = self._get_section("Decisions")
        if not section:
            return ""

        lines = [l for l in section.splitlines() if l.startswith("- ")]
        return "\n".join(lines[-count:])

    def _cleanup_old_snapshots(self) -> None:
        """Remove oldest snapshots if over MAX_SNAPSHOTS."""
        if not self._snapshots_dir.exists():
            return
        files = sorted(self._snapshots_dir.glob("snapshot_*.md"))
        if len(files) > MAX_SNAPSHOTS:
            for f in files[: len(files) - MAX_SNAPSHOTS]:
                f.unlink()

    # ------------------------------------------------------------------
    # Legacy run support (reads from runs/ dir)
    # ------------------------------------------------------------------

    def _read_legacy_run(self, tick: int) -> str:
        path = self._legacy_runs_dir / f"run_{tick}.md"
        if path.exists():
            return path.read_text()
        return ""

    def _list_legacy_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        if not self._legacy_runs_dir.exists():
            return []
        files = sorted(
            self._legacy_runs_dir.glob("run_*.md"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        results = []
        for f in files[:limit]:
            m = re.match(r"run_(\d+)\.md", f.name)
            if m:
                results.append(
                    {
                        "tick": int(m.group(1)),
                        "file": f.name,
                        "size": f.stat().st_size,
                    }
                )
        return results

    def read_run_snapshot(self, tick: int) -> str:
        """Legacy compat: try snapshots first, then runs."""
        return self.read_snapshot(tick)

    def list_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Legacy compat: try snapshots first, then runs."""
        return self.list_snapshots(limit=limit)

    # ------------------------------------------------------------------
    # Writing (journal)
    # ------------------------------------------------------------------

    def write_summary(
        self, tick: int, status: str, pnl: float, open_count: int, last_action: str
    ) -> None:
        """Update the Summary section."""
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        summary = (
            f"Last tick: #{tick} at {now}\n"
            f"Status: {status} | PnL: ${pnl:+.2f} | Open: {open_count} executors\n"
            f"Last action: {last_action[:100]}"
        )
        self._replace_section("Summary", summary)

    def write_state(self, state_text: str) -> None:
        """Overwrite the Summary section (backwards compat for write via MCP)."""
        if self._get_section("State"):
            self._replace_section("State", state_text.strip())
        else:
            self._replace_section("Summary", state_text.strip())

    def append_action(
        self,
        tick: int,
        action: str,
        reasoning: str,
        risk_note: str = "",
    ) -> None:
        """Record an action in the Decisions section."""
        now = datetime.now(timezone.utc).strftime("%H:%M")
        parts = [f"- **#{tick}** ({now}) {action}"]
        if reasoning:
            parts[0] += f" -- {reasoning}"
        if risk_note:
            parts[0] += f" [{risk_note}]"
        entry = parts[0]

        # Write to Decisions section
        section = self._get_section("Decisions")
        lines = [l for l in section.splitlines() if l.strip()]
        lines.append(entry)
        if len(lines) > 20:
            lines = lines[-20:]
        self._replace_section("Decisions", "\n".join(lines))

        # Also write to Recent Actions if it exists (legacy compat)
        if "## Recent Actions" in self.read_full():
            ra_section = self._get_section("Recent Actions")
            ra_lines = [l for l in ra_section.splitlines() if l.strip()]
            ra_lines.append(entry)
            if len(ra_lines) > 10:
                ra_lines = ra_lines[-10:]
            self._replace_section("Recent Actions", "\n".join(ra_lines))

    def append_error(self, error: str) -> None:
        """Append an error as a decision entry."""
        now = datetime.now(timezone.utc).strftime("%H:%M")
        section = self._get_section("Decisions")
        lines = [l for l in section.splitlines() if l.strip()]
        lines.append(f"- **error** ({now}) {error}")
        if len(lines) > 20:
            lines = lines[-20:]
        self._replace_section("Decisions", "\n".join(lines))

    # ------------------------------------------------------------------
    # Tick tracking
    # ------------------------------------------------------------------

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def _count_ticks(self) -> int:
        return highest_tick_number(self._get_section("Ticks"))

    def record_tick(self, response_summary: str = "", actions: int = 0) -> int:
        """Record a tick entry. Returns the new tick number."""
        self._tick_count += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        summary = response_summary[:200].replace("\n", " ")
        entry = (
            f"{TICK_LINE_PREFIX}{self._tick_count} | {now} "
            f"| actions={actions} | {summary}"
        )
        self._append_bounded("Ticks", entry, MAX_TICK_LINES)
        return self._tick_count

    # ------------------------------------------------------------------
    # Executor tracking
    # ------------------------------------------------------------------

    def track_executor(self, executor_id: str, ex_type: str, config: dict) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        connector = config.get("connector_name", "")
        pair = config.get("trading_pair", "")
        side = config.get("side", "")
        amount = config.get("total_amount_quote", 0) or config.get("amount", 0) or 0
        entry = (
            f"- executor={executor_id} | type={ex_type} | {connector} {pair} {side} "
            f"| amount=${float(amount):.2f} | created={now} | status=open | pnl=0 | volume=0"
        )
        self._append_to_section("Executors", entry)

    def update_executor(
        self, executor_id: str, pnl: float, volume: float, stopped: bool = False
    ) -> None:
        text = self.read_full()
        pattern = rf"(- executor={re.escape(executor_id)} \|.*)"
        m = re.search(pattern, text)
        if not m:
            return

        old_line = m.group(1)
        new_line = re.sub(r"pnl=[^ |]*", f"pnl={pnl:.2f}", old_line)
        new_line = re.sub(r"volume=[^ |]*", f"volume={volume:.2f}", new_line)
        if stopped:
            new_line = re.sub(r"status=\w+", "status=closed", new_line)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            new_line += f" | stopped={now}"

        text = text.replace(old_line, new_line)
        self._write_journal(text)

    # ------------------------------------------------------------------
    # Metric snapshots (inline in journal)
    # ------------------------------------------------------------------

    def record_snapshot(
        self,
        total_pnl: float,
        total_volume: float,
        open_count: int,
        position_size: float,
    ) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = (
            f"- {now} | pnl=${total_pnl:+.2f} | volume=${total_volume:,.0f} "
            f"| open={open_count} | exposure=${position_size:.2f}"
        )
        self._append_bounded("Snapshots", entry, MAX_SNAPSHOT_LINES)

    # ------------------------------------------------------------------
    # Queries (used by RiskEngine)
    # ------------------------------------------------------------------

    def _parse_executors(self) -> list[dict]:
        self.read_full()  # refresh parsed cache if the file changed
        cached = self._parsed_cache.get("executors")
        if cached is not None:
            return cached
        section = self._get_section("Executors")
        results = []
        for line in section.splitlines():
            if not line.startswith("- executor="):
                continue
            entry: dict[str, Any] = {}
            for part in line[2:].split(" | "):
                if "=" in part:
                    k, v = part.split("=", 1)
                    entry[k.strip()] = v.strip()
            results.append(entry)
        self._parsed_cache["executors"] = results
        return results

    def _parse_snapshots(self) -> list[dict]:
        self.read_full()  # refresh parsed cache if the file changed
        cached = self._parsed_cache.get("snapshots")
        if cached is not None:
            return cached
        section = self._get_section("Snapshots")
        results = []
        for line in section.splitlines():
            if not line.startswith("- ") or line.startswith(ARCHIVE_MARKER_PREFIX):
                continue
            results.append(_parse_snapshot_line(line))
        self._parsed_cache["snapshots"] = results
        return results

    def get_daily_pnl(self) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total = 0.0
        for ex in self._parse_executors():
            created = ex.get("created", "")
            if created.startswith(today):
                try:
                    total += float(ex.get("pnl", 0))
                except (ValueError, TypeError):
                    pass
        return total

    def get_total_exposure(self) -> float:
        total = 0.0
        for ex in self._parse_executors():
            if ex.get("status") == "open":
                amount_str = ex.get("amount", "$0").lstrip("$")
                try:
                    total += float(amount_str)
                except (ValueError, TypeError):
                    pass
        return total

    def get_open_executor_count(self) -> int:
        return sum(1 for ex in self._parse_executors() if ex.get("status") == "open")

    def get_drawdown_pct(self) -> float:
        snapshots = self._parse_snapshots()
        if not snapshots:
            return 0.0
        pnls = [s.get("pnl", 0) for s in snapshots]
        current = pnls[-1]
        archived_peak = self._archived_peak_pnl()
        if archived_peak is not None:
            pnls.append(archived_peak)  # the high-water mark outlives the trim
        peak = max(pnls)
        drawdown = peak - current
        if drawdown <= 0:
            return 0.0
        exposure = snapshots[-1].get("exposure", 0.0)
        if exposure <= 0:
            return 0.0
        return drawdown / exposure * 100

    def get_pnl_series(self) -> list[dict]:
        """PnL points still held in journal.md (at most MAX_SNAPSHOT_LINES).

        Older points are in ``journal_archive.md``; this is the equity-curve
        fallback used when the bot's own series is empty, so a bounded tail is
        what it needs -- it must not re-read the whole history every tick.
        """
        return [
            {"timestamp": s.get("timestamp", ""), "pnl": s.get("pnl", 0)}
            for s in self._parse_snapshots()
        ]

    def get_total_volume(self) -> float:
        snapshots = self._parse_snapshots()
        if not snapshots:
            return 0.0
        return snapshots[-1].get("volume", 0.0)

    def get_summary_dict(self) -> dict[str, Any]:
        """Overall summary for display."""
        return {
            "total_ticks": self._tick_count,
            "daily_pnl": self.get_daily_pnl(),
            "total_volume": self.get_total_volume(),
            "total_exposure": self.get_total_exposure(),
            "open_executors": self.get_open_executor_count(),
            "drawdown_pct": self.get_drawdown_pct(),
        }

    def close(self):
        """No-op, kept for API compat."""
        pass

    # ------------------------------------------------------------------
    # Section helpers
    # ------------------------------------------------------------------

    def _get_section(self, name: str) -> str:
        """Extract content between ## {name} and the next ## header."""
        text = self.read_full()
        pattern = rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)"
        m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
        return m.group(1).strip() if m else ""

    def _replace_section(self, name: str, content: str) -> None:
        """Replace the content of a section, preserving other sections."""
        text = self.read_full()
        pattern = rf"(^## {re.escape(name)}\n).*?(?=^## |\Z)"
        replacement = rf"\g<1>{content}\n\n"
        new_text, count = re.subn(
            pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL
        )
        if count == 0:
            new_text = text.rstrip() + f"\n\n## {name}\n{content}\n"
        self._write_journal(new_text)

    def _append_bounded(self, section: str, entry: str, max_lines: int) -> None:
        """Append a line to a section, keeping at most ``max_lines`` of them.

        Overflow is *moved* to ``journal_archive.md`` and replaced by a single
        marker line naming the file and how much went into it, so the journal
        never claims to be a complete record when it isn't. Costs the same one
        read + one write as a plain append.
        """
        lines = [l for l in self._get_section(section).splitlines() if l.strip()]
        marker = ""
        if lines and lines[0].startswith(ARCHIVE_MARKER_PREFIX):
            marker = lines.pop(0)
        lines.append(entry)
        if len(lines) > max_lines:
            cut = len(lines) - max_lines
            dropped, keep = lines[:cut], lines[cut:]
            try:
                self._archive_lines(section, dropped)
            except OSError:
                # Could not park them safely -> do not drop them. The section
                # stays one line over the cap and the trim retries next tick.
                log.exception("journal: archiving %s failed, keeping lines", section)
            else:
                lines = keep
                marker = _next_marker(section, marker, dropped)
        body = [marker, *lines] if marker else lines
        self._replace_section(section, "\n".join(body))

    def _archive_lines(self, section: str, lines: list[str]) -> None:
        """Append trimmed lines to the session's archive sidecar.

        Append-only and outside the journal's read path, so it costs O(lines
        written) rather than O(history) -- the point of the trim. A failure
        here must never take down a trading tick, but it must not silently
        destroy the lines either, so it is logged loudly and the trim is
        abandoned (the caller keeps them in journal.md for another round).
        """
        path = self._session_dir / ARCHIVE_NAME
        header = ""
        if not path.exists():
            header = (
                f"# Journal archive - {self.agent_id}\n\n"
                "Entries trimmed out of journal.md to keep it bounded. "
                "Append-only; nothing here is ever rewritten.\n"
            )
        if section != self._archive_last_section:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            header += f"\n## {section} (archived {now} UTC)\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(header + "\n".join(lines) + "\n")
        self._archive_last_section = section

    def _archived_peak_pnl(self) -> float | None:
        """Peak PnL of the snapshots already archived, from the marker line.

        get_drawdown_pct measures the fall from the session's high-water mark,
        so the trim must not lower that mark -- a forgotten peak would quietly
        make the risk gate less protective than it was yesterday.
        """
        for line in self._get_section("Snapshots").splitlines():
            if not line.startswith(ARCHIVE_MARKER_PREFIX):
                break
            m = re.search(r"peak_pnl=\$([+-]?[\d.]+)", line)
            return float(m.group(1)) if m else None
        return None

    def _append_to_section(self, section: str, entry: str) -> None:
        """Append a line to a section."""
        text = self.read_full()
        marker = f"## {section}\n"
        idx = text.find(marker)
        if idx == -1:
            text += f"\n{marker}{entry}\n"
        else:
            insert_at = idx + len(marker)
            next_section = text.find("\n## ", insert_at)
            if next_section == -1:
                text += entry + "\n"
            else:
                text = text[:next_section] + entry + "\n" + text[next_section:]
        self._write_journal(text)

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def entry_count(self) -> int:
        """Count snapshots."""
        snaps = self.list_snapshots(limit=1000)
        if snaps:
            return len(snaps)
        section = self._get_section("Recent Actions")
        return len([l for l in section.splitlines() if l.startswith("- ")])


def _parse_snapshot_line(line: str) -> dict[str, Any]:
    """Parse one ``record_snapshot`` line into its fields."""
    entry: dict[str, Any] = {}
    for part in line[2:].split(" | "):
        part = part.strip()
        if part.startswith("pnl=$"):
            entry["pnl"] = float(part.replace("pnl=$", "").replace("+", ""))
        elif part.startswith("volume=$"):
            entry["volume"] = float(part.replace("volume=$", "").replace(",", ""))
        elif part.startswith("exposure=$"):
            entry["exposure"] = float(part.replace("exposure=$", ""))
        elif part.startswith("open="):
            entry["open"] = int(part.replace("open=", ""))
        elif re.match(r"\d{4}-\d{2}-\d{2}", part):
            entry["timestamp"] = part
    return entry


def _next_marker(section: str, prev_marker: str, dropped: list[str]) -> str:
    """Build the marker line that stands in for the archived entries.

    It is what a reader sees at the boundary, so it says how many entries are
    missing and where they went -- and, for Snapshots, carries forward the peak
    PnL that get_drawdown_pct would otherwise lose with the lines.
    """
    prev_count = 0
    if prev_marker:
        m = re.search(r"archived (\d+)", prev_marker)
        prev_count = int(m.group(1)) if m else 0
    total = prev_count + len(dropped)

    if section == "Snapshots":
        peaks = [
            entry["pnl"]
            for entry in (_parse_snapshot_line(l) for l in dropped)
            if "pnl" in entry
        ]
        m = re.search(r"peak_pnl=\$([+-]?[\d.]+)", prev_marker or "")
        if m:
            peaks.append(float(m.group(1)))
        peak = f" | peak_pnl=${max(peaks):+.2f}" if peaks else ""
        return (
            f"{ARCHIVE_MARKER_PREFIX}{total} earlier snapshot"
            f"{'' if total == 1 else 's'} to {ARCHIVE_NAME}{peak}"
        )

    noun = section.rstrip("s").lower()
    return (
        f"{ARCHIVE_MARKER_PREFIX}{total} earlier {noun}"
        f"{'' if total == 1 else 's'} to {ARCHIVE_NAME}"
    )


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


def _word_overlap(a: str, b: str) -> float:
    """Fraction of words in common between two strings."""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))
