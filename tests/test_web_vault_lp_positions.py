"""``GET /vaults/{account}/lp-positions``: the liquidity a vault still has out.

A balance list does not show it. An LP strategy keeps most of its money in
pools, so a vault whose assets read near zero while three positions are open is
the ordinary case — and a page with only the first half says the opposite.

Two things are pinned here, and both are about *not* lying:

* The protocols are the ones this Gateway reports, filtered by what they can
  do. A list written into Condor is one that goes stale the first time somebody
  adds a connector, and the vault would quietly stop showing positions held
  there.
* A protocol that refuses to answer comes back named. "No positions" and
  "nobody could tell you" are different answers, and only one is good news —
  but the two reasons for the second are kept apart. A fungible-LP AMM has no
  positions to enumerate *by design*, every time, so it is ``unsupported`` and
  off the page; a protocol that should have answered and did not is an
  ``error`` and stays loud. Reporting the first as the second would put a
  warning on every vault page forever, which teaches people to ignore
  warnings.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.vaults as vault_routes
from condor.web.auth import get_current_user, require_server_access_query
from condor.web.models import WebUser

USER = WebUser(id=1, username="anyone", first_name="A", role="user")
SERVER = "vaults"
ACCOUNT = "4YfP3qnEiNeof88jsBE3LpeiV8M1VFPBtyZejmhQXaer"
WALLET = "3xV8qNoHiNCopjSmdqt83c1Gg5VgZTWd36ENZqWf6Vd6"

CONNECTORS = [
    {"name": "jupiter", "chain": "solana", "trading_types": ["router"]},
    {"name": "meteora", "chain": "solana", "trading_types": ["clmm", "amm", "launch"]},
    {"name": "raydium", "chain": "solana", "trading_types": ["amm", "clmm"]},
    {"name": "orca", "chain": "solana", "trading_types": ["clmm"]},
    {"name": "uniswap", "chain": "ethereum", "trading_types": ["clmm", "amm"]},
]

A_POSITION = {
    "position_address": "EjcJMPrXBmFLRkhJKr4KGetWrtsnoHEuCQwBe1x8mAAt",
    "pool_address": "JCLQiP7t1uxHiZHPotpUoVvPFFE48UxpkXHsVJJJvUrJ",
    "trading_pair": "C8fU5GdfAt5mnw2RK7HE6XJGFNxHpaskZMkXxdm88888-SOL",
    "base_token_amount": "1815.18",
    "quote_token_amount": "2.33",
}


class FakeClient:
    """hummingbot-api, answering the way the real one does — Gateway's refusal
    for a fungible-LP AMM included, since that is what shapes the response."""

    def __init__(self):
        self.asked: list[tuple[str, str]] = []

    async def connectors(self):
        return CONNECTORS

    async def clmm_positions_owned(self, connector, network_id, wallet_address):
        assert wallet_address == WALLET
        assert network_id == "solana-mainnet-beta"
        self.asked.append((connector, "clmm"))
        return [A_POSITION] if connector == "meteora" else []

    async def amm_positions_owned(self, connector, network_id, wallet_address):
        self.asked.append((connector, "amm"))
        if connector != "meteora":
            raise RuntimeError(
                f"positions-owned is not supported for {connector}: "
                "fungible-LP AMMs have no enumerable positions"
            )
        return []


class FakeGateway:
    def __init__(self, client, chain):
        self.client = client
        self._chain = chain

    async def vault(self, account):
        return self._chain


@pytest.fixture
def client(monkeypatch):
    upstream = FakeClient()
    gateway = FakeGateway(upstream, {"account": ACCOUNT, "wallet": WALLET})

    async def fake_gateway(server, network="mainnet-beta"):
        assert server == SERVER
        return gateway

    monkeypatch.setattr(vault_routes, "_gateway", fake_gateway)

    app = FastAPI()
    app.include_router(vault_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[require_server_access_query] = lambda: USER
    test_client = TestClient(app)
    test_client.upstream = upstream
    return test_client


def test_positions_come_back_tagged_with_the_protocol_that_held_them(client):
    body = client.get(f"/vaults/{ACCOUNT}/lp-positions?server={SERVER}").json()

    assert body["wallet_address"] == WALLET
    assert [(p["protocol"], p["kind"]) for p in body["positions"]] == [
        ("meteora", "clmm")
    ]
    assert body["positions"][0]["pool_address"] == A_POSITION["pool_address"]


def test_every_solana_protocol_that_can_enumerate_is_asked(client):
    client.get(f"/vaults/{ACCOUNT}/lp-positions?server={SERVER}")

    # Each connector, once per thing it can do — and nothing on another chain.
    assert set(client.upstream.asked) == {
        ("meteora", "clmm"),
        ("meteora", "amm"),
        ("raydium", "clmm"),
        ("raydium", "amm"),
        ("orca", "clmm"),
    }
    assert not any(name == "uniswap" for name, _ in client.upstream.asked)
    assert not any(name == "jupiter" for name, _ in client.upstream.asked)


def test_a_protocol_that_cannot_enumerate_is_not_reported_as_a_failure(client):
    body = client.get(f"/vaults/{ACCOUNT}/lp-positions?server={SERVER}").json()

    # Raydium's AMM says "not supported" every time there is ever a vault page.
    assert body["errors"] == []
    assert len(body["unsupported"]) == 1
    assert "raydium amm" in body["unsupported"][0]
    # And it does not cost the positions that were read.
    assert len(body["positions"]) == 1


def test_a_protocol_that_broke_stays_loud(monkeypatch, client):
    async def broken(connector, network_id, wallet_address):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(client.upstream, "clmm_positions_owned", broken)

    body = client.get(f"/vaults/{ACCOUNT}/lp-positions?server={SERVER}").json()

    # Three CLMM connectors, each named with the upstream's own words.
    assert len(body["errors"]) == 3
    assert all("connection refused" in message for message in body["errors"])
    assert body["positions"] == []


def test_a_vault_the_chain_does_not_know_is_a_404(monkeypatch):
    gateway = FakeGateway(FakeClient(), {})

    async def fake_gateway(server, network="mainnet-beta"):
        return gateway

    monkeypatch.setattr(vault_routes, "_gateway", fake_gateway)
    app = FastAPI()
    app.include_router(vault_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[require_server_access_query] = lambda: USER

    response = TestClient(app).get(f"/vaults/{ACCOUNT}/lp-positions?server={SERVER}")

    assert response.status_code == 404
