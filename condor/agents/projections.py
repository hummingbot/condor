"""Projections over RunStore events (§7.1).

The event stream is the one operational history; everything that used to be
parsed back out of ``journal.md`` markdown is a fold over events now. These
are read-side helpers only — nothing here writes.
"""

from __future__ import annotations

from typing import Any


def run_projection(events: list[dict]) -> dict[str, Any]:
    """Fold one run's events into the working-context view the engine and
    tools consume: last state text, recent decisions, tick count, per-tick
    metric series."""
    state_text = ""
    decisions: list[str] = []
    tick_count = 0
    metrics_series: list[dict] = []
    directives_pending: list[str] = []
    errors: list[str] = []
    tool_calls = 0

    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload", {}) or {}
        if etype == "tick_completed":
            tick_count = max(tick_count, int(ev.get("tick", 0) or 0))
            if "metrics" in payload:
                metrics_series.append(payload["metrics"])
        elif etype == "state_snapshot":
            if payload.get("state"):
                state_text = str(payload["state"])
            if payload.get("decision"):
                decisions.append(str(payload["decision"]))
        elif etype == "tool_call":
            tool_calls += 1
        elif etype == "directive":
            text = str(payload.get("text", ""))
            if payload.get("acked"):
                if text in directives_pending:
                    directives_pending.remove(text)
            else:
                directives_pending.append(text)
        elif etype == "error":
            errors.append(str(payload.get("message", "")))

    return {
        "tick_count": tick_count,
        "state": state_text,
        "decisions": decisions,
        "recent_decisions": "\n".join(f"- {d}" for d in decisions[-3:]),
        "metrics_series": metrics_series,
        "pnl_series": [
            {"pnl": m.get("total_pnl", 0.0)} for m in metrics_series
        ],
        "directives_pending": directives_pending,
        "errors": errors,
        "tool_calls": tool_calls,
    }


class RunMetricsTracker:
    """Risk-engine tracker (duck-typed for :class:`RiskEngine.get_state`)
    fed from the native executor store each tick — never from markdown.

    Exposure and open count are venue/record truth (Phase 3); the PnL series
    is per-run, in-memory: engines are memory-only by design (§4.2), so a
    restarted run starts a fresh drawdown baseline.
    """

    def __init__(self) -> None:
        self.total_exposure = 0.0
        self.open_executor_count = 0
        self._pnl_series: list[float] = []

    def update(
        self, *, total_exposure: float, open_count: int, total_pnl: float
    ) -> None:
        self.total_exposure = float(total_exposure or 0.0)
        self.open_executor_count = int(open_count or 0)
        self._pnl_series.append(float(total_pnl or 0.0))

    # -- RiskEngine tracker surface --------------------------------------

    def get_total_exposure(self) -> float:
        return self.total_exposure

    def get_open_executor_count(self) -> int:
        return self.open_executor_count

    def get_pnl_series(self) -> list[dict]:
        return [{"pnl": p} for p in self._pnl_series]

    def get_drawdown_pct(self) -> float:
        if not self._pnl_series:
            return 0.0
        peak = max(self._pnl_series + [0.0])
        current = self._pnl_series[-1]
        if peak <= 0:
            return 0.0
        return max(0.0, (peak - current) / peak * 100)
