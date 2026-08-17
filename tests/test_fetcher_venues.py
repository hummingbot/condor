"""Tests for FEAT-043: ``fetch_venues`` reports independent venue traits.

The trade panel used to learn one fact — "this connector is a gateway network" —
and use it to answer four different questions. This fetcher replaces that with two
independent traits, each with a single authority, and the property that matters
most is the one the binary could not express:

``xrpl`` is a first-class Hummingbot connector with a real order book whose
*charts* come from GeckoTerminal, so it lives in ``NETWORK_TO_GECKO`` — one of the
two sets the gateway half is built from. If a Gateway version ever reports ``xrpl``
in ``list_networks()``, it must still keep ``hummingbot_market_data: true`` (order
book, trading rules, LIMIT strategies) and must still be denied ``clmm_lp`` (its
AMM is not concentrated-liquidity). No reachable Gateway reports it today, which is
exactly why it is pinned here rather than clicked.

The gateway half also still has to normalize ``list_networks`` entries (plain
strings and ``network_id`` / ``id`` dicts across versions) and drop any network
Condor cannot chart, or the user picks one and gets an empty chart.
"""

import asyncio

import pytest

from condor.fetchers.connectors import fetch_venues
from condor.pool_data import network_has_clmm


class FakeClient:
    """Client replaying one scripted ``list_networks`` + credential answer."""

    def __init__(
        self,
        networks=None,
        networks_error=None,
        credentials=None,
        credentials_error=None,
        available=None,
    ):
        self.gateway_calls = 0
        self.gateway = self._Gateway(self)
        self.accounts = self._Accounts(self)
        self.connectors = self._Connectors(self)
        self._networks = networks
        self._networks_error = networks_error
        self._credentials = credentials if credentials is not None else []
        self._credentials_error = credentials_error
        # None means "every credentialed connector is available", which is what the
        # intersection in fetch_available_cex_connectors degrades to.
        self._available = available

    class _Gateway:
        def __init__(self, outer):
            self._outer = outer

        async def list_networks(self):
            self._outer.gateway_calls += 1
            if self._outer._networks_error is not None:
                raise self._outer._networks_error
            return self._outer._networks

    class _Accounts:
        def __init__(self, outer):
            self._outer = outer

        async def list_account_credentials(self, account_name):
            if self._outer._credentials_error is not None:
                raise self._outer._credentials_error
            return list(self._outer._credentials)

    class _Connectors:
        def __init__(self, outer):
            self._outer = outer

        async def list_connectors(self):
            if self._outer._available is None:
                raise RuntimeError("connector list unavailable")
            return list(self._outer._available)


def _fetch(**kwargs):
    strict = kwargs.pop("strict", False)
    return asyncio.run(fetch_venues(FakeClient(**kwargs), strict=strict))


def _by_name(venues):
    return {v["name"]: v for v in venues}


# ── network_has_clmm: the clmm_lp authority ───────────────────────────────────


def test_network_has_clmm_is_true_for_chains_hosting_a_clmm_venue():
    assert network_has_clmm("solana") is True
    assert network_has_clmm("solana-mainnet-beta") is True
    assert network_has_clmm("ethereum") is True
    assert network_has_clmm("ethereum-mainnet") is True
    assert network_has_clmm("base") is True
    assert network_has_clmm("base-mainnet") is True
    assert network_has_clmm("binance-smart-chain") is True


def test_network_has_clmm_is_false_for_xrpl():
    """The whole point: xrpl charts like a DEX but has no CLMM position model."""
    assert network_has_clmm("xrpl") is False


def test_network_has_clmm_is_false_for_a_chain_with_no_known_clmm_venue():
    assert network_has_clmm("avalanche") is False


def test_network_has_clmm_is_false_for_an_unknown_network():
    assert network_has_clmm("not-a-chain") is False
    assert network_has_clmm("") is False


# ── The xrpl regression this feature exists for ───────────────────────────────


def test_xrpl_in_both_lists_keeps_its_hummingbot_market_data():
    """A venue in both input lists is a Hummingbot connector, not a DEX.

    If Gateway starts reporting ``xrpl``, the panel must not strip its order book,
    its trading rules and its LIMIT strategies, and must not offer it an LP tab.
    """
    venues = _by_name(
        _fetch(
            credentials=["binance", "xrpl"],
            available=["binance", "xrpl"],
            networks={"networks": ["xrpl", "solana-mainnet-beta"]},
        )
    )
    assert venues["xrpl"] == {
        "name": "xrpl",
        "hummingbot_market_data": True,
        "clmm_lp": False,
    }
    # And the real gateway network in the same answer is unaffected.
    assert venues["solana-mainnet-beta"] == {
        "name": "solana-mainnet-beta",
        "hummingbot_market_data": False,
        "clmm_lp": True,
    }


def test_xrpl_credentialed_and_absent_from_gateway_is_a_plain_connector():
    """Today's world: the traits must be identical to the both-lists case."""
    venues = _by_name(
        _fetch(
            credentials=["xrpl"],
            available=["xrpl"],
            networks={"networks": ["solana-mainnet-beta"]},
        )
    )
    assert venues["xrpl"]["hummingbot_market_data"] is True
    assert venues["xrpl"]["clmm_lp"] is False


# ── Trait composition ─────────────────────────────────────────────────────────


def test_a_credentialed_connector_has_market_data_and_no_lp():
    venues = _by_name(
        _fetch(
            credentials=["binance", "binance_perpetual"],
            available=["binance", "binance_perpetual"],
            networks={"networks": []},
        )
    )
    assert venues["binance"] == {
        "name": "binance",
        "hummingbot_market_data": True,
        "clmm_lp": False,
    }
    assert venues["binance_perpetual"]["hummingbot_market_data"] is True


def test_a_gateway_network_has_lp_and_no_market_data():
    venues = _by_name(_fetch(networks={"networks": ["solana-mainnet-beta"]}))
    assert venues["solana-mainnet-beta"] == {
        "name": "solana-mainnet-beta",
        "hummingbot_market_data": False,
        "clmm_lp": True,
    }


def test_a_gateway_network_with_no_clmm_venue_gets_no_lp():
    """``avalanche`` is chartable but hosts none of the CLMM venues."""
    venues = _by_name(_fetch(networks={"networks": ["avalanche"]}))
    assert venues["avalanche"] == {
        "name": "avalanche",
        "hummingbot_market_data": False,
        "clmm_lp": False,
    }


def test_venues_are_sorted_and_deduplicated():
    venues = _fetch(
        credentials=["kucoin", "binance"],
        available=["kucoin", "binance"],
        networks={"networks": ["solana-mainnet-beta", {"id": "base-mainnet"}]},
    )
    assert [v["name"] for v in venues] == [
        "base-mainnet",
        "binance",
        "kucoin",
        "solana-mainnet-beta",
    ]


# ── The gateway half still normalizes and gates ───────────────────────────────


def test_accepts_plain_strings_and_network_id_and_id_dicts():
    names = [
        v["name"]
        for v in _fetch(
            networks={
                "networks": [
                    "solana-mainnet-beta",
                    {"network_id": "base-mainnet"},
                    {"id": "polygon-mainnet"},
                ]
            }
        )
    ]
    assert names == ["base-mainnet", "polygon-mainnet", "solana-mainnet-beta"]


def test_drops_networks_condor_cannot_chart():
    """A network absent from NETWORK_TO_GECKO would 502 on the candle path."""
    names = [
        v["name"]
        for v in _fetch(
            networks={
                "networks": [
                    "solana-mainnet-beta",
                    "solana-devnet",
                    "base-sepolia",
                    {"network_id": "not-a-chain"},
                ]
            }
        )
    ]
    assert names == ["solana-mainnet-beta"]


def test_tolerates_missing_and_empty_gateway_payloads():
    assert _fetch(networks={}) == []
    assert _fetch(networks={"networks": None}) == []
    assert _fetch(networks=None) == []


# ── strict: what a failure is allowed to take down ────────────────────────────


def test_a_gateway_failure_still_yields_the_credentialed_venues():
    """Losing the DEX half must not empty the whole dropdown."""
    venues = _by_name(
        _fetch(
            credentials=["binance"],
            available=["binance"],
            networks_error=RuntimeError("gateway down"),
            strict=True,
        )
    )
    assert venues["binance"]["hummingbot_market_data"] is True
    assert len(venues) == 1


def test_strict_reraises_a_gateway_failure_that_would_leave_nothing():
    """An empty list must never be cached as "this server has no venues"."""
    client = FakeClient(
        credentials=[], available=[], networks_error=RuntimeError("gateway down")
    )
    with pytest.raises(RuntimeError):
        asyncio.run(fetch_venues(client, strict=True))


def test_non_strict_swallows_a_total_failure():
    client = FakeClient(
        credentials=[], available=[], networks_error=RuntimeError("gateway down")
    )
    assert asyncio.run(fetch_venues(client)) == []


def test_strict_reraises_a_credentials_failure():
    """Credentials are load-bearing: without them there is no venue at all."""
    client = FakeClient(
        credentials_error=RuntimeError("server down"),
        networks={"networks": ["solana-mainnet-beta"]},
    )
    with pytest.raises(RuntimeError):
        asyncio.run(fetch_venues(client, strict=True))


def test_non_strict_credentials_failure_degrades_to_the_gateway_half():
    venues = _by_name(
        asyncio.run(
            fetch_venues(
                FakeClient(
                    credentials_error=RuntimeError("server down"),
                    networks={"networks": ["solana-mainnet-beta"]},
                )
            )
        )
    )
    assert venues["solana-mainnet-beta"]["clmm_lp"] is True


def test_rejects_an_unsafe_account_name():
    from condor.fetchers._identifiers import IdentifierError

    with pytest.raises(IdentifierError):
        asyncio.run(fetch_venues(FakeClient(), account_name="../etc"))


# ── The REST route ────────────────────────────────────────────────────────────
#
# The panel's connector dropdown is this endpoint's answer, so a server that
# cannot be described must degrade to an empty list rather than a 502.


class _FakeSds:
    """Stands in for ServerDataService.get_or_fetch."""

    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = 0

    async def get_or_fetch(self, name, data_type, **params):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.value


def _call_route(monkeypatch, sds):
    from condor import server_data_service
    from condor.web.models import WebUser
    from condor.web.routes.market import get_venues

    monkeypatch.setattr(
        server_data_service, "get_server_data_service", lambda: sds, raising=True
    )

    class _Cm:
        def has_server_access(self, user_id, name):
            return True

    monkeypatch.setattr(
        "condor.web.routes.market.get_config_manager", lambda: _Cm(), raising=True
    )
    return asyncio.run(get_venues("srv", user=WebUser(id=1, role="user")))


def test_route_returns_the_venues(monkeypatch):
    payload = [
        {"name": "binance", "hummingbot_market_data": True, "clmm_lp": False},
        {
            "name": "solana-mainnet-beta",
            "hummingbot_market_data": False,
            "clmm_lp": True,
        },
    ]
    assert _call_route(monkeypatch, _FakeSds(value=payload)) == {"venues": payload}


def test_route_returns_an_empty_list_when_the_server_is_undescribable(monkeypatch):
    """Not a 500: the panel renders its empty state."""
    sds = _FakeSds(error=RuntimeError("server down"))
    assert _call_route(monkeypatch, sds) == {"venues": []}


def test_route_normalizes_a_none_answer(monkeypatch):
    assert _call_route(monkeypatch, _FakeSds(value=None)) == {"venues": []}


def test_route_rejects_a_caller_without_access(monkeypatch):
    """The guard is the ``require_server_access`` dependency (SEC-147).

    It runs before the endpoint body, so the refusal is asserted on the
    dependency itself; that the route actually carries it is pinned
    structurally by ``tests/test_web_server_access_dependency.py``.
    """
    from fastapi import HTTPException

    from condor.web.auth import require_server_access
    from condor.web.models import WebUser

    class _Cm:
        def has_server_access(self, user_id, name):
            return False

    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: _Cm(), raising=True
    )
    with pytest.raises(HTTPException) as e:
        asyncio.run(require_server_access("srv", user=WebUser(id=1, role="user")))
    assert e.value.status_code == 403
