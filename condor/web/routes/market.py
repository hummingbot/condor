from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from condor import dex_candles
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

# Simple TTL cache for candle data
_candle_cache: dict[tuple, tuple[float, list]] = {}  # key -> (timestamp, data)
_CANDLE_CACHE_TTL = 30.0  # seconds
_CANDLE_CACHE_MAX = 50  # hard cap on entries (keys rotate every minute per chart)


def _candle_cache_put(key: tuple, value: list, now: float) -> None:
    """Insert into the candle cache, evicting expired entries and capping size."""
    expired = [
        k for k, (ts, _) in _candle_cache.items() if now - ts >= _CANDLE_CACHE_TTL
    ]
    for k in expired:
        _candle_cache.pop(k, None)
    _candle_cache[key] = (now, value)
    while len(_candle_cache) > _CANDLE_CACHE_MAX:
        # dicts preserve insertion order: drop the oldest entry first
        _candle_cache.pop(next(iter(_candle_cache)))


# In-flight coalescing, keyed exactly like _candle_cache. The TTL cache only
# helps requests that arrive *after* one has finished; a dashboard rendering a
# grid of executor panels for the same pair and window misses a cold cache in
# all of them at once, and each miss used to fire its own upstream call — N
# duplicate GeckoTerminal requests against the process-wide rate budget, or N
# duplicate historical-candle calls to the API server. Same idiom as
# `_snapshot_inflight` in condor/fetchers/bot_performance.py: one task per key,
# popped by a done-callback so a failure is retried rather than remembered.
_candle_inflight: dict[tuple, tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}


from condor.fetchers.market_data import fetch_historical_candles
from condor.web.auth import get_current_user, require_server_access
from condor.web.models import (
    CandleData,
    MarketPriceResponse,
    OrderBookLevel,
    OrderBookResponse,
    RatesResponse,
    TickerItem,
    TickersResponse,
    TradingRuleItem,
    TradingRulesResponse,
    WebUser,
)
from condor.web.routes._errors import upstream_error

router = APIRouter(tags=["market"])


# A token or pool address: an EVM 0x-address or a base58 Solana pubkey. Guards the
# values that reach GeckoTerminal as URL path segments, and tells a real address
# apart from a plain ticker like "BTC".
_ADDRESS_RE = dex_candles.ADDRESS_RE
_POOL_ADDRESS_RE = _ADDRESS_RE


async def _fetch_dex_candles(
    connector: str,
    pool_address: str | None,
    trading_pair: str,
    interval: str,
    start_time: float | None,
    end_time: float | None,
) -> list[CandleData]:
    """GeckoTerminal candles for a DEX/LP/XRPL pair, as the response model."""
    rows = await dex_candles.fetch_dex_candles(
        connector, pool_address, trading_pair, interval, start_time, end_time
    )
    return [CandleData(**row) for row in rows]


@router.get("/servers/{name}/market/connectors")
async def get_connectors(name: str, user: WebUser = Depends(require_server_access)):

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(
            name, ServerDataType.CANDLE_CONNECTORS
        )
    except Exception as e:
        logger.exception("Failed to fetch candle connectors from '%s'", name)
        raise upstream_error("Failed to fetch connectors", e)
    return result


@router.get("/servers/{name}/market/connected-exchanges")
async def get_connected_exchanges(
    name: str, user: WebUser = Depends(require_server_access)
):
    """Get connectors that have credentials configured (accounts connected)."""

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(
            name, ServerDataType.CONNECTORS
        )
    except Exception as e:
        logger.exception("Failed to fetch connected exchanges from '%s'", name)
        raise upstream_error("Failed to fetch connectors", e)
    return result or []


@router.get("/servers/{name}/market/venues")
async def get_venues(name: str, user: WebUser = Depends(require_server_access)):
    """Venues the trade panel can offer, each with its independent traits.

    ``{"venues": [{"name", "hummingbot_market_data", "clmm_lp"}, ...]}``. The panel
    maps traits to UI decisions itself — which tabs and strategies to render is a
    product decision, not a server fact; only the facts they rest on come from here.

    Answers ``{"venues": []}`` rather than a 502 when the server cannot be described
    at all, so the panel renders its empty state. The fetcher runs ``strict=True``,
    so that failure is never cached; a gateway-only failure degrades inside the
    fetcher and still reports the credentialed venues.
    """

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(
            name, ServerDataType.VENUES
        )
    except Exception as e:
        logger.warning("Venues unavailable for %s: %s", name, e)
        return {"venues": []}
    return {"venues": result or []}


_NO_POOL = {
    "pool_address": None,
    "dex_id": None,
    "lp_provider": None,
    "lp_supported": False,
    "current_price": None,
    "base_symbol": None,
    "quote_symbol": None,
}


@router.get("/servers/{name}/market/dex-pool")
async def get_dex_pool(
    name: str,
    connector: str = Query(
        ..., description="Gateway network, e.g. solana-mainnet-beta"
    ),
    trading_pair: str = Query(...),
    user: WebUser = Depends(require_server_access),
):
    """The pool a DEX pair trades in, and whether an LP position can be opened in it.

    Makes explicit the choice the chart already makes implicitly: candles for a DEX
    pair come from whichever pool ``resolve_pool_info`` picks, and the LP panel has
    to name that same pool. ``lp_provider`` is the ``dex/trading_type`` string an
    ``lp_executor`` requires, and is ``None`` with ``lp_supported: false`` whenever
    the deepest pool is not on one of the five CLMM venues — a router pool, a plain
    AMM, or Uniswap v4. That is a 200, not an error: the panel's answer to it is to
    ask for a pool address by hand, which is a state to render rather than a failure
    to handle.
    """

    from condor.pool_data import lp_provider_for_dex, resolve_pool_info

    try:
        info = await resolve_pool_info(connector, trading_pair)
    except Exception as e:
        logger.warning(
            "Pool resolution failed connector=%s pair=%s: %s",
            connector,
            trading_pair,
            e,
        )
        return dict(_NO_POOL)

    address = str((info or {}).get("address") or "")
    # A resolved address that does not look like one would be handed straight back
    # to /market/candles and the executor config, so it is refused here instead.
    if not info or not _POOL_ADDRESS_RE.match(address):
        return dict(_NO_POOL)

    dex_id = str(info.get("dex_id") or "")
    provider = lp_provider_for_dex(dex_id, connector)
    return {
        "pool_address": address,
        "dex_id": dex_id or None,
        "lp_provider": provider,
        "lp_supported": provider is not None,
        "current_price": info.get("current_price"),
        "base_symbol": info.get("base_symbol"),
        "quote_symbol": info.get("quote_symbol"),
    }


@router.get("/servers/{name}/market/prices", response_model=MarketPriceResponse)
async def get_price(
    name: str,
    connector: str = Query(...),
    trading_pair: str = Query(...),
    user: WebUser = Depends(require_server_access),
):

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(
            name,
            ServerDataType.PRICES,
            connector_name=connector,
            trading_pair=trading_pair,
        )
    except Exception as e:
        logger.exception(
            "Failed to fetch price for %s on %s of '%s'", trading_pair, connector, name
        )
        raise upstream_error("Failed to fetch price", e)

    if result is None:
        raise HTTPException(status_code=502, detail="Failed to fetch price")

    if isinstance(result, (int, float)):
        return MarketPriceResponse(
            connector=connector, trading_pair=trading_pair, mid_price=float(result)
        )
    elif isinstance(result, dict):
        return MarketPriceResponse(
            connector=connector,
            trading_pair=trading_pair,
            mid_price=float(result.get("mid_price", result.get("price", 0))),
            best_bid=float(result.get("best_bid", 0)),
            best_ask=float(result.get("best_ask", 0)),
        )
    raise HTTPException(status_code=502, detail="Unexpected response format")


@router.post("/servers/{name}/market/rates", response_model=RatesResponse)
async def get_rates(
    name: str,
    body: dict,
    user: WebUser = Depends(require_server_access),
):
    """Cross-rates resolved from Condor's cached ticker pool (replaces the rate oracle)."""

    trading_pairs = body.get("trading_pairs") or []
    if not trading_pairs:
        return RatesResponse(rates={})

    from condor.market_rates import get_rates as resolve

    try:
        rates = await resolve(name, trading_pairs, connector=body.get("connector"))
    except Exception as e:
        logger.exception("Failed to resolve rates on '%s'", name)
        raise upstream_error("Failed to fetch rates", e)

    return RatesResponse(rates=rates)


@router.get("/servers/{name}/market/trading-rules", response_model=TradingRulesResponse)
async def get_trading_rules(
    name: str,
    connector: str = Query(...),
    user: WebUser = Depends(require_server_access),
):

    from condor.fetchers._identifiers import IdentifierError, validate_identifier
    from condor.server_data_service import ServerDataType, get_server_data_service

    # Rejected here, not in the fetcher: SDS records a failed fetch as an
    # error-only cache entry, so a bad connector would still mint a key.
    try:
        validate_identifier(connector, "connector name")
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = await get_server_data_service().get_or_fetch(
            name, ServerDataType.TRADING_RULES, connector_name=connector
        )
    except Exception as e:
        logger.exception(
            "Failed to fetch trading rules for '%s' on '%s'", connector, name
        )
        raise upstream_error("Failed to fetch trading rules", e)

    if not isinstance(result, dict):
        return TradingRulesResponse(connector=connector, rules=[])

    rules = []
    for pair, rule_data in result.items():
        if isinstance(rule_data, dict):
            rules.append(
                TradingRuleItem(
                    trading_pair=pair,
                    min_order_size=float(rule_data.get("min_order_size", 0)),
                    min_notional_size=float(rule_data.get("min_notional_size", 0)),
                    min_price_increment=float(rule_data.get("min_price_increment", 0)),
                    min_base_amount_increment=float(
                        rule_data.get("min_base_amount_increment", 0)
                    ),
                )
            )
    return TradingRulesResponse(connector=connector, rules=rules)


def _join_change(server: str, connector: str, tickers: list[TickerItem]) -> None:
    """Fill in `change_pct` / `change_window_s` from the recorded price history.

    A read of an in-memory ring (`condor.ticker_history`), not of the upstream:
    the API has no change field on the CLOB path at all, so the reference comes
    from the ticker-pool snapshots Condor takes off its own poll. Every row that
    gets a number gets the window it was measured over with it.
    """
    from condor import ticker_history

    ref = ticker_history.reference(server)
    if not ref:
        return

    prices, window = ref
    connector_prices = prices.get(connector) or {}
    for t in tickers:
        was = connector_prices.get(t.trading_pair)
        if was and was > 0 and t.price > 0:
            t.change_pct = (t.price / was - 1) * 100
            t.change_window_s = window


@router.get("/servers/{name}/market/tickers", response_model=TickersResponse)
async def get_tickers(
    name: str,
    connector: str = Query(...),
    user: WebUser = Depends(require_server_access),
):
    """24h tickers for a connector, sorted by USD volume (highest first)."""

    from condor.market_rates import get_connector_tickers

    try:
        result = await get_connector_tickers(name, connector)
    except Exception as e:
        logger.exception("Failed to fetch tickers for '%s' on '%s'", connector, name)
        raise upstream_error("Failed to fetch tickers", e)

    if not isinstance(result, dict):
        return TickersResponse(connector=connector, tickers=[])

    tickers = [
        TickerItem(trading_pair=pair, **data)
        for pair, data in (result.get("tickers") or {}).items()
        if isinstance(data, dict)
    ]
    _join_change(name, connector, tickers)
    # Unpriced quotes sort last rather than mixing in at zero volume.
    tickers.sort(
        key=lambda t: (t.usd_volume is not None, t.usd_volume or 0), reverse=True
    )

    return TickersResponse(
        connector=connector, tickers=tickers, updated_at=result.get("updated_at")
    )


@router.get("/servers/{name}/market/order-book", response_model=OrderBookResponse)
async def get_order_book(
    name: str,
    connector: str = Query(...),
    trading_pair: str = Query(...),
    depth: int = Query(default=20, ge=1, le=100),
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    client = await cm.get_client(name)
    try:
        result = await client.market_data.get_order_book(
            connector_name=connector, trading_pair=trading_pair
        )
    except Exception as e:
        logger.exception(
            "Failed to fetch order book for %s on %s of '%s'",
            trading_pair,
            connector,
            name,
        )
        raise upstream_error("Failed to fetch order book", e)

    bids = []
    asks = []
    if isinstance(result, dict):
        for entry in (result.get("bids") or [])[:depth]:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                bids.append(
                    OrderBookLevel(price=float(entry[0]), amount=float(entry[1]))
                )
            elif isinstance(entry, dict):
                bids.append(
                    OrderBookLevel(
                        price=float(entry.get("price", 0)),
                        amount=float(entry.get("amount", entry.get("quantity", 0))),
                    )
                )
        for entry in (result.get("asks") or [])[:depth]:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                asks.append(
                    OrderBookLevel(price=float(entry[0]), amount=float(entry[1]))
                )
            elif isinstance(entry, dict):
                asks.append(
                    OrderBookLevel(
                        price=float(entry.get("price", 0)),
                        amount=float(entry.get("amount", entry.get("quantity", 0))),
                    )
                )

    return OrderBookResponse(
        connector=connector, trading_pair=trading_pair, bids=bids, asks=asks
    )


async def _fetch_candles_upstream(
    cm,
    name: str,
    connector: str,
    trading_pair: str,
    interval: str,
    limit: int,
    start_time: float | None,
    end_time: float | None,
    pool_address: str | None,
) -> list[CandleData]:
    """Fetch one window of candles upstream, with no caching of its own.

    Split out of the route so concurrent requests on the same cache key can
    share a single execution of it (see `_candle_inflight`). Raises exactly what
    the route used to raise inline, so every waiter on a failing fetch sees the
    same error and nothing is cached.
    """
    # Pairs with no CandlesFactory feed never reach the client below — that path
    # 502s for them. A GeckoTerminal-backed connector (a DEX network, or an
    # on-chain venue like xrpl) or an explicit pool_address routes there instead.
    if pool_address or dex_candles.uses_gecko_candles(connector):
        return await _fetch_dex_candles(
            connector, pool_address, trading_pair, interval, start_time, end_time
        )

    client = await cm.get_client(name)
    try:
        rows = await fetch_historical_candles(
            client,
            connector,
            trading_pair,
            interval,
            # No start_time means no time range to ask for: go straight to the
            # plain `limit`-sized window.
            start_time=int(start_time) if start_time is not None else None,
            end_time=int(end_time) if end_time else int(time.time()),
            limit=limit,
            fallback_on_error=True,
            # CORR-168: one malformed row costs one candle, not the whole
            # chart. The fetcher logs what it dropped at warning, so a payload
            # bug is still diagnosable without 500ing every request for the
            # pair. A real upstream failure still raises below and is wrapped
            # as `upstream_error` — the two stay distinguishable.
            strict=False,
        )
    except Exception as e:
        logger.exception(
            "Failed to fetch candles for %s on %s of '%s'",
            trading_pair,
            connector,
            name,
        )
        raise upstream_error("Failed to fetch candles", e)

    return [CandleData(**row) for row in rows]


@router.get("/servers/{name}/market/candles", response_model=list[CandleData])
async def get_candles(
    name: str,
    connector: str = Query(...),
    trading_pair: str = Query(...),
    interval: str = Query(default="1m"),
    limit: int = Query(default=1000, ge=1, le=5000),
    start_time: float | None = Query(default=None, description="Unix epoch seconds"),
    end_time: float | None = Query(default=None, description="Unix epoch seconds"),
    pool_address: str | None = Query(
        default=None,
        description="DEX pool address. Set by LP/DEX executor charts — their "
        "connector (e.g. solana-mainnet-beta) has no CandlesFactory feed, so "
        "candles come from GeckoTerminal by pool instead.",
    ),
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    if pool_address and not _POOL_ADDRESS_RE.match(pool_address):
        raise HTTPException(status_code=400, detail="Invalid pool_address")

    # Bucket start_time to 60s intervals so near-identical requests share cache
    bucketed_start = int(start_time // 60) * 60 if start_time is not None else None
    bucketed_end = int(end_time // 60) * 60 if end_time is not None else None
    cache_key = (
        name,
        connector,
        trading_pair,
        interval,
        limit,
        bucketed_start,
        bucketed_end,
        pool_address,
    )
    now = time.monotonic()
    cached = _candle_cache.get(cache_key)
    if cached and (now - cached[0]) < _CANDLE_CACHE_TTL:
        return cached[1]

    # Cache miss: share one upstream fetch with every concurrent request on this
    # key. Reuse an in-flight task only from the loop that created it — a task is
    # bound to its loop and awaiting it from another one raises.
    loop = asyncio.get_running_loop()
    inflight = _candle_inflight.get(cache_key)
    task = (
        inflight[1]
        if inflight is not None and inflight[0] is loop and not inflight[1].done()
        # A finished task lingers until its done-callback runs; joining it would
        # hand a fresh request the previous fetch's outcome (a failure included).
        else None
    )
    if task is None:
        task = asyncio.ensure_future(
            _fetch_candles_upstream(
                cm,
                name,
                connector,
                trading_pair,
                interval,
                limit,
                start_time,
                end_time,
                pool_address,
            )
        )
        _candle_inflight[cache_key] = (loop, task)
        task.add_done_callback(lambda _t, k=cache_key: _candle_inflight.pop(k, None))

    # Shielded: a browser that disconnects mid-request cancels this handler, and
    # an unshielded `await task` would cancel the shared fetch out from under
    # every other waiter on the same key.
    candles = await asyncio.shield(task)
    _candle_cache_put(cache_key, candles, now)
    return candles


@router.get("/market/token-symbol")
async def get_token_symbol(
    mint: str = Query(..., description="Token mint or contract address"),
    network: str = Query(
        default="solana",
        description="Network id or DEX connector (e.g. solana-mainnet-beta)",
    ),
    user: WebUser = Depends(get_current_user),
):
    """Resolve a token address to its ticker.

    LP/DEX executors store ``trading_pair`` as ``<base_mint>-<quote>`` because
    Gateway cannot resolve memecoins by symbol, so without this the dashboard shows
    a raw mint. Not server-scoped — it is a pure GeckoTerminal lookup, so the
    executor tables can resolve a symbol without threading a server name through
    every row. Auth is still required.
    """
    from condor.pool_data import fetch_token_symbol

    if not _ADDRESS_RE.match(mint):
        raise HTTPException(status_code=400, detail="Invalid token address")

    return {"mint": mint, "symbol": await fetch_token_symbol(mint, network)}
