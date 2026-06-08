"""
Delta Neutral Market Making controller configuration.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

# Default configuration values
DEFAULTS: Dict[str, Any] = {
    "controller_name": "delta_neutral_mm",
    "controller_type": "generic",
    "id": "",
    # Exchanges
    "connector_pair_maker_connector_name": "kucoin",
    "connector_pair_maker_trading_pair": "SOL-USDT",
    "connector_pair_hedge_connector_name": "hyperliquid_perpetual",
    "connector_pair_hedge_trading_pair": "SOL-USDT",
    # Candles
    "candles_connector": None,
    "candles_trading_pair": None,
    "interval": "3m",
    # MACD
    "macd_fast": 21,
    "macd_slow": 42,
    "macd_signal": 9,
    # NATR
    "natr_length": 14,
    # Market making levels
    "buy_spreads": "1.0, 2.0, 3.0",
    "sell_spreads": "1.0, 2.0, 3.0",
    "order_amount_quote": 15,
    "order_refresh_time": 30,
    # Delta hedging
    "hedge_threshold_quote": 10,
    "max_delta_quote": 50,
    # Hedge settings
    "leverage": 1,
    "position_mode": "HEDGE",
    # Risk
    "sl_global": 0.03,
    "tp_global": 0.05,
    # Timeout
    "hedge_position_timeout": 3600,
    # TP multiplier
    "maker_tp_multiplier": 1.0,
}


# Field definitions
FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(
        name="id",
        label="Config ID",
        type="str",
        required=True,
        hint="Auto-generated",
    ),
    # Maker exchange
    "connector_pair_maker_connector_name": ControllerField(
        name="connector_pair_maker_connector_name",
        label="Maker Exchange",
        type="str",
        required=True,
        hint="Exchange for limit orders (spot preferred)",
    ),
    "connector_pair_maker_trading_pair": ControllerField(
        name="connector_pair_maker_trading_pair",
        label="Maker Pair",
        type="str",
        required=True,
        hint="e.g. SOL-USDT",
    ),
    # Hedge exchange
    "connector_pair_hedge_connector_name": ControllerField(
        name="connector_pair_hedge_connector_name",
        label="Hedge Exchange",
        type="str",
        required=True,
        hint="Perpetual exchange for delta hedging",
    ),
    "connector_pair_hedge_trading_pair": ControllerField(
        name="connector_pair_hedge_trading_pair",
        label="Hedge Pair",
        type="str",
        required=True,
        hint="e.g. SOL-USDT",
    ),
    # Candles
    "candles_connector": ControllerField(
        name="candles_connector",
        label="Candles Connector",
        type="str",
        required=False,
        hint="Leave empty to use maker exchange",
        default=None,
    ),
    "candles_trading_pair": ControllerField(
        name="candles_trading_pair",
        label="Candles Pair",
        type="str",
        required=False,
        hint="Leave empty to use maker pair",
        default=None,
    ),
    "interval": ControllerField(
        name="interval",
        label="Candle Interval",
        type="str",
        required=True,
        hint="e.g. 1m, 3m, 5m, 1h",
        default="3m",
    ),
    # MACD
    "macd_fast": ControllerField(
        name="macd_fast",
        label="MACD Fast",
        type="int",
        required=False,
        hint="Fast EMA period",
        default=21,
    ),
    "macd_slow": ControllerField(
        name="macd_slow",
        label="MACD Slow",
        type="int",
        required=False,
        hint="Slow EMA period",
        default=42,
    ),
    "macd_signal": ControllerField(
        name="macd_signal",
        label="MACD Signal",
        type="int",
        required=False,
        hint="Signal line period",
        default=9,
    ),
    # NATR
    "natr_length": ControllerField(
        name="natr_length",
        label="NATR Length",
        type="int",
        required=False,
        hint="Normalized ATR period",
        default=14,
    ),
    # Spreads
    "buy_spreads": ControllerField(
        name="buy_spreads",
        label="Buy Spreads",
        type="str",
        required=False,
        hint="Comma-separated NATR multiples (e.g. 1.0,2.0,3.0)",
        default="1.0,2.0,3.0",
    ),
    "sell_spreads": ControllerField(
        name="sell_spreads",
        label="Sell Spreads",
        type="str",
        required=False,
        hint="Comma-separated NATR multiples",
        default="1.0,2.0,3.0",
    ),
    "order_amount_quote": ControllerField(
        name="order_amount_quote",
        label="Order Amount (USDT)",
        type="float",
        required=False,
        hint="Amount per level in quote currency",
        default=15,
    ),
    "order_refresh_time": ControllerField(
        name="order_refresh_time",
        label="Refresh Time (s)",
        type="int",
        required=False,
        hint="Cancel unfilled orders after this many seconds",
        default=30,
    ),
    # Delta hedging
    "hedge_threshold_quote": ControllerField(
        name="hedge_threshold_quote",
        label="Hedge Threshold (USDT)",
        type="float",
        required=False,
        hint="Hedge when delta exceeds this value",
        default=10,
    ),
    "max_delta_quote": ControllerField(
        name="max_delta_quote",
        label="Max Delta (USDT)",
        type="float",
        required=False,
        hint="Emergency hedge at this delta",
        default=50,
    ),
    # Leverage
    "leverage": ControllerField(
        name="leverage",
        label="Leverage",
        type="int",
        required=False,
        hint="1x recommended",
        default=1,
    ),
    "position_mode": ControllerField(
        name="position_mode",
        label="Position Mode",
        type="str",
        required=False,
        hint="HEDGE or ONEWAY",
        default="HEDGE",
    ),
    # Risk
    "sl_global": ControllerField(
        name="sl_global",
        label="Stop Loss",
        type="float",
        required=False,
        hint="Emergency exit at this loss (e.g. 0.03 = 3%)",
        default=0.03,
    ),
    "tp_global": ControllerField(
        name="tp_global",
        label="Take Profit",
        type="float",
        required=False,
        hint="Emergency exit at this profit",
        default=0.05,
    ),
    "hedge_position_timeout": ControllerField(
        name="hedge_position_timeout",
        label="Hedge Timeout (s)",
        type="int",
        required=False,
        hint="Close hedge positions after this many seconds (0=disabled)",
        default=3600,
    ),
    "maker_tp_multiplier": ControllerField(
        name="maker_tp_multiplier",
        label="Maker TP Multiplier",
        type="float",
        required=False,
        hint="Take profit multiplier for maker orders",
        default=1.0,
    ),
}


FIELD_ORDER: List[str] = [
    "id",
    "connector_pair_maker_connector_name",
    "connector_pair_maker_trading_pair",
    "connector_pair_hedge_connector_name",
    "connector_pair_hedge_trading_pair",
    "candles_connector",
    "candles_trading_pair",
    "interval",
    "macd_fast",
    "macd_slow",
    "macd_signal",
    "natr_length",
    "buy_spreads",
    "sell_spreads",
    "order_amount_quote",
    "order_refresh_time",
    "hedge_threshold_quote",
    "max_delta_quote",
    "leverage",
    "position_mode",
    "sl_global",
    "tp_global",
    "hedge_position_timeout",
    "maker_tp_multiplier",
]


EDITABLE_FIELDS: List[str] = [
    "connector_pair_maker_connector_name",
    "connector_pair_maker_trading_pair",
    "connector_pair_hedge_connector_name",
    "connector_pair_hedge_trading_pair",
    "candles_connector",
    "candles_trading_pair",
    "interval",
    "buy_spreads",
    "sell_spreads",
    "order_amount_quote",
    "order_refresh_time",
    "hedge_threshold_quote",
    "max_delta_quote",
    "leverage",
    "sl_global",
    "tp_global",
    "hedge_position_timeout",
    "maker_tp_multiplier",
]

def get_flat_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Estrae i campi piatti, convertendo eventuali liste di spreads in stringhe."""
    flat = dict(config)
    for key in ("buy_spreads", "sell_spreads"):
        if key in flat and isinstance(flat[key], list):
            flat[key] = ",".join(str(x) for x in flat[key])
    return flat


def apply_flat_fields(config: Dict[str, Any], updates: Dict[str, Any]) -> None:
    """Applica gli aggiornamenti, mantenendo i spreads come stringhe."""
    for key, value in updates.items():
        if key in ("buy_spreads", "sell_spreads") and isinstance(value, list):
            config[key] = ",".join(str(x) for x in value)
        else:
            config[key] = value

def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate delta neutral MM configuration."""
    required = [
        "connector_pair_maker_connector_name",
        "connector_pair_maker_trading_pair",
        "connector_pair_hedge_connector_name",
        "connector_pair_hedge_trading_pair",
    ]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"

    # Validate spreads
    buy_spreads = config.get("buy_spreads", [])
    sell_spreads = config.get("sell_spreads", [])
    if isinstance(buy_spreads, str):
        buy_spreads = [float(x.strip()) for x in buy_spreads.split(",")]
    if isinstance(sell_spreads, str):
        sell_spreads = [float(x.strip()) for x in sell_spreads.split(",")]

    if not buy_spreads or not sell_spreads:
        return False, "Buy and sell spreads must have at least one level"

    return True, None


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    """Generate unique config ID."""
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

    maker = config.get("connector_pair_maker_connector_name", "unknown")
    hedge = config.get("connector_pair_hedge_connector_name", "unknown")
    pair = config.get("connector_pair_maker_trading_pair", "UNKNOWN").upper()

    maker_clean = maker.replace("_perpetual", "").replace("_spot", "")
    hedge_clean = hedge.replace("_perpetual", "").replace("_spot", "")

    return f"{seq}_dnmm_{maker_clean}_{hedge_clean}_{pair}"
