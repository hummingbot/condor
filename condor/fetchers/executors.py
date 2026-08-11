"""Fetch and manage executors via Hummingbot API."""

import asyncio
import logging
from functools import partial
from typing import Any, Dict, List, Optional

import aiohttp

from condor.fetchers._pagination import collect_pages

logger = logging.getLogger(__name__)

# Cap on how many executors a single walk accumulates (not on iterations:
# the walker's own empty-page and cursor-progress guards end a stalled walk).
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
            label = (
                ex_type.lower()
                .replace("_executor", "")
                .replace("executor", "")
                .strip("_")
            )
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
        "net_pnl_quote",
        "pnl_quote",
        "unrealized_pnl_quote",
        "realized_pnl_quote",
        "net_pnl",
        "pnl",
        "close_pnl",
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


def build_executor_row(ex: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical display row for one raw executor.

    Single source of truth for the raw-executor -> display-row transform, the
    same role :func:`condor.fetchers.bots.build_bots_page` plays for bots. Three
    layers render executors -- the REST route (via
    :meth:`condor.web.models.ExecutorInfo.from_raw`), the WS broadcast, and the
    agents performance rollup -- and the transform used to be written twice, once
    per package. The two copies had already drifted on ``current_price`` with
    neither a superset of the other, so the *same* executor showed a live price in
    the executors tab and ``0`` in the agent's session view. Both chains live here
    now, unioned, and nowhere else.

    Pure: no client, no caching, no UI. The keys are the agent-row shape that
    :func:`condor.fetchers.bot_performance.bot_executor_rows` also emits and the
    agents API serves; ``ExecutorInfo.from_raw`` renames them for the REST/WS
    wire. Casing of ``status`` and ``close_type`` is the callers' business -- they
    are returned as the API spelled them, only stringified.
    """
    config = ex.get("config")
    cfg = config if isinstance(config, dict) else ex
    custom_info = ex.get("custom_info")
    if not isinstance(custom_info, dict):
        custom_info = {}

    # entry_price is a display-only field; PnL comes straight from the executor's
    # reported fields via get_executor_pnl() and never depends on it.
    # Only position executors carry a real entry_price (config > top-level > custom_info).
    # Grid/DCA executors expose break_even_price instead; use it as the display "entry".
    _cfg_entry = float(cfg.get("entry_price") or 0)
    _top_entry = float(ex.get("entry_price") or 0)
    _ci_entry = float(custom_info.get("current_position_average_price") or 0)
    _be_price = float(custom_info.get("break_even_price") or 0)
    entry_price = _cfg_entry or _top_entry or _ci_entry or _be_price or 0.0

    # current_price: the union of the two chains that used to live apart. A
    # position executor marks itself in custom_info.current_price, a closed one
    # only has custom_info.close_price, and an order executor keeps its fill price
    # in held_position_orders -- each copy knew about some of those, so whichever
    # copy built the row decided whether a price appeared at all.
    _held_price = 0.0
    held_orders = custom_info.get("held_position_orders")
    if isinstance(held_orders, list) and held_orders:
        try:
            _held_price = float(held_orders[-1].get("price") or 0)
        except (TypeError, ValueError, AttributeError):
            pass
    current_price = 0.0
    for _candidate in (
        float(ex.get("current_price") or 0),
        float(custom_info.get("current_price") or 0),
        float(custom_info.get("close_price") or 0),
        _held_price,
    ):
        if _candidate > 0:
            current_price = _candidate
            break

    amount = float(cfg.get("total_amount_quote") or cfg.get("amount") or 0)
    if amount <= 0:
        amount = float(custom_info.get("total_value_quote") or 0)

    return {
        "id": str(ex.get("id") or ex.get("executor_id") or ""),
        "type": cfg.get("type") or ex.get("type") or "",
        # ``config`` describes the executor authoritatively, so its own
        # ``connector`` outranks the top-level WS-format shorthand. The web copy
        # skipped ``config.connector`` entirely and rendered a blank connector
        # for the rows that only carry it.
        "connector": cfg.get("connector_name")
        or ex.get("connector_name")
        or cfg.get("connector")
        or ex.get("connector")
        or "",
        "pair": cfg.get("trading_pair") or ex.get("trading_pair") or "",
        # custom_info first: that is where a position executor carries its side.
        "side": normalize_executor_side(
            custom_info.get("side") or cfg.get("side") or ex.get("side")
        ),
        "status": str(ex.get("status") or ""),
        "close_type": str(ex.get("close_type") or ""),
        "pnl": get_executor_pnl(ex),
        "volume": get_executor_volume(ex),
        "fees": get_executor_fees(ex),
        "entry_price": entry_price,
        "current_price": current_price,
        "amount": amount,
        "timestamp": float(cfg.get("timestamp") or ex.get("timestamp") or 0),
        "close_timestamp": float(ex.get("close_timestamp") or 0),
        "controller_id": str(cfg.get("controller_id") or ex.get("controller_id") or ""),
        "custom_info": custom_info,
        "config": config if isinstance(config, dict) else {},
    }


def executor_quote(pair: str) -> str:
    """Quote asset an executor's PnL and volume are denominated in.

    A pair with no quote segment (or none at all) falls back to ``USDT``, which
    is what the API reports for the overwhelming majority of markets and what the
    KPI strip assumed before the totals moved server-side.
    """
    quote = (pair or "").split("-")[1:2]
    return (quote[0] or "USDT").upper() if quote else "USDT"


def summarize_executors_by_quote(
    executors: List[Dict[str, Any]], since: float
) -> Dict[str, Dict[str, float]]:
    """Period totals for executors started at or after ``since``, per quote asset.

    Deliberately currency-blind: PnL and volume are quote-denominated, and two
    executors on ``BTC-USDT`` and ``ETH-BTC`` cannot be added without a rate. The
    caller owns that conversion, so this stays pure and the rate lookup happens
    once per quote instead of once per executor.

    ``since`` is compared against the executor's *start* timestamp, the same
    filter the KPI strip applied client-side before this moved server-side: an
    executor opened before the window is outside it even if it is still running.

    Returns:
        ``{quote: {"pnl": float, "volume": float, "count": float}}``
    """
    totals: Dict[str, Dict[str, float]] = {}
    for ex in executors:
        row = build_executor_row(ex)
        if row["timestamp"] < since:
            continue
        bucket = totals.setdefault(
            executor_quote(row["pair"]), {"pnl": 0.0, "volume": 0.0, "count": 0.0}
        )
        bucket["pnl"] += row["pnl"]
        bucket["volume"] += row["volume"]
        bucket["count"] += 1
    return totals


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
    return await collect_pages(
        partial(client.executors.search_executors, **filters),
        extract_executors_list,
        page_size=EXECUTORS_PAGE_SIZE,
        max_items=max_items,
    )


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
