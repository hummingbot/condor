"""What a finished run leaves behind: its controllers, and their whole history.

``controller-performance-latest`` and ``controller-performance-history`` look
like live-fleet routes and are not. They are the record of every controller of
every bot the API has ever orchestrated, and the rows outlive the bot: measured
on a real server, ``latest`` answers with 139 rows across 86 bots in one call,
of which only 8 are still deployed, and ``history`` walks a bot that stopped a
week ago just as readily as one still trading. Condor read both as live-only,
which is the whole reason the Terminated population had controllers to name but
no history to draw (FEAT-089).

Two things live here:

* :func:`terminated_controllers` — one snapshot per controller of every run that
  has finished, mapped onto ``ControllerInfo``. Deliberately *not* through
  ``ControllerPerformanceSnapshot.from_raw``, which drops ``close_type_counts``
  on purpose (PERF-261): a point on a chart does not need them, but the close
  type strip that leads the scope header does.
* :func:`fetch_run_history` — one finished run's sampled PnL curve, per
  controller, walked once and cached forever (see
  :mod:`condor.run_history_store`).

The walk is **per controller**, and that is a correctness requirement rather
than a tuning choice. Upstream's downsampler buckets by *time only*, so a
request that spans several controllers keeps one row per bucket and silently
drops the rest: the same 12-controller fleet over the same window answers with
12 of 12 controllers at ``5m`` and 11 of 12 at ``1h``, and coarser is worse.
Filtering to one ``controller_id`` first means each bucket holds only that
controller's rows, so nothing is dropped at any interval — 12 separate requests
at ``1h`` return all 12 series in 1.28 MB, against 140 MB for the same span at
``5m``.
"""

from __future__ import annotations

import logging
from typing import Iterable

from condor.web.models import BotRunInfo, ControllerInfo

logger = logging.getLogger(__name__)


def _identity_from_positions(perf: dict) -> tuple[str, str]:
    """The connector and pair a controller traded, out of its open positions.

    A ``controller-performance`` row carries **no** top-level ``connector`` or
    ``trading_pair``; they live one level down, per position. This matters far
    more than it looks: ``foldLeaves`` converts a leaf through ``leaf.pair``, so
    a controller with an empty pair is folded as though its quote were dollars —
    which on a BRL fleet overstates every figure by the whole BRL/USD rate.

    Returns empty strings when the controller stopped flat and holds no
    position. That is honest and recoverable — the history walk sees the rows
    from when it *did* hold one — and it is emphatically better than defaulting
    to a quote nobody traded.
    """
    positions = perf.get("positions_summary")
    if not isinstance(positions, list):
        return "", ""
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        connector = str(pos.get("connector_name") or pos.get("connector") or "")
        pair = str(pos.get("trading_pair") or "")
        if connector or pair:
            return connector, pair
    return "", ""


def terminated_controllers(
    snapshots: Iterable[dict],
    runs: Iterable[BotRunInfo],
) -> tuple[list[ControllerInfo], int]:
    """The controllers of every run that has finished, and how many runs that is.

    The join is on ``bot_name``, which a deployment writes identically into the
    run row and into every snapshot the bot reports. A snapshot whose bot has no
    run record is dropped rather than shown under a bot nobody can open: without
    the run there is no deploy time, no stop time and no archive to reach, so
    the node would be a name with nothing behind it.

    A run that is still live is excluded here and *only* here. The Running
    population already reports it out of the live fleet, and a bot listed in
    both would be counted twice by any fold that spans them.
    """
    by_bot = {run.bot_name: run for run in runs if run.bot_name}
    seen_runs: set[str] = set()
    out: list[ControllerInfo] = []

    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        bot_name = snap.get("bot_name") or ""
        controller_id = snap.get("controller_id") or ""
        run = by_bot.get(bot_name)
        if not bot_name or not controller_id or run is None or run.is_live:
            continue

        perf = snap.get("performance")
        if not isinstance(perf, dict):
            perf = snap
        connector, pair = _identity_from_positions(perf)
        close_types = perf.get("close_type_counts")
        positions = perf.get("positions_summary")

        seen_runs.add(bot_name)
        out.append(
            ControllerInfo(
                # Upstream reports no ``controller_name`` on these rows at all
                # (checked across every row of a real server's table), so the
                # config id is the only name this controller has.
                controller_name="",
                controller_id=controller_id,
                bot_name=bot_name,
                # Never the row's own ``status``: it is a hardcoded "running"
                # in this payload, which for a bot that stopped a week ago is
                # simply false. The run is what knows.
                status="stopped",
                connector=connector,
                trading_pair=pair,
                realized_pnl_quote=float(perf.get("realized_pnl_quote", 0) or 0),
                unrealized_pnl_quote=float(perf.get("unrealized_pnl_quote", 0) or 0),
                global_pnl_quote=float(perf.get("global_pnl_quote", 0) or 0),
                global_pnl_pct=float(perf.get("global_pnl_pct", 0) or 0),
                volume_traded=float(perf.get("volume_traded", 0) or 0),
                close_type_counts=close_types if isinstance(close_types, dict) else {},
                positions_summary=positions if isinstance(positions, list) else [],
                deployed_at=run.created_at,
            )
        )

    return out, len(seen_runs)


def declared_controllers(run: BotRunInfo) -> list[ControllerInfo]:
    """The controllers a run declared, for a run that left no snapshot at all.

    The snapshot table has a retention floor — a property of the deployment,
    not of this code — and a run older than it has rows for none of its
    controllers. Its deployment still named them, and without this the run would
    have no leaf, therefore no bot node, therefore no row on screen: it would
    not read as "a run we know nothing about", it would read as a run that never
    happened. So it keeps its shape, at zero, which is what having no record
    honestly looks like.

    Only for a run with **nothing**, never to top up one that is partly covered.
    A zero-valued controller beside real ones would count in the scope's leaf
    count and drag its win rate down over trading it has no record of — a
    distortion of numbers that are otherwise measured. For a run with no record
    at all there is nothing to distort.
    """
    return [
        ControllerInfo(
            controller_name="",
            controller_id=controller_id,
            bot_name=run.bot_name,
            status="stopped",
            deployed_at=run.created_at,
        )
        for controller_id in run.controller_ids
    ]
