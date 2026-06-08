"""
Arbitrage Controller configuration with dynamic exchange validation and auto-optimization.

CEX/DEX (or CEX/CEX, DEX/DEX) arbitrage strategy that simultaneously
buys on one exchange and sells on another when profitability exceeds
the minimum threshold.

Config structure matches ArbitrageControllerConfig in hummingbot-api:
- exchange_pair_1: {connector_name, trading_pair}  (nested object)
- exchange_pair_2: {connector_name, trading_pair}  (nested object)
- rate_connector: used to fetch conversion rates (gas token, quote conversion)
- quote_conversion_asset: asset used to normalize profits (usually USDT)

Gas fees for DEX connectors are handled automatically by hummingbot
via GatewayHttpClient — no manual gas configuration needed.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
from decimal import Decimal

import numpy as np

from .._base import ControllerField

logger = logging.getLogger(__name__)

# ============================================
# DEFAULTS with intelligent values
# ============================================

DEFAULTS: Dict[str, Any] = {
    "controller_name": "arbitrage_controller",
    "controller_type": "generic",
    "id": "",
    "total_amount_quote": 1000,
    # exchange_pair_1 and exchange_pair_2 are nested ConnectorPair objects
    "exchange_pair_1": {
        "connector_name": "binance",
        "trading_pair": "SOL-USDT",
    },
    "exchange_pair_2": {
        "connector_name": "jupiter/router",
        "trading_pair": "SOL-USDC",
    },
    "min_profitability": 0.005,  # 0.5% (more realistic)
    "delay_between_executors": 5,  # 5 seconds
    "max_executors_imbalance": 2,
    "rate_connector": "binance",
    "quote_conversion_asset": "USDT",
    # Base fields from ControllerConfigBase
    "manual_kill_switch": None,
    "candles_config": [],
    "backtest_interval": "5m",      # timeframe delle candele (1m, 5m, 15m, 1h, 4h, 1d)
    "backtest_candles": 500,        # numero di candele da fetchare (max 1000)
}

# ============================================
# FIELD DEFINITIONS
# ============================================

FIELDS: Dict[str, ControllerField] = {
    "id": ControllerField(
        name="id", label="Config ID", type="str", required=True, hint="Auto-generated"
    ),
    "total_amount_quote": ControllerField(
        name="total_amount_quote", label="Total Amount (Quote)", type="float",
        required=True, hint="Total capital in quote asset (e.g. 1000 USDT)"
    ),
    "exchange_pair_1_connector": ControllerField(
        name="exchange_pair_1_connector", label="Exchange 1 Connector", type="str",
        required=True, hint="e.g. binance, kucoin, hyperliquid_perpetual"
    ),
    "exchange_pair_1_pair": ControllerField(
        name="exchange_pair_1_pair", label="Exchange 1 Pair", type="str",
        required=True, hint="e.g. SOL-USDT"
    ),
    "exchange_pair_2_connector": ControllerField(
        name="exchange_pair_2_connector", label="Exchange 2 Connector", type="str",
        required=True, hint="e.g. jupiter/router, uniswap/ethereum"
    ),
    "exchange_pair_2_pair": ControllerField(
        name="exchange_pair_2_pair", label="Exchange 2 Pair", type="str",
        required=True, hint="e.g. SOL-USDC (can differ if quote assets differ)"
    ),
    "min_profitability": ControllerField(
        name="min_profitability", label="Min Profitability", type="float",
        required=True, hint="Min profit to execute (e.g. 0.01 = 1%)", default=0.005
    ),
    "delay_between_executors": ControllerField(
        name="delay_between_executors", label="Delay Between Exec (s)", type="int",
        required=False, hint="Seconds between executor creation (default: 5)", default=5
    ),
    "max_executors_imbalance": ControllerField(
        name="max_executors_imbalance", label="Max Imbalance", type="int",
        required=False, hint="Max buy/sell imbalance before pausing (default: 2)", default=2
    ),
    "rate_connector": ControllerField(
        name="rate_connector", label="Rate Connector", type="str",
        required=False, hint="CEX for conversion rates, e.g. binance", default="binance"
    ),
    "quote_conversion_asset": ControllerField(
        name="quote_conversion_asset", label="Quote Conversion Asset", type="str",
        required=False, hint="Asset to normalize profits, e.g. USDT", default="USDT"
    ),
    "manual_kill_switch": ControllerField(
        name="manual_kill_switch", label="Kill Switch", type="bool",
        required=False, hint="Manual kill switch", default=None
    ),
    "backtest_interval": ControllerField(
        name="backtest_interval", label="Backtest Interval", type="str",
        required=False, hint="Candle timeframe (1m, 5m, 15m, 1h, 4h, 1d)", default="5m"
    ),
    "backtest_candles": ControllerField(
        name="backtest_candles", label="Backtest Candles", type="int",
        required=False, hint="Number of candles for backtest (100-1000)", default=500
    ),
    "fee_rate_exchange_1": ControllerField(
        name="fee_rate_exchange_1",
        label="Fee Rate Exchange 1",
        type="float",
        required=False,
        hint="Trading fee exchange 1 (e.g. 0.001 = 0.1%)",
        default=0.001
    ),

    "fee_rate_exchange_2": ControllerField(
        name="fee_rate_exchange_2",
        label="Fee Rate Exchange 2",
        type="float",
        required=False,
        hint="Trading fee exchange 2 (e.g. 0.001 = 0.1%)",
        default=0.001
    ),
}

FIELD_ORDER: List[str] = [
    "id",
    "delay_between_executors",
    "exchange_pair_1_connector","exchange_pair_1_pair",
    "exchange_pair_2_connector", "exchange_pair_2_pair",
    "fee_rate_exchange_1",
    "fee_rate_exchange_2",
    "max_executors_imbalance",
    "min_profitability", 
    "quote_conversion_asset",
    "rate_connector",
    "total_amount_quote"
]

EDITABLE_FIELDS: List[str] = [
    "delay_between_executors",
    "exchange_pair_1_connector", "exchange_pair_1_pair",
    "exchange_pair_2_connector", "exchange_pair_2_pair",
    "fee_rate_exchange_1",
    "fee_rate_exchange_2",
    "max_executors_imbalance",
    "min_profitability",
    "quote_conversion_asset",
    "rate_connector",
    "total_amount_quote"

]

# ============================================
# HELPER FUNCTIONS
# ============================================

def get_flat_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract flat key=value fields for the edit form."""
    ep1 = config.get("exchange_pair_1", {}) or {}
    ep2 = config.get("exchange_pair_2", {}) or {}


    return {
        "total_amount_quote": config.get("total_amount_quote", 1000),
        "exchange_pair_1_connector": ep1.get("connector_name", ""),
        "exchange_pair_1_pair": ep1.get("trading_pair", ""),
        "exchange_pair_2_connector": ep2.get("connector_name", ""),
        "exchange_pair_2_pair": ep2.get("trading_pair", ""),
        "min_profitability": config.get("min_profitability", 0.005),
        "delay_between_executors": config.get("delay_between_executors", 5),
        "max_executors_imbalance": config.get("max_executors_imbalance", 2),
        "rate_connector": config.get("rate_connector", "binance"),
        "quote_conversion_asset": config.get("quote_conversion_asset", "USDT"),
        "fee_rate_exchange_1": config.get("fee_rate_exchange_1", 0.001),
        "fee_rate_exchange_2": config.get("fee_rate_exchange_2", 0.001),
    }


def apply_flat_fields(config: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Apply flat key=value updates back into the nested config structure."""
    for key, value in updates.items():
        if key == "exchange_pair_1_connector":
            if "exchange_pair_1" not in config:
                config["exchange_pair_1"] = {}
            config["exchange_pair_1"]["connector_name"] = value
        elif key == "exchange_pair_1_pair":
            if "exchange_pair_1" not in config:
                config["exchange_pair_1"] = {}
            config["exchange_pair_1"]["trading_pair"] = value
        elif key == "exchange_pair_2_connector":
            if "exchange_pair_2" not in config:
                config["exchange_pair_2"] = {}
            config["exchange_pair_2"]["connector_name"] = value
        elif key == "exchange_pair_2_pair":
            if "exchange_pair_2" not in config:
                config["exchange_pair_2"] = {}
            config["exchange_pair_2"]["trading_pair"] = value
        else:
            config[key] = value
    return config


def validate_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate configuration with enhanced business logic."""
    ep1 = config.get("exchange_pair_1", {}) or {}
    ep2 = config.get("exchange_pair_2", {}) or {}

    # Basic validation
    if not ep1.get("connector_name"):
        return False, "Missing exchange_pair_1 connector_name"
    if not ep1.get("trading_pair"):
        return False, "Missing exchange_pair_1 trading_pair"
    if not ep2.get("connector_name"):
        return False, "Missing exchange_pair_2 connector_name"
    if not ep2.get("trading_pair"):
        return False, "Missing exchange_pair_2 trading_pair"

    total_amount = float(config.get("total_amount_quote", 0))
    if total_amount <= 0:
        return False, "total_amount_quote must be positive"

    # Validate base assets match
    pair1 = ep1.get("trading_pair", "")
    pair2 = ep2.get("trading_pair", "")

    if "-" in pair1 and "-" in pair2:
        base1 = pair1.split("-")[0]
        base2 = pair2.split("-")[0]

        if base1 != base2:
            return False, f"Base assets must match: {base1} vs {base2}"

    # Validate profitability
    min_prof = float(config.get("min_profitability", 0))
    if min_prof <= 0:
        return False, "min_profitability must be positive"
    if min_prof > 0.5:
        return False, "min_profitability too high (>50%)"

    # Validate delay
    delay = config.get("delay_between_executors", 5)
    if delay < 1:
        return False, "delay_between_executors must be at least 1 second"
    return True, None


def generate_id(config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
    """Generate unique ID for the configuration."""
    max_num = 0
    for cfg in existing_configs:
        parts = cfg.get("id", "").split("_", 1)
        if parts and parts[0].isdigit():
            max_num = max(max_num, int(parts[0]))
    seq = str(max_num + 1).zfill(3)

    ep1 = config.get("exchange_pair_1", {}) or {}
    ep2 = config.get("exchange_pair_2", {}) or {}
    c1 = ep1.get("connector_name", "ex1").replace("_perpetual", "").replace("_spot", "").replace("/", "-")
    c2 = ep2.get("connector_name", "ex2").replace("_perpetual", "").replace("_spot", "").replace("/", "-")
    pair = ep1.get("trading_pair", "UNKNOWN").replace("-", "_")
    return f"{seq}_arb_{c1}_{c2}_{pair}"


# ============================================
# EXCHANGE DATA FETCHER (internal)
# ============================================


async def _get_current_price(connector_name: str, trading_pair: str, use_mid: bool = True) -> Optional[float]:
    """Get current price from connector."""
    cache_key = f"price_{connector_name}_{trading_pair}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from hummingbot.client.hummingbot_application import HummingbotApplication
        app = HummingbotApplication.main_application()

        if connector_name in app.connectors:
            connector = app.connectors[connector_name]
            price = connector.get_price(trading_pair, False)
            if price:
                _cache.set(cache_key, float(price))
                return float(price)
    except Exception as e:
        logger.debug(f"Failed to get price: {e}")

    return None
