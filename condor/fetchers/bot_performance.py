"""Fetch and aggregate Hummingbot bot performance by bot name.

Single source of truth for the "bot-by-name" PnL aggregation that wraps
``client.bot_orchestration.get_latest_controller_performance()`` and rolls up
the per-controller snapshots into one figure per ``bot_name``.

Used by:
- the web ``/bot-runs`` route (to enrich each run with its live PnL), and
- ``condor.agents.performance`` (to merge a controller-mode agent's bot PnL into
  the agent's reported performance).

The two PnL sources are disjoint by construction: bot controllers create
executors tagged with their own controller-config ids, never with an
``agent_id``, so this aggregate adds to the executor-by-``agent_id`` aggregate
without double counting.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def extract_snapshots(result: Any) -> list[dict]:
    """Normalize a controller-performance API response into a list of snapshot dicts."""
    if isinstance(result, list):
        return [s for s in result if isinstance(s, dict)]
    if isinstance(result, dict):
        data = result.get("data", result.get("snapshots", result.get("records", [])))
        if isinstance(data, list):
            return [s for s in data if isinstance(s, dict)]
        if isinstance(data, dict):
            # Could be keyed by controller_id
            out = []
            for key, val in data.items():
                if isinstance(val, dict):
                    val.setdefault("controller_id", key)
                    out.append(val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            item.setdefault("controller_id", key)
                            out.append(item)
            return out
    return []


def _aggregate_by_bot(snapshots: list[dict]) -> dict[str, dict]:
    """Roll up per-controller snapshots into one aggregate per bot_name.

    Each controller entry keeps its ``positions_summary`` (the open positions the
    controller holds, with per-position PnL/volume/fees) and ``status`` so callers
    can render executor-like rows for bot-mode agents, whose executors live in the
    bot container and never surface in the ``agent_id``-keyed executor table.
    """
    agg: dict[str, dict] = {}
    for snap in snapshots:
        bn = snap.get("bot_name", "")
        if not bn:
            continue
        perf = snap.get("performance", snap)
        if not isinstance(perf, dict):
            perf = {}
        if bn not in agg:
            agg[bn] = {
                "bot_name": bn,
                "realized_pnl_quote": 0.0,
                "unrealized_pnl_quote": 0.0,
                "global_pnl_quote": 0.0,
                "volume_traded": 0.0,
                "cum_fees_quote": 0.0,
                "num_controllers": 0,
                "timestamp": "",
                "controllers": [],
            }
        realized = float(perf.get("realized_pnl_quote", 0) or 0)
        unrealized = float(perf.get("unrealized_pnl_quote", 0) or 0)
        volume = float(perf.get("volume_traded", 0) or 0)
        positions = [
            p for p in (perf.get("positions_summary") or []) if isinstance(p, dict)
        ]
        fees = sum(float(p.get("cum_fees_quote", 0) or 0) for p in positions)
        agg[bn]["realized_pnl_quote"] += realized
        agg[bn]["unrealized_pnl_quote"] += unrealized
        agg[bn]["global_pnl_quote"] += realized + unrealized
        agg[bn]["volume_traded"] += volume
        agg[bn]["cum_fees_quote"] += fees
        agg[bn]["num_controllers"] += 1
        # Track the freshest snapshot timestamp so suffix-tolerant resolution can
        # pick the most recent deploy of a re-launched bot.
        ts = str(snap.get("timestamp", "") or "")
        if ts > agg[bn]["timestamp"]:
            agg[bn]["timestamp"] = ts
        agg[bn]["controllers"].append(
            {
                "controller_id": snap.get("controller_id", ""),
                "controller_name": snap.get("controller_name", ""),
                "connector": snap.get("connector", snap.get("connector_name", "")),
                "trading_pair": snap.get("trading_pair", ""),
                "status": str(snap.get("status", "") or ""),
                "realized_pnl_quote": realized,
                "unrealized_pnl_quote": unrealized,
                "volume_traded": volume,
                "cum_fees_quote": fees,
                "positions_summary": positions,
            }
        )
    return agg


async def fetch_all_bot_performance(client: Any) -> dict[str, dict]:
    """Return ``{bot_name: aggregate}`` from the latest controller-performance snapshot.

    Each aggregate has ``realized_pnl_quote``, ``unrealized_pnl_quote``,
    ``global_pnl_quote``, ``volume_traded``, ``num_controllers`` and a
    ``controllers`` breakdown. Raises if the API call fails — callers decide how
    to degrade.
    """
    result = await client.bot_orchestration.get_latest_controller_performance()
    return _aggregate_by_bot(extract_snapshots(result))


def resolve_bot(all_bot_perf: dict[str, dict], bot_name: str) -> dict | None:
    """Resolve a configured ``bot_name`` to its live aggregate, suffix-tolerant.

    A bot deploys under an instance name with a timestamp suffix appended
    (``dn-CL-BRENTOIL-mm`` → ``dn-CL-BRENTOIL-mm-20260724-182221``), while the
    strategy config only knows the stable base name. Resolution order:

    1. exact match on the base name;
    2. otherwise, among keys of the form ``<base>-<suffix>``, the one with the
       freshest snapshot ``timestamp`` (ISO strings sort chronologically), so a
       re-launched bot resolves to its most recent deploy.

    Returns the aggregate dict (its ``bot_name`` is the full resolved name) or
    ``None`` when nothing matches.
    """
    if not bot_name or not all_bot_perf:
        return None
    exact = all_bot_perf.get(bot_name)
    if exact is not None:
        return exact
    prefix = f"{bot_name}-"
    candidates = [agg for key, agg in all_bot_perf.items() if key.startswith(prefix)]
    if not candidates:
        return None
    return max(candidates, key=lambda a: (str(a.get("timestamp", "")), a["bot_name"]))


def _clean_side(side: Any) -> str:
    """Normalize a ``TradeType.SELL``-style side into a bare ``SELL``/``BUY``."""
    s = str(side or "").upper()
    return s.rsplit(".", 1)[-1] if "." in s else s


def bot_executor_rows(aggregate: dict) -> list[dict[str, Any]]:
    """Build executor-like display rows from a resolved bot aggregate.

    One row per open position (from each controller's ``positions_summary``), in
    the same shape ``condor.agents.performance._executor_row`` emits, so the web
    executors tab and the agent's core-data view render bot-mode positions the
    same way as direct executors. Realized PnL from already-closed positions is
    not row-level here (the snapshot only summarizes open positions); it is still
    reflected in the aggregate totals the caller applies.
    """
    from datetime import datetime

    ts_epoch = 0.0
    ts_iso = str(aggregate.get("timestamp", "") or "")
    if ts_iso:
        try:
            ts_epoch = datetime.fromisoformat(ts_iso).timestamp()
        except ValueError:
            ts_epoch = 0.0

    rows: list[dict[str, Any]] = []
    for ctrl in aggregate.get("controllers", []):
        controller_id = ctrl.get("controller_id", "")
        status = str(ctrl.get("status", "") or "").upper()
        for pos in ctrl.get("positions_summary", []):
            pair = pos.get("trading_pair", "")
            entry = float(pos.get("breakeven_price", 0) or 0)
            amount = float(pos.get("amount", 0) or 0)
            unrealized = float(pos.get("unrealized_pnl_quote", 0) or 0)
            rows.append(
                {
                    "id": f"{controller_id}:{pair}" if pair else controller_id,
                    "type": "controller",
                    "connector": pos.get("connector_name", ctrl.get("connector", "")),
                    "pair": pair,
                    "side": _clean_side(pos.get("side")),
                    "status": status,
                    "close_type": "",
                    # Row PnL is the live (unrealized) mark of the open position;
                    # realized carries in the aggregate totals, not per-row.
                    "pnl": unrealized,
                    "volume": float(pos.get("volume_traded_quote", 0) or 0),
                    "fees": float(pos.get("cum_fees_quote", 0) or 0),
                    "entry_price": entry,
                    "current_price": 0.0,
                    "amount": abs(amount) * entry,
                    "timestamp": ts_epoch,
                    "close_timestamp": 0.0,
                    "controller_id": controller_id,
                    "custom_info": {},
                    "config": {},
                }
            )
    return rows


async def fetch_bot_performance(client: Any, bot_name: str) -> dict | None:
    """Return the aggregate for a single bot, or ``None`` if it has no snapshot.

    Suffix-tolerant (see :func:`resolve_bot`). Resilient: swallows API errors and
    returns ``None`` so a caller merging this into other performance never breaks
    on a transient bot-orchestration hiccup.
    """
    if not client or not bot_name:
        return None
    try:
        all_perf = await fetch_all_bot_performance(client)
    except Exception as e:
        logger.debug("fetch_bot_performance(%s) failed: %s", bot_name, e)
        return None
    return resolve_bot(all_perf, bot_name)
