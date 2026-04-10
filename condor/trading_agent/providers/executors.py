"""Core data provider: active executors with PnL and volume.

Delegates number-crunching to ``condor.trading_agent.performance`` so that
live ticks and the web API always agree.
"""

from __future__ import annotations

from typing import Any

from . import register_provider
from .base import BaseProvider, ProviderResult


class ExecutorsProvider(BaseProvider):
    name = "executors"
    is_core = True

    async def execute(
        self, client: Any, config: dict, agent_id: str = ""
    ) -> ProviderResult:
        from condor.trading_agent.performance import fetch_agent_performance

        if not agent_id:
            return ProviderResult(
                name=self.name,
                data={"executors": [], "total_pnl": 0, "total_volume": 0},
                summary="Active Executors: no agent_id provided",
            )

        try:
            perf = await fetch_agent_performance(client, agent_id)
        except Exception as e:
            return ProviderResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"Active Executors: failed to fetch ({e})",
            )

        running = [e for e in perf.executors if e["status"] == "RUNNING"]
        lines = [
            (
                f"Active Executors ({len(running)}) [agent: {agent_id}]:"
                if running
                else f"Active Executors: none running (agent: {agent_id})"
            )
        ]
        for r in running:
            side = r.get("side") or ""
            lines.append(
                f"  {r['pair']} {side} ${r['pnl']:+.2f} (V:${r['volume']:,.0f})"
            )
        lines.append(
            f"  Realized: ${perf.realized_pnl:+.2f} | "
            f"Unrealized: ${perf.unrealized_pnl:+.2f} | "
            f"Total PnL: ${perf.total_pnl:+.2f} | "
            f"Volume: ${perf.volume:,.0f}"
        )

        total_exposure = sum(r.get("amount", 0) for r in running)

        return ProviderResult(
            name=self.name,
            data={
                "executors": running,
                "all_executors": perf.executors,
                "total_pnl": perf.total_pnl,
                "realized_pnl": perf.realized_pnl,
                "unrealized_pnl": perf.unrealized_pnl,
                "total_volume": perf.volume,
                "total_fees": perf.fees,
                "total_exposure": total_exposure,
                "open_count": perf.open_count,
                "closed_count": perf.closed_count,
                "win_rate": perf.win_rate,
            },
            summary="\n".join(lines),
        )


register_provider(ExecutorsProvider())
