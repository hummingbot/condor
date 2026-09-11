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
    get_timestamp_field,
    truncate_address,
    truncate_string,
)

# Bot formatters
from .bots import (
    format_active_bots_as_table,
    format_bot_logs_as_table,
    format_controller_state,
)

# Executor formatters
from .executors import (
    format_executor_detail,
    format_executor_summary,
    format_executor_types_table,
    format_executors_table,
    format_positions_held_table,
    format_positions_summary,
)

# Gateway formatters
from .gateway import (
    format_amm_result,
    format_clmm_result,
    format_gateway_clmm_pool_result,
    format_gateway_config_result,
    format_gateway_swap_result,
)

# Market data formatters
from .market_data import (
    format_prices_as_table,
)

# Portfolio formatters
from .portfolio import format_lp_positions_table, format_portfolio_as_table

# Table builder for creating consistent tables
from .table_builder import ColumnDef, TableBuilder

# Trading formatters
from .trading import format_orders_as_table, format_positions_as_table

__all__ = [
    # Gateway formatters
    "format_gateway_config_result",
    "format_gateway_swap_result",
    "format_gateway_clmm_pool_result",
    "format_amm_result",
    "format_clmm_result",
    # Base utilities
    "format_currency",
    "format_number",
    "format_percentage",
    "format_table_separator",
    "format_time_only",
    "format_timestamp",
    "get_field",
    "get_timestamp_field",
    "truncate_address",
    "truncate_string",
    # Table builder
    "ColumnDef",
    "TableBuilder",
    # Trading formatters
    "format_orders_as_table",
    "format_positions_as_table",
    # Market data formatters
    "format_prices_as_table",
    # Bot formatters
    "format_bot_logs_as_table",
    "format_active_bots_as_table",
    "format_controller_state",
    # Portfolio formatters
    "format_portfolio_as_table",
    "format_lp_positions_table",
    # Executor formatters
    "format_executor_types_table",
    "format_executors_table",
    "format_executor_detail",
    "format_positions_held_table",
    "format_positions_summary",
    "format_executor_summary",
]
