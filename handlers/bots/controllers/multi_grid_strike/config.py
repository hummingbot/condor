"""
Multi Grid Strike controller configuration.

Contains defaults, field definitions, and validation for multi grid strike controllers.

MultiGridStrike supports multiple grids on the same pair, each with its own
price range and capital allocation (expressed as a percentage of total_amount_quote).
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

# Side value mapping (same as grid_strike)
SIDE_LONG = 1
SIDE_SHORT = 2

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
    "controller_name": "multi_grid_strike",
    "controller_type": "generic",
    "id": "",
    "connector_name": "",
    "trading_pair": "",
    "leverage": 20,
    "position_mode": "HEDGE",
    "total_amount_quote": 1000,
    "min_order_amount_quote": 5,
    "min_spread_between_orders": 0.001,
    "max_open_orders": 2,
    "max_orders_per_batch": 1,
    "order_frequency": 3,
    "activation_bounds": None,
    "keep_position": False,
    "triple_barrier_config": {
        "open_order_type": ORDER_TYPE_LIMIT_MAKER,
        "take_profit": 0.001,
        "take_profit_order_type": ORDER_TYPE_LIMIT_MAKER,
        "stop_loss": None,
        "stop_loss_order_type": ORDER_TYPE_MARKET,
        "time_limit": None,
        "time_limit_order_type": ORDER_TYPE_MARKET,
        "trailing_stop": None,
    },
    # grids is a list of GridConfig dicts - empty by default, user adds them
    "grids": [],
    # Fields from ControllerConfigBase
    "manual_kill_switch": False,
    "candles_config": [],
    "initial_positions": [],
}

# Field definitions for the configuration form
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
        hint="e.g. WLD-USDT, BTC-USDT",
    ),
    "leverage": ControllerField(
        name="leverage",
        label="Leverage",
        type="int",
        required=True,
        hint="e.g. 1, 10, 20",
        default=20,
    ),
    "position_mode": ControllerField(
        name="position_mode",
        label="Position Mode",
        type="str",
        required=False,
        hint="HEDGE (recommended for multi-grid) or ONEWAY",
        default="HEDGE",
    ),
    "total_amount_quote": ControllerField(
        name="total_amount_quote",
        label="Total Amount (Quote)",
        type="float",
        required=True,
        hint="Total capital in USDT distributed across all grids",
    ),
    "max_open_orders": ControllerField(
        name="max_open_orders",
        label="Max Open Orders",
        type="int",
        required=False,
        hint="Max open orders per grid (default: 2)",
        default=2,
    ),
    "max_orders_per_batch": ControllerField(
        name="max_orders_per_batch",
        label="Max Orders/Batch",
        type="int",
        required=False,
        hint="Default: 1",
        default=1,
    ),
    "min_order_amount_quote": ControllerField(
        name="min_order_amount_quote",
        label="Min Order Amount",
        type="float",
        required=False,
        hint="Default: 5",
        default=5,
    ),
    "min_spread_between_orders": ControllerField(
        name="min_spread_between_orders",
        label="Min Spread Between Orders",
        type="float",
        required=False,
        hint="Default: 0.001",
        default=0.001,
    ),
    "order_frequency": ControllerField(
        name="order_frequency",
        label="Order Frequency (s)",
        type="int",
        required=False,
        hint="Seconds between order placement (default: 3)",
        default=3,
    ),
    "take_profit": ControllerField(
        name="take_profit",
        label="Take Profit",
        type="float",
        required=False,
        hint="TP per level (default: 0.001 = 0.1%)",
        default=0.001,
    ),
    "keep_position": ControllerField(
        name="keep_position",
        label="Keep Position",
        type="bool",
        required=False,
        hint="Keep position open after grid completion",
        default=False,
    ),
    "activation_bounds": ControllerField(
        name="activation_bounds",
        label="Activation Bounds",
        type="float",
        required=False,
        hint="Price distance to activate orders (None = disabled)",
        default=None,
    ),
}

# Field display order
FIELD_ORDER: List[str] = [
    "id",
    "connector_name",
    "trading_pair",
    "leverage",
    "position_mode",
    "total_amount_quote",
    "max_open_orders",
    "max_orders_per_batch",
    "order_frequency",
    "min_order_amount_quote",
    "min_spread_between_orders",
    "take_profit",
    "keep_position",
    "activation_bounds",
]

# Wizard steps
WIZARD_STEPS: List[str] = [
    "connector_name",
    "trading_pair",
    "leverage",
    "total_amount_quote",
    "take_profit",
    "review",
]

# Editable fields shown in edit view
EDITABLE_FIELDS: List[str] = [
    "connector_name",
    "trading_pair",
    "total_amount_quote",
    "leverage",
    "position_mode",
    "take_profit",
    "min_spread_between_orders",
    "min_order_amount_quote",
    "max_open_orders",
    "activation_bounds",
    "keep_position",
]


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate a multi grid strike configuration.

    Checks:
    - Required fields are present
    - Grids list is valid (if provided)
    - Each grid has correct price ordering based on side
    - Sum of amount_quote_pct <= 1.0

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required top-level fields
    required = ["connector_name", "trading_pair", "total_amount_quote"]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"

    grids = config.get("grids", [])

    if grids:
        total_pct = 0.0
        for i, grid in enumerate(grids):
            grid_id = grid.get("grid_id", f"grid_{i}")
            side = grid.get("side", SIDE_LONG)
            start_price = grid.get("start_price", 0)
            end_price = grid.get("end_price", 0)
            limit_price = grid.get("limit_price", 0)
            pct = grid.get("amount_quote_pct", 0)

            if side == SIDE_LONG:
                if not (limit_price < start_price < end_price):
                    return False, (
                        f"Grid '{grid_id}': Invalid prices for LONG "
                        f"(require limit < start < end). "
                        f"Got: {limit_price} < {start_price} < {end_price}"
                    )
            else:
                if not (start_price < end_price < limit_price):
                    return False, (
                        f"Grid '{grid_id}': Invalid prices for SHORT "
                        f"(require start < end < limit). "
                        f"Got: {start_price} < {end_price} < {limit_price}"
                    )

            total_pct += pct

        if total_pct > 1.0 + 1e-9:
            return False, (
                f"Sum of amount_quote_pct across grids ({total_pct:.2f}) "
                f"exceeds 1.0 (100%). Reduce grid allocations."
            )

    return True, None


def calculate_auto_prices_for_grid(
    current_price: float,
    side: int,
    base_pct: float = 0.02,
    limit_pct: float = 0.03,
) -> Tuple[float, float, float]:
    """
    Calculate start, end, and limit prices for a single grid.

    Uses the same 3:1 ratio logic as grid_strike.

    Returns:
        Tuple of (start_price, end_price, limit_price)
    """
    if side == SIDE_LONG:
        start_price = current_price * (1 - base_pct)
        end_price = current_price * (1 + base_pct * 3)
        limit_price = current_price * (1 - limit_pct)
    else:
        start_price = current_price * (1 - base_pct * 3)
        end_price = current_price * (1 + base_pct)
        limit_price = current_price * (1 + limit_pct)

    return (round(start_price, 6), round(end_price, 6), round(limit_price, 6))


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    """
    Generate a unique config ID with sequential numbering.

    Format: NNN_mgs_connector_pair
    Example: 001_mgs_binance_WLD-USDT

    Args:
        config: The configuration being created
        existing_configs: List of existing configurations

    Returns:
        Generated config ID
    """
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

    connector = config.get("connector_name", "unknown")
    conn_clean = connector.replace("_perpetual", "").replace("_spot", "")
    pair = config.get("trading_pair", "UNKNOWN").upper()

    return f"{seq}_mgs_{conn_clean}_{pair}"






# ============================================
# Multi-Grid Strategy Types (NEW)
# ============================================
GRID_TYPES = {
    "accumulation_distribution": {
        "label": "📈 Accumulation + Distribution",
        "description": "Buy in low & sell in high range",
        "default_grids": 2,
        "max_grids": 6,
        "min_grids": 2,
    },
    "range_trading": {
        "label": "🔄 Range Trading",
        "description": "Alternating LONG/SHORT grids in a range",
        "default_grids": 4,
        "max_grids": 20,
        "min_grids": 2,
    },
    "pyramid": {
        "label": "🔺 Pyramid DCA",
        "description": "Gradual weighted accumulation",
        "default_grids": 4,
        "max_grids": 8,
        "min_grids": 2,
    },
}

# Wizard steps for MultiGrid Strike
MGS_WIZARD_STEPS: List[str] = [
    "connector_name",
    "trading_pair",
    "grid_type",
    "num_grids",
    "leverage",  # only for perpetual
    "total_amount_quote",
    "review",
]
