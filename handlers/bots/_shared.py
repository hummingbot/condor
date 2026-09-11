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
from typing import Any, Dict, List, Optional, Tuple

from condor.cache import DEFAULT_CACHE_TTL
from condor.cache import cached_call as _cached_call
from condor.cache import get_cached as _get_cached
from condor.cache import set_cached as _set_cached

logger = logging.getLogger(__name__)


# ============================================
# BACKWARDS COMPATIBILITY IMPORTS
# ============================================
# Import from controller modules for backwards compatibility
# New code should import directly from handlers.bots.controllers

from .controllers import SUPPORTED_CONTROLLERS, get_controller

# Convert ControllerField objects to dicts for backwards compatibility
from .controllers.grid_strike import DEFAULTS as GRID_STRIKE_DEFAULTS
from .controllers.grid_strike import EDITABLE_FIELDS as GS_EDITABLE_FIELDS
from .controllers.grid_strike import FIELD_ORDER as GRID_STRIKE_FIELD_ORDER
from .controllers.grid_strike import FIELDS as _GS_FIELDS
from .controllers.grid_strike import (
    ORDER_TYPE_LABELS,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_LIMIT_MAKER,
    ORDER_TYPE_MARKET,
    SIDE_LONG,
    SIDE_SHORT,
    calculate_auto_prices,
)
from .controllers.grid_strike import generate_chart as _gs_generate_chart
from .controllers.grid_strike import generate_id as _gs_generate_id

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
# SERVER RESOLUTION (access-checked)
# ============================================

NO_ACCESSIBLE_SERVERS = (
    "No accessible API servers available. Ask an admin to share a server with you."
)


def resolve_accessible_server(user_data: Optional[Dict]) -> str:
    """Pick the server a user's request may be served from.

    The single seam for "which server does this user get?": candidates are the
    servers the caller can actually reach (``get_accessible_servers``), narrowed
    to the ones that are both configured and enabled, and the caller's stored
    ``active_server`` preference wins only when it is one of them. This is
    access-control logic -- CORR-246 was a real cross-user data leak caused by
    picking from *every* enabled server -- so it lives in exactly one place and
    fails closed.

    Args:
        user_data: context.user_data dict, carrying ``_user_id`` and preferences

    Returns:
        The name of an enabled server the user may use.

    Raises:
        ValueError: when the user has no enabled server they can access, or when
            ``_user_id`` is missing so no access list can be checked at all.
    """
    from config_manager import get_config_manager

    cm = get_config_manager()
    user_id = user_data.get("_user_id") if user_data else None

    if not user_id:
        # Every entry point is decorated @restricted, which stamps _user_id
        # before the handler body runs. Without it there is no access list to
        # check against, so refuse rather than serve an arbitrary server.
        logger.warning("Server resolution without _user_id - refusing")
        raise ValueError(NO_ACCESSIBLE_SERVERS)

    accessible = set(cm.get_accessible_servers(user_id))
    enabled_accessible = [
        name
        for name, cfg in cm.list_servers().items()
        if cfg.get("enabled", True) and name in accessible
    ]

    if not enabled_accessible:
        raise ValueError(NO_ACCESSIBLE_SERVERS)

    from condor.preferences import get_active_server

    preferred = get_active_server(user_data)
    if preferred and preferred in enabled_accessible:
        return preferred
    return enabled_accessible[0]


def resolve_accessible_server_or_none(user_data: Optional[Dict]) -> Optional[str]:
    """``resolve_accessible_server`` for callers that render a "no server" state.

    Same resolution, but returns ``None`` instead of raising so a menu can draw
    itself (offline/empty) rather than blowing up in the user's face.
    """
    try:
        return resolve_accessible_server(user_data)
    except ValueError:
        return None


# ============================================
# SERVER CLIENT HELPER
# ============================================


async def get_bots_client(
    chat_id: Optional[int] = None, user_data: Optional[Dict] = None
) -> Tuple[Any, str]:
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

    server_name = resolve_accessible_server(user_data)
    logger.info(f"Bots using server: {server_name}")
    client = await get_config_manager().get_client(server_name)
    return client, server_name


# ============================================
# STATE MANAGEMENT
# ============================================


def clear_bots_state(context) -> None:
    """Clear only bots-related state from user context.

    This is a NARROW cleaner: it pops just the bots/archived/config-menu feature
    keys and does NOT tear down unrelated state (active /trade SDS subscriptions,
    portfolio cache, etc.). It is invoked mid/end of bots flows and on menu
    re-entry (e.g. controller save, show_bots_menu), so it must not disturb a
    user's live trade view. For full state resets on top-level command
    entrypoints, use ``clear_all_input_states`` instead.

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
    context.user_data.pop("archived_total_count", None)
    # Config menu state
    context.user_data.pop("configs_controller_type", None)
    context.user_data.pop("configs_page", None)
    context.user_data.pop("selected_configs", None)
    context.user_data.pop("configs_type_filtered", None)


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


# Moved to condor/controller_configs.py (ARCH-190) so the web dashboard can use
# them without importing the handlers package; re-imported here for Telegram
# callers.
from condor.controller_configs import (  # noqa: E402,F401
    clean_config_for_save,
    normalize_enum_value,
)


def init_new_controller_config(
    context, controller_type: str = "grid_strike"
) -> Dict[str, Any]:
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
        controller_info = SUPPORTED_CONTROLLERS.get(
            controller_type, SUPPORTED_CONTROLLERS["grid_strike"]
        )
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
# CACHE UTILITIES (delegates to condor.cache)
# ============================================

_NS = "_bots_cache"


def get_cached(
    user_data: dict, key: str, ttl: int = DEFAULT_CACHE_TTL
) -> Optional[Any]:
    return _get_cached(user_data, key, ttl, namespace=_NS)


def set_cached(user_data: dict, key: str, value: Any) -> None:
    _set_cached(user_data, key, value, namespace=_NS)


async def cached_call(
    user_data: dict, key: str, fetch_func, ttl: int = DEFAULT_CACHE_TTL, *args, **kwargs
) -> Any:
    return await _cached_call(
        user_data, key, fetch_func, ttl, *args, namespace=_NS, **kwargs
    )


# ============================================
# CEX CONNECTOR HELPERS
# ============================================


from condor.fetchers.connectors import (  # noqa: F811
    fetch_available_cex_connectors,
    is_cex_connector,
)


async def get_available_cex_connectors(
    user_data: dict,
    client,
    account_name: str = "master_account",
    ttl: int = 300,
    server_name: str = "default",
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
        user_data, cache_key, fetch_available_cex_connectors, ttl, client, account_name
    )


# ============================================
# MARKET DATA HELPERS
# ============================================


from condor.fetchers.market_data import fetch_candles, fetch_current_price  # noqa: F811

# ============================================
# BACKWARDS COMPATIBILITY WRAPPERS
# ============================================


def generate_config_id(
    connector_name: str,
    trading_pair: str,
    side: int = None,
    start_price: float = None,
    end_price: float = None,
    existing_configs: List[Dict[str, Any]] = None,
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
    side: int = SIDE_LONG,
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
