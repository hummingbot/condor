"""Core data provider: active executors with PnL and volume."""

from __future__ import annotations

from typing import Any

from . import register_provider
from .base import BaseProvider, ProviderResult


class ExecutorsProvider(BaseProvider):
    name = "executors"
    is_core = True

    async def execute(self, client: Any, config: dict, agent_id: str = "") -> ProviderResult:
        from handlers.executors._shared import (
            format_executor_status_line,
            get_executor_pnl,
            get_executor_volume,
            search_running_executors,
        )

        try:
            executors = await search_running_executors(client, status="RUNNING", limit=50)
        except Exception as e:
            return ProviderResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"Active Executors: failed to fetch ({e})",
            )

        # Filter to this agent's executors if agent_id is provided
        if agent_id and executors:
            executors = [
                ex for ex in executors
                if ex.get("config", {}).get("controller_id") == agent_id
            ]

        # Fetch authoritative PnL from performance report API
        perf_data = {}
        if agent_id:
            try:
                perf_data = await client.executors.get_performance_report(controller_id=agent_id)
                if not isinstance(perf_data, dict):
                    perf_data = {}
            except Exception:
                pass

        if not executors and not perf_data:
            return ProviderResult(
                name=self.name,
                data={"executors": [], "total_pnl": 0, "total_volume": 0},
                summary="Active Executors: none running" + (f" (filtered by agent {agent_id})" if agent_id else ""),
            )

        # Compute running executor PnL (unrealized)
        running_pnl = 0.0
        running_volume = 0.0
        lines = [f"Active Executors ({len(executors)})" + (f" [agent: {agent_id}]" if agent_id else "") + ":"]

        for ex in executors:
            pnl = get_executor_pnl(ex)
            vol = get_executor_volume(ex)
            running_pnl += pnl
            running_volume += vol
            lines.append(f"  {format_executor_status_line(ex)}")

        # Use performance report as authoritative PnL source
        if perf_data:
            # Try known keys for PnL
            total_pnl = float(
                perf_data.get("net_pnl_quote")
                or perf_data.get("realized_pnl_quote")
                or perf_data.get("total_pnl")
                or perf_data.get("net_pnl")
                or 0
            )
            total_volume = float(
                perf_data.get("total_volume")
                or perf_data.get("volume_traded")
                or perf_data.get("total_volume_quote")
                or 0
            )
            # Add unrealized PnL from currently running executors
            total_pnl += running_pnl
            total_volume = max(total_volume, running_volume)
            lines.append(f"  Performance report PnL: ${total_pnl - running_pnl:+.2f} (realized)")
        else:
            total_pnl = running_pnl
            total_volume = running_volume

        lines.append(f"  Total PnL: ${total_pnl:+.2f} | Volume: ${total_volume:,.0f}")

        total_exposure = 0.0
        executor_data = []
        for ex in executors:
            cfg = ex.get("config", ex)
            amount = float(cfg.get("total_amount_quote", 0) or cfg.get("amount", 0) or 0)
            total_exposure += amount
            executor_data.append({
                "id": ex.get("id", ex.get("executor_id", "")),
                "type": cfg.get("type", ""),
                "pair": cfg.get("trading_pair", ""),
                "pnl": get_executor_pnl(ex),
                "volume": get_executor_volume(ex),
                "amount": amount,
                "status": ex.get("status", ""),
            })

        return ProviderResult(
            name=self.name,
            data={
                "executors": executor_data,
                "total_pnl": total_pnl,
                "total_volume": total_volume,
                "total_exposure": total_exposure,
                "performance_report": perf_data if perf_data else None,
            },
            summary="\n".join(lines),
        )


register_provider(ExecutorsProvider())
