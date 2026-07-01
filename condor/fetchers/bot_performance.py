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
    """Roll up per-controller snapshots into one aggregate per bot_name."""
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
                "num_controllers": 0,
                "controllers": [],
            }
        realized = float(perf.get("realized_pnl_quote", 0) or 0)
        unrealized = float(perf.get("unrealized_pnl_quote", 0) or 0)
        volume = float(perf.get("volume_traded", 0) or 0)
        agg[bn]["realized_pnl_quote"] += realized
        agg[bn]["unrealized_pnl_quote"] += unrealized
        agg[bn]["global_pnl_quote"] += realized + unrealized
        agg[bn]["volume_traded"] += volume
        agg[bn]["num_controllers"] += 1
        agg[bn]["controllers"].append(
            {
                "controller_id": snap.get("controller_id", ""),
                "controller_name": snap.get("controller_name", ""),
                "connector": snap.get("connector", snap.get("connector_name", "")),
                "trading_pair": snap.get("trading_pair", ""),
                "realized_pnl_quote": realized,
                "unrealized_pnl_quote": unrealized,
                "volume_traded": volume,
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


async def fetch_bot_performance(client: Any, bot_name: str) -> dict | None:
    """Return the aggregate for a single bot, or ``None`` if it has no snapshot.

    Resilient: swallows API errors and returns ``None`` so a caller merging this
    into other performance never breaks on a transient bot-orchestration hiccup.
    """
    if not client or not bot_name:
        return None
    try:
        all_perf = await fetch_all_bot_performance(client)
    except Exception as e:
        logger.debug("fetch_bot_performance(%s) failed: %s", bot_name, e)
        return None
    return all_perf.get(bot_name)
