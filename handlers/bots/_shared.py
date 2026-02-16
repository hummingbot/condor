"""
Shared utilities for Bots handlers

Contains:
- Server client helper
- State management helpers
- Market data helpers
- Formatters

Controller-specific code (defaults, fields, charts) is in handlers/bots/controllers/
"""

import logging
import time
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

# Default cache TTL in seconds
DEFAULT_CACHE_TTL = 60


# ============================================
# BACKWARDS COMPATIBILITY IMPORTS
# ============================================
# Import from controller modules for backwards compatibility
# New code should import directly from handlers.bots.controllers

from .controllers import SUPPORTED_CONTROLLERS, get_controller
from .controllers.grid_strike import (
    SIDE_LONG,
    SIDE_SHORT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_LIMIT_MAKER,
    ORDER_TYPE_LABELS,
    generate_chart as _gs_generate_chart,
    generate_id as _gs_generate_id,
    calculate_auto_prices,
    DEFAULTS as GRID_STRIKE_DEFAULTS,
    FIELD_ORDER as GRID_STRIKE_FIELD_ORDER,
    EDITABLE_FIELDS as GS_EDITABLE_FIELDS,
)

# Convert ControllerField objects to dicts for backwards compatibility
from .controllers.grid_strike import FIELDS as _GS_FIELDS
GRID_STRIKE_FIELDS = {
    name: {
        "label": field.label,
        "type": field.type,
        "required": field.required,
        "hint": field.hint,
    }
    for name, field in _GS_FIELDS.items()
}


# ============================================
# SERVER CLIENT HELPER
# ============================================

async def get_bots_client(chat_id: Optional[int] = None, user_data: Optional[Dict] = None) -> Tuple[Any, str]:
    """Get the API client for bot operations

    Args:
        chat_id: Optional chat ID (legacy, not used for server selection)
        user_data: Optional user_data dict to get user's preferred server and user_id

    Returns:
        Tuple of (client, server_name) - client has bot_orchestration and controller endpoints

    Raises:
        ValueError: If no accessible servers are available for the user
    """
    from config_manager import get_config_manager

    cm = get_config_manager()

    # Get user_id from user_data for access control
    user_id = user_data.get('_user_id') if user_data else None

    # Get servers the user has access to (not all servers)
    if user_id:
        accessible_servers = cm.get_accessible_servers(user_id)
        # Filter to only enabled servers
        all_servers = cm.list_servers()
        enabled_accessible = [s for s in accessible_servers if all_servers.get(s, {}).get("enabled", True)]
    else:
        # Fallback for legacy calls without user_data - use all enabled servers
        # This should not happen in normal operation
        logger.warning("get_bots_client called without user_data - cannot verify server access")
        all_servers = cm.list_servers()
        enabled_accessible = [name for name, cfg in all_servers.items() if cfg.get("enabled", True)]

    if not enabled_accessible:
        raise ValueError("No accessible API servers available. Please configure server access.")

    # Use user's preferred server if valid
    preferred = None
    if user_data:
        from handlers.config.user_preferences import get_active_server
        preferred = get_active_server(user_data)

    # Only use preferred server if user has access to it
    if preferred and preferred in enabled_accessible:
        server_name = preferred
    elif enabled_accessible:
        server_name = enabled_accessible[0]
    else:
        raise ValueError("No accessible API servers available")

    logger.info(f"Bots using server: {server_name} (user_id: {user_id})")
    client = await cm.get_client(server_name)
    return client, server_name


# ============================================
# STATE MANAGEMENT
# ============================================

def clear_bots_state(context) -> None:
    """Clear all bots-related state from user context

    Args:
        context: Telegram context object
    """
    context.user_data.pop("bots_state", None)
    context.user_data.pop("controller_config_params", None)
    context.user_data.pop("controller_configs_list", None)
    context.user_data.pop("selected_controllers", None)
    context.user_data.pop("editing_controller_field", None)
    context.user_data.pop("deploy_params", None)
    context.user_data.pop("editing_deploy_field", None)
    # Archived bots state
    context.user_data.pop("archived_databases", None)
    context.user_data.pop("archived_current_db", None)
    context.user_data.pop("archived_page", None)
    context.user_data.pop("archived_summaries", None)


def get_controller_config(context) -> Dict[str, Any]:
    """Get the current controller config being edited

    Args:
        context: Telegram context object

    Returns:
        Controller config dict or empty dict
    """
    return context.user_data.get("controller_config_params", {})


def set_controller_config(context, config: Dict[str, Any]) -> None:
    """Set the current controller config

    Args:
        context: Telegram context object
        config: Controller config dict
    """
    context.user_data["controller_config_params"] = config


def init_new_controller_config(context, controller_type: str = "grid_strike") -> Dict[str, Any]:
    """Initialize a new controller config with defaults

    Args:
        context: Telegram context object
        controller_type: Type of controller (default: grid_strike)

    Returns:
        New controller config with defaults
    """
    controller_cls = get_controller(controller_type)
    if controller_cls:
        config = controller_cls.get_defaults()
    else:
        # Fallback to legacy method
        controller_info = SUPPORTED_CONTROLLERS.get(controller_type, SUPPORTED_CONTROLLERS["grid_strike"])
        config = controller_info["defaults"].copy()
        if "triple_barrier_config" in config:
            config["triple_barrier_config"] = config["triple_barrier_config"].copy()

    context.user_data["controller_config_params"] = config
    return config


# ============================================
# FORMATTERS
# ============================================

def format_controller_config_summary(config: Dict[str, Any]) -> str:
    """Format a controller config for display

    Args:
        config: Controller config dict

    Returns:
        Formatted string (not escaped)
    """
    lines = []

    config_id = config.get("id", "Not set")
    controller_name = config.get("controller_name", "unknown")

    lines.append(f"ID: {config_id}")
    lines.append(f"Type: {controller_name}")
    lines.append(f"Connector: {config.get('connector_name', 'N/A')}")
    lines.append(f"Pair: {config.get('trading_pair', 'N/A')}")

    side = config.get("side", 1)
    side_str = "LONG" if side == SIDE_LONG else "SHORT"
    lines.append(f"Side: {side_str}")

    lines.append(f"Leverage: {config.get('leverage', 1)}x")
    lines.append(f"Total Amount: {config.get('total_amount_quote', 0)}")

    start = config.get("start_price", 0)
    end = config.get("end_price", 0)
    limit = config.get("limit_price", 0)
    lines.append(f"Grid: {start} - {end} (limit: {limit})")

    return "\n".join(lines)


def format_config_field_value(field_name: str, value: Any) -> str:
    """Format a field value for display

    Args:
        field_name: Name of the field
        value: Field value

    Returns:
        Formatted string
    """
    if field_name == "side":
        return "LONG" if value == SIDE_LONG else "SHORT"
    elif field_name in ("open_order_type", "take_profit_order_type"):
        return ORDER_TYPE_LABELS.get(value, f"Unknown ({value})")
    elif field_name == "keep_position":
        return "Yes" if value else "No"
    elif field_name == "activation_bounds":
        if value is None:
            return "0.01 (1%)"
        return f"{value} ({value*100:.1f}%)"
    elif isinstance(value, float):
        if value == 0:
            return "Not set"
        return f"{value:g}"
    elif isinstance(value, bool):
        return "Yes" if value else "No"
    elif isinstance(value, dict):
        return "..."
    elif value == "" or value is None:
        return "Not set"
    return str(value)


# ============================================
# CACHE UTILITIES (borrowed from cex/_shared.py)
# ============================================

def get_cached(user_data: dict, key: str, ttl: int = DEFAULT_CACHE_TTL) -> Optional[Any]:
    """Get a cached value if still valid."""
    cache = user_data.get("_bots_cache", {})
    entry = cache.get(key)

    if entry is None:
        return None

    value, timestamp = entry
    if time.time() - timestamp > ttl:
        return None

    return value


def set_cached(user_data: dict, key: str, value: Any) -> None:
    """Store a value in the conversation cache."""
    if "_bots_cache" not in user_data:
        user_data["_bots_cache"] = {}

    user_data["_bots_cache"][key] = (value, time.time())


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
        logger.debug(f"Bots cache hit for '{key}'")
        return cached

    logger.debug(f"Bots cache miss for '{key}', fetching...")
    result = await fetch_func(*args, **kwargs)
    set_cached(user_data, key, result)
    return result


# ============================================
# CEX CONNECTOR HELPERS
# ============================================

def is_cex_connector(connector_name: str) -> bool:
    """Check if a connector is a CEX (not DEX/on-chain)."""
    connector_lower = connector_name.lower()
    dex_prefixes = ["solana", "ethereum", "polygon", "arbitrum", "base", "optimism", "avalanche"]
    return not any(connector_lower.startswith(prefix) for prefix in dex_prefixes)


async def fetch_available_cex_connectors(client, account_name: str = "master_account") -> List[str]:
    """Fetch list of available CEX connectors with credentials configured."""
    try:
        configured_connectors = await client.accounts.list_account_credentials(account_name)
        return [c for c in configured_connectors if is_cex_connector(c)]
    except Exception as e:
        logger.error(f"Error fetching connectors: {e}", exc_info=True)
        return []


async def get_available_cex_connectors(
    user_data: dict,
    client,
    account_name: str = "master_account",
    ttl: int = 300,
    server_name: str = "default"
) -> List[str]:
    """Get available CEX connectors with caching.

    Args:
        user_data: context.user_data dict
        client: API client instance
        account_name: Account name to check credentials for
        ttl: Cache time-to-live in seconds
        server_name: Server name to include in cache key (prevents cross-server cache pollution)

    Returns:
        List of available CEX connector names
    """
    cache_key = f"available_cex_connectors_{server_name}_{account_name}"
    return await cached_call(
        user_data,
        cache_key,
        fetch_available_cex_connectors,
        ttl,
        client,
        account_name
    )


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


# ============================================
# BACKWARDS COMPATIBILITY WRAPPERS
# ============================================

def generate_config_id(
    connector_name: str,
    trading_pair: str,
    side: int = None,
    start_price: float = None,
    end_price: float = None,
    existing_configs: List[Dict[str, Any]] = None
) -> str:
    """
    Generate a unique config ID with sequential numbering.

    Format: NNN_gs_connector_pair
    Example: 001_gs_binance_SOL-USDT

    Args:
        connector_name: Exchange connector name
        trading_pair: Trading pair (e.g., SOL-USDT)
        side: Side (LONG/SHORT) - unused, kept for compatibility
        start_price: Start price - unused, kept for compatibility
        end_price: End price - unused, kept for compatibility
        existing_configs: List of existing configs to determine sequence number

    Returns:
        Generated config ID
    """
    config = {
        "connector_name": connector_name,
        "trading_pair": trading_pair,
    }
    return _gs_generate_id(config, existing_configs or [])


def generate_candles_chart(
    candles_data: List[Dict[str, Any]],
    trading_pair: str,
    start_price: Optional[float] = None,
    end_price: Optional[float] = None,
    limit_price: Optional[float] = None,
    current_price: Optional[float] = None,
    side: int = SIDE_LONG
):
    """
    Generate a candlestick chart with grid zone overlay.

    Wrapper for backwards compatibility - converts individual parameters to config dict.

    Args:
        candles_data: List of candles from API
        trading_pair: Trading pair name
        start_price: Grid start price
        end_price: Grid end price
        limit_price: Stop limit price
        current_price: Current market price
        side: LONG or SHORT

    Returns:
        BytesIO object containing the PNG image
    """
    config = {
        "trading_pair": trading_pair,
        "start_price": start_price,
        "end_price": end_price,
        "limit_price": limit_price,
        "side": side,
    }
    return _gs_generate_chart(config, candles_data, current_price)
