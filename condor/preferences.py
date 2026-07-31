"""
Centralized User Preferences Management

This module provides a unified interface for managing all user preferences
across the bot. All user-specific settings should be accessed through this module.

Structure:
- Portfolio settings (graph days, interval)
- CLOB trading defaults (account, connector, pair, last order params)
- DEX trading defaults (network, connector, slippage, last swap params)
- General settings (active server)

Storage:
- Primary: config.yml via ConfigManager (shared across TG + Web)
- Fallback: context.user_data pickle (for session-level state)

When a user_data dict contains '_user_id', changes are synced to ConfigManager
so the web dashboard can also read them.
"""

import logging
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, TypedDict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ============================================
# CONFIG MANAGER SYNC
# ============================================


def _get_user_id(user_data: Dict) -> Optional[int]:
    """Extract user_id from user_data if present."""
    return user_data.get("_user_id")


def _sync_to_cm(user_data: Dict, key: str, value) -> None:
    """Persist a preference to ConfigManager (config.yml) if user_id is known."""
    user_id = _get_user_id(user_data)
    if user_id is None:
        return
    try:
        from config_manager import get_config_manager

        cm = get_config_manager()
        cm.set_user_preference(user_id, key, value)
    except Exception as e:
        logger.debug("Failed to sync preference '%s' to config: %s", key, e)


def _sync_section_to_cm(user_data: Dict, section: str) -> None:
    """Persist an entire preference section to ConfigManager."""
    user_id = _get_user_id(user_data)
    if user_id is None:
        return
    try:
        from config_manager import get_config_manager

        prefs = user_data.get(USER_PREFERENCES_KEY, {})
        section_data = prefs.get(section)
        if section_data is not None:
            cm = get_config_manager()
            cm.set_user_preference(user_id, section, section_data)
    except Exception as e:
        logger.debug("Failed to sync section '%s' to config: %s", section, e)


def _load_from_cm(user_data: Dict) -> None:
    """On first access, hydrate user_data preferences from ConfigManager if empty."""
    user_id = _get_user_id(user_data)
    if user_id is None:
        return
    if user_data.get("_prefs_hydrated"):
        return
    try:
        from config_manager import get_config_manager

        cm = get_config_manager()
        cm_prefs = cm.get_user_preferences(user_id)
        if cm_prefs and USER_PREFERENCES_KEY not in user_data:
            # Bootstrap preferences from config.yml
            defaults = _get_default_preferences()
            for section, section_defaults in defaults.items():
                if section in cm_prefs:
                    if isinstance(section_defaults, dict) and isinstance(
                        cm_prefs[section], dict
                    ):
                        merged = {**section_defaults, **cm_prefs[section]}
                        defaults[section] = merged
                    else:
                        defaults[section] = cm_prefs[section]
            user_data[USER_PREFERENCES_KEY] = defaults
        user_data["_prefs_hydrated"] = True
    except Exception as e:
        logger.debug("Failed to hydrate preferences from config: %s", e)


# ============================================
# CONSTANTS AND DEFAULTS
# ============================================

USER_PREFERENCES_KEY = "user_preferences"
_MIGRATION_DONE_KEY = "_prefs_migration_done"

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


class TradingAgentPrefs(TypedDict, total=False):
    last_strategy_id: Optional[str]  # Last selected strategy
    default_frequency_sec: int  # Default tick frequency
    default_risk_limits: Dict[str, Any]  # Default risk limits


class CustomProviderPrefs(TypedDict, total=False):
    """A saved OpenAI-compatible endpoint (Venice AI, Together, vLLM, ...).

    ``name`` is a user-facing nickname and the stable identifier used in agent
    keys (``custom@<name>:<model-id>``), so it is restricted to characters that
    can't collide with the key separators — see :func:`sanitize_provider_name`.
    """

    name: str
    base_url: str
    api_key: str


class AgentPrefs(TypedDict, total=False):
    default_agent: str  # "claude-code", "gemini", "codex", "copilot"
    show_tool_calls: bool  # Show tool call indicators (default True)
    tool_filter_mode: str  # "essential", "moderate", or "full" for PydanticAI models
    custom_providers: List[CustomProviderPrefs]  # OpenAI-compatible endpoints
    # Mirror of the live chat selection (user_data["agent_llm"]). Kept here so
    # code outside a PTB context — the MCP subprocess, web, background agent
    # runs — can see which model the user is actually on.
    active_agent_key: str


class VoicePrefs(TypedDict, total=False):
    whisper_model: str  # "tiny", "base", "small", "medium", "large-v3"
    language: Optional[str]  # ISO code ("en", "es", ...) or None for auto-detect
    auto_send: bool  # Send message immediately after transcription


# Available whisper models with descriptions
WHISPER_MODELS = {
    "tiny": "Tiny — Fastest, lower accuracy",
    "base": "Base — Good balance (default)",
    "small": "Small — Better accuracy",
    "medium": "Medium — High accuracy, slower",
    "large-v3": "Large v3 — Best accuracy, slowest",
}

# Supported languages for manual selection (subset of Whisper supported languages)
VOICE_LANGUAGES = {
    "": "Auto-detect",
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "no": "Norwegian",
}


class UserPreferences(TypedDict, total=False):
    portfolio: PortfolioPrefs
    clob: CLOBPrefs
    dex: DEXPrefs
    general: GeneralPrefs
    gateway: GatewayPrefs
    unified_trade: UnifiedTradePrefs
    executors: ExecutorPrefs
    agent: AgentPrefs
    voice: VoicePrefs
    trading_agent: TradingAgentPrefs
    notes: Dict[str, str]


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
        "agent": {
            "default_agent": "claude-code",
            "show_tool_calls": True,
            "custom_providers": [],
        },
        "voice": {
            "whisper_model": "small",
            "language": None,  # Auto-detect
            "auto_send": True,
        },
        "trading_agent": {
            "last_strategy_id": None,
            "default_frequency_sec": 60,
            "default_risk_limits": {},
        },
        "notes": {},
    }


def _ensure_preferences(user_data: Dict) -> UserPreferences:
    """Ensure preferences exist in user_data with proper structure"""
    if USER_PREFERENCES_KEY not in user_data:
        user_data[USER_PREFERENCES_KEY] = _get_default_preferences()
        return user_data[USER_PREFERENCES_KEY]

    prefs = user_data[USER_PREFERENCES_KEY]
    defaults = _get_default_preferences()

    # Fast path: all top-level sections already present
    if all(section in prefs for section in defaults):
        return prefs

    # Slow path: fill in missing sections / keys
    for section, section_defaults in defaults.items():
        if section not in prefs:
            prefs[section] = section_defaults
        elif isinstance(section_defaults, dict):
            for key, default_value in section_defaults.items():
                if key not in prefs[section]:
                    prefs[section][key] = default_value

    return prefs


def _migrate_legacy_data(user_data: Dict) -> None:
    """Migrate data from old format to new unified preferences

    This handles backward compatibility with existing user data that may have:
    - trading_context: old CLOB/DEX trading context
    - portfolio_config: old portfolio settings
    """
    # Always guarantee the preferences structure exists (cheap fast path),
    # even when migration already ran — e.g. after clear_preferences().
    prefs = _ensure_preferences(user_data)

    # Skip migration work once it has already run for this user
    if user_data.get(_MIGRATION_DONE_KEY):
        return

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

    # Mark migration as done so subsequent calls are a no-op
    user_data[_MIGRATION_DONE_KEY] = True


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
    _load_from_cm(user_data)
    _migrate_legacy_data(user_data)
    return deepcopy(user_data[USER_PREFERENCES_KEY])


def get_portfolio_prefs(user_data: Dict) -> PortfolioPrefs:
    """Get portfolio preferences

    Returns:
        Portfolio preferences with days and interval
    """
    _migrate_legacy_data(user_data)
    prefs = user_data[USER_PREFERENCES_KEY]
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
    return deepcopy(user_data[USER_PREFERENCES_KEY]["clob"])


def get_dex_prefs(user_data: Dict) -> DEXPrefs:
    """Get DEX trading preferences

    Returns:
        DEX preferences with network, connector, slippage, and last params
    """
    _migrate_legacy_data(user_data)
    return deepcopy(user_data[USER_PREFERENCES_KEY]["dex"])


def get_general_prefs(user_data: Dict) -> GeneralPrefs:
    """Get general preferences

    Returns:
        General preferences with active_server
    """
    _migrate_legacy_data(user_data)
    return deepcopy(user_data[USER_PREFERENCES_KEY]["general"])


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
    _sync_section_to_cm(user_data, "portfolio")
    logger.info(f"Set portfolio days to {days}")


def set_portfolio_interval(user_data: Dict, interval: str) -> None:
    """Set portfolio graph interval"""
    prefs = _ensure_preferences(user_data)
    prefs["portfolio"]["interval"] = interval
    _sync_section_to_cm(user_data, "portfolio")
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
    _sync_section_to_cm(user_data, "clob")
    logger.info(f"Set CLOB account to {account}")


def get_clob_last_order(user_data: Dict) -> CLOBOrderParams:
    """Get last CLOB order parameters"""
    return deepcopy(get_clob_prefs(user_data).get("last_order", {}))


def set_clob_last_order(user_data: Dict, params: CLOBOrderParams) -> None:
    """Set last CLOB order parameters (for quick trading)"""
    prefs = _ensure_preferences(user_data)
    prefs["clob"]["last_order"] = dict(params)
    _sync_section_to_cm(user_data, "clob")
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
        "price": last_order.get("price", ""),
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
    _sync_section_to_cm(user_data, "dex")
    logger.info(f"Set DEX slippage to {slippage}%")


def get_dex_last_swap(user_data: Dict) -> DEXSwapParams:
    """Get last DEX swap parameters"""
    return deepcopy(get_dex_prefs(user_data).get("last_swap", {}))


def set_dex_last_swap(user_data: Dict, params: DEXSwapParams) -> None:
    """Set last DEX swap parameters (for quick trading)"""
    prefs = _ensure_preferences(user_data)
    prefs["dex"]["last_swap"] = dict(params)
    _sync_section_to_cm(user_data, "dex")
    logger.info(f"Updated DEX last_swap params")


def get_dex_last_pool(user_data: Dict) -> DEXPoolParams:
    """Get last DEX pool parameters"""
    return deepcopy(get_dex_prefs(user_data).get("last_pool", {}))


def set_dex_last_pool(user_data: Dict, params: DEXPoolParams) -> None:
    """Set last DEX pool parameters"""
    prefs = _ensure_preferences(user_data)
    prefs["dex"]["last_pool"] = dict(params)
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
    _sync_section_to_cm(user_data, "general")
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
    prefs = user_data[USER_PREFERENCES_KEY]
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
    prefs = user_data[USER_PREFERENCES_KEY]
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
    _sync_section_to_cm(user_data, "unified_trade")
    logger.info(f"Set last trade connector: {connector_type}:{connector_name}")


# ============================================
# PUBLIC API - EXECUTORS
# ============================================


def get_executor_prefs(user_data: Dict) -> ExecutorPrefs:
    """Get executor preferences"""
    _migrate_legacy_data(user_data)
    return deepcopy(user_data[USER_PREFERENCES_KEY]["executors"])


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


# ============================================
# PUBLIC API - AGENT
# ============================================


def get_agent_prefs(user_data: Dict) -> "AgentPrefs":
    """Get agent preferences"""
    _migrate_legacy_data(user_data)
    return deepcopy(
        user_data[USER_PREFERENCES_KEY].get(
            "agent",
            {
                "default_agent": "claude-code",
                "show_tool_calls": True,
                "tool_filter_mode": "essential",
            },
        )
    )


def set_default_agent(user_data: Dict, agent_key: str) -> None:
    """Set default agent"""
    prefs = _ensure_preferences(user_data)
    if "agent" not in prefs:
        prefs["agent"] = {}
    prefs["agent"]["default_agent"] = agent_key
    _sync_section_to_cm(user_data, "agent")
    logger.info(f"Set default agent to {agent_key}")


# ============================================
# PUBLIC API - CUSTOM OPENAI-COMPATIBLE PROVIDERS
# ============================================

# Agent keys for a saved endpoint look like "custom@venice:llama-3.3-70b".
# The "@<name>" segment is what lets a bare model id keep colons and slashes
# (e.g. "custom@together:meta-llama/Llama-3.3-70B") while still parsing
# unambiguously with a single partition on ":".
CUSTOM_KEY_PREFIX = "custom"
_PROVIDER_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
MAX_CUSTOM_PROVIDERS = 10


def sanitize_provider_name(name: str) -> str:
    """Coerce a nickname into an identifier safe for use inside an agent key.

    Collapses anything outside ``[A-Za-z0-9_-]`` to a single dash, since ``:``
    and ``@`` are structural in ``custom@<name>:<model-id>``.
    """
    cleaned = _PROVIDER_NAME_RE.sub("-", name.strip()).strip("-")
    return cleaned[:32] or "custom"


def build_custom_agent_key(provider_name: str, model_id: str) -> str:
    """Compose the agent key for a model served by a saved endpoint."""
    return f"{CUSTOM_KEY_PREFIX}@{sanitize_provider_name(provider_name)}:{model_id}"


def parse_custom_agent_key(agent_key: str) -> tuple[Optional[str], str]:
    """Split a custom agent key into ``(provider_name, model_id)``.

    Returns ``(None, model_id)`` for the legacy ``custom:<model-id>`` form,
    which predates named endpoints and resolves against the first saved
    provider (or the ``CUSTOM_LLM_*`` env vars). Returns ``(None, "")`` for
    keys that aren't custom-provider keys at all.
    """
    prefix, _, model_id = agent_key.partition(":")
    head, _, provider = prefix.partition("@")
    if head != CUSTOM_KEY_PREFIX:
        return None, ""
    return (provider or None), model_id


def get_custom_providers(user_data: Dict) -> List[CustomProviderPrefs]:
    """All saved OpenAI-compatible endpoints for this user."""
    _migrate_legacy_data(user_data)
    prefs = _ensure_preferences(user_data)
    _migrate_legacy_custom_llm(user_data, prefs)
    return deepcopy(prefs.get("agent", {}).get("custom_providers", []) or [])


def find_custom_provider(
    user_data: Dict, name: Optional[str]
) -> Optional[CustomProviderPrefs]:
    """Look up one endpoint by nickname.

    With ``name=None`` (a legacy ``custom:<model>`` key) falls back to the only
    saved endpoint, so users who configured one before named endpoints existed
    keep working without reconfiguring.
    """
    providers = get_custom_providers(user_data)
    if not providers:
        return None
    if name is None:
        return providers[0] if len(providers) == 1 else None
    target = sanitize_provider_name(name)
    for provider in providers:
        if sanitize_provider_name(provider.get("name", "")) == target:
            return provider
    return None


def save_custom_provider(
    user_data: Dict, name: str, base_url: str, api_key: str = ""
) -> CustomProviderPrefs:
    """Insert or update an endpoint, keyed by its sanitized nickname.

    Returns the stored record (with the sanitized name), so callers can build
    agent keys from a value that round-trips.
    """
    prefs = _ensure_preferences(user_data)
    agent = prefs.setdefault("agent", {})
    providers: List[CustomProviderPrefs] = agent.setdefault("custom_providers", [])

    safe_name = sanitize_provider_name(name)
    record: CustomProviderPrefs = {
        "name": safe_name,
        "base_url": base_url,
        "api_key": api_key,
    }
    for i, existing in enumerate(providers):
        if sanitize_provider_name(existing.get("name", "")) == safe_name:
            providers[i] = record
            break
    else:
        if len(providers) >= MAX_CUSTOM_PROVIDERS:
            raise ValueError(
                f"You already have {MAX_CUSTOM_PROVIDERS} saved endpoints. "
                "Remove one before adding another."
            )
        providers.append(record)

    _sync_section_to_cm(user_data, "agent")
    logger.info("Saved custom provider '%s' (%s)", safe_name, base_url)
    return deepcopy(record)


def remove_custom_provider(user_data: Dict, name: str) -> bool:
    """Forget an endpoint. Returns True if one was removed."""
    prefs = _ensure_preferences(user_data)
    agent = prefs.setdefault("agent", {})
    providers: List[CustomProviderPrefs] = agent.setdefault("custom_providers", [])

    target = sanitize_provider_name(name)
    remaining = [
        p for p in providers if sanitize_provider_name(p.get("name", "")) != target
    ]
    if len(remaining) == len(providers):
        return False

    agent["custom_providers"] = remaining
    _sync_section_to_cm(user_data, "agent")
    logger.info("Removed custom provider '%s'", target)
    return True


def unique_provider_name(user_data: Dict, suggested: str) -> str:
    """Return ``suggested`` (sanitized), suffixed with -2, -3, ... if taken."""
    existing = {
        sanitize_provider_name(p.get("name", "")) for p in get_custom_providers(user_data)
    }
    base = sanitize_provider_name(suggested)
    if base not in existing:
        return base
    for n in range(2, MAX_CUSTOM_PROVIDERS + 2):
        candidate = f"{base}-{n}"
        if candidate not in existing:
            return candidate
    return base


def resolve_custom_endpoint(
    agent_key: str,
    user_data: Optional[Dict] = None,
    user_id: Optional[int] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve ``(base_url, api_key)`` for a ``custom@<endpoint>:<model>`` key.

    Returns ``(None, None)`` for any key that isn't a custom-endpoint key, so
    callers can pass this through unconditionally.

    Every surface that builds a model — chat sessions, consult, delegate, the
    trading-agent engine — needs this. Without it a custom key resolves to
    nothing and dies with "No base URL configured", which is exactly what
    happens when only the chat path knows how to look endpoints up.

    Pass ``user_data`` when you have it (Telegram handlers) or ``user_id`` when
    you don't (MCP subprocess, web, background agent runs).
    """
    provider_name, model_id = parse_custom_agent_key(agent_key)
    if not model_id:
        return None, None

    if user_data is None and user_id is not None:
        try:
            user_data = load_user_data_for(user_id)
        except Exception:
            logger.debug("Could not load preferences for user %s", user_id)

    provider = find_custom_provider(user_data, provider_name) if user_data else None
    if provider is None and provider_name:
        logger.warning(
            "Agent key '%s' names endpoint '%s', which is not saved for this user",
            agent_key,
            provider_name,
        )

    provider = provider or {}
    import os

    base_url = provider.get("base_url") or os.environ.get("CUSTOM_LLM_BASE_URL")
    api_key = provider.get("api_key") or os.environ.get("CUSTOM_LLM_API_KEY")
    return base_url or None, api_key or None


def get_active_agent_key(user_id: int) -> Optional[str]:
    """The model this user last selected, readable outside a PTB context.

    Mirrors ``user_data["agent_llm"]`` into config.yml (see
    :func:`set_active_agent_key`) so the MCP subprocess and the web dashboard
    can answer "what model is this user actually on?" — which is what lets a
    newly created agent inherit it instead of guessing.
    """
    try:
        prefs = load_user_data_for(user_id)
    except Exception:
        return None
    key = prefs.get(USER_PREFERENCES_KEY, {}).get("agent", {}).get("active_agent_key")
    return key or None


def set_active_agent_key(user_data: Dict, agent_key: str) -> None:
    """Record the user's current model selection in the shared preference store."""
    prefs = _ensure_preferences(user_data)
    agent = prefs.setdefault("agent", {})
    if agent.get("active_agent_key") == agent_key:
        return
    agent["active_agent_key"] = agent_key
    _sync_section_to_cm(user_data, "agent")


def _migrate_legacy_custom_llm(user_data: Dict, prefs: Dict) -> None:
    """Fold the old single-slot ``user_data["custom_llm"]`` into the list.

    The first iteration of custom-endpoint support stored one endpoint in raw
    user_data, outside the preference system (and so invisible to the web
    dashboard). Move it across once, then drop the legacy key.
    """
    legacy = user_data.get("custom_llm")
    if not isinstance(legacy, dict) or not legacy.get("base_url"):
        user_data.pop("custom_llm", None)
        return

    agent = prefs.setdefault("agent", {})
    providers: List[CustomProviderPrefs] = agent.setdefault("custom_providers", [])
    base_url = legacy["base_url"]
    if not any(p.get("base_url") == base_url for p in providers):
        name = sanitize_provider_name(suggest_provider_name(base_url))
        taken = {sanitize_provider_name(p.get("name", "")) for p in providers}
        while name in taken:
            name = f"{name}-2"
        providers.append(
            {
                "name": name,
                "base_url": base_url,
                "api_key": legacy.get("api_key", ""),
            }
        )
        _sync_section_to_cm(user_data, "agent")
        logger.info("Migrated legacy custom_llm endpoint %s → '%s'", base_url, name)

    user_data.pop("custom_llm", None)


def suggest_provider_name(base_url: str) -> str:
    """Derive a readable nickname from an endpoint URL.

    ``https://api.venice.ai/api/v1`` → ``Venice``; ``http://localhost:8000/v1``
    → ``localhost``. Falls back to ``custom`` for anything unparseable.
    """
    try:
        host = urlparse(base_url).hostname or ""
    except ValueError:
        return "custom"
    if not host:
        return "custom"

    # IP literals have no meaningful label to extract
    if all(part.isdigit() for part in host.split(".")) or ":" in host:
        return host

    labels = [label for label in host.split(".") if label not in ("api", "www")]
    if not labels:
        return host
    name = labels[0]
    return name.capitalize() if name.isalpha() and name != "localhost" else name


# ============================================
# PUBLIC API - CROSS-PLATFORM PREFERENCE ACCESS
# ============================================


def load_user_data_for(user_id: int) -> Dict:
    """Build a preferences-bearing ``user_data`` dict outside a PTB context.

    The web dashboard has no ``context.user_data``, so it can't reach anything
    stored through this module. This hydrates an equivalent dict from
    config.yml and tags it with ``_user_id``, which means writes through the
    normal setters sync straight back — giving Telegram and web a single
    shared view of preferences.
    """
    user_data: Dict = {"_user_id": user_id}
    _load_from_cm(user_data)
    _ensure_preferences(user_data)
    return user_data


# ============================================
# PUBLIC API - VOICE
# ============================================


def get_voice_prefs(user_data: Dict) -> "VoicePrefs":
    """Get voice preferences"""
    _migrate_legacy_data(user_data)
    return deepcopy(
        user_data[USER_PREFERENCES_KEY].get(
            "voice",
            {
                "whisper_model": "small",
                "language": None,
                "auto_send": True,
            },
        )
    )


def set_voice_prefs(user_data: Dict, **kwargs) -> None:
    """Update voice preferences. Pass only the keys you want to change."""
    prefs = _ensure_preferences(user_data)
    if "voice" not in prefs:
        prefs["voice"] = {"whisper_model": "small", "language": None, "auto_send": True}
    for key in ("whisper_model", "language", "auto_send"):
        if key in kwargs:
            prefs["voice"][key] = kwargs[key]
    _sync_section_to_cm(user_data, "voice")
    logger.info("Updated voice preferences: %s", kwargs)


# ============================================
# PUBLIC API - NOTES (key-value memory for the AI agent)
# ============================================


def get_notes(user_data: Dict) -> Dict[str, str]:
    """Get all notes (key-value pairs stored by the AI agent)."""
    _migrate_legacy_data(user_data)
    return deepcopy(user_data[USER_PREFERENCES_KEY].get("notes", {}))


def get_note(user_data: Dict, key: str) -> Optional[str]:
    """Get a single note by key."""
    notes = get_notes(user_data)
    return notes.get(key)


def set_note(user_data: Dict, key: str, value: str) -> None:
    """Set a note (key-value pair)."""
    prefs = _ensure_preferences(user_data)
    if "notes" not in prefs:
        prefs["notes"] = {}
    prefs["notes"][key] = value
    logger.info(f"Set note '{key}'")


def delete_note(user_data: Dict, key: str) -> bool:
    """Delete a note by key. Returns True if it existed."""
    prefs = _ensure_preferences(user_data)
    notes = prefs.get("notes", {})
    if key in notes:
        del notes[key]
        logger.info(f"Deleted note '{key}'")
        return True
    return False


# ============================================
# UTILITY FUNCTIONS
# ============================================


def clear_preferences(user_data: Dict) -> None:
    """Clear all user preferences (reset to defaults)"""
    if USER_PREFERENCES_KEY in user_data:
        del user_data[USER_PREFERENCES_KEY]
    # Reset bookkeeping flags so migration/hydration rebuild consistently
    user_data.pop(_MIGRATION_DONE_KEY, None)
    user_data.pop("_prefs_hydrated", None)
    logger.info("Cleared all user preferences")


def export_preferences(user_data: Dict) -> Dict[str, Any]:
    """Export all preferences as a dictionary (for debugging/backup)"""
    return get_preferences(user_data)
