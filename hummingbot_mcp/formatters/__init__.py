"""
Formatters package for the Hummingbot MCP server.

This package provides table formatters for various data types including
trading data, market data, bot information, and portfolio balances.

Key components:
- base.py: Common formatting utilities and field accessor helpers
- table_builder.py: Generic TableBuilder class for consistent table creation
- Individual formatters for specific data types
"""

# Base utilities (commonly used across formatters)
from .base import (
    format_currency,
    format_number,
    format_percentage,
    format_table_separator,
    format_time_only,
    format_timestamp,
    get_field,
    get_formatted_currency,
    get_formatted_number,
    get_formatted_percentage,
    get_timestamp_field,
    get_truncated,
    truncate_address,
    truncate_string,
)

# Table builder for creating consistent tables
from .table_builder import ColumnDef, TableBuilder, create_simple_table

# Account formatters
from .account import format_connector_result

# Bot formatters
from .bots import format_active_bots_as_table, format_bot_logs_as_table

# Gateway formatters
from .gateway import (
    format_gateway_clmm_pool_result,
    format_gateway_config_result,
    format_gateway_container_result,
    format_gateway_swap_result,
)

# Executor formatters
from .executors import (
    format_executor_detail,
    format_executor_schema_table,
    format_executor_summary,
    format_executor_types_table,
    format_executors_table,
    format_positions_held_table,
    format_positions_summary,
)

# Market data formatters
from .market_data import (
    format_candles_as_table,
    format_order_book_as_table,
    format_prices_as_table,
)

# Portfolio formatters
from .portfolio import format_portfolio_as_table

# Trading formatters
from .trading import format_orders_as_table, format_positions_as_table

__all__ = [
    # Account formatters
    "format_connector_result",
    # Gateway formatters
    "format_gateway_container_result",
    "format_gateway_config_result",
    "format_gateway_swap_result",
    "format_gateway_clmm_pool_result",
    # Base utilities
    "format_currency",
    "format_number",
    "format_percentage",
    "format_table_separator",
    "format_time_only",
    "format_timestamp",
    "get_field",
    "get_formatted_currency",
    "get_formatted_number",
    "get_formatted_percentage",
    "get_timestamp_field",
    "get_truncated",
    "truncate_address",
    "truncate_string",
    # Table builder
    "ColumnDef",
    "TableBuilder",
    "create_simple_table",
    # Trading formatters
    "format_orders_as_table",
    "format_positions_as_table",
    # Market data formatters
    "format_prices_as_table",
    "format_candles_as_table",
    "format_order_book_as_table",
    # Bot formatters
    "format_bot_logs_as_table",
    "format_active_bots_as_table",
    # Portfolio formatters
    "format_portfolio_as_table",
    # Executor formatters
    "format_executor_types_table",
    "format_executors_table",
    "format_executor_detail",
    "format_positions_held_table",
    "format_positions_summary",
    "format_executor_schema_table",
    "format_executor_summary",
]
