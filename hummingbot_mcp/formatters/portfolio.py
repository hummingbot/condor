"""
Portfolio formatter for balance and holdings information.

This module provides table formatters for portfolio balances and holdings.
"""
from typing import Any

from .base import format_number, format_table_separator, get_field


def format_portfolio_as_table(portfolio_data: dict[str, Any]) -> str:
    """
    Format portfolio balances as a table string for better LLM processing.

    Columns: token | connector | total | available | value_usd

    Portfolio structure:
    {
      "account_name": {
        "connector_name": [
          {"token": "BTC", "units": 0.5, "available_units": 0.5, "value": 50000}
        ]
      }
    }

    Args:
        portfolio_data: Nested dictionary of portfolio data

    Returns:
        Formatted table string
    """
    if not portfolio_data:
        return "No portfolio data found."

    # Header
    header = "token    | connector         | total        | available    | value_usd"
    separator = format_table_separator(100)

    # Flatten nested structure: account -> connector -> balances
    rows = []
    for account_name, connectors in portfolio_data.items():
        if not isinstance(connectors, dict):
            continue

        for connector_name, balances in connectors.items():
            if not isinstance(balances, list):
                continue

            for balance in balances:
                token = str(get_field(balance, "token", default="N/A"))[:8]
                connector = connector_name[:17]
                total = format_number(get_field(balance, "units", default=None), decimals=4, compact=True)
                available = format_number(get_field(balance, "available_units", default=None), decimals=4, compact=True)
                value_usd = format_number(get_field(balance, "value", default=None), decimals=2, compact=True)

                row = f"{token:8} | {connector:17} | {total:12} | {available:12} | {value_usd}"
                rows.append(row)

    if not rows:
        return "No portfolio balances found."

    return f"{header}\n{separator}\n" + "\n".join(rows)
