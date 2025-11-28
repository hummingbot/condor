"""
Shared utilities for Bots handlers

Contains:
- Server client helper
- Grid Strike controller defaults
- State management helpers
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


# ============================================
# GRID STRIKE CONTROLLER DEFAULTS
# ============================================

GRID_STRIKE_DEFAULTS: Dict[str, Any] = {
    "controller_name": "grid_strike",
    "controller_type": "generic",
    "id": "",
    "connector_name": "binance",
    "trading_pair": "SOL-FDUSD",
    "side": 1,  # 1 = LONG, -1 = SHORT
    "leverage": 1,
    "position_mode": "HEDGE",
    "total_amount_quote": 1000,
    "min_order_amount_quote": 6,
    "start_price": 0.0,
    "end_price": 0.0,
    "limit_price": 0.0,
    "max_open_orders": 3,
    "max_orders_per_batch": 1,
    "min_spread_between_orders": 0.0002,
    "order_frequency": 3,
    "activation_bounds": 0.001,
    "keep_position": True,
    "triple_barrier_config": {
        "open_order_type": 3,
        "take_profit": 0.0001,
        "take_profit_order_type": 3,
    },
}

# Field configurations for the form
GRID_STRIKE_FIELDS = {
    "id": {"label": "Config ID", "type": "str", "required": True, "hint": "e.g. sol_fdusd_grid_long"},
    "connector_name": {"label": "Connector", "type": "str", "required": True, "hint": "e.g. binance"},
    "trading_pair": {"label": "Trading Pair", "type": "str", "required": True, "hint": "e.g. SOL-FDUSD"},
    "side": {"label": "Side", "type": "int", "required": True, "hint": "1=LONG, -1=SHORT"},
    "leverage": {"label": "Leverage", "type": "int", "required": True, "hint": "e.g. 1, 5, 10"},
    "total_amount_quote": {"label": "Total Amount (Quote)", "type": "float", "required": True, "hint": "e.g. 1000"},
    "start_price": {"label": "Start Price", "type": "float", "required": True, "hint": "Grid start price"},
    "end_price": {"label": "End Price", "type": "float", "required": True, "hint": "Grid end price"},
    "limit_price": {"label": "Limit Price", "type": "float", "required": True, "hint": "Stop limit price"},
    "max_open_orders": {"label": "Max Open Orders", "type": "int", "required": False, "hint": "Default: 3"},
    "min_spread_between_orders": {"label": "Min Spread", "type": "float", "required": False, "hint": "Default: 0.0002"},
    "take_profit": {"label": "Take Profit", "type": "float", "required": False, "hint": "Default: 0.0001"},
}

# Field display order for the menu
GRID_STRIKE_FIELD_ORDER = [
    "id", "connector_name", "trading_pair", "side", "leverage",
    "total_amount_quote", "start_price", "end_price", "limit_price",
    "max_open_orders", "min_spread_between_orders", "take_profit"
]


# ============================================
# SUPPORTED CONTROLLER TYPES
# ============================================

SUPPORTED_CONTROLLERS = {
    "grid_strike": {
        "name": "Grid Strike",
        "description": "Grid trading with stop-limit orders",
        "defaults": GRID_STRIKE_DEFAULTS,
        "fields": GRID_STRIKE_FIELDS,
        "field_order": GRID_STRIKE_FIELD_ORDER,
    },
}


# ============================================
# SERVER CLIENT HELPER
# ============================================

async def get_bots_client():
    """Get the API client for bot operations

    Returns:
        Client instance with bot_orchestration and controller endpoints

    Raises:
        ValueError: If no enabled servers available
    """
    from servers import server_manager

    servers = server_manager.list_servers()
    enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

    if not enabled_servers:
        raise ValueError("No enabled API servers available")

    # Use default server if set, otherwise fall back to first enabled
    default_server = server_manager.get_default_server()
    if default_server and default_server in enabled_servers:
        server_name = default_server
    else:
        server_name = enabled_servers[0]

    logger.info(f"Bots using server: {server_name}")
    client = await server_manager.get_client(server_name)

    return client


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
    controller_info = SUPPORTED_CONTROLLERS.get(controller_type, SUPPORTED_CONTROLLERS["grid_strike"])
    config = controller_info["defaults"].copy()
    # Deep copy triple_barrier_config
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
    side_str = "LONG" if side == 1 else "SHORT"
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
        return "LONG" if value == 1 else "SHORT"
    elif field_name == "keep_position":
        return "Yes" if value else "No"
    elif isinstance(value, float):
        if value == 0:
            return "Not set"
        return f"{value:g}"
    elif isinstance(value, dict):
        return "..."
    elif value == "" or value is None:
        return "Not set"
    return str(value)
