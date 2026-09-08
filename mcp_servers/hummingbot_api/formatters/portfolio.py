"""
Portfolio formatter for balance and holdings information.

This module provides table formatters for portfolio balances and holdings.
"""

from typing import Any

from .base import format_number, format_table_separator, get_field, truncate_address
from .table_builder import ColumnDef, TableBuilder

# Default number of LP position rows rendered in the CLMM table. The fetch is
# not bounded per venue (get_positions_owned returns every position a venue
# holds), so the table is capped to keep an LP-heavy account from pushing
# hundreds of rows into the model's context. Callers that want more can raise
# it; the "... and N more" footer always reports the true remainder.
DEFAULT_LP_POSITIONS_LIMIT = 50


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
                total = format_number(
                    get_field(balance, "units", default=None), decimals=4, compact=True
                )
                available = format_number(
                    get_field(balance, "available_units", default=None),
                    decimals=4,
                    compact=True,
                )
                value_usd = format_number(
                    get_field(balance, "value", default=None), decimals=2, compact=True
                )

                row = f"{token:8} | {connector:17} | {total:12} | {available:12} | {value_usd}"
                rows.append(row)

    if not rows:
        return "No portfolio balances found."

    return f"{header}\n{separator}\n" + "\n".join(rows)


def _format_lp_price(value: Any) -> str:
    """
    Format an LP position price bound.

    Malformed values raise ValueError/TypeError, which ColumnDef turns into the
    column default ("N/A") instead of silently rendering the raw value.
    """
    if value is None or value == "N/A":
        return "N/A"
    return f"{float(value):.4f}"


LP_POSITION_COLUMNS = [
    ColumnDef(name="connector", key="connector", width=10),
    ColumnDef(name="trading_pair", key="trading_pair", width=15),
    ColumnDef(
        name="lower_price", key="lower_price", width=11, formatter=_format_lp_price
    ),
    ColumnDef(
        name="upper_price", key="upper_price", width=11, formatter=_format_lp_price
    ),
    ColumnDef(
        name="position_address",
        key="position_address",
        width=17,
        formatter=lambda address: truncate_address(str(address)),
    ),
]


def format_lp_positions_table(
    positions: list[dict[str, Any]], limit: int = DEFAULT_LP_POSITIONS_LIMIT
) -> str:
    """
    Format open CLMM (LP) positions as a table string.

    Columns: connector | trading_pair | lower_price | upper_price | position_address

    Args:
        positions: List of open LP position dictionaries
        limit: Maximum number of rows to render (default:
            DEFAULT_LP_POSITIONS_LIMIT). Positions beyond the limit are reported
            by a "... and N more open positions" footer.

    Returns:
        Formatted table string
    """
    if not positions:
        return "No active LP positions found"

    builder = TableBuilder(LP_POSITION_COLUMNS)
    table = builder.build_with_title(positions[:limit], "Status: OPEN positions")

    remaining = len(positions) - limit
    if remaining > 0:
        table += f"\n... and {remaining} more open positions"

    return table
