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
def _clear_caches(monkeypatch):
    for cache in (
        pool_data._gecko_page_cache,
        pool_data._gecko_dexes_cache,
        pool_data._gateway_pool_list_cache,
        pool_data._pool_by_address_cache,
        pool_data._multi_pool_cache,
        pool_data._orca_pool_list_cache,
    ):
        cache.clear()
    # A single-flight task left over from another test's event loop would be
    # awaited on a loop that is already closed.
    pool_data._gecko_inflight.clear()
    # No test in this module may reach the real GeckoTerminal — and a Gateway
    # listing now looks pools up there too, to fill the figures Orca's connector
    # leaves null. The empty stand-in is overridden by the ``fake_gecko`` fixture
    # wherever a test actually wants rows back.
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: FakeGecko())

    # Nor may any test reach the real api.orca.so. Overridden by ``fake_orca``
    # wherever a test actually wants whirlpools back.
    async def _no_orca(*a, **kw):
        raise AssertionError("test reached the real Orca API")

    monkeypatch.setattr(pool_data.orca_api, "fetch_whirlpools", _no_orca)
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


def api_row(row=None, **over):
    """One row in the shape the *API* returns, which ``glom`` reads.

    ``gecko_row`` is the flat DataFrame shape the library hands back; the paged
    listing talks to the endpoint directly, so the fake has to answer the nested
    JSON the flat shape is derived from.
    """
    flat = dict(row or gecko_row())
    flat.update(over)
    return {
        "id": flat.get("id", ""),
        "type": "pool",
        "attributes": {
            "address": flat.get("address", ""),
            "name": flat.get("name", ""),
            "base_token_price_usd": flat.get("base_token_price_usd"),
            "base_token_price_native_currency": None,
            "quote_token_price_usd": flat.get("quote_token_price_usd"),
            "quote_token_price_native_currency": None,
            "reserve_in_usd": flat.get("reserve_in_usd"),
            "pool_created_at": flat.get("pool_created_at"),
            "fdv_usd": flat.get("fdv_usd"),
            "market_cap_usd": flat.get("market_cap_usd"),
            "price_change_percentage": {
                "h1": flat.get("price_change_percentage_h1"),
                "h24": flat.get("price_change_percentage_h24"),
            },
            "transactions": {
                "h1": {"buys": 0, "sells": 0},
                "h24": {"buys": 0, "sells": 0},
            },
            "volume_usd": {"h24": flat.get("volume_usd_h24")},
        },
        "relationships": {
            "dex": {"data": {"id": flat.get("dex_id", "meteora")}},
            "base_token": {"data": {"id": flat.get("base_token_id", "")}},
            "quote_token": {"data": {"id": flat.get("quote_token_id", "")}},
        },
    }


_VIEW_BY_SUFFIX = {"trending_pools": "trending", "new_pools": "new", "pools": "top"}


class FakeGecko:
    """Stands in for the shared GeckoTerminalAsyncClient.

    The pool listings go through ``api_request`` (the library exposes no ``page``),
    so that is the surface faked; ``frame`` is served as upstream page 1 and every
    page behind it is empty, which is how a real list ends.
    """

    def __init__(self, frame=None, error=None):
        self.frame = frame
        self.error = error
        self.calls: list[tuple] = []
        self.pages: list[int] = []

    def _rows(self):
        if self.frame is None:
            return []
        rows = (
            self.frame.to_dict("records")
            if hasattr(self.frame, "to_dict")
            else list(self.frame)
        )
        return [api_row(r) for r in rows]

    async def api_request(self, method, path, params=None):
        parts = path.split("/")
        network = parts[1]
        if parts[-1] == "dexes":
            self.calls.append(("dexes", network))
        elif parts[-2] == "tokens" or (len(parts) > 3 and parts[2] == "tokens"):
            self.calls.append(("token", network, parts[3]))
        else:
            self.calls.append((_VIEW_BY_SUFFIX.get(parts[-1], parts[-1]), network))
        page = int((params or {}).get("page", 1))
        self.pages.append(page)
        if self.error:
            raise self.error
        if parts[-1] == "dexes":
            return {
                "data": [
                    {"id": "meteora", "type": "dex", "attributes": {"name": "Meteora"}}
                ]
            }
        return {"data": self._rows() if page == 1 else []}

    async def get_multiple_pools_by_network(self, n, addresses):
        self.calls.append(("multi", n, tuple(addresses)))
        if self.error:
            raise self.error
        return self.frame

    async def get_pool_by_network_address(self, n, a):
        self.calls.append(("address", n, a))
        if self.error:
            raise self.error
        return self.frame


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
    # Clamped to _POOL_LIST_MAX, so the whole 60 rows the upstream has come back.
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


# ── gateway rows filled from GeckoTerminal ──


def test_gateway_gaps_are_filled_from_gecko(fake_gecko):
    """Orca reports TVL and nothing else; the browser's other columns come from gecko."""
    client = fake_gecko(pd.DataFrame([gecko_row(dex_id="orca")]))
    clmm = FakeClmm(
        {"pools": [gateway_row(connector="orca", volume_24h=None, apr=None)]}
    )
    pool = run(pool_data.list_gateway_pools(FakeClient(clmm), "orca"))[0]
    assert pool["volume_24h"] == 4_200_000.0
    assert pool["price_change_24h"] == -3.5
    # The connector's own TVL stands; gecko's only fills a gap.
    assert pool["reserve_usd"] == 4_500_000.0
    assert ("multi", "solana", (POOL,)) in client.calls


def test_missing_gateway_yield_is_estimated_from_volume_and_fee_tier(fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row(dex_id="orca")]))
    clmm = FakeClmm(
        {"pools": [gateway_row(connector="orca", volume_24h=None, apr=None)]}
    )
    pool = run(pool_data.list_gateway_pools(FakeClient(clmm), "orca"))[0]
    # 4.2M of volume at 0.2% over 4.5M of TVL.
    fees = 4_200_000.0 * 0.2 / 100
    assert pool["fees_24h"] == pytest.approx(fees)
    assert pool["apr"] == pytest.approx(fees / 4_500_000.0 * 100)
    assert pool["apy"] > pool["apr"]
    assert pool["apr_estimated"] is True


def test_a_reported_yield_is_never_overwritten(fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row()]))
    clmm = FakeClmm({"pools": [gateway_row(volume_24h=None)]})
    pool = run(pool_data.list_gateway_pools(FakeClient(clmm), "meteora"))[0]
    assert pool["apr"] == 42.5
    assert "apr_estimated" not in pool


def test_a_reported_volume_is_never_overwritten(fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row()]))
    clmm = FakeClmm({"pools": [gateway_row()]})
    pool = run(pool_data.list_gateway_pools(FakeClient(clmm), "meteora"))[0]
    assert pool["volume_24h"] == 1_200_000.0


def test_a_failed_lookup_leaves_the_rows_alone(fake_gecko):
    fake_gecko(error=RuntimeError("gecko down"))
    clmm = FakeClmm({"pools": [gateway_row(volume_24h=None, apr=None)]})
    pool = run(pool_data.list_gateway_pools(FakeClient(clmm), "orca"))[0]
    assert pool["volume_24h"] is None
    assert pool["reserve_usd"] == 4_500_000.0


def test_the_filled_page_is_what_gets_cached(fake_gecko):
    """The extra request is spent once per page, not once per viewer of it."""
    client = fake_gecko(pd.DataFrame([gecko_row(dex_id="orca")]))
    clmm = FakeClmm({"pools": [gateway_row(connector="orca", volume_24h=None)]})
    gateway_client = FakeClient(clmm)
    run(pool_data.list_gateway_pools(gateway_client, "orca"))
    second = run(pool_data.list_gateway_pools(gateway_client, "orca"))
    assert len(clmm.calls) == 1
    assert len([c for c in client.calls if c[0] == "multi"]) == 1
    assert second[0]["volume_24h"] == 4_200_000.0


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
        monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
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


# ── paging, dex filter and favourites ──


@pytest.fixture
def paged_gecko(monkeypatch):
    """Install a fake whose upstream pages differ, and take it away again."""

    def install(pages):
        client = PagedGecko(pages)
        monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)
        return client

    return install


class PagedGecko(FakeGecko):
    """A fake whose upstream pages differ, so a window can be told from a repeat."""

    def __init__(self, pages):
        super().__init__()
        self.by_page = pages
        self.scopes: list = []
        # From this upstream page on, the fake answers like a rate limit.
        self.fail_from = None

    def _rows(self):
        return []

    async def api_request(self, method, path, params=None):
        page = int((params or {}).get("page", 1))
        parts = path.split("/")
        self.scopes.append(parts[3] if len(parts) > 3 and parts[2] == "dexes" else None)
        self.pages.append(page)
        self.calls.append(("page", page))
        if self.fail_from is not None and page >= self.fail_from:
            raise RuntimeError("429 rate limited")
        rows = self.by_page.get(page, [])
        return {"data": [api_row(r) for r in rows]}


# Base58 has no 0, O, I or l, and the address guard knows it — so a synthetic
# address has to be built out of the real alphabet, not zero-padded.
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def addr(i):
    """A distinct, valid pool address, so a page's identity is checkable."""
    return POOL[:-2] + _B58[i // 58] + _B58[i % 58]


def _rows(count, dex_id="meteora", start=0):
    return [gecko_row(dex_id=dex_id, address=addr(i + start)) for i in range(count)]


def test_a_page_of_20_maps_onto_the_upstream_page_that_holds_it(paged_gecko):
    """Gecko pages are a fixed 20, so page 3 must not re-walk pages 1 and 2."""
    client = paged_gecko({3: _rows(20, start=40)})
    result = run(pool_data.list_gecko_pools_page("solana", limit=20, page=3))
    assert client.pages == [3], "an unfiltered page is one upstream request"
    assert len(result["pools"]) == 20


def test_paging_cuts_the_window_out_of_the_upstream_pages(paged_gecko):
    client = paged_gecko({1: _rows(20, start=0), 2: _rows(20, start=20)})
    # 10 rows a page: the second half of upstream page 1.
    second = run(pool_data.list_gecko_pools_page("solana", limit=10, page=2))
    assert [p["address"] for p in second["pools"]] == [addr(i) for i in range(10, 20)]
    assert second["has_more"] is True


def test_has_more_is_false_at_the_end_of_the_list(paged_gecko):
    client = paged_gecko({1: _rows(5)})
    result = run(pool_data.list_gecko_pools_page("solana", limit=20, page=1))
    assert len(result["pools"]) == 5 and result["has_more"] is False


def test_the_dex_filter_keeps_only_the_venues_asked_for(paged_gecko):
    client = paged_gecko(
        {1: _rows(10, dex_id="meteora") + _rows(10, dex_id="pumpswap", start=50)}
    )
    result = run(
        pool_data.list_gecko_pools_page("solana", limit=20, page=1, dexes=["meteora"])
    )
    assert {p["dex_id"] for p in result["pools"]} == {"meteora"}
    assert len(result["pools"]) == 10


def test_the_dex_filter_walks_pages_to_fill_one_screen(paged_gecko):
    """Matching rows are scattered upstream, so a filtered page is a walk."""
    client = paged_gecko(
        {
            1: _rows(19, dex_id="pumpswap") + _rows(1, dex_id="meteora", start=90),
            2: _rows(19, dex_id="pumpswap", start=20) + _rows(1, "meteora", start=91),
        }
    )
    result = run(
        pool_data.list_gecko_pools_page("solana", limit=2, page=1, dexes=["meteora"])
    )
    assert len(result["pools"]) == 2
    assert client.pages == [1, 2]


def test_an_empty_dex_filter_is_no_filter(fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row(dex_id="pumpswap")]))
    result = run(pool_data.list_gecko_pools_page("solana", dexes=["", "  "]))
    assert len(result["pools"]) == 1


def test_a_nan_never_reaches_the_wire(route_client, fake_gecko):
    """pandas answers NaN for a missing figure, and NaN is not JSON.

    FastAPI refuses to encode one and the whole listing 500s, which is exactly how
    the trending view broke — so it is settled in decoration, not left to the route.
    """
    row = gecko_row()
    row["market_cap_usd"] = float("nan")
    row["reserve_in_usd"] = float("nan")
    fake_gecko(pd.DataFrame([row]))
    r = route_client().get("/servers/srv/dex/pools")
    assert r.status_code == 200
    pool = r.json()["pools"][0]
    assert pool["market_cap_usd"] is None and pool["reserve_usd"] is None


def test_gateway_numbers_arrive_as_numbers_not_strings():
    """Gateway answers APR and TVL as strings; a string has no ``.toFixed``."""
    clmm = FakeClmm(
        {"pools": [gateway_row(apr="0.2724", liquidity="861007.27", bin_step="4")]}
    )
    pool = run(pool_data.list_gateway_pools(FakeClient(clmm), "meteora"))[0]
    assert pool["apr"] == pytest.approx(0.2724)
    assert pool["reserve_usd"] == pytest.approx(861007.27)
    assert pool["bin_step"] == 4


def test_gateway_paging_is_zero_based_upstream():
    clmm = FakeClmm({"pools": []})
    run(pool_data.list_gateway_pools(FakeClient(clmm), "meteora", page=3))
    assert clmm.calls[0]["page"] == 2


def test_gateway_pages_are_cached_separately():
    clmm = FakeClmm({"pools": [gateway_row()]})
    client = FakeClient(clmm)
    run(pool_data.list_gateway_pools(client, "meteora", page=1))
    run(pool_data.list_gateway_pools(client, "meteora", page=1))
    assert len(clmm.calls) == 1
    run(pool_data.list_gateway_pools(client, "meteora", page=2))
    assert len(clmm.calls) == 2


def test_favourites_are_fetched_by_address_in_the_saved_order(fake_gecko):
    a, b = addr(1), addr(2)
    client = fake_gecko(pd.DataFrame([gecko_row(address=b), gecko_row(address=a)]))
    pools = run(pool_data.fetch_pools_by_addresses("solana-mainnet-beta", [a, b]))
    assert [p["address"] for p in pools] == [a, b]
    assert client.calls[0][0] == "multi"


def test_favourites_ignore_junk_addresses(fake_gecko):
    client = fake_gecko(pd.DataFrame([gecko_row()]))
    assert run(pool_data.fetch_pools_by_addresses("solana", ["", "nope!"])) == []
    assert client.calls == []


def test_the_dex_filter_options_come_from_the_chain(route_client, fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row()]))
    r = route_client().get("/servers/srv/dex/dexes?network=solana-mainnet-beta")
    assert r.status_code == 200
    assert r.json()["dexes"] == [{"id": "meteora", "name": "Meteora"}]


def test_favourites_route_answers_the_saved_pools(route_client, fake_gecko):
    fake_gecko(pd.DataFrame([gecko_row()]))
    r = route_client().get(f"/servers/srv/dex/pools-by-address?addresses={POOL}")
    assert r.status_code == 200
    assert r.json()["pools"][0]["address"] == POOL


def test_favourites_route_is_not_read_as_a_pool_address(route_client, fake_gecko):
    """It is declared above ``/dex/pools/{pool_address}`` for exactly this reason."""
    fake_gecko(pd.DataFrame())
    r = route_client().get("/servers/srv/dex/pools-by-address?addresses=")
    assert r.status_code == 200 and r.json()["pools"] == []


def test_one_venues_top_pools_use_the_venue_endpoint(paged_gecko):
    """A scoped listing is one request, not a walk over the whole chain."""
    client = paged_gecko({1: _rows(20, dex_id="meteora")})
    result = run(
        pool_data.list_gecko_pools_page("solana", view="top", dexes=["meteora"])
    )
    assert len(result["pools"]) == 20
    assert client.pages == [1], "the venue's own listing is already scoped"
    assert client.scopes == ["meteora"]


def test_several_venues_top_pools_are_merged_from_each_venues_listing(paged_gecko):
    """Filtering the chain-wide list would answer almost nothing — one venue
    dominates it — so each venue's own top listing is read and merged."""
    client = paged_gecko({1: _rows(5, dex_id="meteora")})
    result = run(
        pool_data.list_gecko_pools_page("solana", view="top", dexes=["meteora", "orca"])
    )
    assert client.scopes == ["meteora", "orca"]
    assert len(result["pools"]) == 10  # both venues' rows, not an intersection


def test_a_merged_venue_listing_is_ranked_by_volume(paged_gecko):
    quiet = gecko_row(dex_id="meteora", address=addr(1))
    quiet["volume_usd_h24"] = "1000.0"
    busy = gecko_row(dex_id="orca", address=addr(2))
    busy["volume_usd_h24"] = "9000000.0"
    client = paged_gecko({})
    client.by_page = {1: [quiet]}

    async def scoped(method, path, params=None):
        parts = path.split("/")
        venue = parts[3]
        client.scopes.append(venue)
        return {"data": [api_row(busy if venue == "orca" else quiet)]}

    client.api_request = scoped
    result = run(
        pool_data.list_gecko_pools_page("solana", view="top", dexes=["meteora", "orca"])
    )
    assert [p["dex_id"] for p in result["pools"]] == ["orca", "meteora"]


def test_a_venue_whose_listing_fails_does_not_take_the_others_with_it(paged_gecko):
    client = paged_gecko({})

    async def half_broken(method, path, params=None):
        venue = path.split("/")[3]
        client.scopes.append(venue)
        if venue == "orca":
            raise RuntimeError("429 rate limited")
        return {"data": [api_row(gecko_row(dex_id="meteora"))]}

    client.api_request = half_broken
    result = run(
        pool_data.list_gecko_pools_page("solana", view="top", dexes=["meteora", "orca"])
    )
    assert [p["dex_id"] for p in result["pools"]] == ["meteora"]


def test_trending_never_uses_the_venue_endpoint(paged_gecko):
    """There is no venue-scoped *trending*, and silently serving Top instead lies."""
    client = paged_gecko({1: _rows(20, dex_id="meteora")})
    run(pool_data.list_gecko_pools_page("solana", view="trending", dexes=["meteora"]))
    assert client.scopes[0] is None


def test_a_mid_walk_failure_keeps_what_was_already_found(paged_gecko):
    """A rate limit on page 3 must not blank the rows pages 1 and 2 produced."""
    client = paged_gecko({1: _rows(20, dex_id="meteora")})
    client.fail_from = 2
    # 30 a page, so one upstream page is not enough and the walk goes on to fail.
    result = run(
        pool_data.list_gecko_pools_page(
            "solana", view="trending", limit=30, dexes=["meteora"]
        )
    )
    assert client.pages == [1, 2]
    assert len(result["pools"]) == 20
    assert result["has_more"] is False


def test_a_first_page_failure_is_still_empty(paged_gecko):
    client = paged_gecko({})
    client.fail_from = 1
    result = run(pool_data.list_gecko_pools_page("solana", dexes=["meteora"]))
    assert result == {"pools": [], "has_more": False}


# ── surviving a throttled upstream ──
#
# GeckoTerminal's free tier is ~30 requests a minute per IP, shared by every viewer,
# the candle poll loop and this browser at once — so a 429 is a routine event, not
# an exceptional one. What it must never produce is "No pools found", which reads as
# a fact about the chain rather than about the request.


def test_a_throttled_refresh_serves_the_last_good_rows(paged_gecko):
    """A 429 falls back to the cached copy instead of emptying the table."""
    client = paged_gecko({1: _rows(20)})
    first = run(pool_data.list_gecko_pools_page("solana", limit=20, page=1))
    assert len(first["pools"]) == 20

    # Past the TTL, so the next call really does go upstream — and is throttled.
    for key in list(pool_data._gecko_page_cache):
        rows = pool_data._gecko_page_cache[key][1]
        pool_data._gecko_page_cache[key] = (0.0, rows)
    client.fail_from = 1

    again = run(pool_data.list_gecko_pools_page("solana", limit=20, page=1))
    assert [p["address"] for p in again["pools"]] == [
        p["address"] for p in first["pools"]
    ], "a throttled refresh must answer with the stale rows, not an empty table"


def test_a_throttled_first_request_is_still_empty(paged_gecko):
    """With nothing cached there is nothing to fall back to — and no invention."""
    client = paged_gecko({1: _rows(20)})
    client.fail_from = 1
    result = run(pool_data.list_gecko_pools_page("solana", limit=20, page=1))
    assert result == {"pools": [], "has_more": False}


def test_a_rate_limit_is_not_retried(paged_gecko):
    """Backoff cannot clear a per-minute window; retrying only spends more of it."""
    client = paged_gecko({1: _rows(20)})
    client.fail_from = 1
    run(pool_data.list_gecko_pools_page("solana", limit=20, page=1))
    assert client.pages == [1], "a 429 is a final answer, not something to retry"


def test_a_venue_with_nothing_trending_falls_back_to_its_own_listing(paged_gecko):
    """Filtering Trending by a venue that is not in it must not answer 'no pools'.

    The venue has pools; they are simply not in *this* ranking. The scoped listing
    is the query the user meant by picking it.
    """
    client = paged_gecko({1: _rows(20, dex_id="pumpswap")})
    result = run(
        pool_data.list_gecko_pools_page(
            "solana", view="trending", limit=20, dexes=["orca"]
        )
    )
    assert result["pools"], "a venue-scoped retry should have found rows"
    assert "orca" in client.scopes, "the fallback must ask for the venue's own listing"


# ── the network ids Gateway actually reports ──


@pytest.mark.parametrize(
    "gateway_network, gecko_chain",
    [
        ("solana-mainnet-beta", "solana"),
        ("ethereum-mainnet", "eth"),
        ("ethereum-base", "base"),
        ("ethereum-robinhoodchain", "robinhood"),
        ("ethereum-unichain", "unichain"),
        ("ethereum-avalanche", "avax"),
        ("ethereum-polygon", "polygon_pos"),
    ],
)
def test_gateway_network_ids_map_to_a_gecko_chain(gateway_network, gecko_chain):
    """Gateway names networks `chain-network`; this table has to speak that.

    It previously held ids Gateway never emits (``base-mainnet``, ``arbitrum-one``),
    so every EVM pool fell through to "no gecko chain" and rendered untradable.
    """
    assert pool_data.get_gecko_network(gateway_network) == gecko_chain


def test_every_discovered_chain_round_trips_back_to_a_known_network():
    """`decorate_pool` marks a pool tradable by looking its network up again.

    A value that does not round-trip marks a perfectly reachable chain untradable —
    which is what used to happen to BSC, Avalanche and Optimism.
    """
    for chain, network in pool_data.GECKO_TO_GATEWAY_NETWORK.items():
        assert (
            network in pool_data.NETWORK_TO_GECKO
        ), f"{chain} -> {network} is a dead end"
        assert pool_data.NETWORK_TO_GECKO[network] == chain


# ── the chains the browser may offer ──


class FakeGatewayNetworks:
    """Gateway's `/gateway/networks`, which names networks `chain-network`."""

    def __init__(self, networks=None, error=None):
        self._networks = networks
        self._error = error

    async def list_networks(self):
        if self._error:
            raise self._error
        return {"networks": self._networks}


class FakeNetworkClient:
    def __init__(self, gateway):
        self.gateway = gateway


def _net(network_id, chain, network):
    return {"network_id": network_id, "chain": chain, "network": network}


def _chains_client(route_client, networks=None, error=None):
    gateway = FakeGatewayNetworks(networks, error)
    return route_client(client=FakeNetworkClient(gateway))


def test_chains_offers_what_gateway_has_and_gecko_indexes(route_client):
    client = _chains_client(
        route_client,
        [
            _net("solana-mainnet-beta", "solana", "mainnet-beta"),
            _net("ethereum-base", "ethereum", "base"),
            _net("ethereum-robinhoodchain", "ethereum", "robinhoodchain"),
        ],
    )
    chains = client.get("/servers/srv/dex/chains").json()["chains"]
    assert [c["network_id"] for c in chains] == [
        "solana-mainnet-beta",
        "ethereum-base",
        "ethereum-robinhoodchain",
    ], "Solana leads because it is the default; the rest sort by label"
    assert [c["label"] for c in chains] == ["Solana", "Base", "Robinhood"]


def test_chains_drops_testnets(route_client):
    """There is nothing to discover on a testnet — Gecko does not index one."""
    client = _chains_client(
        route_client,
        [
            _net("solana-mainnet-beta", "solana", "mainnet-beta"),
            _net("solana-devnet", "solana", "devnet"),
            _net("ethereum-sepolia", "ethereum", "sepolia"),
            _net(
                "ethereum-robinhoodchain-testnet", "ethereum", "robinhoodchain-testnet"
            ),
        ],
    )
    chains = client.get("/servers/srv/dex/chains").json()["chains"]
    assert [c["network_id"] for c in chains] == ["solana-mainnet-beta"]


def test_chains_drops_networks_gecko_cannot_index(route_client):
    client = _chains_client(
        route_client,
        [
            _net("solana-mainnet-beta", "solana", "mainnet-beta"),
            _net("ethereum-madeupchain", "ethereum", "madeupchain"),
        ],
    )
    chains = client.get("/servers/srv/dex/chains").json()["chains"]
    assert [c["network_id"] for c in chains] == ["solana-mainnet-beta"]


def test_chains_falls_back_to_solana_when_gateway_is_down(route_client):
    """The browser must always have at least the chain it defaults to."""
    client = _chains_client(route_client, error=RuntimeError("gateway is down"))
    r = client.get("/servers/srv/dex/chains")
    assert r.status_code == 200
    assert [c["network_id"] for c in r.json()["chains"]] == ["solana-mainnet-beta"]


def test_chains_needs_server_access(route_client):
    client = route_client(allowed=False)
    assert client.get("/servers/srv/dex/chains").status_code == 403


# ── the Orca source (handlers/dex/orca_api.py + list_orca_pools) ──
#
# Gateway proxies this very API and nulls volume, fees, price and yield on the way
# — and ignores its own `page` argument for this connector, so every page of the
# Orca tab was page one. These pin the shape that replaced it, against the payload
# api.orca.so actually returns.


def orca_row(**over):
    row = {
        "address": POOL,
        "tickSpacing": 4,
        "feeRate": 400,  # millionths of the trade: 0.04%
        "price": "75.4699414017526812",
        "tvlUsdc": "25914246.8432301700048600",
        "tokenMintA": SOL,
        "tokenMintB": USDC,
        "tokenA": {"address": SOL, "symbol": "SOL", "decimals": 9},
        "tokenB": {"address": USDC, "symbol": "USDC", "decimals": 6},
        "stats": {
            "24h": {
                "volume": "51745436.73117720",
                "fees": "20697.75405187479438737000",
                "yieldOverTvl": "0.00079852453975813900",
                "priceDelta": "-0.005889007438397060571184406001499000",
            }
        },
    }
    row.update(over)
    return row


@pytest.fixture
def fake_orca(monkeypatch):
    """Stand in for api.orca.so. No test in this module may reach the real one."""

    class FakeOrca:
        def __init__(self, rows=None, error=None):
            self.rows = rows if rows is not None else []
            self.error = error
            self.calls: list[dict] = []

        async def fetch_whirlpools(self, size, search=None, **kw):
            self.calls.append({"size": size, "search": search, **kw})
            if self.error:
                raise self.error
            return self.rows

    def build(rows=None, error=None):
        fake = FakeOrca(rows, error)
        monkeypatch.setattr(
            pool_data.orca_api, "fetch_whirlpools", fake.fetch_whirlpools
        )
        return fake

    return build


def test_orca_normalization_maps_every_column_gateway_nulls():
    """The whole point: TVL, volume, fees, price and yield in one row."""
    pool = pool_data.normalize_pool_data(orca_row(), source="orca")
    assert pool["source"] == "orca"
    assert pool["address"] == POOL
    assert pool["dex_id"] == "orca"
    assert pool["reserve_usd"] == pytest.approx(25_914_246.84, abs=0.01)
    assert pool["volume_24h"] == pytest.approx(51_745_436.73, abs=0.01)
    assert pool["fees_24h"] == pytest.approx(20_697.754, abs=0.01)
    assert pool["current_price"] == pytest.approx(75.4699, abs=1e-4)


def test_orca_fee_rate_is_millionths_of_the_trade():
    """400 is 0.04%, the tier Orca's own UI shows — not 400% and not 4%."""
    pool = pool_data.normalize_pool_data(orca_row(), source="orca")
    assert pool["base_fee_percentage"] == pytest.approx(0.04)


def test_orca_tick_spacing_lands_in_the_bin_step_column():
    """Both answer the same question: how finely can I place a position here."""
    pool = pool_data.normalize_pool_data(orca_row(), source="orca")
    assert pool["bin_step"] == 4


def test_orca_yield_is_realized_and_not_marked_estimated():
    """Orca reports fees *and* rewards over TVL, so nothing is derived from volume."""
    pool = pool_data.normalize_pool_data(orca_row(), source="orca")
    # 0.00079852 of TVL per day, as a percent, then 365 compoundings of it.
    assert pool["apr"] == pytest.approx(0.0798525, abs=1e-6)
    assert pool["apy"] == pytest.approx(33.63, abs=0.5)
    assert pool["apr_estimated"] is False


def test_orca_price_delta_is_a_ratio_rendered_as_a_percent():
    """Orca says -0.0059; the column says -0.59%, like gecko's already does."""
    pool = pool_data.normalize_pool_data(orca_row(), source="orca")
    assert pool["price_change_24h"] == pytest.approx(-0.5889, abs=1e-3)


def test_orca_pool_with_no_stats_keeps_its_other_columns():
    """A brand-new whirlpool has no 24h window yet; that is not a broken row."""
    pool = pool_data.normalize_pool_data(orca_row(stats={}), source="orca")
    assert pool["reserve_usd"] == pytest.approx(25_914_246.84, abs=0.01)
    assert pool["volume_24h"] is None
    assert pool["apr"] is None


def test_orca_pools_are_decorated_like_any_other_source(fake_orca):
    fake_orca([orca_row()])
    pools, _ = run(pool_data.list_orca_pools())
    assert pools[0]["lp_supported"] is True
    assert pools[0]["tradable"] is True
    assert pools[0]["gateway_network"] == "solana-mainnet-beta"
    assert pools[0]["trading_pair"] == f"{SOL}-USDC"


def test_orca_pages_are_sliced_out_of_one_snapshot(fake_orca):
    """Orca paginates by cursor and the browser by number, so one fetch serves both."""
    fake = fake_orca([orca_row(address=f"pool{i}") for i in range(50)])
    first, more_first = run(pool_data.list_orca_pools(limit=20, page=1))
    second, more_second = run(pool_data.list_orca_pools(limit=20, page=2))
    third, more_third = run(pool_data.list_orca_pools(limit=20, page=3))
    assert [p["address"] for p in first] == [f"pool{i}" for i in range(20)]
    assert [p["address"] for p in second] == [f"pool{i}" for i in range(20, 40)]
    assert [p["address"] for p in third] == [f"pool{i}" for i in range(40, 50)]
    assert (more_first, more_second, more_third) == (True, True, False)
    # Three pages, one upstream request — the Gateway path spent one per page and
    # a GeckoTerminal one on top.
    assert len(fake.calls) == 1


def test_orca_has_more_is_exact_not_a_full_page_guess(fake_orca):
    """A snapshot that ends exactly on a page boundary has no next page."""
    fake_orca([orca_row(address=f"pool{i}") for i in range(20)])
    _, more = run(pool_data.list_orca_pools(limit=20, page=1))
    assert more is False


def test_orca_search_goes_upstream_as_a_query(fake_orca):
    fake = fake_orca([orca_row()])
    run(pool_data.list_orca_pools(search="BONK"))
    assert fake.calls[0]["search"] == "BONK"


def test_blank_orca_search_is_no_search(fake_orca):
    fake = fake_orca([orca_row()])
    run(pool_data.list_orca_pools(search="   "))
    assert fake.calls[0]["search"] is None


def test_orca_searches_are_cached_apart_from_the_unfiltered_list(fake_orca):
    fake = fake_orca([orca_row()])
    run(pool_data.list_orca_pools())
    run(pool_data.list_orca_pools(search="BONK"))
    assert [c["search"] for c in fake.calls] == [None, "BONK"]


def test_orca_rows_with_no_address_are_dropped(fake_orca):
    fake_orca([orca_row(address=""), orca_row()])
    pools, _ = run(pool_data.list_orca_pools())
    assert len(pools) == 1


def test_orca_cached_rows_are_copies_a_caller_cannot_poison(fake_orca):
    fake_orca([orca_row()])
    first, _ = run(pool_data.list_orca_pools())
    first[0]["address"] = "mutated"
    again, _ = run(pool_data.list_orca_pools())
    assert again[0]["address"] == POOL


def test_orca_failure_is_none_so_the_caller_can_fall_back(fake_orca):
    """`[]` would render "no pools" for a venue with thousands; None means fall back."""
    fake_orca(error=RuntimeError("429 rate limited"))
    assert run(pool_data.list_orca_pools()) is None


def test_orca_serves_a_stale_snapshot_before_giving_up(fake_orca):
    """A rate limit should not empty a table that was correct a minute ago."""
    fake_orca([orca_row()])
    run(pool_data.list_orca_pools())
    fresh = pool_data._orca_pool_list_cache[("",)][1]
    pool_data._orca_pool_list_cache[("",)] = (0.0, fresh)
    fake_orca(error=RuntimeError("429 rate limited"))
    pools, _ = run(pool_data.list_orca_pools())
    assert [p["address"] for p in pools] == [POOL]


def test_orca_source_lists_pools(route_client, fake_orca):
    fake_orca([orca_row()])
    r = route_client().get("/servers/srv/dex/pools?source=orca")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "orca"
    assert body["pools"][0]["address"] == POOL
    assert body["pools"][0]["volume_24h"] == pytest.approx(51_745_436.73, abs=0.01)


def test_orca_route_needs_no_gateway_client(route_client, fake_orca):
    """A public API, so a server whose Gateway is down still browses Orca."""
    fake_orca([orca_row()])
    client = route_client(client_error=RuntimeError("gateway is down"))
    r = client.get("/servers/srv/dex/pools?source=orca")
    assert r.status_code == 200
    assert r.json()["pools"][0]["address"] == POOL


def test_orca_route_falls_back_to_gateway_when_orca_is_down(route_client, fake_orca):
    fake_orca(error=RuntimeError("403 cloudflare"))
    clmm = FakeClmm({"pools": [gateway_row(connector="orca")]})
    r = route_client(client=FakeClient(clmm)).get("/servers/srv/dex/pools?source=orca")
    assert r.status_code == 200
    body = r.json()
    # Reported as the source that actually served it, not the one that was asked for.
    assert body["source"] == "gateway"
    assert clmm.calls[0]["connector"] == "orca"
    assert body["pools"][0]["address"] == POOL


def test_orca_route_needs_server_access(route_client, fake_orca):
    fake_orca([orca_row()])
    client = route_client(allowed=False)
    assert client.get("/servers/srv/dex/pools?source=orca").status_code == 403
