"""
Shared utilities for DEX trading handlers

Contains:
- Server client helper
- Explorer URL generation
- Common formatters
- Conversation-level caching (delegates to condor.cache)
"""

import asyncio
import functools
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from condor.cache import (
    DEFAULT_CACHE_TTL,
    clear_cache as _clear_cache,
    get_cached as _get_cached,
    invalidate_groups as _invalidate_groups,
    invalidates as _invalidates,
    set_cached as _set_cached,
    cached_call as _cached_call,
)

logger = logging.getLogger(__name__)


# ============================================
# CONVERSATION-LEVEL CACHE (thin wrappers)
# ============================================

_NS = "_cache"  # namespace for DEX cache


def get_cached(user_data: dict, key: str, ttl: int = DEFAULT_CACHE_TTL) -> Optional[Any]:
    return _get_cached(user_data, key, ttl, namespace=_NS)


def set_cached(user_data: dict, key: str, value: Any) -> None:
    _set_cached(user_data, key, value, namespace=_NS)


def clear_cache(user_data: dict, key: Optional[str] = None) -> None:
    _clear_cache(user_data, key, namespace=_NS)


async def cached_call(
    user_data: dict,
    key: str,
    fetch_func: Callable,
    ttl: int = DEFAULT_CACHE_TTL,
    *args,
    **kwargs,
) -> Any:
    return await _cached_call(user_data, key, fetch_func, ttl, *args, namespace=_NS, **kwargs)


# ============================================
# CACHE INVALIDATION GROUPS
# ============================================

CACHE_GROUPS = {
    "balances": [
        "gateway_balances",
        "portfolio_data",
        "wallet_balances",
        "token_balances",
        "gateway_data",
    ],
    "positions": [
        "clmm_positions",
        "liquidity_positions",
        "pool_positions",
        "gateway_lp_positions",
        "gateway_closed_positions",
    ],
    "swaps": ["swap_history", "recent_swaps"],
    "tokens": ["token_cache"],
    "all": None,
}


def invalidate_cache(user_data: dict, *groups: str) -> None:
    """Invalidate cache keys by group name(s)."""
    # Handle special direct-on-user_data keys for backward compat
    for group in groups:
        if group == "all":
            user_data.pop("token_cache", None)
        else:
            keys = CACHE_GROUPS.get(group, [group])
            if keys:
                for key in keys:
                    if key in user_data:
                        user_data.pop(key, None)
    _invalidate_groups(user_data, CACHE_GROUPS, *groups, namespace=_NS)


def invalidates(*groups: str):
    """Decorator that invalidates cache groups after handler execution."""
    return _invalidates(*groups, groups_map=CACHE_GROUPS, namespace=_NS)


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
        self._user_chat_ids: Dict[int, int] = (
            {}
        )  # Track chat_id per user for server selection

    def register_refresh(self, key: str, func: Callable) -> None:
        """Register a function to be called during background refresh.

        Args:
            key: Cache key this function populates
            func: Async function(user_data, client) that fetches and caches data
        """
        self._refresh_funcs[key] = func
        logger.debug(f"Registered background refresh for '{key}'")

    def touch(self, user_id: int, user_data: dict, chat_id: int = None) -> None:
        """Mark user as active, starting background refresh if needed.

        Call this at the start of any handler to keep refresh alive.

        Args:
            user_id: Telegram user ID
            user_data: context.user_data dict
            chat_id: Chat ID for per-chat server selection
        """
        self._last_activity[user_id] = time.time()

        # Store chat_id for this user (for per-chat server selection)
        if chat_id is not None:
            self._user_chat_ids[user_id] = chat_id

        if user_id not in self._tasks or self._tasks[user_id].done():
            self._tasks[user_id] = asyncio.create_task(
                self._refresh_loop(user_id, user_data)
            )
            logger.debug(f"Started background refresh for user {user_id}")

    async def _refresh_loop(self, user_id: int, user_data: dict) -> None:
        """Background loop that refreshes data until inactivity timeout."""
        try:
            # Use per-chat server if available
            chat_id = self._user_chat_ids.get(user_id)
            client = await get_client(chat_id, context=context)
        except Exception as e:
            logger.warning(f"Background refresh: couldn't get client: {e}")
            return

        while True:
            await asyncio.sleep(REFRESH_INTERVAL)

            # Check for inactivity
            last = self._last_activity.get(user_id, 0)
            if time.time() - last > INACTIVITY_TIMEOUT:
                logger.debug(
                    f"Stopping background refresh for user {user_id} (inactive)"
                )
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
        self._user_chat_ids.pop(user_id, None)

    def stop(self, user_id: int) -> None:
        """Manually stop background refresh for a user."""
        if user_id in self._tasks:
            self._tasks[user_id].cancel()
            self._tasks.pop(user_id, None)
            self._last_activity.pop(user_id, None)
            self._user_chat_ids.pop(user_id, None)
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
            chat_id = update.effective_chat.id if update.effective_chat else None
            background_refresh.touch(
                update.effective_user.id, context.user_data, chat_id=chat_id
            )
        return await func(update, context, *args, **kwargs)

    return wrapper


# ============================================
# SERVER CLIENT HELPERS
# ============================================

from config_manager import get_client

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
    pair = swap.get("trading_pair", "N/A")
    side = swap.get("side", "N/A")
    status = swap.get("status", "N/A")
    network = swap.get("network", "")
    tx_hash = swap.get("transaction_hash", "")

    # Format amounts
    input_amount = swap.get("input_amount")
    output_amount = swap.get("output_amount")
    base_token = swap.get("base_token", "")
    quote_token = swap.get("quote_token", "")

    # Build amount string
    if input_amount is not None and output_amount is not None:
        if side == "BUY":
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
    price = swap.get("price")
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
    status = swap.get("status", "UNKNOWN")
    status_emoji = get_status_emoji(status)
    lines.append(f"{status_emoji} Swap Details")
    lines.append("")

    # Trading info
    pair = swap.get("trading_pair", "N/A")
    side = swap.get("side", "N/A")
    lines.append(f"Pair: {pair}")
    lines.append(f"Side: {side}")

    # Amounts
    input_amount = swap.get("input_amount")
    output_amount = swap.get("output_amount")
    base_token = swap.get("base_token", "")
    quote_token = swap.get("quote_token", "")

    if input_amount is not None:
        lines.append(
            f"Input: {_format_amount(input_amount)} {quote_token if side == 'BUY' else base_token}"
        )
    if output_amount is not None:
        lines.append(
            f"Output: {_format_amount(output_amount)} {base_token if side == 'BUY' else quote_token}"
        )

    # Price
    price = swap.get("price")
    if price:
        lines.append(f"Price: {_format_price(price)}")

    # Slippage
    slippage = swap.get("slippage_pct")
    if slippage is not None:
        lines.append(f"Slippage: {slippage}%")

    # Network info
    lines.append("")
    connector = swap.get("connector", "N/A")
    network = swap.get("network", "N/A")
    lines.append(f"Connector: {connector}")
    lines.append(f"Network: {network}")

    # Transaction
    tx_hash = swap.get("transaction_hash", "")
    if tx_hash:
        lines.append(f"Tx: {tx_hash[:16]}...")

    # Timestamp
    timestamp = swap.get("timestamp", "")
    if timestamp:
        # Format timestamp for display
        if "T" in timestamp:
            date_part = timestamp.split("T")[0]
            time_part = (
                timestamp.split("T")[1].split(".")[0]
                if "." in timestamp.split("T")[1]
                else timestamp.split("T")[1].split("+")[0]
            )
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
# RELATIVE TIME FORMATTER
# ============================================


def format_relative_time(timestamp: str) -> str:
    """Format timestamp as relative time (e.g., '53s', '22m', '1h', '2d')

    Args:
        timestamp: ISO format timestamp string

    Returns:
        Relative time string
    """
    from datetime import datetime, timezone

    if not timestamp:
        return ""

    try:
        # Parse ISO timestamp
        if "T" in timestamp:
            # Handle various ISO formats
            ts_str = timestamp.replace("Z", "+00:00")
            if "." in ts_str:
                # Remove microseconds if present
                parts = ts_str.split(".")
                if "+" in parts[1]:
                    ts_str = parts[0] + "+" + parts[1].split("+")[1]
                elif "-" in parts[1]:
                    ts_str = parts[0] + "-" + parts[1].split("-", 1)[1]
                else:
                    ts_str = parts[0]

            # Parse with timezone
            try:
                dt = datetime.fromisoformat(ts_str)
            except ValueError:
                # Fallback: try without timezone
                dt = datetime.fromisoformat(timestamp.split("+")[0].split(".")[0])
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            return ""

        # Calculate difference
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        diff = now - dt
        seconds = int(diff.total_seconds())

        if seconds < 0:
            return "now"
        elif seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}m"
        elif seconds < 86400:
            return f"{seconds // 3600}h"
        else:
            return f"{seconds // 86400}d"

    except Exception as e:
        logger.debug(f"Error formatting relative time: {e}")
        return ""


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


# ============================================
# HISTORY FILTER & PAGINATION HELPERS
# ============================================

from dataclasses import dataclass
from typing import Literal

HistoryType = Literal["swap", "position"]

# Available filter options per history type
HISTORY_FILTERS = {
    "swap": {
        "trading_pair": ["All", "SOL-USDC", "SOL-ORE", "ORE-USDC", "ETH-USDC"],
        "connector": ["All", "jupiter", "uniswap"],
        "status": ["All", "CONFIRMED", "PENDING", "FAILED"],
    },
    "position": {
        "trading_pair": ["All", "SOL-USDC", "ORE-SOL", "METv-SOL"],
        "connector": ["All", "meteora", "orca", "raydium"],
        "status": ["All", "OPEN", "CLOSED"],
    },
}

DEFAULT_PAGE_SIZE = 10


@dataclass
class HistoryFilters:
    """Stores filter and pagination state for history views"""

    history_type: HistoryType = "swap"
    trading_pair: Optional[str] = None  # None = All
    connector: Optional[str] = None  # None = All
    status: Optional[str] = None  # None = All
    network: Optional[str] = None  # None = All
    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE
    total_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "history_type": self.history_type,
            "trading_pair": self.trading_pair,
            "connector": self.connector,
            "status": self.status,
            "network": self.network,
            "offset": self.offset,
            "limit": self.limit,
            "total_count": self.total_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HistoryFilters":
        return cls(
            history_type=data.get("history_type", "swap"),
            trading_pair=data.get("trading_pair"),
            connector=data.get("connector"),
            status=data.get("status"),
            network=data.get("network"),
            offset=data.get("offset", 0),
            limit=data.get("limit", DEFAULT_PAGE_SIZE),
            total_count=data.get("total_count", 0),
        )

    def reset_pagination(self) -> None:
        """Reset pagination when filters change"""
        self.offset = 0

    @property
    def current_page(self) -> int:
        return (self.offset // self.limit) + 1

    @property
    def total_pages(self) -> int:
        if self.total_count == 0:
            return 1
        return (self.total_count + self.limit - 1) // self.limit

    @property
    def has_next(self) -> bool:
        return self.offset + self.limit < self.total_count

    @property
    def has_prev(self) -> bool:
        return self.offset > 0


def get_history_filters(user_data: dict, history_type: HistoryType) -> HistoryFilters:
    """Get current history filters from user data"""
    key = f"history_filters_{history_type}"
    data = user_data.get(key)
    if data:
        return HistoryFilters.from_dict(data)
    return HistoryFilters(history_type=history_type)


def set_history_filters(user_data: dict, filters: HistoryFilters) -> None:
    """Save history filters to user data"""
    key = f"history_filters_{filters.history_type}"
    user_data[key] = filters.to_dict()


def build_filter_buttons(
    filters: HistoryFilters, callback_prefix: str
) -> List[List["InlineKeyboardButton"]]:
    """Build filter button rows for history views

    Args:
        filters: Current filter state
        callback_prefix: Prefix for callback data (e.g., "dex:swap_hist" or "dex:lp_hist")

    Returns:
        List of button rows
    """
    from telegram import InlineKeyboardButton

    rows = []

    # Trading pair filter
    pair_label = filters.trading_pair or "All Pairs"
    rows.append(
        [
            InlineKeyboardButton(
                f"💱 {pair_label}", callback_data=f"{callback_prefix}_filter_pair"
            ),
        ]
    )

    # Connector & Status filters (same row)
    connector_label = filters.connector or "All DEX"
    status_label = filters.status or "All Status"
    rows.append(
        [
            InlineKeyboardButton(
                f"🔌 {connector_label}",
                callback_data=f"{callback_prefix}_filter_connector",
            ),
            InlineKeyboardButton(
                f"📊 {status_label}", callback_data=f"{callback_prefix}_filter_status"
            ),
        ]
    )

    return rows


def build_pagination_buttons(
    filters: HistoryFilters, callback_prefix: str
) -> List["InlineKeyboardButton"]:
    """Build pagination buttons for history views

    Args:
        filters: Current filter state with pagination info
        callback_prefix: Prefix for callback data

    Returns:
        List of buttons for a single row
    """
    from telegram import InlineKeyboardButton

    buttons = []

    # Previous button
    if filters.has_prev:
        buttons.append(
            InlineKeyboardButton("« Prev", callback_data=f"{callback_prefix}_page_prev")
        )
    else:
        buttons.append(InlineKeyboardButton(" ", callback_data="dex:noop"))

    # Page indicator
    page_text = f"{filters.current_page}/{filters.total_pages}"
    buttons.append(InlineKeyboardButton(page_text, callback_data="dex:noop"))

    # Next button
    if filters.has_next:
        buttons.append(
            InlineKeyboardButton("Next »", callback_data=f"{callback_prefix}_page_next")
        )
    else:
        buttons.append(InlineKeyboardButton(" ", callback_data="dex:noop"))

    return buttons


def build_filter_selection_keyboard(
    options: List[str],
    current_value: Optional[str],
    callback_prefix: str,
    back_callback: str,
) -> "InlineKeyboardMarkup":
    """Build a keyboard for selecting a filter value

    Args:
        options: List of available options
        current_value: Currently selected value (None = All)
        callback_prefix: Prefix for callback data
        back_callback: Callback for back button

    Returns:
        InlineKeyboardMarkup with option buttons
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = []
    row = []

    for opt in options:
        # Check if this option is currently selected
        is_selected = (opt == "All" and current_value is None) or (opt == current_value)
        label = f"✓ {opt}" if is_selected else opt

        # Use None for "All" option
        value = "" if opt == "All" else opt
        row.append(
            InlineKeyboardButton(label, callback_data=f"{callback_prefix}_{value}")
        )

        if len(row) == 2:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("« Back", callback_data=back_callback)])

    return InlineKeyboardMarkup(buttons)
