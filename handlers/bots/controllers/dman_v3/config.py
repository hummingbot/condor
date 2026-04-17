"""
DMan V3 controller configuration.

Mean reversion strategy using Bollinger Bands to determine direction,
with DCA execution to enter positions at multiple levels.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

DEFAULTS: Dict[str, Any] = {
    "controller_name": "dman_v3",
    "controller_type": "directional_trading",
    "id": "",
    # Base fields from ControllerConfigBase
    "manual_kill_switch": False,
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
    # Trailing stop as object (matches TrailingStop dataclass)
    "trailing_stop": {
        "activation_price": 0.015,
        "trailing_delta": 0.005,
    },
    # Candles config
    "candles_connector": "",
    "candles_trading_pair": "",
    "interval": "3m",
    # Bollinger Bands
    "bb_length": 100,
    "bb_std": 2.0,
    "bb_long_threshold": 0.0,
    "bb_short_threshold": 1.0,
    # DCA
    "dca_spreads": "0.001,0.018,0.15,0.25",
    "dca_amounts_pct": "",
    "dynamic_order_spread": False,
    "dynamic_target": False,
    "activation_bounds": None,
}

FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(name="id", label="Config ID", type="str", required=True, hint="Auto-generated"),
    "connector_name": ControllerField(name="connector_name", label="Connector", type="str", required=True, hint="Exchange connector"),
    "trading_pair": ControllerField(name="trading_pair", label="Trading Pair", type="str", required=True, hint="e.g. BTC-USDT"),
    "leverage": ControllerField(name="leverage", label="Leverage", type="int", required=True, hint="e.g. 1, 5, 10", default=1),
    "position_mode": ControllerField(name="position_mode", label="Position Mode", type="str", required=False, hint="HEDGE or ONEWAY", default="HEDGE"),
    "total_amount_quote": ControllerField(name="total_amount_quote", label="Total Amount (Quote)", type="float", required=True, hint="e.g. 1000 USDT"),
    "max_executors_per_side": ControllerField(name="max_executors_per_side", label="Max Executors/Side", type="int", required=False, hint="Max concurrent positions per side (default: 1)", default=1),
    "cooldown_time": ControllerField(name="cooldown_time", label="Cooldown Time (s)", type="int", required=False, hint="Seconds between new executors (default: 60)", default=60),
    "stop_loss": ControllerField(name="stop_loss", label="Stop Loss", type="float", required=False, hint="Stop loss % (e.g. 0.05 = 5%)", default=0.05),
    "take_profit": ControllerField(name="take_profit", label="Take Profit", type="float", required=False, hint="Take profit % (e.g. 0.03 = 3%)", default=0.03),
    "take_profit_order_type": ControllerField(name="take_profit_order_type", label="TP Order Type", type="int", required=False, hint="1=Market, 2=Limit, 3=Limit Maker", default=2),
    "time_limit": ControllerField(name="time_limit", label="Time Limit (s)", type="int", required=False, hint="Max executor lifetime in seconds (None = no limit)", default=None),
    "trailing_stop_activation": ControllerField(name="trailing_stop_activation", label="Trailing Stop Activation", type="float", required=False, hint="Activation price % (e.g. 0.015 = 1.5%)", default=0.015),
    "trailing_stop_delta": ControllerField(name="trailing_stop_delta", label="Trailing Stop Delta", type="float", required=False, hint="Trailing delta % (e.g. 0.005 = 0.5%)", default=0.005),
    "candles_connector": ControllerField(name="candles_connector", label="Candles Connector", type="str", required=False, hint="Leave empty to use same as connector", default=""),
    "candles_trading_pair": ControllerField(name="candles_trading_pair", label="Candles Pair", type="str", required=False, hint="Leave empty to use same as trading pair", default=""),
    "interval": ControllerField(name="interval", label="Candle Interval", type="str", required=True, hint="e.g. 1m, 3m, 5m, 1h", default="3m"),
    "bb_length": ControllerField(name="bb_length", label="BB Length", type="int", required=False, hint="Bollinger Bands period (default: 100)", default=100),
    "bb_std": ControllerField(name="bb_std", label="BB Std Dev", type="float", required=False, hint="Standard deviations (default: 2.0)", default=2.0),
    "bb_long_threshold": ControllerField(name="bb_long_threshold", label="BB Long Threshold", type="float", required=False, hint="BBP below this → LONG signal (default: 0.0)", default=0.0),
    "bb_short_threshold": ControllerField(name="bb_short_threshold", label="BB Short Threshold", type="float", required=False, hint="BBP above this → SHORT signal (default: 1.0)", default=1.0),
    "dca_spreads": ControllerField(name="dca_spreads", label="DCA Spreads", type="str", required=True, hint="Comma-separated (e.g. 0.001,0.018,0.15,0.25)", default="0.001,0.018,0.15,0.25"),
    "dca_amounts_pct": ControllerField(name="dca_amounts_pct", label="DCA Amounts %", type="str", required=False, hint="Comma-separated %, empty = equal distribution", default=""),
    "dynamic_order_spread": ControllerField(name="dynamic_order_spread", label="Dynamic Spread", type="bool", required=False, hint="Scale spreads with BB width", default=False),
    "dynamic_target": ControllerField(name="dynamic_target", label="Dynamic Target", type="bool", required=False, hint="Scale TP/SL with BB width", default=False),
    "activation_bounds": ControllerField(name="activation_bounds", label="Activation Bounds", type="float", required=False, hint="e.g. 0.01 (1%) - None to disable", default=None),
    "manual_kill_switch": ControllerField(name="manual_kill_switch", label="Kill Switch", type="bool", required=False, hint="Manual kill switch", default=False),
}

FIELD_ORDER: List[str] = [
    "id", "connector_name", "trading_pair", "leverage", "position_mode",
    "total_amount_quote", "max_executors_per_side", "cooldown_time",
    "stop_loss", "take_profit", "take_profit_order_type", "time_limit",
    "trailing_stop_activation", "trailing_stop_delta",
    "candles_connector", "candles_trading_pair", "interval",
    "bb_length", "bb_std", "bb_long_threshold", "bb_short_threshold",
    "dca_spreads", "dca_amounts_pct",
    "dynamic_order_spread", "dynamic_target",
    "activation_bounds", "manual_kill_switch",
]

EDITABLE_FIELDS: List[str] = [
    "connector_name", "trading_pair", "total_amount_quote", "leverage",
    "max_executors_per_side", "cooldown_time",
    "stop_loss", "take_profit", "take_profit_order_type",
    "trailing_stop_activation", "trailing_stop_delta",
    "candles_connector", "candles_trading_pair", "interval",
    "bb_length", "bb_std", "bb_long_threshold", "bb_short_threshold",
    "dca_spreads", "dca_amounts_pct",
    "dynamic_order_spread", "dynamic_target", "activation_bounds",
]

def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    # Identifica il connettore principale
    connector = config.get("connector_name", "").lower()
    is_spot = "spot" in connector or not ("perpetual" in connector or "margin" in connector)

    # --- FIX AUTOMATICO PER IL GRAFICO ---
    # Se il connettore delle candele non è specificato, lo creiamo pulendo quello principale
    if not config.get("candles_connector"):
        # Rimuove i suffissi per puntare allo Spot (es: binance_perpetual -> binance)
        clean_conn = connector.replace("_perpetual", "").replace("_margin", "").replace("_spot", "")
        config["candles_connector"] = clean_conn

    # Se la coppia delle candele non è specificata, usa quella di trading
    if not config.get("candles_trading_pair"):
        config["candles_trading_pair"] = config.get("trading_pair")
    # -------------------------------------

    if is_spot:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
    else:
        # Defaults per i Perpetual se non specificati
        if not config.get("leverage"):
            config["leverage"] = 1
        if config.get("position_mode") not in ["HEDGE", "ONEWAY"]:
            config["position_mode"] = "HEDGE"

    # Validazione campi obbligatori
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
    return f"{seq}_dman_{connector}_{pair}"
