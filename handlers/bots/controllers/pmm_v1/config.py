"""
PMM V1 controller configuration.

Contains defaults, field definitions, and validation for PMM V1 (Pure Market Making) controllers.
Simple market making with spread-based buy/sell orders.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

# Default configuration values
DEFAULTS: Dict[str, Any] = {
    "controller_name": "pmm_v1",
    "controller_type": "generic",
    "id": "",
    "connector_name": "",
    "trading_pair": "",
    "order_amount": "0.001",
    "buy_spreads": [0.0002],
    "sell_spreads": [0.0002],
    "minimum_spread": "-1",
    "order_refresh_time": 30,
    "max_order_age": 1800,
    "order_refresh_tolerance_pct": "-1",
    "filled_order_delay": 60,
    "inventory_skew_enabled": False,
    "inventory_range_multiplier": "1.0",
    "price_ceiling": "-1",
    "price_floor": "-1",
    "manual_kill_switch": False,
    "candles_config": [],
    "initial_positions": [],
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
        hint="e.g. BTC-USDT, ETH-USDT",
    ),
    "order_amount": ControllerField(
        name="order_amount",
        label="Order Amount (Base)",
        type="str",
        required=True,
        hint="Amount per order in base currency (e.g. 0.001 BTC)",
        default="0.001",
    ),
    "buy_spreads": ControllerField(
        name="buy_spreads",
        label="Buy Spreads",
        type="str",
        required=True,
        hint="Spread list (e.g. [0.0002] or [0.001, 0.002])",
        default="[0.0002]",
    ),
    "sell_spreads": ControllerField(
        name="sell_spreads",
        label="Sell Spreads",
        type="str",
        required=True,
        hint="Spread list (e.g. [0.0002] or [0.001, 0.002])",
        default="[0.0002]",
    ),
    "minimum_spread": ControllerField(
        name="minimum_spread",
        label="Minimum Spread",
        type="str",
        required=False,
        hint="Minimum spread override (-1 to disable)",
        default="-1",
    ),
    "order_refresh_time": ControllerField(
        name="order_refresh_time",
        label="Refresh Time (s)",
        type="int",
        required=False,
        hint="Order refresh interval (default: 30)",
        default=30,
    ),
    "max_order_age": ControllerField(
        name="max_order_age",
        label="Max Order Age (s)",
        type="int",
        required=False,
        hint="Maximum order age before cancellation (default: 1800)",
        default=1800,
    ),
    "order_refresh_tolerance_pct": ControllerField(
        name="order_refresh_tolerance_pct",
        label="Refresh Tolerance %",
        type="str",
        required=False,
        hint="Price change tolerance before refresh (-1 to disable)",
        default="-1",
    ),
    "filled_order_delay": ControllerField(
        name="filled_order_delay",
        label="Filled Order Delay (s)",
        type="int",
        required=False,
        hint="Delay after order fill (default: 60)",
        default=60,
    ),
    "inventory_skew_enabled": ControllerField(
        name="inventory_skew_enabled",
        label="Inventory Skew",
        type="bool",
        required=False,
        hint="Enable inventory skew management",
        default=False,
    ),
    "inventory_range_multiplier": ControllerField(
        name="inventory_range_multiplier",
        label="Inventory Range Multiplier",
        type="str",
        required=False,
        hint="Inventory range multiplier (default: 1.0)",
        default="1.0",
    ),
    "price_ceiling": ControllerField(
        name="price_ceiling",
        label="Price Ceiling",
        type="str",
        required=False,
        hint="Upper price limit (-1 to disable)",
        default="-1",
    ),
    "price_floor": ControllerField(
        name="price_floor",
        label="Price Floor",
        type="str",
        required=False,
        hint="Lower price limit (-1 to disable)",
        default="-1",
    ),
    "manual_kill_switch": ControllerField(
        name="manual_kill_switch",
        label="Kill Switch",
        type="bool",
        required=False,
        hint="Manual kill switch to stop trading",
        default=False,
    ),
}


# Field display order
FIELD_ORDER: List[str] = [
    "id",
    "connector_name",
    "trading_pair",
    "order_amount",
    "buy_spreads",
    "sell_spreads",
    "minimum_spread",
    "order_refresh_time",
    "max_order_age",
    "order_refresh_tolerance_pct",
    "filled_order_delay",
    "inventory_skew_enabled",
    "inventory_range_multiplier",
    "price_ceiling",
    "price_floor",
    "manual_kill_switch",
]


# Wizard steps - simplified flow
WIZARD_STEPS: List[str] = [
    "connector_name",
    "trading_pair",
    "order_amount",
    "spreads",  # Single spread applied to both buy/sell
    "review",
]


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate a PMM V1 configuration.

    Checks:
    - Required fields are present
    - Order amount is positive
    - Spreads are valid

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required fields
    required = ["connector_name", "trading_pair"]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"

    # Validate order amount
    order_amount = config.get("order_amount", "0.001")
    try:
        amt = float(order_amount)
        if amt <= 0:
            return False, "Order amount must be positive"
    except (ValueError, TypeError):
        return False, f"Invalid order amount: {order_amount}"

    # Validate spreads
    for spread_field in ["buy_spreads", "sell_spreads"]:
        spreads = config.get(spread_field, [])
        if spreads:
            try:
                values = parse_spreads(spreads)
                if not all(v > 0 for v in values):
                    return False, f"{spread_field} must contain positive values"
            except (ValueError, TypeError):
                return False, f"Invalid format for {spread_field}: {spreads}"

    return True, None


def parse_spreads(spreads) -> List[float]:
    """Parse spreads from various formats to list of floats."""
    if isinstance(spreads, list):
        return [float(x) for x in spreads]
    if isinstance(spreads, str):
        # Handle "[0.0002]" format
        cleaned = spreads.strip().strip("[]")
        if not cleaned:
            return []
        return [float(x.strip()) for x in cleaned.split(",")]
    return [float(spreads)]


def format_spreads(spreads) -> str:
    """Format spreads as list string."""
    if isinstance(spreads, list):
        return str(spreads)
    return str(spreads)


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    """
    Generate a unique config ID with sequential numbering.

    Format: NNN_pv1_connector_pair
    Example: 001_pv1_binance_BTC-USDT

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

    return f"{seq}_pv1_{conn_clean}_{pair}"
