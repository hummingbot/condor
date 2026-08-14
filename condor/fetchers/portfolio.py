"""Fetch portfolio / balance data from Hummingbot API."""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from condor.fetchers.connectors import fetch_available_cex_connectors

logger = logging.getLogger(__name__)

# Selectable history windows: range key -> (lookback seconds, candle interval).
# Lives here rather than in the REST route because both the route and the WS
# manager (which subscribes one SDS key per range) need it, and neither layer
# may import the other.
PORTFOLIO_HISTORY_RANGES: Dict[str, Tuple[int, str]] = {
    "1D": (86400, "5m"),
    "1W": (604800, "1h"),
    "1M": (2592000, "4h"),
    "3M": (7776000, "1d"),
}

# Poll cadence for a subscribed history range (seconds).
PORTFOLIO_HISTORY_INTERVAL = 120


async def fetch_portfolio(client, **_kw) -> Any:
    """Fetch full portfolio state from a server."""
    return await client.portfolio.get_state()


async def fetch_portfolio_history(client, range_key: str = "1D", **_kw) -> Any:
    """Fetch the raw portfolio history snapshots for one range window."""
    range_seconds, interval = PORTFOLIO_HISTORY_RANGES[range_key]
    start_time = int(time.time()) - range_seconds
    return await client.portfolio.get_history(
        start_time=start_time, interval=interval, limit=500
    )


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
