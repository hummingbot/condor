"""Performance rollups over the executor store — the single source of truth
for "how is my agent doing" and "compare these strategies".

One aggregation core; the REST route, the MCP manage_executors
performance action, and the pnl CLI all delegate here. Grouping keys map
onto the attribution trio every executor row carries:

    agent    -> agent_slug   (WHO — across all its sessions/delegations)
    run      -> agent_id     (WHICH RUN — one session or delegation)
    strategy -> strategy     (WHICH PLAYBOOK — session-created only;
                              delegation/chat rows group under "(none)")
    venue    -> config connector (jupiter default for swaps/positions)
    type     -> executor type
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from condor.executors.store import ExecutorRecord, ExecutorStore

_OPEN_STATUSES = {"PENDING", "ACTIVE", "CLOSING"}

GROUP_KEYS = ("agent", "run", "strategy", "venue", "type")


@dataclass
class GroupPerformance:
    key: str
    open_count: int = 0
    closed_count: int = 0
    failed_count: int = 0
    realized_pnl_quote: float = 0.0
    costs_quote: float = 0.0  # tx fees + unrefunded rent, in quote units
    open_notional_quote: float = 0.0
    wins: int = 0
    losses: int = 0
    close_types: dict = field(default_factory=dict)

    @property
    def win_rate(self) -> Optional[float]:
        settled = self.wins + self.losses
        return self.wins / settled if settled else None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "open_count": self.open_count,
            "closed_count": self.closed_count,
            "failed_count": self.failed_count,
            "realized_pnl_quote": round(self.realized_pnl_quote, 8),
            "costs_quote": round(self.costs_quote, 8),
            "open_notional_quote": round(self.open_notional_quote, 8),
            "win_rate": self.win_rate,
            "close_types": self.close_types,
        }


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def realized_pnl(record: ExecutorRecord) -> tuple[Decimal, Decimal]:
    """(net_pnl, costs) of a CLOSED executor, in its quote units.

    Same math as the pnl CLI: LP values final vs initial at the add-time
    price minus tx fees and unrefunded rent; positions use actual
    quote-in vs quote-out; swaps are conversions (fee cost only).
    """
    s = record.state
    if record.type == "lp":
        add_price = _dec(s.get("add_mid_price"))
        if add_price <= 0:
            return Decimal("0"), Decimal("0")
        initial = _dec(s.get("initial_base_amount")) * add_price + _dec(s.get("initial_quote_amount"))
        final = ((_dec(s.get("base_amount")) + _dec(s.get("base_fee"))) * add_price
                 + _dec(s.get("quote_amount")) + _dec(s.get("quote_fee")))
        rent_lost = _dec(s.get("position_rent")) - _dec(s.get("position_rent_refunded"))
        costs = (_dec(s.get("tx_fee")) + rent_lost) * add_price
        return final - initial - costs, costs
    if record.type == "position":
        spent = _dec(s.get("quote_spent"))
        returned = s.get("quote_returned")
        if returned is None:
            return Decimal("0"), Decimal("0")  # detached: tokens held, not realized
        # tx_fee is native; for SOL-quoted positions it is already quote units
        costs = _dec(s.get("tx_fee"))
        return _dec(returned) - spent, costs
    if record.type == "swap":
        cost = _dec(s.get("fee")) * _dec(s.get("quoted_price"))
        return -cost, cost
    return Decimal("0"), Decimal("0")


def open_notional(record: ExecutorRecord) -> Decimal:
    s = record.state
    if record.type == "lp":
        return (_dec(s.get("base_amount")) * _dec(s.get("add_mid_price"))
                + _dec(s.get("quote_amount")))
    if record.type == "position":
        return _dec(s.get("quote_spent") or record.config.get("amount_quote"))
    if record.type == "swap":
        return _dec(record.config.get("notional_quote"))
    return Decimal("0")


def _group_key(record: ExecutorRecord, group_by: str) -> str:
    if group_by == "agent":
        return record.agent_slug or "(manual)"
    if group_by == "run":
        return record.agent_id or "(manual)"
    if group_by == "strategy":
        return record.strategy or "(none)"
    if group_by == "venue":
        return record.config.get("connector") or "jupiter"
    if group_by == "type":
        return record.type
    raise ValueError(f"unknown group_by: {group_by!r} (use one of {GROUP_KEYS})")


def aggregate_performance(
    store: ExecutorStore,
    group_by: str = "agent",
    agent_id: Optional[str] = None,
    agent_slug: Optional[str] = None,
    limit: int = 1000,
) -> list[dict]:
    """Roll up the executor store into per-group performance rows.

    Quote units are each executor's own (USD-quoted pairs exact, SOL-quoted
    in SOL) — groups mixing quote currencies sum them as-is; keep groups
    homogeneous or read per-group with that in mind.
    """
    records = store.list_all(limit=limit)
    if agent_id:
        records = [r for r in records if r.agent_id == agent_id]
    if agent_slug:
        records = [r for r in records if r.agent_slug == agent_slug]

    groups: dict[str, GroupPerformance] = {}
    for r in records:
        key = _group_key(r, group_by)
        g = groups.setdefault(key, GroupPerformance(key=key))
        if r.status in _OPEN_STATUSES:
            g.open_count += 1
            g.open_notional_quote += float(open_notional(r))
        elif r.status == "CLOSED":
            g.closed_count += 1
            pnl, costs = realized_pnl(r)
            g.realized_pnl_quote += float(pnl)
            g.costs_quote += float(costs)
            close_type = r.state.get("close_type") or _close_kind_from_reason(r)
            g.close_types[close_type] = g.close_types.get(close_type, 0) + 1
            if r.type != "swap":  # conversions aren't wins/losses
                if pnl > 0:
                    g.wins += 1
                elif pnl < 0:
                    g.losses += 1
        elif r.status == "FAILED":
            g.failed_count += 1

    return [g.to_dict() for g in sorted(groups.values(), key=lambda g: g.key)]


def _close_kind_from_reason(record: ExecutorRecord) -> str:
    reason = (record.close_reason or "").lower()
    if "limit price" in reason:
        return "limit_price"
    if "early stop" in reason or "stopped" in reason:
        return "early_stop"
    if "swap confirmed" in reason:
        return "swap"
    return "other"
