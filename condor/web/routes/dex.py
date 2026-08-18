"""Pool discovery for the DEX page.

The DEX page is pool-first: `SOL-USDC` exists in dozens of pools across Meteora,
Orca, Raydium and Uniswap with different fee tiers, bin steps, TVL and APR, and
which one you are in *is* the decision. So this router browses pools rather than
pairs, from the same three sources Telegram's ``/lp`` flow offers.

Everything upstream lives in ``condor.pool_data`` — the single module that
talks to GeckoTerminal — so these handlers are auth, validation and shape, with no
fetching or normalization of their own.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from condor import dex_candles
from condor.web.auth import require_server_access
from condor.web.models import WebUser
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dex"])

# A token or pool address: an EVM 0x-address or a base58 Solana pubkey. Both reach
# GeckoTerminal as a URL path segment, so they are validated before they get there
# — the same guard ``/market/candles`` and ``/market/token-symbol`` already apply.
_ADDRESS_RE = dex_candles.ADDRESS_RE
_POOL_ADDRESS_RE = _ADDRESS_RE

# The chain the browser opens on, and the one it falls back to when Gateway cannot
# be reached: Solana is where Condor's CLMM connectors (Meteora, Orca) live.
DEFAULT_NETWORK = "solana-mainnet-beta"

_TESTNET_RE = re.compile(r"(testnet|devnet|sepolia|goerli)", re.IGNORECASE)

# Gateway names a network `chain-network`; the browser wants the part that varies.
_CHAIN_LABELS = {
    "solana-mainnet-beta": "Solana",
    "ethereum-mainnet": "Ethereum",
    "ethereum-base": "Base",
    "ethereum-robinhoodchain": "Robinhood",
    "ethereum-unichain": "Unichain",
    "ethereum-arbitrum": "Arbitrum",
    "ethereum-avalanche": "Avalanche",
    "ethereum-bsc": "BNB Chain",
    "ethereum-celo": "Celo",
    "ethereum-optimism": "Optimism",
    "ethereum-polygon": "Polygon",
}


def _chain_label(network_id: str, item: dict) -> str:
    if network_id in _CHAIN_LABELS:
        return _CHAIN_LABELS[network_id]
    # An unmapped network is still perfectly browsable; title-case its own name
    # rather than showing a raw id or dropping it.
    name = str(item.get("network") or network_id).replace("-", " ").strip()
    return name.title() or network_id


@router.get("/servers/{name}/dex/pools")
async def list_pools(
    name: str,
    source: str = Query(
        default="gecko",
        description="'gecko' (a chain's trending/top/new pools, or a token's), "
        "'orca' (Orca's own whirlpool API, with realized fees and yield) or "
        "'gateway' (a CLMM connector's own listing, with APR and bin step)",
    ),
    network: str = Query(
        default="solana-mainnet-beta",
        description="source=gecko: the chain, as a Gateway network or gecko id",
    ),
    view: str = Query(
        default="trending", description="source=gecko: trending | top | new | token"
    ),
    connector: str = Query(
        default="meteora", description="source=gateway: meteora | orca | raydium | ..."
    ),
    query: str | None = Query(
        default=None,
        description="source=gecko+view=token: the token address. "
        "source=gateway: free text matched against pool names.",
    ),
    dexes: str | None = Query(
        default=None,
        description="source=gecko: comma-separated GeckoTerminal dex ids to keep "
        "(meteora,orca,raydium-clmm). Empty means every venue.",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    page: int = Query(default=1, ge=1, description="1-based page of `limit` rows"),
    user: WebUser = Depends(require_server_access),
):
    """Pools to browse, from one of the three upstreams.

    ``source`` is the discriminator because they take genuinely different arguments
    — gecko searches by *token address* on a chain, Gateway and Orca search free
    text within a venue — and one merged parameter set would mean documenting which
    combinations are meaningless.

    An upstream failure answers ``{"pools": []}`` with a warning, like
    ``/market/venues``: the browser's empty state is something the user can act on,
    a 502 is not. ``source=orca`` falls back to Gateway before it comes to that.

    ``has_more`` — not a total — is what the browser's Next needs. Orca answers it
    exactly, having the whole ranked set in hand; Gateway and gecko report no total,
    so there a full page is the only evidence of another.
    """
    cm = get_config_manager()

    from condor.pool_data import (
        list_gateway_pools,
        list_gecko_pools_page,
        list_orca_pools,
    )

    source = (source or "gecko").strip().lower()

    if source == "orca":
        # Orca's own API, not Gateway's proxy of it: same pools, but with the
        # volume, fees, price and yield the proxy nulls, and with pagination that
        # is not stuck on page one. Gateway stays the fallback for when Orca is
        # rate-limiting — a degraded row beats an empty table.
        paged = await list_orca_pools(search=query, limit=limit, page=page)
        if paged is not None:
            pools, has_more = paged
            return {
                "pools": pools,
                "source": source,
                "page": page,
                "has_more": has_more,
            }
        logger.warning("Orca API unavailable for %s; falling back to Gateway", name)
        connector, source = "orca", "gateway"

    if source == "gateway":
        try:
            client = await cm.get_client(name)
        except Exception as e:
            logger.warning("Gateway pools unavailable for %s: %s", name, e)
            return {"pools": [], "source": source, "page": page, "has_more": False}
        pools = await list_gateway_pools(
            client, connector, search=query, limit=limit, page=page
        )
        # Gateway reports no total, so a full page is the only evidence of another.
        return {
            "pools": pools,
            "source": source,
            "page": page,
            "has_more": len(pools) >= limit,
        }

    if source != "gecko":
        raise HTTPException(status_code=400, detail="Unknown source")

    view = (view or "trending").strip().lower()
    token = (query or "").strip()
    # A ticker would be pasted straight into a GeckoTerminal path segment.
    if view == "token" and not _ADDRESS_RE.match(token):
        raise HTTPException(status_code=400, detail="Invalid token address")

    before = _throttle_counter()
    result = await list_gecko_pools_page(
        network,
        view=view,
        token=token,
        limit=limit,
        page=page,
        dexes=[d for d in (dexes or "").split(",") if d.strip()],
    )
    return {
        "pools": result["pools"],
        "source": source,
        "page": page,
        "has_more": result["has_more"],
        # Reported on the same response as the rows so the browser can say why a
        # page came back short, without a second round-trip to find out.
        "upstream": _upstream_state(_throttle_counter() > before),
    }


@router.get("/servers/{name}/dex/dexes")
async def list_dexes(
    name: str,
    network: str = Query(default="solana-mainnet-beta"),
    user: WebUser = Depends(require_server_access),
):
    """The venues on a chain, for the pool browser's dex filter.

    Offered by the chain rather than hardcoded, so a venue GeckoTerminal starts
    indexing is filterable without a deploy. Empty when the lookup fails — a filter
    with no options is one the browser simply does not show.
    """

    from condor.pool_data import list_gecko_dexes

    return {"dexes": await list_gecko_dexes(network)}


@router.get("/servers/{name}/dex/chains")
async def list_chains(
    name: str,
    user: WebUser = Depends(require_server_access),
):
    """The chains the pool browser can offer, from Gateway rather than a constant.

    A chain qualifies only if it is both something *this* Gateway is configured for
    and something GeckoTerminal indexes — a network Gateway reaches but Gecko does
    not has no pools to browse, and one Gecko indexes but Gateway cannot reach
    routes to a workspace that cannot trade. Testnets are dropped for the same
    reason: there is nothing to discover on them.

    Falls back to Solana alone when Gateway is unreachable, so the browser always
    has at least the chain it defaults to.
    """
    cm = get_config_manager()

    from condor.pool_data import NETWORK_TO_GECKO

    fallback = [{"network_id": DEFAULT_NETWORK, "chain": "solana", "label": "Solana"}]
    try:
        client = await cm.get_client(name)
        result = await client.gateway.list_networks()
    except Exception as e:
        logger.warning("Gateway networks unavailable for %s: %s", name, e)
        return {"chains": fallback}

    raw = result.get("networks", result) if isinstance(result, dict) else result
    chains = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        network_id = str(item.get("network_id") or "").strip()
        if not network_id or network_id not in NETWORK_TO_GECKO:
            continue
        if _TESTNET_RE.search(network_id):
            continue
        chains.append(
            {
                "network_id": network_id,
                "chain": str(item.get("chain") or ""),
                "label": _chain_label(network_id, item),
            }
        )

    if not chains:
        return {"chains": fallback}
    # Solana first — it is the default and the only chain with CLMM connectors
    # behind it — then alphabetically, so the order does not shift with Gateway's.
    chains.sort(key=lambda c: (c["network_id"] != DEFAULT_NETWORK, c["label"].lower()))
    return {"chains": chains}


def _throttle_counter() -> int:
    """Every refusal this process has made, as one monotonic number.

    Both refusals count: a 429 from gecko and a request this process declined to
    send because the shared budget was already spent. The second one leaves no
    trace in ``rate_limited`` — it raises without opening the breaker — so a
    counter is the only way a caller can ask "was *my* fetch throttled?".
    """
    from condor.pool_data import gecko_health

    health = gecko_health()
    return int(health.get("throttled_calls") or 0) + int(health.get("count_429") or 0)


def _upstream_state(throttled_request: bool = False) -> dict:
    """What GeckoTerminal's shared budget looks like right now.

    Every DEX view — the pool browser, a pool's stats, its chart — reads the same
    per-IP budget, and every failure downstream of it degrades to an empty list.
    So "no pools on this chain" and "throttled, ask again in a moment" arrive at
    the browser looking identical unless the state is reported alongside the rows.

    ``throttled_request`` is the stronger signal of the two and belongs to the
    call being answered: the budget can be spent without the breaker being open,
    in which case ``rate_limited`` is False while the rows are still missing.
    """
    from condor.pool_data import gecko_health

    health = gecko_health()
    last_429 = health.get("last_429_age")
    return {
        "rate_limited": bool(health.get("rate_limited")),
        "throttled_request": bool(throttled_request),
        "retry_in": round(float(health.get("cooldown_remaining") or 0.0), 1),
        "requests_last_minute": int(health.get("requests_last_minute") or 0),
        "budget": int(health.get("budget") or 0),
        "throttled_calls": int(health.get("throttled_calls") or 0),
        "count_429": int(health.get("count_429") or 0),
        "seconds_since_429": (
            round(float(last_429), 1) if last_429 is not None else None
        ),
    }


@router.get("/servers/{name}/dex/upstream")
async def get_upstream(
    name: str,
    user: WebUser = Depends(require_server_access),
):
    """Whether GeckoTerminal is currently throttling Condor.

    Reads process-local counters only — no outbound request — so the browser can
    poll it while a banner counts a cooldown down without spending the very
    budget it is waiting on. Declared above ``/dex/pools/{pool_address}`` for the
    same reason ``pools-by-address`` is: a path segment would be read as an
    address.
    """
    return _upstream_state()


class EnsureTokensRequest(BaseModel):
    """The tokens a pool is made of, as the browser already knows them."""

    network: str = Field(description="Gateway network id, e.g. solana-mainnet-beta")
    addresses: list[str] = Field(
        default_factory=list,
        description="Token mints/contracts to register — a pool's base and quote",
    )


@router.post("/servers/{name}/dex/tokens")
async def ensure_dex_tokens(
    name: str,
    body: EnsureTokensRequest,
    user: WebUser = Depends(require_server_access),
):
    """Make sure Gateway's token list holds this pool's tokens.

    Called when a pool workspace opens rather than when an order is placed,
    because the panel needs the tokens *before* the trade: an unlisted mint reads
    as a zero balance, which greys out the percentage presets and sizes an LP
    position against a wallet that looks empty. Idempotent and memoized, so
    re-opening a pool costs nothing.

    Never fatal. A pool whose tokens could not be registered is still browsable,
    chartable and (on Solana, where Gateway resolves a mint on chain) tradable —
    so a failure is reported per token and left for the panel to surface, not
    raised.
    """
    from condor.fetchers.gateway_tokens import ensure_tokens_listed, token_addresses
    from condor.pool_data import GECKO_TO_GATEWAY_NETWORK, NETWORK_TO_GECKO

    # Canonicalized rather than taken as given: a pool row can name its chain the
    # gecko way (``solana``) while Gateway only answers to ``solana-mainnet-beta``.
    gateway_network = GECKO_TO_GATEWAY_NETWORK.get(
        NETWORK_TO_GECKO.get(body.network, "")
    )
    if not gateway_network:
        raise HTTPException(status_code=400, detail="Unknown network")

    addresses = token_addresses(*body.addresses)
    if not addresses:
        return {"tokens": {}}

    cm = get_config_manager()
    client = await cm.get_client(name)
    try:
        verdicts = await ensure_tokens_listed(client, gateway_network, addresses)
    except Exception as e:
        logger.warning("ensure tokens failed on %s: %s", gateway_network, e)
        verdicts = {address: "failed" for address in addresses}
    return {"tokens": verdicts}


@router.get("/servers/{name}/dex/pools-by-address")
async def list_pools_by_address(
    name: str,
    addresses: str = Query(description="Comma-separated pool addresses (max 30)"),
    network: str = Query(default="solana-mainnet-beta"),
    user: WebUser = Depends(require_server_access),
):
    """Several pools by address at once, for the favourites view.

    A favourite is stored as an address, not as a copy of the row, so its TVL and
    volume have to be re-read like any other row rather than replayed from
    whenever it was starred. An address that no longer resolves is absent from the
    answer instead of failing the whole view — a de-indexed pool is not an error.

    Declared above ``/dex/pools/{pool_address}`` deliberately: a path segment would
    otherwise be read as an address.
    """

    from condor.pool_data import fetch_pools_by_addresses

    wanted = [a.strip() for a in (addresses or "").split(",") if a.strip()][:30]
    if not wanted:
        return {"pools": [], "upstream": _upstream_state()}
    before = _throttle_counter()
    pools = await fetch_pools_by_addresses(network, wanted)
    return {"pools": pools, "upstream": _upstream_state(_throttle_counter() > before)}


@router.get("/servers/{name}/dex/pools/{pool_address}")
async def get_pool(
    name: str,
    pool_address: str,
    network: str = Query(default="solana-mainnet-beta"),
    user: WebUser = Depends(require_server_access),
):
    """One pool by address, so ``/dex/{network}/{address}`` renders from a URL alone.

    404 when the pool is genuinely unknown, 503 when the lookup was refused by the
    shared GeckoTerminal budget: the workspace has nothing to draw either way, but
    only one of the two is worth retrying, and the user cannot tell them apart
    unless the route does.
    """

    if not _POOL_ADDRESS_RE.match(pool_address):
        raise HTTPException(status_code=400, detail="Invalid pool_address")

    from condor.pool_data import fetch_pool_by_address

    before = _throttle_counter()
    pool = await fetch_pool_by_address(network, pool_address)
    if not pool:
        # A throttled lookup is a different answer from an unknown pool, and the
        # difference is the whole point: one is worth retrying in a moment, the
        # other never resolves. 503 rather than 404 so the browser can say so and
        # offer a retry instead of sending the user back to the browser.
        state = _upstream_state(_throttle_counter() > before)
        if state["throttled_request"] or state["rate_limited"]:
            wait = state["retry_in"] or 20
            raise HTTPException(
                status_code=503,
                detail=(
                    "GeckoTerminal is rate limiting Condor, so this pool could "
                    f"not be read. Try again in about {wait:.0f}s."
                ),
            )
        raise HTTPException(status_code=404, detail="Pool not found")
    return pool


def _no_bins(reason: str) -> dict:
    """The unavailable state, with a 200.

    A pool Condor cannot draw bins for is something to render — a one-line
    reason where the depth column would be — not a failure the user can act on.
    Upstream failures degrade the same way, for the same reason.
    """
    return {
        "bins": [],
        "active_price": None,
        "bin_step": None,
        "available": False,
        "reason": reason,
    }


def _as_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN is not a price


@router.get("/servers/{name}/dex/pools/{pool_address}/bins")
async def get_pool_bins(
    name: str,
    pool_address: str,
    network: str = Query(default="solana-mainnet-beta"),
    connector: str = Query(
        default="meteora",
        description="The pool's Gateway CLMM connector, or the GeckoTerminal "
        "dex_id it is derived from (meteora-dlmm → meteora).",
    ),
    user: WebUser = Depends(require_server_access),
):
    """The pool's liquidity bins, for the depth column beside its chart.

    ``can_fetch_liquidity`` is the gate and is asked first — it is the same
    predicate that decorates every pool row with ``has_bins``, so the workspace
    already knows whether to call. It answers a *different* question from
    ``lp_provider_for_dex``: a pool can be LP-able without Condor being able to
    draw its bins, and the two must not be read as proxies for each other.

    The ``get_pool_info`` → ``get_pools`` fallback for pools outside Gateway's
    DLMM list stays where it already is, in ``fetch_liquidity_bins``; this
    handler injects a client and shapes the answer.
    """
    cm = get_config_manager()

    if not _POOL_ADDRESS_RE.match(pool_address):
        raise HTTPException(status_code=400, detail="Invalid pool_address")

    from condor.pool_data import (
        can_fetch_liquidity,
        fetch_liquidity_bins,
        get_connector_for_dex,
    )

    # Gated on the *dex id* as given, which is exactly what decorates each pool
    # row with ``has_bins`` — so the route and the browser never disagree about
    # which pools have a depth column. A Raydium AMM v4 pool is refused here for
    # the same reason it is refused there: only ``raydium`` (CLMM) is in the set.
    named = (connector or "").strip().lower()
    if not named or not can_fetch_liquidity(named, network):
        return _no_bins(
            f"Condor reads liquidity bins from Gateway CLMM, which does not cover "
            f"{named or 'this venue'} on {network}."
        )
    # The Gateway connector the gated dex id names (``meteora`` → ``meteora``).
    resolved = get_connector_for_dex(named) or named

    try:
        client = await cm.get_client(name)
    except Exception as e:
        logger.warning("Gateway client unavailable for %s: %s", name, e)
        client = None
    if client is None:
        return _no_bins("The API server is not reachable, so bins cannot be read.")

    try:
        bins, pool_info, error = await fetch_liquidity_bins(
            pool_address=pool_address,
            connector=resolved,
            network=network,
            client=client,
        )
    except Exception as e:  # fetch_liquidity_bins already swallows its own
        logger.warning("Liquidity bins failed for %s: %s", pool_address[:12], e)
        return _no_bins("Liquidity bins could not be read from Gateway.")

    if error or not bins:
        return _no_bins(error or "Gateway reports no liquidity bins for this pool.")

    pool_info = pool_info or {}
    base_usd = _as_float(pool_info.get("base_token_price_usd"))
    quote_usd = _as_float(pool_info.get("quote_token_price_usd"))

    rows = []
    for raw in bins:
        if not isinstance(raw, dict):
            continue
        price = _as_float(raw.get("price"))
        if price is None:
            continue
        base_amount = _as_float(raw.get("base_token_amount")) or 0.0
        quote_amount = _as_float(raw.get("quote_token_amount")) or 0.0
        # Only when both sides can be priced: a half-priced bin would size its
        # bar against a different unit than its neighbours.
        liquidity_usd = (
            base_amount * base_usd + quote_amount * quote_usd
            if base_usd is not None and quote_usd is not None
            else None
        )
        rows.append(
            {
                "price": price,
                "base_amount": base_amount,
                "quote_amount": quote_amount,
                "liquidity_usd": liquidity_usd,
            }
        )

    if not rows:
        return _no_bins("Gateway reports no liquidity bins for this pool.")

    active_price = _as_float(pool_info.get("price"))
    if active_price is None:
        active_price = _as_float(pool_info.get("current_price"))
    bin_step = pool_info.get("bin_step")
    try:
        bin_step = int(bin_step) if bin_step is not None else None
    except (TypeError, ValueError):
        bin_step = None

    return {
        "bins": rows,
        "active_price": active_price,
        "bin_step": bin_step,
        "available": True,
        "reason": None,
    }
