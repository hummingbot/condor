from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import CreateExecutorRequest, ExecutorInfo, WebUser
from handlers.executors._shared import get_executor_pnl, get_executor_volume, get_executor_type, get_executor_fees

router = APIRouter(tags=["executors"])


def _build_executor_info(ex: dict) -> ExecutorInfo | None:
    """Convert a raw executor dict to an ExecutorInfo model."""
    if not isinstance(ex, dict):
        return None
    config = ex.get("config", ex)
    return ExecutorInfo(
        id=str(ex.get("id") or ""),
        type=get_executor_type(ex),
        connector=config.get("connector_name") or ex.get("connector") or "",
        trading_pair=config.get("trading_pair") or ex.get("trading_pair") or "",
        side=str(config.get("side") or ex.get("side") or ""),
        status=ex.get("status") or "",
        close_type=str(ex.get("close_type") or ""),
        pnl=get_executor_pnl(ex),
        volume=get_executor_volume(ex),
        timestamp=float(config.get("timestamp") or ex.get("timestamp") or 0),
        controller_id=str(config.get("controller_id") or ex.get("controller_id") or ""),
        cum_fees_quote=get_executor_fees(ex),
        net_pnl_pct=float(ex.get("net_pnl_pct") or 0),
        entry_price=float(config.get("entry_price") or ex.get("entry_price") or 0),
        current_price=float(ex.get("current_price") or 0),
        close_timestamp=float(ex.get("close_timestamp") or 0),
        custom_info=ex.get("custom_info") or {},
        config=ex.get("config", {}),
    )


def _extract_executors_list(result) -> list[dict]:
    """Extract executor list from various API response shapes."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("executors", "data", "results", "items"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


@router.get("/servers/{name}/executors", response_model=list[ExecutorInfo])
async def list_executors(
    name: str,
    executor_type: str = Query(default="", description="Filter by executor type"),
    trading_pair: str = Query(default="", description="Filter by trading pair"),
    status: str = Query(default="", description="Filter by status"),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    # For filtered queries, go direct to API; for unfiltered, use SDS cache
    if executor_type or trading_pair or status:
        client = await cm.get_client(name)
        api_kwargs = {}
        if executor_type:
            api_kwargs["executor_types"] = [executor_type]
        if trading_pair:
            api_kwargs["trading_pairs"] = [trading_pair]
        if status:
            api_kwargs["status"] = status
        try:
            result = await client.executors.search_executors(**api_kwargs)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    else:
        try:
            result = await get_server_data_service().get_or_fetch(name, ServerDataType.EXECUTORS)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
        if result is None:
            raise HTTPException(status_code=502, detail="Failed to fetch executors")

    executors_list = _extract_executors_list(result)

    items: list[ExecutorInfo] = []
    for ex in executors_list:
        info = _build_executor_info(ex)
        if info:
            items.append(info)
    return items


@router.post("/servers/{name}/executors")
async def create_executor_endpoint(
    name: str,
    body: CreateExecutorRequest,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    # Inject executor type into config
    config = {**body.config, "type": body.executor_type}

    from handlers.executors._shared import create_executor

    result = await create_executor(client, config, account_name=body.account_name)
    if isinstance(result, dict) and result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Failed to create executor"))
    executor_id = ""
    if isinstance(result, dict):
        executor_id = str(result.get("executor_id") or result.get("id") or "")
    return {"status": "ok", "executor_id": executor_id}


@router.post("/servers/{name}/executors/{executor_id}/stop")
async def stop_executor_endpoint(
    name: str,
    executor_id: str,
    keep_position: bool = Query(default=False),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    from handlers.executors._shared import stop_executor

    result = await stop_executor(client, executor_id, keep_position=keep_position)
    if isinstance(result, dict) and result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Failed to stop executor"))
    return {"status": "ok", "result": result}
