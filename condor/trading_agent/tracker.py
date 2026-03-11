"""Executor tracker -- markdown-based PnL, volume, and executor history.

Each agent instance stores its tracker at
``data/trading_agents/{agent_id}/tracker.md``.

The file has three sections with parseable structured entries:
- Ticks: one line per tick with timestamp, cost, summary
- Executors: one block per executor with config, PnL, status
- Snapshots: one line per snapshot with PnL, volume, exposure
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "data" / "trading_agents"

TRACKER_TEMPLATE = """\
# Tracker - {agent_id}

## Ticks

## Executors

## Snapshots
"""


class ExecutorTracker:
    """Tracks executor lifecycle, PnL, and volume in a markdown file."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._dir = _DATA_ROOT / agent_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "tracker.md"

        if not self._path.exists():
            self._path.write_text(TRACKER_TEMPLATE.format(agent_id=agent_id))

        self._tick_count = self._count_ticks()

    def close(self):
        pass  # No resource to release for flat files

    @property
    def tick_count(self) -> int:
        return self._tick_count

    # ------------------------------------------------------------------
    # Internal: section read/write
    # ------------------------------------------------------------------

    def _read(self) -> str:
        return self._path.read_text() if self._path.exists() else ""

    def _get_section(self, name: str) -> str:
        """Extract content between ## {name} and the next ## header."""
        text = self._read()
        pattern = rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)"
        m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
        return m.group(1).strip() if m else ""

    def _append_to_section(self, section: str, entry: str) -> None:
        """Append a line/block to a section."""
        text = self._read()
        marker = f"## {section}\n"
        idx = text.find(marker)
        if idx == -1:
            text += f"\n{marker}{entry}\n"
        else:
            insert_at = idx + len(marker)
            # Find end of section (next ## or EOF)
            next_section = text.find("\n## ", insert_at)
            if next_section == -1:
                text += entry + "\n"
            else:
                text = text[:next_section] + entry + "\n" + text[next_section:]
        self._path.write_text(text)

    def _count_ticks(self) -> int:
        section = self._get_section("Ticks")
        return len([l for l in section.splitlines() if l.startswith("- tick#")])

    # ------------------------------------------------------------------
    # Tick tracking
    # ------------------------------------------------------------------

    def record_tick(self, response_summary: str = "", cost: float = 0, actions: int = 0) -> int:
        self._tick_count += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        summary = response_summary[:200].replace("\n", " ")
        entry = f"- tick#{self._tick_count} | {now} | cost=${cost:.4f} | actions={actions} | {summary}"
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
        text = self._read()
        # Find the executor line
        pattern = rf"(- executor={re.escape(executor_id)} \|.*)"
        m = re.search(pattern, text)
        if not m:
            return

        old_line = m.group(1)
        # Update pnl and volume
        new_line = re.sub(r"pnl=[^ |]*", f"pnl={pnl:.2f}", old_line)
        new_line = re.sub(r"volume=[^ |]*", f"volume={volume:.2f}", new_line)
        if stopped:
            new_line = re.sub(r"status=\w+", "status=closed", new_line)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            new_line += f" | stopped={now}"

        text = text.replace(old_line, new_line)
        self._path.write_text(text)

    # ------------------------------------------------------------------
    # Snapshots
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
        """Parse all executor entries from the Executors section."""
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
        """Parse all tick entries from the Ticks section."""
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
                    # Could be timestamp or summary
                    if re.match(r"\d{4}-\d{2}-\d{2}", part.strip()):
                        entry["timestamp"] = part.strip()
                    else:
                        entry["summary"] = part.strip()
            results.append(entry)
        return results

    def _parse_snapshots(self) -> list[dict]:
        """Parse all snapshot entries."""
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
        """Aggregate PnL from executors created today."""
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
        """Sum of position sizes for open executors."""
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
        """Simple drawdown: peak PnL vs current PnL from snapshots."""
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
        """Sum of LLM costs for today's ticks."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total = 0.0
        for tick in self._parse_ticks():
            ts = tick.get("timestamp", "")
            if ts.startswith(today):
                total += tick.get("cost", 0)
        return total

    def get_pnl_series(self, hours: int = 24) -> list[dict]:
        """PnL snapshots for charting."""
        return [
            {"timestamp": s.get("timestamp", ""), "pnl": s.get("pnl", 0)}
            for s in self._parse_snapshots()
        ]

    def get_total_volume(self) -> float:
        """Get latest total volume from snapshots."""
        snapshots = self._parse_snapshots()
        if not snapshots:
            return 0.0
        return snapshots[-1].get("volume", 0.0)

    def get_summary(self) -> dict[str, Any]:
        """Overall summary for display."""
        return {
            "total_ticks": self._tick_count,
            "daily_pnl": self.get_daily_pnl(),
            "total_volume": self.get_total_volume(),
            "total_exposure": self.get_total_exposure(),
            "open_executors": self.get_open_executor_count(),
            "daily_cost": self.get_daily_cost(),
            "drawdown_pct": self.get_drawdown_pct(),
        }
