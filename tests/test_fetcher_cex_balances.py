"""Tests for ARCH-121: fetch_cex_balances delegates connector resolution.

``fetch_cex_balances`` used to re-implement ``fetch_available_cex_connectors``
verbatim. These tests pin the behaviour that must survive the delegation: only
CEX connectors that the server actually exposes reach ``get_state``, and an
empty resolution short-circuits before any portfolio call.
"""

import asyncio

from condor.fetchers.portfolio import fetch_cex_balances


class FakeClient:
    """Minimal client recording the calls each fetcher makes."""

    def __init__(self, configured, available):
        self.calls = []
        self._configured = configured
        self._available = available
        self.connectors = self._Connectors(self)
        self.accounts = self._Accounts(self)
        self.portfolio = self._Portfolio(self)

    class _Connectors:
        def __init__(self, outer):
            self._outer = outer

        async def list_connectors(self):
            self._outer.calls.append(("list_connectors", None))
            if isinstance(self._outer._available, Exception):
                raise self._outer._available
            return list(self._outer._available)

    class _Accounts:
        def __init__(self, outer):
            self._outer = outer

        async def list_account_credentials(self, account_name):
            self._outer.calls.append(("list_account_credentials", account_name))
            if isinstance(self._outer._configured, Exception):
                raise self._outer._configured
            return list(self._outer._configured)

    class _Portfolio:
        def __init__(self, outer):
            self._outer = outer

        async def get_state(self, **kwargs):
            # The real endpoint only reports the connectors it was asked for.
            self._outer.calls.append(("get_state", kwargs))
            return {
                kwargs["account_names"][0]: {
                    name: [{"token": "USDT", "units": 10}]
                    for name in kwargs["connector_names"]
                }
            }

    def get_state_kwargs(self):
        return [kw for name, kw in self.calls if name == "get_state"]


def test_only_cex_credentials_reach_get_state():
    """A DEX credential is filtered out before the portfolio call."""
    client = FakeClient(
        configured=["binance", "solana"],
        available=["binance", "solana", "kucoin"],
    )

    balances = asyncio.run(fetch_cex_balances(client, account_name="master_account"))

    assert client.get_state_kwargs() == [
        {
            "account_names": ["master_account"],
            "connector_names": ["binance"],
            "refresh": False,
        }
    ]
    assert balances == {"binance": [{"token": "USDT", "units": 10}]}


def test_connector_not_available_on_server_is_dropped():
    """A configured CEX the server does not expose never reaches get_state."""
    client = FakeClient(configured=["binance", "kucoin"], available=["binance"])

    asyncio.run(fetch_cex_balances(client, account_name="master_account"))

    assert client.get_state_kwargs()[0]["connector_names"] == ["binance"]


def test_no_cex_connectors_returns_empty_without_get_state():
    """An all-DEX account short-circuits: {} and no portfolio call."""
    client = FakeClient(configured=["solana", "ethereum"], available=["solana"])

    balances = asyncio.run(fetch_cex_balances(client, account_name="master_account"))

    assert balances == {}
    assert client.get_state_kwargs() == []


def test_credential_lookup_failure_returns_empty_without_get_state():
    """The delegate swallows its own errors into [], so balances stay {}."""
    client = FakeClient(configured=RuntimeError("boom"), available=["binance"])

    balances = asyncio.run(fetch_cex_balances(client, account_name="master_account"))

    assert balances == {}
    assert client.get_state_kwargs() == []


def test_unavailable_connector_list_does_not_block_resolution():
    """If list_connectors fails, the configured CEX set is used unfiltered."""
    client = FakeClient(
        configured=["binance", "solana"], available=RuntimeError("down")
    )

    asyncio.run(fetch_cex_balances(client, account_name="master_account"))

    assert client.get_state_kwargs()[0]["connector_names"] == ["binance"]


def test_refresh_is_forwarded():
    """The refresh flag still reaches get_state."""
    client = FakeClient(configured=["binance"], available=["binance"])

    asyncio.run(fetch_cex_balances(client, account_name="master_account", refresh=True))

    assert client.get_state_kwargs()[0]["refresh"] is True
