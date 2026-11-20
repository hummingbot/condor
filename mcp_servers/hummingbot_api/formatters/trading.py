"""
Trading-related formatters for orders and positions.

This module provides table formatters for trading data including
orders and positions.
"""

from typing import Any

from .base import format_exact, format_usd, get_field, get_timestamp_field
from .table_builder import render_table

ORDER_COLUMNS = [
    "time",
    "account",
    "connector",
    "pair",
    "side",
    "type",
    "amount",
    "price",
    "filled",
    "status",
    "order_id",
]

POSITION_COLUMNS = [
    "account",
    "connector",
    "pair",
    "side",
    "amount",
    "entry_price",
    "current_price",
    "unrealized_pnl",
    "leverage",
]


def format_orders_as_table(orders: list[dict[str, Any]]) -> str:
    """
    Format orders as a table string for better LLM processing.

    Columns: time | account | connector | pair | side | type | amount | price |
    filled | status | order_id

    Every identifier is whole and every number is the number the venue holds
    (see ``format_exact``): a model cancels, resizes and reconciles from this
    table, and ``LIMIT_`` / ``PARTIALL`` / an amount of ``0.01`` for ``0.015``
    are not things it can act on.

    Args:
        orders: List of order dictionaries

    Returns:
        Formatted table string
    """
    if not orders:
        return "No orders found."

    rows = [
        [
            get_timestamp_field(order, "created_at", "creation_timestamp", "timestamp"),
            get_field(order, "account_name", "account", default=None),
            get_field(order, "connector_name", "connector", default=None),
            get_field(order, "trading_pair", default=None),
            get_field(order, "trade_type", "side", default=None),
            get_field(order, "order_type", "type", default=None),
            format_exact(get_field(order, "amount", "order_size", default=None)),
            format_exact(get_field(order, "price", default=None)),
            format_exact(
                get_field(order, "filled_amount", "executed_amount_base", default=None)
            ),
            get_field(order, "status", default=None),
            get_field(order, "client_order_id", "order_id", "id", default=None),
        ]
        for order in orders
    ]
    return render_table(ORDER_COLUMNS, rows)


def format_positions_as_table(positions: list[dict[str, Any]]) -> str:
    """
    Format positions as a table string for better LLM processing.

    Columns: account | connector | pair | side | amount | entry_price |
    current_price | unrealized_pnl | leverage

    ``connector`` and ``account`` lead: with two perpetual venues connected, a
    row that names neither cannot be closed on the right one.

    Args:
        positions: List of position dictionaries

    Returns:
        Formatted table string
    """
    if not positions:
        return "No positions found."

    rows = [
        [
            get_field(position, "account_name", "account", default=None),
            get_field(position, "connector_name", "connector", default=None),
            get_field(position, "trading_pair", default=None),
            get_field(position, "position_side", "side", default=None),
            format_exact(get_field(position, "amount", "position_size", default=None)),
            format_exact(get_field(position, "entry_price", default=None)),
            format_exact(
                get_field(position, "current_price", "mark_price", default=None)
            ),
            format_usd(get_field(position, "unrealized_pnl", default=None)),
            get_field(position, "leverage", default=None),
        ]
        for position in positions
    ]
    return render_table(POSITION_COLUMNS, rows)
