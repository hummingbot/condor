"""Fetch price data from Hummingbot API."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_prices(
    client, connector_name: str = "", trading_pair: str = "", **_kw
) -> Optional[float]:
    """Fetch current price for a trading pair.

    Returns the mid-price as a float, or None on failure.
    """
    try:
        prices = await client.market_data.get_prices(
            connector_name=connector_name, trading_pairs=trading_pair
        )
        return prices.get("prices", {}).get(trading_pair)
    except Exception as e:
        logger.error("Error fetching price for %s: %s", trading_pair, e, exc_info=True)
        return None
