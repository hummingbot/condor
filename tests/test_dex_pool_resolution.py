"""Tests for FEAT-042: pool resolution serves the LP panel and the candle path.

Two things are being protected here.

**The candle path must not notice the refactor.** ``fetch_token_top_pool`` used to
do the GeckoTerminal query itself; it is now the address form of
``fetch_token_top_pool_info``, and ``dex_candles._resolve_pool`` still calls the
former. Same address, same cache entry, same "a failure is not cached but an empty
answer is" behavior — ``tests/test_dex_candles.py`` covers the address contract,
these cover the delegation and the shared cache.

**The LP panel cannot guess the venue.** An ``lp_executor`` needs
``lp_provider`` as ``dex/clmm``, and the API rejects anything else. The deepest pool
for a pair is frequently *not* a CLMM pool — ``meteora-damm-v2`` and
``uniswap-v4-ethereum`` are real answers from the live API — so
``lp_provider_for_dex`` has to say "no" rather than brand-match its way to a payload
that fails.
"""

import asyncio

import pytest

from condor import pool_data


def run(coro):
    return asyncio.run(coro)


class _FakeRows:
    """Stands in for the DataFrame ``get_top_pools_by_network_token`` returns."""

    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, _orient):
        return self._rows


def _token_row(**over):
    row = {
        "name": "BONK / SOL",
        "address": "bonk_sol_pool",
        "dex_id": "meteora",
        "base_token_price_usd": "0.000002",
        "quote_token_price_usd": "100.0",
        "volume_usd_h24": 1000.0,
    }
    row.update(over)
    return row


def _search_row(name="SOL / USDC", address="sol_usdc_pool", dex="orca", **attrs):
    base = {
        "name": name,
        "address": address,
        "base_token_price_quote_token": "75.5",
        "volume_usd": {"h24": 5000.0},
    }
    base.update(attrs)
    return {
        "id": f"solana_{address}",
        "attributes": base,
        "relationships": {"dex": {"data": {"id": dex, "type": "dex"}}},
    }


@pytest.fixture(autouse=True)
def _clear_caches():
    caches = (pool_data._token_pool_cache, pool_data._pair_pool_cache)
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()


# ── The candle path is unchanged by the extraction ──


def test_the_address_form_still_returns_a_bare_address(monkeypatch):
    class _Client:
        async def get_top_pools_by_network_token(self, *_a):
            return _FakeRows([_token_row()])

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    assert (
        run(pool_data.fetch_token_top_pool("mint", "solana", "SOL")) == "bonk_sol_pool"
    )


def test_the_two_forms_share_one_cache_entry(monkeypatch):
    """The candle path and the LP panel ask the same question — once."""
    calls = []

    class _Client:
        async def get_top_pools_by_network_token(self, *_a):
            calls.append(1)
            return _FakeRows([_token_row()])

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    info = run(pool_data.fetch_token_top_pool_info("mint", "solana", "SOL"))
    address = run(pool_data.fetch_token_top_pool("mint", "solana", "SOL"))
    assert info["address"] == address == "bonk_sol_pool"
    assert len(calls) == 1


def test_a_no_match_is_cached_once_for_both_forms(monkeypatch):
    """An unlisted pair is a real answer; re-asking on every render is not."""
    calls = []

    class _Client:
        async def get_top_pools_by_network_token(self, *_a):
            calls.append(1)
            return _FakeRows([_token_row(name="BONK / USDC")])

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    assert run(pool_data.fetch_token_top_pool_info("mint", "solana", "SOL")) is None
    assert run(pool_data.fetch_token_top_pool("mint", "solana", "SOL")) == ""
    assert len(calls) == 1


def test_a_failed_lookup_is_still_not_cached_in_the_info_form(monkeypatch):
    calls = []

    class _Failing:
        async def get_top_pools_by_network_token(self, *_a):
            calls.append(1)
            raise RuntimeError("upstream exploded")

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Failing())
    assert run(pool_data.fetch_token_top_pool_info("mint", "solana", "SOL")) is None
    assert run(pool_data.fetch_token_top_pool_info("mint", "solana", "SOL")) is None
    assert len(calls) == 2


def test_a_row_without_an_address_is_skipped(monkeypatch):
    """Preserved from the address form: a row with no address is not a pool."""

    class _Client:
        async def get_top_pools_by_network_token(self, *_a):
            return _FakeRows([_token_row(address=""), _token_row(address="real_pool")])

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    assert run(pool_data.fetch_token_top_pool("mint", "solana", "SOL")) == "real_pool"


# ── What the extraction adds ──


def test_the_info_form_carries_the_venue_and_the_pair_price(monkeypatch):
    class _Client:
        async def get_top_pools_by_network_token(self, *_a):
            return _FakeRows([_token_row()])

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    info = run(
        pool_data.fetch_token_top_pool_info("mint", "solana-mainnet-beta", "SOL")
    )
    assert info["dex_id"] == "meteora"
    assert info["base_symbol"] == "BONK"
    assert info["quote_symbol"] == "SOL"
    # Base priced in quote, the scale every LP bound is expressed in — not USD.
    assert info["current_price"] == pytest.approx(0.000002 / 100.0)
    # The caller's network, not normalize_pool_data's "solana" default.
    assert info["network"] == "solana-mainnet-beta"


def test_the_price_is_none_when_gecko_reports_no_usd_prices(monkeypatch):
    """A missing price must not become 0 — the panel would auto-fill a zero range."""

    class _Client:
        async def get_top_pools_by_network_token(self, *_a):
            return _FakeRows(
                [_token_row(base_token_price_usd=None, quote_token_price_usd=None)]
            )

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    info = run(pool_data.fetch_token_top_pool_info("mint", "solana", "SOL"))
    assert info["current_price"] is None


# ── The symbol-search branch ──


def test_the_search_branch_lifts_the_venue_out_of_relationships(monkeypatch):
    """``search/pools`` reports the dex in relationships, not as a column."""

    class _Client:
        async def api_request(self, *_a, **_kw):
            return {"data": [_search_row()]}

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    info, inverted = run(pool_data.fetch_pair_top_pool_info("SOL", "USDC", "solana"))
    assert inverted is False
    assert info["dex_id"] == "orca"
    assert info["address"] == "sol_usdc_pool"
    assert info["current_price"] == pytest.approx(75.5)


def test_an_inverted_pool_reports_the_pair_as_asked(monkeypatch):
    """Asked for XRP-RLUSD against a ``RLUSD / XRP`` pool: swap sides *and* invert
    the price, or the panel would draw LP bounds on the reciprocal scale."""

    class _Client:
        async def api_request(self, *_a, **_kw):
            return {
                "data": [
                    _search_row(
                        name="RLUSD / XRP",
                        address="rlusd_xrp",
                        dex="xrpl-amm",
                        base_token_price_quote_token="0.4",
                    )
                ]
            }

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    info, inverted = run(pool_data.fetch_pair_top_pool_info("XRP", "RLUSD", "xrpl"))
    assert inverted is True
    assert (info["base_symbol"], info["quote_symbol"]) == ("XRP", "RLUSD")
    assert info["current_price"] == pytest.approx(1 / 0.4)


def test_the_search_address_form_keeps_its_tuple_contract(monkeypatch):
    class _Client:
        async def api_request(self, *_a, **_kw):
            return {"data": [_search_row()]}

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    assert run(pool_data.fetch_pair_top_pool("SOL", "USDC", "solana")) == (
        "sol_usdc_pool",
        False,
    )


def test_the_search_forms_share_one_cache_entry(monkeypatch):
    calls = []

    class _Client:
        async def api_request(self, *_a, **_kw):
            calls.append(1)
            return {"data": [_search_row()]}

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    run(pool_data.fetch_pair_top_pool_info("SOL", "USDC", "solana"))
    run(pool_data.fetch_pair_top_pool("SOL", "USDC", "solana"))
    assert len(calls) == 1


def test_the_deepest_matching_search_pool_wins(monkeypatch):
    """Ticker collisions are real, so volume decides — not result order."""

    class _Client:
        async def api_request(self, *_a, **_kw):
            return {
                "data": [
                    _search_row(address="thin", volume_usd={"h24": 10.0}),
                    _search_row(address="deep", volume_usd={"h24": 900.0}),
                    _search_row(address="mid", volume_usd={"h24": 500.0}),
                ]
            }

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    info, _ = run(pool_data.fetch_pair_top_pool_info("SOL", "USDC", "solana"))
    assert info["address"] == "deep"


# ── resolve_pool_info dispatches like the candle path ──


def test_an_address_base_resolves_through_the_token_branch(monkeypatch):
    seen = {}

    async def _token(mint, network, quote):
        seen["token"] = (mint, network, quote)
        return {"address": "from_token"}

    async def _pair(*_a):
        raise AssertionError("the search branch must not be used for a mint base")

    monkeypatch.setattr(pool_data, "fetch_token_top_pool_info", _token)
    monkeypatch.setattr(pool_data, "fetch_pair_top_pool_info", _pair)
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    info = run(pool_data.resolve_pool_info("solana-mainnet-beta", f"{mint}-SOL"))
    assert info == {"address": "from_token"}
    assert seen["token"] == (mint, "solana-mainnet-beta", "SOL")


def test_a_ticker_base_resolves_through_the_search_branch(monkeypatch):
    async def _token(*_a):
        raise AssertionError("a ticker is not a mint GeckoTerminal can look up")

    async def _pair(base, quote, network):
        return {"address": "from_search", "asked": (base, quote, network)}, False

    monkeypatch.setattr(pool_data, "fetch_token_top_pool_info", _token)
    monkeypatch.setattr(pool_data, "fetch_pair_top_pool_info", _pair)
    info = run(pool_data.resolve_pool_info("solana-mainnet-beta", "SOL-USDC"))
    assert info["address"] == "from_search"
    assert info["asked"] == ("SOL", "USDC", "solana-mainnet-beta")


@pytest.mark.parametrize("pair", ["", "SOL", "-USDC", "SOL-", "   "])
def test_a_pair_missing_a_side_resolves_to_nothing(monkeypatch, pair):
    """No request is spent on a pair that cannot name a pool."""

    async def _boom(*_a):
        raise AssertionError("no lookup should happen")

    monkeypatch.setattr(pool_data, "fetch_token_top_pool_info", _boom)
    monkeypatch.setattr(pool_data, "fetch_pair_top_pool_info", _boom)
    assert run(pool_data.resolve_pool_info("solana-mainnet-beta", pair)) is None


# ── lp_provider_for_dex refuses to guess ──


@pytest.mark.parametrize(
    "dex_id,network,expected",
    [
        # Every id below was observed coming back from the live GeckoTerminal
        # search for SOL-USDC, BONK-SOL, JUP-USDC, WETH-USDC and WBNB-USDT.
        ("meteora", "solana-mainnet-beta", "meteora/clmm"),
        ("orca", "solana-mainnet-beta", "orca/clmm"),
        ("raydium-clmm", "solana-mainnet-beta", "raydium/clmm"),
        # The same venue, spelled three ways across three chains.
        ("uniswap_v3", "ethereum-mainnet", "uniswap/clmm"),
        ("uniswap-v3-base", "base-mainnet", "uniswap/clmm"),
        ("uniswap_v3_arbitrum", "arbitrum-one", "uniswap/clmm"),
        ("pancakeswap-v3-bsc", "binance-smart-chain", "pancakeswap/clmm"),
        ("pancakeswap-v3-ethereum", "ethereum-mainnet", "pancakeswap/clmm"),
        # A brand match is not a position model. `raydium` alone is the
        # constant-product AMM v4, `meteora-damm-v2` is Meteora's AMM, and
        # Uniswap v4's hooks are not the v3 CLMM the connector drives.
        ("raydium", "solana-mainnet-beta", None),
        ("meteora-damm-v2", "solana-mainnet-beta", None),
        ("uniswap-v4-ethereum", "ethereum-mainnet", None),
        ("uniswap-v2-base", "base-mainnet", None),
        ("pancakeswap_v2", "binance-smart-chain", None),
        # `uniswap-bsc` names no version, so it is not knowably v3.
        ("uniswap-bsc", "binance-smart-chain", None),
        # Right venue, wrong chain.
        ("uniswap_v3", "solana-mainnet-beta", None),
        ("meteora", "ethereum-mainnet", None),
        ("pancakeswap-v3-solana", "solana-mainnet-beta", None),
        # Not a venue Condor can LP on at all.
        ("sushiswap-v3-ethereum", "ethereum-mainnet", None),
        ("aerodrome-slipstream-3", "base-mainnet", None),
        ("humidifi", "solana-mainnet-beta", None),
        ("pumpswap", "solana-mainnet-beta", None),
        ("unknown", "solana-mainnet-beta", None),
        ("", "solana-mainnet-beta", None),
    ],
)
def test_lp_provider_only_names_a_clmm_venue_on_its_own_chain(
    dex_id, network, expected
):
    assert pool_data.lp_provider_for_dex(dex_id, network) == expected


# ── The route ──


def _call_route(monkeypatch, resolver, access=True):
    from condor.web.models import WebUser
    from condor.web.routes.market import get_dex_pool

    class _Cm:
        def has_server_access(self, user_id, name):
            return access

    monkeypatch.setattr(
        "condor.web.routes.market.get_config_manager", lambda: _Cm(), raising=True
    )
    monkeypatch.setattr(pool_data, "resolve_pool_info", resolver)
    return asyncio.run(
        get_dex_pool(
            "srv",
            connector="solana-mainnet-beta",
            trading_pair="SOL-USDC",
            user=WebUser(id=1, role="user"),
        )
    )


_REAL_POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"


def test_route_reports_the_pool_and_its_lp_provider(monkeypatch):
    async def _resolve(_net, _pair):
        return {
            "address": _REAL_POOL,
            "dex_id": "orca",
            "current_price": 75.5,
            "base_symbol": "SOL",
            "quote_symbol": "USDC",
        }

    assert _call_route(monkeypatch, _resolve) == {
        "pool_address": _REAL_POOL,
        "dex_id": "orca",
        "lp_provider": "orca/clmm",
        "lp_supported": True,
        "current_price": 75.5,
        "base_symbol": "SOL",
        "quote_symbol": "USDC",
    }


def test_route_reports_an_unsupported_venue_without_failing(monkeypatch):
    """The pool is real and chartable; it just cannot take an LP position. The
    panel's answer is to ask for a pool address by hand, so this is a 200."""

    async def _resolve(_net, _pair):
        return {
            "address": _REAL_POOL,
            "dex_id": "uniswap-v4-ethereum",
            "current_price": 1871.0,
            "base_symbol": "WETH",
            "quote_symbol": "USDC",
        }

    result = _call_route(monkeypatch, _resolve)
    assert result["lp_supported"] is False
    assert result["lp_provider"] is None
    # The address is still reported — the chart uses it even when LP cannot.
    assert result["pool_address"] == _REAL_POOL
    assert result["dex_id"] == "uniswap-v4-ethereum"


def test_route_returns_a_200_when_no_pool_is_found(monkeypatch):
    async def _resolve(_net, _pair):
        return None

    assert _call_route(monkeypatch, _resolve) == {
        "pool_address": None,
        "dex_id": None,
        "lp_provider": None,
        "lp_supported": False,
        "current_price": None,
        "base_symbol": None,
        "quote_symbol": None,
    }


def test_route_returns_a_200_when_resolution_raises(monkeypatch):
    async def _resolve(_net, _pair):
        raise RuntimeError("geckoterminal down")

    assert _call_route(monkeypatch, _resolve)["lp_supported"] is False


def test_route_refuses_an_address_that_is_not_one(monkeypatch):
    """Whatever comes back is handed to /market/candles and to the executor config,
    so a malformed address is dropped rather than passed along."""

    async def _resolve(_net, _pair):
        return {"address": "not an address", "dex_id": "orca"}

    result = _call_route(monkeypatch, _resolve)
    assert result["pool_address"] is None
    assert result["lp_supported"] is False


def test_route_rejects_a_caller_without_access(monkeypatch):
    """Refused by the ``require_server_access`` dependency (SEC-147), which
    runs before the endpoint body — so nothing is resolved. That the route
    carries it is pinned by ``tests/test_web_server_access_dependency.py``."""
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
