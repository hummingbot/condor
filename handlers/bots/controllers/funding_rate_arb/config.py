"""
Funding Rate Arbitrage controller configuration.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

# ============================================
# DEFAULTS values
# ============================================

DEFAULTS: Dict[str, Any] = {
    "controller_name": "funding_rate_arb",
    "controller_type": "generic",
    "id": "",
    # Exchange A
    "connector_pair_a_connector_name": "kucoin_perpetual",
    "connector_pair_a_trading_pair": "SOL-USDT",
    # Exchange B
    "connector_pair_b_connector_name": "hyperliquid_perpetual",
    "connector_pair_b_trading_pair": "SOL-USDT",
    # Funding intervals (optional)
    "funding_interval_a_hours": None,
    "funding_interval_b_hours": None,
    # Thresholds
    "entry_threshold": 0.0002, # se le fees sono 0.1% =0.001 è da 1/5 delle fees 
    "exit_threshold": 0.00003, # 1/5 - 1/10 of entry_threshold
    # Capital and risk
    "total_amount_quote": 100,
    "leverage": 1,
    "position_mode": "HEDGE",
    "sl_global": 0.03,
    "tp_global": 0.05,
    "funding_check_interval": 300,
    "executor_refresh_time": 60,
}

# ============================================
# FIELD DEFINITIONS
# ============================================

FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(
        name="id",
        label="Config ID",
        type="str",
        required=True,
        hint="Auto-generated",
    ),
    # Exchange A
    "connector_pair_a_connector_name": ControllerField(
        name="connector_pair_a_connector_name",
        label="Exchange A",
        type="str",
        required=True,
        hint="First exchange connector (perp or spot)",
    ),
    "connector_pair_a_trading_pair": ControllerField(
        name="connector_pair_a_trading_pair",
        label="Pair A",
        type="str",
        required=True,
        hint="e.g. SOL-USDT",
    ),
    # Exchange B
    "connector_pair_b_connector_name": ControllerField(
        name="connector_pair_b_connector_name",
        label="Exchange B",
        type="str",
        required=True,
        hint="Second exchange connector",
    ),
    "connector_pair_b_trading_pair": ControllerField(
        name="connector_pair_b_trading_pair",
        label="Pair B",
        type="str",
        required=True,
        hint="e.g. SOL-USDT",
    ),
    # Funding intervals
    "funding_interval_a_hours": ControllerField(
        name="funding_interval_a_hours",
        label="Funding Interval A (hours)",
        type="int",
        required=False,
        hint="Leave empty for auto-detect",
        default=None,
    ),
    "funding_interval_b_hours": ControllerField(
        name="funding_interval_b_hours",
        label="Funding Interval B (hours)",
        type="int",
        required=False,
        hint="Leave empty for auto-detect",
        default=None,
    ),
    # Thresholds
    "entry_threshold": ControllerField(
        name="entry_threshold",
        label="Entry Threshold (%/h)",
        type="float",
        required=True,
        hint="Minimum net rate to open (e.g. 0.000025 = 0.0025%/h)",
        default=0.000025,
    ),
    "exit_threshold": ControllerField(
        name="exit_threshold",
        label="Exit Threshold (%/h)",
        type="float",
        required=True,
        hint="Close when net rate below this (e.g. 0.000005 = 0.0005%/h)",
        default=0.000005,
    ),
    # Capital
    "total_amount_quote": ControllerField(
        name="total_amount_quote",
        label="Total Amount (USDT)",
        type="float",
        required=True,
        hint="Total capital, split equally between legs",
        default=100,
    ),
    "leverage": ControllerField(
        name="leverage",
        label="Leverage",
        type="int",
        required=True,
        hint="1x recommended (no liquidation risk)",
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
        label="Global Stop Loss",
        type="float",
        required=False,
        hint="Emergency exit at this loss (e.g. 0.03 = 3%)",
        default=0.03,
    ),
    "tp_global": ControllerField(
        name="tp_global",
        label="Global Take Profit",
        type="float",
        required=False,
        hint="Emergency exit at this profit (e.g. 0.05 = 5%)",
        default=0.05,
    ),
    # Intervals
    "funding_check_interval": ControllerField(
        name="funding_check_interval",
        label="Check Interval (s)",
        type="int",
        required=False,
        hint="Seconds between funding rate checks",
        default=300,
    ),
    "executor_refresh_time": ControllerField(
        name="executor_refresh_time",
        label="Refresh Time (s)",
        type="int",
        required=False,
        hint="Cancel unfilled orders after this many seconds",
        default=60,
    ),
}


FIELD_ORDER: List[str] = [
    "id",
    "connector_pair_a_connector_name",
    "connector_pair_a_trading_pair",
    "connector_pair_b_connector_name",
    "connector_pair_b_trading_pair",
    "funding_interval_a_hours",
    "funding_interval_b_hours",
    "entry_threshold",
    "exit_threshold",
    "total_amount_quote",
    "leverage",
    "position_mode",
    "sl_global",
    "tp_global",
    "funding_check_interval",
    "executor_refresh_time",
]


EDITABLE_FIELDS: List[str] = [
    "connector_pair_a_connector_name",
    "connector_pair_a_trading_pair",
    "connector_pair_b_connector_name",
    "connector_pair_b_trading_pair",
    "funding_interval_a_hours",
    "funding_interval_b_hours",
    "entry_threshold",
    "exit_threshold",
    "total_amount_quote",
    "leverage",
    "sl_global",
    "tp_global",
    "funding_check_interval",
    "executor_refresh_time",
]

# ============================================
# HELPER FUNCTIONS
# ============================================

def get_flat_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Estrae i campi in formato piatto per l'editing."""
    ep1 = config.get("connector_pair_a", {}) or {}
    ep2 = config.get("connector_pair_b", {}) or {}
    flat = {k: v for k, v in config.items() if not isinstance(v, dict)}
    flat["connector_pair_a_connector_name"] = ep1.get("connector_name", "")
    flat["connector_pair_a_trading_pair"] = ep1.get("trading_pair", "")
    flat["connector_pair_b_connector_name"] = ep2.get("connector_name", "")
    flat["connector_pair_b_trading_pair"] = ep2.get("trading_pair", "")
    flat.pop("connector_pair_a", None)
    flat.pop("connector_pair_b", None)
    return flat


def apply_flat_fields(config: Dict[str, Any], updates: Dict[str, Any]) -> None:
    """Applica gli aggiornamenti ai dizionari annidati."""
    for key, value in updates.items():
        if key == "connector_pair_a_connector_name":
            config.setdefault("connector_pair_a", {})["connector_name"] = value
        elif key == "connector_pair_a_trading_pair":
            config.setdefault("connector_pair_a", {})["trading_pair"] = value
        elif key == "connector_pair_b_connector_name":
            config.setdefault("connector_pair_b", {})["connector_name"] = value
        elif key == "connector_pair_b_trading_pair":
            config.setdefault("connector_pair_b", {})["trading_pair"] = value
        else:
            config[key] = value

def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate funding rate arb configuration."""
    # Check required fields
    required = [
        "connector_pair_a_connector_name",
        "connector_pair_a_trading_pair",
        "connector_pair_b_connector_name",
        "connector_pair_b_trading_pair",
        "total_amount_quote",
    ]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"

    # Validate thresholds
    entry = config.get("entry_threshold", 0)
    exit_ = config.get("exit_threshold", 0)
    if entry <= exit_:
        return False, f"Entry threshold ({entry}) must be greater than exit threshold ({exit_})"

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

    # Get connector names for ID
    conn_a = config.get("connector_pair_a_connector_name", "unknown")
    conn_b = config.get("connector_pair_b_connector_name", "unknown")
    pair = config.get("connector_pair_a_trading_pair", "UNKNOWN").upper()

    conn_a_clean = conn_a.replace("_perpetual", "").replace("_spot", "")
    conn_b_clean = conn_b.replace("_perpetual", "").replace("_spot", "")

    return f"{seq}_fra_{conn_a_clean}_{conn_b_clean}_{pair}"
