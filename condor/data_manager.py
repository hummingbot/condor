"""
Context-aware in-memory data manager for Condor.

Provides:
- Per-user sessions with context-aware TTLs (fast when trading, slow when idle)
- Background cache warming for active contexts
- Token-bucket rate limiting shared across foreground and background fetches
- Graceful degradation (stale data on error, exponential backoff)

Architecture:
    DataManager (singleton)
      _sessions: {user_id: UserSession}
      _bg_task: asyncio.Task (tick loop)
      _rate_limiter: RateLimiter
      _fetch_registry: {DataType: FetchSpec}

    UserSession (per-user, in-memory)
      entries: {cache_key: CacheEntry}
      active_context: ActiveContext | None
      last_activity: float
      server_name: str
      chat_id: int

This module is independent of PicklePersistence. Non-migrated handlers
continue using condor.cache as before.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ============================================
# DATA TYPES & CONFIGURATION
# ============================================


@dataclass(frozen=True)
class DataTypeConfig:
    """TTL and refresh configuration for a data type."""

    idle_ttl: float  # TTL when user has no active context
    active_ttl: float  # TTL when user has a relevant active context
    bg_interval: float  # Background refresh interval (0 = no bg refresh)
    stale_threshold: float  # If data is younger than this, always return cached
    group: str  # Invalidation group name


class DataType(Enum):
    """Supported data types with their configurations."""

    CEX_BALANCES = DataTypeConfig(
        idle_ttl=120, active_ttl=10, bg_interval=15, stale_threshold=2, group="cex_balances"
    )
    CEX_PRICES = DataTypeConfig(
        idle_ttl=60, active_ttl=3, bg_interval=5, stale_threshold=2, group="cex_prices"
    )
    CEX_POSITIONS = DataTypeConfig(
        idle_ttl=120, active_ttl=10, bg_interval=15, stale_threshold=2, group="cex_positions"
    )
    CEX_ACTIVE_ORDERS = DataTypeConfig(
        idle_ttl=60, active_ttl=10, bg_interval=15, stale_threshold=2, group="cex_orders"
    )
    CEX_TRADING_RULES = DataTypeConfig(
        idle_ttl=600, active_ttl=300, bg_interval=0, stale_threshold=10, group="cex_rules"
    )
    CEX_CONNECTORS = DataTypeConfig(
        idle_ttl=600, active_ttl=300, bg_interval=0, stale_threshold=10, group="cex_connectors"
    )

    @property
    def config(self) -> DataTypeConfig:
        return self.value


# Context commands and the data types they activate
_CONTEXT_DATA_TYPES: Dict[str, List[DataType]] = {
    "trade": [
        DataType.CEX_BALANCES,
        DataType.CEX_PRICES,
        DataType.CEX_ACTIVE_ORDERS,
        DataType.CEX_TRADING_RULES,
        # CEX_POSITIONS added dynamically for perpetual connectors
    ],
}


# ============================================
# CACHE ENTRY
# ============================================


@dataclass
class CacheEntry:
    """A single cached value with metadata."""

    value: Any
    fetched_at: float
    data_type: DataType
    cache_key: str
    consecutive_errors: int = 0
    last_error_at: float = 0.0
    last_bg_refresh_at: float = 0.0


# ============================================
# ACTIVE CONTEXT
# ============================================


@dataclass
class ActiveContext:
    """Describes what the user is currently doing."""

    command: str  # e.g. "trade", "portfolio"
    connector: Optional[str] = None
    trading_pair: Optional[str] = None
    account: Optional[str] = None
    server_name: Optional[str] = None
    is_perpetual: bool = False

    def relevant_data_types(self) -> List[DataType]:
        """Return data types that should use active TTLs."""
        types = list(_CONTEXT_DATA_TYPES.get(self.command, []))
        if self.is_perpetual and DataType.CEX_POSITIONS not in types:
            types.append(DataType.CEX_POSITIONS)
        return types


# ============================================
# USER SESSION
# ============================================


class UserSession:
    """Per-user in-memory cache and context state."""

    def __init__(self, user_id: int, chat_id: int = 0, server_name: str = ""):
        self.user_id = user_id
        self.chat_id = chat_id
        self.server_name = server_name
        self.entries: Dict[str, CacheEntry] = {}
        self.active_context: Optional[ActiveContext] = None
        self.last_activity: float = time.time()

    def get_ttl(self, data_type: DataType) -> float:
        """Resolve TTL based on whether this data type is relevant to the active context."""
        cfg = data_type.config
        if self.active_context and data_type in self.active_context.relevant_data_types():
            return cfg.active_ttl
        return cfg.idle_ttl

    def get(self, cache_key: str, data_type: DataType) -> Optional[Any]:
        """Get cached value if still valid.

        Returns the value or None if expired/missing.
        """
        entry = self.entries.get(cache_key)
        if entry is None:
            return None

        age = time.time() - entry.fetched_at

        # Always return if within stale threshold
        if age <= data_type.config.stale_threshold:
            return entry.value

        # Check TTL
        ttl = self.get_ttl(data_type)
        if age > ttl:
            return None

        return entry.value

    def get_stale(self, cache_key: str) -> Optional[Any]:
        """Get cached value regardless of TTL (for graceful degradation)."""
        entry = self.entries.get(cache_key)
        return entry.value if entry else None

    def put(self, cache_key: str, data_type: DataType, value: Any) -> None:
        """Store a fetched value."""
        now = time.time()
        existing = self.entries.get(cache_key)
        self.entries[cache_key] = CacheEntry(
            value=value,
            fetched_at=now,
            data_type=data_type,
            cache_key=cache_key,
            consecutive_errors=0,
            last_error_at=0.0,
            last_bg_refresh_at=existing.last_bg_refresh_at if existing else 0.0,
        )

    def record_error(self, cache_key: str, data_type: DataType) -> None:
        """Record a fetch error for backoff tracking."""
        now = time.time()
        entry = self.entries.get(cache_key)
        if entry:
            entry.consecutive_errors += 1
            entry.last_error_at = now
        else:
            self.entries[cache_key] = CacheEntry(
                value=None,
                fetched_at=0.0,
                data_type=data_type,
                cache_key=cache_key,
                consecutive_errors=1,
                last_error_at=now,
            )

    def invalidate(self, *groups: str) -> None:
        """Invalidate cache entries by group name(s)."""
        if "all" in groups:
            self.entries.clear()
            return

        keys_to_remove = []
        for key, entry in self.entries.items():
            if entry.data_type.config.group in groups:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self.entries[key]

    def set_context(self, context: Optional[ActiveContext]) -> None:
        """Set or clear the active context."""
        self.active_context = context
        self.last_activity = time.time()

    def touch(self) -> None:
        """Mark the session as active."""
        self.last_activity = time.time()


# ============================================
# RATE LIMITER (token-bucket)
# ============================================


class RateLimiter:
    """Async token-bucket rate limiter.

    Shared by foreground dm.get() calls and background refresh.
    """

    def __init__(self, per_second: float = 5.0, per_minute: float = 100.0):
        self._per_second = per_second
        self._per_minute = per_minute
        self._tokens_sec = per_second
        self._tokens_min = per_minute
        self._last_refill_sec = time.monotonic()
        self._last_refill_min = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, timeout: float = 5.0) -> bool:
        """Wait for a token. Returns True if acquired, False on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                self._refill()
                if self._tokens_sec >= 1.0 and self._tokens_min >= 1.0:
                    self._tokens_sec -= 1.0
                    self._tokens_min -= 1.0
                    return True

            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

    def _refill(self) -> None:
        now = time.monotonic()
        # Per-second refill
        elapsed_sec = now - self._last_refill_sec
        self._tokens_sec = min(self._per_second, self._tokens_sec + elapsed_sec * self._per_second)
        self._last_refill_sec = now
        # Per-minute refill
        elapsed_min = now - self._last_refill_min
        self._tokens_min = min(self._per_minute, self._tokens_min + elapsed_min * (self._per_minute / 60.0))
        self._last_refill_min = now


# ============================================
# FETCH SPEC & REGISTRY
# ============================================


@dataclass
class FetchSpec:
    """Describes how to fetch data for a DataType."""

    fetch_func: Callable  # async (client, **kwargs) -> Any
    key_builder: Callable  # (**kwargs) -> str


# ============================================
# DATA MANAGER (singleton)
# ============================================


_INACTIVITY_TIMEOUT = 300  # 5 min - stop bg refresh for idle users
_SESSION_CLEANUP_INTERVAL = 600  # 10 min - clean up stale sessions
_BG_TICK_INTERVAL = 1  # seconds between bg loop ticks


class DataManager:
    """Central in-memory data manager with context-aware caching."""

    def __init__(self):
        self._sessions: Dict[int, UserSession] = {}
        self._fetch_registry: Dict[DataType, FetchSpec] = {}
        self._rate_limiter = RateLimiter(per_second=5.0, per_minute=100.0)
        self._bg_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_cleanup = time.time()

    # ------ Session management ------

    def get_session(self, user_id: int, chat_id: int = 0, server_name: str = "") -> UserSession:
        """Get or create a session for a user."""
        session = self._sessions.get(user_id)
        if session is None:
            session = UserSession(user_id, chat_id, server_name)
            self._sessions[user_id] = session
        else:
            if chat_id:
                session.chat_id = chat_id
            if server_name:
                session.server_name = server_name
        return session

    def remove_session(self, user_id: int) -> None:
        """Remove a user's session (e.g. on server switch)."""
        self._sessions.pop(user_id, None)

    # ------ Fetch registry ------

    def register_fetch(self, data_type: DataType, fetch_func: Callable, key_builder: Callable) -> None:
        """Register a fetch function for a data type."""
        self._fetch_registry[data_type] = FetchSpec(fetch_func=fetch_func, key_builder=key_builder)

    # ------ Client resolution ------

    async def _get_client(self, session: UserSession):
        """Resolve an API client for a session.

        Uses the server_name if available, otherwise falls back
        to ConfigManager's default server resolution.
        """
        from config_manager import get_config_manager

        cm = get_config_manager()
        server_name = session.server_name or None
        if not server_name and session.chat_id:
            # Try to resolve from chat defaults
            server_name = cm.get_chat_default_server(session.chat_id)
        return await cm.get_client(server_name)

    # ------ Core API ------

    async def get(
        self,
        user_id: int,
        data_type: DataType,
        chat_id: int = 0,
        server_name: str = "",
        **kwargs,
    ) -> Optional[Any]:
        """Get data, returning cached if valid or fetching fresh.

        Args:
            user_id: Telegram user ID
            data_type: Type of data to fetch
            chat_id: Chat ID for client resolution
            server_name: Server name for client resolution
            **kwargs: Passed to key_builder and fetch_func

        Returns:
            The data, or None on failure
        """
        session = self.get_session(user_id, chat_id, server_name)
        session.touch()

        spec = self._fetch_registry.get(data_type)
        if not spec:
            logger.warning(f"No fetch registered for {data_type.name}")
            return None

        cache_key = spec.key_builder(**kwargs)

        # Check cache
        cached = session.get(cache_key, data_type)
        if cached is not None:
            return cached

        # Fetch fresh data
        if not await self._rate_limiter.acquire(timeout=5.0):
            logger.warning(f"Rate limit hit for {data_type.name}, returning stale data")
            return session.get_stale(cache_key)

        try:
            client = await self._get_client(session)
            result = await spec.fetch_func(client, **kwargs)
            session.put(cache_key, data_type, result)
            return result
        except Exception as e:
            logger.warning(f"Fetch failed for {data_type.name} key={cache_key}: {e}")
            session.record_error(cache_key, data_type)
            # Graceful degradation: return stale data
            stale = session.get_stale(cache_key)
            if stale is not None:
                logger.info(f"Returning stale data for {data_type.name}")
            return stale

    def invalidate(self, user_id: int, *groups: str) -> None:
        """Invalidate cache entries for a user by group names."""
        session = self._sessions.get(user_id)
        if session:
            session.invalidate(*groups)
            logger.debug(f"Invalidated groups {groups} for user {user_id}")

    def invalidate_server(self, user_id: int) -> None:
        """Clear all cached data for a user (e.g. on server switch)."""
        session = self._sessions.get(user_id)
        if session:
            session.entries.clear()
            session.active_context = None
            logger.info(f"Cleared all DataManager cache for user {user_id} (server switch)")

    def set_context(
        self,
        user_id: int,
        command: str,
        connector: Optional[str] = None,
        trading_pair: Optional[str] = None,
        account: Optional[str] = None,
        server_name: Optional[str] = None,
        chat_id: int = 0,
    ) -> None:
        """Set the active context for a user."""
        is_perpetual = "perpetual" in (connector or "").lower()
        ctx = ActiveContext(
            command=command,
            connector=connector,
            trading_pair=trading_pair,
            account=account,
            server_name=server_name,
            is_perpetual=is_perpetual,
        )
        session = self.get_session(user_id, chat_id, server_name or "")
        session.set_context(ctx)
        logger.debug(
            f"Set context for user {user_id}: command={command}, "
            f"connector={connector}, pair={trading_pair}"
        )

    def clear_context(self, user_id: int) -> None:
        """Clear the active context for a user."""
        session = self._sessions.get(user_id)
        if session:
            session.set_context(None)

    # ------ Background refresh ------

    def start(self) -> None:
        """Start the background refresh loop."""
        if self._running:
            return
        self._running = True
        self._bg_task = asyncio.create_task(self._bg_loop())
        logger.info("DataManager background refresh started")

    def stop(self) -> None:
        """Stop the background refresh loop."""
        self._running = False
        if self._bg_task and not self._bg_task.done():
            self._bg_task.cancel()
            logger.info("DataManager background refresh stopped")

    async def _bg_loop(self) -> None:
        """Background loop: ticks every second, refreshes active sessions."""
        while self._running:
            try:
                await asyncio.sleep(_BG_TICK_INTERVAL)
                await self._bg_tick()

                # Periodic cleanup
                now = time.time()
                if now - self._last_cleanup > _SESSION_CLEANUP_INTERVAL:
                    self._cleanup_sessions()
                    self._last_cleanup = now
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"DataManager bg loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _bg_tick(self) -> None:
        """Single tick of the background loop."""
        now = time.time()

        for user_id, session in list(self._sessions.items()):
            # Skip inactive users
            if now - session.last_activity > _INACTIVITY_TIMEOUT:
                continue

            # Skip users without active context
            if not session.active_context:
                continue

            relevant_types = session.active_context.relevant_data_types()

            for data_type in relevant_types:
                bg_interval = data_type.config.bg_interval
                if bg_interval <= 0:
                    continue

                spec = self._fetch_registry.get(data_type)
                if not spec:
                    continue

                # Find entries of this data type that need refresh
                for cache_key, entry in list(session.entries.items()):
                    if entry.data_type != data_type:
                        continue

                    # Check if bg refresh is due
                    time_since_last_bg = now - entry.last_bg_refresh_at
                    if time_since_last_bg < bg_interval:
                        continue

                    # Exponential backoff on consecutive errors
                    if entry.consecutive_errors >= 3:
                        backoff = min(60, 2 ** entry.consecutive_errors)
                        if now - entry.last_error_at < backoff:
                            continue

                    # Try to acquire rate limit
                    if not await self._rate_limiter.acquire(timeout=0.5):
                        return  # Rate limited, stop this tick

                    try:
                        client = await self._get_client(session)

                        # Reconstruct kwargs from cache key
                        # The fetch_func is called with kwargs derived from the key
                        # For bg refresh, we re-call with the same params
                        result = await spec.fetch_func(client, **self._extract_kwargs_from_key(cache_key, data_type))
                        session.put(cache_key, data_type, result)
                        # Update bg refresh timestamp
                        refreshed_entry = session.entries.get(cache_key)
                        if refreshed_entry:
                            refreshed_entry.last_bg_refresh_at = now
                        logger.debug(f"BG refreshed {data_type.name} for user {user_id}")
                    except Exception as e:
                        session.record_error(cache_key, data_type)
                        logger.debug(f"BG refresh error {data_type.name} user {user_id}: {e}")

    def _extract_kwargs_from_key(self, cache_key: str, data_type: DataType) -> dict:
        """Extract fetch kwargs from a cache key.

        Cache keys are structured like:
            cex_balances:account_name
            cex_prices:connector:trading_pair
            cex_positions:connector
            cex_orders:limit
            cex_rules:connector
            cex_connectors:server:account
        """
        parts = cache_key.split(":")
        prefix = parts[0] if parts else ""

        if data_type == DataType.CEX_BALANCES and len(parts) >= 2:
            return {"account_name": parts[1]}
        elif data_type == DataType.CEX_PRICES and len(parts) >= 3:
            return {"connector_name": parts[1], "trading_pair": parts[2]}
        elif data_type == DataType.CEX_POSITIONS and len(parts) >= 2:
            return {"connector_name": parts[1]}
        elif data_type == DataType.CEX_ACTIVE_ORDERS and len(parts) >= 2:
            return {"limit": int(parts[1]) if parts[1].isdigit() else 5}
        elif data_type == DataType.CEX_TRADING_RULES and len(parts) >= 2:
            return {"connector_name": parts[1]}
        elif data_type == DataType.CEX_CONNECTORS and len(parts) >= 3:
            return {"server_name": parts[1], "account_name": parts[2]}
        return {}

    def _cleanup_sessions(self) -> None:
        """Remove sessions that have been inactive for a long time."""
        now = time.time()
        cutoff = now - _SESSION_CLEANUP_INTERVAL
        stale_users = [uid for uid, s in self._sessions.items() if s.last_activity < cutoff]
        for uid in stale_users:
            del self._sessions[uid]
        if stale_users:
            logger.info(f"Cleaned up {len(stale_users)} stale sessions")


# ============================================
# SINGLETON & CONVENIENCE API
# ============================================


_instance: Optional[DataManager] = None


def get_data_manager() -> DataManager:
    """Get the global DataManager singleton."""
    global _instance
    if _instance is None:
        _instance = DataManager()
    return _instance


async def dm_get(
    user_id: int,
    data_type: DataType,
    chat_id: int = 0,
    server_name: str = "",
    **kwargs,
) -> Optional[Any]:
    """Convenience: get data from the DataManager."""
    return await get_data_manager().get(user_id, data_type, chat_id, server_name, **kwargs)


def dm_invalidate(user_id: int, *groups: str) -> None:
    """Convenience: invalidate cache groups."""
    get_data_manager().invalidate(user_id, *groups)


def dm_set_context(
    user_id: int,
    command: str,
    connector: Optional[str] = None,
    trading_pair: Optional[str] = None,
    account: Optional[str] = None,
    server_name: Optional[str] = None,
    chat_id: int = 0,
) -> None:
    """Convenience: set user's active context."""
    get_data_manager().set_context(
        user_id, command, connector, trading_pair, account, server_name, chat_id
    )


def dm_clear_context(user_id: int) -> None:
    """Convenience: clear user's active context."""
    get_data_manager().clear_context(user_id)


# ============================================
# FETCH REGISTRATIONS
# ============================================


def register_default_fetches() -> None:
    """Register the default fetch functions for CEX data types.

    Reuses existing fetch functions from handlers/cex/_shared.py
    and handlers/executors/_shared.py.
    """
    dm = get_data_manager()

    # --- CEX_BALANCES ---
    async def _fetch_balances(client, account_name: str = "master_account", **_kw):
        from handlers.cex._shared import fetch_cex_balances
        return await fetch_cex_balances(client, account_name)

    dm.register_fetch(
        DataType.CEX_BALANCES,
        fetch_func=_fetch_balances,
        key_builder=lambda account_name="master_account", **_kw: f"cex_balances:{account_name}",
    )

    # --- CEX_PRICES ---
    async def _fetch_prices(client, connector_name: str = "", trading_pair: str = "", **_kw):
        from handlers.executors._shared import fetch_current_price
        return await fetch_current_price(client, connector_name, trading_pair)

    dm.register_fetch(
        DataType.CEX_PRICES,
        fetch_func=_fetch_prices,
        key_builder=lambda connector_name="", trading_pair="", **_kw: f"cex_prices:{connector_name}:{trading_pair}",
    )

    # --- CEX_POSITIONS ---
    async def _fetch_positions(client, connector_name: str = None, **_kw):
        from handlers.cex._shared import fetch_positions
        return await fetch_positions(client, connector_name)

    dm.register_fetch(
        DataType.CEX_POSITIONS,
        fetch_func=_fetch_positions,
        key_builder=lambda connector_name=None, **_kw: f"cex_positions:{connector_name or 'all'}",
    )

    # --- CEX_ACTIVE_ORDERS ---
    async def _fetch_orders(client, limit: int = 5, **_kw):
        try:
            result = await client.trading.get_active_orders(limit=limit)
            return result.get("data", [])
        except Exception as e:
            logger.warning(f"Error fetching active orders: {e}")
            return []

    dm.register_fetch(
        DataType.CEX_ACTIVE_ORDERS,
        fetch_func=_fetch_orders,
        key_builder=lambda limit=5, **_kw: f"cex_orders:{limit}",
    )

    # --- CEX_TRADING_RULES ---
    async def _fetch_rules(client, connector_name: str = "", **_kw):
        from handlers.cex._shared import fetch_trading_rules
        return await fetch_trading_rules(client, connector_name)

    dm.register_fetch(
        DataType.CEX_TRADING_RULES,
        fetch_func=_fetch_rules,
        key_builder=lambda connector_name="", **_kw: f"cex_rules:{connector_name}",
    )

    # --- CEX_CONNECTORS ---
    async def _fetch_connectors(client, server_name: str = "default", account_name: str = "master_account", **_kw):
        from handlers.cex._shared import fetch_available_cex_connectors
        return await fetch_available_cex_connectors(client, account_name)

    dm.register_fetch(
        DataType.CEX_CONNECTORS,
        fetch_func=_fetch_connectors,
        key_builder=lambda server_name="default", account_name="master_account", **_kw: f"cex_connectors:{server_name}:{account_name}",
    )

    logger.info("DataManager: registered default CEX fetch functions")
