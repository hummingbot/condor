"""
DataManager — Legacy compatibility layer.

Delegates to ServerDataService (SDS) for all operations.
Kept for any unmigrated handlers that still import from here.

DEPRECATED: New code should use condor.server_data_service directly.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================
# DATA TYPES (kept for import compatibility)
# ============================================


@dataclass(frozen=True)
class DataTypeConfig:
    idle_ttl: float
    active_ttl: float
    bg_interval: float
    stale_threshold: float
    group: str


class DataType(Enum):
    """Legacy data type enum. Maps to ServerDataType internally."""

    CEX_BALANCES = DataTypeConfig(idle_ttl=120, active_ttl=10, bg_interval=15, stale_threshold=2, group="cex_balances")
    CEX_PRICES = DataTypeConfig(idle_ttl=60, active_ttl=3, bg_interval=5, stale_threshold=2, group="cex_prices")
    CEX_POSITIONS = DataTypeConfig(idle_ttl=120, active_ttl=10, bg_interval=15, stale_threshold=2, group="cex_positions")
    CEX_ACTIVE_ORDERS = DataTypeConfig(idle_ttl=60, active_ttl=10, bg_interval=15, stale_threshold=2, group="cex_orders")
    CEX_TRADING_RULES = DataTypeConfig(idle_ttl=600, active_ttl=300, bg_interval=0, stale_threshold=10, group="cex_rules")
    CEX_CONNECTORS = DataTypeConfig(idle_ttl=600, active_ttl=300, bg_interval=0, stale_threshold=10, group="cex_connectors")
    PORTFOLIO = DataTypeConfig(idle_ttl=60, active_ttl=10, bg_interval=10, stale_threshold=2, group="portfolio")
    BOTS_STATUS = DataTypeConfig(idle_ttl=30, active_ttl=5, bg_interval=5, stale_threshold=2, group="bots")
    EXECUTORS = DataTypeConfig(idle_ttl=30, active_ttl=5, bg_interval=5, stale_threshold=2, group="executors")

    @property
    def config(self) -> DataTypeConfig:
        return self.value


# Mapping from DataType to ServerDataType
def _to_sdt(data_type: DataType):
    from condor.server_data_service import ServerDataType
    _MAP = {
        DataType.CEX_BALANCES: ServerDataType.PORTFOLIO,
        DataType.CEX_PRICES: ServerDataType.PRICES,
        DataType.CEX_POSITIONS: ServerDataType.POSITIONS,
        DataType.CEX_ACTIVE_ORDERS: ServerDataType.ACTIVE_ORDERS,
        DataType.CEX_TRADING_RULES: ServerDataType.TRADING_RULES,
        DataType.CEX_CONNECTORS: ServerDataType.CONNECTORS,
        DataType.PORTFOLIO: ServerDataType.PORTFOLIO,
        DataType.BOTS_STATUS: ServerDataType.BOTS_STATUS,
        DataType.EXECUTORS: ServerDataType.EXECUTORS,
    }
    return _MAP.get(data_type)


# ============================================
# DATA MANAGER (thin wrapper around SDS)
# ============================================


class DataManager:
    """Legacy DataManager — delegates to ServerDataService."""

    def __init__(self):
        self._running = False

    def add_listener(self, callback: Callable) -> None:
        from condor.server_data_service import get_server_data_service
        get_server_data_service().add_listener(callback)

    def remove_listener(self, callback: Callable) -> None:
        from condor.server_data_service import get_server_data_service
        get_server_data_service().remove_listener(callback)

    def get_session(self, server_name: str) -> "_LegacySession":
        """Return a legacy session proxy that reads/writes via SDS."""
        return _LegacySession(server_name)

    def register_fetch(self, data_type: DataType, fetch_func: Callable, key_builder: Callable) -> None:
        """No-op — SDS has its own registrations."""
        pass

    async def get(self, server_name: str, data_type: DataType, **kwargs) -> Optional[Any]:
        from condor.server_data_service import get_server_data_service
        sdt = _to_sdt(data_type)
        if sdt is None:
            return None
        return await get_server_data_service().get_or_fetch(server_name, sdt, **kwargs)

    def invalidate(self, server_name: str, *groups: str) -> None:
        from condor.server_data_service import get_server_data_service
        get_server_data_service().invalidate_by_groups(server_name, *groups)

    def invalidate_server(self, server_name: str) -> None:
        from condor.server_data_service import get_server_data_service
        get_server_data_service().invalidate_server(server_name)

    def set_context(self, server_name: str, user_id: int, command: str,
                    connector: Optional[str] = None, trading_pair: Optional[str] = None,
                    account: Optional[str] = None) -> None:
        """Legacy context setting — subscribes to SDS instead."""
        from condor.server_data_service import ServerDataType, get_server_data_service
        sds = get_server_data_service()
        sub_id = f"dm_compat_{user_id}"
        if command == "dashboard":
            asyncio.ensure_future(sds.subscribe(server_name, ServerDataType.PORTFOLIO, sub_id))
            asyncio.ensure_future(sds.subscribe(server_name, ServerDataType.BOTS_STATUS, sub_id))
            asyncio.ensure_future(sds.subscribe(server_name, ServerDataType.EXECUTORS, sub_id))
        elif command == "trade":
            if connector:
                asyncio.ensure_future(sds.subscribe(server_name, ServerDataType.PRICES, sub_id,
                                                     connector_name=connector, trading_pair=trading_pair or ""))
                asyncio.ensure_future(sds.subscribe(server_name, ServerDataType.TRADING_RULES, sub_id,
                                                     connector_name=connector))
            asyncio.ensure_future(sds.subscribe(server_name, ServerDataType.ACTIVE_ORDERS, sub_id, limit="5"))

    def clear_context(self, server_name: str, user_id: int) -> None:
        from condor.server_data_service import get_server_data_service
        get_server_data_service().unsubscribe_all(f"dm_compat_{user_id}")

    def clear_all_user_contexts(self, user_id: int) -> None:
        from condor.server_data_service import get_server_data_service
        get_server_data_service().unsubscribe_all(f"dm_compat_{user_id}")

    def start(self) -> None:
        """No-op — SDS handles the poll loop."""
        if self._running:
            return
        self._running = True
        logger.info("DataManager (legacy wrapper) started — SDS handles polling")

    def stop(self) -> None:
        self._running = False
        logger.info("DataManager (legacy wrapper) stopped")


class _LegacySession:
    """Proxy that maps old dm_session.get()/put() calls to SDS."""

    def __init__(self, server_name: str):
        self.server_name = server_name

    def get(self, cache_key: str, data_type: DataType) -> Optional[Any]:
        from condor.server_data_service import get_server_data_service
        sdt = _to_sdt(data_type)
        if sdt is None:
            return None
        params = _parse_legacy_key(cache_key, data_type)
        return get_server_data_service().get(self.server_name, sdt, **params)

    def get_stale(self, cache_key: str) -> Optional[Any]:
        # SDS doesn't have a separate stale read, just return whatever is cached
        return None

    def put(self, cache_key: str, data_type: DataType, value: Any) -> None:
        from condor.server_data_service import get_server_data_service
        sdt = _to_sdt(data_type)
        if sdt is None:
            return
        params = _parse_legacy_key(cache_key, data_type)
        get_server_data_service().put(self.server_name, sdt, value, **params)


def _parse_legacy_key(cache_key: str, data_type: DataType) -> dict:
    """Parse old-style cache key strings into SDS params."""
    parts = cache_key.split(":")
    if data_type == DataType.CEX_BALANCES and len(parts) >= 2:
        return {"account_name": parts[1]}
    elif data_type == DataType.CEX_PRICES and len(parts) >= 3:
        return {"connector_name": parts[1], "trading_pair": parts[2]}
    elif data_type == DataType.CEX_POSITIONS and len(parts) >= 2:
        return {"connector_name": parts[1]}
    elif data_type == DataType.CEX_ACTIVE_ORDERS and len(parts) >= 2:
        return {"limit": parts[1]}
    elif data_type == DataType.CEX_TRADING_RULES and len(parts) >= 2:
        return {"connector_name": parts[1]}
    elif data_type == DataType.CEX_CONNECTORS and len(parts) >= 3:
        return {"account_name": parts[2]}
    return {}


# ============================================
# SINGLETON & CONVENIENCE API
# ============================================

_instance: Optional[DataManager] = None


def get_data_manager() -> DataManager:
    global _instance
    if _instance is None:
        _instance = DataManager()
    return _instance


async def dm_get(server_name: str, data_type: DataType, **kwargs) -> Optional[Any]:
    return await get_data_manager().get(server_name, data_type, **kwargs)


def dm_invalidate(server_name: str, *groups: str) -> None:
    get_data_manager().invalidate(server_name, *groups)


def dm_set_context(server_name: str, user_id: int, command: str,
                   connector: Optional[str] = None, trading_pair: Optional[str] = None,
                   account: Optional[str] = None) -> None:
    get_data_manager().set_context(server_name, user_id, command, connector, trading_pair, account)


def dm_clear_context(server_name: str, user_id: int) -> None:
    get_data_manager().clear_context(server_name, user_id)


def dm_clear_all_user_contexts(user_id: int) -> None:
    get_data_manager().clear_all_user_contexts(user_id)


def register_default_fetches() -> None:
    """No-op — SDS registers its own fetches."""
    logger.info("DataManager: register_default_fetches() is now a no-op (SDS handles registrations)")
