"""
Anti-Folla V1 controller configuration.

Crowd-contrarian directional trading strategy using real flow parameters:
- LONG  when composite score >= score_buy_threshold
- SHORT when composite score <= score_sell_threshold

Score is a weighted composite of:
  VWAP position, Donchian breakout, OBV divergence, Order Book Imbalance,
  Volume Spike, Trade Flow (whale activity), Funding Rate (futures only).
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

DEFAULTS: Dict[str, Any] = {
    "controller_name": "anti_folla_v1",
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
    # Futures flag
    "is_perpetual": False,
    # Anti-Folla parameters
    "vwap_period": 20,
    "donchian_period": 20,
    "atr_period": 14,
    "obv_divergence_lookback": 10,
    "volume_spike_threshold": 2.5,
    # Order Book Imbalance
    "enable_order_book_imbalance": True,
    "obi_depth_percentage": 0.02,
    "obi_buy_threshold": 1.5,
    "obi_sell_threshold": 0.67,
    # Score thresholds
    "score_buy_threshold": 50.0,
    "score_sell_threshold": -50.0,
    # Weights (must sum to 100)
    "weight_vwap": 15,
    "weight_donchian": 10,
    "weight_obv": 15,
    "weight_obi": 20,
    "weight_volume_spike": 10,
    "weight_trade_flow": 15,
    "weight_funding": 15,
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
    "is_perpetual": ControllerField(name="is_perpetual", label="Is Perpetual/Futures", type="bool", required=False, hint="Enable funding rate signal (True for perp/futures)", default=False),
    # Anti-Folla parameters
    "vwap_period": ControllerField(name="vwap_period", label="VWAP Period", type="int", required=False, hint="Rolling VWAP window", default=20),
    "donchian_period": ControllerField(name="donchian_period", label="Donchian Period", type="int", required=False, hint="Donchian Channel period (with shift)", default=20),
    "atr_period": ControllerField(name="atr_period", label="ATR Period", type="int", required=False, hint="ATR period", default=14),
    "obv_divergence_lookback": ControllerField(name="obv_divergence_lookback", label="OBV Lookback", type="int", required=False, hint="Lookback for OBV divergence detection", default=10),
    "volume_spike_threshold": ControllerField(name="volume_spike_threshold", label="Volume Spike Threshold", type="float", required=False, hint="Volume multiplier to detect spike (e.g. 2.5 = 2.5x avg)", default=2.5),
    "enable_order_book_imbalance": ControllerField(name="enable_order_book_imbalance", label="Enable OBI", type="bool", required=False, hint="Enable Order Book Imbalance analysis", default=True),
    "obi_depth_percentage": ControllerField(name="obi_depth_percentage", label="OBI Depth %", type="float", required=False, hint="Price depth from best bid for OBI (e.g. 0.02 = 2%)", default=0.02),
    "obi_buy_threshold": ControllerField(name="obi_buy_threshold", label="OBI Buy Threshold", type="float", required=False, hint="OBI ratio >= this → buy pressure (default 1.5)", default=1.5),
    "obi_sell_threshold": ControllerField(name="obi_sell_threshold", label="OBI Sell Threshold", type="float", required=False, hint="OBI ratio <= this → sell pressure (default 0.67)", default=0.67),
    "score_buy_threshold": ControllerField(name="score_buy_threshold", label="Score BUY Threshold", type="float", required=False, hint="Min composite score to trigger BUY (default 50)", default=50.0),
    "score_sell_threshold": ControllerField(name="score_sell_threshold", label="Score SELL Threshold", type="float", required=False, hint="Max composite score to trigger SELL (default -50)", default=-50.0),
    # Weights
    "weight_vwap": ControllerField(name="weight_vwap", label="Weight VWAP", type="float", required=False, hint="Weight for VWAP signal (all weights must sum to 100)", default=15),
    "weight_donchian": ControllerField(name="weight_donchian", label="Weight Donchian", type="float", required=False, hint="Weight for Donchian breakout signal", default=10),
    "weight_obv": ControllerField(name="weight_obv", label="Weight OBV", type="float", required=False, hint="Weight for OBV divergence signal", default=15),
    "weight_obi": ControllerField(name="weight_obi", label="Weight OBI", type="float", required=False, hint="Weight for Order Book Imbalance signal", default=20),
    "weight_volume_spike": ControllerField(name="weight_volume_spike", label="Weight Volume Spike", type="float", required=False, hint="Weight for volume spike signal", default=10),
    "weight_trade_flow": ControllerField(name="weight_trade_flow", label="Weight Trade Flow", type="float", required=False, hint="Weight for whale trade flow signal", default=15),
    "weight_funding": ControllerField(name="weight_funding", label="Weight Funding Rate", type="float", required=False, hint="Weight for funding rate contrarian signal (futures only)", default=15),
    "manual_kill_switch": ControllerField(name="manual_kill_switch", label="Kill Switch", type="bool", required=False, hint="Manual kill switch", default=None),
}

FIELD_ORDER: List[str] = [
    "id", "connector_name", "trading_pair", "leverage", "position_mode",
    "total_amount_quote", "max_executors_per_side", "cooldown_time",
    "stop_loss", "take_profit", "take_profit_order_type", "time_limit",
    "trailing_stop_activation", "trailing_stop_delta",
    "candles_connector", "candles_trading_pair", "interval",
    "is_perpetual",
    "vwap_period", "donchian_period", "atr_period",
    "obv_divergence_lookback", "volume_spike_threshold",
    "enable_order_book_imbalance", "obi_depth_percentage",
    "obi_buy_threshold", "obi_sell_threshold",
    "score_buy_threshold", "score_sell_threshold",
    "weight_vwap", "weight_donchian", "weight_obv", "weight_obi",
    "weight_volume_spike", "weight_trade_flow", "weight_funding",
    "manual_kill_switch",
]

EDITABLE_FIELDS: List[str] = [
    "connector_name", "trading_pair", "total_amount_quote", "leverage",
    "max_executors_per_side", "cooldown_time",
    "stop_loss", "take_profit", "take_profit_order_type",
    "trailing_stop_activation", "trailing_stop_delta",
    "candles_connector", "candles_trading_pair", "interval",
    "is_perpetual",
    "vwap_period", "donchian_period", "atr_period",
    "obv_divergence_lookback", "volume_spike_threshold",
    "enable_order_book_imbalance", "obi_depth_percentage",
    "obi_buy_threshold", "obi_sell_threshold",
    "score_buy_threshold", "score_sell_threshold",
    "weight_vwap", "weight_donchian", "weight_obv", "weight_obi",
    "weight_volume_spike", "weight_trade_flow", "weight_funding",
]


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    required = ["connector_name", "trading_pair", "total_amount_quote"]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"

    weights = [
        config.get("weight_vwap", 15),
        config.get("weight_donchian", 10),
        config.get("weight_obv", 15),
        config.get("weight_obi", 20),
        config.get("weight_volume_spike", 10),
        config.get("weight_trade_flow", 15),
        config.get("weight_funding", 15),
    ]
    total = sum(weights)
    if abs(total - 100.0) > 0.01:
        return False, f"Weights must sum to 100, current total: {total:.2f}"

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
    return f"{seq}_antifolla_{connector}_{pair}"
