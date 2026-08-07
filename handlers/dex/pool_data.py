"""
Pool Data Utilities

Provides unified data fetching for DEX pools:
- OHLCV data via GeckoTerminal (works for any pool on any DEX)
- Token symbol / top-pool resolution via GeckoTerminal
- Liquidity/bin data via Gateway CLMM (for supported DEXes)
- Pool info normalization across different sources

This is the single module that talks to GeckoTerminal for pool and token market
data. Consumers (Telegram handlers, the web dashboard's market routes) call these
functions rather than building GeckoTerminal URLs themselves.
"""

import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from geckoterminal_py import GeckoTerminalAsyncClient

from config_manager import get_client

from ._shared import evict_expired, get_cached, set_cached

logger = logging.getLogger(__name__)

# Supported DEXes for liquidity data (via gateway CLMM)
LIQUIDITY_SUPPORTED_DEXES = {
    "meteora": "solana",
    "raydium": "solana",
    "orca": "solana",
    "uniswap": "ethereum",
    "pancakeswap": "bsc",
}

# GeckoTerminal network mapping
NETWORK_TO_GECKO = {
    "solana": "solana",
    "solana-mainnet-beta": "solana",
    "ethereum": "eth",
    "ethereum-mainnet": "eth",
    "arbitrum": "arbitrum",
    "arbitrum-one": "arbitrum",
    "base": "base",
    "base-mainnet": "base",
    "bsc": "bsc",
    "binance-smart-chain": "bsc",
    "polygon": "polygon_pos",
    "polygon-mainnet": "polygon_pos",
    "avalanche": "avalanche",
    "optimism": "optimism",
    # Not a Gateway network: `xrpl` is a first-class Hummingbot connector (orders,
    # balances, order book) that CandlesFactory simply has no feed for. Its AMM
    # pools are indexed by GeckoTerminal, so its charts resolve here like a DEX's.
    "xrpl": "xrpl",
}

# Networks whose venues quote *tickers* rather than token addresses. An XRPL token
# is a (currency, issuer) pair that the connector hides behind a symbol like
# ``SOLO-XRP``, so its pool can only be found by searching for that symbol —
# unlike a Solana pair, whose base already *is* the mint.
SYMBOL_PAIR_NETWORKS = {"xrpl"}

# DEX ID to GeckoTerminal DEX mapping
DEX_TO_GECKO = {
    "meteora": "meteora",
    "raydium": "raydium",
    "orca": "orca",
    "uniswap": "uniswap",
    "uniswap_v3": "uniswap_v3",
    "pancakeswap": "pancakeswap",
    "pancakeswap_v3": "pancakeswap_v3",
    "sushiswap": "sushiswap",
}

# Cache TTLs
OHLCV_CACHE_TTL = 300  # 5 minutes
BINS_CACHE_TTL = 60  # 1 minute
TOKEN_SYMBOL_TTL = 24 * 3600  # a mint's ticker does not change
TOKEN_POOL_TTL = 3600  # a token's main pool is stable over an hour

# GeckoTerminal's OHLCV endpoint only accepts these aggregates, and its client
# raises ValueError on anything else. Charts pick their own interval, so map an
# unsupported one down to the nearest supported bucket rather than returning an
# empty chart.
GECKO_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "12h", "1d")
_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "12h": 43200,
    "1d": 86400,
}
# GeckoTerminal caps a single OHLCV response at this many candles.
GECKO_OHLCV_MAX = 1000


def get_gecko_network(network: str) -> str:
    """Convert internal network name to GeckoTerminal network ID"""
    return NETWORK_TO_GECKO.get(network, network)


def uses_symbol_pairs(network: str) -> bool:
    """Whether this network's trading pairs are tickers instead of addresses."""
    return get_gecko_network(network) in SYMBOL_PAIR_NETWORKS


def timeframe_seconds(timeframe: str) -> int:
    """Length of one candle in seconds; 60 for anything unrecognized."""
    return _TIMEFRAME_SECONDS.get(timeframe, 60)


def normalize_timeframe(interval: str) -> str:
    """Snap an arbitrary chart interval onto a GeckoTerminal timeframe.

    Exact matches pass through. Anything else (``3m``, ``30m``, ``1s``…) resolves
    to the largest supported timeframe that is no coarser than requested, so the
    chart keeps at least the resolution it asked for. Unparseable input falls back
    to ``1m``.
    """
    if interval in _TIMEFRAME_SECONDS:
        return interval
    unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        wanted = int(interval[:-1]) * unit_seconds[interval[-1]]
    except (ValueError, KeyError, IndexError):
        return "1m"
    supported = sorted(_TIMEFRAME_SECONDS.items(), key=lambda kv: kv[1])
    best = supported[0][0]
    for name, seconds in supported:
        if seconds <= wanted:
            best = name
    return best


def candles_needed(start: Optional[float], end: Optional[float], timeframe: str) -> int:
    """How many candles cover ``[start, end]`` at ``timeframe``.

    The chart asks for a *window*; GeckoTerminal answers with a *count* walking
    back from ``before_timestamp``. Without this translation a ten-minute position
    charted at 1m would come back with the maximum 1000 candles — ~16h of history
    that ``fitContent()`` then squeezes the actual position into.
    """
    if start is None or end is None or end <= start:
        return GECKO_OHLCV_MAX
    span = math.ceil((end - start) / timeframe_seconds(timeframe))
    # A couple of candles of headroom so the window's edges are never clipped.
    return max(1, min(span + 2, GECKO_OHLCV_MAX))


# ── GeckoTerminal client ──
# One client (and so one httpx connection pool) for the process. Constructing a
# fresh GeckoTerminalAsyncClient per call leaks an unclosed httpx.AsyncClient
# every time, which at chart-refresh rates exhausts sockets.
_gecko_client_instance: Optional[GeckoTerminalAsyncClient] = None


def _gecko_client() -> GeckoTerminalAsyncClient:
    global _gecko_client_instance
    if _gecko_client_instance is None:
        _gecko_client_instance = GeckoTerminalAsyncClient()
    return _gecko_client_instance


# ── Small TTL caches for token lookups ──
# These answers are process-wide (not per-user) and tiny, so they live here rather
# than in a caller's user_data. Capped so an unbounded stream of distinct mints
# cannot grow them without limit.
_TOKEN_CACHE_MAX = 512
_token_symbol_cache: Dict[Tuple[str, str], Tuple[float, str]] = {}
_token_pool_cache: Dict[Tuple[str, str, str], Tuple[float, str]] = {}
_pair_pool_cache: Dict[Tuple[str, str, str], Tuple[float, Tuple[str, bool]]] = {}


def _ttl_get(cache: dict, key: tuple, ttl: float) -> Optional[Any]:
    entry = cache.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None


def _ttl_put(cache: dict, key: tuple, value: Any, ttl: float) -> None:
    now = time.time()
    for k in [k for k, (ts, _) in cache.items() if now - ts >= ttl]:
        cache.pop(k, None)
    cache[key] = (now, value)
    while len(cache) > _TOKEN_CACHE_MAX:
        cache.pop(next(iter(cache)))


async def fetch_token_symbol(mint: str, network: str) -> str:
    """Resolve a token mint/contract address to its ticker (e.g. ``Bonk``).

    Returns "" when the token is unknown or the lookup fails. An empty answer from
    a *successful* response is cached (a genuinely unlisted mint should not be
    re-queried on every render); a failed lookup is not, so one blip does not blank
    the ticker for a day.
    """
    gnet = get_gecko_network(network)
    key = (gnet, mint)
    cached = _ttl_get(_token_symbol_cache, key, TOKEN_SYMBOL_TTL)
    if cached is not None:
        return cached

    try:
        data = await _gecko_client().get_specific_token_on_network(gnet, mint)
    except Exception as e:
        logger.info("token symbol lookup failed mint=%s net=%s: %s", mint, gnet, e)
        return ""

    symbol = ""
    if isinstance(data, dict):
        symbol = str((data.get("attributes") or {}).get("symbol") or "")
    _ttl_put(_token_symbol_cache, key, symbol, TOKEN_SYMBOL_TTL)
    return symbol


def _pool_quote_symbols(name: Any) -> List[str]:
    """Token symbols in a GeckoTerminal pool name (``"BONK / SOL"`` → BONK, SOL)."""
    return [p.strip().upper() for p in str(name or "").split("/") if p.strip()]


async def fetch_token_top_pool(mint: str, network: str, quote: str) -> str:
    """Address of the token's highest-volume pool **quoted in ``quote``**.

    Used as a fallback when an executor's own ``pool_address`` yields no candles
    (a closed slot, a pool that never had one recorded). The quote match is not
    cosmetic: candles are requested with ``currency="token"``, so prices come back
    denominated in the pool's quote token. Charting a token/USDC pool underneath a
    token/SOL position would silently draw the right shape on the wrong scale, so
    a pool that does not match returns "" and the chart stays empty instead.
    """
    gnet = get_gecko_network(network)
    want = (quote or "").strip().upper()
    key = (gnet, mint, want)
    cached = _ttl_get(_token_pool_cache, key, TOKEN_POOL_TTL)
    if cached is not None:
        return cached

    try:
        pools = await _gecko_client().get_top_pools_by_network_token(gnet, mint)
    except Exception as e:
        # Includes the KeyError geckoterminal_py raises post-processing an empty
        # result set. Not cached — a transient failure must not pin "" for an hour.
        logger.info("top-pool lookup failed mint=%s net=%s: %s", mint, gnet, e)
        return ""

    address = ""
    try:
        # Rows arrive sorted by 24h volume, so the first quote match is the deepest.
        for row in pools.to_dict("records"):
            if want and want not in _pool_quote_symbols(row.get("name")):
                continue
            address = str(row.get("address") or "")
            if address:
                break
    except Exception as e:
        logger.info("top-pool parse failed mint=%s net=%s: %s", mint, gnet, e)
        return ""

    _ttl_put(_token_pool_cache, key, address, TOKEN_POOL_TTL)
    return address


async def fetch_pair_top_pool(base: str, quote: str, network: str) -> Tuple[str, bool]:
    """Deepest pool trading ``base``/``quote`` on ``network``, found by *symbol*.

    For venues that quote tickers rather than addresses (see
    ``SYMBOL_PAIR_NETWORKS``): an xrpl pair like ``SOLO-XRP`` names no token that
    GeckoTerminal can look up, so this searches pools instead and keeps only exact
    symbol matches. Ticker collisions are real on XRPL — any issuer can mint a
    ``RLUSD`` — so the highest 24h volume wins, which is also the pool the
    GeckoTerminal UI shows first.

    Returns:
        ``(pool_address, inverted)``. ``inverted`` is True when the pool is quoted
        the other way round (pair ``XRP-RLUSD`` against a ``RLUSD / XRP`` pool);
        the caller must then read the quote token's price series, not the base's.
        ``("", False)`` when nothing matches — an empty chart beats a chart drawn
        on the wrong pair.
    """
    gnet = get_gecko_network(network)
    b, q = (base or "").strip().upper(), (quote or "").strip().upper()
    if not b or not q:
        return "", False

    key = (gnet, b, q)
    cached = _ttl_get(_pair_pool_cache, key, TOKEN_POOL_TTL)
    if cached is not None:
        return cached

    try:
        # Both symbols in the query: GeckoTerminal matches them against the pool
        # name ("SOLO / XRP"), so this narrows the answer set before we filter.
        data = await _gecko_client().api_request(
            "GET", "search/pools", params={"query": f"{b} {q}", "network": gnet}
        )
    except Exception as e:
        # Not cached — a blip must not pin "no pool" for the full hour.
        logger.info("pair-pool search failed %s-%s net=%s: %s", b, q, gnet, e)
        return "", False

    best: Tuple[str, bool] = ("", False)
    best_volume = -1.0
    try:
        for row in data.get("data") or []:
            attrs = row.get("attributes") or {}
            symbols = _pool_quote_symbols(attrs.get("name"))
            if len(symbols) != 2:
                continue
            if symbols == [b, q]:
                inverted = False
            elif symbols == [q, b]:
                inverted = True
            else:
                continue  # fuzzy hit (RLUSDM for RLUSD), not this pair
            address = str(attrs.get("address") or "")
            if not address:
                continue
            volume = _get_nested_float(attrs, "volume_usd", "h24") or 0.0
            if volume > best_volume:
                best, best_volume = (address, inverted), volume
    except Exception as e:
        logger.info("pair-pool parse failed %s-%s net=%s: %s", b, q, gnet, e)
        return "", False

    _ttl_put(_pair_pool_cache, key, best, TOKEN_POOL_TTL)
    return best


def can_fetch_liquidity(dex_id: str, network: str = None) -> bool:
    """Check if liquidity/bin data can be fetched for this DEX

    Args:
        dex_id: DEX identifier (e.g., "meteora", "raydium")
        network: Optional network to verify (must be Solana for now)

    Returns:
        True if liquidity data is available via gateway CLMM
    """
    dex_lower = dex_id.lower() if dex_id else ""

    if dex_lower not in LIQUIDITY_SUPPORTED_DEXES:
        return False

    if network:
        expected_network = LIQUIDITY_SUPPORTED_DEXES.get(dex_lower)
        gecko_network = get_gecko_network(network)
        if gecko_network != expected_network:
            return False

    return True


def get_connector_for_dex(dex_id: str) -> Optional[str]:
    """Get the gateway connector name for a DEX ID

    Args:
        dex_id: DEX identifier from GeckoTerminal

    Returns:
        Connector name for gateway CLMM or None
    """
    dex_lower = dex_id.lower() if dex_id else ""

    # Direct mapping
    if dex_lower in LIQUIDITY_SUPPORTED_DEXES:
        return dex_lower

    # Handle variations
    if "meteora" in dex_lower:
        return "meteora"
    if "raydium" in dex_lower:
        return "raydium"
    if "orca" in dex_lower:
        return "orca"

    return None


async def fetch_ohlcv(
    pool_address: str,
    network: str,
    timeframe: str = "1h",
    currency: str = "usd",
    user_data: dict = None,
    limit: int = 100,
    before_timestamp: Optional[int] = None,
    token: str = "base",
) -> Tuple[Optional[List], Optional[str]]:
    """Fetch OHLCV data for any pool via GeckoTerminal

    Args:
        pool_address: Pool contract address
        network: Network identifier (will be converted to GeckoTerminal format)
        timeframe: OHLCV timeframe (see GECKO_TIMEFRAMES)
        currency: Price currency - "usd" or "token" (quote token)
        user_data: Optional user_data dict for caching
        limit: Number of candles to fetch (capped at GECKO_OHLCV_MAX)
        before_timestamp: Unix seconds; candles walk back from here. None asks for
            the latest candles, which is also what keeps a live chart's cache key
            stable — pass it only for a window that has actually closed.
        token: Which side of the pool to price, "base" or "quote". "quote" flips
            the series, for a venue pair quoted the other way round from the pool
            (``XRP-RLUSD`` against a ``RLUSD / XRP`` pool).

    Returns:
        Tuple of (ohlcv_list, error_message)
        ohlcv_list: List of [timestamp, open, high, low, close, volume] or None
        error_message: Error string if failed, None on success
    """
    try:
        gecko_network = get_gecko_network(network)
        timeframe = normalize_timeframe(timeframe)
        limit = max(1, min(int(limit), GECKO_OHLCV_MAX))

        # Check cache. limit/before_timestamp belong to the key: the same pool
        # charted over a historical window and over the live one are different
        # answers.
        if user_data is not None:
            cache_key = (
                f"ohlcv_{gecko_network}_{pool_address}_{timeframe}_{currency}"
                f"_{token}_{limit}_{before_timestamp or 0}"
            )
            cached = get_cached(user_data, cache_key, ttl=OHLCV_CACHE_TTL)
            if cached is not None:
                return cached, None
            # Sweep on miss, as cached_call does. Historical windows mint a fresh
            # key per executor, so a long-lived caller would otherwise accumulate
            # one entry per chart forever.
            evict_expired(user_data)

        # Pass all parameters explicitly:
        # - currency="token" means price in quote token (not USD)
        # - token="base" means OHLCV for the base token
        result = await _gecko_client().get_ohlcv(
            gecko_network,
            pool_address,
            timeframe,
            before_timestamp=before_timestamp,
            currency=currency,
            token=token,
            limit=limit,
        )

        # Parse response - handle different formats
        ohlcv_list = None

        try:
            import pandas as pd

            if isinstance(result, pd.DataFrame):
                if not result.empty:
                    # Convert DataFrame to list format
                    ohlcv_list = result.values.tolist()
        except ImportError:
            pass

        if ohlcv_list is None:
            if isinstance(result, list):
                ohlcv_list = result
            elif isinstance(result, dict):
                # Try nested structure
                data = result.get("data", result)
                if isinstance(data, dict):
                    attrs = data.get("attributes", data)
                    ohlcv_list = attrs.get("ohlcv_list", [])
                elif isinstance(data, list):
                    ohlcv_list = data

        if not ohlcv_list:
            return None, "No OHLCV data available"

        # Debug logging: show price range from OHLCV data
        if ohlcv_list:
            try:
                closes = [float(c[4]) for c in ohlcv_list if len(c) > 4 and c[4]]
                if closes:
                    logger.info(
                        f"OHLCV {pool_address[:8]}... {timeframe} currency={currency}: "
                        f"{len(ohlcv_list)} candles, price range [{min(closes):.6f} - {max(closes):.6f}]"
                    )
            except Exception as e:
                logger.debug(f"Could not log OHLCV price range: {e}")

        # Cache result
        if user_data is not None:
            set_cached(user_data, cache_key, ohlcv_list)

        return ohlcv_list, None

    except Exception as e:
        logger.error(f"Error fetching OHLCV: {e}", exc_info=True)
        return None, f"Failed to fetch OHLCV: {str(e)}"


async def fetch_liquidity_bins(
    pool_address: str,
    connector: str = "meteora",
    network: str = "solana-mainnet-beta",
    user_data: dict = None,
    chat_id: int = None,
    context=None,
) -> Tuple[Optional[List], Optional[Dict], Optional[str]]:
    """Fetch liquidity bin data for CLMM pools via gateway

    Args:
        pool_address: Pool contract address
        connector: DEX connector (meteora, raydium, orca)
        network: Network identifier
        user_data: Optional user_data dict for caching
        chat_id: Chat ID for per-chat server selection

    Returns:
        Tuple of (bins_list, pool_info, error_message)
        bins_list: List of bin dicts with price, base_token_amount, quote_token_amount
        pool_info: Full pool info dict
        error_message: Error string if failed, None on success
    """
    try:
        if not can_fetch_liquidity(connector):
            return None, None, f"Liquidity data not available for {connector}"

        # Check cache
        cache_key = f"pool_bins_{connector}_{pool_address}"
        if user_data is not None:
            cached = get_cached(user_data, cache_key, ttl=BINS_CACHE_TTL)
            if cached is not None:
                return cached.get("bins"), cached, None

        client = await get_client(chat_id, context=context)
        if not client:
            return None, None, "Gateway client not available"

        pool_info = None

        # First try get_pool_info (works for pools known to gateway)
        try:
            pool_info = await client.gateway_clmm.get_pool_info(
                connector=connector, network=network, pool_address=pool_address
            )
        except Exception as e:
            # If get_pool_info fails (e.g., pool not in gateway config or not a DLMM pool),
            # try finding the pool via get_pools search
            error_str = str(e)
            if "validation error" in error_str.lower() or "Field required" in error_str:
                logger.info(
                    f"Pool {pool_address[:12]}... not found via get_pool_info, trying get_pools search"
                )
                try:
                    # Search for pool by address using get_pools
                    search_result = await client.gateway_clmm.get_pools(
                        connector=connector, search_term=pool_address, limit=1
                    )
                    pools = search_result.get("pools", [])
                    if pools:
                        # Found the pool, but get_pools doesn't include bins
                        # Return pool info without bins - caller can handle this
                        pool_info = pools[0]
                        pool_info["address"] = pool_address
                        logger.info(
                            f"Found pool via get_pools: {pool_info.get('trading_pair', 'Unknown')}"
                        )
                    else:
                        # Pool not found in DLMM pools - might be an AMM pool or non-existent
                        logger.info(
                            f"Pool {pool_address[:12]}... not found in {connector} DLMM pools"
                        )
                        return (
                            None,
                            None,
                            f"Pool not found in {connector} DLMM pools. This may be an AMM pool or not a {connector} pool.",
                        )
                except Exception as search_e:
                    logger.warning(f"get_pools search also failed: {search_e}")
                    return (
                        None,
                        None,
                        f"Could not fetch pool info. Pool may not be a {connector} DLMM pool.",
                    )

            if pool_info is None:
                # Re-raise with a cleaner message for non-validation errors
                return None, None, f"Failed to fetch pool: {str(e)[:100]}"

        if not pool_info:
            return None, None, "Pool not found"

        bins = pool_info.get("bins", [])

        # Cache result
        if user_data is not None:
            set_cached(user_data, cache_key, pool_info)

        return bins, pool_info, None

    except Exception as e:
        logger.error(f"Error fetching liquidity bins: {e}", exc_info=True)
        return None, None, f"Failed to fetch liquidity: {str(e)}"


def normalize_pool_data(pool: dict, source: str = "gecko") -> Dict[str, Any]:
    """Normalize pool data from different sources to a common format

    Args:
        pool: Raw pool data dict
        source: Data source ("gecko" or "gateway")

    Returns:
        Normalized pool dict with consistent keys
    """
    if source == "gecko":
        # GeckoTerminal format
        attrs = pool.get("attributes", pool)

        return {
            "address": attrs.get("address") or pool.get("id", "").split("_")[-1],
            "name": attrs.get("name", "Unknown"),
            "base_token_symbol": attrs.get("base_token_symbol", "???"),
            "quote_token_symbol": attrs.get("quote_token_symbol", "???"),
            "base_token_price_usd": attrs.get("base_token_price_usd"),
            "quote_token_price_usd": attrs.get("quote_token_price_usd"),
            "network": pool.get("network") or attrs.get("network", "solana"),
            "dex_id": attrs.get("dex_id", "unknown"),
            "reserve_usd": attrs.get("reserve_in_usd"),
            "volume_24h": _get_nested_float(attrs, "volume_usd", "h24"),
            "volume_6h": _get_nested_float(attrs, "volume_usd", "h6"),
            "volume_1h": _get_nested_float(attrs, "volume_usd", "h1"),
            "price_change_24h": _get_nested_float(
                attrs, "price_change_percentage", "h24"
            ),
            "price_change_6h": _get_nested_float(
                attrs, "price_change_percentage", "h6"
            ),
            "price_change_1h": _get_nested_float(
                attrs, "price_change_percentage", "h1"
            ),
            "fdv_usd": attrs.get("fdv_usd"),
            "market_cap_usd": attrs.get("market_cap_usd"),
            "pool_created_at": attrs.get("pool_created_at"),
            "source": "gecko",
        }

    elif source == "gateway":
        # Gateway CLMM format
        return {
            "address": pool.get("pool_address") or pool.get("address", ""),
            "name": pool.get("trading_pair") or pool.get("name", "Unknown"),
            "base_token_symbol": pool.get("base_symbol", "???"),
            "quote_token_symbol": pool.get("quote_symbol", "???"),
            "base_token_price_usd": None,  # Not provided by gateway
            "quote_token_price_usd": None,
            "network": "solana",
            "dex_id": pool.get("connector", "meteora"),
            "reserve_usd": pool.get("liquidity") or pool.get("tvl"),
            "volume_24h": pool.get("volume_24h"),
            "price_change_24h": None,
            "current_price": pool.get("current_price") or pool.get("price"),
            "bin_step": pool.get("bin_step"),
            "apr": pool.get("apr"),
            "apy": pool.get("apy"),
            "base_fee_percentage": pool.get("base_fee_percentage"),
            "mint_x": pool.get("mint_x"),
            "mint_y": pool.get("mint_y"),
            "source": "gateway",
        }

    return pool


def _get_nested_float(data: dict, *keys) -> Optional[float]:
    """Get a nested float value from dict, trying multiple key patterns"""
    # Try nested access
    value = data
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            value = None
            break

    if value is not None:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass

    # Try flattened key with underscore
    flat_key = "_".join(keys)
    value = data.get(flat_key)
    if value is not None:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass

    # Try flattened key with dot
    flat_key = ".".join(keys)
    value = data.get(flat_key)
    if value is not None:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass

    return None


def extract_pair_from_name(name: str) -> Tuple[str, str]:
    """Extract base and quote symbols from pool name

    Args:
        name: Pool name like "SOL/USDC" or "SOL-USDC" or "SOL / USDC"

    Returns:
        Tuple of (base_symbol, quote_symbol)
    """
    if not name:
        return "???", "???"

    # Try different separators
    for sep in ["/", " / ", "-", " - "]:
        if sep in name:
            parts = name.split(sep)
            if len(parts) >= 2:
                return parts[0].strip(), parts[1].strip()

    return name, "???"
