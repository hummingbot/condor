"""Tests for SEC-115: identifiers interpolated into Hummingbot API URL paths.

The API client builds URLs by raw f-string interpolation and yarl *parses* the
result, so ``connector="../accounts/master_account/credentials?"`` silently
becomes a different — real, authenticated — endpoint. These tests pin the two
properties that close that pivot: the fetcher refuses the value *before* it
reaches the client, and the web route refuses it before SDS mints a cache key.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.market as market_routes
from condor.fetchers._identifiers import IdentifierError
from condor.fetchers.connectors import fetch_available_cex_connectors
from condor.fetchers.portfolio import fetch_cex_balances
from condor.fetchers.trading_rules import fetch_trading_rules
from condor.server_data_service import ServerDataService, ServerDataType
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")

# Payloads verified against the repo's yarl to resolve to a *different* endpoint.
TRAVERSAL_PAYLOADS = [
    "../accounts/master_account/credentials?",
    "../bot-orchestration/mqtt?x=",
    "../../admin",
    "binance/../../accounts",
    "binance?x=1",
    "binance#frag",
    "",
    "has space",
]

LEGIT_NAMES = ["binance", "binance_perpetual", "kucoin_perpetual", "gate.io", "ok-x"]


class SpyClient:
    """Records whether any client method was ever awaited."""

    def __init__(self, connectors=None):
        self.calls = []
        self._connectors = connectors or ["binance", "binance_perpetual"]
        self.connectors = self._Connectors(self)
        self.accounts = self._Accounts(self)
        self.portfolio = self._Portfolio(self)

    class _Connectors:
        def __init__(self, outer):
            self._outer = outer

        async def get_trading_rules(self, connector_name=""):
            self._outer.calls.append(("get_trading_rules", connector_name))
            return {"BTC-USDT": {"min_order_size": 1}}

        async def list_connectors(self):
            self._outer.calls.append(("list_connectors", None))
            return list(self._outer._connectors)

    class _Accounts:
        def __init__(self, outer):
            self._outer = outer

        async def list_account_credentials(self, account_name):
            self._outer.calls.append(("list_account_credentials", account_name))
            return list(self._outer._connectors)

    class _Portfolio:
        def __init__(self, outer):
            self._outer = outer

        async def get_state(self, **kwargs):
            self._outer.calls.append(("get_state", kwargs))
            return {
                kwargs.get("account_names", ["master_account"])[0]: {
                    "binance": [{"a": 1}]
                }
            }


# ── The fetchers reject before touching the client ──


@pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
def test_trading_rules_rejects_before_calling_client(payload):
    """A traversal connector name raises, and get_trading_rules is never awaited."""
    client = SpyClient()

    with pytest.raises(IdentifierError):
        asyncio.run(fetch_trading_rules(client, connector_name=payload))

    assert client.calls == []


@pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
def test_cex_connectors_rejects_before_calling_client(payload):
    """A traversal account name raises out of fetch_available_cex_connectors."""
    client = SpyClient()

    with pytest.raises(IdentifierError):
        asyncio.run(fetch_available_cex_connectors(client, account_name=payload))

    assert client.calls == []


@pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
def test_cex_balances_rejects_before_calling_client(payload):
    """A traversal account name raises out of fetch_cex_balances."""
    client = SpyClient()

    with pytest.raises(IdentifierError):
        asyncio.run(fetch_cex_balances(client, account_name=payload))

    assert client.calls == []


def test_rejection_is_not_swallowed_by_the_broad_except():
    """The guard must run before the try/except that turns errors into {}."""
    client = SpyClient()

    # If validation moved inside the try, this would quietly return {} instead.
    with pytest.raises(IdentifierError):
        asyncio.run(fetch_trading_rules(client, connector_name="../admin"))


# ── Legitimate names still pass unchanged ──


@pytest.mark.parametrize("name", LEGIT_NAMES)
def test_legit_connector_names_still_fetch(name):
    """Real connector names reach the client untouched."""
    client = SpyClient()

    result = asyncio.run(fetch_trading_rules(client, connector_name=name))

    assert result == {"BTC-USDT": {"min_order_size": 1}}
    assert client.calls == [("get_trading_rules", name)]


def test_every_name_from_list_connectors_passes():
    """Anything the backend itself reports as a connector must validate."""
    live_names = [
        "binance",
        "binance_perpetual",
        "kucoin",
        "kucoin_perpetual",
        "gate_io",
        "okx_perpetual",
        "hyperliquid_perpetual",
        "bybit",
    ]
    client = SpyClient(connectors=live_names)

    for name in asyncio.run(client.connectors.list_connectors()):
        client.calls.clear()
        asyncio.run(fetch_trading_rules(client, connector_name=name))
        assert client.calls == [("get_trading_rules", name)]


def test_legit_account_names_still_fetch():
    """A normal account name flows through both account-scoped fetchers."""
    client = SpyClient()

    assert asyncio.run(
        fetch_available_cex_connectors(client, account_name="master_account")
    ) == ["binance", "binance_perpetual"]

    balances = asyncio.run(fetch_cex_balances(client, account_name="master_account"))
    assert balances == {"binance": [{"a": 1}]}


# ── The route errors out and leaves no SDS cache entry ──


class FakeConfigManager:
    def has_server_access(self, user_id, name, *a, **kw):
        return True


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(
        market_routes, "get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    app = FastAPI()
    app.include_router(market_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS[:6])
def test_route_rejects_traversal_and_caches_nothing(monkeypatch, payload):
    """The endpoint errors out, and SDS never mints a key for the payload."""
    sds = ServerDataService()
    fetched = []

    async def _spy_fetch(client, **params):
        fetched.append(params)
        return {}

    async def _fake_get_client(server_name):
        return SpyClient()

    sds._get_client = _fake_get_client
    sds.register_fetch(ServerDataType.TRADING_RULES, _spy_fetch)
    monkeypatch.setattr(
        "condor.server_data_service.get_server_data_service", lambda: sds
    )

    resp = _client(monkeypatch).get(
        "/servers/srv/market/trading-rules", params={"connector": payload}
    )

    assert resp.status_code >= 400, resp.text
    # No fetch was attempted and no cache entry (not even an error-only one).
    assert fetched == []
    assert sds._cache == {}


def test_route_still_serves_a_legit_connector(monkeypatch):
    """The guard does not break the normal path."""
    sds = ServerDataService()

    async def _fetch(client, **params):
        return {"BTC-USDT": {"min_order_size": 1, "min_notional_size": 10}}

    async def _fake_get_client(server_name):
        return SpyClient()

    sds._get_client = _fake_get_client
    sds.register_fetch(ServerDataType.TRADING_RULES, _fetch)
    monkeypatch.setattr(
        "condor.server_data_service.get_server_data_service", lambda: sds
    )

    resp = _client(monkeypatch).get(
        "/servers/srv/market/trading-rules", params={"connector": "binance"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connector"] == "binance"
    assert body["rules"][0]["trading_pair"] == "BTC-USDT"
