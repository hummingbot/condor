"""
LMMultiPairDEX configuration for Condor.

Supporta:
- XRPL DEX (latenza 3-5s, fee ~$0.00001)
- Hyperliquid (latenza 0.2ms, maker rebate -0.01%)
"""
from .._base import ControllerField
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


# Default configuration values
DEFAULTS: Dict[str, Any] = {
    "controller_name": "lm_multi_pair_dex",
    "controller_type": "generic",
    "connector_name": "xrpl",
    "markets": ["XRP-RLUSD"],
    "token": "XRP",
    "total_amount_quote": 1000,
    "portfolio_allocation": 0.10,
    "buy_spreads": [0.005, 0.01, 0.02],
    "sell_spreads": [0.005, 0.01, 0.02],
    "use_dynamic_spreads": True,
    "atr_length": 14,
    "atr_multiplier_min": 0.5,
    "atr_multiplier_max": 2.0,
    "order_refresh_time": 45,
    "cooldown_time": 20,
    "order_refresh_tolerance_pct": 0.01,
    "target_base_pct": 0.5,
    "min_base_pct": 0.3,
    "max_base_pct": 0.7,
    "max_skew": 0.2,
    "leverage": 1,
    "take_profit": None,
    "max_spread_multiplier": 3.0,
    "min_spread_multiplier": 0.3,
    "min_volume_usd": 10000,
    "min_liquidity_score": 0.3,
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
        label="DEX connector",
        type="str",
        required=True,
        hint="xrpl or hyperliquid",
    ),
    "markets": ControllerField(
        name="markets",
        label="Trading pairs",
        type="list",
        required=True,
        hint="Comma-separated: XRP-RLUSD, BTC-XRP (XRPL) or SOL-USDC, ETH-USDC (Hyperliquid)",
    ),
    "token": ControllerField(
        name="token",
        label="Unified token",
        type="str",
        required=True,
        hint="XRPL: XRP or RLUSD | Hyperliquid: USDC",
    ),
    "total_amount_quote": ControllerField(
        name="total_amount_quote",
        label="Total capital",
        type="float",
        required=True,
        hint="Total amount in unified token",
        default=1000,
    ),
    "portfolio_allocation": ControllerField(
        name="portfolio_allocation",
        label="Portfolio allocation",
        type="float",
        required=False,
        hint="Percent of total capital to use (0.1 = 10%)",
        default=0.10,
    ),
    "buy_spreads": ControllerField(
        name="buy_spreads",
        label="Buy spreads",
        type="list",
        required=False,
        hint="Spreads for buy orders (e.g., 0.005,0.01 = 0.5%,1.0%)",
        default=[0.005, 0.01, 0.02],
    ),
    "sell_spreads": ControllerField(
        name="sell_spreads",
        label="Sell spreads",
        type="list",
        required=False,
        hint="Spreads for sell orders",
        default=[0.005, 0.01, 0.02],
    ),
    "use_dynamic_spreads": ControllerField(
        name="use_dynamic_spreads",
        label="Dynamic spreads",
        type="bool",
        required=False,
        hint="Adjust spreads based on ATR volatility",
        default=True,
    ),
    "order_refresh_time": ControllerField(
        name="order_refresh_time",
        label="Refresh time (s)",
        type="int",
        required=False,
        hint="Cancel and replace unfilled orders after N seconds",
        default=45,
    ),
    "cooldown_time": ControllerField(
        name="cooldown_time",
        label="Cooldown after fill (s)",
        type="int",
        required=False,
        hint="Wait N seconds after a fill before placing new orders",
        default=20,
    ),
    "order_refresh_tolerance_pct": ControllerField(
        name="order_refresh_tolerance_pct",
        label="Price tolerance",
        type="float",
        required=False,
        hint="Refresh only if price changed more than this (0.01 = 1%)",
        default=0.01,
    ),
    "target_base_pct": ControllerField(
        name="target_base_pct",
        label="Target base %",
        type="float",
        required=False,
        hint="Target percentage of base assets (0.5 = 50%)",
        default=0.5,
    ),
    "min_base_pct": ControllerField(
        name="min_base_pct",
        label="Min base %",
        type="float",
        required=False,
        hint="Below this, buy aggressively",
        default=0.3,
    ),
    "max_base_pct": ControllerField(
        name="max_base_pct",
        label="Max base %",
        type="float",
        required=False,
        hint="Above this, sell aggressively",
        default=0.7,
    ),
    "max_skew": ControllerField(
        name="max_skew",
        label="Max skew",
        type="float",
        required=False,
        hint="Minimum order size multiplier (0.2 = 20% of normal)",
        default=0.2,
    ),
    "min_liquidity_score": ControllerField(
        name="min_liquidity_score",
        label="Min liquidity score",
        type="float",
        required=False,
        hint="Skip pairs with liquidity score below this",
        default=0.3,
    ),
}

# Field order in the wizard
FIELD_ORDER: List[str] = [
    "id",
    "connector_name",
    "markets",
    "token",
    "total_amount_quote",
    "portfolio_allocation",
    "buy_spreads",
    "sell_spreads",
    "use_dynamic_spreads",
    "order_refresh_time",
    "cooldown_time",
    "order_refresh_tolerance_pct",
    "target_base_pct",
    "min_base_pct",
    "max_base_pct",
    "max_skew",
    "min_liquidity_score",
]

# Wizard steps – minimal required for quick setup
WIZARD_STEPS: List[str] = [
    "connector_name",
    "markets",
    "token",
    "total_amount_quote",
    "portfolio_allocation",
    "review",
]


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate the configuration."""
    required = ["connector_name", "markets", "token"]
    for field in required:
        if not config.get(field):
            return False, f"Missing required field: {field}"

    total = config.get("total_amount_quote", 0)
    if total <= 0:
        return False, "total_amount_quote must be > 0"

    alloc = config.get("portfolio_allocation", 0)
    if alloc <= 0 or alloc > 1:
        return False, "portfolio_allocation must be between 0 and 1"

    connector = config.get("connector_name", "")
    if connector not in ["xrpl", "hyperliquid"]:
        return False, f"connector_name must be 'xrpl' or 'hyperliquid', got '{connector}'"

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

    connector = config.get("connector_name", "unknown")
    first_market = config.get("markets", ["UNKNOWN"])[0].split("-")[0]
    return f"{seq}_lmmulti_{connector}_{first_market}"
