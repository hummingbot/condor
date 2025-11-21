"""
Trading context configuration and session management

Manages:
- Default trading account
- Last used trading parameters for quick trading
- Trading session state

Uses telegram-python-bot's persistence via context.user_data
Data is automatically saved to pickle file and persists across bot restarts
"""

import logging
from typing import Dict, Any, Optional
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Default trading account (can be overridden per user)
DEFAULT_TRADING_ACCOUNT = "master_account"

# Default DEX settings
DEFAULT_DEX_NETWORK = "solana-mainnet-beta"

# Key names for persistence
TRADING_CONTEXT_KEY = "trading_context"


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
        # Default fallback
        return "jupiter"


def get_default_account() -> str:
    """Get the default trading account name"""
    return DEFAULT_TRADING_ACCOUNT


def _ensure_trading_context(user_data: Dict) -> None:
    """Ensure trading context exists in user_data"""
    if TRADING_CONTEXT_KEY not in user_data:
        user_data[TRADING_CONTEXT_KEY] = {
            "account": DEFAULT_TRADING_ACCOUNT,
            "last_clob": {},
            "last_dex_swap": {},
            "last_dex_pool": {},
        }


def get_trading_context(user_data: Dict) -> Dict[str, Any]:
    """
    Get trading context from user_data (persisted automatically)

    Args:
        user_data: context.user_data from telegram update

    Returns:
        Dictionary with user's trading context
    """
    _ensure_trading_context(user_data)
    return user_data[TRADING_CONTEXT_KEY]


def update_trading_context(user_data: Dict, context_type: str, data: Dict[str, Any]) -> None:
    """
    Update trading context in user_data (persisted automatically)

    Args:
        user_data: context.user_data from telegram update
        context_type: Type of context ("last_clob", "last_dex_swap", "last_dex_pool", "account")
        data: Data to update
    """
    _ensure_trading_context(user_data)
    context = user_data[TRADING_CONTEXT_KEY]

    if context_type == "account":
        context["account"] = data.get("account", DEFAULT_TRADING_ACCOUNT)
    elif context_type in ["last_clob", "last_dex_swap", "last_dex_pool"]:
        context[context_type].update(data)

    # Update is automatic with persistence
    logger.info(f"Updated {context_type} context (persisted automatically)")


def get_last_clob_params(user_data: Dict) -> Dict[str, Any]:
    """
    Get last used CLOB trading parameters

    Args:
        user_data: context.user_data from telegram update

    Returns:
        Dictionary with last CLOB parameters:
        - connector: Last used connector
        - trading_pair: Last used trading pair
        - side: Last used side (BUY/SELL)
        - amount: Last used amount
        - order_type: Last used order type (MARKET/LIMIT)
    """
    context = get_trading_context(user_data)
    return context.get("last_clob", {})


def get_last_dex_swap_params(user_data: Dict) -> Dict[str, Any]:
    """
    Get last used DEX swap parameters

    Args:
        user_data: context.user_data from telegram update

    Returns:
        Dictionary with last DEX swap parameters:
        - connector: Last used connector (jupiter, 0x)
        - network: Last used network
        - trading_pair: Last used trading pair
        - side: Last used side
        - slippage: Last used slippage
    """
    context = get_trading_context(user_data)
    return context.get("last_dex_swap", {})


def get_last_dex_pool_params(user_data: Dict) -> Dict[str, Any]:
    """
    Get last used DEX pool parameters

    Args:
        user_data: context.user_data from telegram update

    Returns:
        Dictionary with last DEX pool parameters:
        - connector: Last used connector
        - network: Last used network
        - pool_address: Last used pool address
    """
    context = get_trading_context(user_data)
    return context.get("last_dex_pool", {})


def set_last_clob_params(user_data: Dict, params: Dict[str, Any]) -> None:
    """Save last used CLOB parameters"""
    update_trading_context(user_data, "last_clob", params)


def set_last_dex_swap_params(user_data: Dict, params: Dict[str, Any]) -> None:
    """Save last used DEX swap parameters"""
    update_trading_context(user_data, "last_dex_swap", params)


def set_last_dex_pool_params(user_data: Dict, params: Dict[str, Any]) -> None:
    """Save last used DEX pool parameters"""
    update_trading_context(user_data, "last_dex_pool", params)


def get_account(user_data: Dict) -> str:
    """Get the trading account for a user (defaults to master_account)"""
    context = get_trading_context(user_data)
    return context.get("account", DEFAULT_TRADING_ACCOUNT)


def set_account(user_data: Dict, account: str) -> None:
    """Set the trading account for a user"""
    update_trading_context(user_data, "account", {"account": account})


def clear_trading_context(user_data: Dict) -> None:
    """Clear all trading context for a user"""
    if TRADING_CONTEXT_KEY in user_data:
        del user_data[TRADING_CONTEXT_KEY]
        logger.info("Cleared trading context (persisted automatically)")
