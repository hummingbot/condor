"""
XEMM Multiple Levels controller configuration.

Cross-exchange market making: places limit orders on a maker exchange
(less liquid) and hedges them instantly on a taker exchange (more liquid),
at multiple profitability target levels.

buy_levels_targets_amount / sell_levels_targets_amount format:
  "target_profit1,amount1-target_profit2,amount2-..."
  e.g. "0.003,10-0.006,20-0.009,30"
  - target_profit: target profitability for this level (e.g. 0.003 = 0.3%)
  - amount: relative weight (proportional, not absolute USDT)
  Actual order size = (level_weight / total_weight) * (total_amount_quote * 0.5)

min_profitability / max_profitability:
  Range around each target:
  - actual min = target - min_profitability
  - actual max = target + max_profitability

Gas fees for DEX taker connectors are handled automatically by hummingbot.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

DEFAULTS: Dict[str, Any] = {
    "controller_name": "xemm_multiple_levels",
    "controller_type": "generic",
    "id": "",
    "total_amount_quote": 1000,
    # Maker = less liquid CEX where limit orders are placed
    "maker_connector": "mexc",
    "maker_trading_pair": "PEPE-USDT",
    # Taker = more liquid CEX/DEX where hedge orders are filled
    "taker_connector": "binance",
    "taker_trading_pair": "PEPE-USDT",
    # Levels: "target_profit,weight-target_profit,weight-..."
    "buy_levels_targets_amount": "0.003,10-0.006,20-0.009,30",
    "sell_levels_targets_amount": "0.003,10-0.006,20-0.009,30",
    # Profitability range around each target level
    "min_profitability": 0.003,
    "max_profitability": 0.01,
    "max_executors_imbalance": 1,
    # Base fields from ControllerConfigBase
    "manual_kill_switch": None,
    "candles_config": [],
}

FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(
        name="id", label="Config ID", type="str", required=True, hint="Auto-generated"
    ),
    "total_amount_quote": ControllerField(
        name="total_amount_quote", label="Total Amount (Quote)", type="float",
        required=True, hint="Total capital — 50% buy side, 50% sell side"
    ),
    "maker_connector": ControllerField(
        name="maker_connector", label="Maker Exchange", type="str",
        required=True, hint="Less liquid CEX where limit orders are placed (e.g. mexc)"
    ),
    "maker_trading_pair": ControllerField(
        name="maker_trading_pair", label="Maker Pair", type="str",
        required=True, hint="e.g. PEPE-USDT"
    ),
    "taker_connector": ControllerField(
        name="taker_connector", label="Taker Exchange", type="str",
        required=True, hint="More liquid CEX/DEX for hedging (e.g. binance)"
    ),
    "taker_trading_pair": ControllerField(
        name="taker_trading_pair", label="Taker Pair", type="str",
        required=True, hint="Usually same as maker pair"
    ),
    "buy_levels_targets_amount": ControllerField(
        name="buy_levels_targets_amount", label="Buy Levels", type="str",
        required=True,
        hint="Format: profit,weight-profit,weight (e.g. 0.003,10-0.006,20-0.009,30)",
        default="0.003,10-0.006,20-0.009,30"
    ),
    "sell_levels_targets_amount": ControllerField(
        name="sell_levels_targets_amount", label="Sell Levels", type="str",
        required=True,
        hint="Format: profit,weight-profit,weight (e.g. 0.003,10-0.006,20-0.009,30)",
        default="0.003,10-0.006,20-0.009,30"
    ),
    "min_profitability": ControllerField(
        name="min_profitability", label="Min Profitability", type="float",
        required=False,
        hint="Subtracted from each target level (e.g. 0.003 = 0.3%)", default=0.003
    ),
    "max_profitability": ControllerField(
        name="max_profitability", label="Max Profitability", type="float",
        required=False,
        hint="Added to each target level (e.g. 0.01 = 1%)", default=0.01
    ),
    "max_executors_imbalance": ControllerField(
        name="max_executors_imbalance", label="Max Imbalance", type="int",
        required=False, hint="Max buy/sell imbalance before pausing (default: 1)", default=1
    ),
    "manual_kill_switch": ControllerField(
        name="manual_kill_switch", label="Kill Switch", type="bool",
        required=False, hint="Manual kill switch", default=None
    ),
}

FIELD_ORDER: List[str] = [
    "id", "total_amount_quote",
    "maker_connector", "maker_trading_pair",
    "taker_connector", "taker_trading_pair",
    "buy_levels_targets_amount", "sell_levels_targets_amount",
    "min_profitability", "max_profitability",
    "max_executors_imbalance", "manual_kill_switch",
]

EDITABLE_FIELDS: List[str] = [
    "total_amount_quote",
    "maker_connector", "maker_trading_pair",
    "taker_connector", "taker_trading_pair",
    "buy_levels_targets_amount", "sell_levels_targets_amount",
    "min_profitability", "max_profitability",
    "max_executors_imbalance",
]


def parse_levels(levels_str: str) -> List[List[float]]:
    """
    Parse levels string into list of [target_profit, weight] pairs.
    e.g. "0.003,10-0.006,20" -> [[0.003, 10], [0.006, 20]]
    """
    try:
        result = []
        for part in str(levels_str).split("-"):
            values = part.strip().split(",")
            if len(values) == 2:
                result.append([float(values[0]), float(values[1])])
        return result
    except Exception:
        return [[0.003, 10], [0.006, 20], [0.009, 30]]


def format_levels(levels: List[List[float]]) -> str:
    """Convert levels list back to string format."""
    return "-".join(f"{p},{a}" for p, a in levels)


def suggest_levels_from_spread(
    spread_pct: float, total_amount: float, num_levels: int = 3
) -> str:
    """
    Suggest levels based on observed spread between maker and taker.
    Each level targets a fraction of the spread.

    Args:
        spread_pct: Current spread as decimal (e.g. 0.005 = 0.5%)
        total_amount: Total amount per side
        num_levels: Number of levels to generate

    Returns:
        Levels string in format "profit,weight-profit,weight-..."
    """
    if spread_pct <= 0:
        return "0.003,10-0.006,20-0.009,30"

    # Generate levels at 30%, 60%, 90% of spread
    multipliers = [0.3, 0.6, 0.9][:num_levels]
    weights = [10, 20, 30][:num_levels]

    levels = []
    for mult, weight in zip(multipliers, weights):
        target = round(spread_pct * mult, 4)
        target = max(target, 0.001)  # minimum 0.1%
        levels.append(f"{target},{weight}")

    return "-".join(levels)


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    required = ["maker_connector", "maker_trading_pair",
                "taker_connector", "taker_trading_pair", "total_amount_quote"]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"
    for field in ["buy_levels_targets_amount", "sell_levels_targets_amount"]:
        val = config.get(field, "")
        if val:
            try:
                [list(map(float, x.split(","))) for x in str(val).split("-")]
            except Exception:
                return False, f"Invalid format for {field}. Use: profit,weight-profit,weight"
    return True, None


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    max_num = 0
    for cfg in existing_configs:
        parts = cfg.get("id", "").split("_", 1)
        if parts and parts[0].isdigit():
            max_num = max(max_num, int(parts[0]))
    seq = str(max_num + 1).zfill(3)
    maker = config.get("maker_connector", "maker").replace("_perpetual", "").replace("_spot", "")
    taker = config.get("taker_connector", "taker").replace("_perpetual", "").replace("_spot", "")
    pair = config.get("maker_trading_pair", "UNKNOWN").split("-")[0]
    return f"{seq}_xemm_{maker}_{taker}_{pair}"
