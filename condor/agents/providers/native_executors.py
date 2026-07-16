"""Core data provider: Condor-native executors (gateway-backed).

Reads the executor store directly — the provider runs in the main
process, same as the runtime. Mirrors the reporting shape of the
hummingbot ``executors`` provider so the journal and prompt summaries
treat both venues uniformly.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from . import register_provider
from .base import BaseProvider, ProviderResult

_OPEN_STATUSES = {"PENDING", "ACTIVE", "CLOSING"}


def _pair(record) -> str:
    """Human-readable instrument label per executor type (no more '?-?')."""
    c = record.config
    if c.get("trading_pair"):
        return str(c["trading_pair"])
    if c.get("coin"):  # perp types
        return f"{c['coin']}-USD"
    if record.type.endswith("_pred"):
        market = str(c.get("market") or "?")
        outcome = {"LONG": "Yes", "SHORT": "No"}.get(str(c.get("position")), "?")
        return f"{market[:24]}:{outcome}"
    return f"{c.get('base_token', '?')[:12]}-{c.get('quote_token', '?')}"


def _side(record) -> str:
    c = record.config
    return str(c.get("side") or c.get("position") or "")


def _price(record) -> float | None:
    """Best-known price: venue entry when filled, else the declared limit."""
    for v in (record.state.get("entry_price"), record.config.get("limit_px")):
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _notional(record) -> float:
    """Open notional in quote units — shared with the performance rollup so
    exposure is consistent everywhere (all six types accounted for)."""
    from condor.executors.performance import open_notional

    try:
        return float(open_notional(record))
    except (TypeError, ValueError, ArithmeticError):
        return 0.0


def _unrealized_pnl(record) -> float | None:
    """Open PnL. perp: the venue's unrealized_pnl persisted each tick. spot/pred
    position: size*mark - stake from the executor's own last barrier-check mark.
    None when no price/mark is available yet.
    """
    t = record.type
    s = record.state
    extra = s.get("extra") or {}
    try:
        if t == "position_perp":
            u = extra.get("unrealized_pnl")
            return float(u) if u is not None else None
        if t in ("position_spot", "position_pred"):
            mark = s.get("mark_price")
            if mark is None:
                return None
            return float(
                Decimal(str(s.get("size") or 0)) * Decimal(str(mark))
                - Decimal(str(s.get("amount_spent") or 0))
            )
    except (TypeError, ValueError, ArithmeticError):
        return None
    return None


def _realized_pnl(record) -> float:
    """Realized PnL of a CLOSED executor — one source of truth (the performance
    rollup), so perp/pred realized results feed drawdown and scorecards too."""
    from condor.executors.performance import realized_pnl

    try:
        pnl, _costs = realized_pnl(record)
        return float(pnl)
    except (TypeError, ValueError, ArithmeticError):
        return 0.0


def _overlay_live_record(record, runtime):
    """Use in-memory state for running executors while the durable log remains
    transition-only. This keeps volatile mark/PnL out of JSONL without making the
    risk provider stale between phase changes."""
    executor = getattr(runtime, "_executors", {}).get(record.id)
    if executor is None:
        return record
    return replace(
        record,
        status=executor.status.value,
        state=executor.state_model().model_dump(mode="json"),
        close_reason=executor.close_reason,
    )


async def _live_perp_inventory(runtime, records, open_records) -> list[dict]:
    """Venue positions left by terminal order_perp fills.

    Single-leg order executors end once filled, but the Hyperliquid account stays
    net long/short. Represent that venue position as a synthetic open row so risk
    exposure, drawdown, and the journal continue to carry it between ticks.
    """
    owned_by_position = {
        r.config.get("coin")
        for r in open_records
        if r.type == "position_perp" and r.config.get("coin")
    }
    filled_orders = [
        r
        for r in records
        if r.type == "order_perp"
        and r.status == "CLOSED"
        and r.state.get("state") == "DONE"
        and r.state.get("close_type") != "early_stop"
        and r.config.get("coin")
    ]
    coins = {r.config.get("coin") for r in filled_orders} - owned_by_position
    if not coins:
        return []

    connector = getattr(runtime, "_hl_clients", {}).get("perp")
    if connector is None and hasattr(runtime, "connector_for_spec"):
        try:
            connector = runtime.connector_for_spec("order_perp", "hyperliquid")
        except Exception:
            connector = None

    rows = []
    for coin in sorted(coins):
        pos = None
        venue_error = connector is None
        if connector is not None:
            try:
                pos = await connector.position(coin)
            except Exception:
                venue_error = True
        if venue_error:
            # Failing open at zero would create fresh risk headroom. Until the
            # venue can be read again, retain the gross filled notional as a
            # conservative upper bound (opposite fills may have reduced it).
            fallback = sum(
                _notional(r) for r in filled_orders if r.config.get("coin") == coin
            )
            rows.append(
                {
                    "id": f"venue_perp:{coin}",
                    "type": "venue_perp_position",
                    "status": "RUNNING",
                    "pair": f"{coin}-USD",
                    "state": "UNVERIFIED",
                    "amount": fallback,
                    "pnl": None,
                }
            )
            continue
        if pos is None:
            continue
        notional = float(pos.size * pos.entry_px)
        rows.append(
            {
                "id": f"venue_perp:{coin}",
                "type": "venue_perp_position",
                "status": "RUNNING",
                "pair": f"{coin}-USD",
                "state": pos.side,
                "amount": notional,
                "pnl": float(pos.unrealized_pnl),
            }
        )
    return rows


def _filled_holdings(records) -> list[dict]:
    """Attributed inventory left by terminal single-leg orders.

    A filled ``order_pred``/``order_spot`` executor is CLOSED and vanishes from
    the open list, but the shares/tokens it bought remain in the wallet. Roll
    those fills up into durable holding rows (cost basis) so accumulated
    inventory stays visible to the agent — without it, an accumulation strategy
    re-buys past its per-target cap because filled lots become invisible."""
    filled = [
        r
        for r in records
        if r.type in ("order_pred", "order_spot")
        and r.status == "CLOSED"
        and r.state.get("state") == "DONE"
        and r.state.get("close_type") != "early_stop"
    ]
    lots: dict[tuple, dict] = {}
    for r in filled:
        try:
            size = Decimal(str(r.state.get("size") or 0))
            entry = Decimal(str(r.state.get("entry_price") or 0))
        except (TypeError, ValueError, ArithmeticError):
            continue
        if size <= 0:
            continue
        if r.type == "order_spot" and str(r.config.get("side")) == "SELL":
            size = -size
        key = (r.type, _pair(r))
        lot = lots.setdefault(
            key, {"net_size": Decimal("0"), "cost": Decimal("0"), "fills": 0}
        )
        lot["net_size"] += size
        lot["cost"] += size * entry
        lot["fills"] += 1
    rows = []
    for (rtype, pair), lot in sorted(lots.items()):
        if lot["net_size"] <= 0:
            continue
        avg = lot["cost"] / lot["net_size"] if lot["net_size"] else Decimal("0")
        rows.append(
            {
                "id": f"holding:{pair}",
                "type": f"holding_{rtype.split('_', 1)[1]}",
                "status": "HELD",
                "pair": pair,
                "state": f"{lot['fills']} fill(s)",
                "side": "",
                "price": float(avg),
                "size": float(lot["net_size"]),
                "amount": float(lot["cost"]),  # cost basis in quote units
                "pnl": None,
            }
        )
    return rows


class NativeExecutorsProvider(BaseProvider):
    name = "native_executors"
    is_core = True

    async def execute(
        self, client: Any, config: dict, agent_id: str = "", agent_slug: str = ""
    ) -> ProviderResult:
        from condor.executors.service import get_executor_runtime

        if not agent_id:
            return ProviderResult(
                name=self.name,
                data={"executors": [], "total_exposure": 0, "open_count": 0},
                summary="Native Executors: no agent_id provided",
            )
        if not agent_slug:
            # Run ids are opaque ULIDs (§7.1) — the slug-wide inherited-state
            # read needs the explicit slug the engine already knows.
            return ProviderResult(
                name=self.name,
                data={"error": "no agent_slug provided"},
                summary="Native Executors: no agent_slug provided",
            )

        try:
            runtime = get_executor_runtime()
            # Slug-wide read: open executors surviving from a PRIOR run (or
            # a delegation) of this agent must stay visible to the current one,
            # or a restart resets apparent exposure and enables duplication.
            slug = agent_slug
            slug_records = [
                _overlay_live_record(r, runtime)
                for r in runtime.store.load_by_slug(slug)
            ]
        except Exception as e:
            return ProviderResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"Native Executors: failed to read store ({e})",
            )

        records = [r for r in slug_records if r.agent_id == agent_id]
        inherited_open = [
            r
            for r in slug_records
            if r.agent_id != agent_id and r.status in _OPEN_STATUSES
        ]

        open_records = [r for r in records if r.status in _OPEN_STATUSES]
        closed_records = [r for r in records if r.status == "CLOSED"]
        failed_records = [r for r in records if r.status == "FAILED"]

        executors = []
        total_exposure = 0.0
        total_unrealized = 0.0
        for r in open_records + inherited_open:
            notional = _notional(r)
            unrealized = _unrealized_pnl(r)
            total_exposure += notional
            if unrealized is not None:
                total_unrealized += unrealized
            row = {
                "id": r.id,
                "type": r.type,
                "status": "RUNNING",
                "pair": _pair(r),
                "state": r.state.get("state") or r.state.get("phase") or r.status,
                "side": _side(r),
                "price": _price(r),
                "amount": notional,
                "pnl": unrealized,  # None when the pool price was unavailable
            }
            if r.agent_id != agent_id:
                row["owner"] = r.agent_id  # inherited from a prior session
            executors.append(row)

        venue_positions = await _live_perp_inventory(
            runtime, slug_records, open_records + inherited_open
        )
        for position in venue_positions:
            total_exposure += position["amount"]
            if position["pnl"] is not None:
                total_unrealized += position["pnl"]
            executors.append(position)

        # Filled single-leg inventory (order_pred/order_spot) — slug-wide, since
        # holdings persist in the wallet across sessions.
        for holding in _filled_holdings(slug_records):
            total_exposure += holding["amount"]
            executors.append(holding)

        realized = sum(_realized_pnl(r) for r in closed_records)

        lines = [
            f"Native Executors ({len(executors)} open) [agent: {agent_id}]:"
            if executors
            else f"Native Executors: none open (agent: {agent_id})"
        ]
        for e in executors:
            pnl_str = f"{e['pnl']:+.4f}" if e["pnl"] is not None else "n/a (no price)"
            side = f" {e['side']}" if e.get("side") else ""
            px = f" @ {e['price']:g}" if e.get("price") else ""
            owner = f" [prior session: {e['owner']}]" if e.get("owner") else ""
            lines.append(
                f"  {e['id']} {e['pair']}{side} {e['state']}{px} "
                f"(~{e['amount']:,.2f} quote, uPnL {pnl_str}){owner}"
            )
        if inherited_open:
            lines.append(
                f"  NOTE: {len(inherited_open)} open executor(s) inherited from a "
                "prior session — manage or stop them via manage_executors "
                "(stop works by executor id)."
            )
        if closed_records or failed_records:
            lines.append(
                f"  Closed: {len(closed_records)} (realized {realized:+.4f} quote) | "
                f"Failed: {len(failed_records)}"
            )
            # Recent terminal events, typed — so the agent reads what just
            # closed (side, price, why) instead of reconstructing it from prose.
            recent = sorted(
                closed_records + failed_records,
                key=lambda r: r.updated_at or "",
                reverse=True,
            )[:5]
            for r in recent:
                why = r.state.get("close_type") or r.close_reason or r.status
                px = _price(r)
                px_str = f" @ {px:g}" if px else ""
                lines.append(
                    f"  recent: {r.id} {_pair(r)} {_side(r)}{px_str} → {r.status} "
                    f"({why}, realized {_realized_pnl(r):+.4f})"
                )

        return ProviderResult(
            name=self.name,
            data={
                "executors": executors,
                "total_exposure": total_exposure,
                "unrealized_pnl": total_unrealized,
                "open_count": len(executors),
                "closed_count": len(closed_records),
                "failed_count": len(failed_records),
                "realized_pnl": realized,
            },
            summary="\n".join(lines),
        )


register_provider(NativeExecutorsProvider())
