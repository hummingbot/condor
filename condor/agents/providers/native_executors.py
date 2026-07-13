"""Core data provider: Condor-native executors (gateway-backed).

Reads the executor store directly — the provider runs in the main
process, same as the runtime. Mirrors the reporting shape of the
hummingbot ``executors`` provider so the journal and prompt summaries
treat both venues uniformly.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from . import register_provider
from .base import BaseProvider, ProviderResult

_OPEN_STATUSES = {"PENDING", "ACTIVE", "CLOSING"}


def _notional(record) -> float:
    """Open notional from the persisted state (position amounts at last tick)."""
    state = record.state
    try:
        base = float(state.get("base_amount", 0) or 0)
        quote = float(state.get("quote_amount", 0) or 0)
        price = float(state.get("add_mid_price", 0) or 0)
        if record.type == "lp":
            return base * price + quote
        if record.type == "swap":
            return float(record.config.get("notional_quote") or 0)
    except (TypeError, ValueError):
        pass
    return 0.0


def _realized_pnl(record) -> float:
    """Realized PnL of a closed LP executor, in pool quote units.

    (returned + fees, valued at add price) - initial value. Uses the
    same inputs as LpExecutor.net_pnl_quote at close time.
    """
    if record.type != "lp":
        return 0.0
    s = record.state
    try:
        add_price = Decimal(str(s.get("add_mid_price", 0) or 0))
        if add_price <= 0:
            return 0.0
        initial = (
            Decimal(str(s.get("initial_base_amount", 0) or 0)) * add_price
            + Decimal(str(s.get("initial_quote_amount", 0) or 0))
        )
        final = (
            (Decimal(str(s.get("base_amount", 0) or 0)) + Decimal(str(s.get("base_fee", 0) or 0)))
            * add_price
            + Decimal(str(s.get("quote_amount", 0) or 0))
            + Decimal(str(s.get("quote_fee", 0) or 0))
        )
        return float(final - initial)
    except (TypeError, ValueError, ArithmeticError):
        return 0.0


class NativeExecutorsProvider(BaseProvider):
    name = "native_executors"
    is_core = True

    async def execute(
        self, client: Any, config: dict, agent_id: str = ""
    ) -> ProviderResult:
        from condor.executors.service import get_executor_runtime

        if not agent_id:
            return ProviderResult(
                name=self.name,
                data={"executors": [], "total_exposure": 0, "open_count": 0},
                summary="Native Executors: no agent_id provided",
            )

        try:
            records = get_executor_runtime().store.load_by_agent(agent_id)
        except Exception as e:
            return ProviderResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"Native Executors: failed to read store ({e})",
            )

        open_records = [r for r in records if r.status in _OPEN_STATUSES]
        closed_records = [r for r in records if r.status == "CLOSED"]
        failed_records = [r for r in records if r.status == "FAILED"]

        executors = []
        total_exposure = 0.0
        for r in open_records:
            notional = _notional(r)
            total_exposure += notional
            executors.append(
                {
                    "id": r.id,
                    "type": r.type,
                    "status": "RUNNING",
                    "pair": r.config.get("trading_pair")
                    or f"{r.config.get('base_token', '?')}-{r.config.get('quote_token', '?')}",
                    "state": r.state.get("state") or r.state.get("phase") or r.status,
                    "amount": notional,
                    "pnl": 0.0,  # unrealized PnL needs a live price; journal snapshots carry it
                }
            )

        realized = sum(_realized_pnl(r) for r in closed_records)

        lines = [
            f"Native Executors ({len(open_records)} open) [agent: {agent_id}]:"
            if open_records
            else f"Native Executors: none open (agent: {agent_id})"
        ]
        for e in executors:
            lines.append(
                f"  {e['id']} {e['pair']} {e['state']} (~${e['amount']:,.2f})"
            )
        if closed_records or failed_records:
            lines.append(
                f"  Closed: {len(closed_records)} (realized {realized:+.4f} quote) | "
                f"Failed: {len(failed_records)}"
            )

        return ProviderResult(
            name=self.name,
            data={
                "executors": executors,
                "total_exposure": total_exposure,
                "open_count": len(open_records),
                "closed_count": len(closed_records),
                "failed_count": len(failed_records),
                "realized_pnl": realized,
            },
            summary="\n".join(lines),
        )


register_provider(NativeExecutorsProvider())
