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

        # Also fetch closed executors for PnL history
        closed_executors = []
        if agent_id:
            try:
                all_executors = await search_running_executors(client, status=None, limit=100)
                closed_executors = [
                    ex for ex in (all_executors or [])
                    if ex.get("config", {}).get("controller_id") == agent_id
                    and ex.get("status") != "RUNNING"
                ]
            except Exception:
                pass

        if not executors and not closed_executors:
            return ProviderResult(
                name=self.name,
                data={"executors": [], "total_pnl": 0, "total_volume": 0},
                summary="Active Executors: none running" + (f" (filtered by agent {agent_id})" if agent_id else ""),
            )

        total_pnl = 0.0
        total_volume = 0.0
        lines = [f"Active Executors ({len(executors)})" + (f" [agent: {agent_id}]" if agent_id else "") + ":"]

        for ex in executors:
            pnl = get_executor_pnl(ex)
            vol = get_executor_volume(ex)
            total_pnl += pnl
            total_volume += vol
            lines.append(f"  {format_executor_status_line(ex)}")

        # Add closed executor PnL
        closed_pnl = 0.0
        for ex in closed_executors:
            closed_pnl += get_executor_pnl(ex)
            total_volume += get_executor_volume(ex)
        total_pnl += closed_pnl

        if closed_executors:
            lines.append(f"  Closed executors: {len(closed_executors)} (PnL: ${closed_pnl:+.2f})")

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
            },
            summary="\n".join(lines),
        )


register_provider(ExecutorsProvider())
