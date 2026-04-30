from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import (
    BalanceItem,
    ConnectorBalance,
    PortfolioHistoryPoint,
    PortfolioHistoryResponse,
    PortfolioResponse,
    WebUser,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio"])

# ---------------------------------------------------------------------------
# Portfolio history cache
# ---------------------------------------------------------------------------

_RANGE_TTLS: dict[str, int] = {
    "1D": 120,
    "1W": 600,
    "1M": 3600,
    "3M": 7200,
}

_WARM_INTERVAL = 120  # Re-warm every 2 min (matches shortest TTL)
_IDLE_TIMEOUT = 600  # Stop refresh loop after 10 min with no requests


@dataclass
class _CacheEntry:
    data: Any = None
    fetched_at: float = 0.0
    ttl: int = 120


# (server, range, False) -> CacheEntry  (breakdown doesn't affect fetched data)
_history_cache: dict[tuple[str, str, bool], _CacheEntry] = {}
_refresh_tasks: dict[str, asyncio.Task] = {}
_last_request_time: dict[str, float] = {}  # server -> last request ts


@router.get("/servers/{name}/portfolio", response_model=PortfolioResponse)
async def get_portfolio(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access to this server")

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        state = await get_server_data_service().get_or_fetch(name, ServerDataType.PORTFOLIO)
    except Exception as e:
        logger.warning("Portfolio fetch exception for %s: %s", name, e)
        raise HTTPException(status_code=502, detail=f"Failed to get portfolio: {e}")

    if state is None:
        # Check if fetch is registered
        sds = get_server_data_service()
        registered = ServerDataType.PORTFOLIO in sds._fetch_registry
        logger.warning(
            "Portfolio returned None for %s (fetch registered: %s, health: %s)",
            name, registered,
            sds.get_server_health(name).status.value if name in sds._health else "unknown",
        )
        raise HTTPException(
            status_code=502,
            detail=f"Failed to get portfolio: server '{name}' returned no data"
                   + ("" if registered else " (fetch not registered — try restarting)"),
        )

    # Parse portfolio state into structured response
    # API returns: {account_name: {connector_name: [balance_dicts]}}
    connectors: list[ConnectorBalance] = []
    total_usd = 0.0

    if isinstance(state, dict):
        for account_name, account_data in state.items():
            if not isinstance(account_data, dict):
                continue

            for connector_name, connector_balances in account_data.items():
                if not isinstance(connector_balances, list):
                    continue

                balances: list[BalanceItem] = []
                connector_total = 0.0

                for item in connector_balances:
                    if not isinstance(item, dict):
                        continue
                    token = item.get("token", item.get("asset", ""))
                    total_bal = float(item.get("units", item.get("total_balance", 0)))
                    available = float(item.get("available_units", item.get("available_balance", total_bal)))
                    usd_val = float(item.get("value", item.get("usd_value", 0)))

                    if not token:
                        continue

                    balances.append(
                        BalanceItem(token=token, total=total_bal, available=available, usd_value=usd_val)
                    )
                    connector_total += usd_val

                # Filter out zero-value tokens and sort by value descending
                balances = [b for b in balances if b.usd_value >= 0.01]
                balances.sort(key=lambda b: b.usd_value, reverse=True)

                connectors.append(
                    ConnectorBalance(connector=connector_name, balances=balances, total_usd=connector_total)
                )
                total_usd += connector_total

    return PortfolioResponse(server=name, connectors=connectors, total_usd=total_usd)


RANGE_CONFIG = {
    "1D": (86400, "5m"),
    "1W": (604800, "1h"),
    "1M": (2592000, "4h"),
    "3M": (7776000, "1d"),
}


async def _fetch_history(server: str, range_key: str) -> Any:
    """Fetch portfolio history from the Hummingbot API (no caching)."""
    cm = get_config_manager()
    client = await cm.get_client(server)
    range_seconds, interval = RANGE_CONFIG[range_key]
    start_time = int(time.time()) - range_seconds
    return await client.portfolio.get_history(
        start_time=start_time, interval=interval, limit=500
    )


async def _get_cached_history(server: str, range_key: str, breakdown: bool = False) -> Any:
    """Return cached history if fresh, otherwise fetch, cache, and return.

    The raw data is the same regardless of *breakdown* (parsing happens in the
    endpoint), so we cache without the breakdown flag to avoid duplicate fetches.
    """
    key = (server, range_key, False)
    entry = _history_cache.get(key)
    now = time.time()

    if entry and entry.data is not None and (now - entry.fetched_at) < entry.ttl:
        return entry.data

    # Fetch fresh data
    data = await _fetch_history(server, range_key)
    _history_cache[key] = _CacheEntry(
        data=data, fetched_at=time.time(), ttl=_RANGE_TTLS[range_key]
    )
    return data


async def warm_portfolio_history(server: str) -> None:
    """Pre-fetch all 4 history ranges into cache, then start a refresh loop."""
    logger.info("Warming portfolio history cache for %s", server)
    _last_request_time[server] = time.time()

    # Initial warm: fetch all ranges concurrently (no breakdown)
    tasks = []
    for range_key in RANGE_CONFIG:
        tasks.append(_get_cached_history(server, range_key, False))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for range_key, result in zip(RANGE_CONFIG, results):
        if isinstance(result, Exception):
            logger.warning("Warm cache failed for %s/%s: %s", server, range_key, result)

    # Start background refresh loop if not already running
    if server not in _refresh_tasks or _refresh_tasks[server].done():
        _refresh_tasks[server] = asyncio.create_task(_refresh_loop(server))


async def _refresh_loop(server: str) -> None:
    """Periodically re-warm cache; exits after idle timeout."""
    logger.info("Portfolio history refresh loop started for %s", server)
    try:
        while True:
            await asyncio.sleep(_WARM_INTERVAL)
            last = _last_request_time.get(server, 0)
            if time.time() - last > _IDLE_TIMEOUT:
                logger.info(
                    "Portfolio history refresh loop idle for %s, stopping", server
                )
                return

            for range_key in RANGE_CONFIG:
                try:
                    data = await _fetch_history(server, range_key)
                    key = (server, range_key, False)
                    _history_cache[key] = _CacheEntry(
                        data=data, fetched_at=time.time(), ttl=_RANGE_TTLS[range_key]
                    )
                except Exception as e:
                    logger.debug("Refresh failed for %s/%s: %s", server, range_key, e)
    except asyncio.CancelledError:
        logger.info("Portfolio history refresh loop cancelled for %s", server)


def stop_history_refresh(server: str) -> None:
    """Cancel the background refresh loop for a server."""
    task = _refresh_tasks.pop(server, None)
    if task and not task.done():
        task.cancel()
        logger.info("Stopped portfolio history refresh for %s", server)


@router.get("/servers/{name}/portfolio/history", response_model=PortfolioHistoryResponse)
async def get_portfolio_history(
    name: str,
    range: str = Query("1D", pattern="^(1D|1W|1M|3M)$"),
    breakdown: bool = Query(False),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access to this server")

    _last_request_time[name] = time.time()

    try:
        history = await _get_cached_history(name, range, breakdown)
    except Exception as e:
        logger.warning("Failed to get portfolio history: %s", e)
        _, interval = RANGE_CONFIG[range]
        return PortfolioHistoryResponse(server=name, points=[], interval=interval)

    _, interval = RANGE_CONFIG[range]

    logger.debug("Portfolio history response shape: %s", type(history))
    if isinstance(history, dict):
        logger.debug("Portfolio history keys: %s", list(history.keys()))

    points: list[PortfolioHistoryPoint] = []

    # Parse defensively — the response may be a list of snapshots or a dict with data key
    snapshots = []
    found_key = False
    if isinstance(history, list):
        snapshots = history
    elif isinstance(history, dict):
        # Try common keys
        for key in ("data", "snapshots", "history", "points", "results"):
            if key in history and isinstance(history[key], list):
                snapshots = history[key]
                found_key = True
                break
        if not snapshots and not found_key:
            # Maybe the dict itself maps timestamps → portfolio states
            dict_prev_totals: dict[str, float] = {}
            dict_entries: list[tuple[float, object]] = []
            for ts_key, snapshot_data in history.items():
                try:
                    ts = _parse_timestamp(ts_key)
                except (ValueError, TypeError):
                    continue
                dict_entries.append((ts, snapshot_data))
            dict_entries.sort(key=lambda x: x[0])
            for ts, snapshot_data in dict_entries:
                cur = _extract_connector_totals(snapshot_data)
                if cur and dict_prev_totals:
                    for k, v in dict_prev_totals.items():
                        if k not in cur:
                            cur[k] = v
                if cur:
                    dict_prev_totals = cur
                total = sum(cur.values())
                if total > 0:
                    points.append(PortfolioHistoryPoint(timestamp=ts, total_usd=total))

    # Forward-fill: track per-connector totals so missing exchanges
    # carry forward their last known value instead of dropping to 0.
    prev_connector_totals: dict[str, float] = {}

    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        ts = snapshot.get("timestamp", snapshot.get("time", snapshot.get("t", 0)))
        total = snapshot.get("total_value", snapshot.get("total_usd", 0))
        if total == 0:
            # Sum token values from nested structure
            # API returns {timestamp, state: {account: {connector: [balances]}}}
            state = snapshot.get("state", snapshot)
            cur_totals = _extract_connector_totals(state)

            if cur_totals and prev_connector_totals:
                # Forward-fill: for connectors seen before but missing now, use previous value
                for key, prev_val in prev_connector_totals.items():
                    if key not in cur_totals:
                        cur_totals[key] = prev_val

            if cur_totals:
                prev_connector_totals = cur_totals
            total = sum(cur_totals.values())
        if ts:
            points.append(PortfolioHistoryPoint(timestamp=_parse_timestamp(ts), total_usd=float(total)))

    points.sort(key=lambda p: p.timestamp)

    top_tokens: list[str] = []
    if breakdown and points:
        # Extract per-token values for each point
        raw_snapshots = []
        if isinstance(history, list):
            raw_snapshots = history
        elif isinstance(history, dict):
            for key in ("data", "snapshots", "history", "points", "results"):
                if key in history and isinstance(history[key], list):
                    raw_snapshots = history[key]
                    break

        # Build token values per timestamp (with forward-fill for missing exchanges)
        ts_token_map: dict[float, dict[str, float]] = {}
        prev_token_vals: dict[str, float] = {}
        # Collect and sort by timestamp to ensure correct ffill order
        raw_entries: list[tuple[float, dict[str, float]]] = []
        for snapshot in raw_snapshots:
            if not isinstance(snapshot, dict):
                continue
            ts = snapshot.get("timestamp", snapshot.get("time", snapshot.get("t", 0)))
            if not ts:
                continue
            parsed_ts = _parse_timestamp(ts)
            state = snapshot.get("state", snapshot)
            token_vals = _extract_token_values(state)
            if token_vals:
                raw_entries.append((parsed_ts, token_vals))

        # Also handle dict-keyed timestamps
        if not raw_snapshots and isinstance(history, dict):
            for ts_key, snapshot_data in history.items():
                try:
                    ts = _parse_timestamp(ts_key)
                except (ValueError, TypeError):
                    continue
                token_vals = _extract_token_values(snapshot_data)
                if token_vals:
                    raw_entries.append((ts, token_vals))

        raw_entries.sort(key=lambda x: x[0])
        for parsed_ts, token_vals in raw_entries:
            # Forward-fill: tokens present before but missing now keep previous value
            if prev_token_vals:
                for tk, tv in prev_token_vals.items():
                    if tk not in token_vals:
                        token_vals[tk] = tv
            prev_token_vals = token_vals
            ts_token_map[parsed_ts] = token_vals

        if ts_token_map:
            # Determine top 8 tokens by aggregate value
            agg: dict[str, float] = {}
            for tv in ts_token_map.values():
                for token, val in tv.items():
                    agg[token] = agg.get(token, 0) + val
            sorted_tokens = sorted(agg, key=lambda t: agg[t], reverse=True)
            top_tokens = sorted_tokens[:8]
            top_set = set(top_tokens)

            # Populate token breakdown on each point, collapsing rest into "Other"
            for point in points:
                tv = ts_token_map.get(point.timestamp, {})
                if not tv:
                    continue
                tokens_out: dict[str, float] = {}
                other = 0.0
                for token, val in tv.items():
                    if token in top_set:
                        tokens_out[token] = val
                    else:
                        other += val
                if other > 0:
                    tokens_out["Other"] = other
                point.tokens = tokens_out

            if "Other" in {t for p in points for t in p.tokens} and "Other" not in top_tokens:
                top_tokens.append("Other")

    return PortfolioHistoryResponse(server=name, points=points, interval=interval, top_tokens=top_tokens)


def _parse_timestamp(val: object) -> float:
    """Parse a timestamp that may be numeric or ISO 8601 string."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
    return 0.0


def _extract_token_values(data: object) -> dict[str, float]:
    """Extract per-token USD values from a portfolio snapshot."""
    tokens: dict[str, float] = {}
    if not isinstance(data, dict):
        return tokens
    for val in data.values():
        if isinstance(val, dict):
            for inner in val.values():
                if isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict):
                            token = item.get("token", item.get("asset", ""))
                            usd = float(item.get("value", item.get("usd_value", 0)))
                            if token and usd > 0:
                                tokens[token] = tokens.get(token, 0) + usd
    return tokens


def _extract_connector_totals(data: object) -> dict[str, float]:
    """Extract per-connector USD totals from a portfolio snapshot."""
    totals: dict[str, float] = {}
    if not isinstance(data, dict):
        return totals
    for account, val in data.items():
        if isinstance(val, dict):
            for connector, inner in val.items():
                key = f"{account}:{connector}"
                if isinstance(inner, list):
                    s = 0.0
                    for item in inner:
                        if isinstance(item, dict):
                            s += float(item.get("value", item.get("usd_value", 0)))
                    totals[key] = s
                elif isinstance(inner, (int, float)):
                    totals[key] = float(inner)
        elif isinstance(val, (int, float)):
            totals[account] = float(val)
    return totals


def _sum_snapshot_value(data: object) -> float:
    """Sum USD values from a portfolio snapshot (nested account/connector/balances)."""
    return sum(_extract_connector_totals(data).values())
