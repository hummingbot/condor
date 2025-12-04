"""
Shared utilities for CEX trading handlers

Contains:
- Caching utilities for CEX balances and trading rules
- Server client helpers
- Cache invalidation mechanisms
"""

import functools
import logging
import time
from typing import Optional, Dict, Any, Callable, List

logger = logging.getLogger(__name__)

# Default cache TTL in seconds
DEFAULT_CACHE_TTL = 60


# ============================================
# CONVERSATION-LEVEL CACHE
# ============================================

def get_cached(user_data: dict, key: str, ttl: int = DEFAULT_CACHE_TTL) -> Optional[Any]:
    """Get a cached value if still valid.

    Args:
        user_data: context.user_data dict
        key: Cache key
        ttl: Time-to-live in seconds

    Returns:
        Cached value or None if expired/missing
    """
    cache = user_data.get("_cex_cache", {})
    entry = cache.get(key)

    if entry is None:
        return None

    value, timestamp = entry
    if time.time() - timestamp > ttl:
        # Expired
        return None

    return value


def set_cached(user_data: dict, key: str, value: Any) -> None:
    """Store a value in the conversation cache.

    Args:
        user_data: context.user_data dict
        key: Cache key
        value: Value to cache
    """
    if "_cex_cache" not in user_data:
        user_data["_cex_cache"] = {}

    user_data["_cex_cache"][key] = (value, time.time())


def clear_cache(user_data: dict, key: Optional[str] = None) -> None:
    """Clear cached values.

    Args:
        user_data: context.user_data dict
        key: Specific key/prefix to clear, or None to clear all.
              If key ends with '*', clears all keys starting with that prefix.
              Otherwise clears exact key match.
    """
    if key is None:
        user_data.pop("_cex_cache", None)
    elif "_cex_cache" in user_data:
        if key.endswith("*"):
            # Prefix-based clearing
            prefix = key[:-1]
            keys_to_clear = [k for k in user_data["_cex_cache"] if k.startswith(prefix)]
            for k in keys_to_clear:
                user_data["_cex_cache"].pop(k, None)
        else:
            user_data["_cex_cache"].pop(key, None)


async def cached_call(
    user_data: dict,
    key: str,
    fetch_func: Callable,
    ttl: int = DEFAULT_CACHE_TTL,
    *args,
    **kwargs
) -> Any:
    """Execute an async function with caching.

    Args:
        user_data: context.user_data dict
        key: Cache key
        fetch_func: Async function to call if cache miss
        ttl: Time-to-live in seconds
        *args, **kwargs: Arguments to pass to fetch_func

    Returns:
        Cached or fresh result
    """
    # Check cache first
    cached = get_cached(user_data, key, ttl)
    if cached is not None:
        logger.debug(f"CEX cache hit for '{key}'")
        return cached

    # Cache miss - fetch fresh data
    logger.debug(f"CEX cache miss for '{key}', fetching...")
    result = await fetch_func(*args, **kwargs)

    # Store in cache
    set_cached(user_data, key, result)

    return result


# ============================================
# CACHE INVALIDATION GROUPS
# ============================================

# Define which cache keys should be invalidated together
# Use '*' suffix for prefix-based clearing (e.g., "cex_balances_*" clears all account balances)
CACHE_GROUPS = {
    "balances": ["cex_balances_*", "connector_balances_*"],
    "orders": ["active_orders_*", "order_history_*"],
    "positions": ["positions_*"],
    "trading_rules": ["trading_rules_*"],
    "all": None,  # Special: clears entire cache
}


def invalidate_cache(user_data: dict, *groups: str) -> None:
    """Invalidate cache keys by group name(s).

    Args:
        user_data: context.user_data dict
        *groups: One or more group names or individual cache keys
    """
    for group in groups:
        if group == "all":
            clear_cache(user_data)
            logger.debug("CEX cache fully cleared")
            return

        keys = CACHE_GROUPS.get(group, [group])  # Fallback to group as key
        for key in keys:
            clear_cache(user_data, key)
        logger.debug(f"CLOB invalidated cache group '{group}': {keys}")


def invalidates(*groups: str):
    """Decorator that invalidates cache groups after handler execution.

    Args:
        *groups: Cache groups to invalidate after the handler runs

    Example:
        @invalidates("balances", "orders")
        async def execute_order(update, context):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)

            # Find context in args (usually second arg for handlers)
            context = None
            for arg in args:
                if hasattr(arg, 'user_data'):
                    context = arg
                    break

            if context:
                invalidate_cache(context.user_data, *groups)

            return result
        return wrapper
    return decorator


# ============================================
# CEX CONNECTOR HELPERS
# ============================================

def is_cex_connector(connector_name: str) -> bool:
    """Check if a connector is a CEX (not DEX/on-chain).

    Args:
        connector_name: Name of the connector

    Returns:
        True if it's a CEX connector
    """
    connector_lower = connector_name.lower()
    # Filter out on-chain/DEX connectors
    dex_prefixes = ["solana", "ethereum", "polygon", "arbitrum", "base", "optimism", "avalanche"]
    return not any(connector_lower.startswith(prefix) for prefix in dex_prefixes)


def get_cex_connectors(connectors: Dict[str, Any]) -> List[str]:
    """Filter connectors to only include CEX ones.

    Args:
        connectors: Dict of connector_name -> connector_config

    Returns:
        List of CEX connector names
    """
    return [name for name in connectors.keys() if is_cex_connector(name)]


# ============================================
# BALANCE FETCHING
# ============================================

async def fetch_cex_balances(client, account_name: str) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch balances for all CEX connectors.

    Args:
        client: API client
        account_name: Account name to fetch balances for

    Returns:
        Dict of connector_name -> list of balances
    """
    try:
        # Get connectors with credentials configured for this account
        configured_connectors = await client.accounts.list_account_credentials(account_name)

        # Filter to CEX only (list_account_credentials returns List[str])
        cex_connectors = [c for c in configured_connectors if is_cex_connector(c)]

        if not cex_connectors:
            logger.warning("No CEX connectors found")
            return {}

        # Fetch balances using portfolio.get_state
        try:
            portfolio_state = await client.portfolio.get_state(
                account_names=[account_name],
                connector_names=cex_connectors
            )

            # portfolio.get_state returns {account_name: {connector_name: [balances]}}
            account_data = portfolio_state.get(account_name, {})

            # Filter to only connectors with non-empty balances
            balances = {}
            for connector_name, balance_list in account_data.items():
                if balance_list:
                    balances[connector_name] = balance_list

            return balances

        except Exception as e:
            logger.warning(f"Failed to fetch portfolio state: {e}")
            return {}

    except Exception as e:
        logger.error(f"Error fetching CEX balances: {e}", exc_info=True)
        return {}


async def get_cex_balances(
    user_data: dict,
    client,
    account_name: str,
    ttl: int = DEFAULT_CACHE_TTL
) -> Dict[str, List[Dict[str, Any]]]:
    """Get CEX balances with caching.

    Args:
        user_data: context.user_data dict
        client: API client
        account_name: Account name
        ttl: Cache TTL in seconds

    Returns:
        Dict of connector_name -> list of balances
    """
    cache_key = f"cex_balances_{account_name}"
    return await cached_call(
        user_data,
        cache_key,
        fetch_cex_balances,
        ttl,
        client,
        account_name
    )


# ============================================
# POSITIONS FETCHING
# ============================================

async def fetch_positions(client, connector_name: str = None) -> List[Dict[str, Any]]:
    """Fetch positions, optionally filtered by connector.

    Args:
        client: API client
        connector_name: Optional connector name to filter by

    Returns:
        List of position dictionaries
    """
    try:
        result = await client.trading.get_positions(limit=100)
        positions = result.get("data", [])

        # Filter by connector if specified
        if connector_name and positions:
            positions = [
                p for p in positions
                if p.get("connector_name") == connector_name
            ]

        return positions

    except Exception as e:
        logger.error(f"Error fetching positions: {e}", exc_info=True)
        return []


async def get_positions(
    user_data: dict,
    client,
    connector_name: str = None,
    ttl: int = DEFAULT_CACHE_TTL
) -> List[Dict[str, Any]]:
    """Get positions with caching.

    Args:
        user_data: context.user_data dict
        client: API client
        connector_name: Optional connector name to filter by
        ttl: Cache TTL in seconds

    Returns:
        List of position dictionaries
    """
    cache_key = f"positions_{connector_name or 'all'}"
    return await cached_call(
        user_data,
        cache_key,
        fetch_positions,
        ttl,
        client,
        connector_name
    )


# ============================================
# TRADING RULES FETCHING
# ============================================

async def fetch_trading_rules(client, connector_name: str) -> Dict[str, Dict[str, Any]]:
    """Fetch trading rules for a connector.

    Args:
        client: API client
        connector_name: Name of the connector

    Returns:
        Dict of trading_pair -> rules
    """
    try:
        result = await client.connectors.get_trading_rules(connector_name=connector_name)
        return result if result else {}
    except Exception as e:
        logger.error(f"Error fetching trading rules for {connector_name}: {e}", exc_info=True)
        return {}


async def get_trading_rules(
    user_data: dict,
    client,
    connector_name: str,
    ttl: int = 300  # Trading rules change less frequently, 5 min cache
) -> Dict[str, Dict[str, Any]]:
    """Get trading rules for a connector with caching.

    Args:
        user_data: context.user_data dict
        client: API client
        connector_name: Name of the connector
        ttl: Cache TTL in seconds

    Returns:
        Dict of trading_pair -> rules
    """
    cache_key = f"trading_rules_{connector_name}"
    return await cached_call(
        user_data,
        cache_key,
        fetch_trading_rules,
        ttl,
        client,
        connector_name
    )


def validate_order_against_rules(
    trading_rules: Dict[str, Dict[str, Any]],
    trading_pair: str,
    amount: float,
    is_quote_amount: bool = False
) -> tuple[bool, Optional[str]]:
    """Validate an order amount against trading rules.

    Args:
        trading_rules: Dict of trading_pair -> rules
        trading_pair: Trading pair (e.g., "BTC-USDT")
        amount: Order amount
        is_quote_amount: True if amount is in quote currency (e.g., $100)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if trading_pair not in trading_rules:
        return True, None  # Can't validate if no rules

    rules = trading_rules[trading_pair]

    if is_quote_amount:
        # Validate against min_notional_size
        min_notional = rules.get("min_notional_size", 0)
        if amount < min_notional:
            return False, f"Order value ${amount:.2f} is below minimum notional size ${min_notional:.2f} for {trading_pair}"
    else:
        # Validate against min_order_size
        min_order = rules.get("min_order_size", 0)
        if amount < min_order:
            return False, f"Order size {amount} is below minimum order size {min_order} for {trading_pair}"

        # Also check min_notional if we can estimate (need price)
        # This would be done at order execution time when we have the price

    return True, None


def format_trading_rules_info(
    trading_rules: Dict[str, Dict[str, Any]],
    trading_pair: str,
    current_price: float = None
) -> str:
    """Format trading rules for display.

    Args:
        trading_rules: Dict of trading_pair -> rules
        trading_pair: Trading pair to format
        current_price: Optional current market price to include

    Returns:
        Formatted string with rule info
    """
    def fmt_num(n):
        """Format number, removing unnecessary trailing zeros"""
        if n == int(n):
            return str(int(n))
        # Format with enough precision, then strip trailing zeros
        s = f"{n:.8f}".rstrip('0').rstrip('.')
        return s

    def fmt_price(p):
        """Format price with appropriate precision"""
        if p >= 1000:
            return f"${p:,.2f}"
        elif p >= 1:
            return f"${p:.4f}".rstrip('0').rstrip('.')
        else:
            return f"${p:.6f}".rstrip('0').rstrip('.')

    items = []

    # Add current price as first item if provided
    if current_price:
        items.append(("Price", fmt_price(current_price)))

    # Add trading rules if available
    if trading_pair in trading_rules:
        rules = trading_rules[trading_pair]

        min_order = rules.get("min_order_size", 0)
        min_notional = rules.get("min_notional_size", 0)
        min_price_inc = rules.get("min_price_increment", 0)
        min_base_inc = rules.get("min_base_amount_increment", 0)

        if min_order > 0:
            items.append(("Min size", fmt_num(min_order)))
        if min_notional > 0:
            items.append(("Min notional", f"${fmt_num(min_notional)}"))
        if min_price_inc > 0:
            items.append(("Price tick", fmt_num(min_price_inc)))
        if min_base_inc > 0:
            items.append(("Size tick", fmt_num(min_base_inc)))

    if not items:
        return ""

    # Calculate column widths dynamically
    max_label = max(len(label) for label, _ in items)

    # Format as aligned table
    lines = []
    for label, value in items:
        lines.append(f"{label:<{max_label}}: {value}")

    return "\n".join(lines)


# ============================================
# AVAILABLE CONNECTORS FETCHING
# ============================================

async def fetch_available_cex_connectors(client, account_name: str = "master_account") -> List[str]:
    """Fetch list of available CEX connectors with credentials configured.

    Args:
        client: API client
        account_name: Account name to check credentials for

    Returns:
        List of available CEX connector names
    """
    try:
        # Get connectors with credentials configured for this account
        configured_connectors = await client.accounts.list_account_credentials(account_name)
        # Filter to CEX only
        return [c for c in configured_connectors if is_cex_connector(c)]
    except Exception as e:
        logger.error(f"Error fetching connectors: {e}", exc_info=True)
        return []


async def get_available_cex_connectors(
    user_data: dict,
    client,
    account_name: str = "master_account",
    ttl: int = 300  # 5 min cache
) -> List[str]:
    """Get available CEX connectors with caching.

    Args:
        user_data: context.user_data dict
        client: API client
        account_name: Account name to check credentials for
        ttl: Cache TTL in seconds

    Returns:
        List of available CEX connector names
    """
    cache_key = f"available_cex_connectors_{account_name}"
    return await cached_call(
        user_data,
        cache_key,
        fetch_available_cex_connectors,
        ttl,
        client,
        account_name
    )


# ============================================
# STATE HELPERS
# ============================================

def clear_cex_state(context) -> None:
    """Clear all CEX-related state from user context

    Args:
        context: Telegram context object
    """
    context.user_data.pop("cex_state", None)
    context.user_data.pop("cex_previous_state", None)
    context.user_data.pop("place_order_params", None)
    context.user_data.pop("current_positions", None)
    context.user_data.pop("current_orders", None)
