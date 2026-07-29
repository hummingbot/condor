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
import re
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

    cm = get_config_manager()

    # Get user_id from user_data for access control
    user_id = user_data.get("_user_id") if user_data else None

    # Get servers the user has access to (not all servers)
    if user_id:
        accessible_servers = cm.get_accessible_servers(user_id)
        # Filter to only enabled servers
        all_servers = cm.list_servers()
        enabled_accessible = [
            s for s in accessible_servers if all_servers.get(s, {}).get("enabled", True)
        ]
    else:
        # Fallback for legacy calls without user_data - use all enabled servers
        # This should not happen in normal operation
        logger.warning(
            "get_bots_client called without user_data - cannot verify server access"
        )
        all_servers = cm.list_servers()
        enabled_accessible = [
            name for name, cfg in all_servers.items() if cfg.get("enabled", True)
        ]

    if not enabled_accessible:
        raise ValueError(
            "No accessible API servers available. Please configure server access."
        )

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


async def reconcile_initial_positions(
    client: Any,
    credentials_profile: str,
    controllers_config: List[str],
) -> List[str]:
    """Seed each controller's `initial_positions` from the real exchange position
    before it deploys, so the controller's own base_pct/SL/TP accounting starts from
    the truth instead of zero.

    Without this, every fresh deploy (including a redeploy of an existing bot, e.g. to
    pick up a new image or config) resets a controller's position tracking to empty.
    hummingbot's v2 controllers don't reconcile against the real wallet balance on
    their own -- `positions_held` is just an in-memory list seeded only from
    `initial_positions` at startup and otherwise built up from that instance's own
    executor fills. A controller that had already accumulated a large position keeps
    trading against the real exchange position, but its own dashboard/SL/TP can silently
    fall behind (worse, the max_base_pct safety cap can stop protecting once the real
    position runs ahead of what the controller thinks it holds). See the pmm_mister
    07-29 incident where a bot's tracked position (11,840.7 OP) was found to be well
    behind the real exchange position (18,032.8 OP, ~79% of allocation vs. the
    intended 70% cap) after the underlying rate-limit/retry storm caused fills to be
    silently under-counted.

    Best-effort: any failure here is logged and swallowed so a reconciliation problem
    never blocks a deploy outright -- the bot would otherwise start from a *safe*
    (zero) baseline instead of a real one, which is the pre-existing behavior.

    Returns the list of config_names that were updated with a nonzero seeded position
    (for logging/confirmation to the user).
    """
    updated: List[str] = []
    try:
        positions_result = await client.trading.get_positions(
            account_names=[credentials_profile], limit=1000
        )
    except Exception:
        logger.exception(
            "reconcile_initial_positions: failed to fetch live positions, "
            "deploying without position reconciliation"
        )
        return updated

    position_by_key = {}
    for pos in positions_result.get("data", []):
        key = (pos.get("connector_name"), pos.get("trading_pair"))
        position_by_key[key] = pos

    for config_name in controllers_config:
        try:
            config = await client.controllers.get_controller_config(config_name)
            connector_name = config.get("connector_name")
            trading_pair = config.get("trading_pair")
            if not connector_name or not trading_pair:
                # Multi-pair/non-positional controller types don't map to a single
                # (connector, trading_pair) position -- nothing to reconcile.
                continue

            pos = position_by_key.get((connector_name, trading_pair))
            amount = float(pos["amount"]) if pos else 0.0
            if abs(amount) < 1e-9:
                new_initial_positions: List[Dict[str, Any]] = []
            else:
                side = "BUY" if pos.get("side") == "LONG" else "SELL"
                new_initial_positions = [{
                    "connector_name": connector_name,
                    "trading_pair": trading_pair,
                    "amount": abs(amount),
                    "side": side,
                }]

            if config.get("initial_positions") == new_initial_positions:
                continue

            config["initial_positions"] = new_initial_positions
            config = {k: v for k, v in config.items() if not k.startswith("_")}
            await client.controllers.create_or_update_controller_config(config_name, config)
            if new_initial_positions:
                updated.append(config_name)
                logger.info(
                    f"reconcile_initial_positions: seeded {config_name} with real "
                    f"{trading_pair} position ({side} {abs(amount)}) before deploy"
                )
        except Exception:
            logger.exception(
                f"reconcile_initial_positions: failed to reconcile {config_name}, "
                "leaving its config as-is"
            )

    return updated


async def deploy_v2_controllers_headless(
    client: Any,
    instance_name: str,
    credentials_profile: str,
    controllers_config: List[str],
    max_global_drawdown_quote: Optional[float] = None,
    max_controller_drawdown_quote: Optional[float] = None,
    image: str = "condor/hummingbot:hyperliquid-cancel-fix",
) -> Dict[str, Any]:
    """Deploy a V2 controllers bot with headless=True forced in the payload.

    hummingbot-api only force-enables the MQTT bridge (mqtt_autostart) when the deploy
    request includes headless=true, but hummingbot_api_client's deploy_v2_controllers()
    doesn't expose that param at all. Without it, the bot trades normally but is invisible
    to Condor's MQTT-based status/discovery layer (shows as stopped/0 controllers even
    while actively placing orders). Bypasses the client wrapper and posts directly.

    Before deploying, reconciles each controller's `initial_positions` against the real
    exchange position (see `reconcile_initial_positions`) -- this runs on every deploy,
    including a redeploy of a pre-existing bot, which is exactly the case where a
    controller's own position tracking would otherwise silently reset to zero.
    """
    await reconcile_initial_positions(client, credentials_profile, controllers_config)
    payload = {
        "instance_name": instance_name,
        "credentials_profile": credentials_profile,
        "controllers_config": controllers_config,
        "image": image,
        "headless": True,
    }
    if max_global_drawdown_quote is not None:
        payload["max_global_drawdown_quote"] = max_global_drawdown_quote
    if max_controller_drawdown_quote is not None:
        payload["max_controller_drawdown_quote"] = max_controller_drawdown_quote
    return await client.bot_orchestration._post(
        "/bot-orchestration/deploy-v2-controllers", json=payload
    )


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


# Internal/auto-managed fields that a controller config template doesn't declare
# as regular parameters but which are still legitimate to submit.
_CONFIG_TEMPLATE_SKIP_FIELDS = {
    "id",
    "controller_name",
    "controller_type",
    "candles_config",
    "initial_positions",
}


def validate_config_against_template(
    config_data: Dict[str, Any], template: Dict[str, Any]
) -> None:
    """Validate a controller config dict against the controller's real field template.

    Catches the case where a hand-written or LLM-drafted config uses field names from
    a different (often similarly-named) controller — e.g. pmm_dynamic's `cooldown_time`/
    `stop_loss`/`time_limit` pasted into a `pmm_mister` config, which actually uses
    `buy_cooldown_time`/`sell_cooldown_time`/`global_stop_loss` and has no time_limit
    field at all. Without this check, such a config saves and deploys successfully but
    the strategy crashes at startup with an opaque pydantic "extra_forbidden" error,
    silently leaving the bot running with zero active controllers.

    Raises:
        ValueError: listing missing required fields and/or unknown fields, with a
            closest-match suggestion (via difflib) for each unknown field name.
    """
    import difflib

    missing_fields: List[str] = []
    unknown_fields: List[str] = []

    template_field_names = set(template.keys())

    for param_name, param_info in template.items():
        if param_name in _CONFIG_TEMPLATE_SKIP_FIELDS:
            continue
        default = param_info.get("default")
        has_default = default is not None
        if not has_default and param_name not in config_data:
            param_type = str(param_info.get("type", "unknown"))
            param_type = param_type.replace("<class '", "").replace("'>", "")
            missing_fields.append(f"  - {param_name} ({param_type})")

    known_field_names = template_field_names | _CONFIG_TEMPLATE_SKIP_FIELDS
    for key in config_data:
        if key.startswith("_") or key in known_field_names:
            continue
        suggestion = difflib.get_close_matches(
            key, template_field_names, n=1, cutoff=0.5
        )
        if suggestion:
            unknown_fields.append(f"  - {key} (did you mean '{suggestion[0]}'?)")
        else:
            unknown_fields.append(f"  - {key}")

    errors: List[str] = []
    if missing_fields:
        errors.append(
            "Missing required fields (no default value in schema):\n"
            + "\n".join(missing_fields)
        )
    if unknown_fields:
        errors.append(
            "Unknown fields not in controller schema (possible typos or the wrong "
            "controller's field names):\n" + "\n".join(unknown_fields)
        )

    if errors:
        raise ValueError(
            "Config validation failed against controller template schema.\n\n"
            + "\n\n".join(errors)
        )


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


# Matches stringified enum values like "PositionMode.ONEWAY", "OrderType.LIMIT",
# "TradeType.BUY" or the colon variant "PositionMode:ONEWAY". The class name is
# PascalCase and the member starts with an uppercase letter, which avoids matching
# legitimate values such as trading times ("09:30") or token symbols ("USDC.e").
_ENUM_STR_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*[.:][A-Z][A-Za-z0-9_]*$")


def normalize_enum_value(value: Any) -> Any:
    """Strip the enum-class prefix from stringified enum values.

    Controller config templates expose enum defaults as strings like
    "PositionMode.ONEWAY". Saving them verbatim breaks downstream controller
    validation, which expects the plain member name ("ONEWAY"). This recurses
    into dicts and lists so nested config values are normalized too.
    """
    if isinstance(value, str) and _ENUM_STR_RE.match(value):
        return re.split(r"[.:]", value, maxsplit=1)[1]
    if isinstance(value, dict):
        return {k: normalize_enum_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_enum_value(v) for v in value]
    return value


def clean_config_for_save(config: Dict[str, Any]) -> Dict[str, Any]:
    """Strip internal fields and normalize stringified enum values before saving.

    The backend adds _config_name to YAML files, but Pydantic models with
    extra='forbid' reject it on reload. Strip any underscore-prefixed keys
    that aren't part of the controller config schema.

    Enum fields (e.g. position_mode, side, take_profit_order_type) can arrive as
    prefixed strings like "PositionMode.ONEWAY"; normalize them to the plain
    member name so deploy/backtest validation accepts them.
    """
    return {
        k: normalize_enum_value(v) for k, v in config.items() if not k.startswith("_")
    }


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
