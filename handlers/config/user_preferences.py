"""
Centralized User Preferences Management

This module provides a unified interface for managing all user preferences
across the bot. All user-specific settings should be accessed through this module.

Structure:
- Portfolio settings (graph days, interval)
- CLOB trading defaults (account, connector, pair, last order params)
- DEX trading defaults (network, connector, slippage, last swap params)
- General settings (active server)

Uses telegram-python-bot's persistence via context.user_data
Data is automatically saved to pickle file and persists across bot restarts
"""

import logging
from typing import Dict, Any, Optional, TypedDict
from copy import deepcopy

logger = logging.getLogger(__name__)


# ============================================
# CONSTANTS AND DEFAULTS
# ============================================

USER_PREFERENCES_KEY = "user_preferences"

# Portfolio defaults
DEFAULT_PORTFOLIO_DAYS = 3
DEFAULT_PORTFOLIO_INTERVAL = "1h"
PORTFOLIO_DAYS_OPTIONS = [1, 3, 7, 14, 30]
PORTFOLIO_INTERVAL_OPTIONS = ["15m", "1h", "4h", "1d"]

# CLOB trading defaults
DEFAULT_CLOB_ACCOUNT = "master_account"
DEFAULT_CLOB_CONNECTOR = "binance_perpetual"
DEFAULT_CLOB_PAIR = "BTC-USDT"
DEFAULT_CLOB_ORDER_TYPE = "MARKET"
DEFAULT_CLOB_SIDE = "BUY"
DEFAULT_CLOB_POSITION_MODE = "OPEN"
DEFAULT_CLOB_AMOUNT = "$10"

# DEX trading defaults
DEFAULT_DEX_NETWORK = "solana-mainnet-beta"
DEFAULT_DEX_CONNECTOR = "jupiter"
DEFAULT_DEX_PAIR = "SOL-USDC"
DEFAULT_DEX_SLIPPAGE = "1.0"
DEFAULT_DEX_SIDE = "BUY"
DEFAULT_DEX_AMOUNT = "1.0"


# ============================================
# TYPE DEFINITIONS
# ============================================

class PortfolioPrefs(TypedDict, total=False):
    days: int
    interval: str


class CLOBOrderParams(TypedDict, total=False):
    connector: str
    trading_pair: str
    side: str
    order_type: str
    position_mode: str
    amount: str
    price: str


class CLOBPrefs(TypedDict, total=False):
    account: str
    default_connector: str
    default_pair: str
    last_order: CLOBOrderParams


class DEXSwapParams(TypedDict, total=False):
    connector: str
    network: str
    trading_pair: str
    side: str
    amount: str
    slippage: str


class DEXPoolParams(TypedDict, total=False):
    connector: str
    network: str
    pool_address: str


class DEXPrefs(TypedDict, total=False):
    default_network: str
    default_connector: str
    default_slippage: str
    last_swap: DEXSwapParams
    last_pool: DEXPoolParams


class GeneralPrefs(TypedDict, total=False):
    active_server: Optional[str]


class UserPreferences(TypedDict, total=False):
    portfolio: PortfolioPrefs
    clob: CLOBPrefs
    dex: DEXPrefs
    general: GeneralPrefs


# ============================================
# INTERNAL HELPERS
# ============================================

def _get_default_preferences() -> UserPreferences:
    """Get default preferences structure"""
    return {
        "portfolio": {
            "days": DEFAULT_PORTFOLIO_DAYS,
            "interval": DEFAULT_PORTFOLIO_INTERVAL,
        },
        "clob": {
            "account": DEFAULT_CLOB_ACCOUNT,
            "default_connector": DEFAULT_CLOB_CONNECTOR,
            "default_pair": DEFAULT_CLOB_PAIR,
            "last_order": {},
        },
        "dex": {
            "default_network": DEFAULT_DEX_NETWORK,
            "default_connector": DEFAULT_DEX_CONNECTOR,
            "default_slippage": DEFAULT_DEX_SLIPPAGE,
            "last_swap": {},
            "last_pool": {},
        },
        "general": {
            "active_server": None,
        },
    }


def _ensure_preferences(user_data: Dict) -> UserPreferences:
    """Ensure preferences exist in user_data with proper structure"""
    if USER_PREFERENCES_KEY not in user_data:
        user_data[USER_PREFERENCES_KEY] = _get_default_preferences()
    else:
        # Ensure all sections exist (for backward compatibility)
        prefs = user_data[USER_PREFERENCES_KEY]
        defaults = _get_default_preferences()

        for section, section_defaults in defaults.items():
            if section not in prefs:
                prefs[section] = section_defaults
            elif isinstance(section_defaults, dict):
                # Ensure all keys in section exist
                for key, default_value in section_defaults.items():
                    if key not in prefs[section]:
                        prefs[section][key] = default_value

    return user_data[USER_PREFERENCES_KEY]


def _migrate_legacy_data(user_data: Dict) -> None:
    """Migrate data from old format to new unified preferences

    This handles backward compatibility with existing user data that may have:
    - trading_context: old CLOB/DEX trading context
    - portfolio_config: old portfolio settings
    """
    prefs = _ensure_preferences(user_data)
    migrated = False

    # Migrate old trading_context
    if "trading_context" in user_data:
        old_context = user_data["trading_context"]

        # Migrate account
        if "account" in old_context and old_context["account"]:
            prefs["clob"]["account"] = old_context["account"]
            migrated = True

        # Migrate last_clob
        if "last_clob" in old_context and old_context["last_clob"]:
            prefs["clob"]["last_order"].update(old_context["last_clob"])
            migrated = True

        # Migrate last_dex_swap
        if "last_dex_swap" in old_context and old_context["last_dex_swap"]:
            prefs["dex"]["last_swap"].update(old_context["last_dex_swap"])
            migrated = True

        # Migrate last_dex_pool
        if "last_dex_pool" in old_context and old_context["last_dex_pool"]:
            prefs["dex"]["last_pool"].update(old_context["last_dex_pool"])
            migrated = True

        # Remove old key after migration
        del user_data["trading_context"]
        logger.info("Migrated trading_context to user_preferences")

    # Migrate old portfolio_config
    if "portfolio_config" in user_data:
        old_config = user_data["portfolio_config"]

        if "days" in old_config:
            prefs["portfolio"]["days"] = old_config["days"]
            migrated = True

        if "interval" in old_config:
            prefs["portfolio"]["interval"] = old_config["interval"]
            migrated = True

        # Remove old key after migration
        del user_data["portfolio_config"]
        logger.info("Migrated portfolio_config to user_preferences")

    if migrated:
        logger.info("Legacy data migration completed")


# ============================================
# PUBLIC API - GET PREFERENCES
# ============================================

def get_preferences(user_data: Dict) -> UserPreferences:
    """Get all user preferences (read-only copy)

    Args:
        user_data: context.user_data from telegram update

    Returns:
        Complete user preferences dictionary
    """
    _migrate_legacy_data(user_data)
    return deepcopy(_ensure_preferences(user_data))


def get_portfolio_prefs(user_data: Dict) -> PortfolioPrefs:
    """Get portfolio preferences

    Returns:
        Portfolio preferences with days and interval
    """
    _migrate_legacy_data(user_data)
    prefs = _ensure_preferences(user_data)
    return {
        "days": prefs["portfolio"].get("days", DEFAULT_PORTFOLIO_DAYS),
        "interval": prefs["portfolio"].get("interval", DEFAULT_PORTFOLIO_INTERVAL),
    }


def get_clob_prefs(user_data: Dict) -> CLOBPrefs:
    """Get CLOB trading preferences

    Returns:
        CLOB preferences with account, defaults, and last order params
    """
    _migrate_legacy_data(user_data)
    prefs = _ensure_preferences(user_data)
    return deepcopy(prefs["clob"])


def get_dex_prefs(user_data: Dict) -> DEXPrefs:
    """Get DEX trading preferences

    Returns:
        DEX preferences with network, connector, slippage, and last params
    """
    _migrate_legacy_data(user_data)
    prefs = _ensure_preferences(user_data)
    return deepcopy(prefs["dex"])


def get_general_prefs(user_data: Dict) -> GeneralPrefs:
    """Get general preferences

    Returns:
        General preferences with active_server
    """
    _migrate_legacy_data(user_data)
    prefs = _ensure_preferences(user_data)
    return deepcopy(prefs["general"])


# ============================================
# PUBLIC API - PORTFOLIO
# ============================================

def get_portfolio_days(user_data: Dict) -> int:
    """Get portfolio graph days setting"""
    return get_portfolio_prefs(user_data).get("days", DEFAULT_PORTFOLIO_DAYS)


def get_portfolio_interval(user_data: Dict) -> str:
    """Get portfolio graph interval setting"""
    return get_portfolio_prefs(user_data).get("interval", DEFAULT_PORTFOLIO_INTERVAL)


def set_portfolio_days(user_data: Dict, days: int) -> None:
    """Set portfolio graph days"""
    prefs = _ensure_preferences(user_data)
    prefs["portfolio"]["days"] = days
    logger.info(f"Set portfolio days to {days}")


def set_portfolio_interval(user_data: Dict, interval: str) -> None:
    """Set portfolio graph interval"""
    prefs = _ensure_preferences(user_data)
    prefs["portfolio"]["interval"] = interval
    logger.info(f"Set portfolio interval to {interval}")


# ============================================
# PUBLIC API - CLOB TRADING
# ============================================

def get_clob_account(user_data: Dict) -> str:
    """Get CLOB trading account"""
    return get_clob_prefs(user_data).get("account", DEFAULT_CLOB_ACCOUNT)


def set_clob_account(user_data: Dict, account: str) -> None:
    """Set CLOB trading account"""
    prefs = _ensure_preferences(user_data)
    prefs["clob"]["account"] = account
    logger.info(f"Set CLOB account to {account}")


def get_clob_last_order(user_data: Dict) -> CLOBOrderParams:
    """Get last CLOB order parameters"""
    return deepcopy(get_clob_prefs(user_data).get("last_order", {}))


def set_clob_last_order(user_data: Dict, params: CLOBOrderParams) -> None:
    """Set last CLOB order parameters (for quick trading)"""
    prefs = _ensure_preferences(user_data)
    prefs["clob"]["last_order"].update(params)
    logger.info(f"Updated CLOB last_order params")


def get_clob_order_defaults(user_data: Dict) -> CLOBOrderParams:
    """Get default values for a new CLOB order

    Returns merged defaults from:
    1. System defaults
    2. User's configured defaults
    3. Last order params (highest priority)
    """
    prefs = get_clob_prefs(user_data)
    last_order = prefs.get("last_order", {})

    return {
        "connector": last_order.get("connector", prefs.get("default_connector", DEFAULT_CLOB_CONNECTOR)),
        "trading_pair": last_order.get("trading_pair", prefs.get("default_pair", DEFAULT_CLOB_PAIR)),
        "side": DEFAULT_CLOB_SIDE,
        "order_type": DEFAULT_CLOB_ORDER_TYPE,
        "position_mode": DEFAULT_CLOB_POSITION_MODE,
        "amount": DEFAULT_CLOB_AMOUNT,
        "price": "88000",
    }


# ============================================
# PUBLIC API - DEX TRADING
# ============================================

def get_dex_network(user_data: Dict) -> str:
    """Get default DEX network"""
    return get_dex_prefs(user_data).get("default_network", DEFAULT_DEX_NETWORK)


def get_dex_connector(user_data: Dict, network: Optional[str] = None) -> str:
    """Get DEX connector for a network

    Args:
        user_data: User data dict
        network: Optional network to get connector for. If None, uses default network.

    Returns:
        Connector name (jupiter, uniswap, etc.)
    """
    if network is None:
        network = get_dex_network(user_data)

    # Network-specific connector mapping
    if network.startswith("solana"):
        return "jupiter"
    elif network.startswith("ethereum"):
        return "uniswap"

    # Fall back to user preference or default
    return get_dex_prefs(user_data).get("default_connector", DEFAULT_DEX_CONNECTOR)


def get_dex_slippage(user_data: Dict) -> str:
    """Get default DEX slippage percentage"""
    return get_dex_prefs(user_data).get("default_slippage", DEFAULT_DEX_SLIPPAGE)


def set_dex_slippage(user_data: Dict, slippage: str) -> None:
    """Set default DEX slippage percentage"""
    prefs = _ensure_preferences(user_data)
    prefs["dex"]["default_slippage"] = slippage
    logger.info(f"Set DEX slippage to {slippage}%")


def get_dex_last_swap(user_data: Dict) -> DEXSwapParams:
    """Get last DEX swap parameters"""
    return deepcopy(get_dex_prefs(user_data).get("last_swap", {}))


def set_dex_last_swap(user_data: Dict, params: DEXSwapParams) -> None:
    """Set last DEX swap parameters (for quick trading)"""
    prefs = _ensure_preferences(user_data)
    prefs["dex"]["last_swap"].update(params)
    logger.info(f"Updated DEX last_swap params")


def get_dex_last_pool(user_data: Dict) -> DEXPoolParams:
    """Get last DEX pool parameters"""
    return deepcopy(get_dex_prefs(user_data).get("last_pool", {}))


def set_dex_last_pool(user_data: Dict, params: DEXPoolParams) -> None:
    """Set last DEX pool parameters"""
    prefs = _ensure_preferences(user_data)
    prefs["dex"]["last_pool"].update(params)
    logger.info(f"Updated DEX last_pool params")


def get_dex_swap_defaults(user_data: Dict) -> DEXSwapParams:
    """Get default values for a new DEX swap

    Returns merged defaults from:
    1. System defaults
    2. User's configured defaults
    3. Last swap params (highest priority)
    """
    prefs = get_dex_prefs(user_data)
    last_swap = prefs.get("last_swap", {})

    network = last_swap.get("network", prefs.get("default_network", DEFAULT_DEX_NETWORK))

    return {
        "connector": last_swap.get("connector", get_dex_connector(user_data, network)),
        "network": network,
        "trading_pair": last_swap.get("trading_pair", DEFAULT_DEX_PAIR),
        "side": DEFAULT_DEX_SIDE,
        "amount": DEFAULT_DEX_AMOUNT,
        "slippage": last_swap.get("slippage", prefs.get("default_slippage", DEFAULT_DEX_SLIPPAGE)),
    }


# ============================================
# PUBLIC API - GENERAL
# ============================================

def get_active_server(user_data: Dict) -> Optional[str]:
    """Get active server name"""
    return get_general_prefs(user_data).get("active_server")


def set_active_server(user_data: Dict, server_name: Optional[str]) -> None:
    """Set active server name"""
    prefs = _ensure_preferences(user_data)
    prefs["general"]["active_server"] = server_name
    logger.info(f"Set active server to {server_name}")


# ============================================
# UTILITY FUNCTIONS
# ============================================

def clear_preferences(user_data: Dict) -> None:
    """Clear all user preferences (reset to defaults)"""
    if USER_PREFERENCES_KEY in user_data:
        del user_data[USER_PREFERENCES_KEY]
    logger.info("Cleared all user preferences")


def export_preferences(user_data: Dict) -> Dict[str, Any]:
    """Export all preferences as a dictionary (for debugging/backup)"""
    return get_preferences(user_data)
