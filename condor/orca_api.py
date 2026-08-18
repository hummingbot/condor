"""Orca's public whirlpool API — the single module that talks to api.orca.so.

Gateway can already list Orca's pools, but only as a thin proxy over *this* API
that drops almost everything on the way: it answers TVL, fee tier and tick spacing
and nulls `volume_24h`, `fees_24h`, `apr`, `apy` and `current_price`, so the browser
had to make a second trip to GeckoTerminal for volume and then *estimate* the fee
yield from volume times the base tier. It also ignores its `page` argument for this
connector, so every page of the Orca tab was page one.

Reading Orca directly answers all of it in one request, with real fees (rewards
included) rather than an estimate, and with pagination that works.

Public and unauthenticated, but Cloudflare-fronted: a request with no or a
default-library User-Agent is answered 403, so one is always sent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

ORCA_API_BASE = "https://api.orca.so/v2/solana"

# `Python-urllib/x` is 403'd by Cloudflare outright. Identifying the caller is the
# courteous thing to do against a free public API in any case.
_USER_AGENT = "condor-dex-browser/1.0 (+https://github.com/hummingbot)"

# The browser is waiting on this: a slow listing is better abandoned and served
# from the stale cache than held open.
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# One client, and so one connection pool — the same reason ``pool_data`` keeps a
# single GeckoTerminal client. Kept with the loop it was built on: a pooled
# connection belongs to that loop, and reusing it from another one fails with
# "Event loop is closed" rather than reconnecting. The web app is one long-lived
# loop, but a routine or a test driving this through ``asyncio.run`` is not.
_client: Optional[httpx.AsyncClient] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None


def _http() -> httpx.AsyncClient:
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        # The previous client's connections died with its loop; there is nothing
        # left to close, so the reference is simply dropped.
        _client = httpx.AsyncClient(
            base_url=ORCA_API_BASE,
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        _client_loop = loop
    return _client


async def _get(path: str, params: Dict[str, Any]) -> Any:
    response = await _http().get(path, params=params)
    response.raise_for_status()
    return response.json()


def _rows(payload: Any) -> List[Dict[str, Any]]:
    """The whirlpool rows out of Orca's ``{"data": [...], "meta": {...}}`` envelope."""
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):  # a single-pool response
        data = [data]
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


async def fetch_whirlpools(
    size: int,
    search: Optional[str] = None,
    sort_by: str = "tvl",
    sort_direction: str = "desc",
    stats: str = "24h",
) -> List[Dict[str, Any]]:
    """Up to ``size`` whirlpools, ranked, as raw Orca rows.

    One request rather than a cursor walk. Orca lists over 8,000 whirlpools but
    fewer than ~500 of them hold more than $10k, and ranking by TVL puts those
    first — so a single page covers everything the browser would page through,
    which is also what lets the caller keep offset pagination over a cursor-only
    upstream.

    ``stats`` picks which time windows to hydrate; only the ones actually rendered
    are asked for, since the default hydrates 24h, 7d and 30d for every row.

    Raises on an upstream failure rather than returning ``[]``: a 429 and a chain
    with no pools are different answers, and only the caller knows whether it has
    a stale copy to fall back on.
    """
    params: Dict[str, Any] = {
        "size": max(1, int(size)),
        "sortBy": sort_by,
        "sortDirection": sort_direction,
        "stats": stats,
    }
    search = (search or "").strip()
    if search:
        # A different endpoint, because /pools filters by mint and this matches
        # free text against symbols and addresses — which is what the tab's search
        # box offers.
        params["q"] = search
        return _rows(await _get("/pools/search", params))
    return _rows(await _get("/pools", params))


async def close() -> None:
    """Release the connection pool. For tests and shutdown."""
    global _client, _client_loop
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    _client_loop = None
