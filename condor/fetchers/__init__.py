"""
Condor Fetchers — Pure data-fetching functions for Hummingbot API.

This package contains all functions that fetch data from the Hummingbot
Backend API. They are the ONLY layer that talks to the API client directly.

Consumers:
    - condor.server_data_service (polling / subscription cache)
    - condor.web.routes (REST endpoints)
    - handlers/ (Telegram bot handlers)

Rules:
    - Functions receive an API *client* and return data. No caching, no UI.
    - No imports from handlers/ or condor.web/ (prevents circular deps).
    - Keep thin: call client method, light transform, return.
"""

from condor.fetchers.portfolio import fetch_portfolio
from condor.fetchers.prices import fetch_prices
from condor.fetchers.positions import fetch_positions
from condor.fetchers.orders import fetch_active_orders
from condor.fetchers.trading_rules import fetch_trading_rules
from condor.fetchers.connectors import (
    fetch_connectors,
    fetch_available_cex_connectors,
    is_cex_connector,
)
from condor.fetchers.executors import (
    fetch_executors,
    fetch_all_executors,
    create_executor,
    stop_executor,
    get_executor_detail,
    get_executor_type,
    get_executor_pnl,
    get_executor_volume,
    get_executor_fees,
    extract_executors_list,
)
from condor.fetchers.bots import fetch_bots_status, fetch_bot_runs
from condor.fetchers.market_data import (
    fetch_current_price,
    fetch_candles,
    fetch_candle_connectors,
)
from condor.fetchers.server_status import fetch_server_status

__all__ = [
    "fetch_portfolio",
    "fetch_prices",
    "fetch_positions",
    "fetch_active_orders",
    "fetch_trading_rules",
    "fetch_connectors",
    "fetch_available_cex_connectors",
    "is_cex_connector",
    "fetch_executors",
    "fetch_all_executors",
    "create_executor",
    "stop_executor",
    "get_executor_detail",
    "get_executor_type",
    "get_executor_pnl",
    "get_executor_volume",
    "get_executor_fees",
    "extract_executors_list",
    "fetch_bots_status",
    "fetch_bot_runs",
    "fetch_current_price",
    "fetch_candles",
    "fetch_candle_connectors",
    "fetch_server_status",
]
