from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from condor.fetchers.archived_run import (
    ArchivedRunUnavailable,
    cached_run,
    extract_bot_name,
    fetch_archived_run,
)
from condor.web.auth import require_server_access
from condor.web.models import (
    ArchivedBotPerformance,
    ArchivedBotSummary,
    PaginatedExecutors,
    WebUser,
)
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["archived"])


async def _load_run(client: Any, name: str, db_path: str) -> ArchivedBotPerformance:
    """The run's performance, or the HTTP answer for why it cannot be read."""
    try:
        return await fetch_archived_run(client, name, db_path)
    except ArchivedRunUnavailable as e:
        raise HTTPException(status_code=404 if e.missing else 502, detail=e.detail)


async def _get_bot_summary(client: Any, db_path: str) -> ArchivedBotSummary | None:
    """Fetch summary for a single archived bot database."""
    try:
        summary = await client.archived_bots.get_database_summary(db_path)
        if not summary or not isinstance(summary, dict):
            return None

        return ArchivedBotSummary(
            bot_name=summary.get("bot_name") or extract_bot_name(db_path),
            db_path=db_path,
            total_trades=int(summary.get("total_trades", 0)),
            total_orders=int(summary.get("total_orders", 0)),
            trading_pairs=summary.get("trading_pairs", []),
            exchanges=summary.get("exchanges", []),
            start_time=summary.get("start_time"),
            end_time=summary.get("end_time"),
        )
    except Exception as e:
        logger.debug("Failed to get summary for %s: %s", db_path, e)
        return None


@router.get("/servers/{name}/archived")
async def list_archived_bots(name: str, user: WebUser = Depends(require_server_access)):
    cm = get_config_manager()

    client = await cm.get_client(name)

    try:
        databases = await client.archived_bots.list_databases()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to list databases: {e}")

    if not databases or not isinstance(databases, list):
        return {"bots": []}

    # Filter healthy databases
    healthy_paths: list[str] = []
    for db in databases:
        if isinstance(db, str):
            healthy_paths.append(db)
        elif isinstance(db, dict):
            path = db.get("db_path") or db.get("path", "")
            if path:
                status = db.get("status", "healthy")
                if status != "error":
                    healthy_paths.append(path)

    # Fetch summaries in parallel
    tasks = [_get_bot_summary(client, path) for path in healthy_paths]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    bots = []
    for result in results:
        if isinstance(result, ArchivedBotSummary):
            bots.append(result)

    return {"bots": bots}


@router.get(
    "/servers/{name}/archived/performance", response_model=ArchivedBotPerformance
)
async def get_archived_performance(
    name: str,
    db_path: str = Query(..., description="Database path"),
    include_executors: bool = Query(
        False, description="Include full executor list in response"
    ),
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    client = await cm.get_client(name)

    perf = await _load_run(client, name, db_path)

    if not include_executors:
        # Return without executors for fast initial load
        return perf.model_copy(update={"executors": []})

    return perf


@router.get("/servers/{name}/archived/executors", response_model=PaginatedExecutors)
async def get_archived_executors(
    name: str,
    db_path: str = Query(..., description="Database path"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    # Page out of the cached performance entry; on a miss, trigger the full
    # (single-flight) fetch.
    perf = cached_run(name, db_path)
    if perf is None:
        client = await cm.get_client(name)
        perf = await _load_run(client, name, db_path)

    executors = perf.executors
    page = executors[offset : offset + limit]

    return PaginatedExecutors(
        executors=page,
        total=len(executors),
        offset=offset,
        limit=limit,
    )
