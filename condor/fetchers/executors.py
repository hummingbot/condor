"""Fetch and manage executors via Hummingbot API."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# Cap on how many executors a single walk accumulates (not on iterations:
# the loop's own empty-page and cursor-progress guards end a stalled walk).
MAX_EXECUTORS_FETCH = 5000
EXECUTORS_PAGE_SIZE = 500

# Budget for one SDS poll tick. The EXECUTORS key is polled every 2s for every
# configured server, and SDS spends exactly one rate-limiter token per key per
# tick, so the poll must cost exactly one request: keep this at one page.
EXECUTORS_POLL_MAX = EXECUTORS_PAGE_SIZE


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


def normalize_executor_side(raw: Any) -> str:
    """Canonical ``BUY``/``SELL`` from any side encoding the API emits.

    The same logical side arrives in at least three shapes depending on which
    endpoint produced it: the enum's integer value (``1``/``2``), a bare word
    (``BUY``/``SELL``/``LONG``/``SHORT``), or a stringified enum
    (``TradeType.SELL``, ``PositionSide.LONG``). Every consumer funnels through
    here so no renderer has to guess -- the executor table used to carry a
    ``side === "1"`` fallback precisely because unnormalized values leaked to it.

    An unrecognized side is passed through uppercased rather than coerced, so a
    value we have never seen stays visible instead of silently rendering as a buy.
    """
    s = str(raw or "").strip().upper()
    # ``TradeType.SELL`` -> ``SELL``. Guarded on an alphabetic tail so a numeric
    # side is never split on its own decimal point (``1.0`` must not become ``0``).
    prefix, _, tail = s.rpartition(".")
    if prefix and tail.isalpha():
        s = tail
    if s in ("1", "BUY", "LONG"):
        return "BUY"
    if s in ("2", "SELL", "SHORT"):
        return "SELL"
    return s


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
    """Poll variant: the most recent page of executors, in one request.

    This is what SDS registers for ``ServerDataType.EXECUTORS`` and polls every
    2s per server, so it is deliberately bounded to ``EXECUTORS_POLL_MAX``
    instead of walking the whole history: one tick, one request, matching the
    single rate-limiter token SDS spends on it. Callers that need more than the
    newest page (history exports, filtered searches) call ``fetch_all_executors``
    directly with their own ``max_items``.
    """
    return await fetch_all_executors(client, max_items=EXECUTORS_POLL_MAX)


async def fetch_all_executors(
    client, max_items: int = MAX_EXECUTORS_FETCH, **filters
) -> list[dict]:
    """Full-history variant: walk the cursor across pages, on demand.

    Walks the cursor until exhausted or ``max_items`` is reached. Not for the
    hot poll — each call can issue up to ``max_items / EXECUTORS_PAGE_SIZE``
    sequential requests that the SDS rate limiter cannot see.
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
        if not page:
            break
        if not next_cursor or len(page) < page_size:
            break
        if next_cursor == cursor:
            # The API echoed the cursor we sent: the walk is not progressing.
            break
        cursor = next_cursor
    return all_items


def describe_executor_error(exc: BaseException) -> tuple[Optional[int], str]:
    """Split a mutation failure into ``(upstream status, user-safe message)``.

    Never show a user ``str(exc)``: ``aiohttp.ClientResponseError`` stringifies
    with the backend's own URL in it, so a trader on a shared server would read
    the internal host and port off a failed deploy. The pieces that are safe to
    surface live in the attributes — ``status`` is the code the API answered
    with and ``message`` is the API's own ``detail`` — and a transport failure,
    which has neither, collapses to a generic line.

    A ``None`` status means "no HTTP answer": the caller maps that to 502.
    """
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        message = getattr(exc, "message", None)
        if isinstance(message, str) and message.strip():
            return status, message.strip()
        return status, f"the trading API returned HTTP {status}"
    if isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError, OSError)):
        return None, "the trading API is unreachable"
    return None, type(exc).__name__


async def create_executor(
    client, config: Dict[str, Any], account_name: str = "master_account"
) -> Dict[str, Any]:
    """Create a new executor. Raises on failure.

    Deliberately does not translate a failure into a ``{"status": "error"}``
    dict: that envelope is shaped like a successful response, so callers ended
    up guessing at success by substring ("created" appears in *"could not be
    created"*) and pasting the raw exception into user-facing text. Failure is
    an exception here, as in the other fetchers; callers classify it once at
    their own boundary with :func:`describe_executor_error`.
    """
    try:
        return await client.executors.create_executor(
            executor_config=config, account_name=account_name
        )
    except Exception as e:
        logger.error("Error creating executor: %s", e, exc_info=True)
        raise


async def stop_executor(
    client, executor_id: str, keep_position: bool = False
) -> Dict[str, Any]:
    """Stop a running executor. Raises on failure.

    See :func:`create_executor` for why a failure is not returned as a dict.
    The old HTTP-code-in-the-message classification is gone with it: it matched
    ``"400"`` against any executor id that happened to contain those digits.
    """
    try:
        return await client.executors.stop_executor(
            executor_id=executor_id, keep_position=keep_position
        )
    except Exception as e:
        logger.error("Error stopping executor: %s", e, exc_info=True)
        raise


async def get_executor_detail(client, executor_id: str) -> Optional[Dict[str, Any]]:
    """Get details for a specific executor."""
    try:
        return await client.executors.get_executor(executor_id=executor_id)
    except Exception as e:
        logger.error("Error getting executor detail: %s", e, exc_info=True)
        return None
