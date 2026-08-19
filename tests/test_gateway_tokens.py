"""A pool's tokens reach Gateway's token list on their own.

Gateway resolves a swap by mint address, but not a balance: it skips token
accounts whose mint is not on the network's token list, so an unregistered token
reads as 0 and every panel that sizes an order off a balance is working from an
empty wallet. Registration therefore has to happen without anyone asking for it.

Three shapes are pinned here. That an already-listed token is never re-saved (the
memo is what makes opening a pool twice free). That a save is *verified* by
re-reading the list, because Gateway answers 200 and writes nothing when another
token already holds the ticker — the one failure that stays silent otherwise. And
that a failure is never remembered, so it stays retryable on the next visit.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.dex as dex_routes
from condor.fetchers import gateway_tokens
from condor.fetchers.gateway_tokens import ensure_tokens_listed, token_addresses
from condor.web.auth import get_current_user
from condor.web.models import WebUser

MINT = "9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
NETWORK = "solana-mainnet-beta"


class FakeGateway:
    """Gateway's token list, with the two behaviours that matter kept honest."""

    def __init__(self, listed=(), refuse_save=False, fail_search=False):
        self.tokens = [{"address": a, "symbol": a[:4]} for a in listed]
        self.refuse_save = refuse_save
        self.fail_search = fail_search
        self.searches: list[str] = []
        self.saves: list[str] = []
        self.deletes: list[str] = []

    async def get_network_tokens(self, network_id, search=None):
        if self.fail_search:
            raise RuntimeError("gateway down")
        self.searches.append(search)
        # Gateway matches search against symbol, name and address alike.
        needle = (search or "").lower()
        return {
            "tokens": [
                t
                for t in self.tokens
                if not needle
                or needle in str(t["address"]).lower()
                or needle in str(t.get("symbol") or "").lower()
            ]
        }

    async def save_network_token(self, network_id, token_address):
        self.saves.append(token_address)
        # A ticker already held by another token: Gateway answers 200 and keeps
        # the list unchanged.
        if self.refuse_save:
            return {"message": "already exists"}
        self.tokens.append({"address": token_address, "symbol": "NEW"})
        return {"message": "added"}

    async def delete_token(self, network_id, token_address):
        self.deletes.append(token_address)
        before = len(self.tokens)
        self.tokens = [t for t in self.tokens if t["address"] != token_address]
        # The holder is deleted, so its ticker no longer blocks a save.
        if len(self.tokens) < before:
            self.refuse_save = False
        return {"message": "deleted"}


class FakeClient:
    def __init__(self, gateway):
        self.gateway = gateway


@pytest.fixture(autouse=True)
def _clean_memo():
    gateway_tokens.reset_listed_memo()
    yield
    gateway_tokens.reset_listed_memo()


def test_a_missing_token_is_registered_without_being_asked_for():
    gw = FakeGateway(listed=[USDC])
    verdicts = asyncio.run(ensure_tokens_listed(FakeClient(gw), NETWORK, [MINT, USDC]))

    assert verdicts == {MINT: "added", USDC: "listed"}
    assert gw.saves == [MINT]


def test_a_known_token_is_never_saved_twice():
    gw = FakeGateway(listed=[])
    client = FakeClient(gw)

    asyncio.run(ensure_tokens_listed(client, NETWORK, [MINT]))
    second = asyncio.run(ensure_tokens_listed(client, NETWORK, [MINT]))

    assert second == {MINT: "listed"}
    # One save, and no second lookup: the memo answers the repeat visit outright.
    assert gw.saves == [MINT]
    assert gw.searches.count(MINT) == 2  # the pre-check and the verification


def test_a_save_that_wrote_nothing_is_reported_not_assumed():
    # Gateway refuses a token whose ticker another token already holds — with a
    # 200. Trusting the response would leave a token silently unlisted, and its
    # balance silently 0.
    gw = FakeGateway(listed=[], refuse_save=True)
    verdicts = asyncio.run(ensure_tokens_listed(FakeClient(gw), NETWORK, [MINT]))

    assert verdicts == {MINT: "symbol_taken"}


def test_a_failed_lookup_stays_retryable():
    gw = FakeGateway(fail_search=True)
    client = FakeClient(gw)

    assert asyncio.run(ensure_tokens_listed(client, NETWORK, [MINT])) == {
        MINT: "failed"
    }

    # Nothing was remembered, so a later visit tries again rather than trusting
    # a state that was never confirmed.
    gw.fail_search = False
    assert asyncio.run(ensure_tokens_listed(client, NETWORK, [MINT])) == {MINT: "added"}


def test_only_address_shaped_values_are_sent_upstream():
    # A DEX pair is `<base_mint>-<quote_symbol>`: splitting it yields one thing
    # that can be registered and one that cannot.
    assert token_addresses(f"{MINT}-USDC") == [MINT]
    assert token_addresses(MINT, MINT) == [MINT]  # deduped
    assert token_addresses("SOL-USDC", None, "") == []


# ---------------------------------------------------------------------------
# The route the pool workspace calls on open.
# ---------------------------------------------------------------------------

USER = WebUser(id=222, username="u", first_name="U", role="user")


class FakeConfigManager:
    def __init__(self, gateway):
        self.client = FakeClient(gateway)

    def has_server_access(self, user_id, name, *a, **kw):
        return True

    async def get_client(self, name):
        return self.client


@pytest.fixture
def route_client(monkeypatch):
    gw = FakeGateway(listed=[USDC])
    cm = FakeConfigManager(gw)
    monkeypatch.setattr(dex_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(dex_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app), gw


def test_opening_a_pool_registers_both_of_its_tokens(route_client):
    client, gw = route_client

    body = client.post(
        "/servers/srv/dex/tokens",
        json={"network": "solana-mainnet-beta", "addresses": [MINT, USDC]},
    ).json()

    assert body["tokens"] == {MINT: "added", USDC: "listed"}
    assert gw.saves == [MINT]


def test_a_chain_named_the_gecko_way_still_reaches_gateway(route_client):
    # Pool rows carry a chain id from whichever upstream produced them; Gateway
    # only answers to its own spelling, so the route canonicalizes rather than
    # rejecting a pool the user can plainly see.
    client, _gw = route_client

    response = client.post(
        "/servers/srv/dex/tokens", json={"network": "solana", "addresses": [MINT]}
    )

    assert response.status_code == 200
    assert response.json()["tokens"] == {MINT: "added"}


def test_a_chain_gateway_does_not_reach_is_refused(route_client):
    client, _gw = route_client

    response = client.post(
        "/servers/srv/dex/tokens", json={"network": "not-a-chain", "addresses": [MINT]}
    )

    assert response.status_code == 400


def test_a_ticker_is_not_sent_upstream_as_an_address(route_client):
    # `USDC` is a quote symbol, not something Gateway can look up by address.
    client, gw = route_client

    body = client.post(
        "/servers/srv/dex/tokens",
        json={"network": "solana-mainnet-beta", "addresses": ["USDC", ""]},
    ).json()

    assert body["tokens"] == {}
    assert gw.saves == []


# ---------------------------------------------------------------------------
# The banner's add button: /dex/tokens/add, the human's answer to a verdict.
# ---------------------------------------------------------------------------


@pytest.fixture
def make_client(monkeypatch):
    def _make(gw):
        cm = FakeConfigManager(gw)
        monkeypatch.setattr(dex_routes, "get_config_manager", lambda: cm)
        monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
        app = FastAPI()
        app.include_router(dex_routes.router)
        app.dependency_overrides[get_current_user] = lambda: USER
        return TestClient(app)

    return _make


def test_the_add_button_registers_what_the_ensure_could_not(make_client):
    client = make_client(FakeGateway(listed=[]))

    body = client.post(
        "/servers/srv/dex/tokens/add", json={"network": NETWORK, "address": MINT}
    ).json()

    assert body["verdict"] == "added"


def test_a_ticker_collision_names_its_holder(make_client):
    # The verdict alone says the ticker is in use; the button's answer says by
    # whom, which is what the user needs to decide whether to replace it.
    gw = FakeGateway(refuse_save=True)
    gw.tokens = [{"address": USDC, "symbol": "PUMP", "name": "Other Pump"}]
    client = make_client(gw)

    body = client.post(
        "/servers/srv/dex/tokens/add",
        json={"network": NETWORK, "address": MINT, "symbol": "PUMP"},
    ).json()

    assert body["verdict"] == "symbol_taken"
    assert body["conflict"]["address"] == USDC
    assert gw.deletes == []  # naming the holder must not touch it


def test_replace_deletes_the_holder_then_registers(make_client):
    gw = FakeGateway(refuse_save=True)
    gw.tokens = [{"address": USDC, "symbol": "PUMP"}]
    client = make_client(gw)

    body = client.post(
        "/servers/srv/dex/tokens/add",
        json={"network": NETWORK, "address": MINT, "symbol": "PUMP", "replace": True},
    ).json()

    assert gw.deletes == [USDC]
    assert body["verdict"] == "added"


def test_a_replaced_holder_is_forgotten_by_the_memo(make_client):
    # USDC was seen on the list and memoized; after a replace deletes it, a later
    # ensure must re-read the list rather than trust a listing that is gone.
    gw = FakeGateway(refuse_save=True)
    gw.tokens = [{"address": USDC, "symbol": "PUMP"}]
    client = make_client(gw)
    assert asyncio.run(ensure_tokens_listed(FakeClient(gw), NETWORK, [USDC])) == {
        USDC: "listed"
    }

    client.post(
        "/servers/srv/dex/tokens/add",
        json={"network": NETWORK, "address": MINT, "symbol": "PUMP", "replace": True},
    )

    verdicts = asyncio.run(ensure_tokens_listed(FakeClient(gw), NETWORK, [USDC]))
    assert verdicts == {USDC: "added"}  # re-registered, not answered from memory


def test_the_button_surfaces_a_gateway_error_instead_of_a_verdict(make_client):
    # The automatic ensure folds errors into `failed`; a clicked button deserves
    # the actual reason.
    client = make_client(FakeGateway(fail_search=True))

    response = client.post(
        "/servers/srv/dex/tokens/add", json={"network": NETWORK, "address": MINT}
    )

    assert response.status_code == 502


def test_the_button_refuses_a_value_that_is_not_an_address(make_client):
    client = make_client(FakeGateway())

    response = client.post(
        "/servers/srv/dex/tokens/add", json={"network": NETWORK, "address": "USDC"}
    )

    assert response.status_code == 400
