"""
Shared utilities for Executors handlers

Contains:
- State management helpers
- API wrappers for executor operations
- Formatters for executor display
- Cache utilities
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from condor.cache import DEFAULT_CACHE_TTL
from condor.cache import get_cached as _get_cached
from condor.cache import set_cached as _set_cached
from condor.cache import clear_cache as _clear_cache
from condor.cache import cached_call as _cached_call

logger = logging.getLogger(__name__)

# Side constants (matching grid_strike)
SIDE_LONG = 1
SIDE_SHORT = 2


def normalize_side(side_val) -> int:
    """Normalize side value to numeric constant.

    The API may return side as a string ("BUY"/"SELL") or numeric (1/2).
    """
    if isinstance(side_val, str):
        return SIDE_SHORT if side_val.upper() in ("SELL", "SHORT", "S") else SIDE_LONG
    return side_val

# Order type constants (matching grid_strike controller)
ORDER_TYPE_MARKET = 1
ORDER_TYPE_LIMIT = 2
ORDER_TYPE_LIMIT_MAKER = 3

# Grid executor defaults
GRID_EXECUTOR_DEFAULTS = {
    "type": "grid_executor",
    "connector_name": "",
    "trading_pair": "",
    "side": SIDE_LONG,
    "leverage": 10,
    "total_amount_quote": 300,
    "start_price": 0.0,
    "end_price": 0.0,
    "limit_price": 0.0,
    "min_spread_between_orders": 0.0001,
    "min_order_amount_quote": 10,
    "max_open_orders": 5,
    "max_orders_per_batch": 2,
    "order_frequency": 1,
    "take_profit": 0.0002,
    "activation_bounds": 0.05,
    "open_order_type": ORDER_TYPE_LIMIT,
    "take_profit_order_type": ORDER_TYPE_LIMIT,
}

# Position executor defaults
POSITION_EXECUTOR_DEFAULTS = {
    "type": "position_executor",
    "connector_name": "",
    "trading_pair": "",
    "side": SIDE_LONG,
    "leverage": 10,
    "total_amount_quote": 0.0,
    "amount": 0.0,
    "entry_price": 0.0,           # 0 = market order
    "stop_loss": 0.03,            # -1 = disabled
    "take_profit": 0.02,          # -1 = disabled
    "time_limit": -1,             # -1 = disabled, positive int = seconds
    "trailing_stop_activation_price": -1,  # -1 = disabled
    "trailing_stop_trailing_delta": -1,    # -1 = disabled
    "open_order_type": 2,         # LIMIT=2
    "take_profit_order_type": 1,  # MARKET=1
    "stop_loss_order_type": 1,    # MARKET=1
    "time_limit_order_type": 1,   # MARKET=1
    "activation_bounds": -1,      # -1 = disabled, float = value
}


from condor.fetchers.executors import get_executor_type  # noqa: F811 — canonical source


# ============================================
# STATE MANAGEMENT
# ============================================


def clear_executors_state(context) -> None:
    """Clear all executors-related state from user context

    Args:
        context: Telegram context object
    """
    context.user_data.pop("executors_state", None)
    context.user_data.pop("executor_config_params", None)
    context.user_data.pop("executor_wizard_step", None)
    context.user_data.pop("executor_wizard_data", None)
    context.user_data.pop("executor_wizard_type", None)
    context.user_data.pop("executor_list_page", None)
    context.user_data.pop("executor_chart_interval", None)
    context.user_data.pop("history_executors", None)


def get_executor_config(context) -> Dict[str, Any]:
    """Get the current executor config being edited

    Args:
        context: Telegram context object

    Returns:
        Executor config dict or empty dict
    """
    return context.user_data.get("executor_config_params", {})


def set_executor_config(context, config: Dict[str, Any]) -> None:
    """Set the current executor config

    Args:
        context: Telegram context object
        config: Executor config dict
    """
    context.user_data["executor_config_params"] = config


def init_new_executor_config(context, executor_type: str = "grid") -> Dict[str, Any]:
    """Initialize a new executor config with defaults

    Merges hardcoded defaults with user's last-used config params
    (if any), so returning users start from their previous settings.

    Args:
        context: Telegram context object
        executor_type: Type of executor (default: grid)

    Returns:
        New executor config with defaults
    """
    if executor_type == "grid":
        config = GRID_EXECUTOR_DEFAULTS.copy()
    elif executor_type == "position":
        config = POSITION_EXECUTOR_DEFAULTS.copy()
    else:
        config = GRID_EXECUTOR_DEFAULTS.copy()

    # Overlay last-used config params from user preferences
    from handlers.config.user_preferences import get_executor_last_config

    last_config = get_executor_last_config(context.user_data, executor_type)
    if last_config:
        # Only merge keys that exist in defaults (skip connector/pair — those are wizard-selected)
        skip_keys = {"type", "connector_name", "trading_pair", "start_price", "end_price", "limit_price"}
        for key, value in last_config.items():
            if key in config and key not in skip_keys:
                config[key] = value

    context.user_data["executor_config_params"] = config
    return config


# ============================================
# API WRAPPERS
# ============================================


async def get_executors_client(
    chat_id: Optional[int] = None, user_data: Optional[Dict] = None
) -> Tuple[Any, str]:
    """Get the API client for executor operations

    Uses the same logic as bots client - gets user's preferred server.

    Args:
        chat_id: Optional chat ID
        user_data: Optional user_data dict

    Returns:
        Tuple of (client, server_name)

    Raises:
        ValueError: If no accessible servers are available
    """
    from handlers.bots._shared import get_bots_client

    return await get_bots_client(chat_id, user_data)


from condor.fetchers.executors import (  # noqa: F811
    create_executor,
    stop_executor,
    get_executor_detail,
    extract_executors_list,
)


async def search_running_executors(
    client, status: Optional[str] = "RUNNING", limit: int = 50
) -> List[Dict[str, Any]]:
    """Search for executors with a specific status."""
    try:
        result = await client.executors.search_executors(status=status, limit=limit)
        return extract_executors_list(result)
    except Exception as e:
        logger.error(f"Error searching executors: {e}", exc_info=True)
        return []


# ============================================
# FORMATTERS
# ============================================


from condor.fetchers.executors import get_executor_pnl  # noqa: F811
from condor.fetchers.executors import get_executor_volume  # noqa: F811
from condor.fetchers.executors import get_executor_fees  # noqa: F811


def format_executor_status_line(executor: Dict[str, Any]) -> str:
    """Format a single executor as a compact status line

    Format: SOL-USDT S 20x +$12.50 (V:$150)

    Args:
        executor: Executor dict from API

    Returns:
        Formatted status line (not escaped)
    """
    config = executor.get("config", executor)
    pair = config.get("trading_pair", "UNKNOWN")
    side = normalize_side(config.get("side", SIDE_LONG))
    leverage = config.get("leverage", 1)

    side_str = "L" if side == SIDE_LONG else "S"

    pnl = get_executor_pnl(executor)
    volume = get_executor_volume(executor)

    parts = [f"{pair} {side_str} {leverage}x ${pnl:+.2f}"]
    if volume:
        parts.append(f"V:${volume:,.0f}")

    return " ".join(parts)


def format_executor_pnl(pnl: float) -> str:
    """Format PnL with emoji indicator

    Args:
        pnl: PnL value

    Returns:
        Formatted string with emoji (not escaped)
    """
    if pnl >= 0:
        return f"🟢 +${pnl:.2f}"
    else:
        return f"🔴 ${pnl:.2f}"


def format_executor_summary(executor: Dict[str, Any]) -> str:
    """Format executor for detail display

    Args:
        executor: Executor dict from API

    Returns:
        Formatted multi-line string (not escaped)
    """
    config = executor.get("config", executor)
    executor_id = executor.get("id", executor.get("executor_id", "unknown"))

    pair = config.get("trading_pair", "UNKNOWN")
    connector = config.get("connector_name", "unknown")
    side = normalize_side(config.get("side", SIDE_LONG))
    leverage = config.get("leverage", 1)
    amount = config.get("total_amount_quote", 0)

    side_str = "LONG" if side == SIDE_LONG else "SHORT"

    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)

    pnl = get_executor_pnl(executor)
    volume = get_executor_volume(executor)
    fees = get_executor_fees(executor)
    status = executor.get("status", "unknown")

    lines = [
        f"ID: {executor_id}",
        f"Pair: {pair}",
        f"Connector: {connector}",
        f"Side: {side_str} {leverage}x",
        f"Amount: ${amount:,.2f}",
        f"Grid: {start_price:.6g} - {end_price:.6g}",
        f"Limit: {limit_price:.6g}",
        f"Status: {status}",
        f"PnL: {format_executor_pnl(pnl)}",
    ]

    if volume:
        lines.append(f"Volume: ${volume:,.2f}")
    if fees:
        lines.append(f"Fees: ${fees:,.2f}")

    return "\n".join(lines)


# ============================================
# CACHE UTILITIES (delegates to condor.cache)
# ============================================

_NS = "_executors_cache"


def get_cached(user_data: dict, key: str, ttl: int = DEFAULT_CACHE_TTL) -> Optional[Any]:
    return _get_cached(user_data, key, ttl, namespace=_NS)


def set_cached(user_data: dict, key: str, value: Any) -> None:
    _set_cached(user_data, key, value, namespace=_NS)


def invalidate_cache(user_data: dict, *keys: str) -> None:
    """Invalidate specific cache keys or all if 'all' is passed."""
    if "all" in keys:
        _clear_cache(user_data, namespace=_NS)
        return
    for key in keys:
        _clear_cache(user_data, key, namespace=_NS)


async def cached_call(
    user_data: dict, key: str, fetch_func, ttl: int = DEFAULT_CACHE_TTL, *args, **kwargs
) -> Any:
    return await _cached_call(user_data, key, fetch_func, ttl, *args, namespace=_NS, **kwargs)


# ============================================
# MARKET DATA HELPERS
# ============================================


from condor.fetchers.market_data import fetch_current_price  # noqa: F811
from condor.fetchers.market_data import fetch_candles  # noqa: F811
