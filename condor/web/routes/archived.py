from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from condor.fetchers.executors import normalize_executor_side
from condor.web.auth import require_server_access
from condor.web.models import (
    ArchivedBotPerformance,
    ArchivedBotSummary,
    NormalizedExecutor,
    PaginatedExecutors,
    PnlPoint,
    WebUser,
)
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["archived"])

# Module-level LRU cache for archived data (immutable per db_path, so no TTL —
# but bounded, since each entry holds up to ~5000 PnL points plus the full
# executor list and a long-lived process would otherwise grow without ceiling).
# Key: (server_name, db_path) -> cached result. Executors live only inside the
# performance entry; the executors route pages out of it.
_PERFORMANCE_CACHE_MAX = 32
_performance_cache: OrderedDict[tuple[str, str], ArchivedBotPerformance] = OrderedDict()

# In-flight fetches keyed like the cache, so concurrent cold-cache requests for
# the same archived bot share one backend walk (same idiom as
# condor.pool_data._single_flight).
_performance_inflight: dict[tuple[str, str], "asyncio.Task[ArchivedBotPerformance]"] = (
    {}
)


def _cache_get(cache_key: tuple[str, str]) -> ArchivedBotPerformance | None:
    """Return the cached entry, marking it most-recently-used."""
    perf = _performance_cache.get(cache_key)
    if perf is not None:
        _performance_cache.move_to_end(cache_key)
    return perf


def _cache_put(cache_key: tuple[str, str], perf: ArchivedBotPerformance) -> None:
    """Store an entry, evicting the least-recently-used past the cap."""
    _performance_cache[cache_key] = perf
    _performance_cache.move_to_end(cache_key)
    while len(_performance_cache) > _PERFORMANCE_CACHE_MAX:
        _performance_cache.popitem(last=False)


def _extract_bot_name(db_path: str) -> str:
    name = os.path.basename(db_path)
    if name.endswith(".sqlite"):
        name = name[:-7]
    elif name.endswith(".db"):
        name = name[:-3]
    return name


async def _get_bot_summary(client: Any, db_path: str) -> ArchivedBotSummary | None:
    """Fetch summary for a single archived bot database."""
    try:
        summary = await client.archived_bots.get_database_summary(db_path)
        if not summary or not isinstance(summary, dict):
            return None

        return ArchivedBotSummary(
            bot_name=summary.get("bot_name") or _extract_bot_name(db_path),
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


def _parse_json_field(val: Any) -> dict:
    """Parse a field that may be a JSON string or already a dict."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip().startswith("{"):
        import json

        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


def _to_epoch_seconds(ts: Any) -> float:
    """Convert various timestamp formats to epoch seconds."""
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return ts / 1000 if ts > 1e12 else float(ts)
    if hasattr(ts, "timestamp"):
        return ts.timestamp()
    return 0.0


def _normalize_executors(raw: list[dict]) -> list[NormalizedExecutor]:
    """Map raw executor dicts from hummingbot DB to normalized format."""
    result = []
    for ex in raw:
        if not isinstance(ex, dict):
            continue

        custom_info = _parse_json_field(ex.get("custom_info", {}))
        config = _parse_json_field(ex.get("config", {}))

        # Resolve side from multiple sources
        side_raw = ex.get("side") or custom_info.get("side") or config.get("side", "")

        # Resolve connector
        connector = str(
            ex.get("connector", "")
            or ex.get("connector_name", "")
            or config.get("connector_name", "")
        )

        # Resolve trading pair
        trading_pair = str(ex.get("trading_pair", "") or config.get("trading_pair", ""))

        # Resolve prices
        entry_price = float(
            ex.get("entry_price", 0)
            or custom_info.get("current_position_average_price", 0)
            or 0
        )
        close_price = float(
            ex.get("close_price", 0)
            or ex.get("current_price", 0)
            or custom_info.get("close_price", 0)
            or 0
        )

        # Resolve PnL and fees
        pnl = float(ex.get("net_pnl_quote", 0) or ex.get("pnl", 0) or 0)
        fees = float(ex.get("cum_fees_quote", 0) or 0)
        volume = float(ex.get("filled_amount_quote", 0) or ex.get("volume", 0) or 0)
        net_pnl_pct = float(ex.get("net_pnl_pct", 0) or 0)

        result.append(
            NormalizedExecutor(
                id=str(ex.get("id", "") or ex.get("executor_id", "")),
                type=str(
                    ex.get("type", "")
                    or ex.get("executor_type", "")
                    or config.get("type", "position")
                ),
                connector=connector,
                trading_pair=trading_pair,
                side=normalize_executor_side(side_raw),
                status=str(ex.get("status", "") or "closed"),
                close_type=str(ex.get("close_type", "") or ""),
                pnl=pnl,
                volume=volume,
                timestamp=_to_epoch_seconds(ex.get("timestamp")),
                close_timestamp=_to_epoch_seconds(ex.get("close_timestamp")),
                entry_price=entry_price,
                current_price=close_price,
                cum_fees_quote=fees,
                net_pnl_pct=net_pnl_pct,
                controller_id=str(ex.get("controller_id", "")),
                custom_info=custom_info,
                config=config,
            )
        )
    return result


def _derive_primary_pair(
    executors: list[NormalizedExecutor],
    exchanges: list[str],
    trading_pairs: list[str],
) -> tuple[str, str]:
    """Find the most common connector + trading pair from executors."""
    from collections import Counter

    pair_counts: Counter[tuple[str, str]] = Counter()
    for ex in executors:
        if ex.connector and ex.trading_pair:
            pair_counts[(ex.connector, ex.trading_pair)] += 1

    if pair_counts:
        (connector, pair), _ = pair_counts.most_common(1)[0]
        return connector, pair

    # Fallback to summary data
    connector = exchanges[0] if exchanges else ""
    pair = trading_pairs[0] if trading_pairs else ""
    return connector, pair


async def _fetch_and_cache_performance(
    client: Any, name: str, db_path: str
) -> ArchivedBotPerformance:
    """Return cached performance data, coalescing concurrent cold-cache fetches.

    The fetch runs as a detached task and awaiters ``shield`` it, so one caller
    navigating away and cancelling their request cannot also cancel the fetch
    the remaining callers are waiting on.
    """
    cache_key = (name, db_path)

    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    task = _performance_inflight.get(cache_key)
    if task is None or task.done():
        task = asyncio.ensure_future(_fetch_performance(client, name, db_path))
        _performance_inflight[cache_key] = task

        def _clear(finished: "asyncio.Task", _key: tuple[str, str] = cache_key) -> None:
            if _performance_inflight.get(_key) is finished:
                _performance_inflight.pop(_key, None)

        task.add_done_callback(_clear)
    return await asyncio.shield(task)


async def _fetch_performance(
    client: Any, name: str, db_path: str
) -> ArchivedBotPerformance:
    """Fetch full performance data from the backend and cache it."""
    cache_key = (name, db_path)

    # Fetch summary
    try:
        summary = await client.archived_bots.get_database_summary(db_path)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch summary: {e}")

    if not summary or not isinstance(summary, dict):
        raise HTTPException(status_code=404, detail="Database not found")

    bot_name = summary.get("bot_name") or _extract_bot_name(db_path)
    trading_pairs = summary.get("trading_pairs", [])
    exchanges = summary.get("exchanges", [])

    # Fetch all trades (paginated)
    all_trades: list[dict] = []
    offset = 0
    limit = 500
    while True:
        try:
            resp = await client.archived_bots.get_database_trades(
                db_path, limit=limit, offset=offset
            )
        except Exception:
            break
        if not resp:
            break
        trades = resp.get("trades", [])
        if not trades:
            break
        all_trades.extend(trades)
        if len(trades) < limit:
            break
        offset += limit

    # Fetch executors
    raw_executors: list[dict] = []
    try:
        exec_resp = await client.archived_bots.get_database_executors(db_path)
        if exec_resp and isinstance(exec_resp, dict):
            raw_executors = exec_resp.get("executors", [])
        elif exec_resp and isinstance(exec_resp, list):
            raw_executors = exec_resp
    except Exception:
        pass

    # Normalize executors to consistent field names
    executors = _normalize_executors(raw_executors)

    # Derive primary connector and trading pair
    primary_connector, primary_trading_pair = _derive_primary_pair(
        executors, exchanges, trading_pairs
    )

    # Calculate PnL from trades
    from condor.archived_pnl import calculate_pnl_from_trades

    pnl_data = calculate_pnl_from_trades(all_trades)

    # Count buy/sell
    buy_count = sum(1 for t in all_trades if t.get("trade_type", "").upper() == "BUY")
    sell_count = len(all_trades) - buy_count

    # Convert cumulative_pnl to PnlPoint list with epoch seconds
    raw_cumulative = pnl_data.get("cumulative_pnl", [])
    cumulative_pnl: list[PnlPoint] = []
    for point in raw_cumulative:
        ts = point.get("timestamp")
        if ts is None:
            continue
        if hasattr(ts, "timestamp"):
            epoch = ts.timestamp()
        elif isinstance(ts, (int, float)):
            epoch = ts / 1000 if ts > 1e12 else ts
        else:
            continue
        cumulative_pnl.append(PnlPoint(timestamp=epoch, pnl=point.get("pnl", 0)))

    # Downsample if >5000 points
    if len(cumulative_pnl) > 5000:
        step = len(cumulative_pnl) // 5000
        cumulative_pnl = cumulative_pnl[::step]
        if cumulative_pnl[-1] != raw_cumulative[-1]:
            last = raw_cumulative[-1]
            ts = last.get("timestamp")
            if hasattr(ts, "timestamp"):
                epoch = ts.timestamp()
            elif isinstance(ts, (int, float)):
                epoch = ts / 1000 if ts > 1e12 else ts
            else:
                epoch = 0
            cumulative_pnl.append(PnlPoint(timestamp=epoch, pnl=last.get("pnl", 0)))

    result = ArchivedBotPerformance(
        bot_name=bot_name,
        db_path=db_path,
        total_pnl=pnl_data.get("total_pnl", 0),
        total_fees=pnl_data.get("total_fees", 0),
        total_volume=pnl_data.get("total_volume", 0),
        trade_count=len(all_trades),
        buy_count=buy_count,
        sell_count=sell_count,
        pnl_by_pair=pnl_data.get("pnl_by_pair", {}),
        cumulative_pnl=cumulative_pnl,
        trading_pairs=trading_pairs,
        exchanges=exchanges,
        executors=executors,
        primary_connector=primary_connector,
        primary_trading_pair=primary_trading_pair,
        executor_count=len(executors),
    )

    # Cache full result (executors included — the executors route pages out of it)
    _cache_put(cache_key, result)
    return result


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

    perf = await _fetch_and_cache_performance(client, name, db_path)

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

    cache_key = (name, db_path)

    # Page out of the cached performance entry; on a miss, trigger the full
    # (single-flight) fetch.
    perf = _cache_get(cache_key)
    if perf is None:
        client = await cm.get_client(name)
        perf = await _fetch_and_cache_performance(client, name, db_path)

    executors = perf.executors
    page = executors[offset : offset + limit]

    return PaginatedExecutors(
        executors=page,
        total=len(executors),
        offset=offset,
        limit=limit,
    )
