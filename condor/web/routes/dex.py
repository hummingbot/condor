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
