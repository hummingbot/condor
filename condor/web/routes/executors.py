from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

logger = logging.getLogger(__name__)


from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import CreateExecutorRequest, ExecutorInfo, WebUser
from condor.fetchers.executors import (
    describe_executor_error,
    fetch_all_executors,
    extract_executors_list as _extract_executors_list,
    get_executor_pnl,
    get_executor_volume,
    get_executor_type,
    get_executor_fees,
    EXECUTORS_POLL_MAX,
    MAX_EXECUTORS_FETCH,
)

router = APIRouter(tags=["executors"])


_SIDE_MAP = {"1": "BUY", "2": "SELL"}

# Cursor scheme for pages served as an offset into the executor stream rather
# than by an opaque API cursor.
_SDS_OFFSET_PREFIX = "__sds_offset__"


def _mutation_error(action: str, exc: Exception) -> HTTPException:
    """Map an executor mutation failure to an HTTPException that leaks nothing.

    An upstream 4xx is the caller's own bad request and stays a 400; anything
    else — an upstream 5xx, a timeout, a refused connection — is the gateway
    failing, so 502. The detail carries the API's message but never the raw
    exception, whose string embeds the backend URL and port.
    """
    status, message = describe_executor_error(exc)
    code = 400 if status is not None and 400 <= status < 500 else 502
    return HTTPException(status_code=code, detail=f"{action}: {message}")


def _normalize_side(raw: str) -> str:
    return _SIDE_MAP.get(raw, raw.upper() if raw else "")


def _build_executor_info(ex: dict) -> ExecutorInfo | None:
    """Convert a raw executor dict to an ExecutorInfo model.

    Handles both REST API format (id, config.type, config.connector_name)
    and WS format (executor_id, executor_type, connector_name at top level).
    """
    if not isinstance(ex, dict):
        return None
    config = ex.get("config", ex)
    custom_info = ex.get("custom_info") or {}

    # Entry price is display-only (PnL comes from get_executor_pnl(), independent of it).
    # Only position executors carry a real entry_price (config > top-level > custom_info);
    # grid/DCA executors expose break_even_price instead, so fall back to it for display.
    _cfg_entry = float(config.get("entry_price") or 0)
    _top_entry = float(ex.get("entry_price") or 0)
    _ci_entry = float(custom_info.get("current_position_average_price") or 0)
    _be_price = float(custom_info.get("break_even_price") or 0)
    entry_price = _cfg_entry or _top_entry or _ci_entry or _be_price or 0.0

    # Current/close price: top-level > custom_info.close_price > held_position_orders fill price
    _top_cur = float(ex.get("current_price") or 0)
    _ci_close = float(custom_info.get("close_price") or 0)
    # Extract fill price from held_position_orders (order executors store fills there)
    _held_price = 0.0
    held_orders = custom_info.get("held_position_orders")
    if isinstance(held_orders, list) and held_orders:
        try:
            _held_price = float(held_orders[-1].get("price") or 0)
        except (TypeError, ValueError, AttributeError):
            pass
    current_price = _top_cur if _top_cur > 0 else (_ci_close if _ci_close > 0 else (_held_price if _held_price > 0 else 0.0))

    return ExecutorInfo(
        id=str(ex.get("id") or ex.get("executor_id") or ""),
        type=get_executor_type(ex),
        connector=config.get("connector_name") or ex.get("connector_name") or ex.get("connector") or "",
        trading_pair=config.get("trading_pair") or ex.get("trading_pair") or "",
        side=_normalize_side(str(custom_info.get("side") or config.get("side") or ex.get("side") or "")),
        status=(ex.get("status") or "").lower(),
        close_type=str(ex.get("close_type") or "").lower(),
        pnl=get_executor_pnl(ex),
        volume=get_executor_volume(ex),
        timestamp=float(config.get("timestamp") or ex.get("timestamp") or 0),
        controller_id=str(config.get("controller_id") or ex.get("controller_id") or ""),
        cum_fees_quote=get_executor_fees(ex),
        net_pnl_pct=float(ex.get("net_pnl_pct") or 0),
        entry_price=entry_price,
        current_price=current_price,
        close_timestamp=float(ex.get("close_timestamp") or 0),
        custom_info=custom_info,
        config=ex.get("config", {}),
    )



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


def _offset_page(rows: list[dict], offset: int, limit: int) -> dict:
    """Build a page response for the offset-cursor scheme over ``rows``."""
    items: list[ExecutorInfo] = []
    for ex in rows[offset : offset + limit]:
        info = _build_executor_info(ex)
        if info:
            items.append(info)
    has_more = len(rows) > offset + limit
    return {
        "executors": items,
        "next_cursor": _SDS_OFFSET_PREFIX + str(offset + limit) if has_more else None,
    }


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

    First page with no filters: served from SDS cache if available (instant).
    Scrolling past the cached prefix keeps the same offset cursor and is served
    from the API instead, so the stream continues past the poll's page budget.
    """
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    has_filters = bool(executor_type or trading_pair or status or controller_id)

    # Offset paging over the SDS cache: the unfiltered first page, and any page
    # whose cursor was handed out by a previous one.
    offset: int | None = None
    if not has_filters:
        if not cursor:
            offset = 0
        elif cursor.startswith(_SDS_OFFSET_PREFIX):
            offset = int(cursor[len(_SDS_OFFSET_PREFIX) :] or 0)

    if offset is not None:
        from condor.server_data_service import ServerDataType, get_server_data_service

        cached = get_server_data_service().get(name, ServerDataType.EXECUTORS)
        cached_rows = _extract_executors_list(cached) if cached is not None else []
        # The poll caps its walk at EXECUTORS_POLL_MAX, so a cache of exactly
        # that length is only a prefix of the history: just a short cache proves
        # there is nothing past it.
        cache_is_whole = cached is not None and len(cached_rows) < EXECUTORS_POLL_MAX
        if offset + limit < len(cached_rows) or cache_is_whole:
            return _offset_page(cached_rows, offset, limit)

        if cursor:
            # Scrolled past the cached prefix, or the cache expired mid-scroll.
            # An offset cursor has no API equivalent, so keep the scheme going:
            # walk far enough to fill this page and to tell if another follows.
            client = await cm.get_client(name)
            try:
                rows = await fetch_all_executors(client, max_items=offset + limit + 1)
            except Exception as e:
                raise HTTPException(status_code=502, detail=str(e))
            return _offset_page(rows, offset, limit)
        # Cold cache on the first page: fall through to opaque API cursors,
        # which page the rest of the scroll in one request each.

    client = await cm.get_client(name)
    kwargs: dict = {"limit": limit}
    if cursor and not cursor.startswith(_SDS_OFFSET_PREFIX):
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

    items = []
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

    from condor.fetchers.executors import create_executor

    try:
        result = await create_executor(client, config, account_name=body.account_name)
    except Exception as e:
        raise _mutation_error("Failed to create executor", e)
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

    from condor.fetchers.executors import stop_executor

    try:
        result = await stop_executor(client, executor_id, keep_position=keep_position)
    except Exception as e:
        raise _mutation_error("Failed to stop executor", e)
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
