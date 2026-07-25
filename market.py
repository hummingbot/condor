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

# Persistent dict handed to handlers.dex.pool_data.fetch_ohlcv so its own 300s
# GeckoTerminal cache applies on top of the 30s route cache above — keeps us well
# under GeckoTerminal's free-tier rate limit when multiple pool charts are open.
_gecko_ohlcv_user_data: dict = {}


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
    TradingRuleItem,
    TradingRulesResponse,
    WebUser,
)

router = APIRouter(tags=["market"])


_MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# DEX pool addresses across GeckoTerminal networks: base58 (Solana) or 0x-hex
# (EVM). Used to sanitize the pool_address query param before it reaches a URL.
_POOL_ADDR_RE = re.compile(r"^[A-Za-z0-9]{16,90}$")


async def _fetch_pool_candles_raw(
    pool_address: str,
    network: str,
    interval: str,
    limit: int = 100,
    before_timestamp: int | None = None,
) -> list[CandleData]:
    """OHLCV rows for one pool from GeckoTerminal (reuses handlers.dex fetch+cache).

    ``currency="token"`` prices the base token in the quote token (e.g. SOL), matching
    the executor's own entry/range price scale drawn on the same chart. ``limit`` and
    ``before_timestamp`` carry the chart's requested window so an archived executor
    charts against the candles it actually traded in, not the latest ones. Returns []
    on any miss/error (never raises) so DEX pairs don't fall to the CEX 502 path.
    """
    from handlers.dex.pool_data import fetch_ohlcv

    try:
        ohlcv_list, err = await fetch_ohlcv(
            pool_address,
            network,
            timeframe=interval,
            currency="token",
            user_data=_gecko_ohlcv_user_data,
            limit=limit,
            before_timestamp=before_timestamp,
        )
    except Exception as e:
        logger.warning(
            "GeckoTerminal OHLCV failed pool=%s net=%s interval=%s: %s",
            pool_address,
            network,
            interval,
            e,
        )
        return []
    if err or not ohlcv_list:
        return []

    candles: list[CandleData] = []
    for c in ohlcv_list:
        # Rows are [timestamp, open, high, low, close, volume(_usd), (datetime)].
        if not isinstance(c, (list, tuple)) or len(c) < 6:
            continue
        try:
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
        except (TypeError, ValueError):
            continue
    return candles


# Base-token mint → its top GeckoTerminal pool (24h-volume-sorted). Pools are stable,
# so cache for an hour. Lets an executor chart fall back to the token's live main pool
# when its own pool_address is stale/absent (e.g. a closed slot, or a multi-executor
# group where the chart picked a dead pool).
_token_pool_cache: dict[tuple[str, str], tuple[float, str]] = {}
_TOKEN_POOL_TTL = 3600.0


async def _resolve_token_top_pool(mint: str, gnet: str, quote: str = "SOL") -> str:
    key = (gnet, mint)
    now = time.time()
    cached = _token_pool_cache.get(key)
    if cached and (now - cached[0]) < _TOKEN_POOL_TTL:
        return cached[1]

    addr = ""
    try:
        import aiohttp

        url = f"https://api.geckoterminal.com/api/v2/networks/{gnet}/tokens/{mint}/pools?page=1"
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url, headers={"Accept": "application/json;version=20230302"}
            ) as r:
                r.raise_for_status()
                data = await r.json()
        pools = data.get("data") or []
        # Prefer a pool quoted in the executor's quote token (e.g. SOL) so the price
        # scale matches; else the highest-volume pool (list is volume-sorted).
        chosen = None
        for p in pools:
            parts = str((p.get("attributes") or {}).get("name") or "").upper().replace(" ", "").split("/")
            if quote and quote.upper() in parts:
                chosen = p
                break
        chosen = chosen or (pools[0] if pools else None)
        if chosen:
            attrs = chosen.get("attributes") or {}
            addr = str(attrs.get("address") or str(chosen.get("id") or "").split("_")[-1] or "")
        # Only cache on a successful API response — addr "" here means the token
        # genuinely has no pool, which is worth caching. A transient error (below)
        # must not poison the cache for an hour, so it returns without caching.
        _token_pool_cache[key] = (now, addr)
        return addr
    except Exception as e:
        logger.info("top-pool resolve failed mint=%s net=%s: %s", mint, gnet, e)
        return ""


async def _get_pool_candles(
    connector: str,
    pool_address: str | None,
    trading_pair: str,
    interval: str,
    cache_key: tuple,
    now: float,
    limit: int = 100,
    before_timestamp: int | None = None,
) -> list[CandleData]:
    """Candles for a DEX/LP pair from GeckoTerminal.

    Tries the executor's own ``pool_address`` first (exact pool); if that yields
    nothing — a stale/closed slot, no pool_address, or a group whose first executor
    sits on a dead pool — falls back to the base token's top live pool resolved from
    the mint in ``trading_pair``. So a live token always charts even when the passed
    pool is wrong. ``connector`` is the network id (e.g. solana-mainnet-beta).
    ``limit``/``before_timestamp`` carry the chart's requested window (see
    :func:`_fetch_pool_candles_raw`).
    """
    from handlers.dex.pool_data import get_gecko_network

    gnet = get_gecko_network(connector)
    candles: list[CandleData] = []
    if pool_address:
        candles = await _fetch_pool_candles_raw(
            pool_address, connector, interval, limit, before_timestamp
        )

    if not candles:
        dash = trading_pair.rfind("-")
        base = trading_pair[:dash] if dash > 0 else trading_pair
        quote = trading_pair[dash + 1 :] if dash > 0 else "SOL"
        if _MINT_RE.match(base):
            top = await _resolve_token_top_pool(base, gnet, quote)
            if top and top != pool_address:
                candles = await _fetch_pool_candles_raw(
                    top, connector, interval, limit, before_timestamp
                )

    _candle_cache_put(cache_key, candles, now)
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


@router.post("/servers/{name}/rate-oracle/rates")
async def get_rate_oracle_rates(
    name: str,
    body: dict,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    trading_pairs = body.get("trading_pairs", [])
    if not trading_pairs:
        return {"rates": {}}

    client = await cm.get_client(name)
    try:
        result = await client.rate_oracle.get_rates(trading_pairs=trading_pairs)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return result


@router.get("/servers/{name}/market/trading-rules", response_model=TradingRulesResponse)
async def get_trading_rules(
    name: str,
    connector: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

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
        description="DEX pool address. When set, candles are fetched from "
        "GeckoTerminal (by pool) instead of the CEX candle feed — used for LP/DEX "
        "executors whose connector (e.g. solana-mainnet-beta) has no CandlesFactory feed.",
    ),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    # pool_address is interpolated into GeckoTerminal URLs — restrict to plain
    # address characters (base58 for Solana, 0x-hex for EVM networks).
    if pool_address and not _POOL_ADDR_RE.match(pool_address):
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

    # DEX/LP pools have no CEX candle feed — route to GeckoTerminal. Trigger on a
    # DEX network connector (e.g. "solana-mainnet-beta") OR an explicit pool_address,
    # so these pairs never fall through to the CEX path (which 502s). _get_pool_candles
    # uses the pool_address when it has data, else resolves the token's top pool.
    from handlers.dex.pool_data import NETWORK_TO_GECKO

    if pool_address or connector in NETWORK_TO_GECKO:
        # Pass the chart's window through so archived executors chart against the
        # candles they actually traded in. before_timestamp = end of window (candles
        # walk back from there); None = latest. GeckoTerminal caps limit at 1000.
        return await _get_pool_candles(
            connector,
            pool_address,
            trading_pair,
            interval,
            cache_key,
            now,
            limit=limit,
            before_timestamp=bucketed_end,
        )

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


# Token symbol resolution — LP/DEX executors store `trading_pair` as `<base_mint>-SOL`
# (Gateway can't resolve memecoins by symbol), so the dashboard shows the raw mint.
# Resolve mint → ticker via GeckoTerminal (same source as candles). Symbols are
# stable, so cache for a day. Empty string is cached too (so an unknown/illiquid
# mint doesn't re-hit GeckoTerminal every render); the UI falls back to the mint.
_token_symbol_cache: dict[tuple[str, str], tuple[float, str]] = {}
_TOKEN_SYMBOL_TTL = 24 * 3600.0


@router.get("/market/token-symbol")
async def get_token_symbol(
    mint: str = Query(..., description="Base token mint address"),
    network: str = Query(
        default="solana", description="Network id or connector (e.g. solana-mainnet-beta)"
    ),
    user: WebUser = Depends(get_current_user),
):
    # Server-independent: pure GeckoTerminal lookup, no server scoping needed
    # (auth still required). Lets the executor tables resolve symbols without
    # threading a server name into every row.
    from handlers.dex.pool_data import get_gecko_network

    # The mint is interpolated into the GeckoTerminal URL path — reject anything
    # that isn't a base58 pubkey (mirrors the frontend's looksLikeMint gate).
    if not _MINT_RE.match(mint):
        raise HTTPException(status_code=400, detail="Invalid mint address")

    gnet = get_gecko_network(network)
    key = (gnet, mint)
    now = time.time()
    cached = _token_symbol_cache.get(key)
    if cached and (now - cached[0]) < _TOKEN_SYMBOL_TTL:
        return {"mint": mint, "symbol": cached[1]}

    symbol = ""
    try:
        import aiohttp

        url = f"https://api.geckoterminal.com/api/v2/networks/{gnet}/tokens/{mint}"
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url, headers={"Accept": "application/json;version=20230302"}
            ) as r:
                r.raise_for_status()
                data = await r.json()
        symbol = str(
            (((data or {}).get("data") or {}).get("attributes") or {}).get("symbol") or ""
        )
    except Exception as e:
        # Don't cache a transient failure — a single blip must not blank this pair's
        # ticker for 24h. Only successful responses (below) are cached, empty included
        # (a genuinely unknown mint is worth remembering).
        logger.info("token-symbol resolve failed for mint=%s network=%s: %s", mint, gnet, e)
        return {"mint": mint, "symbol": ""}

    _token_symbol_cache[key] = (now, symbol)
    return {"mint": mint, "symbol": symbol}
