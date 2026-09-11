"""
Market data operations business logic.

One function, for the one market data tool this server still exposes (ARCH-308).
Candles, funding rates and order books are read as structured data through
``client.market_data.*`` inside ``run_code``; nothing here renders them into a
table any more, because nothing asks for one.
"""

from datetime import datetime
from typing import Any

from mcp_servers.hummingbot_api.formatters import format_prices_as_table


async def get_prices(
    client: Any, connector_name: str, trading_pairs: list[str]
) -> dict[str, Any]:
    """
    Get latest prices for trading pairs.

    Args:
        client: Hummingbot API client
        connector_name: Exchange connector name
        trading_pairs: List of trading pairs

    Returns:
        Dictionary containing prices data and formatted table
    """
    prices = await client.market_data.get_prices(
        connector_name=connector_name, trading_pairs=trading_pairs
    )

    # Format prices as table
    prices_table = format_prices_as_table(prices)

    timestamp = prices.get("timestamp", 0)
    time_str = (
        datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        if timestamp
        else "N/A"
    )

    return {
        "prices": prices,
        "prices_table": prices_table,
        "connector_name": connector_name,
        "timestamp": time_str,
    }
