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
from copy import deepcopy
from typing import Any, Dict, List, Optional, TypedDict

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

# Unified trade defaults
DEFAULT_TRADE_CONNECTOR_TYPE = "dex"  # "cex" or "dex"
DEFAULT_TRADE_CONNECTOR_NAME = (
    "solana-mainnet-beta"  # For DEX: network ID, for CEX: connector name
)


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


class WalletNetworkPrefs(TypedDict, total=False):
    """Network preferences for a specific wallet.

    Keys are wallet addresses, values are lists of enabled network IDs.
    Example: {"0x1234...": ["ethereum-mainnet", "base", "arbitrum"]}
    """

    pass  # Dynamic keys based on wallet addresses


class GatewayPrefs(TypedDict, total=False):
    """Gateway-related preferences including wallet network settings."""

    wallet_networks: Dict[str, list]  # wallet_address -> list of enabled network IDs


class UnifiedTradePrefs(TypedDict, total=False):
    """Unified trade preferences for /trade command.

    Tracks which connector type (CEX/DEX) and which specific connector
    was last used, so the unified /trade command can show the right UI.
    """

    last_connector_type: str  # "cex" or "dex"
    last_connector_name: str  # e.g., "jupiter", "binance_perpetual"


class ExecutorPrefs(TypedDict, total=False):
    """Executor preferences for /executors command.

    Tracks recently deployed pairs and last-used config params per executor type
    so users don't have to re-enter the same values each time.
    """

    deployed_pairs: List[str]  # Last 8 deployed trading pairs (MRU order)
    last_grid: Dict[str, Any]  # Last grid executor params
    last_position: Dict[str, Any]  # Last position executor params


class UserPreferences(TypedDict, total=False):
    portfolio: PortfolioPrefs
    clob: CLOBPrefs
    dex: DEXPrefs
    general: GeneralPrefs
    gateway: GatewayPrefs
    unified_trade: UnifiedTradePrefs
    executors: ExecutorPrefs


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
        "gateway": {
            "wallet_networks": {},  # wallet_address -> list of enabled network IDs
        },
        "unified_trade": {
            "last_connector_type": DEFAULT_TRADE_CONNECTOR_TYPE,
            "last_connector_name": DEFAULT_TRADE_CONNECTOR_NAME,
        },
        "executors": {
            "deployed_pairs": [],
            "last_grid": {},
            "last_position": {},
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

    # Migrate executor_deployed_pairs from raw user_data key
    if "executor_deployed_pairs" in user_data:
        old_pairs = user_data["executor_deployed_pairs"]
        if isinstance(old_pairs, list) and old_pairs:
            prefs["executors"]["deployed_pairs"] = old_pairs
            migrated = True
        del user_data["executor_deployed_pairs"]
        logger.info("Migrated executor_deployed_pairs to user_preferences.executors")

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
        "connector": last_order.get(
            "connector", prefs.get("default_connector", DEFAULT_CLOB_CONNECTOR)
        ),
        "trading_pair": last_order.get(
            "trading_pair", prefs.get("default_pair", DEFAULT_CLOB_PAIR)
        ),
        "side": last_order.get("side", DEFAULT_CLOB_SIDE),
        "order_type": last_order.get("order_type", DEFAULT_CLOB_ORDER_TYPE),
        "position_mode": last_order.get("position_mode", DEFAULT_CLOB_POSITION_MODE),
        "amount": last_order.get("amount", DEFAULT_CLOB_AMOUNT),
        "price": last_order.get("price", "88000"),
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

    network = last_swap.get(
        "network", prefs.get("default_network", DEFAULT_DEX_NETWORK)
    )

    return {
        "connector": last_swap.get("connector", get_dex_connector(user_data, network)),
        "network": network,
        "trading_pair": last_swap.get("trading_pair", DEFAULT_DEX_PAIR),
        "side": DEFAULT_DEX_SIDE,
        "amount": DEFAULT_DEX_AMOUNT,
        "slippage": last_swap.get(
            "slippage", prefs.get("default_slippage", DEFAULT_DEX_SLIPPAGE)
        ),
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
# PUBLIC API - GATEWAY / WALLET NETWORKS
# ============================================

# Default networks per chain
DEFAULT_ETHEREUM_NETWORKS = ["ethereum-mainnet", "base", "arbitrum"]
DEFAULT_SOLANA_NETWORKS = ["solana-mainnet-beta"]


def get_gateway_prefs(user_data: Dict) -> GatewayPrefs:
    """Get gateway preferences

    Returns:
        Gateway preferences with wallet_networks
    """
    _migrate_legacy_data(user_data)
    prefs = _ensure_preferences(user_data)
    return deepcopy(prefs.get("gateway", {"wallet_networks": {}}))


def get_wallet_networks(user_data: Dict, wallet_address: str) -> list:
    """Get enabled networks for a specific wallet

    Args:
        user_data: User data dict
        wallet_address: The wallet address

    Returns:
        List of enabled network IDs, or None if not configured (use defaults)
    """
    gateway_prefs = get_gateway_prefs(user_data)
    wallet_networks = gateway_prefs.get("wallet_networks", {})
    return wallet_networks.get(wallet_address)


def set_wallet_networks(user_data: Dict, wallet_address: str, networks: list) -> None:
    """Set enabled networks for a specific wallet

    Args:
        user_data: User data dict
        wallet_address: The wallet address
        networks: List of enabled network IDs
    """
    prefs = _ensure_preferences(user_data)
    if "gateway" not in prefs:
        prefs["gateway"] = {"wallet_networks": {}}
    if "wallet_networks" not in prefs["gateway"]:
        prefs["gateway"]["wallet_networks"] = {}
    prefs["gateway"]["wallet_networks"][wallet_address] = networks
    logger.info(f"Set wallet {wallet_address[:10]}... networks to {networks}")


def remove_wallet_networks(user_data: Dict, wallet_address: str) -> None:
    """Remove network preferences for a wallet (when wallet is deleted)

    Args:
        user_data: User data dict
        wallet_address: The wallet address to remove
    """
    prefs = _ensure_preferences(user_data)
    if "gateway" in prefs and "wallet_networks" in prefs["gateway"]:
        prefs["gateway"]["wallet_networks"].pop(wallet_address, None)
        logger.info(f"Removed wallet {wallet_address[:10]}... network preferences")


def get_default_networks_for_chain(chain: str) -> list:
    """Get default networks for a blockchain chain

    Args:
        chain: The blockchain chain (ethereum, solana)

    Returns:
        List of default network IDs for the chain
    """
    if chain == "ethereum":
        return DEFAULT_ETHEREUM_NETWORKS.copy()
    elif chain == "solana":
        return DEFAULT_SOLANA_NETWORKS.copy()
    return []


def get_all_networks_for_chain(chain: str) -> list:
    """Get all available networks for a blockchain chain

    Args:
        chain: The blockchain chain (ethereum, solana)

    Returns:
        List of all available network IDs for the chain
    """
    if chain == "ethereum":
        return [
            "ethereum-mainnet",
            "base",
            "arbitrum",
            "polygon",
            "optimism",
            "avalanche",
        ]
    elif chain == "solana":
        return [
            "solana-mainnet-beta",
            "solana-devnet",
        ]
    return []


def get_all_enabled_networks(user_data: Dict) -> set:
    """Get all enabled networks across all configured wallets.

    This aggregates networks from all wallet configurations.
    If no wallets are configured, returns None (meaning no filtering).

    Args:
        user_data: User data dict

    Returns:
        Set of enabled network IDs, or None if no wallets configured
    """
    gateway_prefs = get_gateway_prefs(user_data)
    wallet_networks = gateway_prefs.get("wallet_networks", {})

    if not wallet_networks:
        return None  # No wallets configured, don't filter

    # Aggregate all enabled networks from all wallets
    all_networks = set()
    for networks in wallet_networks.values():
        if networks:
            all_networks.update(networks)

    return all_networks if all_networks else None


# ============================================
# PUBLIC API - UNIFIED TRADE
# ============================================


def get_unified_trade_prefs(user_data: Dict) -> UnifiedTradePrefs:
    """Get unified trade preferences

    Returns:
        Unified trade preferences with last_connector_type and last_connector_name
    """
    _migrate_legacy_data(user_data)
    prefs = _ensure_preferences(user_data)
    return deepcopy(
        prefs.get(
            "unified_trade",
            {
                "last_connector_type": DEFAULT_TRADE_CONNECTOR_TYPE,
                "last_connector_name": DEFAULT_TRADE_CONNECTOR_NAME,
            },
        )
    )


def get_last_trade_connector(user_data: Dict) -> tuple:
    """Get last used trade connector type and name

    Returns:
        Tuple of (connector_type, connector_name)
        - For DEX: ("dex", "solana-mainnet-beta") - connector_name is the NETWORK ID
        - For CEX: ("cex", "binance_perpetual") - connector_name is the connector
    """
    prefs = get_unified_trade_prefs(user_data)
    return (
        prefs.get("last_connector_type", DEFAULT_TRADE_CONNECTOR_TYPE),
        prefs.get("last_connector_name", DEFAULT_TRADE_CONNECTOR_NAME),
    )


def set_last_trade_connector(
    user_data: Dict, connector_type: str, connector_name: str
) -> None:
    """Set last used trade connector

    Args:
        user_data: User data dict
        connector_type: "cex" or "dex"
        connector_name: For DEX: network ID (e.g., "solana-mainnet-beta")
                        For CEX: connector name (e.g., "binance_perpetual")
    """
    prefs = _ensure_preferences(user_data)
    if "unified_trade" not in prefs:
        prefs["unified_trade"] = {}
    prefs["unified_trade"]["last_connector_type"] = connector_type
    prefs["unified_trade"]["last_connector_name"] = connector_name
    logger.info(f"Set last trade connector: {connector_type}:{connector_name}")


# ============================================
# PUBLIC API - EXECUTORS
# ============================================


def get_executor_prefs(user_data: Dict) -> ExecutorPrefs:
    """Get executor preferences"""
    _migrate_legacy_data(user_data)
    prefs = _ensure_preferences(user_data)
    return deepcopy(prefs["executors"])


def get_executor_deployed_pairs(user_data: Dict) -> List[str]:
    """Get list of recently deployed trading pairs (MRU order)"""
    return get_executor_prefs(user_data).get("deployed_pairs", [])


def set_executor_deployed_pairs(user_data: Dict, pairs: List[str]) -> None:
    """Set recently deployed trading pairs"""
    prefs = _ensure_preferences(user_data)
    prefs["executors"]["deployed_pairs"] = pairs[:8]


def add_executor_deployed_pair(user_data: Dict, pair: str) -> None:
    """Add a trading pair to the front of the deployed pairs list"""
    prefs = _ensure_preferences(user_data)
    deployed = list(prefs["executors"].get("deployed_pairs", []))
    if pair in deployed:
        deployed.remove(pair)
    deployed.insert(0, pair)
    prefs["executors"]["deployed_pairs"] = deployed[:8]


def get_executor_last_config(user_data: Dict, executor_type: str) -> Dict[str, Any]:
    """Get last-used config params for an executor type

    Args:
        user_data: User data dict
        executor_type: 'grid' or 'position'

    Returns:
        Last-used config dict, or empty dict if none saved
    """
    ep = get_executor_prefs(user_data)
    key = f"last_{executor_type}"
    return ep.get(key, {})


def set_executor_last_config(
    user_data: Dict, executor_type: str, params: Dict[str, Any]
) -> None:
    """Save last-used config params for an executor type

    Args:
        user_data: User data dict
        executor_type: 'grid' or 'position'
        params: Config params to save
    """
    prefs = _ensure_preferences(user_data)
    key = f"last_{executor_type}"
    prefs["executors"][key] = params
    logger.info(f"Updated executor last_{executor_type} config")


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
