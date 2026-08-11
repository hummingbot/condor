"""Fetch connector information from Hummingbot API."""

import logging
from typing import List

from condor.fetchers._identifiers import validate_identifier

logger = logging.getLogger(__name__)

_DEX_PREFIXES = (
    "solana", "ethereum", "polygon", "arbitrum", "base", "optimism", "avalanche",
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


async def fetch_gateway_networks(client, strict: bool = False, **_kw) -> List[str]:
    """Gateway networks that Condor can chart (subset of ``NETWORK_TO_GECKO``).

    The intersection is the point: a network is only offered to the trade panel if
    ``dex_candles.uses_gecko_candles`` will answer for it, so selecting one can
    never produce an empty chart. Do not widen this to every gateway network.

    Args:
        strict: Raise when the gateway request itself fails, instead of reporting
            that no networks exist. Callers that cache the answer want the
            distinction: an unreachable gateway is worth retrying, and must not be
            cached as "this server has no DEX".
    """
    # Lazy, like condor.dex_candles.uses_gecko_candles — condor.fetchers must not
    # import handlers at module scope.
    from handlers.dex.pool_data import NETWORK_TO_GECKO

    try:
        response = await client.gateway.list_networks()
        networks = (response or {}).get("networks") or []
        return sorted(
            {n for n in (_network_id(i) for i in networks) if n in NETWORK_TO_GECKO}
        )
    except Exception as e:
        if strict:
            raise
        logger.error("Error fetching gateway networks: %s", e, exc_info=True)
        return []


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
