"""
Executor-related formatters for table display.

This module provides table formatters for executor types, executor lists,
positions held, and executor configuration schemas.
"""

from typing import Any

from .base import (
    format_currency,
    format_exact,
    format_percentage,
    format_table_separator,
    format_timestamp,
    format_usd,
    get_field,
    truncate_string,
)
from .table_builder import render_table


def format_executor_types_table(executor_types: list[dict[str, Any]]) -> str:
    """
    Format executor types with descriptions as a table.

    Columns: type | description | use_when | avoid_when

    Args:
        executor_types: List of executor type dictionaries with keys:
            - name: Executor type name
            - description: Brief description
            - use_when: When to use this executor
            - avoid_when: When to avoid this executor

    Returns:
        Formatted table string
    """
    if not executor_types:
        return "No executor types available."

    # Header
    header = "executor_type        | description                              | use_when                                 | avoid_when"
    separator = format_table_separator(140)

    # Format each executor type as a row
    rows = []
    for exec_type in executor_types:
        name = str(get_field(exec_type, "name", default="unknown"))[:20]
        description = truncate_string(
            str(get_field(exec_type, "description", default="")), max_len=40
        )
        use_when = truncate_string(
            str(get_field(exec_type, "use_when", default="")), max_len=40
        )
        avoid_when = truncate_string(
            str(get_field(exec_type, "avoid_when", default="")), max_len=40
        )

        row = f"{name:20} | {description:40} | {use_when:40} | {avoid_when}"
        rows.append(row)

    return f"{header}\n{separator}\n" + "\n".join(rows)


EXECUTOR_COLUMNS = [
    "id",
    "type",
    "connector",
    "pair",
    "status",
    "close_type",
    "side",
    "controller_id",
    "account",
    "volume",
    "pnl",
    "created",
]


def format_executors_table(executors: list[dict[str, Any]]) -> str:
    """
    Format a list of executors as a table.

    Columns: id | type | connector | pair | status | close_type | side |
    controller_id | account | volume | pnl | created

    ``controller_id`` is the owner the list tool's docstring promises ("under
    which controller"), and the only way a session tells its own executors from
    another agent's.

    Args:
        executors: List of executor dictionaries

    Returns:
        Formatted table string
    """
    if not executors:
        return "No executors found."

    rows = []
    for executor in executors:
        config = executor.get("config")
        config = config if isinstance(config, dict) else {}
        custom_info = executor.get("custom_info")
        custom_info = custom_info if isinstance(custom_info, dict) else {}

        # Get side from top level or from custom_info
        side = get_field(executor, "side", default=None) or custom_info.get("side")

        rows.append(
            [
                get_field(executor, "id", "executor_id", default=None),
                get_field(executor, "type", "executor_type", default="unknown"),
                get_field(executor, "connector_name", default=None)
                or config.get("connector_name"),
                get_field(executor, "trading_pair", default=None)
                or config.get("trading_pair"),
                get_field(executor, "status", default="unknown"),
                get_field(executor, "close_type", default=None),
                side,
                get_field(executor, "controller_id", default=None)
                or config.get("controller_id"),
                get_field(executor, "account_name", "account", default=None),
                # Volume is filled_amount_quote
                format_usd(get_field(executor, "filled_amount_quote", default=None)),
                format_usd(get_field(executor, "net_pnl_quote", "pnl", default=None)),
                format_timestamp(
                    get_field(executor, "timestamp", "created_at", default=0)
                ),
            ]
        )

    return render_table(EXECUTOR_COLUMNS, rows)


#: A list this long or shorter is printed whole in an executor's detail. A DCA
#: ladder's ``prices`` and ``amounts_quote`` ARE the executor; "[3 items]" told
#: the reader nothing about what it had deployed.
INLINE_LIST_MAX_ITEMS = 20


def _format_list(values: list[Any]) -> str:
    """A list value for the detail view: whole when short, else head and count."""

    def render(value: Any) -> str:
        return (
            format_exact(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else str(value)
        )

    if len(values) <= INLINE_LIST_MAX_ITEMS:
        return "[" + ", ".join(render(value) for value in values) + "]"
    head = ", ".join(render(value) for value in values[:INLINE_LIST_MAX_ITEMS])
    return (
        f"[{head}, ... {len(values) - INLINE_LIST_MAX_ITEMS} more "
        f"({len(values)} items)]"
    )


def format_executor_detail(executor: dict[str, Any]) -> str:
    """
    Format a single executor's details in a readable format.

    Args:
        executor: Executor dictionary

    Returns:
        Formatted executor detail string
    """
    if not executor:
        return "No executor data."

    output = "Executor Details:\n"
    output += format_table_separator(60) + "\n"

    # Basic info
    output += f"ID: {get_field(executor, 'id', 'executor_id', default='N/A')}\n"
    output += f"Type: {get_field(executor, 'type', 'executor_type', default='N/A')}\n"
    output += f"Status: {get_field(executor, 'status', default='N/A')}\n"

    close_type = get_field(executor, "close_type", default=None)
    if close_type:
        output += f"Close Type: {close_type}\n"

    output += f"Connector: {get_field(executor, 'connector_name', default='N/A')}\n"
    output += f"Trading Pair: {get_field(executor, 'trading_pair', default='N/A')}\n"

    # Get side from top level or custom_info
    side = get_field(executor, "side", default=None)
    custom_info = executor.get("custom_info", {}) or {}
    if not side:
        side = custom_info.get("side", "N/A")
    output += f"Side: {side}\n"

    output += "\n"

    # Volume info
    volume = get_field(executor, "filled_amount_quote", default=None)
    if volume is not None:
        output += f"Volume Traded: {format_currency(volume)}\n"

    # Position info from custom_info
    if custom_info:
        position_size = custom_info.get("position_size_quote")
        if position_size is not None:
            output += f"Position Size: {format_currency(position_size)}\n"

        break_even = custom_info.get("break_even_price")
        if break_even is not None:
            output += f"Break-even Price: {format_exact(break_even)}\n"

    entry_price = get_field(executor, "entry_price", default=None)
    if entry_price is not None and entry_price != "N/A":
        output += f"Entry Price: {format_exact(entry_price)}\n"

    current_price = get_field(executor, "current_price", default=None)
    if current_price is not None and current_price != "N/A":
        output += f"Current Price: {format_exact(current_price)}\n"

    output += "\n"

    # PnL info
    net_pnl = get_field(executor, "net_pnl_quote", default=None)
    if net_pnl is not None and net_pnl != "N/A":
        output += f"Net PnL (Quote): {format_currency(net_pnl)}\n"

    net_pnl_pct = get_field(executor, "net_pnl_pct", default=None)
    if net_pnl_pct is not None and net_pnl_pct != "N/A":
        output += f"Net PnL (%): {format_percentage(net_pnl_pct)}\n"

    # Realized/Unrealized breakdown from custom_info
    if custom_info:
        realized_pnl = custom_info.get("realized_pnl_quote")
        if realized_pnl is not None:
            output += f"Realized PnL: {format_currency(realized_pnl)}\n"

        position_pnl = custom_info.get("position_pnl_quote")
        if position_pnl is not None:
            output += f"Unrealized PnL: {format_currency(position_pnl)}\n"

        realized_buy = custom_info.get("realized_buy_size_quote")
        realized_sell = custom_info.get("realized_sell_size_quote")
        if realized_buy is not None and realized_sell is not None:
            output += f"Buy Volume: {format_currency(realized_buy)} | Sell Volume: {format_currency(realized_sell)}\n"

    cum_fees = get_field(executor, "cum_fees_quote", default=None)
    if cum_fees is not None and cum_fees != "N/A":
        output += f"Cumulative Fees: {format_currency(cum_fees)}\n"

    output += "\n"

    # Timestamps
    created = get_field(executor, "timestamp", "created_at", default=None)
    if created is not None and created != "N/A" and created != 0:
        output += f"Created: {format_timestamp(created, '%Y-%m-%d %H:%M:%S')}\n"

    close_timestamp = get_field(executor, "close_timestamp", default=None)
    if (
        close_timestamp is not None
        and close_timestamp != "N/A"
        and close_timestamp != 0
    ):
        output += f"Closed: {format_timestamp(close_timestamp, '%Y-%m-%d %H:%M:%S')}\n"

    # Show full config if present (creation parameters)
    config = executor.get("config")
    if config and isinstance(config, dict):
        output += "\nConfig:\n"
        for key, value in config.items():
            if isinstance(value, list):
                output += f"  {key}: {_format_list(value)}\n"
            else:
                output += f"  {key}: {value}\n"

    # Extract grid config from custom_info level prices
    if custom_info:
        grid_levels = custom_info.get("grid_level_prices")
        grid_tp = custom_info.get("grid_tp_prices")
        if isinstance(grid_levels, list) and len(grid_levels) > 1:
            output += "\nGrid Config (derived from level prices):\n"
            output += f"  start_price: {format_exact(min(grid_levels))}\n"
            output += f"  end_price: {format_exact(max(grid_levels))}\n"
            output += f"  num_levels: {len(grid_levels)}\n"
            sorted_levels = sorted(grid_levels)
            spreads = [
                (sorted_levels[i + 1] - sorted_levels[i]) / sorted_levels[i]
                for i in range(len(sorted_levels) - 1)
            ]
            avg_spread = sum(spreads) / len(spreads) if spreads else 0
            output += f"  avg_spread_between_levels: {format_percentage(avg_spread)}\n"
            if isinstance(grid_tp, list) and len(grid_tp) > 1:
                tp_diffs = [
                    abs(grid_tp[i] - grid_levels[i]) / grid_levels[i]
                    for i in range(min(len(grid_tp), len(grid_levels)))
                ]
                avg_tp = sum(tp_diffs) / len(tp_diffs) if tp_diffs else 0
                output += f"  avg_take_profit: {format_percentage(avg_tp)}\n"

        output += "\nCustom Info:\n"
        for key, value in custom_info.items():
            if isinstance(value, (list, dict)):
                if isinstance(value, list):
                    output += f"  {key}: {_format_list(value)}\n"
                else:
                    output += f"  {key}: {{...}} ({len(value)} keys)\n"
            else:
                output += f"  {key}: {value}\n"

    return output


POSITIONS_HELD_COLUMNS = [
    "connector",
    "trading_pair",
    "side",
    "amount",
    "notional",
    "entry_price",
    "current_price",
    "unrealized_pnl",
    "leverage",
    "controller_id",
    "account",
]


def format_positions_held_table(positions: list[dict[str, Any]]) -> str:
    """
    Format positions held by executors as a table.

    Columns: connector | trading_pair | side | amount | notional | entry_price |
    current_price | unrealized_pnl | leverage | controller_id | account

    Args:
        positions: List of position dictionaries

    Returns:
        Formatted table string
    """
    if not positions:
        return "No positions held."

    rows = []
    for position in positions:
        # Handle both 'amount' and 'net_amount_base' field names
        amount_val = get_field(position, "net_amount_base", "amount", default=None)
        # Handle both 'entry_price' and 'buy_breakeven_price' field names
        entry_price_val = get_field(
            position, "buy_breakeven_price", "entry_price", default=None
        )
        # Compute notional value (amount * entry_price)
        notional_val = None
        if amount_val is not None and entry_price_val is not None:
            try:
                notional_val = abs(float(amount_val)) * float(entry_price_val)
            except (ValueError, TypeError):
                pass

        rows.append(
            [
                get_field(position, "connector_name", default=None),
                get_field(position, "trading_pair", default=None),
                # Handle both 'side' and 'position_side' field names
                get_field(position, "position_side", "side", default=None),
                format_exact(amount_val),
                format_usd(notional_val),
                format_exact(entry_price_val),
                format_exact(get_field(position, "current_price", default=None)),
                format_usd(
                    get_field(
                        position, "unrealized_pnl_quote", "unrealized_pnl", default=None
                    )
                ),
                get_field(position, "leverage", default="1"),
                get_field(position, "controller_id", default=None),
                get_field(position, "account_name", "account", default=None),
            ]
        )

    return render_table(POSITIONS_HELD_COLUMNS, rows)


def format_positions_summary(summary: dict[str, Any]) -> str:
    """
    Format a positions summary in a readable format.

    Args:
        summary: Summary dictionary with aggregated position data

    Returns:
        Formatted summary string
    """
    if not summary:
        return "No position summary available."

    output = "Positions Summary:\n"
    output += format_table_separator(50) + "\n"

    output += f"Total Positions: {get_field(summary, 'total_positions', default=0)}\n"

    total_value = get_field(summary, "total_value", default=None)
    if total_value is not None and total_value != "N/A":
        output += f"Total Value: {format_currency(total_value)}\n"

    # Handle both 'total_unrealized_pnl' and 'total_realized_pnl' field names
    total_realized = get_field(summary, "total_realized_pnl", default=None)
    if total_realized is not None and total_realized != "N/A":
        output += f"Total Realized PnL: {format_currency(total_realized)}\n"

    total_unrealized = get_field(summary, "total_unrealized_pnl", default=None)
    if total_unrealized is not None and total_unrealized != "N/A":
        output += f"Total Unrealized PnL: {format_currency(total_unrealized)}\n"

    # Breakdown by connector if available
    by_connector = summary.get("by_connector", {})
    if by_connector:
        output += "\nBy Connector:\n"
        for connector, data in by_connector.items():
            count = get_field(data, "count", default=0)
            value = format_currency(get_field(data, "value", default=0))
            output += f"  - {connector}: {count} positions, {value}\n"

    return output


def format_executor_summary(summary: dict[str, Any]) -> str:
    """
    Format an overall executor summary.

    Args:
        summary: Summary dictionary with executor statistics

    Returns:
        Formatted summary string
    """
    if not summary:
        return "No executor summary available."

    output = "Executor Summary:\n"
    output += format_table_separator(60) + "\n"

    # Compute totals from by_type if not directly provided
    by_type = summary.get("by_type", {})
    by_status = summary.get("by_status", {})

    # Calculate total from by_type (sum of all executor types)
    total = get_field(summary, "total", default=None)
    if total is None and by_type:
        total = sum(by_type.values())
    elif total is None:
        total = 0

    # Calculate active/completed/failed from by_status if not directly provided
    active = get_field(summary, "active", default=None)
    if active is None:
        active = by_status.get("RUNNING", 0)

    completed = get_field(summary, "completed", default=None)
    if completed is None:
        completed = by_status.get("COMPLETED", 0)

    failed = get_field(summary, "failed", default=None)
    if failed is None:
        failed = by_status.get("FAILED", 0)

    # Overall stats
    output += f"Total Executors: {total}\n"
    output += f"Active Executors: {active}\n"
    output += f"Completed Executors: {completed}\n"
    output += f"Failed Executors: {failed}\n"

    output += "\n"

    # PnL stats
    total_pnl = get_field(summary, "total_pnl", default=None)
    if total_pnl is not None and total_pnl != "N/A":
        output += f"Total PnL: {format_currency(total_pnl)}\n"

    total_volume = get_field(summary, "total_volume", default=None)
    if total_volume is not None and total_volume != "N/A":
        output += f"Total Volume: {format_currency(total_volume)}\n"

    # By type breakdown
    by_type = summary.get("by_type", {})
    if by_type:
        output += "\nBy Type:\n"
        for exec_type, count in by_type.items():
            output += f"  - {exec_type}: {count}\n"

    # By status breakdown
    by_status = summary.get("by_status", {})
    if by_status:
        output += "\nBy Status:\n"
        for status, count in by_status.items():
            output += f"  - {status}: {count}\n"

    return output
