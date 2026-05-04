"""Fetch market data (prices, candles) from Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def fetch_current_price(
    client, connector_name: str, trading_pair: str, **_kw
) -> Optional[float]:
    """Fetch current price for a trading pair."""
    try:
        prices = await client.market_data.get_prices(
            connector_name=connector_name, trading_pairs=trading_pair
        )
        return prices.get("prices", {}).get(trading_pair)
    except Exception as e:
        logger.error("Error fetching price for %s: %s", trading_pair, e, exc_info=True)
        return None


async def fetch_candles(
    client,
    connector_name: str,
    trading_pair: str,
    interval: str = "1m",
    max_records: int = 420,
    **_kw,
) -> Optional[Dict[str, Any]]:
    """Fetch candle data for a trading pair."""
    try:
        candles = await client.market_data.get_candles(
            connector_name=connector_name,
            trading_pair=trading_pair,
            interval=interval,
            max_records=max_records,
        )
        if not candles:
            return None
        data = candles if isinstance(candles, list) else candles.get("data", [])
        if not data:
            return None
        return candles
    except Exception as e:
        logger.error("Error fetching candles for %s: %s", trading_pair, e, exc_info=True)
        return None


async def fetch_candle_connectors(client, **_kw) -> List[str]:
    """Fetch available candle connectors."""
    return await client.market_data.get_available_candle_connectors()
