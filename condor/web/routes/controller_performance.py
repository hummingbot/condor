from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from condor.fetchers._pagination import next_cursor as _next_cursor
from condor.fetchers.bot_performance import extract_snapshots as _extract_snapshots
from condor.fetchers.bot_performance import (
    fetch_all_bot_performance,
    fetch_archived_paths,
)
from condor.fetchers.run_history import declared_controllers, terminated_controllers
from condor.web.auth import require_server_access
from condor.web.models import (
    BotRunInfo,
    BotRunsResponse,
    ControllerPerformanceHistoryResponse,
    ControllerPerformanceLatestResponse,
    ControllerPerformanceSnapshot,
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


# ── Terminated: the controllers of every run that has finished ──

#: How long a terminated listing stays warm.
#:
#: It is one cheap upstream call, and its answer changes only when a bot stops —
#: which is minutes apart at best. Long enough that clicking around the
#: Terminated tree costs nothing, short enough that a bot stopped a minute ago
#: shows up without a reload.
_TERMINATED_TTL_SEC = 60

#: ``server -> (expires_at, response)``. Bounded by the number of configured
#: servers, which is why it needs no eviction of its own.
_terminated_cache: dict[str, tuple[float, TerminatedControllersResponse]] = {}


def clear_terminated_cache() -> None:
    """Drop every warm listing. For tests, and for a config reload."""
    _terminated_cache.clear()


@router.get(
    "/servers/{name}/terminated/controllers",
    response_model=TerminatedControllersResponse,
)
async def get_terminated_controllers(
    name: str,
    limit: int = Query(200, ge=1, le=500),
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

    cached = _terminated_cache.get(name)
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
    covered = {c.bot_name for c in controllers}
    for run in runs:
        if run.is_live or run.bot_name in covered or not run.controller_ids:
            continue
        controllers.extend(declared_controllers(run))
        runs_seen += 1

    response = TerminatedControllersResponse(
        controllers=controllers, runs_seen=runs_seen
    )
    _terminated_cache[name] = (time.monotonic() + _TERMINATED_TTL_SEC, response)
    return response
