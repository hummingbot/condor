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
    - No *per-caller* result caching — that belongs to condor.server_data_service.
      A fetcher may keep a **process-wide** cache only when the answer is the same
      for every caller: the upstream call is whole-server (so its payload does not
      depend on who asks) or its subject is immutable. Every such cache must be
      keyed so one server's — or one Gateway network's — answer can never be
      served for another, carry a comment saying why the caller cannot hold it
      instead, and be listed here. There are six today:

        * ``bot_performance._snapshot_cache`` — whole-server controller
          performance, ``_SNAPSHOT_TTL`` 5s, in-flight coalesced,
          ``clear_snapshot_cache()``.
        * ``bot_performance._archived_cache`` — the archived-database listing,
          ``_ARCHIVED_TTL`` 60s, ``clear_archived_cache()``.
        * ``bot_performance._history_cache`` — per-instance history pages,
          ``_HISTORY_TTL`` 20s, LRU-capped at ``_HISTORY_CACHE_MAX`` (256),
          in-flight coalesced, ``clear_history_cache()``.
        * ``archived_run._performance_cache`` — whole archived runs. No TTL: an
          archived sqlite file is immutable. LRU-capped at
          ``_PERFORMANCE_CACHE_MAX`` (32) because each entry is large; tests
          clear it directly, there is no ``clear_*`` hook.
        * ``run_history._CLASS_CACHE`` — a terminated controller's class, held
          for the process lifetime (a stored config is immutable, and one that
          is gone does not come back), ``clear_controller_class_cache()``.
        * ``gateway_tokens._listed`` — addresses confirmed present on a Gateway
          token list. Confirmations only, never failures; flushed wholesale past
          ``_MAX_MEMO`` (4000). ``reset_listed_memo()`` /
          ``forget_listed()``.

      Everything one of these caches hands back is shared between callers and must
      be treated as read-only — including the aggregate ``fetch_all_bot_performance``
      returns.
    - No handlers/ or condor.web imports (prevents circular deps). Wire shapes
      shared with the web layer live in ``condor.fetchers.models`` and are
      re-exported by ``condor.web.models``, so the edge points outward only; a
      fetcher raises its own exception type (e.g.
      ``archived_run.ArchivedRunUnavailable``) rather than ``HTTPException``, and
      mapping that to a status code is the route's job.
    - Keep thin: call client method, light transform, return.

Importing:
    Deep module imports are the convention — ``from condor.fetchers.bot_performance
    import fetch_all_bot_performance``. Every consumer except one does that, and
    modules with no re-export below (``bot_performance``, ``archived_run``,
    ``run_history``, ``gateway_tokens``, ``models``, ``bots.build_bots_page`` /
    ``extract_bots_list``, ``portfolio.fetch_portfolio_refreshed`` /
    ``fetch_cex_balances``) are reached that way only. The names re-exported here
    exist for condor.server_data_service.register_default_fetches(), the sole
    façade consumer; they are not the package's public surface.
"""

from condor.fetchers.bots import (
    fetch_bot_runs,
    fetch_bots_enrichment,
    fetch_bots_status,
)
from condor.fetchers.connectors import (
    fetch_available_cex_connectors,
    fetch_connectors,
    fetch_venues,
    is_cex_connector,
)
from condor.fetchers.executors import (
    create_executor,
    describe_executor_error,
    extract_executors_list,
    fetch_all_executors,
    fetch_executors,
    get_executor_detail,
    get_executor_fees,
    get_executor_pnl,
    get_executor_type,
    get_executor_volume,
    stop_executor,
)
from condor.fetchers.market_data import (
    fetch_candle_connectors,
    fetch_candles,
    fetch_current_price,
    fetch_rates,
    fetch_ticker_pool,
    fetch_tickers,
)
from condor.fetchers.orders import fetch_active_orders
from condor.fetchers.performance_history import (
    PerformanceHistoryUnsupported,
)
from condor.fetchers.performance_history import extract_rows as extract_performance_rows
from condor.fetchers.performance_history import (
    fetch_performance_history,
    probe_performance_history,
    reject_foreign_filters,
)
from condor.fetchers.portfolio import fetch_portfolio, fetch_portfolio_history
from condor.fetchers.positions import fetch_positions
from condor.fetchers.server_status import fetch_server_status
from condor.fetchers.tracked_positions import fetch_tracked_positions
from condor.fetchers.trading_rules import fetch_trading_rules

__all__ = [
    "fetch_portfolio",
    "fetch_portfolio_history",
    "fetch_positions",
    "fetch_tracked_positions",
    "fetch_active_orders",
    "fetch_trading_rules",
    "fetch_connectors",
    "fetch_available_cex_connectors",
    "fetch_venues",
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
    "fetch_bots_enrichment",
    "fetch_bot_runs",
    "fetch_current_price",
    "fetch_candles",
    "fetch_candle_connectors",
    "fetch_rates",
    "fetch_ticker_pool",
    "fetch_tickers",
    "fetch_server_status",
    "fetch_performance_history",
    "probe_performance_history",
    "extract_performance_rows",
    "reject_foreign_filters",
    "PerformanceHistoryUnsupported",
]
