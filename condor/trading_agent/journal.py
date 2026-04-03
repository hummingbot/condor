"""JournalManager -- compact persistent memory for trading agents.

Data is organized by strategy and session::

    trading_agents/
        {strategy_slug}/
            agent.md            # strategy definition
            config.yml          # runtime config
            learnings.md        # cross-session learnings
            sessions/
                session_1/
                    journal.md  # summary + decisions + ticks + executors + snapshots
                    snapshots/
                        snapshot_1.md
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "trading_agents"

MAX_LEARNINGS = 20


def resolve_agent_dirs(agent_id: str) -> tuple[Path | None, Path | None]:
    """Derive (session_dir, agent_dir) from an agent_id like 'slug_N' or 'slug_eN'.

    Experiment IDs use the format 'slug_eN' (e.g. 'my_strategy_e3').
    Session IDs use the format 'slug_N' (e.g. 'my_strategy_3').

    Returns (None, None) if the path doesn't exist on disk.
    """
    # agent_id format: {slug}_{session_num} or {slug}_e{experiment_num}
    last_sep = agent_id.rfind("_")
    if last_sep == -1:
        return None, None
    slug = agent_id[:last_sep]
    num_part = agent_id[last_sep + 1:]

    agent_dir = _DATA_ROOT / slug
    if not agent_dir.is_dir():
        return None, None

    # Experiments (e.g. "e3") are flat files, not directories
    if num_part.startswith("e"):
        return None, agent_dir

    try:
        session_num = int(num_part)
    except ValueError:
        return None, None
    session_dir = agent_dir / "sessions" / f"session_{session_num}"
    return session_dir, agent_dir
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

## Active Insights

## Retired Insights
"""

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

<details><summary>Tool Calls ({tool_count})</summary>

{tool_calls}

</details>

## Stats
Duration: {duration:.1f}s
"""


def get_session_dir(strategy_slug: str, session_number: int) -> Path:
    """Build the path for a specific session directory."""
    return _DATA_ROOT / strategy_slug / "sessions" / f"session_{session_number}"


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
    """Determine the next experiment number by scanning experiment_*.md files."""
    experiments_dir = agent_dir / "experiments"
    if not experiments_dir.exists():
        return 1
    existing = []
    for f in experiments_dir.iterdir():
        if f.is_file() and f.suffix == ".md":
            m = re.match(r"experiment_(\d+)\.md", f.name)
            if m:
                existing.append(int(m.group(1)))
    return max(existing, default=0) + 1


EXPERIMENT_TEMPLATE = """\
# Experiment #{num} — {timestamp}
Mode: {execution_mode}

<details><summary>System Prompt ({prompt_len} chars)</summary>

{system_prompt}

</details>

## Executor State
{executors_data}

## Risk State
{risk_state}

## Agent Response
{response_text}

<details><summary>Tool Calls ({tool_count})</summary>

{tool_calls}

</details>

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
) -> Path:
    """Save a single experiment snapshot as a flat .md file."""
    experiments_dir = agent_dir / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)

    # Format risk state
    max_dd = risk_state.get("max_drawdown_pct", -1)
    dd_display = f"{risk_state.get('drawdown_pct', 0):.1f}% / {max_dd:.1f}% limit" if max_dd >= 0 else "disabled"
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
            input_str = json.dumps(tc["input"], indent=2) if isinstance(tc["input"], dict) else str(tc["input"])
            tool_parts.append(f"**Input:**\n```json\n{input_str}\n```")
        if tc.get("output"):
            output_str = str(tc["output"])[:500]
            tool_parts.append(f"**Output:**\n```\n{output_str}\n```")
        tool_parts.append("")

    content = EXPERIMENT_TEMPLATE.format(
        num=experiment_num,
        timestamp=timestamp,
        execution_mode=execution_mode,
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
            self._session_dir = resolved_session if resolved_session else _DATA_ROOT / agent_id
            if not agent_dir and resolved_agent:
                agent_dir = resolved_agent
        self._agent_dir = agent_dir  # For cross-session learnings
        self._path = self._session_dir / "journal.md"
        self._snapshots_dir = self._session_dir / "snapshots"
        self._session_dir.mkdir(parents=True, exist_ok=True)

        # Also support legacy runs/ dir for reading
        self._legacy_runs_dir = self._session_dir / "runs"

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
        """Get the learnings file path."""
        if self._agent_dir:
            return self._agent_dir / "learnings.md"
        # Fallback: try to find learnings in session dir parent
        parent = self._session_dir.parent
        if parent.name == "sessions" or parent.name == "trading_sessions":
            return parent.parent / "learnings.md"
        return None

    def read_learnings(self) -> str:
        """Return the learnings content (cross-session)."""
        path = self._learnings_path()
        if path and path.exists():
            text = path.read_text()
            # Extract Active Insights section
            m = re.search(r"^## Active Insights\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
            if m:
                return m.group(1).strip()
            # Fallback: return everything after the header
            lines = text.strip().splitlines()
            content = [l for l in lines if not l.startswith("# ")]
            return "\n".join(content).strip()
        # Fallback: read from journal Learnings section (legacy)
        return self._get_section("Learnings")

    def append_learning(self, text_content: str) -> None:
        """Add a learning, deduplicating against existing ones."""
        path = self._learnings_path()
        if not path:
            # Fallback to journal section
            self._append_learning_to_journal(text_content)
            return

        if not path.exists():
            path.write_text(LEARNINGS_TEMPLATE)

        full_text = path.read_text()

        # Extract existing learnings from Active Insights
        m = re.search(r"(^## Active Insights\n)(.*?)(?=^## |\Z)", full_text, re.MULTILINE | re.DOTALL)
        if m:
            section_header = m.group(1)
            section_content = m.group(2).strip()
            existing_lines = [l for l in section_content.splitlines() if l.startswith("- ")]
        else:
            existing_lines = []

        # Deduplicate
        normalized_new = _normalize(text_content)
        for line in existing_lines:
            existing_text = re.sub(r"^- (\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] |\[\d{2}:\d{2}\] )?", "", line)
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
            new_text = full_text[:m.start(2)] + new_section + "\n\n" + full_text[m.end(2):]
        else:
            new_text = full_text.rstrip() + f"\n\n## Active Insights\n{new_section}\n"
        path.write_text(new_text)

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
        """Return the entire journal contents."""
        if not self._path.exists():
            return ""
        return self._path.read_text()

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
                    m = re.search(r"^## Agent Response\n(.*?)(?=^## |\Z|^<details)", content, re.MULTILINE | re.DOTALL)
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
                    m = re.search(r"^## Decision\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
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
        max_dd = risk_state.get('max_drawdown_pct', -1)
        dd_display = f"{risk_state.get('drawdown_pct', 0):.1f}% / {max_dd:.1f}% limit" if max_dd >= 0 else "disabled"
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
                input_str = json.dumps(tc["input"], indent=2) if isinstance(tc["input"], dict) else str(tc["input"])
                tool_parts.append(f"**Input:**\n```json\n{input_str}\n```")
            if tc.get("output"):
                output_str = str(tc["output"])[:500]
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
            files = sorted(self._snapshots_dir.glob("snapshot_*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files[:limit]:
                m = re.match(r"snapshot_(\d+)\.md", f.name)
                if m:
                    results.append({
                        "tick": int(m.group(1)),
                        "file": f.name,
                        "size": f.stat().st_size,
                    })

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
            for f in files[:len(files) - MAX_SNAPSHOTS]:
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
        files = sorted(self._legacy_runs_dir.glob("run_*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        results = []
        for f in files[:limit]:
            m = re.match(r"run_(\d+)\.md", f.name)
            if m:
                results.append({
                    "tick": int(m.group(1)),
                    "file": f.name,
                    "size": f.stat().st_size,
                })
        return results

    # Keep for backward compat with existing code that calls these
    def save_run_snapshot(self, **kwargs) -> Path:
        """Legacy compat: redirect to save_full_snapshot with adapted args."""
        return self.save_full_snapshot(
            tick=kwargs.get("tick", 0),
            timestamp=kwargs.get("timestamp", ""),
            system_prompt=kwargs.get("system_prompt", ""),
            response_text=kwargs.get("response_text", ""),
            tool_calls=kwargs.get("tool_calls", []),
            executors_data="\n".join(
                f"### {name}\n{summary}" for name, summary in kwargs.get("core_data_summaries", {}).items()
            ) or "",
            risk_state=kwargs.get("risk_state", {}),
            duration=kwargs.get("duration", 0),
        )

    def read_run_snapshot(self, tick: int) -> str:
        """Legacy compat: try snapshots first, then runs."""
        return self.read_snapshot(tick)

    def list_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Legacy compat: try snapshots first, then runs."""
        return self.list_snapshots(limit=limit)

    # ------------------------------------------------------------------
    # Writing (journal)
    # ------------------------------------------------------------------

    def write_summary(self, tick: int, status: str, pnl: float, open_count: int, last_action: str) -> None:
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
        section = self._get_section("Ticks")
        return len([l for l in section.splitlines() if l.startswith("- tick#")])

    def record_tick(self, response_summary: str = "", actions: int = 0) -> int:
        """Record a tick entry. Returns the new tick number."""
        self._tick_count += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        summary = response_summary[:200].replace("\n", " ")
        entry = f"- tick#{self._tick_count} | {now} | actions={actions} | {summary}"
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

    def update_executor(self, executor_id: str, pnl: float, volume: float, stopped: bool = False) -> None:
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
        self._path.write_text(text)

    # ------------------------------------------------------------------
    # Metric snapshots (inline in journal)
    # ------------------------------------------------------------------

    def record_snapshot(self, total_pnl: float, total_volume: float, open_count: int, position_size: float) -> None:
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
        return results

    def _parse_ticks(self) -> list[dict]:
        section = self._get_section("Ticks")
        results = []
        for line in section.splitlines():
            if not line.startswith("- tick#"):
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
        return results

    def _parse_snapshots(self) -> list[dict]:
        section = self._get_section("Snapshots")
        results = []
        for line in section.splitlines():
            if not line.startswith("- "):
                continue
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
            results.append(entry)
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
        new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL)
        if count == 0:
            new_text = text.rstrip() + f"\n\n## {name}\n{content}\n"
        self._path.write_text(new_text)

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
        self._path.write_text(text)

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

    def get_data_dir(self) -> Path:
        """Return the session data directory."""
        return self._session_dir

    def size_bytes(self) -> int:
        """Current file size."""
        return self._path.stat().st_size if self._path.exists() else 0


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


def migrate_legacy_agents() -> int:
    """Move old hex-ID agent folders to _legacy/.

    Returns the number of folders moved.
    """
    import shutil

    legacy_dir = _DATA_ROOT / "_legacy"
    moved = 0

    if not _DATA_ROOT.exists():
        return 0

    for d in _DATA_ROOT.iterdir():
        if not d.is_dir():
            continue
        if d.name in ("strategies", "_legacy"):
            continue
        if re.fullmatch(r"[0-9a-f]{8}", d.name):
            legacy_dir.mkdir(parents=True, exist_ok=True)
            dest = legacy_dir / d.name
            if not dest.exists():
                shutil.move(str(d), str(dest))
                moved += 1
                log.info("Migrated legacy agent folder %s to _legacy/", d.name)

    return moved
