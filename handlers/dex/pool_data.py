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
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from geckoterminal_py import GeckoTerminalAsyncClient

from config_manager import get_client

from ._shared import evict_expired, get_cached, set_cached

logger = logging.getLogger(__name__)

# CLMM venues an ``lp_executor`` can open a position on: (brand, the qualifiers its
# GeckoTerminal dex id must carry, the gecko networks it exists on). Deliberately
# separate from LIQUIDITY_SUPPORTED_DEXES below, which answers a different question
# (can Condor draw Telegram's liquidity-bin chart) and compares gecko network ids
# against chain *names*, so it is false for every Uniswap pool on Ethereum.
_CLMM_VENUES: Tuple[Tuple[str, List[str], set], ...] = (
    # Gecko's `meteora` is DLMM and `orca` is Whirlpools — both already the
    # concentrated product, so any qualifier means a different pool type.
    ("meteora", [], {"solana"}),
    ("orca", [], {"solana"}),
    # Plain `raydium` is the constant-product AMM v4; only `raydium-clmm` is CLMM.
    ("raydium", ["clmm"], {"solana"}),
    ("uniswap", ["v3"], {"eth", "arbitrum", "base", "polygon_pos", "optimism", "bsc"}),
    ("pancakeswap", ["v3"], {"bsc", "eth", "base", "arbitrum"}),
)

# Chain suffixes gecko tacks onto a dex id (`uniswap-v3-base`). They say where the
# venue is, not which product it is, so they are dropped before matching.
_CHAIN_TOKENS = {
    "solana",
    "ethereum",
    "eth",
    "base",
    "arbitrum",
    "bsc",
    "polygon",
    "pos",
    "optimism",
    "avalanche",
}

_DEX_ID_SPLIT_RE = re.compile(r"[-_]")

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

# The inverse of NETWORK_TO_GECKO, one entry per gecko chain: the Gateway network
# id a pool discovered on that chain belongs to. Lives here rather than in the
# Telegram handler that used to own it because the pool browser's rows are routed
# by it (``/dex/{gateway_network}/{address}``), and a gecko chain with no entry is
# one Gateway cannot reach — a state to render, not an error.
GECKO_TO_GATEWAY_NETWORK = {
    "solana": "solana-mainnet-beta",
    "eth": "ethereum-mainnet",
    "base": "base-mainnet",
    "arbitrum": "arbitrum-one",
    "bsc": "bsc-mainnet",
    "polygon_pos": "polygon-mainnet",
    "avalanche": "avalanche-mainnet",
    "optimism": "optimism-mainnet",
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
# Pool *lists* are the pool browser's hot path and GeckoTerminal rate-limits per
# IP across every dashboard viewer and every polling chart, so these are not an
# optimization: an un-cached browser refetching per keystroke would starve the
# candle poll loop of the same budget.
POOL_LIST_TTL = 60  # gecko trending/top/new/token lists
GATEWAY_POOL_LIST_TTL = 30  # gateway CLMM lists, per (connector, search)

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
# Both hold normalized pool dicts, and an empty dict for "asked, there is no such
# pool" — a real answer worth caching, unlike a failed lookup, which is not cached
# at all. The address-only forms (fetch_token_top_pool, fetch_pair_top_pool) read
# these same entries.
_token_pool_cache: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}
_pair_pool_cache: Dict[
    Tuple[str, str, str], Tuple[float, Tuple[Dict[str, Any], bool]]
] = {}


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


async def fetch_token_top_pool_info(
    mint: str, network: str, quote: str
) -> Optional[Dict[str, Any]]:
    """The token's highest-volume pool **quoted in ``quote``**, normalized.

    The quote match is not cosmetic: candles are requested with ``currency="token"``,
    so prices come back denominated in the pool's quote token. Charting a token/USDC
    pool underneath a token/SOL position would silently draw the right shape on the
    wrong scale, so a pool that does not match yields ``None`` and the chart stays
    empty instead.

    ``None`` distinguishes "no answer yet" from "asked and there is none": a
    no-match *is* cached (as ``{}``), a failed lookup is not, so a GeckoTerminal
    blip cannot pin an empty answer for the cache's full hour.
    """
    gnet = get_gecko_network(network)
    want = (quote or "").strip().upper()
    key = (gnet, mint, want)
    cached = _ttl_get(_token_pool_cache, key, TOKEN_POOL_TTL)
    if cached is not None:
        return cached or None

    try:
        pools = await _gecko_client().get_top_pools_by_network_token(gnet, mint)
    except Exception as e:
        # Includes the KeyError geckoterminal_py raises post-processing an empty
        # result set. Not cached — a transient failure must not pin "" for an hour.
        logger.info("top-pool lookup failed mint=%s net=%s: %s", mint, gnet, e)
        return None

    info: Dict[str, Any] = {}
    try:
        # Rows arrive sorted by 24h volume, so the first quote match is the deepest.
        for row in pools.to_dict("records"):
            if want and want not in _pool_quote_symbols(row.get("name")):
                continue
            if not str(row.get("address") or ""):
                continue
            info = normalize_pool_data(row, source="gecko")
            _fill_pool_pair_fields(info, network, row)
            break
    except Exception as e:
        logger.info("top-pool parse failed mint=%s net=%s: %s", mint, gnet, e)
        return None

    _ttl_put(_token_pool_cache, key, info, TOKEN_POOL_TTL)
    return info or None


async def fetch_token_top_pool(mint: str, network: str, quote: str) -> str:
    """Address of the token's highest-volume pool **quoted in ``quote``**.

    Used as a fallback when an executor's own ``pool_address`` yields no candles
    (a closed slot, a pool that never had one recorded). The address form of
    :func:`fetch_token_top_pool_info`, which is all the candle path needs; the LP
    panel wants the venue and price too and shares this function's cache entry.
    """
    info = await fetch_token_top_pool_info(mint, network, quote)
    return str((info or {}).get("address") or "")


async def fetch_pair_top_pool_info(
    base: str, quote: str, network: str
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Deepest pool trading ``base``/``quote`` on ``network``, found by *symbol*.

    For venues that quote tickers rather than addresses (see
    ``SYMBOL_PAIR_NETWORKS``): an xrpl pair like ``SOLO-XRP`` names no token that
    GeckoTerminal can look up, so this searches pools instead and keeps only exact
    symbol matches. Ticker collisions are real on XRPL — any issuer can mint a
    ``RLUSD`` — so the highest 24h volume wins, which is also the pool the
    GeckoTerminal UI shows first.

    Returns:
        ``(pool_info, inverted)``. ``inverted`` is True when the pool is quoted
        the other way round (pair ``XRP-RLUSD`` against a ``RLUSD / XRP`` pool);
        the caller must then read the quote token's price series, not the base's,
        and ``pool_info``'s pair fields are reported as the *caller* asked for
        them, not as the pool names them. ``(None, False)`` when nothing matches —
        an empty chart beats a chart drawn on the wrong pair.
    """
    gnet = get_gecko_network(network)
    b, q = (base or "").strip().upper(), (quote or "").strip().upper()
    if not b or not q:
        return None, False

    key = (gnet, b, q)
    cached = _ttl_get(_pair_pool_cache, key, TOKEN_POOL_TTL)
    if cached is not None:
        return (cached[0] or None), cached[1]

    try:
        # Both symbols in the query: GeckoTerminal matches them against the pool
        # name ("SOLO / XRP"), so this narrows the answer set before we filter.
        data = await _gecko_client().api_request(
            "GET", "search/pools", params={"query": f"{b} {q}", "network": gnet}
        )
    except Exception as e:
        # Not cached — a blip must not pin "no pool" for the full hour.
        logger.info("pair-pool search failed %s-%s net=%s: %s", b, q, gnet, e)
        return None, False

    best: Tuple[Dict[str, Any], bool] = ({}, False)
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
                flat = _flatten_search_pool(row)
                info = normalize_pool_data(flat, source="gecko")
                _fill_pool_pair_fields(
                    info, network, flat["attributes"], inverted=inverted
                )
                best, best_volume = (info, inverted), volume
    except Exception as e:
        logger.info("pair-pool parse failed %s-%s net=%s: %s", b, q, gnet, e)
        return None, False

    _ttl_put(_pair_pool_cache, key, best, TOKEN_POOL_TTL)
    return (best[0] or None), best[1]


async def fetch_pair_top_pool(base: str, quote: str, network: str) -> Tuple[str, bool]:
    """``(pool_address, inverted)`` for a symbol-quoted pair.

    The address form of :func:`fetch_pair_top_pool_info`, which is all the candle
    path needs; both share the same cache entry.
    """
    info, inverted = await fetch_pair_top_pool_info(base, quote, network)
    return str((info or {}).get("address") or ""), inverted


def _flatten_search_pool(row: Dict[str, Any]) -> Dict[str, Any]:
    """Lift a ``search/pools`` row's venue onto its attributes.

    The top-pools *token* endpoint carries ``dex_id`` as a column, but
    ``search/pools`` puts the venue in ``relationships.dex.data.id`` instead. The
    LP panel's whole question is which venue the pool is on, so the two shapes have
    to agree before :func:`normalize_pool_data` sees them.
    """
    attrs = dict(row.get("attributes") or {})
    if not attrs.get("dex_id"):
        dex = ((row.get("relationships") or {}).get("dex") or {}).get("data") or {}
        attrs["dex_id"] = str(dex.get("id") or "unknown")
    return {"id": row.get("id", ""), "attributes": attrs}


def _fill_pool_pair_fields(
    info: Dict[str, Any],
    network: str,
    attrs: Dict[str, Any],
    inverted: bool = False,
) -> None:
    """Add the pair fields the LP panel needs, in place.

    GeckoTerminal reports no token *symbol* on a pool row (only the ``"SOL / USDC"``
    display name) and prices in USD or as a quote-token ratio depending on the
    endpoint — and ``normalize_pool_data`` keeps neither ratio, hence ``attrs``, the
    row it was built from. This fills ``base_symbol`` / ``quote_symbol`` /
    ``current_price`` — base priced in quote, the scale every LP bound is expressed
    in — and pins ``network`` to what the caller asked for rather than
    ``normalize_pool_data``'s ``"solana"`` default.
    """
    base_sym, quote_sym = extract_pair_from_name(str(info.get("name") or ""))
    ratio = _get_nested_float(attrs, "base_token_price_quote_token")
    if ratio is None:
        base_usd = _get_nested_float(attrs, "base_token_price_usd")
        quote_usd = _get_nested_float(attrs, "quote_token_price_usd")
        if base_usd and quote_usd:
            ratio = base_usd / quote_usd

    if inverted:
        base_sym, quote_sym = quote_sym, base_sym
        ratio = (1 / ratio) if ratio else None

    info["base_symbol"] = base_sym.upper()
    info["quote_symbol"] = quote_sym.upper()
    info["current_price"] = ratio
    info["network"] = network


async def resolve_pool_info(
    network: str, trading_pair: str
) -> Optional[Dict[str, Any]]:
    """The pool a trading pair trades in, normalized, or ``None``.

    Mirrors ``condor.dex_candles._resolve_pool``'s branching — a base that *is* a
    mint resolves through the token's top pools, a ticker base through a symbol
    search — so the LP panel names the same pool the chart is drawn from rather
    than resolving pools by a second mechanism.

    It differs in one direction only: ``_resolve_pool`` restricts the symbol
    branch to ``SYMBOL_PAIR_NETWORKS``, so a ticker pair like ``SOL-USDC`` on
    Solana resolves here but not there (the chart stays blank while the panel
    still names a pool). Pointing ``_resolve_pool`` at this function would close
    that gap; it is a change to the candle path and deliberately not made here.
    """
    # Lazy, like dex_candles' own import of this module: one definition of what an
    # on-chain address looks like, and no import cycle between the two.
    from condor.dex_candles import ADDRESS_RE

    base, _, quote = str(trading_pair or "").partition("-")
    base, quote = base.strip(), quote.strip()
    if not base or not quote:
        return None

    if ADDRESS_RE.match(base):
        return await fetch_token_top_pool_info(base, network, quote)

    info, _inverted = await fetch_pair_top_pool_info(base, quote, network)
    return info


def network_has_clmm(network: str) -> bool:
    """Whether any known CLMM venue exists on this network's gecko chain.

    This is the authority for "can a venue on this network take an ``lp_executor``
    position", and it is deliberately *not* "is this a gateway network". ``xrpl`` is
    a gateway-adjacent id that lives in ``NETWORK_TO_GECKO`` for charting only — its
    AMM is not concentrated-liquidity, so it answers ``False`` here whether or not
    Gateway ever reports it in ``list_networks()``. Deriving the trait from
    ``_CLMM_VENUES`` instead of from gateway membership is what keeps that true.
    """
    gecko = get_gecko_network(network)
    return any(gecko in networks for _brand, _qualifiers, networks in _CLMM_VENUES)


def lp_provider_for_dex(dex_id: str, network: str) -> Optional[str]:
    """``"meteora/clmm"``-style LP provider for a GeckoTerminal dex id, or ``None``.

    ``None`` means the pool is not one an ``lp_executor`` can add liquidity to, and
    the caller is expected to report that dead end rather than guess: the API
    rejects an ``lp_provider`` that does not match the pool, so a wrong answer here
    becomes a failed executor.

    Matching is by (brand, product) because GeckoTerminal's dex ids are not a
    stable vocabulary and brand alone is not the position model. Uniswap V3 is
    ``uniswap_v3`` on Ethereum, ``uniswap-v3-base`` on Base and
    ``uniswap_v3_arbitrum`` on Arbitrum; ``uniswap-v4-ethereum`` and
    ``meteora-damm-v2`` share a brand with a supported venue but not its mechanics;
    and plain ``raydium`` is the constant-product AMM, while only ``raydium-clmm``
    is the concentrated pool the ``raydium/clmm`` connector drives.
    """
    tokens = [t for t in _DEX_ID_SPLIT_RE.split((dex_id or "").strip().lower()) if t]
    if not tokens:
        return None
    brand, qualifiers = tokens[0], [t for t in tokens[1:] if t not in _CHAIN_TOKENS]
    for name, required, networks in _CLMM_VENUES:
        if brand != name:
            continue
        if qualifiers != required:
            return None
        if get_gecko_network(network) not in networks:
            return None
        return f"{name}/clmm"
    return None


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


# Bins for one pool, keyed ``(connector, pool_address)``. Used when no caller
# supplies a ``user_data`` to cache in — i.e. by the web dashboard, where every
# viewer of a pool would otherwise open its own Gateway call at the same cadence.
# Same one-minute TTL as the per-chat cache: bins move with every swap through
# the active bin, so a longer one would draw liquidity that has already left.
_pool_bins_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}


async def fetch_liquidity_bins(
    pool_address: str,
    connector: str = "meteora",
    network: str = "solana-mainnet-beta",
    user_data: dict = None,
    chat_id: int = None,
    context=None,
    client=None,
) -> Tuple[Optional[List], Optional[Dict], Optional[str]]:
    """Fetch liquidity bin data for CLMM pools via gateway

    Args:
        pool_address: Pool contract address
        connector: DEX connector (meteora, raydium, orca)
        network: Network identifier
        user_data: Optional user_data dict for caching. When omitted the result
            is cached process-wide instead, so callers with no conversation of
            their own (the web dashboard) still share one Gateway call per pool.
        chat_id: Chat ID for per-chat server selection
        client: An already-resolved API client. The web side holds a server
            *name* and resolves its client through the config manager, which the
            ``get_client(chat_id)`` path below cannot express.

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
        else:
            cached = _ttl_get(
                _pool_bins_cache, (connector, pool_address), BINS_CACHE_TTL
            )
        if cached is not None:
            return cached.get("bins"), cached, None

        if client is None:
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
        else:
            _ttl_put(
                _pool_bins_cache, (connector, pool_address), pool_info, BINS_CACHE_TTL
            )

        return bins, pool_info, None

    except Exception as e:
        logger.error(f"Error fetching liquidity bins: {e}", exc_info=True)
        return None, None, f"Failed to fetch liquidity: {str(e)}"


def _token_address_from_id(token_id: Any) -> str:
    """``solana_<mint>`` → ``<mint>``. Anything without the prefix passes through.

    GeckoTerminal ids are ``{network}_{address}`` in the relationships block, but
    ``process_pools_list`` already strips them on its list endpoints and the
    by-address endpoint does not. Neither a base58 pubkey nor an EVM 0x-address
    contains an underscore, so stripping is idempotent and safe for both shapes.
    """
    text = str(token_id or "").strip()
    if not text or text.lower() == "nan":
        return ""
    _head, sep, tail = text.partition("_")
    return tail if sep and tail else text


def _gecko_token_address(pool: dict, attrs: dict, side: str) -> str:
    """The ``base``/``quote`` token address on a GeckoTerminal pool row.

    Reads the nested ``relationships.{side}_token.data.id`` the raw API returns,
    then the flat ``{side}_token_id`` / ``{side}_token_address`` columns a
    DataFrame row carries instead. Columns have moved between library versions,
    so every known shape is tried before giving up.
    """
    rel = ((pool.get("relationships") or {}).get(f"{side}_token") or {}).get("data")
    nested = (rel or {}).get("id") if isinstance(rel, dict) else None
    token_obj = attrs.get(f"{side}_token")
    candidates = (
        nested,
        attrs.get(f"{side}_token_id"),
        attrs.get(f"{side}_token_address"),
        token_obj.get("address") if isinstance(token_obj, dict) else None,
    )
    for candidate in candidates:
        address = _token_address_from_id(candidate)
        if address:
            return address
    return ""


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
            # A pool-first UI cannot do without the mints: an LP or DEX order
            # executor's trading_pair is ``<base_mint>-<quote_symbol>``, so a pool
            # with no base address is a pool nothing can be opened in.
            "base_token_address": _gecko_token_address(pool, attrs, "base"),
            "quote_token_address": _gecko_token_address(pool, attrs, "quote"),
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
            "base_token_address": str(pool.get("mint_x") or ""),
            "quote_token_address": str(pool.get("mint_y") or ""),
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


# ── Pool discovery ──
# The pool browser's three sources, behind one module. Telegram's /lp flow reaches
# GeckoTerminal and Gateway CLMM through its own inline fetching, each wrapped in
# MarkdownV2 table building and per-user caching, so none of it is callable from a
# web route. These are: the same upstreams, normalized, decorated and TTL-cached
# process-wide, with no presentation attached.

GECKO_POOL_VIEWS = ("trending", "top", "new", "token")

_gecko_pool_list_cache: Dict[Tuple, Tuple[float, List[Dict[str, Any]]]] = {}
_gateway_pool_list_cache: Dict[Tuple, Tuple[float, List[Dict[str, Any]]]] = {}
_pool_by_address_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}

# A pool list is a browser page, not a report: cap what an upstream can be asked
# for so one query cannot drag a thousand rows through normalization.
_POOL_LIST_MAX = 100


def _coerce_pool_rows(result: Any, limit: int = 20) -> List[Dict[str, Any]]:
    """``DataFrame | {"data": [...]} | [...]`` → a list of raw pool dicts.

    ``geckoterminal_py`` answers the pool-list endpoints with a pandas DataFrame,
    but the raw JSON shapes turn up too (a hand-rolled ``api_request``, a recorded
    fixture), and which one arrives has changed across library versions. Anything
    that is not a mapping is dropped rather than crashing the whole listing.
    """
    if result is None:
        return []

    try:
        import pandas as pd

        if isinstance(result, pd.DataFrame):
            rows = result.to_dict("records")
            return [r for r in rows if isinstance(r, dict)][:limit]
    except ImportError:  # pragma: no cover - pandas ships with geckoterminal_py
        pass

    if isinstance(result, dict):
        rows = result.get("data") or []
    elif isinstance(result, list):
        rows = result
    else:
        rows = getattr(result, "data", None) or []

    if isinstance(rows, dict):  # a by-address response: one pool, not a list
        rows = [rows]
    return [r for r in rows if isinstance(r, dict)][:limit]


def decorate_pool(info: Dict[str, Any], network: str) -> Dict[str, Any]:
    """Add the venue facts a pool-first UI cannot derive on its own, in place.

    A browser row has to answer three questions before the user clicks it — can I
    chart this pool, can I trade in it, can I LP in it — and every one of them is
    a fact about *our* connectors, not about the pool. Deciding them here keeps
    the frontend from re-deriving venue rules it cannot know, and keeps "chart yes,
    LP no" a renderable state rather than an executor that fails on create.
    """
    dex_id = str(info.get("dex_id") or "")
    gecko_network = get_gecko_network(network)
    gateway_network = GECKO_TO_GATEWAY_NETWORK.get(gecko_network) or (
        network if network in NETWORK_TO_GECKO else ""
    )

    # normalize_pool_data reports "???" for a symbol GeckoTerminal only publishes
    # inside the pool's display name; _fill_pool_pair_fields has parsed it by now.
    def _symbol(side: str) -> str:
        for key in (f"{side}_symbol", f"{side}_token_symbol"):
            value = str(info.get(key) or "").strip()
            if value and value != "???":
                return value.upper()
        return ""

    base_symbol, quote_symbol = _symbol("base"), _symbol("quote")
    info["base_symbol"] = base_symbol or "???"
    info["quote_symbol"] = quote_symbol or "???"
    info["base_token_symbol"] = base_symbol or "???"
    info["quote_token_symbol"] = quote_symbol or "???"

    provider = lp_provider_for_dex(dex_id, network)
    info["lp_provider"] = provider
    info["lp_supported"] = provider is not None
    info["gateway_network"] = gateway_network
    info["network"] = network
    info["gecko_network"] = gecko_network
    # A chain GeckoTerminal indexes is not necessarily one Gateway reaches.
    info["tradable"] = bool(gateway_network) and gateway_network in NETWORK_TO_GECKO
    # Consumed by the liquidity-bin depth chart (FEAT-045); decided here because
    # it is the same kind of venue fact as lp_supported.
    info["has_bins"] = can_fetch_liquidity(dex_id, network)

    # The <base_mint>-<quote_symbol> form LP and DEX order executors already carry,
    # except on a network whose venues quote tickers rather than addresses (xrpl),
    # where the base *is* a symbol.
    base = base_symbol if uses_symbol_pairs(network) else info.get("base_token_address")
    base = str(base or "")
    info["trading_pair"] = f"{base}-{quote_symbol}" if base and quote_symbol else ""
    return info


def _normalize_gecko_pool(row: Dict[str, Any], network: str) -> Dict[str, Any]:
    """One raw GeckoTerminal pool row → a decorated, normalized pool."""
    info = normalize_pool_data(row, source="gecko")
    _fill_pool_pair_fields(info, network, row)
    return decorate_pool(info, network)


def _clamp_pool_limit(limit: Any) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 20
    return max(1, min(value, _POOL_LIST_MAX))


async def list_gecko_pools(
    network: str,
    view: str = "trending",
    token: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """GeckoTerminal pools for a chain: ``trending``, ``top``, ``new`` or by token.

    ``network`` may be a Gateway network id or a gecko chain id — ``get_gecko_network``
    normalizes either. ``view="token"`` needs ``token`` to be a real address; a
    ticker would be pasted straight into a URL path segment, so it is refused here.

    An upstream failure answers ``[]`` (and is not cached), because the caller's
    honest rendering of "no pools" is more useful than an error the user cannot act
    on. A successful empty answer *is* cached — that chain really has no such list.
    """
    from condor.dex_candles import ADDRESS_RE

    gnet = get_gecko_network(network)
    view = (view or "trending").strip().lower()
    if view not in GECKO_POOL_VIEWS:
        view = "trending"
    token = (token or "").strip()
    if view == "token" and not ADDRESS_RE.match(token):
        return []
    limit = _clamp_pool_limit(limit)

    key = (gnet, view, token, limit)
    cached = _ttl_get(_gecko_pool_list_cache, key, POOL_LIST_TTL)
    if cached is not None:
        return [dict(row) for row in cached]

    client = _gecko_client()
    try:
        if view == "trending":
            result = await client.get_trending_pools_by_network(gnet)
        elif view == "top":
            result = await client.get_top_pools_by_network(gnet)
        elif view == "new":
            result = await client.get_new_pools_by_network(gnet)
        else:
            result = await client.get_top_pools_by_network_token(gnet, token)
    except Exception as e:
        # Includes the KeyError geckoterminal_py raises post-processing an empty
        # result set, and every rate-limit response.
        logger.warning("gecko pool list failed net=%s view=%s: %s", gnet, view, e)
        return []

    pools: List[Dict[str, Any]] = []
    for row in _coerce_pool_rows(result, limit):
        try:
            pool = _normalize_gecko_pool(row, network)
        except Exception as e:
            logger.info("gecko pool row skipped net=%s: %s", gnet, e)
            continue
        if pool.get("address"):
            pools.append(pool)

    _ttl_put(_gecko_pool_list_cache, key, pools, POOL_LIST_TTL)
    return [dict(row) for row in pools]


def gateway_connector_network(connector: str) -> str:
    """The network a Gateway CLMM connector's pools live on."""
    return LIQUIDITY_SUPPORTED_DEXES.get((connector or "").strip().lower(), "solana")


async def list_gateway_pools(
    client: Any,
    connector: str,
    search: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Gateway CLMM pools for a connector, optionally filtered by free text.

    Unlike the gecko sources this one is server-scoped (it needs a Hummingbot API
    client) and searches *within* a connector by name rather than by token address
    — which is why the route keeps the two behind a ``source`` discriminator
    instead of one parameter set with meaningless combinations.
    """
    connector = (connector or "").strip().lower()
    if not connector:
        return []
    search = (search or "").strip() or None
    limit = _clamp_pool_limit(limit)

    key = (connector, search or "", limit)
    cached = _ttl_get(_gateway_pool_list_cache, key, GATEWAY_POOL_LIST_TTL)
    if cached is not None:
        return [dict(row) for row in cached]

    if client is None or not hasattr(client, "gateway_clmm"):
        logger.warning("Gateway CLMM unavailable for connector=%s", connector)
        return []

    try:
        result = await client.gateway_clmm.get_pools(
            connector=connector, page=0, limit=limit, search_term=search
        )
    except Exception as e:
        logger.warning("gateway pool list failed connector=%s: %s", connector, e)
        return []

    raw = result.get("pools") if isinstance(result, dict) else result
    network = gateway_connector_network(connector)
    pools: List[Dict[str, Any]] = []
    for row in _coerce_pool_rows(raw, limit):
        row = dict(row)
        row.setdefault("connector", connector)
        try:
            pool = decorate_pool(normalize_pool_data(row, source="gateway"), network)
        except Exception as e:
            logger.info("gateway pool row skipped connector=%s: %s", connector, e)
            continue
        if pool.get("address"):
            pools.append(pool)

    _ttl_put(_gateway_pool_list_cache, key, pools, GATEWAY_POOL_LIST_TTL)
    return [dict(row) for row in pools]


async def fetch_pool_by_address(
    network: str, pool_address: str
) -> Optional[Dict[str, Any]]:
    """One normalized pool, for a deep link or a reload of the pool workspace.

    The workspace has to render from a URL alone, with no state carried over from
    the browser that linked to it, so the pool is fetched by address rather than
    looked up in a list. A pool that genuinely does not exist is cached as such;
    a failed lookup is not.
    """
    from condor.dex_candles import ADDRESS_RE

    pool_address = (pool_address or "").strip()
    if not ADDRESS_RE.match(pool_address):
        return None

    gnet = get_gecko_network(network)
    key = (gnet, pool_address)
    cached = _ttl_get(_pool_by_address_cache, key, POOL_LIST_TTL)
    if cached is not None:
        return dict(cached) if cached else None

    try:
        result = await _gecko_client().get_pool_by_network_address(gnet, pool_address)
    except Exception as e:
        logger.info("pool lookup failed net=%s pool=%s: %s", gnet, pool_address, e)
        return None

    rows = _coerce_pool_rows(result, 1)
    if not rows:
        _ttl_put(_pool_by_address_cache, key, {}, POOL_LIST_TTL)
        return None

    try:
        pool = _normalize_gecko_pool(rows[0], network)
    except Exception as e:
        logger.info("pool parse failed net=%s pool=%s: %s", gnet, pool_address, e)
        return None

    if not pool.get("address"):
        pool["address"] = pool_address
    _ttl_put(_pool_by_address_cache, key, pool, POOL_LIST_TTL)
    return dict(pool)
