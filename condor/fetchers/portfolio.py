"""Fetch portfolio / balance data from Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional

from condor.fetchers.connectors import fetch_available_cex_connectors

logger = logging.getLogger(__name__)


async def fetch_portfolio(client, **_kw) -> Any:
    """Fetch full portfolio state from a server."""
    return await client.portfolio.get_state()


async def fetch_portfolio_refreshed(client, **_kw) -> Any:
    """Fetch portfolio state with refresh=True to force exchange re-fetch."""
    return await client.portfolio.get_state(refresh=True)


async def fetch_cex_balances(
    client, account_name: str, refresh: bool = False
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch balances for all CEX connectors on an account.

    Returns:
        Dict of connector_name -> list of balances

    Raises:
        IdentifierError: if ``account_name`` is not a safe URL path segment.
    """
    # Outside the try: this validates ``account_name`` and the except below
    # turns everything into {}, which would cache a bogus entry instead of
    # surfacing the rejection.
    cex = await fetch_available_cex_connectors(client, account_name)
    if not cex:
        return {}

    try:
        portfolio_state = await client.portfolio.get_state(
            account_names=[account_name],
            connector_names=cex,
            refresh=refresh,
        )
        account_data = portfolio_state.get(account_name, {})
        return {k: v for k, v in account_data.items() if v}

    except Exception as e:
        logger.error("Error fetching CEX balances: %s", e, exc_info=True)
        return {}
