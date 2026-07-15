"""JournalManager -- compact persistent memory for trading agents.

All operational history lives at the **agent** level; strategies are pure
playbook templates (refactor-01b)::

    agents/
        {agent_slug}/
            AGENT.md               # identity + domain knowledge
            learnings.md           # agent-level, all strategies & run kinds
            experiments/           # experiment snapshots ({date}-eN.md)
            delegations/           # delegation transcripts ({date}-dN.md)
            sessions/              # ONLY real (stateful) sessions
                session_1/
                    meta.yml       # strategy + status + timestamps
                    journal.md     # summary / decisions / ticks / executors
                    config.yml     # frozen launch config
                    snapshots/     # snapshot_N.md
            strategies/
                {strategy_slug}.md # playbook body + default_config — nothing else

Session identity is ``"{agent_slug}_{N}"`` (``"..._e{N}"`` for experiments).
The strategy a tick session ran is *metadata* (``meta.yml``), not part of
the address.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "agents"


@contextmanager
def _file_lock(path: Path):
    """Inter-process mutex for read-modify-write on a shared journal file.

    The engine (main process) and the MCP subprocess both mutate the same
    markdown files through independent JournalManager instances; unlocked
    read-modify-write loses whichever update lands first. flock on a sidecar
    ``.lock`` file serializes them (works across processes on the same host).
    """
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_write(path: Path, text: str) -> None:
    """Write via tmp+rename so a concurrent reader never sees a torn file."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text)
    tmp.replace(path)

MAX_LEARNINGS = 20

_NUM_RE = re.compile(r"[-+]?\d*\.?\d+")


def _num(text: Any) -> float | None:
    """Extract the leading numeric value from a journal token, tolerating a
    currency symbol and/or a trailing unit — e.g. ``"$0.02"``, ``"0.021 SOL"``,
    ``"+1.5"``, ``"1,234.5 SOL"`` all parse. Returns ``None`` when no number is
    present. Journal snapshots are quote-currency-agnostic (an agent may write
    SOL, USD, etc.), so risk-state parsing MUST NOT assume a ``$`` prefix or die
    on a unit suffix: a parse crash here blocks the agent from managing its own
    open positions.
    """
    if text is None:
        return None
    m = _NUM_RE.search(str(text).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None

# Session id suffix: "_{N}" for sessions, "_e{N}" for experiments.
_AGENT_ID_RE = re.compile(r"^(?P<slug>.+)_(?P<exp>e?)(?P<num>\d+)$")


def split_agent_id(agent_id: str) -> tuple[str, int, bool] | None:
    """Parse ``"{slug}_{N}"`` / ``"{slug}_e{N}"`` → (slug, num, is_experiment).

    Returns None when the id doesn't match the single supported format.
    """
    m = _AGENT_ID_RE.match(agent_id)
    if not m:
        return None
    return m.group("slug"), int(m.group("num")), bool(m.group("exp"))


def resolve_agent_dirs(agent_id: str) -> tuple[Path | None, Path | None]:
    """Derive (session_dir, agent_dir) from an agent_id.

    ``agent_dir`` is ``agents/{slug}/`` (holds ``sessions/``, ``learnings.md``,
    ``experiments/``). Experiments have no session dir → (None, agent_dir).
    Returns (None, None) if the agent dir doesn't exist on disk.
    """
    parsed = split_agent_id(agent_id)
    if parsed is None:
        return None, None
    slug, num, is_experiment = parsed

    agent_dir = _DATA_ROOT / slug
    if not agent_dir.is_dir():
        return None, None

    if is_experiment:
        return None, agent_dir
    return agent_dir / "sessions" / f"session_{num}", agent_dir


# ---------------------------------------------------------------------------
# Session allocation + meta.yml
# ---------------------------------------------------------------------------

SESSION_META_FILE = "meta.yml"


def allocate_session_dir(agent_dir: Path) -> tuple[int, Path]:
    """Atomically allocate the next ``sessions/session_N`` directory.

    Uses mkdir (exist_ok=False) as the allocation lock so two concurrent
    starts under the same agent can never claim the same number.
    """
    sessions_dir = agent_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    n = next_session_number(agent_dir)
    while True:
        candidate = sessions_dir / f"session_{n}"
        try:
            candidate.mkdir()
            return n, candidate
        except FileExistsError:
            n += 1


def write_session_meta(session_dir: Path, meta: dict[str, Any]) -> None:
    """Write/overwrite a session's ``meta.yml`` (atomic replace)."""
    _atomic_write(
        session_dir / SESSION_META_FILE,
        yaml.dump(meta, default_flow_style=False, sort_keys=False),
    )


def read_session_meta(session_dir: Path) -> dict[str, Any]:
    """Read a session's ``meta.yml``; {} when absent/unparseable (crash husk)."""
    path = session_dir / SESSION_META_FILE
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        log.warning("Unparseable meta.yml at %s", path)
        return {}


def finalize_session_meta(session_dir: Path, status: str, **extra: Any) -> None:
    """Update a session's meta.yml with a terminal status + ended_at."""
    meta = read_session_meta(session_dir)
    meta["status"] = status
    meta["ended_at"] = datetime.now(timezone.utc).isoformat()
    meta.update(extra)
    write_session_meta(session_dir, meta)


def mark_interrupted_sessions(agents_root: Path) -> list[str]:
    """Startup sweep: any session whose meta.yml still says ``running`` has no
    engine (engines are memory-only and did not survive the restart) — mark it
    ``interrupted`` so list_sessions/track records stop reporting a phantom
    live session. Returns the agent_ids marked."""
    marked: list[str] = []
    if not agents_root.exists():
        return marked
    for agent_dir in agents_root.iterdir():
        sessions_dir = agent_dir / "sessions"
        if not sessions_dir.is_dir():
            continue
        for d in sessions_dir.iterdir():
            if not d.is_dir() or not d.name.startswith("session_"):
                continue
            meta = read_session_meta(d)
            if meta.get("status") != "running":
                continue
            try:
                finalize_session_meta(d, "interrupted")
                marked.append(f"{agent_dir.name}/{d.name}")
            except Exception:
                log.exception("could not mark %s interrupted", d)
    return marked


MAX_SNAPSHOTS = 100

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


def get_session_dir(agent_slug: str, session_number: int) -> Path:
    """Build the path for a specific session directory."""
    return _DATA_ROOT / agent_slug / "sessions" / f"session_{session_number}"


def next_session_number(agent_dir: Path) -> int:
    """Determine the next session number by scanning existing session_* dirs."""
    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.exists():
        return 1
    existing = []
    for d in sessions_dir.iterdir():
        if not d.is_dir() or not d.name.startswith("session_"):
            continue
        try:
            existing.append(int(d.name.split("_", 1)[1]))
        except ValueError:
            continue
    return max(existing, default=0) + 1


# Flat one-shot artifact filenames (refactor-07): {date}-e{N}.md experiments,
# {date}-d{N}.md delegations — date-first so ls sorts chronologically; the
# short id suffix stays the lookup handle.
_EXPERIMENT_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-e(\d+)\.md$")
_DELEGATION_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-d(\d+)\.md$")


def next_experiment_number(agent_dir: Path) -> int:
    """Determine the next experiment number by scanning experiments/*-eN.md."""
    existing = []
    d = agent_dir / "experiments"
    if d.exists():
        for f in d.iterdir():
            m = _EXPERIMENT_FILE_RE.match(f.name)
            if m:
                existing.append(int(m.group(2)))
    return max(existing, default=0) + 1


def next_delegation_number(agent_dir: Path) -> int:
    """Determine the next delegation number by scanning delegations/*-dN.md."""
    existing = []
    d = agent_dir / "delegations"
    if d.exists():
        for f in d.iterdir():
            m = _DELEGATION_FILE_RE.match(f.name)
            if m:
                existing.append(int(m.group(2)))
    return max(existing, default=0) + 1


def find_delegation_file(agent_dir: Path, num: int) -> Path | None:
    """Locate the transcript of delegation ``num`` (filename carries the date)."""
    d = agent_dir / "delegations"
    if not d.exists():
        return None
    for f in d.iterdir():
        m = _DELEGATION_FILE_RE.match(f.name)
        if m and int(m.group(2)) == num:
            return f
    return None


def allocate_delegation_file(agent_dir: Path, started_at: str) -> tuple[int, Path]:
    """Reserve the next delegation number by exclusively creating its file.

    Mirrors ``allocate_session_dir``'s mkdir-atomicity: the ``touch(exist_ok=
    False)`` claims the number, so concurrent starts never collide.
    """
    d = agent_dir / "delegations"
    d.mkdir(parents=True, exist_ok=True)
    date = started_at[:10]
    while True:
        num = next_delegation_number(agent_dir)
        path = d / f"{date}-d{num}.md"
        try:
            path.touch(exist_ok=False)
            return num, path
        except FileExistsError:
            continue


# Line format written by JournalManager.record_tick; count_journal_ticks and
# _count_ticks parse it back, so the format has a single owner here.
TICK_LINE_PREFIX = "- tick#"


def count_journal_ticks(journal_path: Path) -> int:
    """Count tick entries in a session's journal.md (lines record_tick writes)."""
    if not journal_path.exists():
        return 0
    text = journal_path.read_text(errors="replace")
    return sum(1 for line in text.splitlines() if line.startswith(TICK_LINE_PREFIX))


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
    experiments_dir = agent_dir / "experiments"
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

    path = experiments_dir / f"{timestamp[:10]}-e{experiment_num}.md"
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
        strategy_name: str = "",
        strategy_description: str = "",
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

        # Read cache: journal.md text keyed by (mtime_ns, size), plus parsed
        # sections derived from that text. Invalidated when the stamp changes
        # (external writers, e.g. the MCP journal_write tool) or explicitly on
        # our own writes (mtime granularity could otherwise mask them).
        self._cache_stamp: tuple[int, int] | None = None
        self._cache_text: str = ""
        self._parsed_cache: dict[str, list[dict]] = {}

        if not self._path.exists():
            self._path.write_text(JOURNAL_TEMPLATE.format(agent_id=agent_id))

        # Ensure learnings.md exists at agent level
        if self._agent_dir:
            learnings_path = self._agent_dir / "learnings.md"
            if not learnings_path.exists():
                learnings_path.write_text(LEARNINGS_TEMPLATE)

        self._tick_count = self._count_ticks()

    # ------------------------------------------------------------------
    # Learnings (cross-session, stored in agent_dir/learnings.md)
    # ------------------------------------------------------------------

    def _learnings_path(self) -> Path | None:
        """Get the learnings file path (agent-level)."""
        if self._agent_dir:
            return self._agent_dir / "learnings.md"
        # Fallback: derive the agent dir from the session dir layout
        parent = self._session_dir.parent
        if parent.name == "sessions":
            return parent.parent / "learnings.md"
        return None

    def read_learnings(self) -> str:
        """Return the learnings content (cross-session), grouped by category."""
        path = self._learnings_path()
        if not path or not path.exists():
            return ""
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
        return "\n\n".join(parts)

    def append_learning(
        self, text_content: str, category: str = "", strategy: str = ""
    ) -> None:
        """Add a learning under a category, deduplicating against existing ones.

        Args:
            text_content: The learning text.
            category: "market" or "execution". Defaults to "market".
            strategy: Provenance — the strategy slug of the tick session that
                learned it. Learnings pool at the agent level, so entries are
                prefixed ``[{strategy}] …`` to keep mixing visible/filterable.
                Empty for delegation/consult learnings (no strategy scope).
        """
        path = self._learnings_path()
        if not path:
            log.warning(
                "append_learning: no agent dir for %s; learning dropped", self.agent_id
            )
            return

        if strategy:
            text_content = f"[{strategy}] {text_content}"

        # learnings.md is shared by every session/consult/delegation of the
        # agent, across processes — serialize the read-modify-write.
        with _file_lock(path):
            self._append_learning_locked(path, text_content, category)

    def _append_learning_locked(
        self, path: Path, text_content: str, category: str
    ) -> None:
        if not path.exists():
            path.write_text(LEARNINGS_TEMPLATE)

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
        _atomic_write(path, new_text)

    def promote_learning(self, text_content: str) -> bool:
        """Move a learning line to the ``## Promoted`` section (agent-level)."""
        path = self._learnings_path()
        if not path:
            return False
        return promote_agent_learning(path.parent, text_content)

    # ------------------------------------------------------------------
    # Reading (journal)
    # ------------------------------------------------------------------

    def read_full(self) -> str:
        """Return the entire journal contents (cached until the file changes)."""
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

    def _write_journal(self, text: str) -> None:
        """Write journal.md (atomic replace) and invalidate the read cache."""
        _atomic_write(self._path, text)
        self._cache_stamp = None
        self._parsed_cache.clear()

    def read_summary(self) -> str:
        """Return the summary section."""
        return self._get_section("Summary")

    def read_state(self) -> str:
        """Return the summary (alias for read_summary, kept for the MCP tool)."""
        return self._get_section("Summary")

    def read_recent(self, max_entries: int = 10) -> str:
        """Return recent decisions from snapshots."""
        parts = []
        for snap in self.list_snapshots(limit=max_entries):
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
        return "\n".join(parts)

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
        return ""

    def list_snapshots(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent snapshots, newest first."""
        results = []
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
        """Overwrite the Summary section (the MCP journal_write "state" entry)."""
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
        section = self._get_section("Ticks")
        return len([l for l in section.splitlines() if l.startswith(TICK_LINE_PREFIX)])

    def record_tick(self, response_summary: str = "", actions: int = 0) -> int:
        """Record a tick entry. Returns the new tick number."""
        self._tick_count += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        summary = response_summary[:200].replace("\n", " ")
        entry = (
            f"{TICK_LINE_PREFIX}{self._tick_count} | {now} "
            f"| actions={actions} | {summary}"
        )
        self._append_to_section("Ticks", entry)
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

    def sync_open_executors(self, executors: list[dict]) -> None:
        """Rewrite the Executors section from the CURRENT open executors.

        Gateway-native executors aren't tracked via track_executor/update_executor
        (nothing calls those on the native path), which left the section empty and
        made ``get_open_executor_count``/``get_total_exposure`` — read by the risk
        engine — report zero for an agent that is actually holding positions. This
        rewrites the section each tick from the live native_executors data so both
        the journal view and risk accounting are correct. Reflects OPEN positions
        (the risk-relevant set); closed trades stay in the Decisions log.
        """
        lines = []
        for e in executors:
            pnl = e.get("pnl")
            pnl_str = f"{pnl:.4f}" if isinstance(pnl, (int, float)) else "n/a"
            try:
                amount = float(e.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            lines.append(
                f"- executor={e.get('id', '?')} | type={e.get('type', 'position')} "
                f"| {e.get('pair', '?')} | amount=${amount:.4f} "
                f"| status=open | pnl={pnl_str} | state={e.get('state', '')}"
            )
        self._replace_section("Executors", "\n".join(lines))

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
        self._append_to_section("Snapshots", entry)

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

    def _parse_ticks(self) -> list[dict]:
        self.read_full()  # refresh parsed cache if the file changed
        cached = self._parsed_cache.get("ticks")
        if cached is not None:
            return cached
        section = self._get_section("Ticks")
        results = []
        for line in section.splitlines():
            if not line.startswith(TICK_LINE_PREFIX):
                continue
            entry: dict[str, Any] = {}
            parts = line[2:].split(" | ")
            for part in parts:
                if part.startswith("tick#"):
                    entry["tick"] = int(part.replace("tick#", ""))
                elif part.startswith("actions="):
                    entry["actions"] = int(part.replace("actions=", ""))
                else:
                    if re.match(r"\d{4}-\d{2}-\d{2}", part.strip()):
                        entry["timestamp"] = part.strip()
                    else:
                        entry["summary"] = part.strip()
            results.append(entry)
        self._parsed_cache["ticks"] = results
        return results

    def _parse_snapshots(self) -> list[dict]:
        self.read_full()  # refresh parsed cache if the file changed
        cached = self._parsed_cache.get("snapshots")
        if cached is not None:
            return cached
        section = self._get_section("Snapshots")
        results = []
        for line in section.splitlines():
            if not line.startswith("- "):
                continue
            entry: dict[str, Any] = {}
            for part in line[2:].split(" | "):
                part = part.strip()
                if part.startswith("pnl="):
                    val = _num(part)
                    if val is not None:
                        entry["pnl"] = val
                elif part.startswith("volume="):
                    val = _num(part)
                    if val is not None:
                        entry["volume"] = val
                elif part.startswith("exposure="):
                    val = _num(part)
                    if val is not None:
                        entry["exposure"] = val
                elif part.startswith("open="):
                    val = _num(part)
                    if val is not None:
                        entry["open"] = int(val)
                elif re.match(r"\d{4}-\d{2}-\d{2}", part):
                    entry["timestamp"] = part
            results.append(entry)
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
                val = _num(ex.get("amount"))
                if val is not None:
                    total += val
        return total

    def get_open_executor_count(self) -> int:
        return sum(1 for ex in self._parse_executors() if ex.get("status") == "open")

    def get_drawdown_pct(self) -> float:
        snapshots = self._parse_snapshots()
        if not snapshots:
            return 0.0
        pnls = [s.get("pnl", 0) for s in snapshots]
        peak = max(pnls)
        current = pnls[-1]
        if peak <= 0:
            return 0.0
        return max(0, (peak - current) / peak * 100)

    def get_pnl_series(self, hours: int = 24) -> list[dict]:
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
        return len(self.list_snapshots(limit=1000))

    def get_data_dir(self) -> Path:
        """Return the session data directory."""
        return self._session_dir

    def size_bytes(self) -> int:
        """Current file size."""
        return self._path.stat().st_size if self._path.exists() else 0


def render_transcript(events: list[dict]) -> str:
    """Render a chronological run transcript (thoughts, tool calls, text).

    Shared by delegation and consult session persistence (``transcript.md``).
    """
    parts: list[str] = []
    tool_n = 0
    for ev in events:
        kind = ev.get("type")
        if kind == "thought":
            text = (ev.get("text") or "").strip()
            if text:
                quoted = "\n".join(f"> {line}" for line in text.splitlines())
                parts.append(f"💭 **Reasoning**\n\n{quoted}")
        elif kind == "text":
            text = (ev.get("text") or "").strip()
            if text:
                parts.append(f"💬 {text}")
        elif kind == "tool":
            tool_n += 1
            name = ev.get("name") or "unknown"
            status = ev.get("status") or ""
            block = [f"🔧 **{tool_n}. {name}** ({status})"]
            if ev.get("input"):
                inp = ev["input"]
                inp_str = (
                    json.dumps(inp, indent=2, default=str)
                    if isinstance(inp, dict)
                    else str(inp)
                )
                block.append(f"**Input:**\n```json\n{inp_str}\n```")
            if ev.get("output"):
                out_str = str(ev["output"])
                if len(out_str) > 2000:
                    out_str = out_str[:2000] + "\n… (truncated)"
                block.append(f"**Output:**\n```\n{out_str}\n```")
            parts.append("\n".join(block))
    return "\n\n".join(parts)


def promote_agent_learning(agent_dir: Path, text_content: str) -> bool:
    """Move a learning line to learnings.md's ``## Promoted`` section.

    Used after folding a learning into a skill: the entry stops occupying the
    capped active sections but stays on record (provenance for what fed which
    skill). Learnings are agent-level, so this takes the bare agent dir — no
    session handle required. Matches by normalized text against the active
    category sections. Returns False when nothing matched.
    """
    path = agent_dir / "learnings.md"
    if not path.exists():
        return False
    with _file_lock(path):
        return _promote_agent_learning_locked(path, text_content)


def _promote_agent_learning_locked(path: Path, text_content: str) -> bool:
    full_text = path.read_text()
    target = _normalize(text_content)

    for cat_header in LEARNING_CATEGORIES.values():
        pattern = rf"(^## {re.escape(cat_header)}\n)(.*?)(?=^## |\Z)"
        m = re.search(pattern, full_text, re.MULTILINE | re.DOTALL)
        if not m:
            continue
        lines = m.group(2).strip().splitlines()
        for line in lines:
            if not line.startswith("- "):
                continue
            stripped = re.sub(
                r"^- (\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] |\[\d{2}:\d{2}\] )?",
                "",
                line,
            )
            norm = _normalize(stripped)
            if norm == target or (target and target in norm):
                remaining = [l for l in lines if l != line]
                new_text = (
                    full_text[: m.start(2)]
                    + ("\n".join(remaining) + "\n\n" if remaining else "")
                    + full_text[m.end(2) :]
                )
                if "## Promoted" in new_text:
                    new_text = re.sub(
                        r"(^## Promoted\n)",
                        rf"\g<1>{line}\n",
                        new_text,
                        count=1,
                        flags=re.MULTILINE,
                    )
                else:
                    new_text = new_text.rstrip() + f"\n\n## Promoted\n{line}\n"
                _atomic_write(path, new_text)
                return True
    return False


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
