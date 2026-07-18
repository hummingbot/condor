"""Run metrics tracking for the risk engine.

The engine's working context (state line, recent decisions) is plain
in-engine memory now — runs don't survive the process (§4.2), so nothing
folds it back out of the run record.
"""

from __future__ import annotations


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
