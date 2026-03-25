from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

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


@router.get("/servers/{name}/portfolio/history", response_model=PortfolioHistoryResponse)
async def get_portfolio_history(
    name: str,
    range: str = Query("1D", pattern="^(1D|1W|1M|3M)$"),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access to this server")

    try:
        client = await cm.get_client(name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Cannot connect to server: {e}")

    range_seconds, interval = RANGE_CONFIG[range]
    start_time = int(time.time()) - range_seconds

    try:
        history = await client.portfolio.get_history(
            start_time=start_time, interval=interval, limit=500
        )
    except Exception as e:
        logger.warning("Failed to get portfolio history: %s", e)
        return PortfolioHistoryResponse(server=name, points=[], interval=interval)

    logger.debug("Portfolio history response shape: %s", type(history))
    if isinstance(history, dict):
        logger.debug("Portfolio history keys: %s", list(history.keys()))

    points: list[PortfolioHistoryPoint] = []

    # Parse defensively — the response may be a list of snapshots or a dict with data key
    snapshots = []
    if isinstance(history, list):
        snapshots = history
    elif isinstance(history, dict):
        # Try common keys
        for key in ("data", "snapshots", "history", "points", "results"):
            if key in history and isinstance(history[key], list):
                snapshots = history[key]
                break
        if not snapshots:
            # Maybe the dict itself maps timestamps → portfolio states
            # like {account: {connector: [balances]}} per timestamp
            # Try treating top-level as timestamp-keyed
            for ts_key, snapshot_data in history.items():
                try:
                    ts = _parse_timestamp(ts_key)
                except (ValueError, TypeError):
                    continue
                total = _sum_snapshot_value(snapshot_data)
                if total > 0:
                    points.append(PortfolioHistoryPoint(timestamp=ts, total_usd=total))

    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        ts = snapshot.get("timestamp", snapshot.get("time", snapshot.get("t", 0)))
        total = snapshot.get("total_value", snapshot.get("total_usd", 0))
        if total == 0:
            # Sum token values from nested structure
            total = _sum_snapshot_value(snapshot)
        if ts:
            points.append(PortfolioHistoryPoint(timestamp=_parse_timestamp(ts), total_usd=float(total)))

    points.sort(key=lambda p: p.timestamp)
    return PortfolioHistoryResponse(server=name, points=points, interval=interval)


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


def _sum_snapshot_value(data: object) -> float:
    """Sum USD values from a portfolio snapshot (nested account/connector/balances)."""
    total = 0.0
    if not isinstance(data, dict):
        return total
    for val in data.values():
        if isinstance(val, dict):
            for inner in val.values():
                if isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict):
                            total += float(item.get("value", item.get("usd_value", 0)))
                elif isinstance(inner, (int, float)):
                    total += float(inner)
        elif isinstance(val, (int, float)):
            total += float(val)
    return total
