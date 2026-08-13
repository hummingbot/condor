"""Pool discovery in handlers/dex/pool_data.py (FEAT-044).

The DEX page is pool-first, so every row it renders has to answer three questions
the frontend cannot: can I chart this pool, can I trade in it, can I LP in it.
That is what ``decorate_pool`` decides, and what these tests pin — against the
DataFrame shape ``geckoterminal_py`` actually returns (flat columns, symbols only
inside the display name) and the Gateway CLMM payload shape.
"""

import asyncio

import pandas as pd
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.dex as dex_routes
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from handlers.dex import pool_data

POOL = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_caches():
    for cache in (
        pool_data._gecko_pool_list_cache,
        pool_data._gateway_pool_list_cache,
        pool_data._pool_by_address_cache,
    ):
        cache.clear()
    yield


def gecko_row(dex_id="meteora", name="SOL / USDC", address=POOL):
    """One row as ``process_pools_list`` produces it: flat, ids already stripped."""
    return {
        "id": f"solana_{address}",
        "type": "pool",
        "name": name,
        "address": address,
        "dex_id": dex_id,
        "base_token_id": SOL,
        "quote_token_id": USDC,
        "network_id": "solana",
        "base_token_price_usd": "150.0",
        "quote_token_price_usd": "1.0",
        "reserve_in_usd": "12500000.0",
        "volume_usd_h24": "4200000.0",
        "price_change_percentage_h24": "-3.5",
        "fdv_usd": "9000000000",
        "market_cap_usd": "8000000000",
        "pool_created_at": "2024-01-01T00:00:00Z",
    }


def gateway_row(**over):
    row = {
        "pool_address": POOL,
        "connector": "meteora",
        "trading_pair": "SOL-USDC",
        "base_symbol": "SOL",
        "quote_symbol": "USDC",
        "mint_x": SOL,
        "mint_y": USDC,
        "liquidity": 4_500_000.0,
        "volume_24h": 1_200_000.0,
        "apr": 42.5,
        "bin_step": 20,
        "base_fee_percentage": 0.2,
        "price": 150.0,
    }
    row.update(over)
    return row


class FakeGecko:
    """Stands in for the shared GeckoTerminalAsyncClient."""

    def __init__(self, frame=None, error=None):
        self.frame = frame
        self.error = error
        self.calls: list[tuple] = []

    async def _answer(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.frame

    async def get_trending_pools_by_network(self, n):
        return await self._answer("trending", n)

    async def get_top_pools_by_network(self, n):
        return await self._answer("top", n)

    async def get_new_pools_by_network(self, n):
        return await self._answer("new", n)

    async def get_top_pools_by_network_token(self, n, t):
        return await self._answer("token", n, t)

    async def get_pool_by_network_address(self, n, a):
        return await self._answer("address", n, a)


@pytest.fixture
def fake_gecko(monkeypatch):
    def install(frame=None, error=None):
        client = FakeGecko(frame, error)
        monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)
        return client

    return install


# ── _coerce_pool_rows ──


def test_coerce_handles_a_dataframe():
    rows = pool_data._coerce_pool_rows(pd.DataFrame([gecko_row(), gecko_row()]), 10)
    assert len(rows) == 2 and rows[0]["address"] == POOL


def test_coerce_handles_json_envelope_and_bare_list():
    assert pool_data._coerce_pool_rows({"data": [gecko_row()]}, 10)[0]["name"] == (
        "SOL / USDC"
    )
    assert len(pool_data._coerce_pool_rows([gecko_row()], 10)) == 1


def test_coerce_handles_a_single_pool_mapping():
    """The by-address endpoint answers with one pool, not a list."""
    assert len(pool_data._coerce_pool_rows({"data": gecko_row()}, 1)) == 1


def test_coerce_respects_the_limit_and_drops_non_mappings():
    assert len(pool_data._coerce_pool_rows([gecko_row()] * 5, 2)) == 2
    assert pool_data._coerce_pool_rows([gecko_row(), "junk", None], 10) == [gecko_row()]


def test_coerce_of_nothing_is_empty():
    assert pool_data._coerce_pool_rows(None, 10) == []
    assert pool_data._coerce_pool_rows(pd.DataFrame(), 10) == []


# ── normalization: the two new address keys ──


def test_gecko_normalization_carries_token_addresses():
    info = pool_data.normalize_pool_data(gecko_row(), source="gecko")
    assert info["base_token_address"] == SOL
    assert info["quote_token_address"] == USDC


def test_gecko_normalization_reads_nested_relationships():
    """The raw API shape, where ids are still ``{network}_{address}``."""
    raw = {
        "id": f"solana_{POOL}",
        "attributes": {"address": POOL, "name": "SOL / USDC", "dex_id": "meteora"},
        "relationships": {
            "base_token": {"data": {"id": f"solana_{SOL}"}},
            "quote_token": {"data": {"id": f"solana_{USDC}"}},
        },
    }
    info = pool_data.normalize_pool_data(raw, source="gecko")
    assert (info["base_token_address"], info["quote_token_address"]) == (SOL, USDC)


def test_gateway_normalization_takes_addresses_from_the_mints():
    info = pool_data.normalize_pool_data(gateway_row(), source="gateway")
    assert info["base_token_address"] == SOL
    assert info["quote_token_address"] == USDC


def test_missing_token_addresses_are_empty_not_an_error():
    info = pool_data.normalize_pool_data({"address": POOL}, source="gecko")
    assert info["base_token_address"] == ""


# ── decoration ──


def test_meteora_pool_is_lp_supported_and_tradable():
    pool = pool_data._normalize_gecko_pool(gecko_row("meteora"), "solana-mainnet-beta")
    assert pool["lp_provider"] == "meteora/clmm"
    assert pool["lp_supported"] is True
    assert pool["tradable"] is True
    assert pool["gateway_network"] == "solana-mainnet-beta"
    assert pool["has_bins"] is True


def test_raydium_amm_v4_pool_is_charted_but_not_lp_able():
    """Plain ``raydium`` is the constant-product AMM; only raydium-clmm is CLMM."""
    pool = pool_data._normalize_gecko_pool(gecko_row("raydium"), "solana-mainnet-beta")
    assert pool["lp_provider"] is None
    assert pool["lp_supported"] is False
    # Still a pool the user can chart and swap in — a state, not an error.
    assert pool["tradable"] is True
    assert pool["trading_pair"] == f"{SOL}-USDC"


def test_raydium_clmm_pool_is_lp_able():
    pool = pool_data._normalize_gecko_pool(
        gecko_row("raydium-clmm"), "solana-mainnet-beta"
    )
    assert pool["lp_provider"] == "raydium/clmm"


def test_pool_on_a_chain_gateway_cannot_reach_is_not_tradable():
    pool = pool_data._normalize_gecko_pool(gecko_row("cetus"), "sui-network")
    assert pool["tradable"] is False
    assert pool["gateway_network"] == ""
    assert pool["lp_supported"] is False


def test_trading_pair_is_base_mint_dash_quote_symbol():
    """The form LP and DEX order executors already carry (FEAT-042)."""
    pool = pool_data._normalize_gecko_pool(gecko_row(), "solana-mainnet-beta")
    assert pool["trading_pair"] == f"{SOL}-USDC"
    assert pool["base_symbol"] == "SOL"
    assert pool["quote_symbol"] == "USDC"


def test_trading_pair_is_empty_when_the_base_mint_is_unknown():
    row = gecko_row()
    row["base_token_id"] = ""
    pool = pool_data._normalize_gecko_pool(row, "solana-mainnet-beta")
    assert pool["trading_pair"] == ""


def test_symbols_are_parsed_out_of_the_pool_display_name():
    """GeckoTerminal publishes no symbol column — only ``"BONK / SOL"``."""
    pool = pool_data._normalize_gecko_pool(
        gecko_row(name="BONK / SOL"), "solana-mainnet-beta"
    )
    assert (pool["base_token_symbol"], pool["quote_token_symbol"]) == ("BONK", "SOL")


def test_gateway_pool_keeps_its_apr_and_bin_step_through_decoration():
    pool = pool_data.decorate_pool(
        pool_data.normalize_pool_data(gateway_row(), source="gateway"), "solana"
    )
    assert pool["apr"] == 42.5
    assert pool["bin_step"] == 20
    assert pool["lp_provider"] == "meteora/clmm"
    assert pool["trading_pair"] == f"{SOL}-USDC"


# ── list_gecko_pools ──


@pytest.mark.parametrize(
    "view,expected", [("trending", "trending"), ("top", "top"), ("new", "new")]
)
def test_each_gecko_view_hits_its_own_endpoint(fake_gecko, view, expected):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    pools = run(pool_data.list_gecko_pools("solana-mainnet-beta", view=view))
    assert client.calls[0][0] == expected
    assert client.calls[0][1] == "solana"  # gateway id normalized to the gecko chain
    assert len(pools) == 1


def test_unknown_view_falls_back_to_trending(fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    run(pool_data.list_gecko_pools("solana", view="nonsense"))
    assert client.calls[0][0] == "trending"


def test_token_view_requires_a_real_address(fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    assert run(pool_data.list_gecko_pools("solana", view="token", token="SOL")) == []
    assert run(pool_data.list_gecko_pools("solana", view="token", token="")) == []
    assert client.calls == [], "a ticker must never reach a URL path segment"


def test_token_view_passes_the_mint_through(fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    run(pool_data.list_gecko_pools("solana", view="token", token=SOL))
    assert client.calls[0] == ("token", "solana", SOL)


def test_an_upstream_failure_is_an_empty_list_not_an_exception(fake_gecko):
    fake_gecko(error=RuntimeError("429 rate limited"))
    assert run(pool_data.list_gecko_pools("solana")) == []


def test_a_failure_is_not_cached(fake_gecko):
    fake_gecko(error=RuntimeError("boom"))
    run(pool_data.list_gecko_pools("solana"))
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    assert len(run(pool_data.list_gecko_pools("solana"))) == 1


def test_lists_are_ttl_cached_across_callers(fake_gecko):
    """Every viewer shares one GeckoTerminal budget, so the list is cached."""
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    run(pool_data.list_gecko_pools("solana"))
    run(pool_data.list_gecko_pools("solana"))
    assert len(client.calls) == 1


def test_cached_rows_are_copies_a_caller_cannot_poison(fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row()]))
    first = run(pool_data.list_gecko_pools("solana"))
    first[0]["address"] = "mutated"
    assert run(pool_data.list_gecko_pools("solana"))[0]["address"] == POOL


def test_rows_with_no_address_are_dropped(fake_gecko):
    """A row with neither an address nor an id to recover one from is unusable."""
    row = gecko_row()
    row["address"] = ""
    row["id"] = ""
    fake_gecko(pd.DataFrame([row, gecko_row()]))
    assert len(run(pool_data.list_gecko_pools("solana"))) == 1


def test_limit_is_clamped(fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row(address=POOL)] * 60))
    assert len(run(pool_data.list_gecko_pools("solana", limit=5))) == 5
    assert len(run(pool_data.list_gecko_pools("solana", limit=10_000))) == 60


# ── list_gateway_pools ──


class FakeClmm:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []

    async def get_pools(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.payload


class FakeClient:
    def __init__(self, clmm):
        self.gateway_clmm = clmm


def test_gateway_pools_are_normalized_and_decorated():
    clmm = FakeClmm({"pools": [gateway_row()]})
    pools = run(pool_data.list_gateway_pools(FakeClient(clmm), "meteora"))
    assert len(pools) == 1
    assert pools[0]["source"] == "gateway"
    assert pools[0]["apr"] == 42.5
    assert pools[0]["lp_supported"] is True
    assert pools[0]["gateway_network"] == "solana-mainnet-beta"
    assert clmm.calls[0]["connector"] == "meteora"


def test_gateway_search_term_is_forwarded():
    clmm = FakeClmm({"pools": []})
    run(pool_data.list_gateway_pools(FakeClient(clmm), "orca", search="SOL"))
    assert clmm.calls[0]["search_term"] == "SOL"


def test_blank_gateway_search_is_no_search():
    clmm = FakeClmm({"pools": []})
    run(pool_data.list_gateway_pools(FakeClient(clmm), "orca", search="   "))
    assert clmm.calls[0]["search_term"] is None


def test_gateway_failure_is_an_empty_list():
    clmm = FakeClmm(error=RuntimeError("gateway down"))
    assert run(pool_data.list_gateway_pools(FakeClient(clmm), "meteora")) == []


def test_client_without_gateway_clmm_is_an_empty_list():
    assert run(pool_data.list_gateway_pools(object(), "meteora")) == []
    assert run(pool_data.list_gateway_pools(None, "meteora")) == []


def test_gateway_lists_are_cached_per_connector_and_search():
    clmm = FakeClmm({"pools": [gateway_row()]})
    client = FakeClient(clmm)
    run(pool_data.list_gateway_pools(client, "meteora"))
    run(pool_data.list_gateway_pools(client, "meteora"))
    assert len(clmm.calls) == 1
    run(pool_data.list_gateway_pools(client, "meteora", search="SOL"))
    assert len(clmm.calls) == 2


# ── fetch_pool_by_address ──


def test_pool_by_address_returns_one_decorated_pool(fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    pool = run(pool_data.fetch_pool_by_address("solana-mainnet-beta", POOL))
    assert pool["address"] == POOL
    assert pool["lp_provider"] == "meteora/clmm"
    assert client.calls[0] == ("address", "solana", POOL)


def test_pool_by_address_refuses_a_non_address(fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    assert run(pool_data.fetch_pool_by_address("solana", "../../etc/passwd")) is None
    assert client.calls == []


def test_unknown_pool_is_none_and_cached(fake_gecko):
    client = fake_gecko(pd.DataFrame())
    assert run(pool_data.fetch_pool_by_address("solana", POOL)) is None
    assert run(pool_data.fetch_pool_by_address("solana", POOL)) is None
    assert len(client.calls) == 1


def test_pool_by_address_failure_is_none_and_not_cached(fake_gecko):
    fake_gecko(error=RuntimeError("boom"))
    assert run(pool_data.fetch_pool_by_address("solana", POOL)) is None
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    assert run(pool_data.fetch_pool_by_address("solana", POOL)) is not None


# ── the route (condor/web/routes/dex.py) ──


USER = WebUser(id=111, username="u", first_name="U", role="user")


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


def test_gecko_source_lists_pools(route_client, fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row()]))
    r = route_client().get("/servers/srv/dex/pools?source=gecko&view=trending")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "gecko"
    assert body["pools"][0]["address"] == POOL
    assert body["pools"][0]["lp_supported"] is True


def test_gecko_token_view_rejects_a_ticker(route_client, fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    r = route_client().get("/servers/srv/dex/pools?source=gecko&view=token&query=SOL")
    assert r.status_code == 400
    assert client.calls == []


def test_gecko_token_view_accepts_a_mint(route_client, fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    r = route_client().get(
        f"/servers/srv/dex/pools?source=gecko&view=token&query={SOL}"
    )
    assert r.status_code == 200
    assert client.calls[0] == ("token", "solana", SOL)


def test_gateway_source_lists_pools(route_client):
    clmm = FakeClmm({"pools": [gateway_row()]})
    r = route_client(client=FakeClient(clmm)).get(
        "/servers/srv/dex/pools?source=gateway&connector=meteora&query=SOL"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "gateway"
    assert body["pools"][0]["apr"] == 42.5
    assert clmm.calls[0]["search_term"] == "SOL"


def test_an_unknown_source_is_a_400(route_client):
    assert route_client().get("/servers/srv/dex/pools?source=moon").status_code == 400


def test_upstream_failure_is_an_empty_list_not_a_502(route_client, fake_gecko):
    fake_gecko(error=RuntimeError("429"))
    r = route_client().get("/servers/srv/dex/pools")
    assert r.status_code == 200 and r.json()["pools"] == []


def test_an_unreachable_server_is_an_empty_gateway_list(route_client):
    r = route_client(client_error=RuntimeError("no server")).get(
        "/servers/srv/dex/pools?source=gateway&connector=meteora"
    )
    assert r.status_code == 200 and r.json()["pools"] == []


def test_no_server_access_is_a_403(route_client, fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row()]))
    assert route_client(allowed=False).get("/servers/srv/dex/pools").status_code == 403
    r = route_client(allowed=False).get(f"/servers/srv/dex/pools/{POOL}")
    assert r.status_code == 403


def test_pool_by_address_renders_from_the_url_alone(route_client, fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row()]))
    r = route_client().get(f"/servers/srv/dex/pools/{POOL}?network=solana-mainnet-beta")
    assert r.status_code == 200
    pool = r.json()
    assert pool["address"] == POOL
    assert pool["trading_pair"] == f"{SOL}-USDC"
    assert pool["gateway_network"] == "solana-mainnet-beta"


def test_pool_by_address_rejects_a_non_address(route_client, fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    assert (
        route_client().get("/servers/srv/dex/pools/not-an-address").status_code == 400
    )
    assert client.calls == []


def test_unknown_pool_is_a_404(route_client, fake_gecko):
    fake_gecko(pd.DataFrame())
    assert route_client().get(f"/servers/srv/dex/pools/{POOL}").status_code == 404
