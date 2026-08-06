from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query

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


from condor.web.auth import get_current_user
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

router = APIRouter(tags=["market"])


# A token or pool address: an EVM 0x-address or a base58 Solana pubkey. Guards the
# values that reach GeckoTerminal as URL path segments, and tells a real address
# apart from a plain ticker like "BTC".
_ADDRESS_RE = re.compile(r"^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")
_POOL_ADDRESS_RE = _ADDRESS_RE

# GeckoTerminal answers are shared across users, so one process-wide dict backs the
# pool_data OHLCV cache for every dashboard viewer.
_dex_ohlcv_cache: dict = {}


def _is_dex_connector(connector: str) -> bool:
    """Whether this connector is a DEX network rather than a CEX venue."""
    from handlers.dex.pool_data import NETWORK_TO_GECKO

    return connector in NETWORK_TO_GECKO


def _split_pair(trading_pair: str) -> tuple[str, str]:
    """``<base>-<quote>`` → (base, quote). LP/DEX pairs carry a raw mint as base."""
    dash = trading_pair.rfind("-")
    if dash <= 0:
        return trading_pair, ""
    return trading_pair[:dash], trading_pair[dash + 1 :]


async def _pool_ohlcv(
    pool_address: str,
    connector: str,
    timeframe: str,
    limit: int,
    before_timestamp: int | None,
) -> list:
    """Raw OHLCV rows for one pool, or [] on any miss. Never raises."""
    from handlers.dex.pool_data import fetch_ohlcv

    try:
        rows, err = await fetch_ohlcv(
            pool_address,
            connector,
            timeframe=timeframe,
            # Prices in the quote token, matching the scale of the entry and range
            # prices the executor overlays draw on the same chart.
            currency="token",
            user_data=_dex_ohlcv_cache,
            limit=limit,
            before_timestamp=before_timestamp,
        )
    except Exception as e:
        logger.warning(
            "GeckoTerminal OHLCV failed pool=%s connector=%s tf=%s: %s",
            pool_address,
            connector,
            timeframe,
            e,
        )
        return []
    return [] if err or not rows else rows


async def _fetch_dex_candles(
    connector: str,
    pool_address: str | None,
    trading_pair: str,
    interval: str,
    start_time: float | None,
    end_time: float | None,
) -> list[CandleData]:
    """Candles for a DEX/LP pair from GeckoTerminal.

    Prefers the executor's own pool. If that yields nothing — a closed slot, or an
    executor that never recorded one — falls back to the base token's deepest pool
    *in the same quote token*; a pool on another quote would draw a plausible chart
    on the wrong price scale, so no match means no candles.

    Note the volume column is USD (GeckoTerminal reports ``volume_usd``) while the
    CEX path reports base units.
    """
    from handlers.dex.pool_data import (
        candles_needed,
        normalize_timeframe,
        timeframe_seconds,
    )

    timeframe = normalize_timeframe(interval)
    tf_seconds = timeframe_seconds(timeframe)
    # The chart asks for a window; GeckoTerminal answers with a count walking back
    # from before_timestamp. Asking for the raw `limit` would return ~16h of 1m
    # candles for a ten-minute position.
    count = candles_needed(start_time, end_time, timeframe)

    # Only pin before_timestamp for a window that has already closed. A live chart
    # would otherwise mint a new cache key on every request as its end drifts,
    # re-hitting GeckoTerminal each time; "latest candles" is both correct and
    # cacheable for it.
    before = (
        int(end_time)
        if end_time is not None and end_time < time.time() - tf_seconds
        else None
    )

    rows = []
    if pool_address:
        rows = await _pool_ohlcv(pool_address, connector, timeframe, count, before)

    if not rows:
        base, quote = _split_pair(trading_pair)
        if _ADDRESS_RE.match(base):
            from handlers.dex.pool_data import fetch_token_top_pool

            top = await fetch_token_top_pool(base, connector, quote)
            if top and top != pool_address:
                rows = await _pool_ohlcv(top, connector, timeframe, count, before)

    # Rows are [timestamp, open, high, low, close, volume_usd, (datetime)], ascending.
    candles: list[CandleData] = []
    # Keep the candle that contains `start`, not just those starting after it.
    lower = start_time - tf_seconds if start_time is not None else None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            ts = float(row[0])
            if lower is not None and ts < lower:
                continue
            if end_time is not None and ts > end_time:
                continue
            candles.append(
                CandleData(
                    timestamp=ts,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        except (TypeError, ValueError):
            continue
    return candles


@router.get("/servers/{name}/market/connectors")
async def get_connectors(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(
            name, ServerDataType.CANDLE_CONNECTORS
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


@router.get("/servers/{name}/market/connected-exchanges")
async def get_connected_exchanges(name: str, user: WebUser = Depends(get_current_user)):
    """Get connectors that have credentials configured (accounts connected)."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(
            name, ServerDataType.CONNECTORS
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result or []


@router.get("/servers/{name}/market/prices", response_model=MarketPriceResponse)
async def get_price(
    name: str,
    connector: str = Query(...),
    trading_pair: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(
            name,
            ServerDataType.PRICES,
            connector_name=connector,
            trading_pair=trading_pair,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

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
    user: WebUser = Depends(get_current_user),
):
    """Cross-rates resolved from Condor's cached ticker pool (replaces the rate oracle)."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    trading_pairs = body.get("trading_pairs") or []
    if not trading_pairs:
        return RatesResponse(rates={})

    from condor.market_rates import get_rates as resolve

    try:
        rates = await resolve(name, trading_pairs, connector=body.get("connector"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return RatesResponse(rates=rates)


@router.get("/servers/{name}/market/trading-rules", response_model=TradingRulesResponse)
async def get_trading_rules(
    name: str,
    connector: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

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
        raise HTTPException(status_code=502, detail=str(e))

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


@router.get("/servers/{name}/market/tickers", response_model=TickersResponse)
async def get_tickers(
    name: str,
    connector: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    """24h tickers for a connector, sorted by USD volume (highest first)."""
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.market_rates import get_connector_tickers

    try:
        result = await get_connector_tickers(name, connector)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not isinstance(result, dict):
        return TickersResponse(connector=connector, tickers=[])

    tickers = [
        TickerItem(trading_pair=pair, **data)
        for pair, data in (result.get("tickers") or {}).items()
        if isinstance(data, dict)
    ]
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
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        result = await client.market_data.get_order_book(
            connector_name=connector, trading_pair=trading_pair
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

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
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

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

    # DEX/LP pairs have no CEX candle feed, so they never reach the client below —
    # that path 502s for them. A DEX network connector or an explicit pool_address
    # routes to GeckoTerminal instead.
    if pool_address or _is_dex_connector(connector):
        candles = await _fetch_dex_candles(
            connector, pool_address, trading_pair, interval, start_time, end_time
        )
        _candle_cache_put(cache_key, candles, now)
        return candles

    client = await cm.get_client(name)
    result = None
    try:
        # Prefer historical candles with time range when start_time is given
        if start_time is not None:
            st = int(start_time)
            et = int(end_time) if end_time else int(time.time())
            logger.info(
                "Fetching historical candles: connector=%s pair=%s interval=%s start=%s end=%s",
                connector,
                trading_pair,
                interval,
                st,
                et,
            )
            result = await client.market_data.get_historical_candles(
                connector,
                trading_pair,
                interval,
                start_time=st,
                end_time=et,
            )
            logger.info(
                "Historical candles result: type=%s len=%s",
                type(result).__name__,
                len(result) if isinstance(result, (list, dict)) else "?",
            )
    except Exception as e:
        logger.warning(
            "get_historical_candles failed: %s — falling back to get_candles", e
        )
        result = None

    # Fallback: if historical returned nothing usable, use regular candles
    candles_raw = (
        result
        if isinstance(result, list)
        else result.get("data", []) if isinstance(result, dict) else []
    )
    if not candles_raw:
        try:
            logger.info(
                "Falling back to get_candles: connector=%s pair=%s interval=%s limit=%s",
                connector,
                trading_pair,
                interval,
                limit,
            )
            result = await client.market_data.get_candles(
                connector, trading_pair, interval, limit
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    candles_raw = (
        result
        if isinstance(result, list)
        else result.get("data", []) if isinstance(result, dict) else []
    )

    candles = []
    for c in candles_raw:
        if isinstance(c, dict):
            candles.append(
                CandleData(
                    timestamp=float(c.get("timestamp", 0)),
                    open=float(c.get("open", 0)),
                    high=float(c.get("high", 0)),
                    low=float(c.get("low", 0)),
                    close=float(c.get("close", 0)),
                    volume=float(c.get("volume", 0)),
                )
            )
        elif isinstance(c, (list, tuple)) and len(c) >= 6:
            candles.append(
                CandleData(
                    timestamp=float(c[0]),
                    open=float(c[1]),
                    high=float(c[2]),
                    low=float(c[3]),
                    close=float(c[4]),
                    volume=float(c[5]),
                )
            )
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
    from handlers.dex.pool_data import fetch_token_symbol

    if not _ADDRESS_RE.match(mint):
        raise HTTPException(status_code=400, detail="Invalid token address")

    return {"mint": mint, "symbol": await fetch_token_symbol(mint, network)}
