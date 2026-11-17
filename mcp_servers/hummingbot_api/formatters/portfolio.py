"""
Portfolio formatter for balance and holdings information.

This module provides table formatters for portfolio balances and holdings.
"""

from typing import Any

from .base import format_exact, format_usd, get_field
from .table_builder import ColumnDef, TableBuilder, render_table

# Default number of LP position rows rendered in the CLMM table. The fetch is
# not bounded per venue (get_positions_owned returns every position a venue
# holds), so the table is capped to keep an LP-heavy account from pushing
# hundreds of rows into the model's context. Callers that want more can raise
# it; the "... and N more" footer always reports the true remainder.
DEFAULT_LP_POSITIONS_LIMIT = 50


BALANCE_COLUMNS = ["account", "connector", "token", "total", "available", "value_usd"]


def format_portfolio_as_table(portfolio_data: dict[str, Any]) -> str:
    """
    Format portfolio balances as a table string for better LLM processing.

    Columns: account | connector | token | total | available | value_usd

    Units are exact (see ``format_exact``) and the connector name is whole: a
    balance is what an order is sized against, and ``12.3457M`` BONK on
    ``hyperliquid_perpe`` is neither a size nor a venue.

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

    # Flatten nested structure: account -> connector -> balances
    rows = []
    for account_name, connectors in portfolio_data.items():
        if not isinstance(connectors, dict):
            continue

        for connector_name, balances in connectors.items():
            if not isinstance(balances, list):
                continue

            for balance in balances:
                rows.append(
                    [
                        account_name,
                        connector_name,
                        get_field(balance, "token", default=None),
                        format_exact(get_field(balance, "units", default=None)),
                        format_exact(
                            get_field(balance, "available_units", default=None)
                        ),
                        format_usd(get_field(balance, "value", default=None)),
                    ]
                )

    if not rows:
        return "No portfolio balances found."

    return render_table(BALANCE_COLUMNS, rows)


def _format_lp_price(value: Any) -> str:
    """
    Format an LP position price bound.

    Malformed values raise ValueError/TypeError, which ColumnDef turns into the
    column default ("N/A") instead of silently rendering the raw value.
    """
    if value is None or value == "N/A":
        return "N/A"
    float(value)  # a malformed bound is "N/A", not echoed
    return format_exact(value)


LP_POSITION_COLUMNS = [
    ColumnDef(name="connector", key="connector", width=10),
    ColumnDef(name="trading_pair", key="trading_pair", width=15),
    ColumnDef(
        name="lower_price", key="lower_price", width=11, formatter=_format_lp_price
    ),
    ColumnDef(
        name="upper_price", key="upper_price", width=11, formatter=_format_lp_price
    ),
    # Both addresses whole: `manage_clmm(action="close")` takes the two of them,
    # and the orphan listing sends the caller to this table to find them.
    ColumnDef(name="network", key="network", width=19),
    ColumnDef(name="position_address", key="position_address", width=44),
    ColumnDef(name="pool_address", key=["pool_address", "poolAddress"], width=44),
]


def format_lp_positions_table(
    positions: list[dict[str, Any]], limit: int = DEFAULT_LP_POSITIONS_LIMIT
) -> str:
    """
    Format open CLMM (LP) positions as a table string.

    Columns: connector | trading_pair | lower_price | upper_price | network |
    position_address | pool_address

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
