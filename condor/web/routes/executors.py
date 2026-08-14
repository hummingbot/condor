from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query

logger = logging.getLogger(__name__)


from condor.fetchers.executors import EXECUTORS_POLL_MAX, MAX_EXECUTORS_FETCH
from condor.fetchers.executors import extract_executors_list as _extract_executors_list
from condor.fetchers.executors import fetch_all_executors, summarize_executors_by_quote
from condor.web.auth import require_server_access
from condor.web.models import (
    CreateExecutorRequest,
    ExecutorInfo,
    ExecutorPeriodSummary,
    WebUser,
)
from condor.web.routes._errors import upstream_error
from config_manager import get_config_manager

router = APIRouter(tags=["executors"])


# Cursor scheme for pages served as an offset into the executor stream rather
# than by an opaque API cursor.
_SDS_OFFSET_PREFIX = "__sds_offset__"


# Windows the dashboard's KPI strip offers, in seconds, with the TTL each
# aggregate is cached for. A period total is not a live number -- the longer the
# window, the less one new executor moves it, so the month total is allowed to
# age far more than the day's. The floor matters because computing one of these
# walks the whole executor history: without it, every browser tab polling the
# strip would re-walk it.
_PERIOD_SECONDS: dict[str, int] = {"1D": 86400, "1W": 7 * 86400, "1M": 30 * 86400}
_PERIOD_TTLS: dict[str, int] = {"1D": 60, "1W": 300, "1M": 900}

# (server, period) -> (computed_at, summary). Bounded by servers x periods.
_summary_cache: dict[tuple[str, str], tuple[float, ExecutorPeriodSummary]] = {}


@router.get("/servers/{name}/executors", response_model=list[ExecutorInfo])
async def list_executors(
    name: str,
    executor_type: str = Query(default="", description="Filter by executor type"),
    trading_pair: str = Query(default="", description="Filter by trading pair"),
    status: str = Query(default="", description="Filter by status"),
    controller_id: str = Query(default="", description="Filter by controller id"),
    limit: int = Query(
        default=0,
        ge=0,
        le=MAX_EXECUTORS_FETCH,
        description="Max executors to return (0 = default SDS cache)",
    ),
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

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
            logger.exception("Failed to fetch executors for server %s", name)
            raise upstream_error("Failed to fetch executors", e)
    else:
        try:
            result = await get_server_data_service().get_or_fetch(
                name, ServerDataType.EXECUTORS
            )
        except Exception as e:
            logger.exception("Failed to fetch executors for server %s", name)
            raise upstream_error("Failed to fetch executors", e)
        if result is None:
            raise HTTPException(status_code=502, detail="Failed to fetch executors")

    executors_list = _extract_executors_list(result)

    items: list[ExecutorInfo] = []
    for ex in executors_list:
        info = ExecutorInfo.from_raw(ex)
        if info:
            items.append(info)
    return items


def _offset_page(rows: list[dict], offset: int, limit: int) -> dict:
    """Build a page response for the offset-cursor scheme over ``rows``."""
    items: list[ExecutorInfo] = []
    for ex in rows[offset : offset + limit]:
        info = ExecutorInfo.from_raw(ex)
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
    user: WebUser = Depends(require_server_access),
):
    """Fetch a single page of executors with a next_cursor for progressive loading.

    Designed for the frontend to stream executors in chunks (e.g. 50 at a time)
    and render them as they arrive instead of waiting for the full dataset.

    First page with no filters: served from SDS cache if available (instant).
    Scrolling past the cached prefix keeps the same offset cursor and is served
    from the API instead, so the stream continues past the poll's page budget.
    """
    cm = get_config_manager()

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
                logger.exception("Failed to page executors for server %s", name)
                raise upstream_error("Failed to fetch executors", e)
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
        logger.exception("Failed to page executors for server %s", name)
        raise upstream_error("Failed to fetch executors", e)

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
        info = ExecutorInfo.from_raw(ex)
        if info:
            items.append(info)
    return {"executors": items, "next_cursor": next_cursor or None}


async def _usd_summary(
    server: str, period: str, by_quote: dict[str, dict[str, float]]
) -> ExecutorPeriodSummary:
    """Fold per-quote totals into one USD-denominated summary.

    One rate lookup per quote asset, not per executor, and served from the cached
    ticker pool. A quote with no path to USD is added at face value and flips
    ``converted`` — the same fallback the strip's client-side ``convert()`` made,
    but reported instead of silent.
    """
    from condor.market_rates import get_rates

    rates: dict[str, float | None] = {}
    if by_quote:
        try:
            rates = await get_rates(server, [f"{q}-USDT" for q in by_quote])
        except Exception as e:
            logger.warning(
                "Rates unavailable while summarizing executors for %s: %s", server, e
            )

    pnl = 0.0
    volume = 0.0
    count = 0
    converted = True
    for quote, totals in by_quote.items():
        rate = rates.get(f"{quote}-USDT")
        if not rate or rate <= 0:
            converted = False
            rate = 1.0
        pnl += totals["pnl"] * rate
        volume += totals["volume"] * rate
        count += int(totals["count"])

    return ExecutorPeriodSummary(
        period=period, pnl=pnl, volume=volume, count=count, converted=converted
    )


@router.get("/servers/{name}/executors/summary", response_model=ExecutorPeriodSummary)
async def executors_summary(
    name: str,
    period: str = Query(default="1D", description="Window: 1D, 1W or 1M"),
    user: WebUser = Depends(require_server_access),
):
    """PnL, volume and executor count over a period, across the whole history.

    The dashboard's KPI strip used to compute this in the browser by summing the
    executor list it already had. That list is the SDS cache, which the poll
    bounds to a single page (``EXECUTORS_POLL_MAX``, PERF-117), so on a busy
    server the 1W and 1M tiles silently reported the newest page instead of the
    period — a wrong number with nothing to distinguish it from a right one.

    A period total belongs here, over the full history: this walks it with
    ``fetch_all_executors``, on demand and cached per period, leaving the 2s poll
    at its one request per tick.
    """
    cm = get_config_manager()

    period = period.upper()
    window = _PERIOD_SECONDS.get(period)
    if window is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown period '{period}': expected one of "
            + ", ".join(_PERIOD_SECONDS),
        )

    now = time.time()
    cached = _summary_cache.get((name, period))
    if cached is not None and now - cached[0] < _PERIOD_TTLS[period]:
        return cached[1]

    client = await cm.get_client(name)
    try:
        executors = await fetch_all_executors(client)
    except Exception as e:
        logger.exception("Failed to summarize executors for server %s", name)
        raise upstream_error("Failed to fetch executors", e)

    summary = await _usd_summary(
        name, period, summarize_executors_by_quote(executors, now - window)
    )
    _summary_cache[(name, period)] = (now, summary)
    return summary


@router.post("/servers/{name}/executors")
async def create_executor_endpoint(
    name: str,
    body: CreateExecutorRequest,
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    client = await cm.get_client(name)

    # Inject executor type into config
    config = {**body.config, "type": body.executor_type}

    from condor.fetchers.executors import create_executor

    try:
        result = await create_executor(client, config, account_name=body.account_name)
    except Exception as e:
        raise upstream_error("Failed to create executor", e)
    executor_id = ""
    if isinstance(result, dict):
        executor_id = str(result.get("executor_id") or result.get("id") or "")
    return {"status": "ok", "executor_id": executor_id}


@router.post("/servers/{name}/executors/{executor_id}/stop")
async def stop_executor_endpoint(
    name: str,
    executor_id: str,
    keep_position: bool = Query(default=False),
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    client = await cm.get_client(name)

    from condor.fetchers.executors import stop_executor

    try:
        result = await stop_executor(client, executor_id, keep_position=keep_position)
    except Exception as e:
        raise upstream_error("Failed to stop executor", e)
    return {"status": "ok", "result": result}


@router.get("/servers/{name}/executors/positions")
async def get_positions_held(
    name: str,
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    client = await cm.get_client(name)
    try:
        result = await client.executors.get_positions_summary()
    except Exception as e:
        logger.exception("Failed to fetch held positions for server %s", name)
        raise upstream_error("Failed to fetch positions", e)

    # Normalize: extract positions list from various shapes
    if isinstance(result, dict):
        positions = result.get("positions", [])
        if not isinstance(positions, list):
            positions = [positions] if positions else []
    elif isinstance(result, list):
        positions = result
    else:
        positions = []

    return {
        "positions": positions,
        "summary": result if isinstance(result, dict) else {},
    }


@router.delete("/servers/{name}/executors/positions/{connector}/{pair}")
async def clear_position_held(
    name: str,
    connector: str,
    pair: str,
    controller_id: str = Query(default=""),
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

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
        logger.exception("Failed to clear held position on server %s", name)
        raise upstream_error("Failed to clear position", e)
    return {"status": "ok", "result": result}
