"""Liquidity bins for the depth column beside a pool's chart (FEAT-045).

Two things are pinned here. First, that ``fetch_liquidity_bins`` can be handed a
client — the web side holds a server *name*, not a chat id, and the
``get_pool_info`` → ``get_pools`` fallback for pools outside Gateway's DLMM list
must keep working through that door. Second, that the route degrades: an
unsupported venue, an unreachable server and an upstream raise are all states the
workspace renders, never a 502 the user cannot act on.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.dex as dex_routes
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from handlers.dex import pool_data

POOL = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"
USER = WebUser(id=111, username="u", first_name="U", role="user")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_bins_cache():
    pool_data._pool_bins_cache.clear()
    yield
    pool_data._pool_bins_cache.clear()


def bin_row(price, base=0.0, quote=0.0):
    return {
        "bin_id": int(price * 100),
        "price": price,
        "base_token_amount": base,
        "quote_token_amount": quote,
    }


def pool_info(**over):
    info = {
        "address": POOL,
        "price": 150.0,
        "bin_step": 20,
        "active_bin_id": 15000,
        "bins": [
            bin_row(149.0, quote=1000.0),
            bin_row(150.0, base=2.0, quote=300.0),
            bin_row(151.0, base=5.0),
        ],
    }
    info.update(over)
    return info


class FakeClmm:
    """Gateway's CLMM namespace, recording what it was asked."""

    def __init__(self, info=None, error=None, pools=None):
        self.info = info
        self.error = error
        self.pools = pools
        self.calls = []

    async def get_pool_info(self, connector, network, pool_address):
        self.calls.append(("info", connector, network, pool_address))
        if self.error:
            raise self.error
        return self.info

    async def get_pools(self, connector, search_term=None, limit=None, page=None):
        self.calls.append(("pools", connector, search_term))
        return {"pools": self.pools or []}


class FakeClient:
    def __init__(self, clmm):
        self.gateway_clmm = clmm


class FakeConfigManager:
    def __init__(self, allowed=True, client=None, client_error=None):
        self.allowed = allowed
        self._client = client
        self._client_error = client_error

    def has_server_access(self, user_id, name, *a, **kw):
        return self.allowed

    async def get_client(self, name):
        if self._client_error:
            raise self._client_error
        return self._client


@pytest.fixture
def route_client(monkeypatch):
    def build(allowed=True, client=None, client_error=None):
        cm = FakeConfigManager(allowed, client, client_error)
        monkeypatch.setattr(dex_routes, "get_config_manager", lambda: cm)
        app = FastAPI()
        app.include_router(dex_routes.router)
        app.dependency_overrides[get_current_user] = lambda: USER
        return TestClient(app)

    return build


def bins_url(connector="meteora", network="solana-mainnet-beta", pool=POOL):
    return f"/servers/srv/dex/pools/{pool}/bins?network={network}&connector={connector}"


# ── fetch_liquidity_bins takes a client ──


def test_an_injected_client_replaces_the_telegram_accessor(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("get_client must not be reached when a client is given")

    monkeypatch.setattr(pool_data, "get_client", boom)
    clmm = FakeClmm(pool_info())
    bins, info, error = run(
        pool_data.fetch_liquidity_bins(POOL, "meteora", client=FakeClient(clmm))
    )
    assert error is None
    assert len(bins) == 3
    assert info["bin_step"] == 20
    assert clmm.calls[0][0] == "info"


def test_the_telegram_path_still_resolves_its_own_client(monkeypatch):
    clmm = FakeClmm(pool_info())
    seen = {}

    async def fake_get_client(chat_id, context=None):
        seen["chat_id"] = chat_id
        return FakeClient(clmm)

    monkeypatch.setattr(pool_data, "get_client", fake_get_client)
    user_data = {}
    bins, _info, error = run(
        pool_data.fetch_liquidity_bins(POOL, "meteora", user_data=user_data, chat_id=7)
    )
    assert error is None and len(bins) == 3
    assert seen["chat_id"] == 7
    # Still cached in the caller's conversation, not in the process-wide dict.
    assert pool_data._pool_bins_cache == {}
    run(pool_data.fetch_liquidity_bins(POOL, "meteora", user_data=user_data, chat_id=7))
    assert len(clmm.calls) == 1


def test_two_clientless_viewers_share_one_gateway_call():
    clmm = FakeClmm(pool_info())
    client = FakeClient(clmm)
    first = run(pool_data.fetch_liquidity_bins(POOL, "meteora", client=client))
    second = run(pool_data.fetch_liquidity_bins(POOL, "meteora", client=client))
    assert first[0] == second[0]
    assert len(clmm.calls) == 1


def test_the_get_pools_fallback_survives_the_client_parameter():
    clmm = FakeClmm(
        error=ValueError("1 validation error for PoolInfo: Field required"),
        pools=[{"trading_pair": "SOL-USDC", "bins": [bin_row(150.0, base=1.0)]}],
    )
    bins, info, error = run(
        pool_data.fetch_liquidity_bins(POOL, "meteora", client=FakeClient(clmm))
    )
    assert error is None
    assert info["address"] == POOL
    assert [c[0] for c in clmm.calls] == ["info", "pools"]
    assert len(bins) == 1


def test_an_unsupported_connector_never_reaches_gateway():
    clmm = FakeClmm(pool_info())
    bins, info, error = run(
        pool_data.fetch_liquidity_bins(POOL, "sushiswap", client=FakeClient(clmm))
    )
    assert (bins, info) == (None, None)
    assert "not available" in error
    assert clmm.calls == []


# ── the route ──


def test_a_supported_pool_returns_normalized_bins(route_client):
    clmm = FakeClmm(pool_info())
    r = route_client(client=FakeClient(clmm)).get(bins_url())
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True and body["reason"] is None
    assert body["active_price"] == 150.0
    assert body["bin_step"] == 20
    assert body["bins"][0] == {
        "price": 149.0,
        "base_amount": 0.0,
        "quote_amount": 1000.0,
        "liquidity_usd": None,
    }
    assert [b["price"] for b in body["bins"]] == [149.0, 150.0, 151.0]


def test_liquidity_usd_is_priced_only_when_both_sides_are(route_client):
    priced = pool_info(base_token_price_usd="150.0", quote_token_price_usd="1.0")
    r = route_client(client=FakeClient(FakeClmm(priced))).get(bins_url())
    body = r.json()
    assert body["bins"][1]["liquidity_usd"] == pytest.approx(2.0 * 150.0 + 300.0)

    pool_data._pool_bins_cache.clear()
    half = pool_info(base_token_price_usd="150.0")
    r = route_client(client=FakeClient(FakeClmm(half))).get(bins_url())
    assert all(b["liquidity_usd"] is None for b in r.json()["bins"])


@pytest.mark.parametrize(
    "dex_id",
    ["meteora", "orca", "raydium", "raydium-amm-v4", "uniswap-v3-base", "sushiswap"],
)
def test_the_route_gate_is_the_same_answer_as_has_bins(route_client, dex_id):
    """A row's ``has_bins`` decoration and this route must never disagree.

    The browser only asks for a depth column when the pool says it has one, so
    a route that answered a wider set would be reachable by nothing, and a
    narrower one would draw an empty column on a pool that promised bins.
    """
    network = "solana-mainnet-beta"
    expected = pool_data.can_fetch_liquidity(dex_id, network)
    r = route_client(client=FakeClient(FakeClmm(pool_info()))).get(
        bins_url(connector=dex_id, network=network)
    )
    assert r.status_code == 200
    assert r.json()["available"] is expected


def test_an_unsupported_dex_is_unavailable_with_a_reason(route_client):
    clmm = FakeClmm(pool_info())
    r = route_client(client=FakeClient(clmm)).get(bins_url(connector="raydium-amm-v4"))
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["bins"] == [] and body["reason"]
    # Gate first: an unsupported venue costs no Gateway call.
    assert clmm.calls == []


def test_a_supported_dex_on_the_wrong_chain_is_unavailable(route_client):
    clmm = FakeClmm(pool_info())
    r = route_client(client=FakeClient(clmm)).get(bins_url(network="base-mainnet"))
    assert r.status_code == 200
    assert r.json()["available"] is False
    assert clmm.calls == []


def test_an_upstream_raise_does_not_502(route_client):
    clmm = FakeClmm(error=RuntimeError("gateway is down"))
    r = route_client(client=FakeClient(clmm)).get(bins_url())
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["reason"]


def test_an_unreachable_server_is_unavailable_not_a_502(route_client):
    r = route_client(client_error=RuntimeError("no server")).get(bins_url())
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_a_pool_with_no_bins_is_unavailable(route_client):
    r = route_client(client=FakeClient(FakeClmm(pool_info(bins=[])))).get(bins_url())
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["bins"] == []


def test_bins_without_a_price_are_dropped(route_client):
    info = pool_info(bins=[bin_row(150.0, base=1.0), {"base_token_amount": 5.0}])
    r = route_client(client=FakeClient(FakeClmm(info))).get(bins_url())
    body = r.json()
    assert body["available"] is True
    assert [b["price"] for b in body["bins"]] == [150.0]


def test_a_non_address_is_a_400(route_client):
    clmm = FakeClmm(pool_info())
    r = route_client(client=FakeClient(clmm)).get(bins_url(pool="not-an-address"))
    assert r.status_code == 400
    assert clmm.calls == []


def test_no_server_access_is_a_403(route_client):
    r = route_client(allowed=False, client=FakeClient(FakeClmm(pool_info()))).get(
        bins_url()
    )
    assert r.status_code == 403
