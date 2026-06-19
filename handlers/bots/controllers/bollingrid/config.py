"""
Bollinger Grid controller configuration.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

DEFAULTS: Dict[str, Any] = {
    "controller_name": "bollingrid",
    "controller_type": "directional_trading",
    "id": "",
    "connector_name": "",
    "trading_pair": "",
    "leverage": 1,
    "position_mode": "HEDGE",
    "total_amount_quote": 1000,
    "candles_connector": None,
    "candles_trading_pair": None,
    "interval": "5m",
    "bb_length": 100,
    "bb_std": 2.0,
    "bb_long_threshold": 0.0,
    "bb_short_threshold": 1.0,
    "grid_start_price_coefficient": 0.25,
    "grid_end_price_coefficient": 0.75,
    "grid_limit_price_coefficient": 0.35,
    "min_spread_between_orders": 0.005,
    "order_frequency": 2,
    "max_orders_per_batch": 1,
    "min_order_amount_quote": 6,
    "max_open_orders": 5,
    "stop_loss": 0.05,
    "take_profit": 0.03,
    "trailing_stop_activation": 0.015,
    "trailing_stop_delta": 0.005,
}

FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(name="id", label="Config ID", type="str", required=True),
    "connector_name": ControllerField(name="connector_name", label="Exchange", type="str", required=True),
    "trading_pair": ControllerField(name="trading_pair", label="Trading Pair", type="str", required=True),
    "leverage": ControllerField(name="leverage", label="Leverage", type="int", required=False, default=1),
    "position_mode": ControllerField(name="position_mode", label="Position Mode", type="str", required=False, default="HEDGE"),
    "total_amount_quote": ControllerField(name="total_amount_quote", label="Total Amount (USDT)", type="float", required=True),
    "candles_connector": ControllerField(name="candles_connector", label="Candles Connector", type="str", required=False),
    "candles_trading_pair": ControllerField(name="candles_trading_pair", label="Candles Pair", type="str", required=False),
    "interval": ControllerField(name="interval", label="Interval", type="str", required=False, default="5m"),
    "bb_length": ControllerField(name="bb_length", label="BB Length", type="int", required=False, default=100),
    "bb_std": ControllerField(name="bb_std", label="BB Std Dev", type="float", required=False, default=2.0),
    "bb_long_threshold": ControllerField(name="bb_long_threshold", label="BB Long Threshold", type="float", required=False, default=0.0),
    "bb_short_threshold": ControllerField(name="bb_short_threshold", label="BB Short Threshold", type="float", required=False, default=1.0),
    "grid_start_price_coefficient": ControllerField(name="grid_start_price_coefficient", label="Start Price Coeff", type="float", required=False, default=0.25),
    "grid_end_price_coefficient": ControllerField(name="grid_end_price_coefficient", label="End Price Coeff", type="float", required=False, default=0.75),
    "grid_limit_price_coefficient": ControllerField(name="grid_limit_price_coefficient", label="Limit Price Coeff", type="float", required=False, default=0.35),
    "min_spread_between_orders": ControllerField(name="min_spread_between_orders", label="Min Spread", type="float", required=False, default=0.005),
    "order_frequency": ControllerField(name="order_frequency", label="Order Frequency (s)", type="int", required=False, default=2),
    "max_orders_per_batch": ControllerField(name="max_orders_per_batch", label="Max Orders/Batch", type="int", required=False, default=1),
    "min_order_amount_quote": ControllerField(name="min_order_amount_quote", label="Min Order Amount", type="float", required=False, default=6),
    "max_open_orders": ControllerField(name="max_open_orders", label="Max Open Orders", type="int", required=False, default=5),
    "stop_loss": ControllerField(name="stop_loss", label="Stop Loss", type="float", required=False, default=0.05),
    "take_profit": ControllerField(name="take_profit", label="Take Profit", type="float", required=False, default=0.03),
    "trailing_stop_activation": ControllerField(name="trailing_stop_activation", label="TS Activation", type="float", required=False, default=0.015),
    "trailing_stop_delta": ControllerField(name="trailing_stop_delta", label="TS Delta", type="float", required=False, default=0.005),
}

FIELD_ORDER: List[str] = [
    "id", "connector_name", "trading_pair", "leverage", "position_mode",
    "total_amount_quote", "candles_connector", "candles_trading_pair", "interval",
    "bb_length", "bb_std", "bb_long_threshold", "bb_short_threshold",
    "grid_start_price_coefficient", "grid_end_price_coefficient", "grid_limit_price_coefficient",
    "min_spread_between_orders", "order_frequency", "max_orders_per_batch",
    "min_order_amount_quote", "max_open_orders", "stop_loss", "take_profit",
    "trailing_stop_activation", "trailing_stop_delta",
]

EDITABLE_FIELDS: List[str] = FIELD_ORDER.copy()


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if not config.get("connector_name"):
        return False, "Missing exchange"
    if not config.get("trading_pair"):
        return False, "Missing trading pair"
    if config.get("total_amount_quote", 0) <= 0:
        return False, "Total amount must be positive"
    return True, None


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    max_num = 0
    for cfg in existing_configs:
        cfg_id = cfg.get("id", "")
        if cfg_id and cfg_id[:3].isdigit():
            max_num = max(max_num, int(cfg_id[:3]))
    seq = str(max_num + 1).zfill(3)
    connector = config.get("connector_name", "unknown").replace("_perpetual", "").replace("_spot", "")
    pair = config.get("trading_pair", "UNKNOWN").upper()
    return f"{seq}_bg_{connector}_{pair}"


