"""
Condor Fetchers — Pure data-fetching functions for Hummingbot API.

This package contains all functions that fetch data from the Hummingbot
Backend API. They are the ONLY layer that talks to the API client directly.

Consumers:
    - condor.server_data_service (polling / subscription cache)
    - condor.web.routes (REST endpoints)
    - handlers/ (Telegram bot handlers)
    - condor.agents (engine, performance, risk)
    - agent routines (agents/*/routines/)

Rules:
    - Functions receive an API *client* and return data. No UI.
    - No per-caller result caching — that belongs to condor.server_data_service.
      One sanctioned exception: condor.fetchers.bot_performance keeps a short-TTL,
      in-flight-coalescing cache of the whole-server controller-performance
      snapshot, because that call returns the same payload for every caller and
      the agents rollup fans out N of them at once (see the rationale comment
      above ``_SNAPSHOT_TTL``). The aggregate ``fetch_all_bot_performance``
      returns is therefore shared between callers and must be treated as
      read-only.
    - No imports from handlers/ or condor.web/ (prevents circular deps).
    - Keep thin: call client method, light transform, return.

Importing:
    Deep module imports are the convention — ``from condor.fetchers.bot_performance
    import fetch_all_bot_performance``. Every consumer except one does that, and
    modules with no re-export below (``bot_performance``, ``bots.build_bots_page`` /
    ``extract_bots_list``, ``portfolio.fetch_portfolio_refreshed`` /
    ``fetch_cex_balances``) are reached that way only. The names re-exported here
    exist for condor.server_data_service.register_default_fetches(), the sole
    façade consumer; they are not the package's public surface.
"""

from condor.fetchers.portfolio import fetch_portfolio
from condor.fetchers.positions import fetch_positions
from condor.fetchers.orders import fetch_active_orders
from condor.fetchers.trading_rules import fetch_trading_rules
from condor.fetchers.connectors import (
    fetch_connectors,
    fetch_available_cex_connectors,
    fetch_gateway_networks,
    is_cex_connector,
)
from condor.fetchers.executors import (
    fetch_executors,
    fetch_all_executors,
    create_executor,
    stop_executor,
    describe_executor_error,
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
    fetch_rates,
    fetch_ticker_pool,
    fetch_tickers,
)
from condor.fetchers.server_status import fetch_server_status

__all__ = [
    "fetch_portfolio",
    "fetch_positions",
    "fetch_active_orders",
    "fetch_trading_rules",
    "fetch_connectors",
    "fetch_available_cex_connectors",
    "fetch_gateway_networks",
    "is_cex_connector",
    "fetch_executors",
    "fetch_all_executors",
    "create_executor",
    "stop_executor",
    "describe_executor_error",
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
    "fetch_rates",
    "fetch_ticker_pool",
    "fetch_tickers",
    "fetch_server_status",
]
