from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import (
    BotRunInfo,
    BotRunsResponse,
    ControllerPerformanceHistoryResponse,
    ControllerPerformanceLatestResponse,
    ControllerPerformanceSnapshot,
    WebUser,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["controller-performance"])


# ── Helpers ──


def _parse_snapshot(raw: dict) -> ControllerPerformanceSnapshot:
    """Normalize a raw performance snapshot dict into our model."""
    perf = raw.get("performance", raw)
    if not isinstance(perf, dict):
        perf = {}

    return ControllerPerformanceSnapshot(
        timestamp=str(raw.get("timestamp", "")),
        bot_name=raw.get("bot_name", ""),
        controller_id=raw.get("controller_id", ""),
        controller_name=raw.get("controller_name", ""),
        connector=raw.get("connector", raw.get("connector_name", "")),
        trading_pair=raw.get("trading_pair", ""),
        realized_pnl_quote=float(perf.get("realized_pnl_quote", 0) or 0),
        unrealized_pnl_quote=float(perf.get("unrealized_pnl_quote", 0) or 0),
        global_pnl_quote=float(perf.get("global_pnl_quote", 0) or 0),
        global_pnl_pct=float(perf.get("global_pnl_pct", 0) or 0),
        volume_traded=float(perf.get("volume_traded", 0) or 0),
        close_type_counts=perf.get("close_type_counts", {}),
        positions_summary=perf.get("positions_summary", []),
        custom_info=perf.get("custom_info", raw.get("custom_info", {})),
    )


def _parse_bot_run(raw: dict, perf_by_bot: dict[str, dict] | None = None) -> BotRunInfo:
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

    return BotRunInfo(
        bot_name=bot_name,
        bot_run_id=raw.get("id"),
        account_name=raw.get("account_name", ""),
        strategy_type=raw.get("strategy_type", ""),
        strategy_name=raw.get("strategy_name", ""),
        run_status=raw.get("run_status", raw.get("status", "")),
        deployment_status=raw.get("deployment_status", ""),
        created_at=str(raw["deployed_at"]) if raw.get("deployed_at") else None,
        stopped_at=str(raw["stopped_at"]) if raw.get("stopped_at") else None,
        realized_pnl_quote=realized,
        unrealized_pnl_quote=unrealized,
        global_pnl_quote=realized + unrealized,
        volume_traded=volume,
        num_controllers=num_controllers,
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
    user: WebUser = Depends(get_current_user),
):
    """Get bot runs with optional filtering."""
    import asyncio

    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

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
            result = await client.bot_orchestration.get_latest_controller_performance()
            snapshots = _extract_snapshots(result)
            agg: dict[str, dict] = {}
            for snap in snapshots:
                bn = snap.get("bot_name", "")
                if not bn:
                    continue
                perf = snap.get("performance", snap)
                if not isinstance(perf, dict):
                    perf = {}
                if bn not in agg:
                    agg[bn] = {"realized_pnl_quote": 0.0, "unrealized_pnl_quote": 0.0, "volume_traded": 0.0, "num_controllers": 0}
                agg[bn]["realized_pnl_quote"] += float(perf.get("realized_pnl_quote", 0) or 0)
                agg[bn]["unrealized_pnl_quote"] += float(perf.get("unrealized_pnl_quote", 0) or 0)
                agg[bn]["volume_traded"] += float(perf.get("volume_traded", 0) or 0)
                agg[bn]["num_controllers"] += 1
            return agg
        except Exception:
            logger.debug("Could not fetch controller performance for bot runs enrichment")
            return {}

    try:
        result, perf_by_bot = await asyncio.gather(_fetch_runs(), _fetch_perf())
    except Exception as e:
        logger.warning("Failed to fetch bot runs from '%s': %s", name, e)
        raise HTTPException(status_code=502, detail=str(e))

    runs_list = _extract_runs_list(result)

    return BotRunsResponse(
        runs=[_parse_bot_run(r, perf_by_bot) for r in runs_list],
        total=len(runs_list),
    )


@router.delete(
    "/servers/{name}/bot-runs/{bot_run_id}",
)
async def delete_bot_run(
    name: str,
    bot_run_id: int,
    user: WebUser = Depends(get_current_user),
):
    """Delete an archived bot run by its numeric ID."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    try:
        result = await client.bot_orchestration.delete_bot_run(bot_run_id)
    except Exception as e:
        logger.warning("Failed to delete bot run %d from '%s': %s", bot_run_id, name, e)
        raise HTTPException(status_code=502, detail=str(e))

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
    user: WebUser = Depends(get_current_user),
):
    """Get the most recent performance snapshot for each bot/controller."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    try:
        result = await client.bot_orchestration.get_latest_controller_performance(
            bot_name=bot_name,
        )
    except Exception as e:
        logger.warning("Failed to fetch latest controller performance from '%s': %s", name, e)
        return ControllerPerformanceLatestResponse(
            server_online=False,
            error_hint=f"Connection error: {e}",
        )

    snapshots = _extract_snapshots(result)

    return ControllerPerformanceLatestResponse(
        snapshots=[_parse_snapshot(s) for s in snapshots],
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
    limit: Optional[int] = Query(None, ge=1, le=5000),
    cursor: Optional[str] = Query(None),
    user: WebUser = Depends(get_current_user),
):
    """Get historical controller performance with pagination and interval sampling."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

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
        logger.warning("Failed to fetch controller performance history from '%s': %s", name, e)
        return ControllerPerformanceHistoryResponse(
            server_online=False,
            error_hint=f"Connection error: {e}",
        )

    snapshots = _extract_snapshots(result)
    next_cursor = None
    if isinstance(result, dict):
        next_cursor = result.get("next_cursor") or result.get("cursor")

    return ControllerPerformanceHistoryResponse(
        snapshots=[_parse_snapshot(s) for s in snapshots],
        next_cursor=next_cursor,
        interval=interval,
    )


def _extract_snapshots(result) -> list[dict]:
    """Normalize performance API response into a list of snapshot dicts."""
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
