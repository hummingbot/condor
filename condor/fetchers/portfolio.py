"""Fetch portfolio / balance data from Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def fetch_portfolio(client, **_kw) -> Any:
    """Fetch full portfolio state from a server."""
    return await client.portfolio.get_state()


async def fetch_cex_balances(
    client, account_name: str, refresh: bool = False
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch balances for all CEX connectors on an account.

    Returns:
        Dict of connector_name -> list of balances
    """
    from condor.fetchers.connectors import is_cex_connector

    try:
        configured = await client.accounts.list_account_credentials(account_name)

        try:
            available = set(await client.connectors.list_connectors())
        except Exception:
            available = None

        cex = [c for c in configured if is_cex_connector(c)]
        if available is not None:
            cex = [c for c in cex if c in available]

        if not cex:
            return {}

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
