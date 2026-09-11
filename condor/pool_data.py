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

import asyncio
import logging
import math
import os
import re
import time
from collections import OrderedDict, deque
from typing import Any, Dict, List, Optional, Tuple

import httpx
from geckoterminal_py import GeckoTerminalAsyncClient
from geckoterminal_py import constants as GECKO_CONSTANTS
from glom import glom

import utils.config  # noqa: F401  (imported for its load_dotenv() side effect)
from condor import orca_api

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

# GeckoTerminal network mapping.
#
# The keys are the network ids Gateway actually reports from ``/gateway/networks``
# — ``chain-network``, so every EVM chain is ``ethereum-<network>``. That is not
# cosmetic: the ids this table used to carry (``base-mainnet``, ``arbitrum-one``,
# ``polygon-mainnet``) are ones Gateway never emits, so every EVM pool fell through
# to "no gecko chain" and rendered as untradable. The older spellings are kept as
# aliases because persisted preferences and Telegram flows still hold them.
NETWORK_TO_GECKO = {
    # Solana
    "solana": "solana",
    "solana-mainnet-beta": "solana",
    # EVM, as Gateway names them
    "ethereum": "eth",
    "ethereum-mainnet": "eth",
    "ethereum-arbitrum": "arbitrum",
    "ethereum-avalanche": "avax",
    "ethereum-base": "base",
    "ethereum-bsc": "bsc",
    "ethereum-celo": "celo",
    "ethereum-optimism": "optimism",
    "ethereum-polygon": "polygon_pos",
    "ethereum-robinhoodchain": "robinhood",
    "ethereum-unichain": "unichain",
    # Legacy spellings still held by persisted preferences and Telegram flows.
    "arbitrum": "arbitrum",
    "arbitrum-one": "arbitrum",
    "base": "base",
    "base-mainnet": "base",
    "bsc": "bsc",
    "binance-smart-chain": "bsc",
    "polygon": "polygon_pos",
    "polygon-mainnet": "polygon_pos",
    # Gecko's id for Avalanche is `avax`, not `avalanche` — the old value here
    # matched no chain at all.
    "avalanche": "avax",
    "avalanche-mainnet": "avax",
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
#
# Every value must be a key of NETWORK_TO_GECKO, because `decorate_pool` decides
# `tradable` by looking the result up there; a value that round-trips to nothing
# marks a perfectly reachable chain untradable.
GECKO_TO_GATEWAY_NETWORK = {
    "solana": "solana-mainnet-beta",
    "eth": "ethereum-mainnet",
    "arbitrum": "ethereum-arbitrum",
    "avax": "ethereum-avalanche",
    "base": "ethereum-base",
    "bsc": "ethereum-bsc",
    "celo": "ethereum-celo",
    "optimism": "ethereum-optimism",
    "polygon_pos": "ethereum-polygon",
    "robinhood": "ethereum-robinhoodchain",
    "unichain": "ethereum-unichain",
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
# Only a *forming* candle needs this: every closed candle is immutable, so the
# series store below serves a window that has already ended without re-asking,
# however old the copy is. This is the freshness of the tail alone.
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
# One Orca snapshot serves every page and every column sort of the Orca tab, so
# this is a whole tab's upstream budget rather than one page's — cheaper than the
# Gateway path it replaces, which spent a request per page and a gecko one on top.
ORCA_POOL_LIST_TTL = 60

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


# ── How this process reaches GeckoTerminal ──
# Exactly two supported configurations, and no third:
#
# * **No key** (the default). GeckoTerminal's public API is free and needs no
#   account. Every guard below is shaped by its ~30 requests/minute *per IP*,
#   shared with every other caller on this host.
# * **A CoinGecko Analyst key.** The same data, served as CoinGecko's
#   ``/onchain`` endpoints at 500 requests/minute.
#
# CoinGecko's free *Demo* key is deliberately not supported: on the ``/onchain``
# endpoints it carries the same ~30/min ceiling as no key at all, so it would be
# a third configuration to document and test that buys nothing over the default.
# A Demo key set here is rejected by the paid host, which `_note_key_rejected`
# turns into a fall back to keyless rather than a dead DEX page.
#
# So the key is not a credential this module needs to function — it is the single
# input that decides how much of the pool browser's walk we can afford, which is
# why it widens the budget and the walk depth together rather than only swapping
# a URL.
_GECKO_PUBLIC_URL = "https://api.geckoterminal.com/api/v2"
_GECKO_ANALYST_URL = "https://pro-api.coingecko.com/api/v3/onchain"

# The same key also unlocks CoinGecko's ordinary *market* endpoints (/global,
# /coins/markets, /search/trending) — one level up from the /onchain mirror above,
# and the surface an agent routine reads for cross-market context. The plan and the
# auth header are identical there; only the host pair differs, so a surface is a
# row in this table rather than a second copy of the analyst/public branch.
_GECKO_SURFACE_URLS: Dict[str, Tuple[str, str]] = {
    # surface: (keyless host, Analyst host)
    "onchain": (_GECKO_PUBLIC_URL, _GECKO_ANALYST_URL),
    "market": (
        "https://api.coingecko.com/api/v3",
        "https://pro-api.coingecko.com/api/v3",
    ),
}

# Per-minute budget by plan, each held under the published ceiling so a burst
# straddling the window boundary still lands inside it, and so a Telegram handler
# racing the dashboard has headroom: 25 under the public ~30, 400 under Analyst's
# 500.
_GECKO_PLAN_RATE_LIMITS = {"public": 25, "analyst": 400}
# Concurrency rises with the budget, but far less: past a dozen sockets the walk
# is bounded by gecko's own latency, not by ours.
_GECKO_PLAN_CONCURRENCY = {"public": 4, "analyst": 12}

# Set when the paid host has rejected the configured key. Sticky for the life of
# the process: the key does not become valid again on the next request, and
# re-asking once per call would spend the budget discovering the same 401.
_gecko_key_rejected = False


def _gecko_key() -> str:
    return (os.environ.get("COINGECKO_API_KEY") or "").strip()


def gecko_plan() -> str:
    """``"analyst"`` or ``"public"`` — how this process reaches gecko.

    ``public`` is the supported default, not a degraded mode. It is also where a
    rejected key lands, so this answers what is actually in use rather than what
    was configured.
    """
    if _gecko_key() and not _gecko_key_rejected:
        return "analyst"
    return "public"


def coingecko_access(surface: str = "onchain") -> Tuple[str, Dict[str, str]]:
    """``(base_url, extra headers)`` for the plan in use, on one CoinGecko surface.

    The single place that decides how the configured key is sent, so callers
    outside this module — an agent routine reading the market endpoints, say —
    inherit the plan resolution *and* its fallback (a key the paid host rejected
    stops being sent everywhere at once) instead of re-deriving the branch and
    drifting from it. ``surface`` selects only the host pair: ``"onchain"`` for
    GeckoTerminal's pool data, ``"market"`` for CoinGecko's market endpoints.

    The public tier returns no headers at all so the library keeps its own
    versioned ``Accept``, which is a GeckoTerminal-specific pin and means nothing
    to the CoinGecko host.
    """
    try:
        public_url, analyst_url = _GECKO_SURFACE_URLS[surface]
    except KeyError:
        raise ValueError(f"unknown CoinGecko surface: {surface!r}") from None
    if gecko_plan() == "analyst":
        return analyst_url, {
            "Accept": "application/json",
            "x-cg-pro-api-key": _gecko_key(),
        }
    return public_url, {}


def _is_key_rejected(exc: BaseException) -> bool:
    """Whether the paid host refused the key, as opposed to refusing a request.

    Only 401 and 403 count. A 404 is a pool that does not exist, and gecko also
    answers 401 past page 10 on the *public* API — which is why this is consulted
    only while a key is actually in use.
    """
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    return status in (401, 403)


def _note_key_rejected() -> None:
    """Fall back to the keyless API for the rest of the process.

    A bad key must not be worse than no key: without this the 401 propagates,
    every caller degrades to an empty list, and the whole DEX surface reads as a
    chain with no pools. Loud, because the operator paid for a plan they are not
    getting — and because the usual cause is a free Demo key, which this build
    does not support.
    """
    global _gecko_key_rejected
    if _gecko_key_rejected:
        return
    _gecko_key_rejected = True
    logger.error(
        "CoinGecko rejected COINGECKO_API_KEY (401/403) — falling back to the "
        "keyless GeckoTerminal API at %d requests/minute. Condor supports an "
        "Analyst (or higher) key; a free Demo key is not accepted by this host.",
        _GECKO_PLAN_RATE_LIMITS["public"],
    )
    configure_gecko_access()


# ── GeckoTerminal client ──
# One client (and so one httpx connection pool) for the process. Constructing a
# fresh GeckoTerminalAsyncClient per call leaks an unclosed httpx.AsyncClient
# every time, which at chart-refresh rates exhausts sockets.
_gecko_client_instance: Optional[GeckoTerminalAsyncClient] = None


def _gecko_client() -> GeckoTerminalAsyncClient:
    global _gecko_client_instance
    if _gecko_client_instance is None:
        client = GeckoTerminalAsyncClient()
        base_url, headers = coingecko_access("onchain")
        # Set on the *instance*, not the class: `base_url` and `headers` are class
        # attributes on GeckoTerminalClientBase, so assigning to the class would
        # reconfigure the library for anything else importing it in-process.
        client.base_url = base_url
        # The httpx client is built with `headers=self.headers` in __init__, so the
        # class attribute is already baked in by now — the auth header has to go
        # onto the live client rather than onto `client.headers`.
        if headers:
            client.client.headers.update(headers)
        # geckoterminal_py builds its httpx client with no timeout override, so a
        # slow chain listing blocks for httpx's 5s default and then fails outright.
        # A read timeout well above that is worth more than a fast empty table.
        client.client.timeout = httpx.Timeout(_GECKO_TIMEOUT, connect=5.0)
        _gecko_client_instance = client
    return _gecko_client_instance


# GeckoTerminal's public tier is ~30 requests/minute *per IP*, and that budget is
# shared by every dashboard viewer, the candle poll loop and the pool browser at
# once. Three separate guards defend it, because they fail differently:
#
# * ``_gecko_gate`` (concurrency) turns a burst into a queue, so one page load
#   with a dex filter cannot open eight sockets at once.
# * ``_acquire_rate_slot`` (rate) is the one that actually keeps us under the
#   limit. A concurrency cap alone does not: four *fast* concurrent requests
#   sustain well over 100/min, which is how the budget was being spent before
#   anything looked like a burst.
# * ``_trip_breaker`` (circuit) stops calling at all for a moment once gecko has
#   said 429. Every request sent during a throttled window is spent for nothing
#   and keeps the window rolling, so the cheapest response is silence.
#
# A paid key widens the first two. It does not remove them: the budget is finite
# on every plan, and a 429 costs just as much when there is more headroom to
# waste. `configure_gecko_access()` below is what sets them from the plan.
_GECKO_TIMEOUT = 15.0
_GECKO_RETRIES = 2
_GECKO_RATE_WINDOW = 60.0
_gecko_semaphore: Optional[asyncio.Semaphore] = None

# Set by configure_gecko_access() at import. Left as module globals rather than
# read through a function at each use so the guards stay monkeypatchable in tests.
_GECKO_MAX_CONCURRENCY = _GECKO_PLAN_CONCURRENCY["public"]
_GECKO_RATE_LIMIT = _GECKO_PLAN_RATE_LIMITS["public"]

# How long a caller will queue for a slot before giving up. A web request is
# waiting on the other end, and this module's standing answer to "no budget" is
# the stale cache the callers already fall back to — which beats a request that
# hangs for most of a minute and then succeeds into a closed browser tab.
_GECKO_MAX_WAIT = 5.0

# Silence after a 429. Long enough for a rolling per-minute window to drain most
# of the way, short enough that a chart recovers on its next poll rather than
# staying blank until someone reloads.
_GECKO_COOLDOWN = 20.0

# How deep the pool browser may walk, by plan. On the public tier these are set
# by what a walk *costs* rather than by what would answer best; a paid budget
# lets them be set by the question instead. GECKO_MAX_PAGE is the ceiling on both
# because gecko answers 401 past page 10 — that is its limit, not our budget's,
# so a key does not lift it.
_GECKO_PLAN_FILTER_WALK = {"public": 3, "analyst": 10}
_GECKO_PLAN_SCOPED_MAX = {"public": 8, "analyst": 30}


def configure_gecko_access() -> str:
    """Point the module at the configured plan and size the guards for it.

    Called once at import, again by anything that changes the environment
    afterwards (tests, a re-read .env), and again if the key is rejected. Drops
    the cached client so the next call builds one against the host now in force,
    and returns the plan now in force.
    """
    global _GECKO_MAX_CONCURRENCY, _GECKO_RATE_LIMIT
    global _GECKO_FILTER_WALK_PAGES, _GECKO_SCOPED_MAX_REQUESTS
    global _gecko_client_instance, _gecko_semaphore

    plan = gecko_plan()
    _GECKO_RATE_LIMIT = _GECKO_PLAN_RATE_LIMITS[plan]
    _GECKO_MAX_CONCURRENCY = _GECKO_PLAN_CONCURRENCY[plan]
    _GECKO_FILTER_WALK_PAGES = min(_GECKO_PLAN_FILTER_WALK[plan], GECKO_MAX_PAGE)
    _GECKO_SCOPED_MAX_REQUESTS = _GECKO_PLAN_SCOPED_MAX[plan]

    # The old client holds the old base URL, headers and concurrency gate. It is
    # dropped rather than reconfigured: it may have requests in flight against
    # the previous host, and those should finish on it.
    _gecko_client_instance = None
    _gecko_semaphore = None
    return plan


class GeckoRateLimited(RuntimeError):
    """Raised *instead of* calling gecko when the shared budget is spent.

    Carries "rate limit" in its message so ``_is_rate_limited`` recognizes it and
    callers that already branch on a throttled upstream need no change.
    """


# Timestamps of the requests inside the current rolling window.
_gecko_call_times: deque = deque()
_gecko_rate_lock: Optional[asyncio.Lock] = None

# Circuit-breaker + health state. Process-wide, because the budget is per-IP:
# one viewer's 429 is every viewer's 429, and there is no point rediscovering it
# once per chart.
_gecko_cooldown_until: float = 0.0
_gecko_last_429: float = 0.0
_gecko_429_count: int = 0
_gecko_throttled_calls: int = 0


def _gecko_gate() -> asyncio.Semaphore:
    """The concurrency gate, built lazily so it binds to the running loop."""
    global _gecko_semaphore
    if _gecko_semaphore is None:
        _gecko_semaphore = asyncio.Semaphore(_GECKO_MAX_CONCURRENCY)
    return _gecko_semaphore


def _gecko_rate_gate() -> asyncio.Lock:
    """The rate gate, built lazily for the same reason as the semaphore."""
    global _gecko_rate_lock
    if _gecko_rate_lock is None:
        _gecko_rate_lock = asyncio.Lock()
    return _gecko_rate_lock


def _is_rate_limited(exc: BaseException) -> bool:
    if isinstance(exc, GeckoRateLimited):
        return True
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 429:
        return True
    return "429" in str(exc) or "rate limit" in str(exc).lower()


def _trip_breaker() -> None:
    """Record a 429 and stop outbound calls for ``_GECKO_COOLDOWN`` seconds."""
    global _gecko_cooldown_until, _gecko_last_429, _gecko_429_count
    now = time.time()
    _gecko_last_429 = now
    _gecko_429_count += 1
    _gecko_cooldown_until = now + _GECKO_COOLDOWN
    logger.warning(
        "GeckoTerminal rate-limited (429 #%d) — pausing all requests for %.0fs",
        _gecko_429_count,
        _GECKO_COOLDOWN,
    )


async def _acquire_rate_slot() -> None:
    """Claim one request from the shared per-minute budget.

    Waits for a slot up to ``_GECKO_MAX_WAIT``; raises ``GeckoRateLimited`` when
    the budget is spent for longer than that, or while the breaker is open. The
    wait happens under the lock so callers queue in arrival order instead of
    stampeding the instant a slot frees.
    """
    global _gecko_throttled_calls
    deadline = time.time() + _GECKO_MAX_WAIT
    async with _gecko_rate_gate():
        while True:
            now = time.time()

            if now < _gecko_cooldown_until:
                _gecko_throttled_calls += 1
                raise GeckoRateLimited(
                    f"GeckoTerminal rate limit: cooling down for another "
                    f"{_gecko_cooldown_until - now:.1f}s"
                )

            while (
                _gecko_call_times and now - _gecko_call_times[0] >= _GECKO_RATE_WINDOW
            ):
                _gecko_call_times.popleft()

            if len(_gecko_call_times) < _GECKO_RATE_LIMIT:
                _gecko_call_times.append(now)
                return

            # Budget full: wait exactly until the oldest request ages out.
            wait = _GECKO_RATE_WINDOW - (now - _gecko_call_times[0])
            if now + wait > deadline:
                _gecko_throttled_calls += 1
                raise GeckoRateLimited(
                    f"GeckoTerminal rate limit: {_GECKO_RATE_LIMIT} requests "
                    f"already sent this minute, next slot in {wait:.1f}s"
                )
            await asyncio.sleep(min(wait, 0.25))


def gecko_health() -> Dict[str, Any]:
    """Current state of the shared GeckoTerminal budget.

    Exists so "are we rate limited?" has an answer that is not "read the logs" —
    every failure downstream of here degrades to an empty list, which cannot tell
    a throttled upstream apart from a pool that simply has no trades.
    """
    now = time.time()
    recent = sum(1 for t in _gecko_call_times if now - t < _GECKO_RATE_WINDOW)
    return {
        "rate_limited": now < _gecko_cooldown_until,
        "cooldown_remaining": max(0.0, _gecko_cooldown_until - now),
        "requests_last_minute": recent,
        "budget": _GECKO_RATE_LIMIT,
        # Which plan the budget belongs to, so "we are throttled" can be read
        # alongside "on 25/min, keyless" rather than looking like a paid tier
        # failing. Never the key itself.
        "plan": gecko_plan(),
        # True when a key is configured but the paid host refused it, which is
        # otherwise indistinguishable from having set no key at all.
        "key_rejected": _gecko_key_rejected,
        "count_429": _gecko_429_count,
        "last_429_age": (now - _gecko_last_429) if _gecko_last_429 else None,
        "throttled_calls": _gecko_throttled_calls,
    }


def reset_gecko_throttle() -> None:
    """Forget the spent budget and any open breaker.

    For tests, which drive hundreds of calls through this module inside one real
    wall-clock minute and would otherwise throttle themselves.
    """
    global _gecko_cooldown_until, _gecko_last_429, _gecko_429_count
    global _gecko_throttled_calls, _gecko_key_rejected
    _gecko_call_times.clear()
    _gecko_key_rejected = False
    _gecko_inflight.clear()
    _gecko_cooldown_until = 0.0
    _gecko_last_429 = 0.0
    _gecko_429_count = 0
    _gecko_throttled_calls = 0
    for cache in (
        _gecko_page_cache,
        _pool_by_address_cache,
        _multi_pool_cache,
        _gecko_dexes_cache,
        _gateway_pool_list_cache,
        _token_symbol_cache,
        _token_pool_cache,
        _pair_pool_cache,
        _ohlcv_series,
        _pool_bins_cache,
    ):
        cache.clear()


async def gecko_call(method: str, *args: Any, **kwargs: Any) -> Any:
    """One GeckoTerminal client call, rate-gated and retried where that helps.

    ``method`` names a method on the shared client. Every outbound GeckoTerminal
    request in this module goes through here — calling the client directly skips
    the budget, which is exactly how the candle poll loop (the highest-frequency
    caller in the process) used to exhaust it while the guards sat idle in front
    of two pool-list walks.

    Only timeouts and transport errors are retried. A 429 is deliberately *not*:
    gecko's limit is a per-minute window, so no backoff short enough to sit inside
    a web request will clear it, and a retry only spends more of the very budget
    that is exhausted. It trips the breaker instead. A 404, or the 401 gecko
    answers past page 10 of the public API, is a final answer and is raised on the
    first attempt.

    The one exception is a key the paid host refuses, which is not this request's
    fault and is answered by changing hosts rather than by failing.
    """
    # Whether *this* call went out keyed, captured before the fallback can flip it.
    # A 401 means two different things on the two hosts — a rejected key on the
    # paid one, page 10 on the public one — so the check has to know which it was.
    keyed = gecko_plan() == "analyst"
    for attempt in range(_GECKO_RETRIES + 1):
        await _acquire_rate_slot()
        try:
            async with _gecko_gate():
                return await getattr(_gecko_client(), method)(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - re-raised below when not retryable
            if keyed and _is_key_rejected(e):
                # Does not spend the retry budget: the fallback changes the host,
                # so this is the first attempt at the request, not a second one.
                # It cannot recur — the retry below is keyless by construction.
                _note_key_rejected()
                return await gecko_call(method, *args, **kwargs)
            if _is_rate_limited(e):
                if not isinstance(e, GeckoRateLimited):
                    _trip_breaker()
                raise
            retryable = isinstance(e, (httpx.TimeoutException, httpx.TransportError))
            if not retryable or attempt == _GECKO_RETRIES:
                raise
            await asyncio.sleep(_GECKO_BACKOFF[attempt])
    raise RuntimeError("unreachable")


async def gecko_request(method: str, path: str, **kwargs) -> Any:
    """A raw REST call for paths the typed client does not expose."""
    return await gecko_call("api_request", method, path, **kwargs)


# Backoff between retries of a *timeout*, which is a slow chain listing rather than
# a throttled one — short, because a web request is waiting on it.
_GECKO_BACKOFF = (0.4, 1.0)


# ── Single-flight ──
# A TTL cache only helps once an answer has *arrived*. Flicking between explorer
# tabs asks for the same listings again while the first requests are still in the
# air, so every one of them misses the cache and opens its own gecko request — the
# fastest way there is to spend the minute's budget. This collapses concurrent
# callers of the same key onto one upstream request.
_gecko_inflight: Dict[Tuple, "asyncio.Task"] = {}


async def _single_flight(key: Tuple, factory) -> Any:
    """Run ``factory()`` once per key, sharing its result with every concurrent caller.

    The work runs as a detached task and awaiters ``shield`` it, so the very thing
    that causes the stampede — a viewer navigating away and cancelling their
    request — cannot also cancel the fetch the remaining viewers are waiting on.
    """
    task = _gecko_inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(factory())
        _gecko_inflight[key] = task

        def _clear(finished: "asyncio.Task", _key: Tuple = key) -> None:
            if _gecko_inflight.get(_key) is finished:
                _gecko_inflight.pop(_key, None)

        task.add_done_callback(_clear)
    return await asyncio.shield(task)


# ── Small TTL caches for token lookups ──
# These answers are process-wide (not per-user) and tiny. Capped so an unbounded
# stream of distinct mints cannot grow them without limit.
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


# ── Stale-tolerant cache, for upstreams that fail more often than they change ──
# The rule everywhere else — never cache a failure — is right, but on its own it
# turns every rate-limited request into an empty table: the browser asks, gecko
# says 429, and the user sees "No pools found" for a chain with thousands. Pool
# listings are ranked-by-volume snapshots, so a two-minute-old copy is a far better
# answer than no answer, and these two helpers exist so a failed refresh can fall
# back to one.
#
# Separate from _ttl_put because that one *sweeps* expired entries on every write,
# which would throw away the very copies this fallback depends on.
_STALE_MAX_AGE = 15 * 60


def _stale_put(cache: dict, key: tuple, value: Any) -> None:
    cache[key] = (time.time(), value)
    now = time.time()
    for k in [k for k, (ts, _) in cache.items() if now - ts >= _STALE_MAX_AGE]:
        cache.pop(k, None)
    while len(cache) > _TOKEN_CACHE_MAX:
        cache.pop(next(iter(cache)))


def _stale_get(cache: dict, key: tuple) -> Tuple[Optional[Any], float]:
    """The cached value regardless of freshness, with its age in seconds."""
    entry = cache.get(key)
    if not entry:
        return None, 0.0
    return entry[1], time.time() - entry[0]


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
        data = await gecko_call("get_specific_token_on_network", gnet, mint)
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
        pools = await gecko_call("get_top_pools_by_network_token", gnet, mint)
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
        data = await gecko_request(
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
    limit: int = 100,
    before_timestamp: Optional[int] = None,
    token: str = "base",
    use_cache: bool = True,
) -> Tuple[Optional[List], Optional[str]]:
    """Fetch OHLCV data for any pool via GeckoTerminal

    A pool's candles are global, not per-user, so they are held once for the
    process as a growing *series* (see ``_CandleSeries``) rather than as one entry
    per window: every caller — the dashboard's backfill, its live poll, a Telegram
    chart, a routine — slices the window it wants out of the same rows, and only
    the candles nobody has fetched yet cost a request.

    Args:
        pool_address: Pool contract address
        network: Network identifier (will be converted to GeckoTerminal format)
        timeframe: OHLCV timeframe (see GECKO_TIMEFRAMES)
        currency: Price currency - "usd" or "token" (quote token)
        limit: How many candles to *return*, newest last (capped at
            GECKO_OHLCV_MAX). It no longer sizes the upstream request: a request
            costs the same whatever its limit, so a cold series always buys the
            full GECKO_OHLCV_MAX and a warm one buys only its missing tail.
        before_timestamp: Unix seconds; candles walk back from here. None asks for
            the latest candles.
        token: Which side of the pool to price, "base" or "quote". "quote" flips
            the series, for a venue pair quoted the other way round from the pool
            (``XRP-RLUSD`` against a ``RLUSD / XRP`` pool).
        use_cache: False forces the live edge to be re-fetched — for a poll that
            must see the forming candle rather than one up to ``OHLCV_CACHE_TTL``
            old. It never discards history: the fresh candles are merged into the
            same series every cached reader shares. A window that has already
            closed is immutable and is served from memory either way.

    Returns:
        Tuple of (ohlcv_list, error_message)
        ohlcv_list: List of [timestamp, open, high, low, close, volume] or None
        error_message: Error string if failed, None on success
    """
    try:
        gecko_network = get_gecko_network(network)
        timeframe = normalize_timeframe(timeframe)
        limit = max(1, min(int(limit), GECKO_OHLCV_MAX))

        # The series is keyed by what identifies it upstream. The window the
        # caller wants is deliberately absent: that is what lets a 7-day chart, a
        # 3-day chart and a 5-candle poll on one pool share a single copy.
        series_key = (gecko_network, pool_address, timeframe, currency, token)
        series = _get_series(series_key)

        end = float(before_timestamp) if before_timestamp else time.time()
        oldest_wanted = end - limit * timeframe_seconds(timeframe)

        if series.serves(oldest_wanted, end, use_cache):
            return series.slice(end, limit), None

        # One upstream request per call, spent where it unblocks this window.
        # Reaching further back than one page is left to the next call: a chart
        # scrolling into history walks back a thousand candles at a time rather
        # than opening several requests at once.
        needs_history = (
            series.rows and not series.exhausted and series.oldest > oldest_wanted
        )
        req_before: Optional[int]
        if needs_history:
            # Walk back from whichever ceiling is lower — our oldest row, which
            # pages back contiguously behind what we hold, or the caller's own
            # ``before_timestamp``, which lands on a distant closed window in one
            # request instead of paging all the way down to it.
            req_before = int(min(series.oldest, end))
            req_limit = GECKO_OHLCV_MAX
        else:
            req_before = int(before_timestamp) if before_timestamp else None
            req_limit = (
                GECKO_OHLCV_MAX if req_before else _tail_count(series, timeframe)
            )

        # Coalesce on the upstream request: a stored series only helps once an
        # answer has landed, so concurrent callers of the same request (a
        # cache-bypassing live poll racing a chart render, say) would each spend
        # a request without this. A fetch that raises reaches every waiter and is
        # stored by nobody.
        flight_key = ("ohlcv", *series_key, req_limit, req_before or 0)
        rows = await _single_flight(
            flight_key,
            lambda: _fetch_ohlcv_upstream(
                gecko_network,
                pool_address,
                timeframe,
                currency,
                token,
                req_limit,
                req_before,
            ),
        )

        if rows:
            # Re-read the series: an eviction may have dropped it while the
            # request was open, and merging into a detached object would throw
            # the answer away.
            series = _get_series(series_key)
            series.merge(rows, req_limit, req_before, timeframe_seconds(timeframe))

        served = series.slice(end, limit)
        if not served:
            return None, "No OHLCV data available"
        return served, None

    except Exception as e:
        logger.error(f"Error fetching OHLCV: {e}", exc_info=True)
        return None, f"Failed to fetch OHLCV: {str(e)}"


async def _fetch_ohlcv_upstream(
    gecko_network: str,
    pool_address: str,
    timeframe: str,
    currency: str,
    token: str,
    limit: int,
    before_timestamp: Optional[int],
) -> Optional[List]:
    """One GeckoTerminal OHLCV request, normalized to a list of rows."""
    # Pass all parameters explicitly:
    # - currency="token" means price in quote token (not USD)
    # - token="base" means OHLCV for the base token
    result = await gecko_call(
        "get_ohlcv",
        gecko_network,
        pool_address,
        timeframe,
        before_timestamp=before_timestamp,
        currency=currency,
        token=token,
        limit=limit,
    )

    # Parse response - handle different formats
    rows = None

    try:
        import pandas as pd

        if isinstance(result, pd.DataFrame):
            if not result.empty:
                # Convert DataFrame to list format
                rows = result.values.tolist()
    except ImportError:
        pass

    if rows is None:
        if isinstance(result, list):
            rows = result
        elif isinstance(result, dict):
            # Try nested structure
            data = result.get("data", result)
            if isinstance(data, dict):
                attrs = data.get("attributes", data)
                rows = attrs.get("ohlcv_list", [])
            elif isinstance(data, list):
                rows = data

    # Debug logging: show price range from OHLCV data
    if rows:
        try:
            closes = [float(c[4]) for c in rows if len(c) > 4 and c[4]]
            if closes:
                logger.info(
                    f"OHLCV {pool_address[:8]}... {timeframe} currency={currency}: "
                    f"{len(rows)} candles, price range [{min(closes):.6f} - {max(closes):.6f}]"
                )
        except Exception as e:
            logger.debug(f"Could not log OHLCV price range: {e}")

    return rows


# ── OHLCV series store ──
#
# A pool's candles are one growing series, not a set of independent windows.
# Keying the cache by the *upstream request* (limit + before_timestamp) treated
# them as independent: the pool page's 7-day backfill, the same page's 3-day
# button, and the live poll's 5-candle tail were three entries holding three
# overlapping copies of the same candles, and a caller whose window differed by
# one candle from a cached one paid a fresh request for data already in memory.
#
# Two facts make the series form strictly better:
#
# * **A request costs the same whatever its `limit`.** GeckoTerminal's budget is
#   counted in requests, so a cold series always asks for the full
#   ``GECKO_OHLCV_MAX`` — the most history one request can buy — and every later
#   caller slices what it needs out of that.
# * **A closed candle never changes.** So only the tail is perishable: a window
#   that has already ended is served from memory regardless of age, and a live
#   window only needs the candles minted since the last fetch (``_tail_count``),
#   not the whole series again.
#
# Memory is bounded on both axes — ``_SERIES_MAX_ROWS`` per pool and
# ``_SERIES_MAX`` pools, evicted least-recently-used — because the old cache was
# not: it capped *entries*, not rows, so 512 windows of up to 1000 candles each
# was a ~67MB ceiling for a process that only ever charts a handful of pools.
# Measured, the ceiling here is ~18MB fully occupied (581KB per full series), and
# a few hundred KB in the normal case of one or two open charts.

# Rows kept per pool/timeframe: the live page plus one page of history walked
# back behind it, with headroom. Trimming drops the oldest rows, so a *much*
# older window — a finished executor from last month, charted while a live chart
# on the same pool and timeframe holds the tail — falls outside the series and is
# a cold read again. That is the deliberate edge of the bound, not an oversight.
_SERIES_MAX_ROWS = 2 * GECKO_OHLCV_MAX + 100
# Distinct (network, pool, timeframe, currency, token) series held at once.
_SERIES_MAX = 32


class _CandleSeries:
    """One pool's candles at one timeframe, and what we know about their edges.

    ``rows`` is timestamp-keyed because GeckoTerminal omits empty buckets: a pool
    with no trades for an hour returns no candles for it, so a series can never be
    treated as a dense array, and merging has to be by timestamp.

    Coverage is tracked rather than inferred, for the same reason — a gap in the
    middle is indistinguishable from missing data unless we remember what we
    actually asked for. It is a single *interval*, not a ceiling, because a
    request can land anywhere: the distant-jump below reaches a closed window ten
    days back in one call, and a live poll only ever buys the newest page, so the
    two together would otherwise look like one contiguous stretch with an
    unfetched week in the middle.

    * ``covered_from`` / ``covered_to`` — the span this series is known
      *complete* over. A fetch returns every candle upstream has between the
      oldest row it answered with and its ``before_timestamp``, so those bounds
      become fact: a later window inside them is answered from memory, forever,
      no matter how stale the series is. A fetch that lands clear of the interval
      *replaces* it rather than widening it — one honest block beats two blocks
      pretending to be one.
    * ``exhausted`` — upstream returned fewer rows than the maximum asked for, so
      there is no more history behind ``oldest``: the covered interval reaches
      down forever. Without this a chart of a pool younger than its window would
      re-ask for the same missing history on every single render.
    * ``tail_fetched_at`` — when the *live* edge (``before_timestamp=None``) was
      last refreshed. Only a historical fetch leaves this untouched, so a series
      built from a closed window never poses as a fresh live one.
    """

    __slots__ = ("rows", "covered_from", "covered_to", "exhausted", "tail_fetched_at")

    def __init__(self) -> None:
        self.rows: Dict[float, List] = {}
        self.covered_from: float = math.inf
        self.covered_to: float = 0.0
        self.exhausted: bool = False
        self.tail_fetched_at: float = 0.0

    @property
    def oldest(self) -> Optional[float]:
        return min(self.rows) if self.rows else None

    @property
    def newest(self) -> Optional[float]:
        return max(self.rows) if self.rows else None

    def merge(
        self, fetched: List, requested: int, before: Optional[int], tf_seconds: float
    ) -> None:
        """Fold an upstream answer in, and record what it proves about the edges."""
        block: Dict[float, List] = {}
        kept = 0
        for row in fetched:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            try:
                ts = float(row[0])
            except (TypeError, ValueError):
                continue
            block[ts] = list(row)
            kept += 1

        # Upstream answers with the newest `requested` candles before `before`,
        # so a short answer means it has nothing older left to give: this block
        # reaches all the way down.
        short = kept < requested
        # Everything between the block's floor and the request's ceiling is now
        # known — including the buckets that came back empty, which is what lets
        # a gappy pool be served from memory instead of re-asked forever.
        ceiling = float(before) if before else time.time()
        floor = -math.inf if short else (min(block) if block else ceiling)

        # A block clear of what we already hold leaves an unfetched gap between
        # the two, and a series that claimed the gap would answer a window inside
        # it with rows from the wrong period. Keep the new block alone instead —
        # the same one request any cold read costs. (An answer with no usable
        # rows proves nothing worth throwing a warm series away for.)
        disjoint = (
            bool(self.rows)
            and bool(block)
            and (
                floor > self.covered_to + tf_seconds
                or ceiling < self.covered_from - tf_seconds
            )
        )
        if disjoint:
            self.rows = block
            self.covered_from = floor
            self.covered_to = ceiling
            self.exhausted = short
            # The rows just dropped included whatever live edge we held, so the
            # series is no longer a fresh one until a live fetch says so below.
            self.tail_fetched_at = 0.0
        else:
            self.rows.update(block)
            self.covered_from = min(self.covered_from, floor)
            self.covered_to = max(self.covered_to, ceiling)
            if short:
                self.exhausted = True
        if not before:
            self.tail_fetched_at = time.time()

        # Trim oldest-first: the live edge is what every caller shares. The rows
        # dropped are no longer covered, so the floor rises with them.
        if len(self.rows) > _SERIES_MAX_ROWS:
            for ts in sorted(self.rows)[: len(self.rows) - _SERIES_MAX_ROWS]:
                del self.rows[ts]
            self.covered_from = min(self.rows)
            self.exhausted = False

    def serves(self, oldest_wanted: float, end: float, use_cache: bool) -> bool:
        """Whether this series can answer ``[oldest_wanted, end]`` on its own."""
        if not self.rows:
            return False
        deep_enough = self.exhausted or self.covered_from <= oldest_wanted
        if not deep_enough:
            return False
        # A window that has already closed is immutable — age is irrelevant.
        if end <= self.covered_to:
            return True
        # The live edge, on the other hand, is only as good as its last refresh.
        return use_cache and (time.time() - self.tail_fetched_at) < OHLCV_CACHE_TTL

    def slice(self, end: float, limit: int) -> List:
        """The newest ``limit`` rows at or before ``end``, ascending."""
        stamps = [ts for ts in sorted(self.rows) if ts <= end]
        return [self.rows[ts] for ts in stamps[-limit:]]


# Keyed by what identifies a series upstream — never by the window a caller
# happens to want, which is the whole point.
_ohlcv_series: "OrderedDict[Tuple, _CandleSeries]" = OrderedDict()


def _get_series(key: Tuple) -> _CandleSeries:
    """The series for ``key``, created if new, marked most-recently-used."""
    series = _ohlcv_series.get(key)
    if series is None:
        series = _CandleSeries()
        _ohlcv_series[key] = series
    _ohlcv_series.move_to_end(key)
    while len(_ohlcv_series) > _SERIES_MAX:
        _ohlcv_series.popitem(last=False)
    return series


def _tail_count(series: _CandleSeries, timeframe: str) -> int:
    """How many candles to ask for to catch a warm series up to now.

    The "only add what is missing" half of the store: a chart that polled three
    minutes ago needs the candles minted since, not the thousand it already
    holds. A cold series has no tail to extend and takes the full request.
    """
    newest = series.newest
    if newest is None:
        return GECKO_OHLCV_MAX
    behind = time.time() - newest
    # Two candles of headroom: the forming one, and the one it just closed.
    return max(
        2, min(math.ceil(behind / timeframe_seconds(timeframe)) + 2, GECKO_OHLCV_MAX)
    )


# Liquidity bins per ``(connector, pool_address)``. A global answer — a pool's
# liquidity does not depend on who is asking — so it is cached once for the
# process and every viewer (web dashboard, any Telegram chat) shares one upstream
# call. One-minute TTL: bins move with every swap through the active bin, so a
# longer one would draw liquidity that has already left. (Candles live in
# ``_ohlcv_series`` above, which is a series rather than a per-window cache.)
_pool_bins_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}


async def fetch_liquidity_bins(
    pool_address: str,
    connector: str = "meteora",
    network: str = "solana-mainnet-beta",
    client=None,
) -> Tuple[Optional[List], Optional[Dict], Optional[str]]:
    """Fetch liquidity bin data for CLMM pools via gateway

    Args:
        pool_address: Pool contract address
        connector: DEX connector (meteora, raydium, orca)
        network: Network identifier
        client: An already-resolved API client, required to reach Gateway.
            Resolving one is the caller's job — the web side holds a server
            *name*, a Telegram handler a chat id, and each has its own accessor
            for turning that into a client.

    Returns:
        Tuple of (bins_list, pool_info, error_message)
        bins_list: List of bin dicts with price, base_token_amount, quote_token_amount
        pool_info: Full pool info dict
        error_message: Error string if failed, None on success
    """
    try:
        if not can_fetch_liquidity(connector):
            return None, None, f"Liquidity data not available for {connector}"

        cached = _ttl_get(_pool_bins_cache, (connector, pool_address), BINS_CACHE_TTL)
        if cached is not None:
            return cached.get("bins"), cached, None

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

        _ttl_put(_pool_bins_cache, (connector, pool_address), pool_info, BINS_CACHE_TTL)

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

    elif source == "orca":
        return _normalize_orca_whirlpool(pool)

    return pool


# Orca states a fee tier in millionths of the trade: 400 is 0.04%.
_ORCA_FEE_RATE_DENOMINATOR = 10_000


def _normalize_orca_whirlpool(pool: dict) -> Dict[str, Any]:
    """One raw ``api.orca.so`` whirlpool → the same shape the other sources produce.

    Every figure Gateway's Orca listing leaves null is present here, so unlike a
    Gateway row this one needs no GeckoTerminal backfill and no estimated yield:
    ``apr`` is Orca's own realized fees (rewards included) over TVL, not volume
    times the base tier.
    """
    token_a = pool.get("tokenA") if isinstance(pool.get("tokenA"), dict) else {}
    token_b = pool.get("tokenB") if isinstance(pool.get("tokenB"), dict) else {}
    stats = pool.get("stats") if isinstance(pool.get("stats"), dict) else {}
    day = stats.get("24h") if isinstance(stats.get("24h"), dict) else {}

    base_symbol = str(token_a.get("symbol") or "???")
    quote_symbol = str(token_b.get("symbol") or "???")
    mint_a = str(pool.get("tokenMintA") or token_a.get("address") or "")
    mint_b = str(pool.get("tokenMintB") or token_b.get("address") or "")

    fee_rate = _finite(pool.get("feeRate"))
    info: Dict[str, Any] = {
        "address": str(pool.get("address") or ""),
        "name": f"{base_symbol}-{quote_symbol}",
        "base_token_symbol": base_symbol,
        "quote_token_symbol": quote_symbol,
        "base_token_address": mint_a,
        "quote_token_address": mint_b,
        # Carried under the Gateway row's names too, so a consumer that already
        # reads a CLMM pool's mints does not need to know which source it came from.
        "mint_x": mint_a,
        "mint_y": mint_b,
        "base_token_price_usd": None,
        "quote_token_price_usd": None,
        "network": "solana",
        "dex_id": "orca",
        "reserve_usd": pool.get("tvlUsdc"),
        "current_price": pool.get("price"),
        # Orca's tick spacing sits in the column Meteora's bin step does: both are
        # "how finely can I place a position in this pool".
        "bin_step": pool.get("tickSpacing"),
        "base_fee_percentage": (
            fee_rate / _ORCA_FEE_RATE_DENOMINATOR if fee_rate is not None else None
        ),
        "volume_24h": day.get("volume"),
        "fees_24h": day.get("fees"),
        # Orca reports deltas as ratios (-0.05 = -5%); the table renders percents.
        "price_change_24h": _scale_ratio(day.get("priceDelta")),
        "source": "orca",
    }

    # Fees *and* rewards over TVL, which is the number Orca's own UI shows — a
    # reward-heavy pool's real yield is not its fee yield.
    daily = _finite(day.get("yieldOverTvl"))
    if daily is None:
        daily = _finite(pool.get("yieldOverTvl"))
    # Always present, even as None, so an Orca row and a Gateway one have the same
    # keys and a consumer never has to ask which source it is holding.
    info["apr"] = daily * 100 if daily is not None else None
    info["apy"] = (
        ((1 + daily) ** _DAYS_PER_YEAR - 1) * 100 if daily is not None else None
    )
    # Not estimated: no ``~`` on either column.
    info["apr_estimated"] = False

    # Orca states every figure as a decimal *string*. The yields above are already
    # floats, so coercing here — rather than leaving it to ``decorate_pool`` as the
    # other sources do — is what keeps the row from being half one and half the
    # other for anything that reads it before decoration.
    return coerce_pool_numbers(info)


def _scale_ratio(value: Any) -> Optional[float]:
    """A ratio from Orca as a percentage, or None when it reports nothing."""
    ratio = _finite(value)
    return None if ratio is None else ratio * 100


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

# GeckoTerminal serves every pool list in fixed pages of 20 and answers 401 past
# page 10, so those two numbers — not a preference of ours — bound what the pool
# browser can page through.
GECKO_PAGE_SIZE = 20
GECKO_MAX_PAGE = 10
# With a dex filter on, matching rows are scattered across upstream pages and have
# to be walked for. Capped because the walk costs one rate-limited request per
# page and the browser is asking for a screenful, not a census.
#
# The cap is the reason a venue filter can come back empty and fall back to that
# venue's own top listing (see list_gecko_pools_page): three pages is not far
# enough to find a quiet venue in a chain-wide ranking. With a paid key it is
# raised to the full depth gecko will serve, so the filter answers the question
# that was actually asked instead of a nearby one.
_GECKO_FILTER_WALK_PAGES = _GECKO_PLAN_FILTER_WALK["public"]
# Merging several venues' own listings costs one request per venue per page, so
# the fan-out is bounded: past this, the chain-wide walk is used instead.
_GECKO_SCOPED_MAX_REQUESTS = _GECKO_PLAN_SCOPED_MAX["public"]

# The list endpoint behind each view. The library exposes one method per view but
# none of them take ``page``, so the paths are used directly.
_GECKO_VIEW_PATHS = {
    "trending": GECKO_CONSTANTS.GET_TRENDING_POOLS_BY_NETWORK_PATH,
    "top": GECKO_CONSTANTS.GET_TOP_POOLS_BY_NETWORK_PATH,
    "new": GECKO_CONSTANTS.GET_NEW_POOLS_BY_NETWORK_PATH,
}

# A chain's venues change on the order of weeks; the filter dropdown reads this.
GECKO_DEXES_TTL = 6 * 3600

_gecko_page_cache: Dict[Tuple, Tuple[float, List[Dict[str, Any]]]] = {}
_gecko_dexes_cache: Dict[Tuple, Tuple[float, List[Dict[str, str]]]] = {}
_gateway_pool_list_cache: Dict[Tuple, Tuple[float, List[Dict[str, Any]]]] = {}
_orca_pool_list_cache: Dict[Tuple, Tuple[float, List[Dict[str, Any]]]] = {}
_multi_pool_cache: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]]]] = {}
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


# Every numeric a pool row carries, and the two upstreams disagree on the type of
# nearly all of them: GeckoTerminal answers strings (``"12500000.0"``) *and*
# pandas ``NaN`` for a missing figure, Gateway answers strings for APR and TVL.
# A NaN is not JSON (FastAPI refuses to encode one, and the whole listing 500s),
# and a string has no ``.toFixed``, so the browser crashes formatting it. Both are
# settled here, once, rather than in every consumer.
_POOL_NUMERIC_FIELDS = (
    "reserve_usd",
    "volume_24h",
    "volume_6h",
    "volume_1h",
    "price_change_24h",
    "price_change_6h",
    "price_change_1h",
    "fdv_usd",
    "market_cap_usd",
    "current_price",
    "base_token_price_usd",
    "quote_token_price_usd",
    "apr",
    "apy",
    "fees_24h",
    "base_fee_percentage",
)


def _finite(value: Any) -> Optional[float]:
    """``value`` as a float, or None when it is missing, unparseable or NaN."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def coerce_pool_numbers(info: Dict[str, Any]) -> Dict[str, Any]:
    """Make every numeric on a pool row a finite float (or None), in place."""
    for field in _POOL_NUMERIC_FIELDS:
        if field in info:
            info[field] = _finite(info[field])
    if "bin_step" in info:
        step = _finite(info.get("bin_step"))
        info["bin_step"] = int(step) if step is not None else None
    return info


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
    return coerce_pool_numbers(info)


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


async def _gecko_page_rows(
    gnet: str, view: str, token: str, page: int, dex: Optional[str] = None
) -> Optional[List[Dict[str, Any]]]:
    """One raw upstream page of pool rows, or None when the fetch failed.

    Raw rather than normalized, because normalization depends on the *caller's*
    network id (``solana-mainnet-beta`` and ``solana`` are one gecko chain but two
    Gateway networks) while this cache is keyed by the gecko chain alone.

    None and ``[]`` mean different things and are cached differently: an empty page
    really is the end of the list, a failure is not, and caching a rate-limit reply
    for a minute would turn one throttled request into a minute of empty browsers.
    """
    key = (gnet, view, token, page, dex or "")
    cached = _ttl_get(_gecko_page_cache, key, POOL_LIST_TTL)
    if cached is not None:
        return [dict(row) for row in cached]

    # Concurrent askers for this exact page share one request. Rows are copied per
    # caller below, since the shared answer is one list object.
    rows = await _single_flight(
        ("page",) + key, lambda: _gecko_page_fetch(gnet, view, token, page, dex, key)
    )
    return None if rows is None else [dict(row) for row in rows]


async def _gecko_page_fetch(
    gnet: str,
    view: str,
    token: str,
    page: int,
    dex: Optional[str],
    key: Tuple,
) -> Optional[List[Dict[str, Any]]]:
    """The uncached fetch behind ``_gecko_page_rows``. One upstream request."""
    if dex:
        path = GECKO_CONSTANTS.GET_TOP_POOLS_BY_NETWORK_DEX_PATH.format(gnet, dex)
    elif view == "token":
        path = GECKO_CONSTANTS.GET_TOP_POOLS_BY_NETWORK_TOKEN_PATH.format(gnet, token)
    else:
        path = _GECKO_VIEW_PATHS[view].format(gnet)

    try:
        payload = await gecko_request("GET", path, params={"page": page})
        rows = [
            row
            for row in glom(payload, GECKO_CONSTANTS.POOL_SPEC)
            if isinstance(row, dict)
        ]
    except Exception as e:
        # Every rate-limit reply, and the 401 gecko answers past page 10.
        # A stale copy beats an empty table: this listing is a ranked snapshot, so
        # rows from a few minutes ago are still the answer to "what should I look
        # at", while "No pools found" reads as a fact about the chain. The stale
        # entry is returned but *not* refreshed, so the next request past the TTL
        # tries upstream again rather than settling into serving old rows forever.
        stale, age = _stale_get(_gecko_page_cache, key)
        logger.warning(
            "gecko pool page failed net=%s view=%s dex=%s page=%s: %s%s",
            gnet,
            view,
            dex or "-",
            page,
            e,
            f" (serving {age:.0f}s-old cache)" if stale is not None else "",
        )
        if stale is not None:
            return stale
        return None

    _stale_put(_gecko_page_cache, key, rows)
    return rows


def _normalized_gecko_rows(
    rows: List[Dict[str, Any]], network: str, dexes: set
) -> List[Dict[str, Any]]:
    """Raw upstream rows → decorated pools, keeping only the venues asked for."""
    pools: List[Dict[str, Any]] = []
    for row in rows:
        try:
            pool = _normalize_gecko_pool(row, network)
        except Exception as e:
            logger.info("gecko pool row skipped net=%s: %s", network, e)
            continue
        if not pool.get("address"):
            continue
        if dexes and str(pool.get("dex_id") or "").strip().lower() not in dexes:
            continue
        pools.append(pool)
    return pools


async def _scoped_top_page(
    gnet: str, network: str, venues: List[str], limit: int, page: int
) -> Dict[str, Any]:
    """Top pools across several venues, from each venue's own listing.

    Filtering the chain-wide list instead would answer almost nothing: Solana's
    top hundred pools are overwhelmingly one venue's, so "Top, on Meteora and
    Orca" scanned that way finds a handful of rows and calls it the answer. Each
    venue's own listing is already the top *of that venue*, so they are merged
    and re-ranked by volume — which is what the chain-wide list ranks by too.

    Bounded by ``_GECKO_SCOPED_MAX_REQUESTS`` because the fan-out is one request
    per venue per page, and every one of them shares the chart loop's budget.
    """
    skip = (page - 1) * limit
    pages_each = min(-(-(skip + limit) // GECKO_PAGE_SIZE), GECKO_MAX_PAGE)
    budget = _GECKO_SCOPED_MAX_REQUESTS

    rows: List[Dict[str, Any]] = []
    for venue in venues:
        for upstream in range(1, pages_each + 1):
            if budget <= 0:
                break
            budget -= 1
            got = await _gecko_page_rows(gnet, "top", "", upstream, dex=venue)
            if got is None:
                break  # this venue is as far as it goes; the others still count
            rows.extend(got)
            if len(got) < GECKO_PAGE_SIZE:
                break

    pools = _normalized_gecko_rows(rows, network, set())
    pools.sort(key=lambda p: p.get("volume_24h") or 0.0, reverse=True)
    return {
        "pools": pools[skip : skip + limit],
        "has_more": len(pools) > skip + limit,
    }


async def list_gecko_pools_page(
    network: str,
    view: str = "trending",
    token: Optional[str] = None,
    limit: int = 20,
    page: int = 1,
    dexes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """One page of GeckoTerminal pools, optionally narrowed to given venues.

    ``page`` is the browser's page, which is not the upstream's: gecko pages are a
    fixed 20 rows, the browser picks its own size, and with a dex filter on the
    matching rows are scattered across upstream pages. So the window
    ``[(page-1)*limit, page*limit)`` is cut out of a walk over upstream pages —
    started at the page that can hold it when unfiltered, and at the first page
    when filtering, since only then do we know how many rows matched before it.

    ``has_more`` is answered from what the walk saw, not guessed from a full page:
    a Next that lands on nothing is worse than no Next at all.
    """
    from condor.dex_candles import ADDRESS_RE

    gnet = get_gecko_network(network)
    view = (view or "trending").strip().lower()
    if view not in GECKO_POOL_VIEWS:
        view = "trending"
    token = (token or "").strip()
    if view == "token" and not ADDRESS_RE.match(token):
        return {"pools": [], "has_more": False}
    limit = _clamp_pool_limit(limit)
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    wanted = {d.strip().lower() for d in (dexes or []) if str(d).strip()}

    # One venue's *top* pools is a listing GeckoTerminal serves directly, already
    # scoped and already paged — so that case costs one request instead of a walk,
    # and pages as deep as the venue goes rather than as deep as we cared to scan.
    # Trending and New have no venue-scoped form, so those still walk.
    if view == "top" and len(wanted) > 1:
        return await _scoped_top_page(gnet, network, sorted(wanted), limit, page)
    scope = next(iter(wanted)) if view == "top" and len(wanted) == 1 else None
    if scope:
        wanted = set()

    skip = (page - 1) * limit
    # With nothing to filter out, row N is on a known upstream page, so the walk
    # starts there rather than re-fetching everything before it.
    upstream = 1 if wanted else skip // GECKO_PAGE_SIZE + 1
    local_skip = skip if wanted else skip % GECKO_PAGE_SIZE
    last_page = (
        min(GECKO_MAX_PAGE, upstream + _GECKO_FILTER_WALK_PAGES - 1)
        if wanted
        else GECKO_MAX_PAGE
    )

    collected: List[Dict[str, Any]] = []
    has_more = False
    while upstream <= last_page:
        rows = await _gecko_page_rows(gnet, view, token, upstream, dex=scope)
        if rows is None:
            # An upstream failure is not an empty chain: say nothing rather than
            # claiming this view has no pools, and never cache the claim. Mid-walk
            # though, the rows already in hand are a screenful the user can use —
            # a rate limit hit on page 3 must not blank pages 1 and 2.
            if not collected:
                return {"pools": [], "has_more": False}
            break
        collected.extend(_normalized_gecko_rows(rows, network, wanted))
        if len(collected) > local_skip + limit:
            has_more = True
            break
        if len(rows) < GECKO_PAGE_SIZE:
            break  # upstream has no more rows behind this page
        upstream += 1
        if len(collected) >= local_skip + limit:
            has_more = upstream <= last_page
            break

    pools = collected[local_skip : local_skip + limit]

    # Trending and New have no venue-scoped form upstream, so a dex filter on them
    # is a scan of the chain-wide list — and a venue that simply has nothing
    # trending right now then reads as a venue with no pools at all. It has pools;
    # they are just not in *this* ranking. So ask the venue's own top listing
    # instead of answering "none", which is the query the user meant by picking it.
    if not pools and wanted:
        scoped = await _scoped_top_page(gnet, network, sorted(wanted), limit, page)
        if scoped["pools"]:
            return scoped

    return {"pools": pools, "has_more": has_more and len(pools) == limit}


async def list_gecko_pools(
    network: str,
    view: str = "trending",
    token: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """The first page of :func:`list_gecko_pools_page`, for callers with no paging."""
    result = await list_gecko_pools_page(
        network, view=view, token=token, limit=limit, page=1
    )
    return result["pools"]


async def list_gecko_dexes(network: str) -> List[Dict[str, str]]:
    """The venues GeckoTerminal indexes on a chain, for the browser's dex filter.

    The filter offers what the chain actually has rather than a hardcoded list, so
    a new venue shows up without a deploy. An upstream failure answers ``[]``: a
    filter that cannot be populated is a filter the user simply does not see.
    """
    gnet = get_gecko_network(network)
    cached = _ttl_get(_gecko_dexes_cache, (gnet,), GECKO_DEXES_TTL)
    if cached is not None:
        return [dict(row) for row in cached]

    try:
        payload = await gecko_request(
            "GET", GECKO_CONSTANTS.GET_DEXES_BY_NETWORK_PATH.format(gnet)
        )
        rows = glom(payload, GECKO_CONSTANTS.DEXES_BY_NETWORK_SPEC)
    except Exception as e:
        # A chain's venue list changes about never, so a stale copy is as good as a
        # fresh one — and losing it makes the filter vanish from the browser.
        stale, age = _stale_get(_gecko_dexes_cache, (gnet,))
        logger.warning(
            "gecko dex list failed net=%s: %s%s",
            gnet,
            e,
            f" (serving {age:.0f}s-old cache)" if stale is not None else "",
        )
        return [dict(row) for row in stale] if stale is not None else []

    dexes = [
        {"id": str(row["id"]), "name": str(row.get("name") or row["id"])}
        for row in rows
        if isinstance(row, dict) and row.get("id")
    ]
    _ttl_put(_gecko_dexes_cache, (gnet,), dexes, GECKO_DEXES_TTL)
    return [dict(row) for row in dexes]


# GeckoTerminal's multi-pool endpoint takes at most 30 addresses in one path.
_GECKO_MULTI_MAX = 30


async def fetch_pools_by_addresses(
    network: str, addresses: List[str]
) -> List[Dict[str, Any]]:
    """Several pools by address in one request, for the favourites view.

    Favourites are stored as addresses, not as copies of the rows: a saved pool is
    a pointer, and its TVL, volume and price have to be as live as any other row in
    the table. Addresses that resolve to nothing are simply absent from the answer
    — a pool that has since been dropped from the index is not an error.
    """
    from condor.dex_candles import ADDRESS_RE

    gnet = get_gecko_network(network)
    seen: List[str] = []
    for address in addresses:
        address = str(address or "").strip()
        if address and ADDRESS_RE.match(address) and address not in seen:
            seen.append(address)
    if not seen:
        return []

    # The favourites view re-reads every starred pool on every visit, and this was
    # the one listing with no cache at all — a tab switch back and forth spent the
    # chain listing's rate-limit budget. Keyed by the batch, so it collides with
    # itself the way the other pool caches do.
    pools: List[Dict[str, Any]] = []
    for start in range(0, len(seen), _GECKO_MULTI_MAX):
        batch = seen[start : start + _GECKO_MULTI_MAX]
        batch_key = (gnet, ",".join(batch))
        cached = _ttl_get(_multi_pool_cache, batch_key, POOL_LIST_TTL)
        if cached is not None:
            pools.extend(dict(row) for row in cached)
            continue
        try:
            result = await gecko_call("get_multiple_pools_by_network", gnet, batch)
        except Exception as e:
            stale, age = _stale_get(_multi_pool_cache, batch_key)
            logger.warning(
                "gecko multi-pool failed net=%s: %s%s",
                gnet,
                e,
                f" (serving {age:.0f}s-old cache)" if stale is not None else "",
            )
            if stale is not None:
                pools.extend(dict(row) for row in stale)
            continue
        batch_pools: List[Dict[str, Any]] = []
        for row in _coerce_pool_rows(result, len(batch)):
            try:
                pool = _normalize_gecko_pool(row, network)
            except Exception as e:
                logger.info("gecko pool row skipped net=%s: %s", gnet, e)
                continue
            if pool.get("address"):
                batch_pools.append(pool)
        _stale_put(_multi_pool_cache, batch_key, batch_pools)
        pools.extend(dict(row) for row in batch_pools)

    # In the order the caller saved them, not the order gecko answers in.
    rank = {address: i for i, address in enumerate(seen)}
    pools.sort(key=lambda p: rank.get(str(p.get("address")), len(rank)))
    return pools


def gateway_connector_network(connector: str) -> str:
    """The network a Gateway CLMM connector's pools live on."""
    return LIQUIDITY_SUPPORTED_DEXES.get((connector or "").strip().lower(), "solana")


# A day is 365 compoundings of a daily rate — the same annualization Meteora's own
# `apy` applies to its `apr`, so a derived Orca figure sits on the same scale as
# the Meteora one in the row above it.
_DAYS_PER_YEAR = 365


def _estimate_pool_yield(pool: Dict[str, Any]) -> None:
    """Derive 24h fee/TVL and its annualization from volume and the fee tier.

    For a CLMM connector whose Gateway listing reports the fee tier and TVL but no
    yield, the two yield columns would otherwise be empty for every one of its
    pools. Fees are mechanical — volume times the tier — so with a volume from
    GeckoTerminal the figure Meteora reports directly can be computed rather than
    left blank. Orca no longer needs this: it is read from its own API, which
    reports realized fees (see ``list_orca_pools``).

    Marked ``apr_estimated`` because it is: it assumes every trade paid the base
    tier, which a dynamic-fee venue's real fees only approximate.
    """
    volume = _finite(pool.get("volume_24h"))
    tvl = _finite(pool.get("reserve_usd"))
    fee_pct = _finite(pool.get("base_fee_percentage"))
    if not volume or not tvl or fee_pct is None or tvl <= 0:
        return
    fees_24h = volume * fee_pct / 100
    daily = fees_24h / tvl
    pool["fees_24h"] = fees_24h
    pool["apr"] = daily * 100
    pool["apy"] = ((1 + daily) ** _DAYS_PER_YEAR - 1) * 100
    pool["apr_estimated"] = True


async def _fill_gateway_pool_gaps(pools: List[Dict[str, Any]], network: str) -> None:
    """Fill the figures a CLMM connector leaves blank, from GeckoTerminal.

    Meteora's own listing answers volume, fees and APR, but no connector reports a
    24h price change, and a row with an em-dash under the columns that say whether
    a pool is worth entering is barely a row. GeckoTerminal indexes the same pools
    under the same addresses, so one multi-pool request per page closes the gap.

    Only rows with an actual gap are looked up, cached with the page they filled.
    A failed lookup leaves the rows as they were: a blank column is what the
    browser already renders. Orca used to be the worst case here — TVL and nulls —
    and no longer passes through at all; it is read from its own API instead.
    """
    wanted = [
        str(p.get("address"))
        for p in pools
        if p.get("address")
        and (p.get("volume_24h") is None or p.get("price_change_24h") is None)
    ]
    if not wanted:
        return

    try:
        indexed = await fetch_pools_by_addresses(network, wanted)
    except Exception as e:  # pragma: no cover - fetch_pools_by_addresses swallows
        logger.info("gateway pool enrichment failed net=%s: %s", network, e)
        return

    by_address = {str(row.get("address")): row for row in indexed}
    for pool in pools:
        row = by_address.get(str(pool.get("address")))
        if not row:
            continue
        if pool.get("volume_24h") is None:
            pool["volume_24h"] = row.get("volume_24h")
        if pool.get("price_change_24h") is None:
            pool["price_change_24h"] = row.get("price_change_24h")
        # Gateway's TVL is the connector's own; gecko's only stands in for a gap.
        if pool.get("reserve_usd") is None:
            pool["reserve_usd"] = row.get("reserve_usd")
        if pool.get("apr") is None and pool.get("apy") is None:
            _estimate_pool_yield(pool)


async def list_gateway_pools(
    client: Any,
    connector: str,
    search: Optional[str] = None,
    limit: int = 20,
    page: int = 1,
) -> List[Dict[str, Any]]:
    """Gateway CLMM pools for a connector, optionally filtered by free text.

    Unlike the gecko sources this one is server-scoped (it needs a Hummingbot API
    client) and searches *within* a connector by name rather than by token address
    — which is why the route keeps the two behind a ``source`` discriminator
    instead of one parameter set with meaningless combinations.

    ``page`` is 1-based like every other page in the browser; Gateway counts from
    zero, and that translation stays here rather than in the route.
    """
    connector = (connector or "").strip().lower()
    if not connector:
        return []
    search = (search or "").strip() or None
    limit = _clamp_pool_limit(limit)
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1

    key = (connector, search or "", limit, page)
    cached = _ttl_get(_gateway_pool_list_cache, key, GATEWAY_POOL_LIST_TTL)
    if cached is not None:
        return [dict(row) for row in cached]

    if client is None or not hasattr(client, "gateway_clmm"):
        logger.warning("Gateway CLMM unavailable for connector=%s", connector)
        return []

    try:
        result = await client.gateway_clmm.get_pools(
            connector=connector, page=page - 1, limit=limit, search_term=search
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

    # Before the cache, so the extra upstream request is spent once per page and
    # not once per viewer of it.
    await _fill_gateway_pool_gaps(pools, network)

    _ttl_put(_gateway_pool_list_cache, key, pools, GATEWAY_POOL_LIST_TTL)
    return [dict(row) for row in pools]


# How many whirlpools one snapshot holds. Orca lists over 8,000, but ranked by TVL
# fewer than ~500 hold more than $10k and the rest are dust the browser would never
# page to — so this is the whole browsable set, not a first page of it.
ORCA_SNAPSHOT_SIZE = 500


async def _orca_snapshot(search: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """Every browsable Orca whirlpool, normalized and ranked by TVL.

    ``None`` — not ``[]`` — when the upstream failed and there is no stale copy:
    the caller falls back to the Gateway listing on that, and an empty table is
    the wrong thing to show for a venue with thousands of pools.
    """
    key = (search or "",)
    cached = _ttl_get(_orca_pool_list_cache, key, ORCA_POOL_LIST_TTL)
    if cached is not None:
        return cached

    # Flicking between tabs asks for the same snapshot while the first request is
    # still in the air; without this each one opens its own.
    async def _fetch() -> Optional[List[Dict[str, Any]]]:
        try:
            rows = await orca_api.fetch_whirlpools(ORCA_SNAPSHOT_SIZE, search=search)
        except Exception as e:
            stale, age = _stale_get(_orca_pool_list_cache, key)
            logger.warning(
                "orca pool list failed search=%r: %s%s",
                search or "",
                e,
                f" (serving {age:.0f}s-old cache)" if stale is not None else "",
            )
            return stale

        network = gateway_connector_network("orca")
        pools: List[Dict[str, Any]] = []
        for row in rows:
            try:
                pool = decorate_pool(normalize_pool_data(row, source="orca"), network)
            except Exception as e:
                logger.info("orca pool row skipped: %s", e)
                continue
            if pool.get("address"):
                pools.append(pool)
        _stale_put(_orca_pool_list_cache, key, pools)
        return pools

    return await _single_flight(("orca-pools", key), _fetch)


async def list_orca_pools(
    search: Optional[str] = None, limit: int = 20, page: int = 1
) -> Optional[Tuple[List[Dict[str, Any]], bool]]:
    """One page of Orca whirlpools, with whether another follows.

    Read straight from Orca rather than through Gateway, which proxies this very
    API and drops volume, fees, price and yield on the way — and which ignores its
    own ``page`` argument for this connector, so every page of the tab was page one.

    Sliced out of a cached snapshot because Orca paginates by cursor and the browser
    pages by number: one upstream request per minute serves every page, which is
    fewer than the request-per-page-plus-GeckoTerminal it replaces. ``has_more`` is
    then a fact rather than the "a full page implies another" guess Gateway forces.

    ``None`` when the upstream failed and nothing stale was cached.
    """
    limit = _clamp_pool_limit(limit)
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1

    pools = await _orca_snapshot((search or "").strip() or None)
    if pools is None:
        return None

    start = (page - 1) * limit
    window = pools[start : start + limit]
    return [dict(row) for row in window], len(pools) > start + limit


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
        # Shared with any concurrent viewer of the same pool — opening pools in
        # quick succession otherwise opens one request each.
        result = await _single_flight(
            ("pool", gnet, pool_address),
            lambda: gecko_call("get_pool_by_network_address", gnet, pool_address),
        )
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


# Applied at import, once every constant it reads exists. Without a key this is a
# no-op that reasserts the public defaults; with one it repoints the client and
# widens the guards before the first request is made.
configure_gecko_access()
