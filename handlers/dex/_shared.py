"""
Shared utilities for DEX trading handlers

Contains:
- Server client helper
- Explorer URL generation
- Common formatters
- Conversation-level caching
"""

import asyncio
import functools
import logging
import time
from typing import Optional, Dict, Any, Callable, TypeVar, List

logger = logging.getLogger(__name__)

T = TypeVar('T')

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
    cache = user_data.get("_cache", {})
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
    if "_cache" not in user_data:
        user_data["_cache"] = {}

    user_data["_cache"][key] = (value, time.time())


def clear_cache(user_data: dict, key: Optional[str] = None) -> None:
    """Clear cached values.

    Args:
        user_data: context.user_data dict
        key: Specific key to clear, or None to clear all
    """
    if key is None:
        user_data.pop("_cache", None)
    elif "_cache" in user_data:
        user_data["_cache"].pop(key, None)


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

    Example:
        data = await cached_call(
            context.user_data,
            "gateway_balances",
            _fetch_gateway_data,
            ttl=60,
            client
        )
    """
    # Check cache first
    cached = get_cached(user_data, key, ttl)
    if cached is not None:
        logger.debug(f"Cache hit for '{key}'")
        return cached

    # Cache miss - fetch fresh data
    logger.debug(f"Cache miss for '{key}', fetching...")
    result = await fetch_func(*args, **kwargs)

    # Store in cache
    set_cached(user_data, key, result)

    return result


# ============================================
# CACHE INVALIDATION GROUPS
# ============================================

# Define which cache keys should be invalidated together
CACHE_GROUPS = {
    "balances": ["gateway_balances", "portfolio_data", "wallet_balances", "token_balances", "gateway_data"],
    "positions": ["clmm_positions", "liquidity_positions", "pool_positions", "gateway_lp_positions"],
    "swaps": ["swap_history", "recent_swaps"],
    "tokens": ["token_cache"],  # Token list from gateway
    "all": None,  # Special: clears entire cache
}


def invalidate_cache(user_data: dict, *groups: str) -> None:
    """Invalidate cache keys by group name(s).

    Args:
        user_data: context.user_data dict
        *groups: One or more group names or individual cache keys

    Example:
        invalidate_cache(context.user_data, "balances")
        invalidate_cache(context.user_data, "balances", "swaps")
    """
    for group in groups:
        if group == "all":
            clear_cache(user_data)
            # Also clear token_cache stored directly in user_data
            user_data.pop("token_cache", None)
            logger.debug("Cache fully cleared")
            return

        keys = CACHE_GROUPS.get(group, [group])  # Fallback to group as key
        for key in keys:
            clear_cache(user_data, key)
            # Also clear if stored directly in user_data (e.g., token_cache)
            if key in user_data:
                user_data.pop(key, None)
        logger.debug(f"Invalidated cache group '{group}': {keys}")


def invalidates(*groups: str):
    """Decorator that invalidates cache groups after handler execution.

    Args:
        *groups: Cache groups to invalidate after the handler runs

    Example:
        @invalidates("balances", "swaps")
        async def execute_swap(update, context):
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
# BACKGROUND REFRESH MANAGER
# ============================================

# Inactivity timeout in seconds (stop refreshing after this)
INACTIVITY_TIMEOUT = 300  # 5 minutes
REFRESH_INTERVAL = 30  # seconds between refreshes


class BackgroundRefreshManager:
    """Manages background data refresh for active users.

    Starts refreshing when user becomes active, stops after inactivity.
    Stores refresh tasks per user_id to avoid duplicates.
    """

    def __init__(self):
        self._tasks: Dict[int, asyncio.Task] = {}
        self._last_activity: Dict[int, float] = {}
        self._refresh_funcs: Dict[str, Callable] = {}

    def register_refresh(self, key: str, func: Callable) -> None:
        """Register a function to be called during background refresh.

        Args:
            key: Cache key this function populates
            func: Async function(user_data, client) that fetches and caches data
        """
        self._refresh_funcs[key] = func
        logger.debug(f"Registered background refresh for '{key}'")

    def touch(self, user_id: int, user_data: dict) -> None:
        """Mark user as active, starting background refresh if needed.

        Call this at the start of any handler to keep refresh alive.

        Args:
            user_id: Telegram user ID
            user_data: context.user_data dict
        """
        self._last_activity[user_id] = time.time()

        if user_id not in self._tasks or self._tasks[user_id].done():
            self._tasks[user_id] = asyncio.create_task(
                self._refresh_loop(user_id, user_data)
            )
            logger.debug(f"Started background refresh for user {user_id}")

    async def _refresh_loop(self, user_id: int, user_data: dict) -> None:
        """Background loop that refreshes data until inactivity timeout."""
        try:
            client = await get_gateway_client()
        except Exception as e:
            logger.warning(f"Background refresh: couldn't get client: {e}")
            return

        while True:
            await asyncio.sleep(REFRESH_INTERVAL)

            # Check for inactivity
            last = self._last_activity.get(user_id, 0)
            if time.time() - last > INACTIVITY_TIMEOUT:
                logger.debug(f"Stopping background refresh for user {user_id} (inactive)")
                break

            # Refresh all registered functions
            for key, func in self._refresh_funcs.items():
                try:
                    result = await func(client)
                    set_cached(user_data, key, result)
                    logger.debug(f"Background refreshed '{key}' for user {user_id}")
                except Exception as e:
                    logger.warning(f"Background refresh failed for '{key}': {e}")

        # Cleanup
        self._tasks.pop(user_id, None)
        self._last_activity.pop(user_id, None)

    def stop(self, user_id: int) -> None:
        """Manually stop background refresh for a user."""
        if user_id in self._tasks:
            self._tasks[user_id].cancel()
            self._tasks.pop(user_id, None)
            self._last_activity.pop(user_id, None)
            logger.debug(f"Manually stopped background refresh for user {user_id}")


# Global instance
background_refresh = BackgroundRefreshManager()


def with_background_refresh(func: Callable) -> Callable:
    """Decorator that touches background refresh on handler entry.

    Example:
        @with_background_refresh
        async def my_handler(update, context):
            ...
    """
    @functools.wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        if update.effective_user:
            background_refresh.touch(
                update.effective_user.id,
                context.user_data
            )
        return await func(update, context, *args, **kwargs)
    return wrapper


# ============================================
# SERVER CLIENT HELPERS
# ============================================

async def get_gateway_client():
    """Get the gateway client from the default server

    Returns:
        Client instance with gateway_swap and gateway_clmm attributes

    Raises:
        ValueError: If no enabled servers or gateway not available
    """
    from servers import server_manager

    servers = server_manager.list_servers()
    enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

    if not enabled_servers:
        raise ValueError("No enabled API servers available")

    # Use default server if set, otherwise fall back to first enabled
    default_server = server_manager.get_default_server()
    if default_server and default_server in enabled_servers:
        server_name = default_server
    else:
        server_name = enabled_servers[0]

    logger.info(f"DEX using server: {server_name} (default: {default_server}, enabled: {enabled_servers})")
    client = await server_manager.get_client(server_name)

    return client


# ============================================
# EXPLORER URL GENERATION
# ============================================

SOLANA_EXPLORERS = {
    "orb": "https://orb.helius.dev/tx/{tx_hash}?cluster={cluster}&tab=summary",
    "solscan": "https://solscan.io/tx/{tx_hash}",
    "solana_explorer": "https://explorer.solana.com/tx/{tx_hash}",
}

ETHEREUM_EXPLORERS = {
    "etherscan": "https://etherscan.io/tx/{tx_hash}",
    "arbiscan": "https://arbiscan.io/tx/{tx_hash}",
    "basescan": "https://basescan.org/tx/{tx_hash}",
}


def get_explorer_url(tx_hash: str, network: str) -> Optional[str]:
    """Generate explorer URL for a transaction

    Args:
        tx_hash: Transaction hash/signature
        network: Network name (e.g., 'solana-mainnet-beta', 'ethereum-mainnet')

    Returns:
        Explorer URL or None if network not supported
    """
    if not tx_hash:
        return None

    if network.startswith("solana"):
        # Use Orb explorer for Solana (Helius)
        cluster = "mainnet-beta" if "mainnet" in network else "devnet"
        return SOLANA_EXPLORERS["orb"].format(tx_hash=tx_hash, cluster=cluster)
    elif "ethereum" in network or "mainnet" in network:
        if "arbitrum" in network:
            return ETHEREUM_EXPLORERS["arbiscan"].format(tx_hash=tx_hash)
        elif "base" in network:
            return ETHEREUM_EXPLORERS["basescan"].format(tx_hash=tx_hash)
        else:
            return ETHEREUM_EXPLORERS["etherscan"].format(tx_hash=tx_hash)

    return None


def get_explorer_name(network: str) -> str:
    """Get the explorer name for display

    Args:
        network: Network name

    Returns:
        Explorer name (e.g., 'Orb', 'Etherscan')
    """
    if network.startswith("solana"):
        return "Orb"
    elif "arbitrum" in network:
        return "Arbiscan"
    elif "base" in network:
        return "Basescan"
    elif "ethereum" in network:
        return "Etherscan"
    return "Explorer"


# ============================================
# SWAP FORMATTERS
# ============================================

def format_swap_summary(swap: Dict[str, Any], include_explorer: bool = True) -> str:
    """Format a swap record for display

    Args:
        swap: Swap data dictionary
        include_explorer: Whether to include explorer link

    Returns:
        Formatted swap summary string (not escaped)
    """
    pair = swap.get('trading_pair', 'N/A')
    side = swap.get('side', 'N/A')
    status = swap.get('status', 'N/A')
    network = swap.get('network', '')
    tx_hash = swap.get('transaction_hash', '')

    # Format amounts
    input_amount = swap.get('input_amount')
    output_amount = swap.get('output_amount')
    base_token = swap.get('base_token', '')
    quote_token = swap.get('quote_token', '')

    # Build amount string
    if input_amount is not None and output_amount is not None:
        if side == 'BUY':
            # Buying base with quote
            amount_str = f"{_format_amount(output_amount)} {base_token} for {_format_amount(input_amount)} {quote_token}"
        else:
            # Selling base for quote
            amount_str = f"{_format_amount(input_amount)} {base_token} for {_format_amount(output_amount)} {quote_token}"
    elif input_amount is not None:
        amount_str = f"{_format_amount(input_amount)}"
    else:
        amount_str = "N/A"

    # Format price
    price = swap.get('price')
    price_str = f"@ {_format_price(price)}" if price else ""

    # Build the line
    parts = [f"{side} {pair}", amount_str]
    if price_str:
        parts.append(price_str)
    parts.append(f"[{status}]")

    return " ".join(parts)


def format_swap_detail(swap: Dict[str, Any]) -> str:
    """Format detailed swap information

    Args:
        swap: Swap data dictionary

    Returns:
        Formatted multi-line swap details (not escaped)
    """
    lines = []

    # Header with status emoji
    status = swap.get('status', 'UNKNOWN')
    status_emoji = get_status_emoji(status)
    lines.append(f"{status_emoji} Swap Details")
    lines.append("")

    # Trading info
    pair = swap.get('trading_pair', 'N/A')
    side = swap.get('side', 'N/A')
    lines.append(f"Pair: {pair}")
    lines.append(f"Side: {side}")

    # Amounts
    input_amount = swap.get('input_amount')
    output_amount = swap.get('output_amount')
    base_token = swap.get('base_token', '')
    quote_token = swap.get('quote_token', '')

    if input_amount is not None:
        lines.append(f"Input: {_format_amount(input_amount)} {quote_token if side == 'BUY' else base_token}")
    if output_amount is not None:
        lines.append(f"Output: {_format_amount(output_amount)} {base_token if side == 'BUY' else quote_token}")

    # Price
    price = swap.get('price')
    if price:
        lines.append(f"Price: {_format_price(price)}")

    # Slippage
    slippage = swap.get('slippage_pct')
    if slippage is not None:
        lines.append(f"Slippage: {slippage}%")

    # Network info
    lines.append("")
    connector = swap.get('connector', 'N/A')
    network = swap.get('network', 'N/A')
    lines.append(f"Connector: {connector}")
    lines.append(f"Network: {network}")

    # Transaction
    tx_hash = swap.get('transaction_hash', '')
    if tx_hash:
        lines.append(f"Tx: {tx_hash[:16]}...")

    # Timestamp
    timestamp = swap.get('timestamp', '')
    if timestamp:
        # Format timestamp for display
        if 'T' in timestamp:
            date_part = timestamp.split('T')[0]
            time_part = timestamp.split('T')[1].split('.')[0] if '.' in timestamp.split('T')[1] else timestamp.split('T')[1].split('+')[0]
            lines.append(f"Time: {date_part} {time_part}")

    # Status
    lines.append(f"Status: {status}")

    return "\n".join(lines)


def get_status_emoji(status: str) -> str:
    """Get emoji for swap status

    Args:
        status: Status string (CONFIRMED, PENDING, FAILED, etc.)

    Returns:
        Emoji character
    """
    status_emojis = {
        "CONFIRMED": "✅",
        "PENDING": "⏳",
        "FAILED": "❌",
        "REJECTED": "🚫",
        "UNKNOWN": "❓",
    }
    return status_emojis.get(status.upper(), "📊")


def _format_amount(amount: float) -> str:
    """Format amount with appropriate precision"""
    if amount is None:
        return "N/A"

    if amount == 0:
        return "0"

    # Use appropriate decimal places based on size
    if abs(amount) >= 1000:
        return f"{amount:,.2f}"
    elif abs(amount) >= 1:
        return f"{amount:.4f}"
    elif abs(amount) >= 0.0001:
        return f"{amount:.6f}"
    else:
        return f"{amount:.8f}"


def _format_price(price: float) -> str:
    """Format price with appropriate precision"""
    if price is None:
        return "N/A"

    if price == 0:
        return "0"

    if abs(price) >= 1:
        return f"{price:.4f}"
    elif abs(price) >= 0.0001:
        return f"{price:.6f}"
    else:
        return f"{price:.10f}"


# ============================================
# STATE HELPERS
# ============================================

def clear_dex_state(context) -> None:
    """Clear all DEX-related state from user context

    Args:
        context: Telegram context object
    """
    context.user_data.pop("dex_state", None)
    context.user_data.pop("dex_previous_state", None)
    context.user_data.pop("quote_swap_params", None)
    context.user_data.pop("execute_swap_params", None)
