from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import ExecutorInfo, WebUser
from handlers.executors._shared import get_executor_pnl, get_executor_volume, get_executor_type

router = APIRouter(tags=["executors"])


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

    if isinstance(result, list):
        executors_list = result
    elif isinstance(result, dict):
        executors_list = []
        for key in ("executors", "data", "results", "items"):
            if key in result and isinstance(result[key], list):
                executors_list = result[key]
                break
    else:
        executors_list = []

    items: list[ExecutorInfo] = []
    for ex in executors_list:
        if not isinstance(ex, dict):
            continue
        config = ex.get("config", ex)
        items.append(
            ExecutorInfo(
                id=str(ex.get("id") or ""),
                type=get_executor_type(ex),
                connector=config.get("connector_name") or ex.get("connector") or "",
                trading_pair=config.get("trading_pair") or ex.get("trading_pair") or "",
                side=str(config.get("side") or ex.get("side") or ""),
                status=ex.get("status") or "",
                pnl=get_executor_pnl(ex),
                volume=get_executor_volume(ex),
                timestamp=float(ex.get("timestamp") or 0),
                config=ex.get("config", {}),
            )
        )
    return items
