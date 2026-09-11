from __future__ import annotations

import json
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from condor.fetchers._pagination import next_cursor as _next_cursor
from condor.fetchers.bot_performance import extract_snapshots as _extract_snapshots
from condor.fetchers.bot_performance import (
    fetch_all_bot_performance,
    fetch_archived_paths,
)
from condor.fetchers.performance_history import (
    PerformanceHistoryUnsupported,
)
from condor.fetchers.performance_history import extract_rows as extract_performance_rows
from condor.fetchers.performance_history import (
    fetch_performance_history,
    probe_performance_history,
    reject_foreign_filters,
)
from condor.fetchers.run_history import (
    RunHistoryUnavailable,
    declared_controllers,
    fetch_run_history,
    fill_classes_from_config,
    fill_pairs_from_cache,
    terminated_controllers,
)
from condor.server_data_service import ServerDataType, get_server_data_service
from condor.web.auth import require_server_access
from condor.web.models import (
    BotRunInfo,
    BotRunsResponse,
    ControllerPerformanceHistoryResponse,
    ControllerPerformanceLatestResponse,
    ControllerPerformanceSnapshot,
    PerformanceCapabilityResponse,
    PerformanceHistoryResponse,
    PerformanceSnapshot,
    RunHistoryResponse,
    TerminatedControllersResponse,
    WebUser,
)
from condor.web.routes._errors import upstream_error
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["controller-performance"])


# ── Helpers ──


def parse_controller_ids(deployment_config: object) -> list[str]:
    """The controller config ids a run was deployed with.

    ``deployment_config`` arrives as a *JSON string* on the wire, holding the
    whole deploy request; the one field worth keeping is
    ``controllers_config``, which names every controller the run was started
    with. Everything else in that blob is bulk nobody reads (see the
    ``_fetch_bot_runs`` note in ``bots.py`` about forwarding payloads whole), so
    the ids are lifted out here and the blob is dropped.

    The ``.yml`` suffix is stripped because a controller reports itself by the
    bare id — the snapshots, the executors and the config routes all use
    ``btcbrl-ganjahro-1__toppnl``, while a deploy may name the file it came
    from. Servers differ on this: brigado writes the bare id today, so the strip
    is what makes the two spellings the same key rather than two.
    """
    if isinstance(deployment_config, str):
        try:
            deployment_config = json.loads(deployment_config)
        except (ValueError, TypeError):
            return []
    if not isinstance(deployment_config, dict):
        return []
    raw_ids = deployment_config.get("controllers_config")
    if not isinstance(raw_ids, list):
        return []
    ids = []
    for entry in raw_ids:
        if not isinstance(entry, str) or not entry.strip():
            continue
        name = entry.strip()
        if name.endswith(".yml"):
            name = name[:-4]
        elif name.endswith(".yaml"):
            name = name[:-5]
        ids.append(name)
    return ids


def _parse_bot_run(
    raw: dict,
    perf_by_bot: dict[str, dict] | None = None,
    archive_paths: dict[str, str] | None = None,
) -> BotRunInfo:
    """Normalize a raw bot run dict into our model."""
    bot_name = raw.get("bot_name", "")
    realized = 0.0
    unrealized = 0.0
    volume = 0.0
    num_controllers = 0

    if perf_by_bot and bot_name in perf_by_bot:
        agg = perf_by_bot[bot_name]
        realized = agg.get("realized_pnl_quote", 0.0)
        unrealized = agg.get("unrealized_pnl_quote", 0.0)
        volume = agg.get("volume_traded", 0.0)
        num_controllers = agg.get("num_controllers", 0)

    stopped_at = str(raw["stopped_at"]) if raw.get("stopped_at") else None
    deployment_status = raw.get("deployment_status", "")

    return BotRunInfo(
        bot_name=bot_name,
        bot_run_id=raw.get("id"),
        account_name=raw.get("account_name", ""),
        strategy_type=raw.get("strategy_type", ""),
        strategy_name=raw.get("strategy_name", ""),
        run_status=raw.get("run_status", raw.get("status", "")),
        deployment_status=deployment_status,
        created_at=str(raw["deployed_at"]) if raw.get("deployed_at") else None,
        stopped_at=stopped_at,
        controller_ids=parse_controller_ids(raw.get("deployment_config")),
        # Not read off ``run_status``: upstream never writes ``RUNNING``. Over
        # 150 runs on a real server the only values are ``STOPPED`` and
        # ``CREATED``, and every bot trading right now is a ``CREATED`` one —
        # so a filter on that string files the live fleet under history.
        is_live=deployment_status == "DEPLOYED" and not stopped_at,
        realized_pnl_quote=realized,
        unrealized_pnl_quote=unrealized,
        global_pnl_quote=realized + unrealized,
        volume_traded=volume,
        num_controllers=num_controllers,
        archive_db_path=(archive_paths or {}).get(bot_name),
    )


# ── Bot Runs ──


@router.get(
    "/servers/{name}/bot-runs",
    response_model=BotRunsResponse,
)
async def get_bot_runs(
    name: str,
    bot_name: Optional[str] = Query(None),
    account_name: Optional[str] = Query(None),
    strategy_type: Optional[str] = Query(None),
    strategy_name: Optional[str] = Query(None),
    run_status: Optional[str] = Query(None),
    deployment_status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: WebUser = Depends(require_server_access),
):
    """Get bot runs with optional filtering."""
    import asyncio

    cm = get_config_manager()

    client = await cm.get_client(name)

    async def _fetch_runs():
        return await client.bot_orchestration.get_bot_runs(
            bot_name=bot_name,
            account_name=account_name,
            strategy_type=strategy_type,
            strategy_name=strategy_name,
            run_status=run_status,
            deployment_status=deployment_status,
            limit=limit,
            offset=offset,
        )

    async def _fetch_perf() -> dict[str, dict]:
        """Fetch latest controller performance and aggregate by bot_name."""
        try:
            return await fetch_all_bot_performance(client)
        except Exception:
            logger.debug(
                "Could not fetch controller performance for bot runs enrichment"
            )
            return {}

    async def _fetch_archives() -> dict[str, str]:
        """Which of these runs left a database behind, and where.

        This is what lets one Runs table stand in for the old separate Archived
        tab: the upstream listing is a directory walk that opens no sqlite, so
        marking every archived run costs one cheap call instead of the per-database
        ``/summary`` fan-out the archived list used to pay for.
        """
        try:
            return await fetch_archived_paths(client)
        except Exception:
            logger.debug("Could not list archived databases for bot runs enrichment")
            return {}

    try:
        result, perf_by_bot, archive_paths = await asyncio.gather(
            _fetch_runs(), _fetch_perf(), _fetch_archives()
        )
    except Exception as e:
        logger.exception("Failed to fetch bot runs from '%s'", name)
        raise upstream_error("Failed to fetch bot runs", e)

    runs_list = _extract_runs_list(result)

    return BotRunsResponse(
        runs=[_parse_bot_run(r, perf_by_bot, archive_paths) for r in runs_list],
        total=len(runs_list),
    )


@router.delete(
    "/servers/{name}/bot-runs/{bot_run_id}",
)
async def delete_bot_run(
    name: str,
    bot_run_id: int,
    user: WebUser = Depends(require_server_access),
):
    """Delete an archived bot run by its numeric ID."""
    cm = get_config_manager()

    client = await cm.get_client(name)

    try:
        result = await client.bot_orchestration.delete_bot_run(bot_run_id)
    except Exception as e:
        logger.exception("Failed to delete bot run %d from '%s'", bot_run_id, name)
        raise upstream_error("Failed to delete bot run", e)

    # The run is gone upstream, but the Terminated listing this server was
    # serving warm still names it, and a browser refetching straight after the
    # delete would be handed back the very rows it just removed (CORR-298).
    # Every page size is dropped, not just the one someone happened to ask for:
    # the cache is keyed ``(server, limit)`` and each entry has its own copy of
    # the run.  Other servers keep theirs — this delete says nothing about them.
    drop_terminated_cache(name)

    return {"deleted": True, "bot_run_id": bot_run_id, "result": result}


def _extract_runs_list(result) -> list[dict]:
    """Normalize bot runs API response into a list of dicts."""
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if isinstance(result, dict):
        data = result.get("data", result.get("runs", result))
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            # Dict keyed by bot_name
            return [
                {"bot_name": k, **v} for k, v in data.items() if isinstance(v, dict)
            ]
    return []


# ── Controller Performance: Latest ──


@router.get(
    "/servers/{name}/controller-performance/latest",
    response_model=ControllerPerformanceLatestResponse,
)
async def get_latest_controller_performance(
    name: str,
    bot_name: Optional[str] = Query(None),
    user: WebUser = Depends(require_server_access),
):
    """Get the most recent performance snapshot for each bot/controller."""
    cm = get_config_manager()

    client = await cm.get_client(name)

    try:
        result = await client.bot_orchestration.get_latest_controller_performance(
            bot_name=bot_name,
        )
    except Exception as e:
        logger.warning(
            "Failed to fetch latest controller performance from '%s': %s", name, e
        )
        return ControllerPerformanceLatestResponse(
            server_online=False,
            error_hint=f"Connection error: {e}",
        )

    snapshots = _extract_snapshots(result)

    return ControllerPerformanceLatestResponse(
        snapshots=[ControllerPerformanceSnapshot.from_raw(s) for s in snapshots],
    )


# ── Controller Performance: History ──


@router.get(
    "/servers/{name}/controller-performance/history",
    response_model=ControllerPerformanceHistoryResponse,
)
async def get_controller_performance_history(
    name: str,
    bot_name: Optional[str] = Query(None),
    controller_id: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    interval: str = Query("5m"),
    # Upstream caps the page at 1000 (``le=1000`` on the Hummingbot API route),
    # so advertising more here only turned a request Condor called valid into a
    # 422 the except block below reported as an offline server. The explicit
    # default matters too: an omitted limit used not to be forwarded at all, and
    # upstream then served its own default of 100 rows — minutes of history —
    # with nothing to say the page had been truncated.
    limit: int = Query(1000, ge=1, le=1000),
    cursor: Optional[str] = Query(None),
    user: WebUser = Depends(require_server_access),
):
    """Get historical controller performance with pagination and interval sampling."""
    cm = get_config_manager()

    client = await cm.get_client(name)

    try:
        result = await client.bot_orchestration.get_controller_performance_history(
            bot_name=bot_name,
            controller_id=controller_id,
            start_time=start_time,
            end_time=end_time,
            interval=interval,
            limit=limit,
            cursor=cursor,
        )
    except Exception as e:
        logger.warning(
            "Failed to fetch controller performance history from '%s': %s", name, e
        )
        return ControllerPerformanceHistoryResponse(
            server_online=False,
            error_hint=f"Connection error: {e}",
        )

    snapshots = _extract_snapshots(result)

    return ControllerPerformanceHistoryResponse(
        snapshots=[ControllerPerformanceSnapshot.from_raw(s) for s in snapshots],
        # Upstream nests the cursor under "pagination"; the shared extractor
        # reads all four spellings the backend has used (CORR-259).
        next_cursor=_next_cursor(result),
        interval=interval,
    )


# ── The shared performance surface, over both populations (FEAT-087) ──


@router.get(
    "/servers/{name}/performance/capability",
    response_model=PerformanceCapabilityResponse,
)
async def get_performance_capability(
    name: str,
    user: WebUser = Depends(require_server_access),
):
    """Whether this server serves ``/performance/history``.

    A capability probe, not a version check: the question is whether the route
    answers, which is the only thing that actually decides what the browser can
    draw. The route landed in hummingbot/hummingbot-api#226 and is unreleased,
    so most servers answer no — that is the normal case, not a defensive edge,
    and the chart's notice tells a reader their series is derived *because
    their API is older* rather than leaving them to guess.

    Cached with the other per-server data, so it costs one request per server.
    Every chart on the page asks this route and gets the same warm answer; a
    tree click issues no request at all.
    """
    cm = get_config_manager()
    sds = get_server_data_service()

    try:
        result = await sds.get_or_fetch(name, ServerDataType.PERF_HISTORY_CAPABILITY)
    except ValueError:
        # SDS does not know this server. Ask it directly rather than reporting
        # a capability nobody probed — the answer is still one request.
        try:
            client = await cm.get_client(name)
            result = await probe_performance_history(client)
        except Exception as e:
            logger.debug("Performance capability probe failed for '%s': %s", name, e)
            result = {"supported": False, "unknown": True}

    if not isinstance(result, dict):
        return PerformanceCapabilityResponse(supported=False, unknown=True)
    return PerformanceCapabilityResponse(
        supported=bool(result.get("supported")),
        unknown=bool(result.get("unknown")),
        detail=result.get("detail"),
    )


@router.get(
    "/servers/{name}/performance/history",
    response_model=PerformanceHistoryResponse,
)
async def get_performance_history(
    name: str,
    subject: str = Query(..., pattern="^(controller|executor)$"),
    bot_name: Optional[str] = Query(None),
    controller_id: Optional[str] = Query(None),
    executor_id: Optional[str] = Query(None),
    executor_type: Optional[str] = Query(None),
    account_name: Optional[str] = Query(None),
    connector_name: Optional[str] = Query(None),
    trading_pair: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    interval: str = Query("5m", pattern="^(1m|5m|15m|30m|1h|4h|12h|1d)$"),
    # The same ceiling the controller history route carries, for the same
    # reason: upstream declares ``le=1000``, so advertising more here only turns
    # a request Condor called valid into a 422 (CORR-260).
    limit: int = Query(1000, ge=1, le=1000),
    cursor: Optional[str] = Query(None),
    user: WebUser = Depends(require_server_access),
):
    """One page of the shared performance history, for either population.

    Three failure modes, kept distinct because the browser draws something
    different for each:

    * **the route is not there** — 200 with ``supported: false``. An older API
      is not a broken one, and the client falls back to its derived series.
    * **the request was wrong** — forwarded as a 400. A filter aimed at the
      wrong population is the caller's mistake and must read as one; reporting
      it as an offline server would send the browser to a fallback and hide the
      bug (which is exactly why upstream 400s rather than serving an empty
      page). The cross-population check runs here too, so the common case does
      not spend a round trip to be told.
    * **the server did not answer** — ``server_online: false``, matching what
      the controller history route does for the same case.

    ``interval`` is a floor, not a guarantee: the echoed value says what was
    asked for, the timestamps say what was served.
    """
    foreign = reject_foreign_filters(
        subject,
        bot_name=bot_name,
        executor_id=executor_id,
        executor_type=executor_type,
        account_name=account_name,
        connector_name=connector_name,
        trading_pair=trading_pair,
    )
    if foreign:
        raise HTTPException(status_code=400, detail=foreign)

    cm = get_config_manager()
    client = await cm.get_client(name)

    try:
        result = await fetch_performance_history(
            client,
            subject=subject,
            bot_name=bot_name,
            controller_id=controller_id,
            executor_id=executor_id,
            executor_type=executor_type,
            account_name=account_name,
            connector_name=connector_name,
            trading_pair=trading_pair,
            start_time=start_time,
            end_time=end_time,
            interval=interval,
            limit=limit,
            cursor=cursor,
        )
    except PerformanceHistoryUnsupported as e:
        return PerformanceHistoryResponse(
            subject=subject, interval=interval, supported=False, error_hint=str(e)
        )
    except Exception as e:
        status = getattr(e, "status", None)
        if isinstance(status, int) and 400 <= status < 500:
            logger.info("Performance history rejected by '%s': HTTP %s", name, status)
            raise upstream_error("Failed to fetch performance history", e)
        logger.warning("Failed to fetch performance history from '%s': %s", name, e)
        return PerformanceHistoryResponse(
            subject=subject,
            interval=interval,
            server_online=False,
            error_hint=f"Connection error: {e}",
        )

    return PerformanceHistoryResponse(
        snapshots=[
            PerformanceSnapshot.from_raw(row)
            for row in extract_performance_rows(result)
        ],
        # Upstream nests the cursor under "pagination"; the shared extractor
        # reads every spelling the backend has used (CORR-259).
        next_cursor=_next_cursor(result),
        interval=interval,
        subject=subject,
    )


# ── Terminated: the controllers of every run that has finished ──

#: How long a terminated listing stays warm.
#:
#: It is one cheap upstream call, and its answer changes only when a bot stops —
#: which is minutes apart at best. Long enough that clicking around the
#: Terminated tree costs nothing, short enough that a bot stopped a minute ago
#: shows up without a reload.
_TERMINATED_TTL_SEC = 60

#: ``(server, limit) -> (expires_at, response)``. The limit belongs in the key:
#: it decides how many runs are fetched, and so both ``runs_seen`` and which
#: forgotten runs get topped up. Keyed by server alone, a listing built for one
#: page size was served as the answer to another. Bounded by the number of
#: configured servers times the handful of page sizes anyone asks for, which is
#: why it needs no eviction of its own.
_terminated_cache: dict[
    tuple[str, int], tuple[float, TerminatedControllersResponse]
] = {}


def clear_terminated_cache() -> None:
    """Drop every warm listing. For tests, and for a config reload."""
    _terminated_cache.clear()


def drop_terminated_cache(name: str) -> None:
    """Drop one server's warm listings, at every page size.

    For the events that make the listing wrong rather than merely old — a run
    deleted — where clearing the whole cache would punish every other server
    for it.
    """
    for key in [k for k in _terminated_cache if k[0] == name]:
        del _terminated_cache[key]


@router.get(
    "/servers/{name}/terminated/controllers",
    response_model=TerminatedControllersResponse,
)
async def get_terminated_controllers(
    name: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    user: WebUser = Depends(require_server_access),
):
    """The controllers of every finished run, as the browser reports them.

    ``controller-performance-latest`` is not a live-fleet route, whatever its
    neighbours imply: it is the final snapshot of every controller of every bot
    the API has ever orchestrated, and the rows outlive the bot. One call
    answers for the whole finished population — measured on a real server, 139
    rows across 86 bots, of which 8 are still deployed — which is why the
    Terminated tree can have real controllers under real bots for the cost of
    the listing it was already missing.

    Joined to the runs so each controller knows when its bot was deployed and
    which run it belongs to, and so the live ones can be excluded: the Running
    population already reports those out of the live fleet.
    """
    import asyncio
    import time

    cached = _terminated_cache.get((name, limit))
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]

    cm = get_config_manager()
    client = await cm.get_client(name)

    async def _fetch_latest():
        return await client.bot_orchestration.get_latest_controller_performance()

    async def _fetch_runs():
        return await client.bot_orchestration.get_bot_runs(limit=limit)

    try:
        latest, runs_raw = await asyncio.gather(_fetch_latest(), _fetch_runs())
    except Exception as e:
        logger.warning("Failed to fetch terminated controllers from '%s': %s", name, e)
        return TerminatedControllersResponse(
            server_online=False,
            error_hint=f"Connection error: {e}",
        )

    runs = [_parse_bot_run(r) for r in _extract_runs_list(runs_raw)]
    controllers, runs_seen = terminated_controllers(_extract_snapshots(latest), runs)

    # A run older than the snapshot table's retention floor has rows for none of
    # its controllers. Its deployment still named them, and a run with no leaf
    # gets no node and therefore no row — it would read as a run that never
    # happened rather than one we have no record of. See ``declared_controllers``
    # for why this tops up nothing and only fills in the empty.
    # A controller that stopped flat reports no pair, because the payload only
    # carries one inside its open positions — and a leaf with no pair is folded
    # as though its quote were dollars. Any run whose history has already been
    # walked knows better, and asking the store costs a dict lookup.
    fill_pairs_from_cache(controllers, runs, name)

    covered = {c.bot_name for c in controllers}
    for run in runs:
        if run.is_live or run.bot_name in covered or not run.controller_ids:
            continue
        controllers.extend(declared_controllers(run))
        runs_seen += 1

    # None of the above carries a `controller_name` — `controller-performance`
    # reports none on a finished run, and a run past the retention floor
    # declares its controllers with nothing at all. The config a controller was
    # deployed from often still does, so this is the "Controller type" bubble's
    # one real source on the terminated side (see `fill_classes_from_config`).
    await fill_classes_from_config(client, controllers, name)

    response = TerminatedControllersResponse(
        controllers=controllers, runs_seen=runs_seen
    )
    _terminated_cache[(name, limit)] = (
        time.monotonic() + _TERMINATED_TTL_SEC,
        response,
    )
    return response


@router.get(
    "/servers/{name}/terminated/history",
    response_model=RunHistoryResponse,
)
async def get_run_history(
    name: str,
    bot_name: str = Query(...),
    deployed_at: str = Query(...),
    # The run's archived database, when one survived it. Supplied by the caller
    # rather than looked up because the caller already holds it — every run row
    # carries ``archive_db_path`` — and the alternative is a second upstream
    # listing on a route whose whole point is to be cheap after the first open.
    # It is only ever reached through the same server-access check as the
    # archived routes that already take a ``db_path`` this way.
    db_path: Optional[str] = Query(None),
    user: WebUser = Depends(require_server_access),
):
    """One finished run's sampled PnL curve, per controller.

    Awaits the single-flight on a cold cache, exactly as ``/archived/performance``
    does — a run is walked once however many readers ask for it at once, and
    every read after that is a file open.

    A run older than the snapshot table's retention floor has no rows at all,
    and falls back to its archived database when one survived it — a weaker
    series (per run rather than per controller, and with no unrealized
    component), labelled as such the whole way to the notice under the chart.

    A run with neither answers ``source: "none"`` with a reason rather than a
    404. The snapshot table has a retention floor, that floor is a
    property of the deployment rather than of this code, and "we have no record
    of this run" is a true statement about a run that really happened — which is
    a better thing to draw than a fabricated single step.
    """
    cm = get_config_manager()
    client = await cm.get_client(name)

    # The run's own deployment names its controllers, which is what makes the
    # walk per controller — and per controller is a correctness requirement, not
    # a tuning choice: upstream buckets by time only, so a request spanning
    # several controllers keeps one row per bucket and silently drops the rest.
    run = await _find_run(client, bot_name, deployed_at)
    if run is None:
        return RunHistoryResponse(
            source="none",
            detail=f"No run recorded for {bot_name} at {deployed_at}",
        )

    try:
        history = await fetch_run_history(
            client,
            name,
            bot_name=bot_name,
            deployed_at=run.created_at or deployed_at,
            stopped_at=run.stopped_at,
            controller_ids=run.controller_ids,
            db_path=db_path,
        )
    except RunHistoryUnavailable as e:
        if e.missing:
            return RunHistoryResponse(source="none", detail=e.detail)
        logger.warning(
            "Run history for %s on '%s' failed: %s", bot_name, name, e.detail
        )
        return RunHistoryResponse(source="none", detail=e.detail)

    return RunHistoryResponse(
        controllers=history.controllers,
        identities=history.identities,
        interval=history.interval,
        source=history.source,
        points=history.points,
        cached=history.cached,
    )


async def _find_run(client, bot_name: str, deployed_at: str) -> BotRunInfo | None:
    """The run this request is about, by name and deploy time.

    Both are needed because a bot name is reused across runs, and asked of the
    server rather than taken from the query so the controller ids come from the
    deployment itself rather than from whatever the caller passed.
    """
    try:
        raw = await client.bot_orchestration.get_bot_runs(bot_name=bot_name, limit=50)
    except Exception:
        logger.debug("Could not resolve run %s for its history", bot_name)
        return None
    runs = [_parse_bot_run(r) for r in _extract_runs_list(raw)]
    exact = [r for r in runs if r.created_at == deployed_at]
    if exact:
        return exact[0]
    # A caller that rounded the timestamp, or a server that reformatted it: fall
    # back to the newest run of that name rather than refusing to draw anything.
    named = sorted(runs, key=lambda r: r.created_at or "", reverse=True)
    return named[0] if named else None
