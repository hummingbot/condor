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
    # NEW: Auto-optimization and advanced settings
    "auto_optimize": True,
    "max_slippage": 0.005,  # 0.5% max slippage
    "use_market_mid_price": True,
    "min_gas_balance": 0.05,  # Minimum gas token balance (e.g., 0.05 SOL)
    "grid_analysis": {
        "enabled": False,
        "num_levels": 10,
        "spread_percentage": 0.01
    }
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
    # NEW FIELDS
    "auto_optimize": ControllerField(
        name="auto_optimize", label="Auto-Optimize", type="bool",
        required=False, hint="Automatically optimize parameters based on market conditions", default=True
    ),
    "max_slippage": ControllerField(
        name="max_slippage", label="Max Slippage", type="float",
        required=False, hint="Maximum allowed slippage (e.g., 0.005 = 0.5%)", default=0.005
    ),
    "use_market_mid_price": ControllerField(
        name="use_market_mid_price", label="Use Mid Price", type="bool",
        required=False, hint="Use mid price instead of bid/ask for calculations", default=True
    ),
    "min_gas_balance": ControllerField(
        name="min_gas_balance", label="Min Gas Balance", type="float",
        required=False, hint="Minimum gas token balance required (e.g., 0.05 SOL)", default=0.05
    ),
    "grid_analysis_enabled": ControllerField(
        name="grid_analysis_enabled", label="Grid Analysis", type="bool",
        required=False, hint="Enable grid analysis for optimal entry points", default=False
    ),
    "grid_analysis_num_levels": ControllerField(
        name="grid_analysis_num_levels", label="Grid Levels", type="int",
        required=False, hint="Number of grid levels for analysis", default=10
    ),
    "grid_analysis_spread": ControllerField(
        name="grid_analysis_spread", label="Grid Spread", type="float",
        required=False, hint="Spread percentage between grid levels", default=0.01
    ),
}

FIELD_ORDER: List[str] = [
    "id", "total_amount_quote",
    "exchange_pair_1_connector", "exchange_pair_1_pair",
    "exchange_pair_2_connector", "exchange_pair_2_pair",
    "min_profitability", "delay_between_executors",
    "max_executors_imbalance", "rate_connector", "quote_conversion_asset",
]

EDITABLE_FIELDS: List[str] = [
    "total_amount_quote",
    "exchange_pair_1_connector", "exchange_pair_1_pair",
    "exchange_pair_2_connector", "exchange_pair_2_pair",
    "min_profitability", "delay_between_executors",
    "max_executors_imbalance", "rate_connector", "quote_conversion_asset",
]

# ============================================
# HELPER FUNCTIONS
# ============================================

def get_flat_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract flat key=value fields for the edit form."""
    ep1 = config.get("exchange_pair_1", {}) or {}
    ep2 = config.get("exchange_pair_2", {}) or {}
    grid = config.get("grid_analysis", {}) or {}
    
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

class _ExchangeDataCache:
    """Simple cache for exchange data."""
    _instance = None
    _cache = {}
    _ttl = 1.0  # 1 second cache
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def get(self, key: str):
        if key in self._cache:
            age = asyncio.get_event_loop().time() - self._cache[key]["timestamp"]
            if age < self._ttl:
                return self._cache[key]["data"]
        return None
    
    def set(self, key: str, data: Any):
        self._cache[key] = {
            "data": data,
            "timestamp": asyncio.get_event_loop().time()
        }


_cache = _ExchangeDataCache()


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


async def _get_order_book_depth(connector_name: str, trading_pair: str, side: str = "ask", limit: int = 20):
    """Get order book depth."""
    cache_key = f"depth_{connector_name}_{trading_pair}_{side}_{limit}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    
    try:
        from hummingbot.client.hummingbot_application import HummingbotApplication
        app = HummingbotApplication.main_application()
        
        if connector_name in app.connectors:
            connector = app.connectors[connector_name]
            order_book = connector.get_order_book(trading_pair)
            
            if side == "bid":
                entries = order_book.bid_entries()
            else:
                entries = order_book.ask_entries()
            
            result = [(float(e.price), float(e.amount)) for e in entries[:limit]]
            _cache.set(cache_key, result)
            return result
    except Exception as e:
        logger.debug(f"Failed to get order book: {e}")
    
    return []


async def _calculate_max_amount_without_slippage(
    connector_name: str,
    trading_pair: str,
    max_slippage_pct: float = 0.005
) -> float:
    """Calculate maximum trade amount within slippage tolerance."""
    asks = await _get_order_book_depth(connector_name, trading_pair, "ask", 50)
    
    if not asks:
        return float('inf')
    
    current_price = await _get_current_price(connector_name, trading_pair, use_mid=True)
    if not current_price:
        return float('inf')
    
    cumulative_volume = 0
    cumulative_cost = 0
    
    for price, amount in asks:
        cumulative_volume += amount
        cumulative_cost += price * amount
        
        if cumulative_volume > 0:
            avg_price = cumulative_cost / cumulative_volume
            slippage = abs(avg_price - current_price) / current_price
            
            if slippage > max_slippage_pct:
                return cumulative_volume - amount
    
    return cumulative_volume


async def _get_gas_token_for_connector(connector_name: str) -> Optional[str]:
    """Get gas token for a connector."""
    try:
        from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient
        
        gateway = GatewayHttpClient.get_instance()
        chain, network, error = await gateway.get_connector_chain_network(connector_name)
        
        if not error:
            native_currency = await gateway.get_native_currency_symbol(chain, network)
            return native_currency
    except Exception as e:
        logger.debug(f"Failed to get gas token: {e}")
    
    return None


async def _get_balance(connector_name: str, asset: str) -> Optional[float]:
    """Get balance for an asset."""
    cache_key = f"balance_{connector_name}_{asset}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    
    try:
        from hummingbot.client.hummingbot_application import HummingbotApplication
        app = HummingbotApplication.main_application()
        
        if connector_name in app.connectors:
            connector = app.connectors[connector_name]
            balance = connector.get_balance(asset)
            if balance is not None:
                _cache.set(cache_key, float(balance))
                return float(balance)
    except Exception as e:
        logger.debug(f"Failed to get balance: {e}")
    
    return None


# ============================================
# DYNAMIC VALIDATION AND OPTIMIZATION
# ============================================

async def validate_config_dynamic(config: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """Validate configuration by querying exchanges dynamically."""
    optimized_config = config.copy()
    warnings = []
    errors = []
    suggestions = {}
    
    ep1 = config.get("exchange_pair_1", {}) or {}
    ep2 = config.get("exchange_pair_2", {}) or {}
    
    connector1 = ep1.get("connector_name", "")
    pair1 = ep1.get("trading_pair", "")
    connector2 = ep2.get("connector_name", "")
    pair2 = ep2.get("trading_pair", "")
    
    # 1. Get current prices
    price1 = await _get_current_price(connector1, pair1, config.get("use_market_mid_price", True))
    price2 = await _get_current_price(connector2, pair2, config.get("use_market_mid_price", True))
    
    if price1 and price2:
        current_spread = abs(price1 - price2) / price1
        
        current_min = config.get("min_profitability", 0.005)
        if current_min > current_spread:
            optimal_profit = current_spread * 0.8
            warnings.append(
                f"⚠️ min_profitability ({current_min*100:.2f}%) > current spread ({current_spread*100:.2f}%). "
                f"Consider {optimal_profit*100:.2f}%"
            )
            suggestions["min_profitability"] = optimal_profit
    
    # 2. Calculate optimal amount based on liquidity
    try:
        max_amount1 = await _calculate_max_amount_without_slippage(
            connector1, pair1, config.get("max_slippage", 0.005)
        )
        max_amount2 = await _calculate_max_amount_without_slippage(
            connector2, pair2, config.get("max_slippage", 0.005)
        )
        
        optimal_amount = min(max_amount1, max_amount2, config.get("total_amount_quote", 1000))
        
        if price1:
            optimal_amount_quote = optimal_amount * price1
            current_amount = config.get("total_amount_quote", 1000)
            
            if optimal_amount_quote < current_amount:
                warnings.append(
                    f"⚠️ total_amount_quote ({current_amount}) may cause > {config.get('max_slippage', 0.005)*100:.1f}% slippage. "
                    f"Suggested: {optimal_amount_quote:.2f}"
                )
                suggestions["total_amount_quote"] = optimal_amount_quote
    except Exception as e:
        warnings.append(f"⚠️ Could not calculate optimal amount: {e}")
    
    # 3. Check gas balance for DEX connectors
    for connector, name in [(connector2, "Exchange 2")]:
        if "jupiter" in connector or "router" in connector or "uniswap" in connector:
            gas_token = await _get_gas_token_for_connector(connector)
            if gas_token:
                balance = await _get_balance(connector, gas_token)
                min_required = config.get("min_gas_balance", 0.05)
                
                if balance is not None:
                    if balance < min_required:
                        errors.append(
                            f"❌ {name} gas token ({gas_token}) balance: {balance:.4f} "
                            f"< required {min_required}. Please add funds."
                        )
                    elif balance < min_required * 2:
                        warnings.append(
                            f"⚠️ {name} gas token ({gas_token}) balance low: {balance:.4f}"
                        )
    
    # Apply suggestions
    for key, value in suggestions.items():
        optimized_config[key] = value
        optimized_config[f"{key}_auto_optimized"] = True
    
    is_valid = len(errors) == 0
    message = "\n".join(errors + warnings) if errors or warnings else None
    
    return is_valid, message, optimized_config


async def auto_optimize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Automatically optimize configuration based on market conditions."""
    if not config.get("auto_optimize", True):
        return config
    
    logger.info("Running auto-optimization for arbitrage controller...")
    
    is_valid, message, optimized = await validate_config_dynamic(config)
    
    if message:
        logger.info(f"Auto-optimization results:\n{message}")
    
    for key in config:
        if key not in optimized:
            optimized[key] = config[key]
    
    return optimized
