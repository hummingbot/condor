"""Group an archived run's executors by the controller that ran them.

A run is one bot, but a bot runs several controllers, and until now Condor had
no controller dimension on its history at all: ``controller_id`` reaches the API
on every executor and nothing ever grouped by it, so a run with three
controllers read as one undifferentiated number.

Pure computation over normalized executors (attribute access, not dicts), with
no client, no plotting and no web imports, so both the web routes and routines
can call it — the same shape as :mod:`condor.archived_chart_series`.

The rollup is *executor*-derived. That reconciles exactly with a run whose
headline stats came from executors (``stats_source == "executors"``) and closely
— not to the cent — with a trade-sourced one, where the run totals are
reconstructed from fills rather than from what each executor reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ControllerRollup:
    """What one controller did inside a run. Money is USD."""

    controller_id: str
    pnl_usd: float = 0.0
    volume_usd: float = 0.0
    fees_usd: float = 0.0
    executor_count: int = 0
    # Epoch seconds: first executor opened, last one closed (or opened, for one
    # that never closed).
    first_ts: float = 0.0
    last_ts: float = 0.0
    trading_pairs: list[str] = field(default_factory=list)
    connectors: list[str] = field(default_factory=list)


def group_by_controller(executors: list[Any]) -> list[ControllerRollup]:
    """One rollup per ``controller_id``, ordered by absolute PnL.

    Executors carrying no controller collapse into a single ``""`` row rather
    than being dropped: an LP or a manual run has no controller and still has to
    appear, and its money is part of the run's.

    Each executor's money is restated in USD through its own ``usd_rate``, which
    the fetcher stamped per market — a run spanning a BRL and a USDT market
    totals correctly because the rate travels on the row, not on the run.
    """
    rollups: dict[str, ControllerRollup] = {}
    pairs: dict[str, dict[str, None]] = {}
    connectors: dict[str, dict[str, None]] = {}

    for ex in executors:
        key = str(getattr(ex, "controller_id", "") or "")
        rollup = rollups.get(key)
        if rollup is None:
            rollup = rollups[key] = ControllerRollup(controller_id=key)
            # Insertion-ordered sets: the first market a controller touched
            # reads first, which is the order the run itself happened in.
            pairs[key] = {}
            connectors[key] = {}

        rate = float(getattr(ex, "usd_rate", 1.0) or 1.0)
        rollup.pnl_usd += float(getattr(ex, "pnl", 0) or 0) * rate
        rollup.volume_usd += float(getattr(ex, "volume", 0) or 0) * rate
        rollup.fees_usd += float(getattr(ex, "cum_fees_quote", 0) or 0) * rate
        rollup.executor_count += 1

        opened = float(getattr(ex, "timestamp", 0) or 0)
        closed = float(getattr(ex, "close_timestamp", 0) or 0)
        for stamp in (opened, closed):
            if stamp <= 0:
                continue
            rollup.first_ts = (
                stamp if rollup.first_ts == 0 else min(rollup.first_ts, stamp)
            )
            rollup.last_ts = max(rollup.last_ts, stamp)

        pair = str(getattr(ex, "trading_pair", "") or "")
        if pair:
            pairs[key][pair] = None
        connector = str(getattr(ex, "connector", "") or "")
        if connector:
            connectors[key][connector] = None

    for key, rollup in rollups.items():
        rollup.trading_pairs = list(pairs[key])
        rollup.connectors = list(connectors[key])

    return sorted(rollups.values(), key=lambda r: abs(r.pnl_usd), reverse=True)
