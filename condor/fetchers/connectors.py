"""Fetch connector information from Hummingbot API."""

import logging
from typing import Dict, List

from condor.fetchers._identifiers import validate_identifier
from condor.fetchers.market_data import fetch_candle_connectors
from condor.pool_data import NETWORK_TO_GECKO, network_has_clmm

logger = logging.getLogger(__name__)

_DEX_PREFIXES = (
    "solana",
    "ethereum",
    "polygon",
    "arbitrum",
    "base",
    "optimism",
    "avalanche",
)


def is_cex_connector(connector_name: str) -> bool:
    """Check if a connector is a CEX (not DEX/on-chain)."""
    lower = connector_name.lower()
    return not any(lower.startswith(p) for p in _DEX_PREFIXES)


async def fetch_connectors(client, **_kw) -> List[str]:
    """Fetch list of connectors available on a server."""
    return await client.connectors.list_connectors()


def _network_id(item) -> str:
    """The network id of a ``list_networks`` entry, whatever shape it arrives in.

    Gateway has returned plain strings and ``{"network_id": ...}`` / ``{"id": ...}``
    dicts across versions; the Telegram swap flow normalizes the same three shapes
    (``handlers/dex/swap.py``).
    """
    if isinstance(item, dict):
        return str(item.get("network_id") or item.get("id") or item)
    return str(item)


async def _chartable_gateway_networks(client) -> List[str]:
    """Gateway networks Condor can chart: ``list_networks()`` ∩ ``NETWORK_TO_GECKO``.

    The intersection is the point: a network is only offered to the trade panel if
    ``dex_candles.uses_gecko_candles`` will answer for it, so selecting one can
    never produce an empty chart. Do not widen this to every gateway network.

    Raises whatever the gateway request raises — the caller decides how much of the
    answer a gateway failure is allowed to take down.
    """
    response = await client.gateway.list_networks()
    networks = (response or {}).get("networks") or []
    return sorted(
        {n for n in (_network_id(i) for i in networks) if n in NETWORK_TO_GECKO}
    )


async def fetch_available_cex_connectors(
    client, account_name: str = "master_account", strict: bool = False, **_kw
) -> List[str]:
    """Fetch CEX connectors with credentials configured for an account.

    Intersects configured connectors with actually-available connectors
    and filters to CEX only.

    Args:
        strict: Raise when the credentials request itself fails, instead of
            reporting the account as having no connectors. Callers that cache
            the answer want the distinction: an unreachable server is worth
            retrying, "no credentials configured" is not.

    Raises:
        IdentifierError: if ``account_name`` is not a safe URL path segment.
    """
    # Before the try: the except below turns everything into [], which would
    # cache a bogus entry instead of surfacing the rejection.
    validate_identifier(account_name, "account name")

    try:
        configured = await client.accounts.list_account_credentials(account_name)

        try:
            available = set(await client.connectors.list_connectors())
        except Exception:
            available = None

        cex = [c for c in configured if is_cex_connector(c)]
        if available is not None:
            cex = [c for c in cex if c in available]
        return cex
    except Exception as e:
        if strict:
            raise
        logger.error("Error fetching connectors: %s", e, exc_info=True)
        return []


async def fetch_venues(
    client, account_name: str = "master_account", strict: bool = False, **_kw
) -> List[Dict]:
    """Venues the trade panel can offer, each with its independent traits.

    Returns ``[{"name", "hummingbot_market_data", "clmm_lp", "credentialed"}, ...]``
    sorted by name, over the union of three sources: the account's credentialed
    connectors, the candle-capable connectors and the chartable gateway networks.
    The three traits are *independent facts about the venue*, each with exactly one
    authority, because the panel's decisions (which executor tabs, which execution
    strategies, whether the order-book/rules/price endpoints are called, where the
    current price comes from, whether an order can be placed at all) are different
    questions. Answering them from a single ``cex``/``dex`` membership test — or
    from "does this account have keys here" — is what this shape replaces.

    ``hummingbot_market_data``: ``/market/tickers``, ``/market/order-book``,
    ``/market/trading-rules`` and ``/market/prices`` answer for this venue. Both a
    credentialed connector and a candle-capable one qualify: those endpoints are
    public reads and need no keys, which is why the candle list is an input here and
    not merely a chart detail. **A venue in the credentialed list is a Hummingbot
    connector whatever else it is in.** That is the ``xrpl`` guarantee and this is
    the only place it is expressed: ``xrpl`` is a first-class connector with a real
    order book whose *charts* come from GeckoTerminal, so it sits in
    ``NETWORK_TO_GECKO`` and would appear in the gateway half the day a Gateway
    version reports it — without this rule it would silently lose its order book,
    its trading rules and every strategy but MARKET.

    ``clmm_lp``: the venue is a gateway network *and* its gecko chain hosts a CLMM
    venue (``pool_data.network_has_clmm``), not merely that it is a gateway network.
    That is what keeps an order-book venue from being offered an LP tab it cannot
    honor.

    ``credentialed``: the venue reached the list from a *trading* source — account
    credentials or a configured Gateway — rather than from the candle list alone.
    A gateway network counts: it is there because a wallet-backed Gateway is
    configured, and demoting every gateway network to read-only would break the LP
    pages that trade fine today. Whether a Gateway network has a usable *wallet* is
    a separate question and deliberately not asked here.

    The candle source (CandlesFactory vs GeckoTerminal) is deliberately *not* a
    field: the server already forks on it internally and no UI decision needs it.

    Args:
        strict: Raise instead of reporting a venue-less server, so a failure is
            never cached as "there is nothing here". Credentials are load-bearing,
            so their failure always re-raises under ``strict``. A gateway or
            candle-connector failure only re-raises when it would leave the list
            empty: losing one source must not empty the whole dropdown, and a list
            that still carries the other sources' venues is not a cached lie.
    """
    # Credentials first: under strict this raises, and there is no point asking the
    # gateway about a server we cannot describe at all.
    connectors = await fetch_available_cex_connectors(
        client, account_name=account_name, strict=strict
    )

    degraded_error = None
    networks: List[str] = []
    try:
        networks = await _chartable_gateway_networks(client)
    except Exception as e:
        degraded_error = e
        logger.error("Error fetching gateway networks: %s", e, exc_info=True)

    candles: List[str] = []
    try:
        candles = await fetch_candle_connectors(client) or []
    except Exception as e:
        degraded_error = degraded_error or e
        logger.error("Error fetching candle connectors: %s", e, exc_info=True)

    credentialed = set(connectors)
    gateway = set(networks)
    candle_capable = set(candles)
    venues = [
        {
            "name": name,
            "hummingbot_market_data": name in credentialed or name in candle_capable,
            "clmm_lp": name in gateway and network_has_clmm(name),
            "credentialed": name in credentialed or name in gateway,
        }
        for name in sorted(credentialed | candle_capable | gateway)
    ]

    if strict and degraded_error is not None and not venues:
        raise degraded_error
    return venues
