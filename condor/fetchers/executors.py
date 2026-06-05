"""Fetch and manage executors via Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Safety cap to avoid runaway pagination loops
MAX_EXECUTORS_FETCH = 5000
EXECUTORS_PAGE_SIZE = 500


# ============================================
# EXTRACTION / PARSING HELPERS
# ============================================


def extract_executors_list(result) -> list[dict]:
    """Extract executor list from various API response shapes."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("executors", "data", "results", "items"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


def get_executor_type(executor: Dict[str, Any]) -> str:
    """Determine executor type from its data.

    Returns the executor type label (e.g. 'grid', 'position', 'order', 'dca', 'lp').
    """
    config = executor.get("config", executor)
    for source in (config, executor):
        ex_type = source.get("type", "") or source.get("executor_type", "")
        if isinstance(ex_type, str) and ex_type:
            label = ex_type.lower().replace("_executor", "").replace("executor", "").strip("_")
            if label:
                return label
    if "start_price" in config and "end_price" in config:
        return "grid"
    if "stop_loss" in config or "trailing_stop" in config:
        return "position"
    return "unknown"


def get_executor_pnl(executor: Dict[str, Any]) -> float:
    """Extract PnL from an executor response."""
    for key in (
        "net_pnl_quote", "pnl_quote", "unrealized_pnl_quote",
        "realized_pnl_quote", "net_pnl", "pnl", "close_pnl",
    ):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


def get_executor_volume(executor: Dict[str, Any]) -> float:
    """Extract filled/traded volume from an executor response."""
    for key in ("filled_amount_quote", "volume_traded", "total_volume"):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


def get_executor_fees(executor: Dict[str, Any]) -> float:
    """Extract cumulative fees from an executor response."""
    for key in ("cum_fees_quote", "fees_quote", "total_fees"):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


# ============================================
# API FETCHERS
# ============================================


async def fetch_executors(client, **_kw) -> list[dict]:
    """Fetch all executors via cursor-based pagination (used by SDS)."""
    return await fetch_all_executors(client)


async def fetch_all_executors(
    client, max_items: int = MAX_EXECUTORS_FETCH, **filters
) -> list[dict]:
    """Fetch all executors via cursor-based pagination.

    Walks the cursor until exhausted or safety cap reached.
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
        page = extract_executors_list(result)
        all_items.extend(page)

        next_cursor = None
        if isinstance(result, dict):
            next_cursor = result.get("next_cursor") or result.get("cursor")
            pagination = result.get("pagination")
            if not next_cursor and isinstance(pagination, dict):
                next_cursor = pagination.get("next_cursor") or pagination.get("cursor")
        if not next_cursor:
            if len(page) < page_size:
                break
            break
        if len(all_items) >= max_items:
            break
        cursor = next_cursor
    return all_items


async def create_executor(
    client, config: Dict[str, Any], account_name: str = "master_account"
) -> Dict[str, Any]:
    """Create a new executor."""
    try:
        return await client.executors.create_executor(
            executor_config=config, account_name=account_name
        )
    except Exception as e:
        logger.error("Error creating executor: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


async def stop_executor(
    client, executor_id: str, keep_position: bool = False
) -> Dict[str, Any]:
    """Stop a running executor."""
    try:
        return await client.executors.stop_executor(
            executor_id=executor_id, keep_position=keep_position
        )
    except Exception as e:
        logger.error("Error stopping executor: %s", e, exc_info=True)
        error_str = str(e)
        if "404" in error_str and "not found" in error_str.lower():
            return {"status": "error", "message": "Executor not found (may have already stopped or expired)"}
        elif "403" in error_str:
            return {"status": "error", "message": "Permission denied - cannot stop this executor"}
        elif "400" in error_str:
            return {"status": "error", "message": "Bad request - executor may be in invalid state"}
        return {"status": "error", "message": error_str}


async def get_executor_detail(client, executor_id: str) -> Optional[Dict[str, Any]]:
    """Get details for a specific executor."""
    try:
        return await client.executors.get_executor(executor_id=executor_id)
    except Exception as e:
        logger.error("Error getting executor detail: %s", e, exc_info=True)
        return None
