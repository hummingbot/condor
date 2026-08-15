"""
Quantum Grid Allocator controller configuration.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._base import ControllerField

DEFAULTS: Dict[str, Any] = {
    "controller_name": "quantum_grid_allocator",
    "controller_type": "generic",
    "id": "",
    "connector_name": "binance",
    "leverage": 1,
    "position_mode": "HEDGE",
    "quote_asset": "FDUSD",
    "fee_asset": "BNB",
    "portfolio_allocation": {"SOL": 0.50},
    "long_only_threshold": 0.2,
    "short_only_threshold": 0.2,
    "hedge_ratio": 2,
    "base_grid_value_pct": 0.08,
    "max_grid_value_pct": 0.15,
    "grid_range": 0.002,
    "tp_sl_ratio": 0.8,
    "min_order_amount": 5,
    "max_deviation": 0.05,
    "max_open_orders": 2,
    "safe_extra_spread": 0.0001,
    "favorable_order_frequency": 2,
    "unfavorable_order_frequency": 5,
    "max_orders_per_batch": 1,
    "min_spread_between_orders": 0.0001,
    "grid_tp_multiplier": 0.0001,
    "limit_price_spread": 0.001,
    "activation_bounds": 0.0002,
    "bb_length": 100,
    "bb_std_dev": 2.0,
    "interval": "1s",
    "dynamic_grid_range": False,
    "show_terminated_details": False,
}

FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(name="id", label="Config ID", type="str", required=True),
    "connector_name": ControllerField(name="connector_name", label="Exchange", type="str", required=True),
    "leverage": ControllerField(name="leverage", label="Leverage", type="int", required=False, default=1),
    "position_mode": ControllerField(name="position_mode", label="Position Mode", type="str", required=False, default="HEDGE"),
    "quote_asset": ControllerField(name="quote_asset", label="Quote Asset", type="str", required=False, default="FDUSD"),
    "fee_asset": ControllerField(name="fee_asset", label="Fee Asset", type="str", required=False, default="BNB"),
    "portfolio_allocation": ControllerField(name="portfolio_allocation", label="Portfolio Allocation", type="str", required=False, hint="e.g. SOL:0.5,BTC:0.3"),
    "long_only_threshold": ControllerField(name="long_only_threshold", label="Long Only Threshold", type="float", required=False, default=0.2),
    "short_only_threshold": ControllerField(name="short_only_threshold", label="Short Only Threshold", type="float", required=False, default=0.2),
    "hedge_ratio": ControllerField(name="hedge_ratio", label="Hedge Ratio", type="float", required=False, default=2),
    "base_grid_value_pct": ControllerField(name="base_grid_value_pct", label="Base Grid Value %", type="float", required=False, default=0.08),
    "max_grid_value_pct": ControllerField(name="max_grid_value_pct", label="Max Grid Value %", type="float", required=False, default=0.15),
    "grid_range": ControllerField(name="grid_range", label="Grid Range", type="float", required=False, default=0.002),
    "tp_sl_ratio": ControllerField(name="tp_sl_ratio", label="TP/SL Ratio", type="float", required=False, default=0.8),
    "min_order_amount": ControllerField(name="min_order_amount", label="Min Order Amount", type="float", required=False, default=5),
    "max_deviation": ControllerField(name="max_deviation", label="Max Deviation", type="float", required=False, default=0.05),
    "max_open_orders": ControllerField(name="max_open_orders", label="Max Open Orders", type="int", required=False, default=2),
    "safe_extra_spread": ControllerField(name="safe_extra_spread", label="Safe Extra Spread", type="float", required=False, default=0.0001),
    "favorable_order_frequency": ControllerField(name="favorable_order_frequency", label="Favorable Order Freq (s)", type="int", required=False, default=2),
    "unfavorable_order_frequency": ControllerField(name="unfavorable_order_frequency", label="Unfavorable Order Freq (s)", type="int", required=False, default=5),
    "max_orders_per_batch": ControllerField(name="max_orders_per_batch", label="Max Orders/Batch", type="int", required=False, default=1),
    "min_spread_between_orders": ControllerField(name="min_spread_between_orders", label="Min Spread", type="float", required=False, default=0.0001),
    "grid_tp_multiplier": ControllerField(name="grid_tp_multiplier", label="Grid TP Multiplier", type="float", required=False, default=0.0001),
    "limit_price_spread": ControllerField(name="limit_price_spread", label="Limit Price Spread", type="float", required=False, default=0.001),
    "activation_bounds": ControllerField(name="activation_bounds", label="Activation Bounds", type="float", required=False, default=0.0002),
    "bb_length": ControllerField(name="bb_length", label="BB Length", type="int", required=False, default=100),
    "bb_std_dev": ControllerField(name="bb_std_dev", label="BB Std Dev", type="float", required=False, default=2.0),
    "interval": ControllerField(name="interval", label="Interval", type="str", required=False, default="1s"),
    "dynamic_grid_range": ControllerField(name="dynamic_grid_range", label="Dynamic Grid Range", type="bool", required=False, default=False),
    "show_terminated_details": ControllerField(name="show_terminated_details", label="Show Terminated", type="bool", required=False, default=False),
}

FIELD_ORDER: List[str] = [
    "id", "connector_name", "leverage", "position_mode", "quote_asset", "fee_asset",
    "portfolio_allocation", "long_only_threshold", "short_only_threshold", "hedge_ratio",
    "base_grid_value_pct", "max_grid_value_pct", "grid_range", "tp_sl_ratio",
    "min_order_amount", "max_deviation", "max_open_orders", "safe_extra_spread",
    "favorable_order_frequency", "unfavorable_order_frequency", "max_orders_per_batch",
    "min_spread_between_orders", "grid_tp_multiplier", "limit_price_spread",
    "activation_bounds", "bb_length", "bb_std_dev", "interval", "dynamic_grid_range",
    "show_terminated_details",
]

EDITABLE_FIELDS: List[str] = FIELD_ORDER.copy()


def _parse_portfolio_allocation(value: str) -> Dict[str, float]:
    """Parse portfolio allocation string like 'SOL:0.5,BTC:0.3'"""
    result = {}
    for part in value.split(","):
        if ":" in part:
            asset, pct = part.split(":")
            result[asset.strip()] = float(pct.strip())
    return result


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if not config.get("connector_name"):
        return False, "Missing exchange"
    
    # Validate portfolio allocation
    portfolio = config.get("portfolio_allocation", {})
    if isinstance(portfolio, str):
        portfolio = _parse_portfolio_allocation(portfolio)
    
    total = sum(portfolio.values())
    if total >= 1.0:
        return False, f"Total allocation {total*100:.0f}% must be less than 100%"
    
    return True, None


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    max_num = 0
    for cfg in existing_configs:
        cfg_id = cfg.get("id", "")
        if cfg_id and cfg_id[:3].isdigit():
            max_num = max(max_num, int(cfg_id[:3]))
    seq = str(max_num + 1).zfill(3)
    connector = config.get("connector_name", "unknown").replace("_perpetual", "").replace("_spot", "")
    quote = config.get("quote_asset", "FDUSD")
    return f"{seq}_qga_{connector}_{quote}"


