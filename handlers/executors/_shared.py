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
    "activation_bounds": 0.001,
}

# Position executor defaults
POSITION_EXECUTOR_DEFAULTS = {
    "type": "position_executor",
    "connector_name": "",
    "trading_pair": "",
    "side": SIDE_LONG,
    "leverage": 10,
    "amount": 0.0,
    "entry_price": 0.0,
    "stop_loss": 0.03,
    "take_profit": 0.02,
    "time_limit": 0,
    "trailing_stop_activation": 0.0,
    "trailing_stop_delta": 0.0,
}


def get_executor_type(executor: Dict[str, Any]) -> str:
    """Determine executor type from its data.

    Returns: 'grid' or 'position'
    """
    config = executor.get("config", executor)
    for source in (config, executor):
        ex_type = source.get("type", "")
        if isinstance(ex_type, str):
            if "position" in ex_type.lower():
                return "position"
            if "grid" in ex_type.lower():
                return "grid"
    # Heuristic fallback based on config fields
    if "total_amount_quote" in config or "start_price" in config:
        return "grid"
    if "stop_loss" in config or "trailing_stop" in config:
        return "position"
    return "grid"


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
    elif executor_type == "position":
        config = POSITION_EXECUTOR_DEFAULTS.copy()
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
    status: str = "RUNNING",
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
        logger.info(f"search_executors response type={type(result).__name__}, keys={list(result.keys()) if isinstance(result, dict) else 'N/A'}, len={len(result) if isinstance(result, (list, dict)) else 'N/A'}")
        if isinstance(result, dict):
            # Try known keys in order of likelihood
            for key in ("executors", "data", "results", "items"):
                if key in result and isinstance(result[key], list):
                    logger.info(f"search_executors found {len(result[key])} executors under key '{key}'")
                    executors = result[key]
                    for ex in executors:
                        if isinstance(ex, dict):
                            ex_t = get_executor_type(ex)
                            numeric = {k: v for k, v in ex.items() if isinstance(v, (int, float))}
                            logger.info(f"search_executors [{ex_t}] keys={list(ex.keys())} numeric={numeric}")
                    return executors
            # If dict has no recognized list key, log and return empty
            logger.warning(f"search_executors: no recognized list key in response: {list(result.keys())}")
            return []
        executors = result if isinstance(result, list) else []
        for ex in executors:
            if isinstance(ex, dict):
                ex_t = get_executor_type(ex)
                numeric = {k: v for k, v in ex.items() if isinstance(v, (int, float))}
                logger.info(f"search_executors [{ex_t}] keys={list(ex.keys())} numeric={numeric}")
        return executors
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

def get_executor_pnl(executor: Dict[str, Any]) -> float:
    """Extract PnL from an executor response.

    Checks multiple field names since the API response structure varies.
    """
    pnl_keys = ("net_pnl_quote", "pnl_quote", "unrealized_pnl_quote", "realized_pnl_quote",
                 "net_pnl", "pnl", "close_pnl")
    for key in pnl_keys:
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)

    # Log available keys when PnL is 0 to help debug
    ex_type = get_executor_type(executor)
    if ex_type == "position":
        available = {k: v for k, v in executor.items()
                     if isinstance(v, (int, float)) and k != "timestamp"}
        logger.debug(f"Position executor PnL=0, numeric fields: {available}")

    return 0.0


def get_executor_volume(executor: Dict[str, Any]) -> float:
    """Extract filled/traded volume from an executor response."""
    for key in ("filled_amount_quote", "volume_traded", "total_volume"):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


def get_executor_fees(executor: Dict[str, Any]) -> float:
    """Extract cumulative fees from an executor response."""
    for key in ("cum_fees_quote", "fees_quote", "total_fees"):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


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
    side = config.get("side", SIDE_LONG)
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
    side = config.get("side", SIDE_LONG)
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
        # Validate that candles actually contain data
        if not candles:
            return None
        data = candles if isinstance(candles, list) else candles.get("data", [])
        if not data:
            logger.debug(f"No candle data available for {trading_pair} on {connector_name}")
            return None
        return candles
    except Exception as e:
        logger.error(f"Error fetching candles for {trading_pair}: {e}", exc_info=True)
        return None
