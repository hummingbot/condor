"""Tests for FEAT-041: ``fetch_gateway_networks`` normalizes and gates the list.

The trade panel learns "this connector is a DEX" from this fetcher's output, so
two properties matter. First, gateway has returned ``list_networks`` entries as
plain strings and as ``network_id`` / ``id`` dicts across versions, and all three
have to reduce to the same id. Second, the result is intersected with
``NETWORK_TO_GECKO``: a network Condor cannot chart must never be offered, or the
user picks it and gets an empty chart (``dex_candles.uses_gecko_candles`` is an
exact-match membership test on that same dict).
"""

import asyncio

import pytest

from condor.fetchers.connectors import fetch_gateway_networks


class FakeClient:
    """Client whose ``gateway.list_networks`` replays one scripted response."""

    def __init__(self, response=None, error=None):
        self.calls = 0
        self.gateway = self._Gateway(self)
        self._response = response
        self._error = error

    class _Gateway:
        def __init__(self, outer):
            self._outer = outer

        async def list_networks(self):
            self._outer.calls += 1
            if self._outer._error is not None:
                raise self._outer._error
            return self._outer._response


def _fetch(response, **kwargs):
    return asyncio.run(fetch_gateway_networks(FakeClient(response), **kwargs))


def test_accepts_plain_string_entries():
    result = _fetch({"networks": ["solana-mainnet-beta", "base-mainnet"]})
    assert result == ["base-mainnet", "solana-mainnet-beta"]


def test_accepts_network_id_dicts():
    result = _fetch({"networks": [{"network_id": "solana-mainnet-beta"}]})
    assert result == ["solana-mainnet-beta"]


def test_accepts_id_dicts():
    result = _fetch({"networks": [{"id": "ethereum-mainnet"}]})
    assert result == ["ethereum-mainnet"]


def test_mixed_shapes_normalize_to_the_same_ids():
    result = _fetch(
        {
            "networks": [
                "solana-mainnet-beta",
                {"network_id": "base-mainnet"},
                {"id": "polygon-mainnet"},
            ]
        }
    )
    assert result == ["base-mainnet", "polygon-mainnet", "solana-mainnet-beta"]


def test_drops_networks_condor_cannot_chart():
    """A network absent from NETWORK_TO_GECKO would 502 on the candle path."""
    result = _fetch(
        {
            "networks": [
                "solana-mainnet-beta",
                "solana-devnet",
                "base-sepolia",
                {"network_id": "not-a-chain"},
            ]
        }
    )
    assert result == ["solana-mainnet-beta"]


def test_deduplicates_and_sorts():
    result = _fetch(
        {"networks": ["base-mainnet", {"network_id": "base-mainnet"}, "solana"]}
    )
    assert result == ["base-mainnet", "solana"]


def test_tolerates_missing_and_empty_payloads():
    assert _fetch({}) == []
    assert _fetch({"networks": None}) == []
    assert _fetch(None) == []


def test_strict_reraises_so_the_failure_is_not_cached():
    """A cached empty list would tell the panel "this server has no DEX"."""
    client = FakeClient(error=RuntimeError("gateway down"))
    with pytest.raises(RuntimeError):
        asyncio.run(fetch_gateway_networks(client, strict=True))


def test_non_strict_swallows_the_failure():
    client = FakeClient(error=RuntimeError("gateway down"))
    assert asyncio.run(fetch_gateway_networks(client)) == []


# ── The REST route ────────────────────────────────────────────────────────────
#
# The panel merges this endpoint's answer into its connector dropdown, so a
# server whose gateway container is down must degrade to "no DEX offered" rather
# than break the whole selector with a 502.


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
    from condor.web.routes.market import get_gateway_networks

    monkeypatch.setattr(
        server_data_service, "get_server_data_service", lambda: sds, raising=True
    )

    class _Cm:
        def has_server_access(self, user_id, name):
            return True

    monkeypatch.setattr(
        "condor.web.routes.market.get_config_manager", lambda: _Cm(), raising=True
    )
    return asyncio.run(get_gateway_networks("srv", user=WebUser(id=1, role="user")))


def test_route_returns_the_networks(monkeypatch):
    sds = _FakeSds(value=["base-mainnet", "solana-mainnet-beta"])
    assert _call_route(monkeypatch, sds) == {
        "networks": ["base-mainnet", "solana-mainnet-beta"]
    }


def test_route_returns_empty_list_when_the_gateway_is_down(monkeypatch):
    """Not a 500: the panel just shows no DEX."""
    sds = _FakeSds(error=RuntimeError("gateway down"))
    assert _call_route(monkeypatch, sds) == {"networks": []}


def test_route_normalizes_a_none_answer(monkeypatch):
    assert _call_route(monkeypatch, _FakeSds(value=None)) == {"networks": []}


def test_route_rejects_a_caller_without_access(monkeypatch):
    from fastapi import HTTPException

    from condor.web.models import WebUser
    from condor.web.routes.market import get_gateway_networks

    class _Cm:
        def has_server_access(self, user_id, name):
            return False

    monkeypatch.setattr(
        "condor.web.routes.market.get_config_manager", lambda: _Cm(), raising=True
    )
    with pytest.raises(HTTPException) as e:
        asyncio.run(get_gateway_networks("srv", user=WebUser(id=1, role="user")))
    assert e.value.status_code == 403
