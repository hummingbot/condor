"""
Market data operations business logic.

This module provides the core business logic for market data operations including
prices, candles, funding rates, and order books.
"""
from datetime import datetime
from typing import Any, Literal

from hummingbot_mcp.formatters import (
    format_candles_as_table,
    format_order_book_as_table,
    format_prices_as_table,
)


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


async def get_candles(
    client: Any,
    connector_name: str,
    trading_pair: str,
    interval: str = "1h",
    days: int = 30,
) -> dict[str, Any]:
    """
    Get candle data for a trading pair.

    Args:
        client: Hummingbot API client
        connector_name: Exchange connector name
        trading_pair: Trading pair
        interval: Candle interval (e.g., '1h', '5m', '1d')
        days: Number of days of historical data

    Returns:
        Dictionary containing candles data and formatted table

    Raises:
        ValueError: If connector doesn't support candles or interval is invalid
    """
    # Check if connector supports candle data
    available_connectors = await client.market_data.get_available_candle_connectors()
    if connector_name not in available_connectors:
        raise ValueError(
            f"Connector '{connector_name}' does not support candle data. "
            f"Available connectors: {available_connectors}"
        )

    # Calculate max records based on interval
    if interval.endswith("m"):
        max_records = 1440 * days  # 1440 minutes in a day
    elif interval.endswith("h"):
        max_records = 24 * days
    elif interval.endswith("d"):
        max_records = days
    elif interval.endswith("w"):
        max_records = 7 * days
    else:
        raise ValueError(
            f"Unsupported interval format: {interval}. "
            f"Use '1m', '5m', '15m', '30m', '1h', '4h', '1d', or '1w'."
        )

    # Adjust for interval multiplier
    interval_num = interval[:-1]
    if interval_num:
        max_records = int(max_records / int(interval_num))

    # Fetch candles
    candles = await client.market_data.get_candles(
        connector_name=connector_name,
        trading_pair=trading_pair,
        interval=interval,
        max_records=max_records,
    )

    # Format candles as table
    candles_table = format_candles_as_table(candles)

    return {
        "candles": candles,
        "candles_table": candles_table,
        "connector_name": connector_name,
        "trading_pair": trading_pair,
        "interval": interval,
        "total_candles": len(candles),
    }


async def get_funding_rate(
    client: Any, connector_name: str, trading_pair: str
) -> dict[str, Any]:
    """
    Get funding rate for a perpetual trading pair.

    Args:
        client: Hummingbot API client
        connector_name: Exchange connector name (must have '_perpetual')
        trading_pair: Trading pair

    Returns:
        Dictionary containing funding rate data

    Raises:
        ValueError: If connector is not a perpetual connector
    """
    if "_perpetual" not in connector_name:
        raise ValueError(
            f"Connector '{connector_name}' is not a perpetual connector. "
            f"Funding rates are only available for perpetual connectors."
        )

    # Fetch funding rate
    funding_rate = await client.market_data.get_funding_info(
        connector_name=connector_name, trading_pair=trading_pair
    )

    # Format data
    next_funding_time = funding_rate.get("next_funding_time", 0)
    time_str = (
        datetime.fromtimestamp(next_funding_time).strftime("%Y-%m-%d %H:%M:%S")
        if next_funding_time
        else "N/A"
    )

    rate = funding_rate.get("funding_rate", 0)
    rate_pct = rate * 100  # Convert to percentage

    return {
        "connector_name": connector_name,
        "trading_pair": trading_pair,
        "funding_rate": rate,
        "funding_rate_pct": rate_pct,
        "mark_price": funding_rate.get("mark_price", 0),
        "index_price": funding_rate.get("index_price", 0),
        "next_funding_time": time_str,
    }


async def get_order_book(
    client: Any,
    connector_name: str,
    trading_pair: str,
    query_type: Literal[
        "snapshot",
        "volume_for_price",
        "price_for_volume",
        "quote_volume_for_price",
        "price_for_quote_volume",
    ],
    query_value: float | None = None,
    is_buy: bool = True,
) -> dict[str, Any]:
    """
    Get order book data for a trading pair.

    Args:
        client: Hummingbot API client
        connector_name: Exchange connector name
        trading_pair: Trading pair
        query_type: Type of order book query
        query_value: Value for query (required for non-snapshot queries)
        is_buy: Whether to analyze buy or sell side

    Returns:
        Dictionary containing order book data

    Raises:
        ValueError: If query_value is missing for non-snapshot queries
    """
    if query_type == "snapshot":
        # Get full order book snapshot
        order_book = await client.market_data.get_order_book(
            connector_name=connector_name, trading_pair=trading_pair
        )

        # Format order book as table
        order_book_table = format_order_book_as_table(order_book)

        timestamp = order_book.get("timestamp", 0)
        time_str = (
            datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            if timestamp
            else "N/A"
        )

        return {
            "query_type": "snapshot",
            "order_book": order_book,
            "order_book_table": order_book_table,
            "connector_name": connector_name,
            "trading_pair": trading_pair,
            "timestamp": time_str,
        }
    else:
        # Handle query-based requests
        if query_value is None:
            raise ValueError(f"query_value must be provided for query_type '{query_type}'")

        # Execute appropriate query
        if query_type == "volume_for_price":
            result = await client.market_data.get_volume_for_price(
                connector_name=connector_name,
                trading_pair=trading_pair,
                price=query_value,
                is_buy=is_buy,
            )
        elif query_type == "price_for_volume":
            result = await client.market_data.get_price_for_volume(
                connector_name=connector_name,
                trading_pair=trading_pair,
                volume=query_value,
                is_buy=is_buy,
            )
        elif query_type == "quote_volume_for_price":
            result = await client.market_data.get_quote_volume_for_price(
                connector_name=connector_name,
                trading_pair=trading_pair,
                price=query_value,
                is_buy=is_buy,
            )
        elif query_type == "price_for_quote_volume":
            result = await client.market_data.get_price_for_quote_volume(
                connector_name=connector_name,
                trading_pair=trading_pair,
                quote_volume=query_value,
                is_buy=is_buy,
            )
        else:
            raise ValueError(f"Unsupported query type: {query_type}")

        side_str = "BUY" if is_buy else "SELL"

        return {
            "query_type": query_type,
            "result": result,
            "connector_name": connector_name,
            "trading_pair": trading_pair,
            "query_value": query_value,
            "side": side_str,
        }
