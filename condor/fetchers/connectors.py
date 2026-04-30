"""Fetch connector information from Hummingbot API."""

import logging
from typing import List

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


async def fetch_available_cex_connectors(
    client, account_name: str = "master_account", **_kw
) -> List[str]:
    """Fetch CEX connectors with credentials configured for an account.

    Intersects configured connectors with actually-available connectors
    and filters to CEX only.
    """
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
        logger.error("Error fetching connectors: %s", e, exc_info=True)
        return []
