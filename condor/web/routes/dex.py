"""Pool discovery for the DEX page.

The DEX page is pool-first: `SOL-USDC` exists in dozens of pools across Meteora,
Orca, Raydium and Uniswap with different fee tiers, bin steps, TVL and APR, and
which one you are in *is* the decision. So this router browses pools rather than
pairs, from the same three sources Telegram's ``/lp`` flow offers.

Everything upstream lives in ``handlers.dex.pool_data`` — the single module that
talks to GeckoTerminal — so these handlers are auth, validation and shape, with no
fetching or normalization of their own.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from condor import dex_candles
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dex"])

# A token or pool address: an EVM 0x-address or a base58 Solana pubkey. Both reach
# GeckoTerminal as a URL path segment, so they are validated before they get there
# — the same guard ``/market/candles`` and ``/market/token-symbol`` already apply.
_ADDRESS_RE = dex_candles.ADDRESS_RE
_POOL_ADDRESS_RE = _ADDRESS_RE


@router.get("/servers/{name}/dex/pools")
async def list_pools(
    name: str,
    source: str = Query(
        default="gecko",
        description="'gecko' (a chain's trending/top/new pools, or a token's) or "
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
    limit: int = Query(default=20, ge=1, le=100),
    user: WebUser = Depends(get_current_user),
):
    """Pools to browse, from one of the two upstreams.

    ``source`` is the discriminator because the two take genuinely different
    arguments — gecko searches by *token address* on a chain, Gateway searches free
    text within a connector — and one merged parameter set would mean documenting
    which combinations are meaningless.

    An upstream failure answers ``{"pools": []}`` with a warning, like
    ``/market/venues``: the browser's empty state is something the user can act on,
    a 502 is not.
    """
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from handlers.dex.pool_data import list_gateway_pools, list_gecko_pools

    source = (source or "gecko").strip().lower()

    if source == "gateway":
        try:
            client = await cm.get_client(name)
        except Exception as e:
            logger.warning("Gateway pools unavailable for %s: %s", name, e)
            return {"pools": [], "source": source}
        pools = await list_gateway_pools(client, connector, search=query, limit=limit)
        return {"pools": pools, "source": source}

    if source != "gecko":
        raise HTTPException(status_code=400, detail="Unknown source")

    view = (view or "trending").strip().lower()
    token = (query or "").strip()
    # A ticker would be pasted straight into a GeckoTerminal path segment.
    if view == "token" and not _ADDRESS_RE.match(token):
        raise HTTPException(status_code=400, detail="Invalid token address")

    pools = await list_gecko_pools(network, view=view, token=token, limit=limit)
    return {"pools": pools, "source": source}


@router.get("/servers/{name}/dex/pools/{pool_address}")
async def get_pool(
    name: str,
    pool_address: str,
    network: str = Query(default="solana-mainnet-beta"),
    user: WebUser = Depends(get_current_user),
):
    """One pool by address, so ``/dex/{network}/{address}`` renders from a URL alone.

    404 when the pool is genuinely unknown *or* the lookup failed: either way the
    workspace has nothing to draw, and the two are indistinguishable to the user.
    """
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    if not _POOL_ADDRESS_RE.match(pool_address):
        raise HTTPException(status_code=400, detail="Invalid pool_address")

    from handlers.dex.pool_data import fetch_pool_by_address

    pool = await fetch_pool_by_address(network, pool_address)
    if not pool:
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
    user: WebUser = Depends(get_current_user),
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
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    if not _POOL_ADDRESS_RE.match(pool_address):
        raise HTTPException(status_code=400, detail="Invalid pool_address")

    from handlers.dex.pool_data import (
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
