"""
Shared utilities for Executors handlers

Contains:
- State management helpers
- API wrappers for executor operations
- Formatters for executor display
- Cache utilities
"""

import logging
import time
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

# Default cache TTL in seconds
DEFAULT_CACHE_TTL = 60

# Side constants (matching grid_strike)
SIDE_LONG = 1
SIDE_SHORT = 2

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
    "min_order_amount_quote": 6,
    "max_open_orders": 5,
    "max_orders_per_batch": 2,
    "order_frequency": 1,
    "take_profit": 0.0002,
}


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
    context.user_data.pop("executor_list_page", None)
    context.user_data.pop("executor_chart_interval", None)


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

    Args:
        context: Telegram context object
        executor_type: Type of executor (default: grid)

    Returns:
        New executor config with defaults
    """
    if executor_type == "grid":
        config = GRID_EXECUTOR_DEFAULTS.copy()
    else:
        config = GRID_EXECUTOR_DEFAULTS.copy()

    context.user_data["executor_config_params"] = config
    return config


# ============================================
# API WRAPPERS
# ============================================

async def get_executors_client(chat_id: Optional[int] = None, user_data: Optional[Dict] = None) -> Tuple[Any, str]:
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


async def search_running_executors(
    client,
    status: str = "running",
    limit: int = 50
) -> List[Dict[str, Any]]:
    """Search for executors with a specific status

    Args:
        client: API client
        status: Status to filter by (default: running)
        limit: Maximum number of results

    Returns:
        List of executor dicts
    """
    try:
        result = await client.executors.search_executors(
            status=status,
            limit=limit
        )
        if isinstance(result, dict):
            return result.get("executors", result.get("data", []))
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error(f"Error searching executors: {e}", exc_info=True)
        return []


async def create_executor(
    client,
    config: Dict[str, Any],
    account_name: str = "master_account"
) -> Dict[str, Any]:
    """Create a new executor

    Args:
        client: API client
        config: Executor configuration
        account_name: Account to use

    Returns:
        API response dict
    """
    try:
        result = await client.executors.create_executor(
            executor_config=config,
            account_name=account_name
        )
        return result
    except Exception as e:
        logger.error(f"Error creating executor: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def stop_executor(
    client,
    executor_id: str,
    keep_position: bool = False
) -> Dict[str, Any]:
    """Stop a running executor

    Args:
        client: API client
        executor_id: ID of executor to stop
        keep_position: Whether to keep the position open

    Returns:
        API response dict
    """
    try:
        result = await client.executors.stop_executor(
            executor_id=executor_id,
            keep_position=keep_position
        )
        return result
    except Exception as e:
        logger.error(f"Error stopping executor: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def get_executor_detail(
    client,
    executor_id: str
) -> Optional[Dict[str, Any]]:
    """Get details for a specific executor

    Args:
        client: API client
        executor_id: ID of executor

    Returns:
        Executor details dict or None
    """
    try:
        result = await client.executors.get_executor(executor_id=executor_id)
        return result
    except Exception as e:
        logger.error(f"Error getting executor detail: {e}", exc_info=True)
        return None


# ============================================
# FORMATTERS
# ============================================

def format_executor_status_line(executor: Dict[str, Any]) -> str:
    """Format a single executor as a compact status line

    Format: SOL-USDT L 10x +$12.50

    Args:
        executor: Executor dict from API

    Returns:
        Formatted status line (not escaped)
    """
    config = executor.get("config", executor)
    pair = config.get("trading_pair", "UNKNOWN")
    side = config.get("side", SIDE_LONG)
    leverage = config.get("leverage", 1)

    side_str = "L" if side == SIDE_LONG else "S"

    # Get PnL if available
    pnl = executor.get("pnl_quote", 0) or executor.get("unrealized_pnl_quote", 0) or 0
    pnl_str = f"{pnl:+.2f}"

    return f"{pair} {side_str} {leverage}x ${pnl_str}"


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
    side = config.get("side", SIDE_LONG)
    leverage = config.get("leverage", 1)
    amount = config.get("total_amount_quote", 0)

    side_str = "LONG" if side == SIDE_LONG else "SHORT"

    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)

    pnl = executor.get("pnl_quote", 0) or 0
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

    return "\n".join(lines)


# ============================================
# CACHE UTILITIES
# ============================================

def get_cached(user_data: dict, key: str, ttl: int = DEFAULT_CACHE_TTL) -> Optional[Any]:
    """Get a cached value if still valid."""
    cache = user_data.get("_executors_cache", {})
    entry = cache.get(key)

    if entry is None:
        return None

    value, timestamp = entry
    if time.time() - timestamp > ttl:
        return None

    return value


def set_cached(user_data: dict, key: str, value: Any) -> None:
    """Store a value in the conversation cache."""
    if "_executors_cache" not in user_data:
        user_data["_executors_cache"] = {}

    user_data["_executors_cache"][key] = (value, time.time())


def invalidate_cache(user_data: dict, *keys: str) -> None:
    """Invalidate specific cache keys or all if 'all' is passed."""
    cache = user_data.get("_executors_cache", {})

    if "all" in keys:
        user_data["_executors_cache"] = {}
        return

    for key in keys:
        cache.pop(key, None)


async def cached_call(
    user_data: dict,
    key: str,
    fetch_func,
    ttl: int = DEFAULT_CACHE_TTL,
    *args,
    **kwargs
) -> Any:
    """Execute an async function with caching."""
    cached = get_cached(user_data, key, ttl)
    if cached is not None:
        logger.debug(f"Executors cache hit for '{key}'")
        return cached

    logger.debug(f"Executors cache miss for '{key}', fetching...")
    result = await fetch_func(*args, **kwargs)
    set_cached(user_data, key, result)
    return result


# ============================================
# MARKET DATA HELPERS
# ============================================

async def fetch_current_price(client, connector_name: str, trading_pair: str) -> Optional[float]:
    """Fetch current price for a trading pair."""
    try:
        prices = await client.market_data.get_prices(
            connector_name=connector_name,
            trading_pairs=trading_pair
        )
        return prices.get("prices", {}).get(trading_pair)
    except Exception as e:
        logger.error(f"Error fetching price for {trading_pair}: {e}", exc_info=True)
        return None


async def fetch_candles(
    client,
    connector_name: str,
    trading_pair: str,
    interval: str = "1m",
    max_records: int = 420
) -> Optional[Dict[str, Any]]:
    """Fetch candles data for a trading pair."""
    try:
        candles = await client.market_data.get_candles(
            connector_name=connector_name,
            trading_pair=trading_pair,
            interval=interval,
            max_records=max_records
        )
        return candles
    except Exception as e:
        logger.error(f"Error fetching candles for {trading_pair}: {e}", exc_info=True)
        return None
