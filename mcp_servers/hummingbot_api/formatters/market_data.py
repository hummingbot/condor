"""
Market data formatters.

Prices only: the candle and order book tables that used to live here rendered
data no tool returns any more (ARCH-308) — both are read as structured rows
through ``client.market_data.*`` inside ``run_code``.
"""

from typing import Any

from .base import format_currency, format_table_separator


def format_prices_as_table(prices_data: dict[str, Any]) -> str:
    """
    Format prices data as a table string for better LLM processing.

    Columns: trading_pair | price

    Args:
        prices_data: Dictionary containing prices keyed by trading pair

    Returns:
        Formatted table string
    """
    prices = prices_data.get("prices", {})

    if not prices:
        return "No prices available."

    # Header
    header = "trading_pair      | price"
    separator = format_table_separator(50)

    # Format each price as a row
    rows = []
    for pair, price in prices.items():
        pair_str = pair[:16].ljust(16)
        price_str = format_currency(price, decimals=2 if price >= 1 else 6)
        row = f"{pair_str}  | {price_str}"
        rows.append(row)

    return f"{header}\n{separator}\n" + "\n".join(rows)
