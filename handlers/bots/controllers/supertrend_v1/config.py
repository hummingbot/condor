"""
SuperTrend V1 controller configuration.

Directional trading strategy using SuperTrend indicator:
- LONG  when SuperTrend direction == UP  AND price is within percentage_threshold of the line
- SHORT when SuperTrend direction == DOWN AND price is within percentage_threshold of the line
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

DEFAULTS: Dict[str, Any] = {
    "controller_name": "supertrend_v1",
    "controller_type": "directional_trading",
    "id": "",
    # Base fields
    "manual_kill_switch": None,
    "candles_config": [],
    # Connector
    "connector_name": "",
    "trading_pair": "",
    "total_amount_quote": 1000,
    "leverage": 1,
    "position_mode": "HEDGE",
    # DirectionalTradingControllerConfigBase fields
    "max_executors_per_side": 1,
    "cooldown_time": 60,
    "stop_loss": 0.05,
    "take_profit": 0.03,
    "take_profit_order_type": 2,
    "time_limit": None,
    # Trailing stop
    "trailing_stop": {
        "activation_price": 0.015,
        "trailing_delta": 0.005,
    },
    # Candles config
    "candles_connector": "",
    "candles_trading_pair": "",
    "interval": "3m",
    # SuperTrend parameters
    "length": 20,
    "multiplier": 4.0,
    "percentage_threshold": 0.01,
}

FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(name="id", label="Config ID", type="str", required=True, hint="Auto-generated"),
    "connector_name": ControllerField(name="connector_name", label="Connector", type="str", required=True, hint="Exchange connector"),
    "trading_pair": ControllerField(name="trading_pair", label="Trading Pair", type="str", required=True, hint="e.g. BTC-USDT"),
    "leverage": ControllerField(name="leverage", label="Leverage", type="int", required=True, hint="e.g. 1, 5, 10", default=1),
    "position_mode": ControllerField(name="position_mode", label="Position Mode", type="str", required=False, hint="HEDGE or ONEWAY", default="HEDGE"),
    "total_amount_quote": ControllerField(name="total_amount_quote", label="Total Amount (Quote)", type="float", required=True, hint="e.g. 1000 USDT"),
    "max_executors_per_side": ControllerField(name="max_executors_per_side", label="Max Executors/Side", type="int", required=False, hint="Max concurrent positions per side", default=1),
    "cooldown_time": ControllerField(name="cooldown_time", label="Cooldown Time (s)", type="int", required=False, hint="Seconds between new executors", default=60),
    "stop_loss": ControllerField(name="stop_loss", label="Stop Loss", type="float", required=False, hint="e.g. 0.05 = 5%", default=0.05),
    "take_profit": ControllerField(name="take_profit", label="Take Profit", type="float", required=False, hint="e.g. 0.03 = 3%", default=0.03),
    "take_profit_order_type": ControllerField(name="take_profit_order_type", label="TP Order Type", type="int", required=False, hint="1=Market, 2=Limit, 3=Limit Maker", default=2),
    "time_limit": ControllerField(name="time_limit", label="Time Limit (s)", type="int", required=False, hint="Max executor lifetime (None = no limit)", default=None),
    "trailing_stop_activation": ControllerField(name="trailing_stop_activation", label="Trailing Stop Activation", type="float", required=False, hint="e.g. 0.015 = 1.5%", default=0.015),
    "trailing_stop_delta": ControllerField(name="trailing_stop_delta", label="Trailing Stop Delta", type="float", required=False, hint="e.g. 0.005 = 0.5%", default=0.005),
    "candles_connector": ControllerField(name="candles_connector", label="Candles Connector", type="str", required=False, hint="Leave empty to use same as connector", default=""),
    "candles_trading_pair": ControllerField(name="candles_trading_pair", label="Candles Pair", type="str", required=False, hint="Leave empty to use same as trading pair", default=""),
    "interval": ControllerField(name="interval", label="Candle Interval", type="str", required=True, hint="e.g. 1m, 3m, 5m, 1h", default="3m"),
    "length": ControllerField(name="length", label="SuperTrend Length", type="int", required=False, hint="ATR period (default: 20)", default=20),
    "multiplier": ControllerField(name="multiplier", label="SuperTrend Multiplier", type="float", required=False, hint="ATR multiplier (default: 4.0)", default=4.0),
    "percentage_threshold": ControllerField(name="percentage_threshold", label="% Threshold", type="float", required=False, hint="Max distance from ST line to signal (e.g. 0.01 = 1%)", default=0.01),
    "manual_kill_switch": ControllerField(name="manual_kill_switch", label="Kill Switch", type="bool", required=False, hint="Manual kill switch", default=None),
}

FIELD_ORDER: List[str] = [
    "id", "connector_name", "trading_pair", "leverage", "position_mode",
    "total_amount_quote", "max_executors_per_side", "cooldown_time",
    "stop_loss", "take_profit", "take_profit_order_type", "time_limit",
    "trailing_stop_activation", "trailing_stop_delta",
    "candles_connector", "candles_trading_pair", "interval",
    "length", "multiplier", "percentage_threshold",
    "manual_kill_switch",
]

EDITABLE_FIELDS: List[str] = [
    "connector_name", "trading_pair", "total_amount_quote", "leverage",
    "max_executors_per_side", "cooldown_time",
    "stop_loss", "take_profit", "take_profit_order_type",
    "trailing_stop_activation", "trailing_stop_delta",
    "candles_connector", "candles_trading_pair", "interval",
    "length", "multiplier", "percentage_threshold",
]


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    required = ["connector_name", "trading_pair", "total_amount_quote"]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"
    return True, None


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    max_num = 0
    for cfg in existing_configs:
        parts = cfg.get("id", "").split("_", 1)
        if parts and parts[0].isdigit():
            max_num = max(max_num, int(parts[0]))
    seq = str(max_num + 1).zfill(3)
    connector = config.get("connector_name", "unknown").replace("_perpetual", "").replace("_spot", "")
    pair = config.get("trading_pair", "UNKNOWN").upper()
    return f"{seq}_st_{connector}_{pair}"
