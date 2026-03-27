"""
Trading-related formatters for orders and positions.

This module provides table formatters for trading data including
orders and positions.
"""
from typing import Any

from .base import format_number, get_field, get_timestamp_field
from .table_builder import ColumnDef, TableBuilder


def format_orders_as_table(orders: list[dict[str, Any]]) -> str:
    """
    Format orders as a table string for better LLM processing.

    Columns: time | pair | side | type | amount | price | filled | status

    Args:
        orders: List of order dictionaries

    Returns:
        Formatted table string
    """
    if not orders:
        return "No orders found."

    def format_time(item: dict) -> str:
        return get_timestamp_field(item, "created_at", "creation_timestamp", "timestamp")

    def format_pair(item: dict) -> str:
        return str(get_field(item, "trading_pair", default="N/A"))[:12]

    def format_side(item: dict) -> str:
        return str(get_field(item, "trade_type", "side", default="N/A"))[:4]

    def format_type(item: dict) -> str:
        return str(get_field(item, "order_type", "type", default="N/A"))[:6]

    def format_amount(item: dict) -> str:
        return format_number(get_field(item, "amount", "order_size", default=None), compact=False)

    def format_price(item: dict) -> str:
        return format_number(get_field(item, "price", default=None), compact=False)

    def format_filled(item: dict) -> str:
        return format_number(get_field(item, "filled_amount", "executed_amount_base", default=None), compact=False)

    def format_status(item: dict) -> str:
        return str(get_field(item, "status", default="N/A"))[:8]

    columns = [
        ColumnDef(name="time", key="__time", width=11, formatter=lambda _: ""),
        ColumnDef(name="pair", key="__pair", width=13, formatter=lambda _: ""),
        ColumnDef(name="side", key="__side", width=4, formatter=lambda _: ""),
        ColumnDef(name="type", key="__type", width=6, formatter=lambda _: ""),
        ColumnDef(name="amount", key="__amount", width=8, formatter=lambda _: ""),
        ColumnDef(name="price", key="__price", width=8, formatter=lambda _: ""),
        ColumnDef(name="filled", key="__filled", width=8, formatter=lambda _: ""),
        ColumnDef(name="status", key="__status", width=8, formatter=lambda _: ""),
    ]

    builder = TableBuilder(columns, empty_message="No orders found.")

    # Build header
    header = "time        | pair          | side | type   | amount   | price    | filled   | status"
    separator = "-" * 120

    # Format rows manually for better control
    rows = []
    for order in orders:
        row = (
            f"{format_time(order):11} | "
            f"{format_pair(order):13} | "
            f"{format_side(order):4} | "
            f"{format_type(order):6} | "
            f"{format_amount(order):8} | "
            f"{format_price(order):8} | "
            f"{format_filled(order):8} | "
            f"{format_status(order)}"
        )
        rows.append(row)

    return f"{header}\n{separator}\n" + "\n".join(rows)


def format_positions_as_table(positions: list[dict[str, Any]]) -> str:
    """
    Format positions as a table string for better LLM processing.

    Columns: pair | side | amount | entry_price | current_price | unrealized_pnl | leverage

    Args:
        positions: List of position dictionaries

    Returns:
        Formatted table string
    """
    if not positions:
        return "No positions found."

    def format_pair(item: dict) -> str:
        return str(get_field(item, "trading_pair", default="N/A"))[:12]

    def format_side(item: dict) -> str:
        return str(get_field(item, "position_side", "side", default="N/A"))[:5]

    def format_amount(item: dict) -> str:
        return format_number(get_field(item, "amount", "position_size", default=None), compact=False)

    def format_entry(item: dict) -> str:
        return format_number(get_field(item, "entry_price", default=None), compact=False)

    def format_current(item: dict) -> str:
        return format_number(get_field(item, "current_price", "mark_price", default=None), compact=False)

    def format_pnl(item: dict) -> str:
        return format_number(get_field(item, "unrealized_pnl", default=None), compact=False)

    def format_leverage(item: dict) -> str:
        return str(get_field(item, "leverage", default="N/A"))

    # Build header
    header = "pair          | side  | amount   | entry_price | current_price | unrealized_pnl | leverage"
    separator = "-" * 120

    # Format rows
    rows = []
    for position in positions:
        row = (
            f"{format_pair(position):13} | "
            f"{format_side(position):5} | "
            f"{format_amount(position):8} | "
            f"{format_entry(position):11} | "
            f"{format_current(position):13} | "
            f"{format_pnl(position):14} | "
            f"{format_leverage(position)}"
        )
        rows.append(row)

    return f"{header}\n{separator}\n" + "\n".join(rows)
