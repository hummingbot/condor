"""JournalManager -- compact persistent memory for trading agents.

Data is organized by strategy and session::

    data/trading_agents/
        {strategy_slug}/
            agent.md            # strategy definition
            trading_sessions/
                session_1/
                    journal.md  # learnings + summary + ticks + executors + snapshots
                    runs/
                        run_1.md

Legacy agents (pre-session structure) are stored directly under
``data/trading_agents/{hex_id}/`` and still work via the fallback path.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "data" / "trading_agents"

MAX_LEARNINGS = 20
MAX_RUN_SNAPSHOTS = 100

JOURNAL_TEMPLATE = """\
# Journal - {agent_id}

## Learnings

## Summary
No ticks yet.

## Ticks

## Executors

## Snapshots
"""

RUN_SNAPSHOT_TEMPLATE = """\
# Tick #{tick} — {timestamp}

## Environment
- Pair: {pair} | Connector: {connector}
- Frequency: {frequency}s

## Market Data
{market_data}

## Risk State
{risk_state}

## Journal Context
{journal_context}

## Decision
{decision}

## Cost
LLM: ${cost:.4f} | Tick duration: {duration:.1f}s
"""


def get_session_dir(strategy_slug: str, session_number: int) -> Path:
    """Build the path for a specific session directory."""
    return _DATA_ROOT / strategy_slug / "trading_sessions" / f"session_{session_number}"


def next_session_number(agent_dir: Path) -> int:
    """Determine the next session number by scanning existing session_* dirs."""
    sessions_dir = agent_dir / "trading_sessions"
    if not sessions_dir.exists():
        return 1
    existing = [
        int(d.name.split("_", 1)[1])
        for d in sessions_dir.iterdir()
        if d.is_dir() and d.name.startswith("session_")
    ]
    return max(existing, default=0) + 1


class JournalManager:
    """Read/write journal + tracker for one agent session.

    Combines living memory (Learnings, Summary) with execution tracking
    (Ticks, Executors, Snapshots) in a single ``journal.md`` file.
    Per-run detail goes into ``runs/run_N.md``.
    """

    def __init__(
        self,
        agent_id: str,
        strategy_name: str = "",
        strategy_description: str = "",
        session_dir: Path | None = None,
    ):
        self.agent_id = agent_id
        # New path: session_dir is passed explicitly by TickEngine
        # Legacy fallback: data/trading_agents/{agent_id}/
        self._dir = session_dir if session_dir else _DATA_ROOT / agent_id
        self._path = self._dir / "journal.md"
        self._runs_dir = self._dir / "runs"
        self._dir.mkdir(parents=True, exist_ok=True)

        if not self._path.exists():
            self._path.write_text(JOURNAL_TEMPLATE.format(agent_id=agent_id))

        # Cache tick count to avoid re-parsing every call
        self._tick_count = self._count_ticks()

    # ------------------------------------------------------------------
    # Reading (journal)
    # ------------------------------------------------------------------

    def read_full(self) -> str:
        """Return the entire journal contents."""
        if not self._path.exists():
            return ""
        return self._path.read_text()

    def read_learnings(self) -> str:
        """Return the learnings section."""
        return self._get_section("Learnings")

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
        """Return recent decisions from run snapshots.

        Falls back to the old Recent Actions section for backwards compat.
        """
        snapshots = self.list_runs(limit=max_entries)
        if snapshots:
            parts = []
            for snap in snapshots:
                content = self.read_run_snapshot(snap["tick"])
                if content:
                    m = re.search(r"^## Decision\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
                    if m:
                        decision = m.group(1).strip()
                        parts.append(f"- **#{snap['tick']}** {decision}")
            if parts:
                return "\n".join(parts)

        content = self._get_section("Recent Actions")
        if not content:
            content = self._get_section("Actions Log")
        return content

    # ------------------------------------------------------------------
    # Run Snapshots
    # ------------------------------------------------------------------

    def save_run_snapshot(
        self,
        tick: int,
        timestamp: str,
        config: dict[str, Any],
        core_data_summaries: dict[str, str],
        risk_state: dict[str, Any],
        learnings: str,
        recent_actions: str,
        response_text: str,
        cost: float,
        duration: float,
    ) -> Path:
        """Write a per-run snapshot to runs/run_N.md."""
        self._runs_dir.mkdir(parents=True, exist_ok=True)

        market_data = "\n".join(
            f"### {name}\n{summary}" for name, summary in core_data_summaries.items()
        ) or "No data available."

        risk_lines = [
            f"- Daily PnL: ${risk_state.get('daily_pnl', 0):+.2f} / -${risk_state.get('max_daily_loss', 50):.2f} limit",
            f"- Position Size: ${risk_state.get('total_exposure', 0):.2f} / ${risk_state.get('max_position_size', 500):.2f} limit",
            f"- Open Executors: {risk_state.get('executor_count', 0)} / {risk_state.get('max_open_executors', 5)} limit",
            f"- Status: {'BLOCKED - ' + risk_state.get('block_reason', '') if risk_state.get('is_blocked') else 'ACTIVE'}",
        ]
        risk_text = "\n".join(risk_lines)

        journal_parts = []
        if learnings:
            journal_parts.append(f"### Learnings\n{learnings}")
        if recent_actions:
            journal_parts.append(f"### Recent Actions\n{recent_actions}")
        journal_context = "\n\n".join(journal_parts) or "No prior context."

        decision = response_text[:500] if response_text else "No response."

        content = RUN_SNAPSHOT_TEMPLATE.format(
            tick=tick,
            timestamp=timestamp,
            pair=config.get("trading_pair", "unknown"),
            connector=config.get("connector_name", "unknown"),
            frequency=config.get("frequency_sec", 60),
            market_data=market_data,
            risk_state=risk_text,
            journal_context=journal_context,
            decision=decision,
            cost=cost,
            duration=duration,
        )

        path = self._runs_dir / f"run_{tick}.md"
        path.write_text(content)

        self._cleanup_old_runs()
        return path

    def read_run_snapshot(self, tick: int) -> str:
        """Read a specific run snapshot by tick number."""
        path = self._runs_dir / f"run_{tick}.md"
        if path.exists():
            return path.read_text()
        return ""

    def list_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent run snapshots, newest first."""
        if not self._runs_dir.exists():
            return []

        files = sorted(self._runs_dir.glob("run_*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
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

    def get_recent_decisions(self, count: int = 3) -> str:
        """Get the Decision section from the last N run snapshots."""
        snapshots = self.list_runs(limit=count)
        if not snapshots:
            return ""

        parts = []
        for snap in reversed(snapshots):  # chronological order
            content = self.read_run_snapshot(snap["tick"])
            if not content:
                continue
            m = re.search(r"^## Decision\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
            if m:
                decision = m.group(1).strip()
                tm = re.search(r"^# Tick #\d+ — (.+)$", content, re.MULTILINE)
                ts = tm.group(1) if tm else ""
                parts.append(f"**#{snap['tick']}** ({ts}): {decision[:200]}")

        return "\n".join(parts)

    def _cleanup_old_runs(self) -> None:
        """Remove oldest run snapshots if over MAX_RUN_SNAPSHOTS."""
        if not self._runs_dir.exists():
            return
        files = sorted(self._runs_dir.glob("run_*.md"))
        if len(files) > MAX_RUN_SNAPSHOTS:
            for f in files[: len(files) - MAX_RUN_SNAPSHOTS]:
                f.unlink()

    # ------------------------------------------------------------------
    # Writing (journal)
    # ------------------------------------------------------------------

    def write_summary(self, tick: int, status: str, pnl: float, open_count: int, last_action: str) -> None:
        """Update the Summary section with a one-liner about the last tick."""
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
        """Record an action. Kept for MCP tool compat."""
        now = datetime.now(timezone.utc).strftime("%H:%M")
        parts = [f"- **#{tick}** ({now}) {action}"]
        if reasoning:
            parts[0] += f" — {reasoning}"
        if risk_note:
            parts[0] += f" [{risk_note}]"
        entry = parts[0]

        # Only write to Recent Actions if the section exists (old journals)
        section = self._get_section("Recent Actions")
        if section or "## Recent Actions" in self.read_full():
            lines = [l for l in section.splitlines() if l.strip()]
            lines.append(entry)
            if len(lines) > 10:
                lines = lines[-10:]
            self._replace_section("Recent Actions", "\n".join(lines))

    def append_learning(self, text_content: str) -> None:
        """Add a learning, deduplicating against existing ones."""
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

    def append_error(self, error: str) -> None:
        """Append an error as an action entry."""
        now = datetime.now(timezone.utc).strftime("%H:%M")
        section = self._get_section("Recent Actions")
        if section or "## Recent Actions" in self.read_full():
            lines = [l for l in section.splitlines() if l.strip()]
            lines.append(f"- **error** ({now}) {error}")
            if len(lines) > 10:
                lines = lines[-10:]
            self._replace_section("Recent Actions", "\n".join(lines))

    # ------------------------------------------------------------------
    # Tick tracking (merged from ExecutorTracker)
    # ------------------------------------------------------------------

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def _count_ticks(self) -> int:
        section = self._get_section("Ticks")
        return len([l for l in section.splitlines() if l.startswith("- tick#")])

    def record_tick(self, response_summary: str = "", cost: float = 0, actions: int = 0) -> int:
        """Record a tick entry. Returns the new tick number."""
        self._tick_count += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        summary = response_summary[:200].replace("\n", " ")
        entry = f"- tick#{self._tick_count} | {now} | cost=${cost:.4f} | actions={actions} | {summary}"
        self._append_to_section("Ticks", entry)
        return self._tick_count

    # ------------------------------------------------------------------
    # Executor tracking (merged from ExecutorTracker)
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
    # Snapshots (merged from ExecutorTracker)
    # ------------------------------------------------------------------

    def record_snapshot(self, total_pnl: float, total_volume: float, open_count: int, position_size: float) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = (
            f"- {now} | pnl=${total_pnl:+.2f} | volume=${total_volume:,.0f} "
            f"| open={open_count} | exposure=${position_size:.2f}"
        )
        self._append_to_section("Snapshots", entry)

    # ------------------------------------------------------------------
    # Queries (used by RiskEngine — previously on ExecutorTracker)
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
                elif part.startswith("cost=$"):
                    entry["cost"] = float(part.replace("cost=$", ""))
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

    def get_daily_cost(self) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total = 0.0
        for tick in self._parse_ticks():
            ts = tick.get("timestamp", "")
            if ts.startswith(today):
                total += tick.get("cost", 0)
        return total

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
        """Overall summary for display (replaces ExecutorTracker.get_summary)."""
        return {
            "total_ticks": self._tick_count,
            "daily_pnl": self.get_daily_pnl(),
            "total_volume": self.get_total_volume(),
            "total_exposure": self.get_total_exposure(),
            "open_executors": self.get_open_executor_count(),
            "daily_cost": self.get_daily_cost(),
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
        """Count run snapshots (or recent action entries for old journals)."""
        runs = self.list_runs(limit=1000)
        if runs:
            return len(runs)
        section = self._get_section("Recent Actions")
        return len([l for l in section.splitlines() if l.startswith("- ")])

    def get_data_dir(self) -> Path:
        """Return the agent's data directory."""
        return self._dir

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
