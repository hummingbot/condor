"""
MACD BB V1 controller configuration.

Directional trading strategy combining Bollinger Bands and MACD:
- LONG  when BBP < long_threshold AND MACD histogram > 0 AND MACD < 0
- SHORT when BBP > short_threshold AND MACD histogram < 0 AND MACD > 0
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

DEFAULTS: Dict[str, Any] = {
    "controller_name": "macd_bb_v1",
    "controller_type": "directional_trading",
    "id": "",
    # Base fields
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
    # Trailing stop as object (matches TrailingStop dataclass)
    "trailing_stop": {
        "activation_price": 0.015,
        "trailing_delta": 0.005,
    },
    # Candles config
    "candles_connector": "",
    "candles_trading_pair": "",
    "interval": "5m",
    # Bollinger Bands
    "bb_length": 100,
    "bb_std": 2.0,
    "bb_long_threshold": 0.0,
    "bb_short_threshold": 1.0,
    # MACD
    "macd_fast": 21,
    "macd_slow": 42,
    "macd_signal": 9,
}

FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(
        name="id",
        label="Config ID",
        type="str",
        required=True,
        hint="Auto-generated"
    ),
    "connector_name": ControllerField(
        name="connector_name",
        label="Connector",
        type="str",
        required=True,
        hint="Exchange connector"
    ),
    "trading_pair": ControllerField(
        name="trading_pair",
        label="Trading Pair",
        type="str",
        required=True,
        hint="e.g. BTC-USDT"
    ),
    "leverage": ControllerField(
        name="leverage",
        label="Leverage",
        type="int",
        required=True,
        hint="e.g. 1, 5, 10",
        default=1
    ),
    "position_mode": ControllerField(
        name="position_mode",
        label="Position Mode",
        type="str",
        required=False,
        hint="HEDGE or ONEWAY",
        default="HEDGE"
    ),
    "total_amount_quote": ControllerField(
        name="total_amount_quote",
        label="Total Amount (Quote)",
        type="float",
        required=True,
        hint="e.g. 1000 USDT"
    ),
    "max_executors_per_side": ControllerField(
        name="max_executors_per_side",
        label="Max Executors/Side",
        type="int",
        required=False,
        hint="Max concurrent positions per side (default: 1)",
        default=1
    ),
    "cooldown_time": ControllerField(
        name="cooldown_time",
        label="Cooldown Time (s)",
        type="int",
        required=False,
        hint="Seconds between new executors (default: 60)",
        default=60
    ),
    "stop_loss": ControllerField(
        name="stop_loss",
        label="Stop Loss",
        type="float",
        required=False,
        hint="Stop loss % (e.g. 0.05 = 5%)",
        default=0.05
    ),
    "take_profit": ControllerField(
        name="take_profit",
        label="Take Profit",
        type="float",
        required=False,
        hint="Take profit % (e.g. 0.03 = 3%)",
        default=0.03
    ),
    "take_profit_order_type": ControllerField(
        name="take_profit_order_type",
        label="TP Order Type",
        type="int",
        required=False,
        hint="1=Market, 2=Limit, 3=Limit Maker",
        default=2
    ),
    "time_limit": ControllerField(
        name="time_limit",
        label="Time Limit (s)",
        type="int",
        required=False,
        hint="Max executor lifetime in seconds (None = no limit)",
        default=None
    ),
    "candles_connector": ControllerField(
        name="candles_connector",
        label="Candles Connector",
        type="str",
        required=False,
        hint="Leave empty to use same as connector",
        default=""
    ),
    "candles_trading_pair": ControllerField(
        name="candles_trading_pair",
        label="Candles Pair",
        type="str",
        required=False,
        hint="Leave empty to use same as trading pair",
        default=""
    ),
    "interval": ControllerField(
        name="interval",
        label="Candle Interval",
        type="str",
        required=True,
        hint="e.g. 1m, 5m, 1h, 8h",
        default="5m"
    ),
    "bb_length": ControllerField(
        name="bb_length",
        label="BB Length",
        type="int",
        required=False,
        hint="Bollinger Bands period (default: 100)",
        default=100
    ),
    "bb_std": ControllerField(
        name="bb_std",
        label="BB Std Dev",
        type="float",
        required=False,
        hint="Standard deviations (default: 2.0)",
        default=2.0
    ),
    "bb_long_threshold": ControllerField(
        name="bb_long_threshold",
        label="BB Long Threshold",
        type="float",
        required=False,
        hint="BBP below this → LONG signal (default: 0.0)",
        default=0.0
    ),
    "bb_short_threshold": ControllerField(
        name="bb_short_threshold",
        label="BB Short Threshold",
        type="float",
        required=False,
        hint="BBP above this → SHORT signal (default: 1.0)",
        default=1.0
    ),
    "macd_fast": ControllerField(
        name="macd_fast",
        label="MACD Fast",
        type="int",
        required=False,
        hint="Fast EMA period (default: 21)",
        default=21
    ),
    "macd_slow": ControllerField(
        name="macd_slow",
        label="MACD Slow",
        type="int",
        required=False,
        hint="Slow EMA period (default: 42)",
        default=42
    ),
    "macd_signal": ControllerField(
        name="macd_signal",
        label="MACD Signal",
        type="int",
        required=False,
        hint="Signal line period (default: 9)",
        default=9
    ),
}

FIELD_ORDER: List[str] = [
    "bb_length",
    "bb_long_threshold",
    "bb_short_threshold",
    "bb_std",
    "candles_connector",
    "candles_trading_pair",
    "connector_name",
    "cooldown_time",
    "id",
    "interval",
    "leverage",
    "macd_fast",
    "macd_signal",
    "macd_slow",
    "max_executors_per_side",
    "position_mode",
    "stop_loss",
    "take_profit_order_type",
    "take_profit",
    "time_limit",
    "total_amount_quote",
    "trading_pair",
]

EDITABLE_FIELDS: List[str] = [
    "bb_length",
    "bb_long_threshold",
    "bb_short_threshold",
    "bb_std",
    "candles_connector",
    "candles_trading_pair",
    "connector_name",
    "cooldown_time",
    "interval",
    "leverage",
    "macd_fast",
    "macd_signal",
    "macd_slow",
    "max_executors_per_side",
    "stop_loss",
    "take_profit_order_type",
    "take_profit",
    "total_amount_quote",
    "trading_pair",
    "trailing_stop_activation",
    "trailing_stop_delta",

]

def get_flat_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Estrae i campi in formato piatto per l'editing, gestendo trailing_stop."""
    trailing = config.get("trailing_stop", {})
    # Copia i campi esistenti (che sono già piatti)
    flat = dict(config)
    # Aggiungi i due campi virtuali
    flat["trailing_stop_activation"] = trailing.get("activation_price", 0.015)
    flat["trailing_stop_delta"] = trailing.get("trailing_delta", 0.005)
    # Rimuovi il dizionario originale per non mostrarlo come campo separato
    flat.pop("trailing_stop", None)
    return flat


def apply_flat_fields(config: Dict[str, Any], updates: Dict[str, Any]) -> None:
    """Applica gli aggiornamenti, riconvertendo trailing_stop_activation/delta."""
    for key, value in updates.items():
        if key == "trailing_stop_activation":
            if "trailing_stop" not in config:
                config["trailing_stop"] = {}
            config["trailing_stop"]["activation_price"] = value
        elif key == "trailing_stop_delta":
            if "trailing_stop" not in config:
                config["trailing_stop"] = {}
            config["trailing_stop"]["trailing_delta"] = value
        else:
            config[key] = value

def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate and auto-fix MACD BB V1 configuration."""

    # Identifica il connettore principale
    connector = config.get("connector_name", "").lower()
    is_spot = "spot" in connector or not ("perpetual" in connector or "margin" in connector)

    # Auto-popola candles_connector se vuoto
    if not config.get("candles_connector"):
        # Rimuove i suffissi per puntare allo Spot (es: binance_perpetual -> binance)
        clean_conn = connector.replace("_perpetual", "").replace("_margin", "").replace("_spot", "")
        config["candles_connector"] = clean_conn

    # Auto-popola candles_trading_pair se vuoto
    if not config.get("candles_trading_pair"):
        config["candles_trading_pair"] = config.get("trading_pair")

    # Gestione spot vs perpetual
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

    # Validazione incrociata periodi
    bb_length = config.get("bb_length", 100)
    macd_slow = config.get("macd_slow", 42)
    if bb_length < macd_slow:
        return False, f"BB length ({bb_length}) should be >= MACD slow ({macd_slow}) for sufficient data"

    # Validazione thresholds
    bb_long = config.get("bb_long_threshold", 0.0)
    bb_short = config.get("bb_short_threshold", 1.0)
    if bb_long >= bb_short:
        return False, f"BB long threshold ({bb_long}) must be less than BB short threshold ({bb_short})"

    return True, None


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    """Generate sequential ID for MACD BB V1 configuration."""
    max_num = 0
    for cfg in existing_configs:
        parts = cfg.get("id", "").split("_", 1)
        if parts and parts[0].isdigit():
            max_num = max(max_num, int(parts[0]))
    seq = str(max_num + 1).zfill(3)
    connector = config.get("connector_name", "unknown").replace("_perpetual", "").replace("_spot", "")
    pair = config.get("trading_pair", "UNKNOWN").upper()
    return f"{seq}_macdbb_{connector}_{pair}"
