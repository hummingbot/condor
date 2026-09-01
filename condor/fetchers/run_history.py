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

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from condor.fetchers._pagination import collect_pages
from condor.fetchers.bot_performance import extract_snapshots
from condor.run_history_store import (
    RunHistoryEntry,
    get_run_history_store,
    is_settled,
    run_key,
)
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


# ── One finished run's sampled history ──


class RunHistoryUnavailable(Exception):
    """The run's history could not be read.

    ``missing`` separates "the server has no rows for this run" from "the
    backend failed", which is the whole of what a caller needs in order to
    choose between saying so and reporting an error. Same convention as
    :class:`condor.fetchers.archived_run.ArchivedRunUnavailable`.
    """

    def __init__(self, detail: str, *, missing: bool = False):
        super().__init__(detail)
        self.detail = detail
        self.missing = missing


#: How many points one controller's curve is thinned to.
#:
#: The same budget the client's own sampler uses, and for the same two reasons
#: that happen to agree on it: a page is capped at 1000 rows upstream, and the
#: chart is about a thousand pixels wide, so everything past this is fetched,
#: parsed, stored, folded and drawn onto a column that is already lit.
HISTORY_POINT_BUDGET = 1000

#: Rows per upstream page. Upstream caps ``limit`` at 1000 and answers 422 above
#: it (CORR-260), so asking for more turns a chart into an error.
_PAGE_SIZE = 1000

#: The ceiling on one controller's walk.
#:
#: The cursor upstream hands back is a *timestamp*, so two rows sharing one
#: microsecond could in principle stall the walk; the shared walker's
#: non-advancing-cursor guard already ends that, and this bounds the genuinely
#: enormous case. Twenty pages is a year of five-minute samples for one
#: controller — far past anything the budget above will draw.
_MAX_ROWS_PER_CONTROLLER = 20_000

#: How many controllers are walked at once.
#:
#: A finished run has a handful; a wide fleet has a dozen. Serial, twelve
#: controllers took 17.7s of wall clock against the same bytes — this is the
#: difference between a spinner and a wait. Bounded because these are sequential
#: page walks against one API that also serves the live fleet.
_WALK_CONCURRENCY = 4

_ORDER = ("5m", "15m", "30m", "1h", "4h", "12h", "1d")
_INTERVAL_MS = {
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def pick_interval(span_ms: float, budget: int = HISTORY_POINT_BUDGET) -> str:
    """The finest interval whose point count over ``span_ms`` fits the budget.

    The same ladder the client uses (``pickSamplingInterval``), and it means the
    same thing here because the walk is per controller: upstream's downsampler
    buckets by time only, so asking for a coarse interval across *several*
    controllers keeps one row per bucket and drops the rest — but asking for it
    with a ``controller_id`` bound leaves only that controller's rows in each
    bucket, and nothing is lost at any rung.

    Upstream validates the parameter against exactly this set and answers 422
    for anything else, so a value outside it turns a chart into an error rather
    than a coarser chart.
    """
    if not span_ms or span_ms <= 0:
        return _ORDER[0]
    for interval in _ORDER:
        if math.ceil(span_ms / _INTERVAL_MS[interval]) <= budget:
            return interval
    return _ORDER[-1]


def _to_ms(timestamp: Any) -> float | None:
    if isinstance(timestamp, (int, float)):
        return float(timestamp) * (1000 if timestamp < 1e12 else 1)
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() * 1000


def project_rows(rows: Iterable[dict]) -> tuple[list[list[float]], str, str]:
    """One controller's rows as points, plus the identity they carry.

    A point is ``[t_ms, realized, unrealized, net, volume, pct]`` — six floats,
    against a raw row that also carries ``custom_info`` (by far the largest
    object in the payload for a grid or LP controller) and a whole
    ``positions_summary``. Projecting here is what turns 4.2 MB of upstream into
    ~30 KB on disk.

    The identity is resolved from the **first row that has a position**, walking
    forward through the run: a controller that stopped flat has no position in
    its last snapshot but almost always had one earlier, which is exactly the
    case ``positions_summary`` on the latest row cannot answer.
    """
    points: list[list[float]] = []
    connector = pair = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        at = _to_ms(row.get("timestamp"))
        if at is None:
            continue
        perf = row.get("performance")
        if not isinstance(perf, dict):
            perf = row
        if not pair and not connector:
            connector, pair = _identity_from_positions(perf)
        points.append(
            [
                at,
                float(perf.get("realized_pnl_quote", 0) or 0),
                float(perf.get("unrealized_pnl_quote", 0) or 0),
                float(perf.get("global_pnl_quote", 0) or 0),
                float(perf.get("volume_traded", 0) or 0),
                float(perf.get("global_pnl_pct", 0) or 0),
            ]
        )
    points.sort(key=lambda p: p[0])
    return points, connector, pair


def downsample(
    points: list[list[float]], budget: int = HISTORY_POINT_BUDGET
) -> list[list[float]]:
    """Thin one controller's curve to at most ``budget`` points.

    Last value per time bucket, not every Nth row: the series is cumulative PnL,
    so the last reading in a bucket is what the bucket *ended at*, which is what
    a step on the chart means. Taking every Nth row would drift with a controller
    that reports irregularly.

    Bucketed by **time** rather than by index so the thinned curve is evenly
    spaced on the axis it is drawn against — a gap in reporting stays a gap
    instead of being compressed away. The final point is always kept: it is the
    run's outcome, and a curve whose last bucket happened to be sparse would
    otherwise stop short of the number the strip above it reports.
    """
    if len(points) <= budget:
        return points
    start = points[0][0]
    span = points[-1][0] - start
    if span <= 0:
        return points[-budget:]

    # ``budget - 1`` buckets, not ``budget``: the last point is appended
    # unconditionally below, so dividing by the budget itself would land the
    # final instant in a bucket of its own and answer with ``budget + 1``
    # points — a budget that is exceeded by one every time is not a budget.
    bucket = span / max(1, budget - 1)
    out: list[list[float]] = []
    current_index: int | None = None
    for point in points:
        index = int((point[0] - start) // bucket)
        if current_index is not None and index != current_index:
            out.append(pending)
        current_index = index
        pending = point
    if not out or out[-1] is not points[-1]:
        out.append(points[-1])
    return out


@dataclass
class RunHistory:
    """One finished run's curve, per controller, ready to draw."""

    #: ``controller_id -> [[t_ms, realized, unrealized, net, volume, pct], ...]``
    controllers: dict[str, list[list[float]]]
    #: ``controller_id -> {"connector", "trading_pair"}``. What the *fold* needs:
    #: a leaf with no pair converts as though its quote were dollars.
    identities: dict[str, dict[str, str]]
    interval: str
    #: Which source answered — never assumed, always discovered per run.
    source: str
    points: int
    #: True when the answer came off disk rather than off the wire.
    cached: bool = False


# Single-flight, keyed like the store, so concurrent cold-cache readers of one
# run share a walk instead of each paying for their own. Same idiom as
# ``archived_run.py``: the fetch is a detached task and awaiters ``shield`` it,
# so one reader navigating away cannot cancel the walk the others are waiting on.
_inflight: dict[str, "asyncio.Task[RunHistory]"] = {}


async def fetch_run_history(
    client: Any,
    server: str,
    *,
    bot_name: str,
    deployed_at: str,
    stopped_at: str | None,
    controller_ids: Iterable[str],
    db_path: str | None = None,
) -> RunHistory:
    """One finished run's sampled history, walked once and cached for ever.

    Raises :class:`RunHistoryUnavailable` with ``missing=True`` when the server
    has no rows for this run — which is an *answer*, not an error: the snapshot
    table has a retention floor and a run older than it was never recorded. The
    route turns that into ``source: "none"`` rather than a 404.
    """
    key = run_key(server, bot_name, deployed_at)
    store = get_run_history_store()

    cached = store.get(key)
    if cached is not None:
        entry, series = cached
        return RunHistory(
            controllers=series,
            identities=entry.controllers,
            interval=entry.interval,
            source=entry.source,
            points=entry.points,
            cached=True,
        )

    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(
            _build(
                client,
                server,
                key=key,
                bot_name=bot_name,
                deployed_at=deployed_at,
                stopped_at=stopped_at,
                controller_ids=list(controller_ids),
                db_path=db_path,
            )
        )
        _inflight[key] = task

        def _clear(finished: "asyncio.Task", _key: str = key) -> None:
            if _inflight.get(_key) is finished:
                _inflight.pop(_key, None)

        task.add_done_callback(_clear)
    return await asyncio.shield(task)


async def _build(
    client: Any,
    server: str,
    *,
    key: str,
    bot_name: str,
    deployed_at: str,
    stopped_at: str | None,
    controller_ids: list[str],
    db_path: str | None,
) -> RunHistory:
    start_ms = _to_ms(deployed_at) or 0.0
    end_ms = _to_ms(stopped_at) if stopped_at else None
    span = (end_ms or time.time() * 1000) - start_ms
    interval = pick_interval(span)

    # The window is widened by one bucket at each end. A run's first dump lands
    # a moment after its deploy row is written and its last a moment after the
    # stop, and a window clipped to the run's own timestamps drops both — which
    # for a short run is a visible fraction of the curve.
    pad = _INTERVAL_MS[interval]
    window = {
        "start_time": _iso(start_ms - pad),
        "end_time": _iso(end_ms + pad) if end_ms else None,
    }

    ids = list(controller_ids)
    if not ids:
        # A run that declared nothing: ask once, unfiltered, and take whatever
        # controllers come back. The interval collapse (see the module note)
        # applies to this path and cannot be avoided — there is no id to bind —
        # so it asks at the finest rung and accepts the cost.
        rows = await _walk(
            client, bot_name=bot_name, controller_id=None, interval="5m", window=window
        )
        ids = sorted(
            {str(r.get("controller_id") or "") for r in rows if isinstance(r, dict)}
            - {""}
        )
        by_controller = {
            cid: [
                r for r in rows if isinstance(r, dict) and r.get("controller_id") == cid
            ]
            for cid in ids
        }
    else:
        semaphore = asyncio.Semaphore(_WALK_CONCURRENCY)

        async def _one(controller_id: str) -> tuple[str, list[dict]]:
            async with semaphore:
                return controller_id, await _walk(
                    client,
                    bot_name=bot_name,
                    controller_id=controller_id,
                    interval=interval,
                    window=window,
                )

        by_controller = dict(await asyncio.gather(*(_one(cid) for cid in ids)))

    controllers: dict[str, list[list[float]]] = {}
    identities: dict[str, dict[str, str]] = {}
    for controller_id, rows in by_controller.items():
        points, connector, pair = project_rows(rows)
        if not points:
            continue
        controllers[controller_id] = downsample(points)
        identities[controller_id] = {"connector": connector, "trading_pair": pair}

    source = "snapshots"
    if not controllers and db_path:
        # The snapshot table has a retention floor, and this run started before
        # it. The archived database is the only record left of what it did, and
        # it is already built and already cached (``archived_run.py``) — but it
        # is a run-level trade walk, not a per-controller sampled series, so it
        # is emphatically the fallback and is labelled as one all the way to the
        # notice under the chart.
        controllers, identities = await _from_archive(client, server, db_path)
        source = "archive"

    if not controllers:
        raise RunHistoryUnavailable(f"No recorded history for {bot_name}", missing=True)

    total = sum(len(v) for v in controllers.values())
    history = RunHistory(
        controllers=controllers,
        identities=identities,
        interval=interval,
        source=source,
        points=total,
    )

    # Written only once the run can no longer change. A run served live here is
    # simply not stored, and the next reader pays the walk again — which is the
    # right trade against an immutable entry that is wrong for ever.
    if is_settled(stopped_at):
        get_run_history_store().put(
            key,
            RunHistoryEntry(
                server=server,
                bot_name=bot_name,
                deployed_at=deployed_at,
                stopped_at=stopped_at or "",
                controllers=identities,
                points=total,
                interval=interval,
                source=source,
            ),
            controllers,
        )
    return history


async def _walk(
    client: Any,
    *,
    bot_name: str,
    controller_id: str | None,
    interval: str,
    window: dict[str, str | None],
) -> list[dict]:
    """Every page of one controller's history, in order."""

    async def _page(**kwargs):
        return await client.bot_orchestration.get_controller_performance_history(
            bot_name=bot_name,
            controller_id=controller_id,
            interval=interval,
            start_time=window.get("start_time"),
            end_time=window.get("end_time"),
            **kwargs,
        )

    try:
        return await collect_pages(
            _page,
            extract_snapshots,
            page_size=_PAGE_SIZE,
            max_items=_MAX_ROWS_PER_CONTROLLER,
        )
    except Exception as e:
        raise RunHistoryUnavailable(f"Failed to read history for {bot_name}: {e}")


def _iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


#: The controller id an archive-derived curve is filed under.
#:
#: The archived database's ``cumulative_pnl`` is one series for the **whole
#: run**, computed from its trade table — it has no per-controller breakdown and
#: cannot be given one without inventing a split nobody recorded. So it is filed
#: under a reserved id that names what it actually is, rather than being
#: attributed to whichever controller happened to be listed first. Deliberately
#: not ``""``: ``controllerKey`` reads an empty id as "drop this", so the series
#: would silently never be drawn.
ARCHIVE_SERIES_ID = "(run)"


async def _from_archive(
    client: Any, server: str, db_path: str
) -> tuple[dict[str, list[list[float]]], dict[str, dict[str, str]]]:
    """One run's curve out of its archived sqlite, or nothing.

    Weaker than the snapshot series in three ways the caller has to keep saying
    out loud: it is per *run* rather than per controller, it has no unrealized
    component (a closed trade has nothing left unrealized), and it carries no
    volume series. That is what the archive records, and stating it is better
    than drawing a fabricated split.
    """
    from condor.fetchers.archived_run import ArchivedRunUnavailable, fetch_archived_run

    try:
        perf = await fetch_archived_run(client, server, db_path)
    except ArchivedRunUnavailable:
        return {}, {}
    except Exception:
        logger.warning("Archived fallback failed for %s", db_path, exc_info=True)
        return {}, {}

    points = [
        [float(p.timestamp) * 1000, float(p.pnl), 0.0, float(p.pnl), 0.0, 0.0]
        for p in (perf.cumulative_pnl or [])
        if p.timestamp
    ]
    if not points:
        return {}, {}
    points.sort(key=lambda p: p[0])

    # ``ArchivedBotPerformance`` already restates its figures in USD when it
    # can, and says so through ``converted``. Handing back the pair as well
    # would convert them a second time; handing back nothing when it *could
    # not* convert would report the run's own currency as dollars. So the pair
    # rides along exactly when it is still needed.
    pair = "" if perf.converted else perf.primary_trading_pair
    return (
        {ARCHIVE_SERIES_ID: downsample(points)},
        {
            ARCHIVE_SERIES_ID: {
                "connector": perf.primary_connector,
                "trading_pair": pair,
            }
        },
    )
