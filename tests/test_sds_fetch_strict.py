"""Tests for CORR-108: SDS-registered read fetchers must not cache an outage.

The four fetchers the SDS registers for POSITIONS, ACTIVE_ORDERS, TRADING_RULES
and CONNECTORS used to swallow every API error into an empty container, which
``_do_fetch_and_cache`` then stored as a perfectly good value: the last known
positions were evicted, the server stayed green and no backoff engaged.

They now take ``strict`` (default ``False``, so every direct caller keeps
today's behaviour) and the SDS binds ``strict=True`` at registration, routing a
failure into the except branch that keeps the stale value and records the error.
"""

import asyncio
from functools import partial

from condor.fetchers.connectors import fetch_available_cex_connectors
from condor.fetchers.orders import fetch_active_orders
from condor.fetchers.positions import fetch_positions
from condor.fetchers.trading_rules import fetch_trading_rules
from condor.server_data_service import (
    CacheKey,
    ServerDataService,
    ServerDataType,
    register_default_fetches,
)

POSITION = {"connector_name": "binance_perpetual", "trading_pair": "BTC-USDT"}


class _Trading:
    """Fake ``client.trading`` that serves one page, then fails."""

    def __init__(self, failing):
        self._failing = failing

    async def get_positions(self, **kwargs):
        if self._failing["on"]:
            raise RuntimeError("502 Bad Gateway")
        return {"data": [POSITION]}

    async def get_active_orders(self, **kwargs):
        if self._failing["on"]:
            raise RuntimeError("502 Bad Gateway")
        return {"data": [{"client_order_id": "abc"}]}


class _Connectors:
    def __init__(self, failing):
        self._failing = failing

    async def get_trading_rules(self, connector_name=""):
        if self._failing["on"]:
            raise RuntimeError("404 Client Error: Not Found")
        return {"BTC-USDT": {"min_order_size": 1}}

    async def list_connectors(self):
        return ["binance", "binance_perpetual"]


class _Accounts:
    def __init__(self, failing):
        self._failing = failing

    async def list_account_credentials(self, account_name):
        if self._failing["on"]:
            raise RuntimeError("502 Bad Gateway")
        return ["binance"]


class _FakeClient:
    def __init__(self, failing):
        self.trading = _Trading(failing)
        self.connectors = _Connectors(failing)
        self.accounts = _Accounts(failing)


def _make_sds(data_type, fetch_func, failing):
    """Fresh (non-singleton) SDS wired to a fake client."""
    sds = ServerDataService()
    client = _FakeClient(failing)

    async def _fake_get_client(server_name):
        return client

    sds._get_client = _fake_get_client
    sds.register_fetch(data_type, fetch_func)
    return sds


# --------------------------------------------------------------------------
# Default behaviour is untouched for direct callers
# --------------------------------------------------------------------------


def test_fetchers_still_swallow_errors_by_default():
    """Without ``strict``, every fetcher returns today's empty sentinel."""
    failing = {"on": True}
    client = _FakeClient(failing)

    async def _drive():
        assert await fetch_positions(client) == []
        assert await fetch_active_orders(client) == []
        assert await fetch_trading_rules(client, connector_name="binance") == {}
        assert await fetch_available_cex_connectors(client) == []

    asyncio.run(_drive())


def test_fetchers_raise_under_strict():
    """With ``strict=True``, the API exception reaches the caller."""
    failing = {"on": True}
    client = _FakeClient(failing)

    async def _drive():
        for call in (
            lambda: fetch_positions(client, strict=True),
            lambda: fetch_active_orders(client, strict=True),
            lambda: fetch_trading_rules(client, connector_name="b", strict=True),
            lambda: fetch_available_cex_connectors(client, strict=True),
        ):
            try:
                await call()
            except RuntimeError:
                continue
            raise AssertionError("strict fetcher swallowed the error")

    asyncio.run(_drive())


# --------------------------------------------------------------------------
# The SDS keeps the last good value instead of caching the outage
# --------------------------------------------------------------------------


def test_failed_positions_fetch_keeps_the_last_known_positions():
    """A transient error must not turn a cached open position into "flat"."""
    failing = {"on": False}
    sds = _make_sds(
        ServerDataType.POSITIONS, partial(fetch_positions, strict=True), failing
    )
    key = CacheKey.make("srv", ServerDataType.POSITIONS)

    async def _drive():
        assert await sds.get_or_fetch("srv", ServerDataType.POSITIONS) == [POSITION]

        failing["on"] = True
        await sds._fetch_and_cache(key)

        # The cache still reports the position, and the failure was recorded.
        assert sds.get("srv", ServerDataType.POSITIONS) == [POSITION]
        assert sds._cache[key].consecutive_errors == 1
        assert sds.get_server_health("srv").consecutive_failures == 1

    asyncio.run(_drive())


def test_without_strict_the_outage_would_have_evicted_the_positions():
    """Guards the regression: the plain fetcher caches [] over a live position."""
    failing = {"on": False}
    sds = _make_sds(ServerDataType.POSITIONS, fetch_positions, failing)
    key = CacheKey.make("srv", ServerDataType.POSITIONS)

    async def _drive():
        assert await sds.get_or_fetch("srv", ServerDataType.POSITIONS) == [POSITION]
        failing["on"] = True
        await sds._fetch_and_cache(key)
        assert sds.get("srv", ServerDataType.POSITIONS) == []
        assert sds._cache[key].consecutive_errors == 0

    asyncio.run(_drive())


def test_failed_active_orders_fetch_keeps_the_last_known_orders():
    failing = {"on": False}
    sds = _make_sds(
        ServerDataType.ACTIVE_ORDERS, partial(fetch_active_orders, strict=True), failing
    )
    key = CacheKey.make("srv", ServerDataType.ACTIVE_ORDERS)

    async def _drive():
        first = await sds.get_or_fetch("srv", ServerDataType.ACTIVE_ORDERS)
        assert first == [{"client_order_id": "abc"}]

        failing["on"] = True
        await sds._fetch_and_cache(key)

        assert sds.get("srv", ServerDataType.ACTIVE_ORDERS) == first
        assert sds._cache[key].consecutive_errors == 1

    asyncio.run(_drive())


# --------------------------------------------------------------------------
# A connector whose trading-rules endpoint 404s is dropped, not polled forever
# --------------------------------------------------------------------------


def test_trading_rules_404_leaves_an_error_only_entry():
    """``entry.value is None`` + ``consecutive_errors > 0`` — the shape the
    connector-unavailable unsubscribe branch tests for."""
    failing = {"on": True}
    sds = _make_sds(
        ServerDataType.TRADING_RULES, partial(fetch_trading_rules, strict=True), failing
    )
    key = CacheKey.make("srv", ServerDataType.TRADING_RULES, connector_name="binance")

    async def _drive():
        await sds._fetch_and_cache(key)
        entry = sds._cache[key]
        assert entry.value is None
        assert entry.consecutive_errors > 0

    asyncio.run(_drive())


def test_unavailable_connector_is_unsubscribed_from_trading_rules():
    """The guard at _subscribe_trading_rules_for_server can finally fire."""
    failing = {"on": True}
    sds = _make_sds(
        ServerDataType.TRADING_RULES, partial(fetch_trading_rules, strict=True), failing
    )
    sds.put("srv", ServerDataType.CONNECTORS, ["binance"])
    key = CacheKey.make("srv", ServerDataType.TRADING_RULES, connector_name="binance")

    async def _drive():
        added = await sds._subscribe_trading_rules_for_server("srv")

        assert added == 0, "an unavailable connector must not count as subscribed"
        assert key not in sds._subscriptions
        assert key not in sds._cache, "the error-only entry must be cleaned up"

    asyncio.run(_drive())


def test_available_connector_stays_subscribed():
    """The unsubscribe branch does not fire on a healthy connector."""
    failing = {"on": False}
    sds = _make_sds(
        ServerDataType.TRADING_RULES, partial(fetch_trading_rules, strict=True), failing
    )
    sds.put("srv", ServerDataType.CONNECTORS, ["binance"])
    key = CacheKey.make("srv", ServerDataType.TRADING_RULES, connector_name="binance")

    async def _drive():
        added = await sds._subscribe_trading_rules_for_server("srv")

        assert added == 1
        assert key in sds._subscriptions
        assert sds._cache[key].value == {"BTC-USDT": {"min_order_size": 1}}

    asyncio.run(_drive())


# --------------------------------------------------------------------------
# The registrations actually bind strict
# --------------------------------------------------------------------------


def test_default_registrations_bind_strict_true():
    register_default_fetches()
    from condor.server_data_service import get_server_data_service

    registry = get_server_data_service()._fetch_registry
    for data_type in (
        ServerDataType.POSITIONS,
        ServerDataType.ACTIVE_ORDERS,
        ServerDataType.TRADING_RULES,
        ServerDataType.CONNECTORS,
    ):
        fetch_func = registry[data_type].fetch_func
        assert isinstance(fetch_func, partial), data_type
        assert fetch_func.keywords == {"strict": True}, data_type
