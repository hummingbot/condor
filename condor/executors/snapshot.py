"""One portfolio snapshot, account-keyed (§6.3).

``snapshot(runtime, account_ref=None, agent_slug=None, agent_id=None)`` —
the account view is the primary object; ``agent_slug``/``agent_id`` are
ATTRIBUTION FILTERS over it (what the agent prompt and per-run risk
consume). Physical checks read the account view; agent/run risk caps read
the filtered projection (§6.1 — there is no account-level cap).

Sourced from the durable executor records (the sole financial authority,
§6.2b). Live venue inventory joins this view through the venue packages'
read surface as they land; until then the snapshot is the attributed
projection plus per-instrument owned inventory folded from ``orders[]``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from condor.accounts.model import AccountRef
from condor.executors.orders import LandedOrder, live_orders, owned_net_base

_OPEN_STATUSES = ("PENDING", "ACTIVE", "CLOSING")


def _instrument_of(cfg: dict) -> str:
    base, quote = cfg.get("base_token"), cfg.get("quote_token")
    if base and quote:
        return f"{base}-{quote}"
    return str(cfg.get("coin") or cfg.get("market") or cfg.get("type", ""))


def snapshot(
    runtime,
    *,
    account_ref: Optional[AccountRef] = None,
    agent_slug: Optional[str] = None,
    agent_id: Optional[str] = None,
    recent_terminal: int = 10,
) -> dict:
    from condor.executors.ops import _record_exposure, _scope_exposure

    records = (
        runtime.store.load_by_slug(agent_slug)
        if agent_slug
        else runtime.store.list_all(limit=500)
    )
    if agent_id:
        records = [r for r in records if r.agent_id == agent_id]
    if account_ref is not None:
        records = [
            r
            for r in records
            if (r.config or {}).get("account_ref") == account_ref.as_dict()
        ]

    open_records = [r for r in records if r.status in _OPEN_STATUSES]
    terminal = sorted(
        (r for r in records if r.status not in _OPEN_STATUSES),
        key=lambda r: r.updated_at,
        reverse=True,
    )[:recent_terminal]

    executors = []
    exposure = _scope_exposure(records)
    live_order_count = 0
    inventory: dict[str, Decimal] = {}
    for r in records:
        cfg = r.config or {}
        landed = [LandedOrder(**o) for o in (r.state or {}).get("orders") or []]
        inst = _instrument_of(cfg)
        product = (r.type.split("_", 1)[1] if "_" in r.type else "spot") or "spot"
        net = owned_net_base(
            landed, product=product, base_asset=str(cfg.get("base_token") or "")
        )
        if net:
            inventory[inst] = inventory.get(inst, Decimal("0")) + net
    for r in open_records:
        cfg = r.config or {}
        landed = [LandedOrder(**o) for o in (r.state or {}).get("orders") or []]
        live = live_orders(landed)
        live_order_count += len(live)
        inst = _instrument_of(cfg)
        record_exposure = _record_exposure(r)
        executors.append(
            {
                "id": r.id,
                "type": r.type,
                "status": r.status,
                "instrument": inst,
                "venue": cfg.get("venue", "solana"),
                "agent_slug": r.agent_slug,
                "run_id": r.agent_id,
                "origin": cfg.get("origin", ""),
                "phase": (r.state or {}).get("state", ""),
                "exposure_quote": record_exposure,
                "live_orders": [
                    {
                        "venue_order_id": o.venue_order_id,
                        "role": o.role.value,
                        "side": o.side,
                        "status": o.status.value,
                    }
                    for o in live
                ],
            }
        )

    return {
        "account": account_ref.as_dict() if account_ref else None,
        "filters": {"agent_slug": agent_slug, "run_id": agent_id},
        "open_executors": executors,
        "open_count": len(open_records),
        "live_order_count": live_order_count,
        "attributed_exposure_quote": exposure,
        "owned_inventory": {k: str(v) for k, v in inventory.items()},
        "recent_terminal": [
            {
                "id": r.id,
                "type": r.type,
                "status": r.status,
                "close_reason": r.close_reason,
                "run_id": r.agent_id,
            }
            for r in terminal
        ],
    }
