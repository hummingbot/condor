"""
StatArb V2 configuration for Condor.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField


# Default configuration values
DEFAULTS: Dict[str, Any] = {
    "controller_name": "stat_arb_v2",
    "controller_type": "generic",
    "connector_name": "",
    "trading_pair_dominant": "",
    "trading_pair_hedge": "",
    "total_amount_quote": 1000,
    "leverage": 20,
    "position_mode": "HEDGE",
    "interval": "5m",
    "lookback_period": 100,
    "entry_threshold": 2.0,
    "take_profit": 0.0008,
    "tp_global": 0.01,
    "sl_global": 0.05,
    "min_amount_quote": 10,
    "quoter_spread": 0.0001,
    "quoter_cooldown": 30,
    "quoter_refresh": 10,
    "max_orders_placed_per_side": 2,
    "max_orders_filled_per_side": 2,
    "max_position_deviation": 0.1,
    "use_dynamic_hedge_ratio": True,
    "pos_hedge_ratio": 1.0,
    "max_dynamic_hedge_ratio": 3.0,
    "min_dynamic_hedge_ratio": 0.2,
    "min_r_squared": 0.70,
    "adf_pvalue_threshold": 0.05,
}

# Field definitions for the Condor wizard
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
        label="Exchange connector",
        type="str",
        required=True,
        hint="e.g., binance_perpetual, bybit_perpetual, etc.",
    ),
    "trading_pair_dominant": ControllerField(
        name="trading_pair_dominant",
        label="Dominant trading pair",
        type="str",
        required=True,
        hint="e.g., SOL-USDT, BTC-USDT",
    ),
    "trading_pair_hedge": ControllerField(
        name="trading_pair_hedge",
        label="Hedge trading pair",
        type="str",
        required=True,
        hint="e.g., XRP-USDT, ETH-USDT",
    ),
    "total_amount_quote": ControllerField(
        name="total_amount_quote",
        label="Total capital (quote)",
        type="float",
        required=True,
        hint="Total amount in quote currency (USDT, USDC, etc.)",
        default=1000,
    ),
    "leverage": ControllerField(
        name="leverage",
        label="Leverage",
        type="int",
        required=True,
        hint="Leverage for perpetual futures (1 for spot)",
        default=20,
    ),
    "position_mode": ControllerField(
        name="position_mode",
        label="Position mode",
        type="str",
        required=False,
        hint="HEDGE or ONEWAY",
        default="HEDGE",
    ),
    "interval": ControllerField(
        name="interval",
        label="Candle interval",
        type="str",
        required=False,
        hint="e.g., 1m, 5m, 15m",
        default="1m",
    ),
    "lookback_period": ControllerField(
        name="lookback_period",
        label="Lookback candles",
        type="int",
        required=False,
        hint="Number of candles for regression and z-score",
        default=300,
    ),
    "entry_threshold": ControllerField(
        name="entry_threshold",
        label="Entry threshold (z-score)",
        type="float",
        required=False,
        hint="Z-score threshold to trigger a trade (2.0 = 95% quantile)",
        default=2.0,
    ),
    "take_profit": ControllerField(
        name="take_profit",
        label="Take profit per leg",
        type="float",
        required=False,
        hint="Percent profit to close a leg (e.g., 0.0008 = 0.08%)",
        default=0.0008,
    ),
    "tp_global": ControllerField(
        name="tp_global",
        label="Global take profit",
        type="float",
        required=False,
        hint="Pair PnL% to close everything (e.g., 0.01 = 1%)",
        default=0.01,
    ),
    "sl_global": ControllerField(
        name="sl_global",
        label="Global stop loss",
        type="float",
        required=False,
        hint="Pair PnL% loss to close everything (e.g., 0.05 = 5%)",
        default=0.05,
    ),
    "min_amount_quote": ControllerField(
        name="min_amount_quote",
        label="Min order amount (quote)",
        type="float",
        required=False,
        hint="Minimum notional per order in quote currency",
        default=10,
    ),
    "quoter_spread": ControllerField(
        name="quoter_spread",
        label="Quoter spread",
        type="float",
        required=False,
        hint="Offset from mid price for limit orders (e.g., 0.0001 = 0.01%)",
        default=0.0001,
    ),
    "quoter_cooldown": ControllerField(
        name="quoter_cooldown",
        label="Cooldown after fill (s)",
        type="int",
        required=False,
        hint="Seconds to wait before removing a filled executor",
        default=30,
    ),
    "quoter_refresh": ControllerField(
        name="quoter_refresh",
        label="Refresh time for unfilled (s)",
        type="int",
        required=False,
        hint="Seconds before cancelling and re-pricing an unfilled order",
        default=10,
    ),
    "max_orders_placed_per_side": ControllerField(
        name="max_orders_placed_per_side",
        label="Max pending orders per side",
        type="int",
        required=False,
        hint="Maximum number of unfilled orders per leg",
        default=2,
    ),
    "max_orders_filled_per_side": ControllerField(
        name="max_orders_filled_per_side",
        label="Max filled orders per side",
        type="int",
        required=False,
        hint="Maximum number of filled (active) positions per leg",
        default=2,
    ),
    "max_position_deviation": ControllerField(
        name="max_position_deviation",
        label="Max position deviation",
        type="float",
        required=False,
        hint="Imbalance threshold that blocks one leg (0.1 = 10%)",
        default=0.1,
    ),
    "use_dynamic_hedge_ratio": ControllerField(
        name="use_dynamic_hedge_ratio",
        label="Use dynamic hedge ratio",
        type="bool",
        required=False,
        hint="Size hedge leg according to OLS beta",
        default=True,
    ),
    "pos_hedge_ratio": ControllerField(
        name="pos_hedge_ratio",
        label="Fixed hedge ratio (if dynamic off)",
        type="float",
        required=False,
        hint="Hedge notional / dominant notional",
        default=1.0,
    ),
    "max_dynamic_hedge_ratio": ControllerField(
        name="max_dynamic_hedge_ratio",
        label="Max dynamic ratio",
        type="float",
        required=False,
        hint="Cap for 1/beta",
        default=3.0,
    ),
    "min_dynamic_hedge_ratio": ControllerField(
        name="min_dynamic_hedge_ratio",
        label="Min dynamic ratio",
        type="float",
        required=False,
        hint="Floor for 1/beta",
        default=0.2,
    ),
    "min_r_squared": ControllerField(
        name="min_r_squared",
        label="Min R² to trade",
        type="float",
        required=False,
        hint="Minimum coefficient of determination to allow signals",
        default=0.70,
    ),
    "adf_pvalue_threshold": ControllerField(
        name="adf_pvalue_threshold",
        label="ADF p-value threshold",
        type="float",
        required=False,
        hint="Maximum p-value for stationarity (lower is better)",
        default=0.05,
    ),
}

# Field order in the wizard
FIELD_ORDER: List[str] = [
    "id",
    "connector_name",
    "trading_pair_dominant",
    "trading_pair_hedge",
    "total_amount_quote",
    "leverage",
    "position_mode",
    "interval",
    "lookback_period",
    "entry_threshold",
    "take_profit",
    "tp_global",
    "sl_global",
    "min_amount_quote",
    "quoter_spread",
    "quoter_cooldown",
    "quoter_refresh",
    "max_orders_placed_per_side",
    "max_orders_filled_per_side",
    "max_position_deviation",
    "use_dynamic_hedge_ratio",
    "pos_hedge_ratio",
    "max_dynamic_hedge_ratio",
    "min_dynamic_hedge_ratio",
    "min_r_squared",
    "adf_pvalue_threshold",
]

# Wizard steps – minimal required for quick setup
WIZARD_STEPS: List[str] = [
    "connector_name",
    "trading_pair_dominant",
    "trading_pair_hedge",
    "total_amount_quote",
    "leverage",
    "position_mode",
    "entry_threshold",
    "take_profit",
    "tp_global",
    "review",
]


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate the configuration."""
    # Check required fields
    required = ["connector_name", "trading_pair_dominant", "trading_pair_hedge"]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"

    # Check that total_amount_quote > 0
    total = config.get("total_amount_quote", 0)
    if total <= 0:
        return False, "total_amount_quote must be > 0"

    # Check entry_threshold positive
    entry = config.get("entry_threshold", 0)
    if entry <= 0:
        return False, "entry_threshold must be positive"

    # Check take_profit positive
    tp = config.get("take_profit", 0)
    if tp <= 0:
        return False, "take_profit must be positive"

    return True, None


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    """Generate unique config ID with sequence number."""
    max_num = 0
    for cfg in existing_configs:
        cid = cfg.get("id", "")
        if cid and cid.split("_")[0].isdigit():
            num = int(cid.split("_")[0])
            max_num = max(max_num, num)
    next_num = max_num + 1
    seq = str(next_num).zfill(3)

    connector = config.get("connector_name", "unknown").replace("_perpetual", "").replace("_spot", "")
    dom = config.get("trading_pair_dominant", "UNKNOWN").split("-")[0]
    hedge = config.get("trading_pair_hedge", "UNKNOWN").split("-")[0]
    return f"{seq}_statarb_{connector}_{dom}_{hedge}"
