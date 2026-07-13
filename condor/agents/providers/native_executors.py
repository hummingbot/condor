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


async def _live_pool_prices(gateway, records) -> dict[str, float]:
    """Fetch the live price once per unique open LP pool. Fail soft per pool."""
    prices: dict[str, float] = {}
    for r in records:
        pool = r.config.get("pool_address")
        if r.type != "lp" or not pool or pool in prices:
            continue
        try:
            info = await gateway.clmm_pool_info(
                r.config["connector"], r.config["chain_network"], pool
            )
            prices[pool] = float(info["price"])
        except Exception:  # noqa: BLE001 — a dead pool feed must not block the tick
            pass
    return prices


def _notional(record, live_price: float | None) -> float:
    """Open notional, valued at the live price when available."""
    state = record.state
    try:
        if record.type == "lp":
            base = float(state.get("base_amount", 0) or 0)
            quote = float(state.get("quote_amount", 0) or 0)
            price = live_price if live_price else float(state.get("add_mid_price", 0) or 0)
            return base * price + quote
        if record.type == "swap":
            return float(record.config.get("notional_quote") or 0)
        if record.type == "position":
            return float(state.get("quote_spent") or record.config.get("amount_quote") or 0)
    except (TypeError, ValueError):
        pass
    return 0.0


def _unrealized_pnl(record, live_price: float | None) -> float | None:
    """Open PnL. LP: live pool price (same math as LpExecutor.net_pnl_quote);
    position: the executor's own last barrier-check price (refreshed every
    few seconds by its loop). None when no price is available.
    """
    if record.type == "position":
        s = record.state
        try:
            last = s.get("last_price")
            if last is None:
                return None
            return float(
                Decimal(str(s.get("base_bought", 0))) * Decimal(str(last))
                - Decimal(str(s.get("quote_spent", 0)))
            )
        except (TypeError, ValueError, ArithmeticError):
            return None
    if record.type != "lp" or not live_price:
        return None
    s = record.state
    try:
        price = Decimal(str(live_price))
        add_price = Decimal(str(s.get("add_mid_price", 0) or 0))
        if add_price <= 0:
            add_price = price
        initial = (
            Decimal(str(s.get("initial_base_amount", 0) or 0)) * add_price
            + Decimal(str(s.get("initial_quote_amount", 0) or 0))
        )
        current = (
            Decimal(str(s.get("base_amount", 0) or 0)) * price
            + Decimal(str(s.get("quote_amount", 0) or 0))
        )
        fees = (
            Decimal(str(s.get("base_fee", 0) or 0)) * price
            + Decimal(str(s.get("quote_fee", 0) or 0))
        )
        return float(current + fees - initial)
    except (TypeError, ValueError, ArithmeticError):
        return None


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
            runtime = get_executor_runtime()
            records = runtime.store.load_by_agent(agent_id)
        except Exception as e:
            return ProviderResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"Native Executors: failed to read store ({e})",
            )

        open_records = [r for r in records if r.status in _OPEN_STATUSES]
        closed_records = [r for r in records if r.status == "CLOSED"]
        failed_records = [r for r in records if r.status == "FAILED"]

        prices = await _live_pool_prices(runtime.gateway, open_records)

        executors = []
        total_exposure = 0.0
        total_unrealized = 0.0
        for r in open_records:
            live = prices.get(r.config.get("pool_address", ""))
            notional = _notional(r, live)
            unrealized = _unrealized_pnl(r, live)
            total_exposure += notional
            if unrealized is not None:
                total_unrealized += unrealized
            executors.append(
                {
                    "id": r.id,
                    "type": r.type,
                    "status": "RUNNING",
                    "pair": r.config.get("trading_pair")
                    or f"{r.config.get('base_token', '?')}-{r.config.get('quote_token', '?')}",
                    "state": r.state.get("state") or r.state.get("phase") or r.status,
                    "amount": notional,
                    "pnl": unrealized,  # None when the pool price was unavailable
                }
            )

        realized = sum(_realized_pnl(r) for r in closed_records)

        lines = [
            f"Native Executors ({len(open_records)} open) [agent: {agent_id}]:"
            if open_records
            else f"Native Executors: none open (agent: {agent_id})"
        ]
        for e in executors:
            pnl_str = f"{e['pnl']:+.4f}" if e["pnl"] is not None else "n/a (no price)"
            lines.append(
                f"  {e['id']} {e['pair']} {e['state']} (~${e['amount']:,.2f}, uPnL {pnl_str})"
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
                "unrealized_pnl": total_unrealized,
                "open_count": len(open_records),
                "closed_count": len(closed_records),
                "failed_count": len(failed_records),
                "realized_pnl": realized,
            },
            summary="\n".join(lines),
        )


register_provider(NativeExecutorsProvider())
