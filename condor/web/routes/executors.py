from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

logger = logging.getLogger(__name__)

# Safety cap to avoid runaway pagination loops
MAX_EXECUTORS_FETCH = 5000
EXECUTORS_PAGE_SIZE = 500


async def fetch_all_executors(client, max_items: int = MAX_EXECUTORS_FETCH, **filters) -> list[dict]:
    """Fetch all executors via cursor-based pagination.

    The underlying hummingbot-api-client defaults to limit=50, so without
    pagination we'd only ever see the 50 most recent executors across the
    whole server. This walks the cursor until exhausted or a safety cap.
    """
    all_items: list[dict] = []
    cursor: str | None = None
    while True:
        remaining = max_items - len(all_items)
        if remaining <= 0:
            break
        page_size = min(EXECUTORS_PAGE_SIZE, remaining)
        kwargs = {**filters, "limit": page_size}
        if cursor:
            kwargs["cursor"] = cursor
        result = await client.executors.search_executors(**kwargs)
        page = _extract_executors_list(result)
        all_items.extend(page)

        # Detect next cursor from common shapes; stop if none or page short.
        next_cursor = None
        if isinstance(result, dict):
            next_cursor = result.get("next_cursor") or result.get("cursor")
            pagination = result.get("pagination")
            if not next_cursor and isinstance(pagination, dict):
                next_cursor = pagination.get("next_cursor") or pagination.get("cursor")
        # Continue while the backend advertises a next cursor, even if the page
        # is short (some backends silently cap page size below what we requested).
        if not next_cursor:
            if len(page) < page_size:
                break
            # No cursor but a full page: nothing more to fetch safely.
            break
        if len(all_items) >= max_items:
            break
        cursor = next_cursor
    return all_items

from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import CreateExecutorRequest, ExecutorInfo, WebUser
from handlers.executors._shared import get_executor_pnl, get_executor_volume, get_executor_type, get_executor_fees

router = APIRouter(tags=["executors"])


def _build_executor_info(ex: dict) -> ExecutorInfo | None:
    """Convert a raw executor dict to an ExecutorInfo model.

    Handles both REST API format (id, config.type, config.connector_name)
    and WS format (executor_id, executor_type, connector_name at top level).
    """
    if not isinstance(ex, dict):
        return None
    config = ex.get("config", ex)
    return ExecutorInfo(
        id=str(ex.get("id") or ex.get("executor_id") or ""),
        type=get_executor_type(ex),
        connector=config.get("connector_name") or ex.get("connector_name") or ex.get("connector") or "",
        trading_pair=config.get("trading_pair") or ex.get("trading_pair") or "",
        side=str(config.get("side") or ex.get("side") or ""),
        status=(ex.get("status") or "").lower(),
        close_type=str(ex.get("close_type") or "").lower(),
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
    controller_id: str = Query(default="", description="Filter by controller id"),
    limit: int = Query(default=0, ge=0, le=MAX_EXECUTORS_FETCH, description="Max executors to return (0 = default SDS cache)"),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    # For filtered queries or when a custom limit is requested, go direct to API.
    # For unfiltered default requests, use the SDS cache.
    if executor_type or trading_pair or status or controller_id or limit:
        client = await cm.get_client(name)
        api_kwargs = {}
        if executor_type:
            api_kwargs["executor_types"] = [executor_type]
        if trading_pair:
            api_kwargs["trading_pairs"] = [trading_pair]
        if status:
            api_kwargs["status"] = status
        if controller_id:
            api_kwargs["controller_ids"] = [controller_id]
        try:
            executors_list = await fetch_all_executors(
                client,
                max_items=limit or MAX_EXECUTORS_FETCH,
                **api_kwargs,
            )
            result = executors_list
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


@router.get("/servers/{name}/executors/page")
async def list_executors_page(
    name: str,
    cursor: str = Query(default="", description="Pagination cursor from previous page"),
    limit: int = Query(default=50, ge=1, le=500, description="Page size"),
    executor_type: str = Query(default=""),
    trading_pair: str = Query(default=""),
    status: str = Query(default=""),
    controller_id: str = Query(default=""),
    user: WebUser = Depends(get_current_user),
):
    """Fetch a single page of executors with a next_cursor for progressive loading.

    Designed for the frontend to stream executors in chunks (e.g. 50 at a time)
    and render them as they arrive instead of waiting for the full dataset.
    """
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    kwargs: dict = {"limit": limit}
    if cursor:
        kwargs["cursor"] = cursor
    if executor_type:
        kwargs["executor_types"] = [executor_type]
    if trading_pair:
        kwargs["trading_pairs"] = [trading_pair]
    if status:
        kwargs["status"] = status
    if controller_id:
        kwargs["controller_ids"] = [controller_id]

    try:
        result = await client.executors.search_executors(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    page = _extract_executors_list(result)
    next_cursor = None
    if isinstance(result, dict):
        next_cursor = result.get("next_cursor") or result.get("cursor")
        pagination = result.get("pagination")
        if not next_cursor and isinstance(pagination, dict):
            next_cursor = pagination.get("next_cursor") or pagination.get("cursor")
    # If the page came back short, treat as end-of-stream regardless of cursor.
    if len(page) < limit:
        next_cursor = None

    items: list[ExecutorInfo] = []
    for ex in page:
        info = _build_executor_info(ex)
        if info:
            items.append(info)
    return {"executors": items, "next_cursor": next_cursor or None}


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


@router.get("/servers/{name}/executors/positions")
async def get_positions_held(
    name: str,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        result = await client.executors.get_positions_summary()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Normalize: extract positions list from various shapes
    if isinstance(result, dict):
        positions = result.get("positions", [])
        if not isinstance(positions, list):
            positions = [positions] if positions else []
    elif isinstance(result, list):
        positions = result
    else:
        positions = []

    return {"positions": positions, "summary": result if isinstance(result, dict) else {}}


@router.delete("/servers/{name}/executors/positions/{connector}/{pair}")
async def clear_position_held(
    name: str,
    connector: str,
    pair: str,
    controller_id: str = Query(default=""),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        kwargs: dict = {}
        if controller_id:
            kwargs["controller_id"] = controller_id
        result = await client.executors.clear_position_held(
            connector_name=connector,
            trading_pair=pair,
            account_name="master_account",
            **kwargs,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"status": "ok", "result": result}
