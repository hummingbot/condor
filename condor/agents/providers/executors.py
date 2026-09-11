"""Core data provider: active executors with PnL and volume.

Delegates number-crunching to ``condor.agents.performance`` so that
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
        self,
        client: Any,
        config: dict,
        agent_id: str = "",
        bot_names: list[str] | None = None,
        since: float = 0.0,
    ) -> ProviderResult:
        from condor.agents.performance import fetch_agent_performance

        if not agent_id:
            return ProviderResult(
                name=self.name,
                data={"executors": [], "total_pnl": 0, "total_volume": 0},
                summary="Active Executors: no agent_id provided",
            )

        # The ledger's bases are what this session actually owns — several, if it
        # deployed several. Without one (executor mode, tests) fall back to the
        # configured name, which is what the session would deploy under anyway.
        bases = list(bot_names or []) or [config.get("bot_name", "")]

        try:
            # ``since`` slices an adopted bot's history to this session's window,
            # so what the agent is told it earned is what the dashboard attributes
            # to it — inherited PnL is not reported as its own.
            perf = await fetch_agent_performance(
                client, agent_id, bot_names=bases, since=since
            )
        except Exception as e:
            return ProviderResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"Active Executors: failed to fetch ({e})",
            )

        running = [e for e in perf.executors if e["status"] == "RUNNING"]
        # A terminal executor that still reports an on-chain position address has
        # stranded live exposure with no automated owner: an involuntary hold
        # (POSITION_HOLD with hold_reason set — an LP close that exhausted its
        # retries) or a legacy FAILED-with-position. It would otherwise vanish
        # from this RUNNING-only summary — surface it until it is recovered.
        orphaned = [
            e
            for e in perf.executors
            if e["status"] != "RUNNING"
            and not (e.get("custom_info") or {}).get("orphan_resolved")
            and (
                (e.get("custom_info") or {}).get("orphaned_position")
                or (
                    (e.get("custom_info") or {}).get("hold_reason")
                    and (e.get("custom_info") or {}).get("position_address")
                )
                or (
                    str(e.get("close_type") or "").upper() == "FAILED"
                    and (e.get("custom_info") or {}).get("position_address")
                )
            )
        ]
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
        for o in orphaned:
            pos = (o.get("custom_info") or {}).get("position_address")
            reason = (o.get("custom_info") or {}).get("hold_reason") or "close failed"
            lines.append(
                f"  🚨 ORPHANED POSITION: executor {o['id']} ({o['pair']}) terminated "
                f"({reason}) with position {pos} still open on-chain. Stopping the executor "
                "will NOT close it — it has already terminated. Close it with "
                'manage_clmm(action="close", position_address=..., pool_address=...); '
                "list_orphaned_positions() reports the dex, pool and network for "
                "the call, and pool_address is required. A new create_lp_executor CANNOT adopt "
                "the position and would mint a second one. Then mark it recovered with "
                f"resolve_orphaned_position(executor_id=\"{o['id']}\"). "
                "Do not open new positions on these funds first."
            )
        if perf.bot_names:
            lines.append(f"  Bots operated: {', '.join(perf.bot_names)}")
        lines.append(
            f"  Realized: ${perf.realized_pnl:+.2f} | "
            f"Unrealized: ${perf.unrealized_pnl:+.2f} | "
            f"Total PnL: ${perf.total_pnl:+.2f} | "
            f"Volume: ${perf.volume:,.0f}"
        )
        # Say so rather than letting a missing bot read as a flat one. The figures
        # above are then a floor, and an agent told a floor can go look; an agent
        # told "$0.00" has no reason to.
        if perf.unresolved_bases:
            lines.append(
                "  ⚠️ No live or archived instance found for: "
                f"{', '.join(perf.unresolved_bases)} — the PnL above EXCLUDES "
                "them and is incomplete, not flat. Verify with manage_bots before "
                "concluding this session is break-even."
            )

        total_exposure = sum(r.get("amount", 0) for r in running)

        return ProviderResult(
            name=self.name,
            data={
                "executors": running,
                "orphaned_executors": orphaned,
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
                # Provenance travels with the figures. Everything downstream —
                # get_info(), the session report, the journal snapshot — reads
                # this dict, so a consumer that shows a total can show what the
                # total is made of and whether any of it is missing.
                "bot_names": perf.bot_names,
                "bot_instances": perf.bot_instances,
                "unresolved_bases": perf.unresolved_bases,
                "controllers": perf.controllers,
                "close_type_counts": perf.close_type_counts,
                "fees_known": perf.fees_known,
            },
            summary="\n".join(lines),
        )


register_provider(ExecutorsProvider())
