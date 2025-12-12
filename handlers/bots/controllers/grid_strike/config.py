"""
Grid Strike controller configuration.

Contains defaults, field definitions, and validation for grid strike controllers.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField


# Side value mapping
SIDE_LONG = 1
SIDE_SHORT = 2  # Backend expects 2 for SHORT (not -1)

# Order type mapping
ORDER_TYPE_MARKET = 1
ORDER_TYPE_LIMIT = 2
ORDER_TYPE_LIMIT_MAKER = 3

ORDER_TYPE_LABELS = {
    ORDER_TYPE_MARKET: "Market",
    ORDER_TYPE_LIMIT: "Limit",
    ORDER_TYPE_LIMIT_MAKER: "Limit Maker",
}


# Default configuration values
DEFAULTS: Dict[str, Any] = {
    "controller_name": "grid_strike",
    "controller_type": "generic",
    "id": "",
    "connector_name": "",
    "trading_pair": "",
    "side": SIDE_LONG,
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
    "activation_bounds": 0.01,  # 1%
    "keep_position": True,
    "triple_barrier_config": {
        "open_order_type": 3,
        "take_profit": 0.0001,
        "take_profit_order_type": 3,
    },
}


# Field definitions for form
FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(
        name="id",
        label="Config ID",
        type="str",
        required=True,
        hint="Auto-generated with sequence number"
    ),
    "connector_name": ControllerField(
        name="connector_name",
        label="Connector",
        type="str",
        required=True,
        hint="Select from available exchanges"
    ),
    "trading_pair": ControllerField(
        name="trading_pair",
        label="Trading Pair",
        type="str",
        required=True,
        hint="e.g. SOL-FDUSD, BTC-USDT"
    ),
    "side": ControllerField(
        name="side",
        label="Side",
        type="int",
        required=True,
        hint="LONG or SHORT"
    ),
    "leverage": ControllerField(
        name="leverage",
        label="Leverage",
        type="int",
        required=True,
        hint="e.g. 1, 5, 10"
    ),
    "total_amount_quote": ControllerField(
        name="total_amount_quote",
        label="Total Amount (Quote)",
        type="float",
        required=True,
        hint="e.g. 1000 USDT"
    ),
    "start_price": ControllerField(
        name="start_price",
        label="Start Price",
        type="float",
        required=True,
        hint="Auto: -2% from current"
    ),
    "end_price": ControllerField(
        name="end_price",
        label="End Price",
        type="float",
        required=True,
        hint="Auto: +2% from current"
    ),
    "limit_price": ControllerField(
        name="limit_price",
        label="Limit Price",
        type="float",
        required=True,
        hint="Auto: -3% LONG, +3% SHORT"
    ),
    "max_open_orders": ControllerField(
        name="max_open_orders",
        label="Max Open Orders",
        type="int",
        required=False,
        hint="Default: 3",
        default=3
    ),
    "max_orders_per_batch": ControllerField(
        name="max_orders_per_batch",
        label="Max Orders/Batch",
        type="int",
        required=False,
        hint="Default: 1",
        default=1
    ),
    "min_order_amount_quote": ControllerField(
        name="min_order_amount_quote",
        label="Min Order Amount",
        type="float",
        required=False,
        hint="Default: 6",
        default=6
    ),
    "min_spread_between_orders": ControllerField(
        name="min_spread_between_orders",
        label="Min Spread",
        type="float",
        required=False,
        hint="Default: 0.0002",
        default=0.0002
    ),
    "order_frequency": ControllerField(
        name="order_frequency",
        label="Order Frequency",
        type="int",
        required=False,
        hint="Seconds between order placement (default: 3)",
        default=3
    ),
    "take_profit": ControllerField(
        name="take_profit",
        label="Take Profit",
        type="float",
        required=False,
        hint="Default: 0.0001",
        default=0.0001
    ),
    "keep_position": ControllerField(
        name="keep_position",
        label="Keep Position",
        type="bool",
        required=False,
        hint="Keep position open after grid completion",
        default=True
    ),
    "activation_bounds": ControllerField(
        name="activation_bounds",
        label="Activation Bounds",
        type="float",
        required=False,
        hint="Price distance to activate (default: 0.01 = 1%)",
        default=0.01
    ),
    "open_order_type": ControllerField(
        name="open_order_type",
        label="Open Order Type",
        type="int",
        required=False,
        hint="Order type for opening positions",
        default=ORDER_TYPE_LIMIT_MAKER
    ),
    "take_profit_order_type": ControllerField(
        name="take_profit_order_type",
        label="TP Order Type",
        type="int",
        required=False,
        hint="Order type for take profit",
        default=ORDER_TYPE_LIMIT_MAKER
    ),
}


# Field display order
FIELD_ORDER: List[str] = [
    "id", "connector_name", "trading_pair", "side", "leverage",
    "total_amount_quote", "start_price", "end_price", "limit_price",
    "max_open_orders", "max_orders_per_batch", "order_frequency",
    "min_order_amount_quote", "min_spread_between_orders", "take_profit",
    "open_order_type", "take_profit_order_type", "keep_position", "activation_bounds"
]


# Wizard steps
WIZARD_STEPS: List[str] = [
    "connector_name",
    "trading_pair",
    "side",
    "leverage",
    "total_amount_quote",
    "prices",
    "take_profit",
    "review",
]


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate a grid strike configuration.

    Checks:
    - Required fields are present
    - Price ordering is correct based on side

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required fields
    required = ["connector_name", "trading_pair", "start_price", "end_price", "limit_price"]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"

    side = config.get("side", SIDE_LONG)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)

    # Validate price ordering
    if side == SIDE_LONG:
        # LONG: limit_price < start_price < end_price
        if not (limit_price < start_price < end_price):
            return False, (
                f"Invalid prices for LONG: require limit < start < end. "
                f"Got: {limit_price:.6g} < {start_price:.6g} < {end_price:.6g}"
            )
    else:
        # SHORT: end_price < start_price < limit_price
        if not (end_price < start_price < limit_price):
            return False, (
                f"Invalid prices for SHORT: require end < start < limit. "
                f"Got: {end_price:.6g} < {start_price:.6g} < {limit_price:.6g}"
            )

    return True, None


def calculate_auto_prices(
    current_price: float,
    side: int,
    start_pct: float = 0.02,
    end_pct: float = 0.02,
    limit_pct: float = 0.03
) -> Tuple[float, float, float]:
    """
    Calculate start, end, and limit prices based on current price and side.

    For LONG:
        - start_price: current_price - 2%
        - end_price: current_price + 2%
        - limit_price: current_price - 3%

    For SHORT:
        - start_price: current_price + 2%
        - end_price: current_price - 2%
        - limit_price: current_price + 3%

    Returns:
        Tuple of (start_price, end_price, limit_price)
    """
    if side == SIDE_LONG:
        start_price = current_price * (1 - start_pct)
        end_price = current_price * (1 + end_pct)
        limit_price = current_price * (1 - limit_pct)
    else:  # SHORT
        start_price = current_price * (1 + start_pct)
        end_price = current_price * (1 - end_pct)
        limit_price = current_price * (1 + limit_pct)

    return (
        round(start_price, 6),
        round(end_price, 6),
        round(limit_price, 6)
    )


def generate_id(
    config: Dict[str, Any],
    existing_configs: List[Dict[str, Any]]
) -> str:
    """
    Generate a unique config ID with sequential numbering.

    Format: NNN_gs_connector_pair
    Example: 001_gs_binance_SOL-USDT

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

    return f"{seq}_gs_{conn_clean}_{pair}"
