"""
PMM Mister controller configuration.

Contains defaults, field definitions, and validation for PMM (Pure Market Making) controllers.
Features hanging executors, price distance requirements, and breakeven awareness.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

# Order type mapping
ORDER_TYPE_MARKET = 1
ORDER_TYPE_LIMIT = 2
ORDER_TYPE_LIMIT_MAKER = 3

ORDER_TYPE_LABELS = {
    ORDER_TYPE_MARKET: "MARKET",
    ORDER_TYPE_LIMIT: "LIMIT",
    ORDER_TYPE_LIMIT_MAKER: "LIMIT_MAKER",
}


# Default configuration values
DEFAULTS: Dict[str, Any] = {
    "controller_name": "pmm_mister",
    "controller_type": "generic",
    "id": "",
    "connector_name": "",
    "trading_pair": "",
    "leverage": 20,
    "position_mode": "HEDGE",
    "total_amount_quote": 100,
    "portfolio_allocation": 0.05,
    "target_base_pct": 0.5,
    "min_base_pct": 0.4,
    "max_base_pct": 0.6,
    "buy_spreads": "0.0002,0.001",
    "sell_spreads": "0.0002,0.001",
    "buy_amounts_pct": None,  # Auto-calculated: 1 per spread level
    "sell_amounts_pct": None,  # Auto-calculated: 1 per spread level
    "executor_refresh_time": 30,
    "buy_cooldown_time": 15,
    "sell_cooldown_time": 15,
    "buy_position_effectivization_time": 3600,
    "sell_position_effectivization_time": 3600,
    "min_buy_price_distance_pct": 0.003,
    "min_sell_price_distance_pct": 0.003,
    "take_profit": 0.0001,
    "take_profit_order_type": "LIMIT_MAKER",  # String format for API
    "open_order_type": "LIMIT",  # String format for API
    "max_active_executors_by_level": 4,
    "tick_mode": False,
    "candles_config": [],
}


# Field definitions for form
FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(
        name="id",
        label="Config ID",
        type="str",
        required=True,
        hint="Auto-generated with sequence number",
    ),
    "connector_name": ControllerField(
        name="connector_name",
        label="Connector",
        type="str",
        required=True,
        hint="Select from available exchanges",
    ),
    "trading_pair": ControllerField(
        name="trading_pair",
        label="Trading Pair",
        type="str",
        required=True,
        hint="e.g. BTC-FDUSD, ETH-USDT",
    ),
    "leverage": ControllerField(
        name="leverage",
        label="Leverage",
        type="int",
        required=True,
        hint="e.g. 1, 10, 20",
        default=20,
    ),
    "portfolio_allocation": ControllerField(
        name="portfolio_allocation",
        label="Portfolio Allocation",
        type="float",
        required=True,
        hint="Fraction of portfolio (e.g. 0.05 = 5%)",
        default=0.05,
    ),
    "target_base_pct": ControllerField(
        name="target_base_pct",
        label="Target Base %",
        type="float",
        required=True,
        hint="Target base asset percentage (e.g. 0.2 = 20%)",
        default=0.2,
    ),
    "min_base_pct": ControllerField(
        name="min_base_pct",
        label="Min Base %",
        type="float",
        required=False,
        hint="Minimum base % before buying (default: 0.1)",
        default=0.1,
    ),
    "max_base_pct": ControllerField(
        name="max_base_pct",
        label="Max Base %",
        type="float",
        required=False,
        hint="Maximum base % before selling (default: 0.4)",
        default=0.4,
    ),
    "buy_spreads": ControllerField(
        name="buy_spreads",
        label="Buy Spreads",
        type="str",
        required=True,
        hint="Comma-separated spreads (e.g. 0.0002,0.001)",
        default="0.0002,0.001",
    ),
    "sell_spreads": ControllerField(
        name="sell_spreads",
        label="Sell Spreads",
        type="str",
        required=True,
        hint="Comma-separated spreads (e.g. 0.0002,0.001)",
        default="0.0002,0.001",
    ),
    "buy_amounts_pct": ControllerField(
        name="buy_amounts_pct",
        label="Buy Amounts %",
        type="str",
        required=False,
        hint="Comma-separated amounts (e.g. 1,2)",
        default="1,2",
    ),
    "sell_amounts_pct": ControllerField(
        name="sell_amounts_pct",
        label="Sell Amounts %",
        type="str",
        required=False,
        hint="Comma-separated amounts (e.g. 1,2)",
        default="1,2",
    ),
    "take_profit": ControllerField(
        name="take_profit",
        label="Take Profit",
        type="float",
        required=True,
        hint="Take profit percentage (e.g. 0.0001 = 0.01%)",
        default=0.0001,
    ),
    "take_profit_order_type": ControllerField(
        name="take_profit_order_type",
        label="TP Order Type",
        type="int",
        required=False,
        hint="Order type for take profit",
        default=ORDER_TYPE_LIMIT_MAKER,
    ),
    "executor_refresh_time": ControllerField(
        name="executor_refresh_time",
        label="Refresh Time (s)",
        type="int",
        required=False,
        hint="Executor refresh interval (default: 30)",
        default=30,
    ),
    "buy_cooldown_time": ControllerField(
        name="buy_cooldown_time",
        label="Buy Cooldown (s)",
        type="int",
        required=False,
        hint="Cooldown between buy orders (default: 15)",
        default=15,
    ),
    "sell_cooldown_time": ControllerField(
        name="sell_cooldown_time",
        label="Sell Cooldown (s)",
        type="int",
        required=False,
        hint="Cooldown between sell orders (default: 15)",
        default=15,
    ),
    "buy_position_effectivization_time": ControllerField(
        name="buy_position_effectivization_time",
        label="Buy Effect. Time (s)",
        type="int",
        required=False,
        hint="Time to effectivize buy positions (default: 60)",
        default=60,
    ),
    "sell_position_effectivization_time": ControllerField(
        name="sell_position_effectivization_time",
        label="Sell Effect. Time (s)",
        type="int",
        required=False,
        hint="Time to effectivize sell positions (default: 60)",
        default=60,
    ),
    "min_buy_price_distance_pct": ControllerField(
        name="min_buy_price_distance_pct",
        label="Min Buy Distance %",
        type="float",
        required=False,
        hint="Min price distance for buys (default: 0.003)",
        default=0.003,
    ),
    "min_sell_price_distance_pct": ControllerField(
        name="min_sell_price_distance_pct",
        label="Min Sell Distance %",
        type="float",
        required=False,
        hint="Min price distance for sells (default: 0.003)",
        default=0.003,
    ),
    "max_active_executors_by_level": ControllerField(
        name="max_active_executors_by_level",
        label="Max Executors/Level",
        type="int",
        required=False,
        hint="Max active executors per level (default: 4)",
        default=4,
    ),
    "tick_mode": ControllerField(
        name="tick_mode",
        label="Tick Mode",
        type="bool",
        required=False,
        hint="Enable tick-based updates",
        default=False,
    ),
    "total_amount_quote": ControllerField(
        name="total_amount_quote",
        label="Total Amount (Quote)",
        type="float",
        required=False,
        hint="Total amount in quote currency (e.g. 500 USDT)",
        default=100,
    ),
    "open_order_type": ControllerField(
        name="open_order_type",
        label="Open Order Type",
        type="str",
        required=False,
        hint="Order type for opening (LIMIT, LIMIT_MAKER, MARKET)",
        default="LIMIT",
    ),
    "position_mode": ControllerField(
        name="position_mode",
        label="Position Mode",
        type="str",
        required=False,
        hint="Position mode (HEDGE, ONEWAY)",
        default="HEDGE",
    ),
}


# Field display order
FIELD_ORDER: List[str] = [
    "id",
    "connector_name",
    "trading_pair",
    "leverage",
    "total_amount_quote",
    "portfolio_allocation",
    "position_mode",
    "target_base_pct",
    "min_base_pct",
    "max_base_pct",
    "buy_spreads",
    "sell_spreads",
    "buy_amounts_pct",
    "sell_amounts_pct",
    "take_profit",
    "take_profit_order_type",
    "open_order_type",
    "executor_refresh_time",
    "buy_cooldown_time",
    "sell_cooldown_time",
    "buy_position_effectivization_time",
    "sell_position_effectivization_time",
    "min_buy_price_distance_pct",
    "min_sell_price_distance_pct",
    "max_active_executors_by_level",
    "tick_mode",
]


# Wizard steps - prompts only the most important fields
WIZARD_STEPS: List[str] = [
    "connector_name",
    "trading_pair",
    "leverage",
    "portfolio_allocation",
    "base_percentages",  # Combined: target/min/max base pct
    "spreads",  # Combined: buy/sell spreads
    "take_profit",
    "review",
]


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate a PMM Mister configuration.

    Checks:
    - Required fields are present
    - Base percentages are valid (min < target < max)
    - Spreads are properly formatted
    - Values are within reasonable bounds

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required fields
    required = ["connector_name", "trading_pair"]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"

    # Validate base percentages
    min_base = float(config.get("min_base_pct", 0.1))
    target_base = float(config.get("target_base_pct", 0.2))
    max_base = float(config.get("max_base_pct", 0.4))

    if not (0 <= min_base < target_base < max_base <= 1):
        return False, (
            f"Invalid base percentages: require 0 <= min < target < max <= 1. "
            f"Got: min={min_base}, target={target_base}, max={max_base}"
        )

    # Validate portfolio allocation
    allocation = float(config.get("portfolio_allocation", 0.05))
    if not (0 < allocation <= 1):
        return False, f"Portfolio allocation must be between 0 and 1, got: {allocation}"

    # Validate spreads format
    for spread_field in ["buy_spreads", "sell_spreads"]:
        spreads = config.get(spread_field, "")
        if spreads:
            try:
                if isinstance(spreads, str):
                    values = [float(x.strip()) for x in spreads.split(",")]
                else:
                    values = [float(x) for x in spreads]
                if not all(v > 0 for v in values):
                    return False, f"{spread_field} must contain positive values"
            except ValueError:
                return False, f"Invalid format for {spread_field}: {spreads}"

    # Validate take profit
    take_profit = config.get("take_profit")
    if take_profit is not None:
        try:
            tp = float(take_profit)
            if tp <= 0:
                return False, "Take profit must be positive"
        except (ValueError, TypeError):
            return False, f"Invalid take profit value: {take_profit}"

    return True, None


def parse_spreads(spread_str: str) -> List[float]:
    """Parse comma-separated spread string to list of floats."""
    if not spread_str:
        return []
    if isinstance(spread_str, list):
        return [float(x) for x in spread_str]
    return [float(x.strip()) for x in spread_str.split(",")]


def format_spreads(spreads: List[float]) -> str:
    """Format list of spreads to comma-separated string."""
    return ",".join(str(x) for x in spreads)


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    """
    Generate a unique config ID with sequential numbering.

    Format: NNN_pmm_connector_pair
    Example: 001_pmm_binance_BTC-FDUSD

    Args:
        config: The configuration being created
        existing_configs: List of existing configurations

    Returns:
        Generated config ID
    """
    # Get next sequence number
    max_num = 0
    for cfg in existing_configs:
        config_id = cfg.get("id", "")
        if not config_id:
            continue
        parts = config_id.split("_", 1)
        if parts and parts[0].isdigit():
            num = int(parts[0])
            max_num = max(max_num, num)

    next_num = max_num + 1
    seq = str(next_num).zfill(3)

    # Clean connector name
    connector = config.get("connector_name", "unknown")
    conn_clean = connector.replace("_perpetual", "").replace("_spot", "")

    # Get trading pair
    pair = config.get("trading_pair", "UNKNOWN").upper()

    return f"{seq}_pmm_{conn_clean}_{pair}"
