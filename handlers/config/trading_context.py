"""
Trading context configuration and session management

DEPRECATED: This module is maintained for backward compatibility only.
New code should use handlers.config.user_preferences directly.

This module now acts as a thin wrapper around the new centralized
user_preferences module, forwarding all calls to maintain compatibility
with existing code.
"""

import logging
from typing import Dict, Any

# Import from new centralized preferences module
from .user_preferences import (
    # Portfolio
    get_portfolio_prefs as _get_portfolio_prefs,
    get_portfolio_days as _get_portfolio_days,
    get_portfolio_interval as _get_portfolio_interval,
    set_portfolio_days as _set_portfolio_days,
    set_portfolio_interval as _set_portfolio_interval,
    PORTFOLIO_DAYS_OPTIONS,
    PORTFOLIO_INTERVAL_OPTIONS,
    DEFAULT_PORTFOLIO_DAYS,
    DEFAULT_PORTFOLIO_INTERVAL,

    # CLOB
    get_clob_account,
    set_clob_account,
    get_clob_last_order,
    set_clob_last_order,
    DEFAULT_CLOB_ACCOUNT,

    # DEX
    get_dex_last_swap,
    set_dex_last_swap,
    get_dex_last_pool,
    set_dex_last_pool,
    get_dex_connector,
    DEFAULT_DEX_NETWORK,
)

logger = logging.getLogger(__name__)

# Re-export constants for backward compatibility
DEFAULT_TRADING_ACCOUNT = DEFAULT_CLOB_ACCOUNT
TRADING_CONTEXT_KEY = "trading_context"  # Deprecated, kept for reference
PORTFOLIO_CONFIG_KEY = "portfolio_config"  # Deprecated, kept for reference


# ============================================
# BACKWARD COMPATIBLE FUNCTIONS
# These wrap the new user_preferences module
# ============================================

def get_default_account() -> str:
    """Get the default trading account name"""
    return DEFAULT_TRADING_ACCOUNT


def get_default_dex_connector(network: str = DEFAULT_DEX_NETWORK) -> str:
    """
    Get default DEX connector based on network

    Args:
        network: Network name (e.g., "solana-mainnet-beta", "ethereum-mainnet")

    Returns:
        Default connector name for the network
    """
    if network.startswith("solana"):
        return "jupiter"
    elif network.startswith("ethereum"):
        return "uniswap"
    else:
        return "jupiter"


def get_trading_context(user_data: Dict) -> Dict[str, Any]:
    """
    DEPRECATED: Use user_preferences module directly.

    Get trading context - reconstructs old format from new preferences
    """
    return {
        "account": get_clob_account(user_data),
        "last_clob": get_clob_last_order(user_data),
        "last_dex_swap": get_dex_last_swap(user_data),
        "last_dex_pool": get_dex_last_pool(user_data),
    }


def update_trading_context(user_data: Dict, context_type: str, data: Dict[str, Any]) -> None:
    """
    DEPRECATED: Use specific set functions from user_preferences.

    Update trading context
    """
    if context_type == "account":
        set_clob_account(user_data, data.get("account", DEFAULT_TRADING_ACCOUNT))
    elif context_type == "last_clob":
        set_clob_last_order(user_data, data)
    elif context_type == "last_dex_swap":
        set_dex_last_swap(user_data, data)
    elif context_type == "last_dex_pool":
        set_dex_last_pool(user_data, data)


def get_last_clob_params(user_data: Dict) -> Dict[str, Any]:
    """Get last used CLOB trading parameters"""
    return get_clob_last_order(user_data)


def get_last_dex_swap_params(user_data: Dict) -> Dict[str, Any]:
    """Get last used DEX swap parameters"""
    return get_dex_last_swap(user_data)


def get_last_dex_pool_params(user_data: Dict) -> Dict[str, Any]:
    """Get last used DEX pool parameters"""
    return get_dex_last_pool(user_data)


def set_last_clob_params(user_data: Dict, params: Dict[str, Any]) -> None:
    """Save last used CLOB parameters"""
    set_clob_last_order(user_data, params)


def set_last_dex_swap_params(user_data: Dict, params: Dict[str, Any]) -> None:
    """Save last used DEX swap parameters"""
    set_dex_last_swap(user_data, params)


def set_last_dex_pool_params(user_data: Dict, params: Dict[str, Any]) -> None:
    """Save last used DEX pool parameters"""
    set_dex_last_pool(user_data, params)


def get_account(user_data: Dict) -> str:
    """Get the trading account for a user (defaults to master_account)"""
    return get_clob_account(user_data)


def set_account(user_data: Dict, account: str) -> None:
    """Set the trading account for a user"""
    set_clob_account(user_data, account)


def clear_trading_context(user_data: Dict) -> None:
    """Clear all trading context for a user"""
    # This is now a no-op since we don't want to clear all preferences
    # Just clear the last order/swap params
    from .user_preferences import _ensure_preferences
    prefs = _ensure_preferences(user_data)
    prefs["clob"]["last_order"] = {}
    prefs["dex"]["last_swap"] = {}
    prefs["dex"]["last_pool"] = {}
    logger.info("Cleared trading context (last order/swap/pool params)")


# ============================================
# PORTFOLIO CONFIGURATION (Backward Compatible)
# ============================================

def _ensure_portfolio_config(user_data: Dict) -> None:
    """DEPRECATED: Portfolio config is now part of user_preferences"""
    pass  # Migration handled by user_preferences module


def get_portfolio_config(user_data: Dict) -> Dict[str, Any]:
    """Get portfolio configuration"""
    return _get_portfolio_prefs(user_data)


def set_portfolio_days(user_data: Dict, days: int) -> None:
    """Set portfolio evolution days"""
    _set_portfolio_days(user_data, days)


def set_portfolio_interval(user_data: Dict, interval: str) -> None:
    """Set portfolio data interval"""
    _set_portfolio_interval(user_data, interval)
