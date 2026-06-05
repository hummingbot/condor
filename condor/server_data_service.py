"""
ServerDataService — Unified server-centric data layer for Condor.

Single cache that all consumers (Telegram, Web REST, WebSocket, MCP) read from.
Subscription-based polling with per-server rate limiting and health tracking.

Architecture:
    ServerDataService (singleton)
      _cache: {CacheKey: CacheEntry}
      _subscriptions: {CacheKey: {subscriber_id: Subscription}}
      _health: {server_name: ServerHealth}
      _rate_limiters: {server_name: RateLimiter}
      _fetch_registry: {ServerDataType: FetchSpec}
      _poll_task: asyncio.Task (1s tick loop)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================
# DATA TYPES
# ============================================


class ServerDataType(Enum):
    """All data types fetchable from a Hummingbot server."""

    PORTFOLIO = "portfolio"
    PRICES = "prices"
    POSITIONS = "positions"
    ACTIVE_ORDERS = "active_orders"
    TRADING_RULES = "trading_rules"
    CONNECTORS = "connectors"
    BOTS_STATUS = "bots_status"
    EXECUTORS = "executors"
    BOT_RUNS = "bot_runs"
    CANDLE_CONNECTORS = "candle_connectors"
    SERVER_STATUS = "server_status"
    ALL_CONNECTORS = "all_connectors"


@dataclass(frozen=True)
class DataTypeDefaults:
    """Default interval, TTL, and stale threshold for a data type."""

    interval: float  # Default polling interval (seconds)
    ttl: float  # How long cached data is considered valid without subscribers
    stale_threshold: float  # Data younger than this is always returned


_DEFAULTS: Dict[ServerDataType, DataTypeDefaults] = {
    ServerDataType.PORTFOLIO: DataTypeDefaults(interval=10, ttl=60, stale_threshold=5),
    ServerDataType.PRICES: DataTypeDefaults(interval=3, ttl=30, stale_threshold=2),
    ServerDataType.POSITIONS: DataTypeDefaults(interval=10, ttl=60, stale_threshold=5),
    ServerDataType.ACTIVE_ORDERS: DataTypeDefaults(interval=10, ttl=60, stale_threshold=5),
    ServerDataType.TRADING_RULES: DataTypeDefaults(interval=300, ttl=600, stale_threshold=30),
    ServerDataType.CONNECTORS: DataTypeDefaults(interval=300, ttl=600, stale_threshold=30),
    ServerDataType.BOTS_STATUS: DataTypeDefaults(interval=5, ttl=30, stale_threshold=3),
    ServerDataType.EXECUTORS: DataTypeDefaults(interval=2, ttl=30, stale_threshold=1),
    ServerDataType.BOT_RUNS: DataTypeDefaults(interval=30, ttl=120, stale_threshold=10),
    ServerDataType.CANDLE_CONNECTORS: DataTypeDefaults(interval=300, ttl=600, stale_threshold=30),
    ServerDataType.SERVER_STATUS: DataTypeDefaults(interval=60, ttl=120, stale_threshold=15),
    ServerDataType.ALL_CONNECTORS: DataTypeDefaults(interval=300, ttl=600, stale_threshold=30),
}


# Mapping from old DataManager DataType names to ServerDataType
_OLD_DATATYPE_MAP = {
    "CEX_BALANCES": ServerDataType.PORTFOLIO,
    "CEX_PRICES": ServerDataType.PRICES,
    "CEX_POSITIONS": ServerDataType.POSITIONS,
    "CEX_ACTIVE_ORDERS": ServerDataType.ACTIVE_ORDERS,
    "CEX_TRADING_RULES": ServerDataType.TRADING_RULES,
    "CEX_CONNECTORS": ServerDataType.CONNECTORS,
    "PORTFOLIO": ServerDataType.PORTFOLIO,
    "BOTS_STATUS": ServerDataType.BOTS_STATUS,
    "EXECUTORS": ServerDataType.EXECUTORS,
}

# Mapping from old invalidation group names to ServerDataTypes
_OLD_GROUP_MAP = {
    "cex_balances": [ServerDataType.PORTFOLIO],
    "cex_prices": [ServerDataType.PRICES],
    "cex_positions": [ServerDataType.POSITIONS],
    "cex_orders": [ServerDataType.ACTIVE_ORDERS],
    "cex_rules": [ServerDataType.TRADING_RULES],
    "cex_connectors": [ServerDataType.CONNECTORS],
    "portfolio": [ServerDataType.PORTFOLIO],
    "bots": [ServerDataType.BOTS_STATUS],
    "executors": [ServerDataType.EXECUTORS],
    "all": list(ServerDataType),
}


# ============================================
# CACHE KEY & ENTRY
# ============================================


@dataclass(frozen=True)
class CacheKey:
    """Server-centric cache key."""

    server: str
    data_type: ServerDataType
    params: FrozenSet[Tuple[str, str]] = frozenset()

    @staticmethod
    def make(server: str, data_type: ServerDataType, **params) -> "CacheKey":
        """Create a CacheKey, converting params to a frozenset."""
        p = frozenset((k, str(v)) for k, v in sorted(params.items()) if v is not None)
        return CacheKey(server=server, data_type=data_type, params=p)

    @property
    def params_dict(self) -> dict:
        return dict(self.params)


@dataclass
class CacheEntry:
    """A single cached value with metadata."""

    key: CacheKey
    value: Any
    fetched_at: float
    consecutive_errors: int = 0
    last_error_at: float = 0.0


# ============================================
# SUBSCRIPTION
# ============================================


@dataclass
class Subscription:
    """A consumer's interest in a CacheKey."""

    subscriber_id: str
    key: CacheKey
    interval: float  # Desired refresh interval in seconds
    callback: Optional[Callable] = None  # async callback(key, old_value, new_value)


# ============================================
# SERVER HEALTH
# ============================================


class ServerStatus(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass
class ServerHealth:
    """Per-server health tracking."""

    server: str
    status: ServerStatus = ServerStatus.UNKNOWN
    last_success_at: float = 0.0
    last_error_at: float = 0.0
    last_latency_ms: float = 0.0
    consecutive_failures: int = 0
    total_fetches: int = 0
    total_errors: int = 0

    def record_success(self, latency_ms: float) -> None:
        self.last_success_at = time.time()
        self.last_latency_ms = latency_ms
        self.consecutive_failures = 0
        self.total_fetches += 1
        self.status = ServerStatus.ONLINE

    def record_error(self) -> None:
        self.last_error_at = time.time()
        self.consecutive_failures += 1
        self.total_fetches += 1
        self.total_errors += 1
        if self.consecutive_failures >= 5:
            self.status = ServerStatus.OFFLINE
        elif self.consecutive_failures >= 2:
            self.status = ServerStatus.DEGRADED


# ============================================
# RATE LIMITER (token-bucket, per-server)
# ============================================


class RateLimiter:
    """Async token-bucket rate limiter."""

    def __init__(self, per_second: float = 5.0, per_minute: float = 100.0):
        self._per_second = per_second
        self._per_minute = per_minute
        self._tokens_sec = per_second
        self._tokens_min = per_minute
        self._last_refill_sec = time.monotonic()
        self._last_refill_min = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, timeout: float = 5.0) -> bool:
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
        elapsed_sec = now - self._last_refill_sec
        self._tokens_sec = min(self._per_second, self._tokens_sec + elapsed_sec * self._per_second)
        self._last_refill_sec = now
        elapsed_min = now - self._last_refill_min
        self._tokens_min = min(self._per_minute, self._tokens_min + elapsed_min * (self._per_minute / 60.0))
        self._last_refill_min = now


# ============================================
# FETCH SPEC
# ============================================


@dataclass
class FetchSpec:
    """Describes how to fetch data for a ServerDataType."""

    fetch_func: Callable  # async (client, **params) -> Any


# ============================================
# SERVER DATA SERVICE
# ============================================

_POLL_TICK = 1  # seconds between poll loop snapshots
_CLEANUP_INTERVAL = 300  # clean stale entries every 5 min


class ServerDataService:
    """Unified server-centric data cache with subscription-based polling."""

    def __init__(self):
        self._cache: Dict[CacheKey, CacheEntry] = {}
        self._subscriptions: Dict[CacheKey, Dict[str, Subscription]] = {}
        self._health: Dict[str, ServerHealth] = {}
        self._rate_limiters: Dict[str, RateLimiter] = {}
        self._fetch_registry: Dict[ServerDataType, FetchSpec] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_cleanup = time.time()
        # Listeners for DataManager compatibility (sync callbacks)
        self._listeners: List[Callable] = []

    # ------ Fetch registry ------

    def register_fetch(self, data_type: ServerDataType, fetch_func: Callable) -> None:
        """Register a fetch function for a data type."""
        self._fetch_registry[data_type] = FetchSpec(fetch_func=fetch_func)

    # ------ Client resolution ------

    async def _get_client(self, server_name: str):
        from config_manager import get_config_manager
        return await get_config_manager().get_client(server_name)

    # ------ Rate limiter per server ------

    def _get_rate_limiter(self, server: str) -> RateLimiter:
        if server not in self._rate_limiters:
            self._rate_limiters[server] = RateLimiter(per_second=5.0, per_minute=100.0)
        return self._rate_limiters[server]

    # ------ Health tracking ------

    def get_server_health(self, server: str) -> ServerHealth:
        if server not in self._health:
            self._health[server] = ServerHealth(server=server)
        return self._health[server]

    # ------ Subscription API ------

    async def subscribe(
        self,
        server: str,
        data_type: ServerDataType,
        subscriber_id: str,
        interval: float = 0,
        callback: Optional[Callable] = None,
        **params,
    ) -> CacheKey:
        """Subscribe to data updates. Returns the CacheKey.

        If interval is 0 or not provided, the default for the data type is used.
        Callback signature: async callback(key: CacheKey, old_value, new_value)
        """
        if interval <= 0:
            interval = _DEFAULTS[data_type].interval

        key = CacheKey.make(server, data_type, **params)
        sub = Subscription(
            subscriber_id=subscriber_id,
            key=key,
            interval=interval,
            callback=callback,
        )

        if key not in self._subscriptions:
            self._subscriptions[key] = {}
        self._subscriptions[key][subscriber_id] = sub

        logger.debug(
            "SDS subscribe: %s -> %s:%s (interval=%.1fs, subs=%d)",
            subscriber_id, server, data_type.value, interval,
            len(self._subscriptions[key]),
        )

        # Prime cache if empty
        if key not in self._cache:
            try:
                await self._fetch_and_cache(key)
            except Exception as e:
                logger.debug("SDS prime failed for %s: %s", key, e)

        return key

    def unsubscribe(self, key: CacheKey, subscriber_id: str) -> None:
        """Remove a subscription. Stops polling if last subscriber."""
        subs = self._subscriptions.get(key)
        if subs:
            subs.pop(subscriber_id, None)
            if not subs:
                del self._subscriptions[key]
                logger.debug("SDS: no subscribers left for %s, polling stopped", key)

    def unsubscribe_all(self, subscriber_id: str) -> None:
        """Remove all subscriptions for a subscriber."""
        empty_keys = []
        for key, subs in self._subscriptions.items():
            subs.pop(subscriber_id, None)
            if not subs:
                empty_keys.append(key)
        for key in empty_keys:
            del self._subscriptions[key]

    # ------ Read API ------

    def get(self, server: str, data_type: ServerDataType, **params) -> Optional[Any]:
        """Read from cache only (hot path). Returns None if not cached or expired."""
        key = CacheKey.make(server, data_type, **params)
        entry = self._cache.get(key)
        if entry is None:
            return None

        defaults = _DEFAULTS[data_type]
        age = time.time() - entry.fetched_at

        # Always return if within stale threshold
        if age <= defaults.stale_threshold:
            return entry.value

        # If subscribed, data is actively refreshed — use TTL
        if key in self._subscriptions and self._subscriptions[key]:
            return entry.value if age <= defaults.ttl else None

        # No subscribers — use idle TTL
        return entry.value if age <= defaults.ttl else None

    async def get_or_fetch(self, server: str, data_type: ServerDataType, **params) -> Optional[Any]:
        """Return cached data if fresh, otherwise fetch. For REST/one-shot reads."""
        key = CacheKey.make(server, data_type, **params)

        # Check cache
        cached = self.get(server, data_type, **params)
        if cached is not None:
            return cached

        # Fetch fresh
        return await self._fetch_and_cache(key)

    def get_entry(self, server: str, data_type: ServerDataType, **params) -> Optional[CacheEntry]:
        """Get the full cache entry (for age/metadata checks)."""
        key = CacheKey.make(server, data_type, **params)
        return self._cache.get(key)

    # ------ Write API (for manual puts after mutations) ------

    def put(self, server: str, data_type: ServerDataType, value: Any, **params) -> None:
        """Manually insert/update a cache entry. Fires change callbacks."""
        key = CacheKey.make(server, data_type, **params)
        old_entry = self._cache.get(key)
        old_value = old_entry.value if old_entry else None

        self._cache[key] = CacheEntry(
            key=key,
            value=value,
            fetched_at=time.time(),
        )

        self._notify_listeners(key, value)

        if value != old_value:
            self._fire_callbacks(key, old_value, value)

    # ------ Invalidation ------

    def invalidate(self, server: str, *data_types: ServerDataType) -> None:
        """Invalidate cache entries for specific data types on a server."""
        keys_to_remove = [
            k for k in self._cache
            if k.server == server and k.data_type in data_types
        ]
        for k in keys_to_remove:
            del self._cache[k]
        if keys_to_remove:
            logger.debug("SDS invalidated %d entries for %s: %s", len(keys_to_remove), server,
                         [dt.value for dt in data_types])

    def invalidate_by_groups(self, server: str, *groups: str) -> None:
        """Invalidate using old DataManager group names (compatibility)."""
        data_types: Set[ServerDataType] = set()
        for group in groups:
            mapped = _OLD_GROUP_MAP.get(group, [])
            data_types.update(mapped)
        if data_types:
            self.invalidate(server, *data_types)

    def invalidate_server(self, server: str) -> None:
        """Clear all cached data for a server."""
        keys_to_remove = [k for k in self._cache if k.server == server]
        for k in keys_to_remove:
            del self._cache[k]
        logger.info("SDS invalidated all cache for server %s (%d entries)", server, len(keys_to_remove))

    # ------ Listener compatibility (for WebSocketManager) ------

    def add_listener(self, callback: Callable) -> None:
        """Add a sync listener: callback(server_name, cache_key_str, data_type_name, value)"""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable) -> None:
        self._listeners = [cb for cb in self._listeners if cb is not callback]

    def _notify_listeners(self, key: CacheKey, value: Any) -> None:
        """Notify listeners with server/channel/data_type/value arguments.

        The WS manager uses the data_type name string to map to channels.
        No longer imports from data_manager — passes the ServerDataType directly.
        """
        if not self._listeners:
            return

        # Build a channel-compatible cache_key string
        params = key.params_dict
        if key.data_type == ServerDataType.PRICES:
            cache_key_str = f"cex_prices:{params.get('connector_name', '')}:{params.get('trading_pair', '')}"
        elif key.data_type == ServerDataType.PORTFOLIO:
            account = params.get("account_name", "")
            cache_key_str = f"cex_balances:{account}" if account else "portfolio"
        elif key.data_type == ServerDataType.BOTS_STATUS:
            cache_key_str = "bots_status"
        elif key.data_type == ServerDataType.EXECUTORS:
            cache_key_str = "executors"
        else:
            cache_key_str = key.data_type.value

        for cb in self._listeners:
            try:
                cb(key.server, cache_key_str, key.data_type, value)
            except Exception as e:
                logger.debug("SDS listener error: %s", e)

    # ------ Lifecycle ------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("ServerDataService started")

    async def auto_subscribe_servers(self) -> None:
        """Subscribe to core data types for all configured servers.

        Called at startup so the cache is warm before any client connects.
        Subscribes to: PORTFOLIO, EXECUTORS, BOTS_STATUS, CONNECTORS,
        CANDLE_CONNECTORS, POSITIONS, ACTIVE_ORDERS, SERVER_STATUS,
        and TRADING_RULES (per connector) for every server.

        All initial fetches run concurrently via asyncio.gather.
        """
        from config_manager import get_config_manager

        cm = get_config_manager()
        servers = cm.list_servers()  # Dict[str, dict]
        if not servers:
            logger.info("SDS auto-subscribe: no servers configured")
            return

        core_types = [
            ServerDataType.PORTFOLIO,
            ServerDataType.EXECUTORS,
            ServerDataType.BOTS_STATUS,
            ServerDataType.CONNECTORS,
            ServerDataType.CANDLE_CONNECTORS,
            ServerDataType.POSITIONS,
            ServerDataType.ACTIVE_ORDERS,
            ServerDataType.SERVER_STATUS,
            ServerDataType.ALL_CONNECTORS,
        ]
        subscriber_id = "_auto"

        # Launch all core subscriptions concurrently
        async def _sub(name: str, dt: ServerDataType) -> bool:
            try:
                await self.subscribe(server=name, data_type=dt, subscriber_id=subscriber_id)
                return True
            except Exception as e:
                logger.debug("SDS auto-subscribe failed for %s/%s: %s", name, dt.value, e)
                return False

        tasks = [_sub(name, dt) for name in servers for dt in core_types]
        results = await asyncio.gather(*tasks)
        count = sum(1 for r in results if r)

        # Pre-subscribe trading rules concurrently across servers
        tr_tasks = [self._subscribe_trading_rules_for_server(name, subscriber_id) for name in servers]
        tr_results = await asyncio.gather(*tr_tasks, return_exceptions=True)
        for r in tr_results:
            if isinstance(r, int):
                count += r

        # Register on_change callback for CONNECTORS so that when a server
        # comes online later and CONNECTORS data is fetched for the first time
        # (or new connectors appear), we auto-subscribe TRADING_RULES.
        for name in servers:
            key = CacheKey.make(name, ServerDataType.CONNECTORS)
            subs = self._subscriptions.get(key, {})
            if subscriber_id in subs:
                subs[subscriber_id].callback = self._make_connectors_change_callback(name)

        logger.info("SDS auto-subscribe: %d subscriptions for %d servers", count, len(servers))

    async def _subscribe_trading_rules_for_server(
        self, server_name: str, subscriber_id: str = "_auto"
    ) -> int:
        """Subscribe TRADING_RULES for all known connectors on a server. Returns count."""
        connectors = self.get(server_name, ServerDataType.CONNECTORS)
        if not connectors:
            return 0
        count = 0
        for connector in connectors:
            key = CacheKey.make(
                server_name, ServerDataType.TRADING_RULES,
                connector_name=connector,
            )
            if key in self._subscriptions:
                continue  # Already subscribed
            try:
                await self.subscribe(
                    server=server_name,
                    data_type=ServerDataType.TRADING_RULES,
                    subscriber_id=subscriber_id,
                    connector_name=connector,
                )
                # Check if the initial fetch failed (connector unavailable/404)
                entry = self._cache.get(key)
                if entry and entry.value is None and entry.consecutive_errors > 0:
                    self.unsubscribe(key, subscriber_id)
                    self._cache.pop(key, None)  # Clean up error-only entry
                    logger.info(
                        "SDS: skipping trading rules for %s/%s (connector unavailable)",
                        server_name, connector,
                    )
                    continue
                count += 1
            except Exception as e:
                logger.debug(
                    "SDS auto-subscribe failed for %s/trading_rules/%s: %s",
                    server_name, connector, e,
                )
        return count

    def _make_connectors_change_callback(self, server_name: str) -> Callable:
        """Create a callback that auto-subscribes TRADING_RULES when CONNECTORS change."""
        async def _on_connectors_change(key: CacheKey, old_value, new_value):
            if not new_value:
                return
            added = await self._subscribe_trading_rules_for_server(server_name)
            if added:
                logger.info(
                    "SDS: auto-subscribed %d new TRADING_RULES for %s after CONNECTORS update",
                    added, server_name,
                )
        return _on_connectors_change

    def stop(self) -> None:
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            logger.info("ServerDataService stopped")

    # ------ Poll loop ------

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(_POLL_TICK)
                await self._poll_tick()

                # Periodic cleanup
                now = time.time()
                if now - self._last_cleanup > _CLEANUP_INTERVAL:
                    self._cleanup_stale()
                    self._last_cleanup = now
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("SDS poll loop error: %s", e, exc_info=True)
                await asyncio.sleep(5)

    async def _poll_tick(self) -> None:
        """Single tick: check each subscribed key and refresh if due.

        Collects all due keys first, then fetches them concurrently via gather.
        """
        now = time.time()
        due_keys: List[CacheKey] = []

        for key, subs in list(self._subscriptions.items()):
            if not subs:
                continue

            # Effective interval = min of all subscriber intervals
            effective_interval = min(s.interval for s in subs.values())

            entry = self._cache.get(key)
            if entry:
                age = now - entry.fetched_at
                if age < effective_interval:
                    continue

                # Exponential backoff on consecutive errors
                if entry.consecutive_errors >= 3:
                    backoff = min(60, 2 ** entry.consecutive_errors)
                    if now - entry.last_error_at < backoff:
                        continue

            due_keys.append(key)

        if not due_keys:
            return

        # Acquire rate limits and fetch concurrently
        async def _rate_limited_fetch(key: CacheKey):
            limiter = self._get_rate_limiter(key.server)
            if not await limiter.acquire(timeout=0.5):
                return
            try:
                await self._fetch_and_cache(key)
            except Exception:
                pass  # Error already recorded in _fetch_and_cache

        await asyncio.gather(*[_rate_limited_fetch(k) for k in due_keys])

    async def _fetch_and_cache(self, key: CacheKey) -> Optional[Any]:
        """Fetch data and update cache. Returns the fetched value."""
        spec = self._fetch_registry.get(key.data_type)
        if not spec:
            logger.debug("SDS: no fetch registered for %s", key.data_type.value)
            return None

        health = self.get_server_health(key.server)
        t0 = time.monotonic()

        try:
            client = await self._get_client(key.server)
            result = await spec.fetch_func(client, **key.params_dict)
        except Exception as e:
            logger.debug("SDS fetch failed %s:%s: %s", key.server, key.data_type.value, e)
            health.record_error()

            # Record error on cache entry
            entry = self._cache.get(key)
            if entry:
                entry.consecutive_errors += 1
                entry.last_error_at = time.time()
                return entry.value  # Return stale
            else:
                # Create error-only entry
                self._cache[key] = CacheEntry(
                    key=key, value=None, fetched_at=0.0,
                    consecutive_errors=1, last_error_at=time.time(),
                )
            return None

        latency_ms = (time.monotonic() - t0) * 1000
        health.record_success(latency_ms)

        old_entry = self._cache.get(key)
        old_value = old_entry.value if old_entry else None

        self._cache[key] = CacheEntry(
            key=key,
            value=result,
            fetched_at=time.time(),
        )

        self._notify_listeners(key, result)

        # Fire change callbacks only on diff
        if result != old_value:
            self._fire_callbacks(key, old_value, result)

        return result

    def _fire_callbacks(self, key: CacheKey, old_value: Any, new_value: Any) -> None:
        """Fire subscriber callbacks asynchronously."""
        subs = self._subscriptions.get(key)
        if not subs:
            return
        for sub in subs.values():
            if sub.callback:
                try:
                    asyncio.ensure_future(sub.callback(key, old_value, new_value))
                except Exception as e:
                    logger.debug("SDS callback error for %s: %s", sub.subscriber_id, e)

    def _cleanup_stale(self) -> None:
        """Remove cache entries with no subscribers and expired TTL."""
        now = time.time()
        stale = []
        for key, entry in self._cache.items():
            if key in self._subscriptions and self._subscriptions[key]:
                continue  # Active subscribers, keep
            defaults = _DEFAULTS.get(key.data_type)
            if defaults and now - entry.fetched_at > defaults.ttl * 2:
                stale.append(key)
        for key in stale:
            del self._cache[key]
        if stale:
            logger.debug("SDS cleaned up %d stale entries", len(stale))

    # ------ Stats ------

    def get_stats(self) -> dict:
        """Return service stats for debugging."""
        return {
            "cached_entries": len(self._cache),
            "active_subscriptions": sum(len(s) for s in self._subscriptions.values()),
            "subscribed_keys": len(self._subscriptions),
            "servers_tracked": len(self._health),
            "health": {
                name: {
                    "status": h.status.value,
                    "consecutive_failures": h.consecutive_failures,
                    "last_latency_ms": round(h.last_latency_ms, 1),
                }
                for name, h in self._health.items()
            },
        }


# ============================================
# SINGLETON
# ============================================

_instance: Optional[ServerDataService] = None


def get_server_data_service() -> ServerDataService:
    global _instance
    if _instance is None:
        _instance = ServerDataService()
        register_default_fetches()
    return _instance


# ============================================
# FETCH REGISTRATIONS
# ============================================


def register_default_fetches() -> None:
    """Register the default fetch functions for all data types.

    All fetch functions live in condor.fetchers — no handler imports.
    """
    from condor.fetchers import (
        fetch_portfolio,
        fetch_current_price as _fetch_price,
        fetch_positions,
        fetch_active_orders,
        fetch_trading_rules,
        fetch_connectors,
        fetch_available_cex_connectors,
        fetch_executors,
        fetch_bots_status,
        fetch_bot_runs,
        fetch_candle_connectors,
        fetch_server_status,
    )

    sds = get_server_data_service()

    sds.register_fetch(ServerDataType.PORTFOLIO, fetch_portfolio)

    # Prices needs a thin wrapper to match the (client, **params) signature
    async def _fetch_prices(client, connector_name: str = "", trading_pair: str = "", **_kw):
        return await _fetch_price(client, connector_name, trading_pair)
    sds.register_fetch(ServerDataType.PRICES, _fetch_prices)

    sds.register_fetch(ServerDataType.POSITIONS, fetch_positions)
    sds.register_fetch(ServerDataType.ACTIVE_ORDERS, fetch_active_orders)
    sds.register_fetch(ServerDataType.TRADING_RULES, fetch_trading_rules)
    sds.register_fetch(ServerDataType.CONNECTORS, fetch_available_cex_connectors)
    sds.register_fetch(ServerDataType.ALL_CONNECTORS, fetch_connectors)
    sds.register_fetch(ServerDataType.BOTS_STATUS, fetch_bots_status)
    sds.register_fetch(ServerDataType.EXECUTORS, fetch_executors)
    sds.register_fetch(ServerDataType.BOT_RUNS, fetch_bot_runs)
    sds.register_fetch(ServerDataType.CANDLE_CONNECTORS, fetch_candle_connectors)
    sds.register_fetch(ServerDataType.SERVER_STATUS, fetch_server_status)

    logger.info("ServerDataService: registered fetch functions for %d data types", len(sds._fetch_registry))
