"""Running with no key, and running with a CoinGecko Analyst key.

Those are the only two supported setups. Keyless is the default and not a
degraded mode, so most of these assert that the DEX surface is untouched without
a key; the rest cover the one thing a key changes — the size of the budget and of
the pool browser's walk — and the failure that makes "only two setups" safe: a
key the paid host refuses (a free Demo key, a typo, a lapsed plan) falls back to
keyless instead of taking the whole DEX surface down with it.
"""

import ast
import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from condor import pool_data

_FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "agents"
    / "smart_money_flow"
    / "routines"
    / "onchain_flow.py"
)


def run(coro):
    """Drive a coroutine to completion (the suite has no async plugin)."""
    return asyncio.run(coro)


class _Client:
    """A gecko client that records its calls instead of making them."""

    def __init__(self, error=None):
        self.calls = 0
        self._error = error

    async def api_request(self, *_a, **_k):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return {"data": []}


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://pro-api.coingecko.com/api/v3/onchain/x")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts keyless and leaves the module as it found it."""
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    pool_data.reset_gecko_throttle()
    pool_data.configure_gecko_access()
    yield
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    pool_data.reset_gecko_throttle()
    pool_data.configure_gecko_access()


def with_key(monkeypatch, key="CG-secret"):
    monkeypatch.setenv("COINGECKO_API_KEY", key)
    return pool_data.configure_gecko_access()


# ── No key: the default path is untouched ──


def test_without_a_key_the_public_api_is_used():
    """No key is the supported default, and reaches GeckoTerminal directly."""
    assert pool_data.gecko_plan() == "public"
    client = pool_data._gecko_client()
    assert client.base_url == pool_data._GECKO_PUBLIC_URL
    assert not [h for h in client.client.headers if h.startswith("x-cg-")]


def test_without_a_key_the_guards_keep_their_free_tier_sizes():
    """The public budget stays held under GeckoTerminal's ~30/min."""
    assert pool_data._GECKO_RATE_LIMIT == 25
    assert pool_data._GECKO_MAX_CONCURRENCY == 4
    assert pool_data._GECKO_FILTER_WALK_PAGES == 3
    assert pool_data._GECKO_SCOPED_MAX_REQUESTS == 8


def test_a_blank_key_is_not_a_key(monkeypatch):
    """An env var left empty in .env must not send an empty auth header."""
    assert with_key(monkeypatch, "   ") == "public"
    assert pool_data._gecko_client().base_url == pool_data._GECKO_PUBLIC_URL


# ── An Analyst key ──


def test_a_key_routes_to_the_onchain_endpoints(monkeypatch):
    """The paid host, its header name, and the /onchain path prefix."""
    assert with_key(monkeypatch) == "analyst"
    client = pool_data._gecko_client()
    assert client.base_url == "https://pro-api.coingecko.com/api/v3/onchain"
    assert client.client.headers["x-cg-pro-api-key"] == "CG-secret"


def test_a_key_widens_the_budget_and_the_walk(monkeypatch):
    """The point of the key: more budget *and* a deeper pool-browser walk.

    The walk matters as much as the budget — capped at 3 pages, a venue filter
    can miss and fall back to a different query (list_gecko_pools_page).
    """
    with_key(monkeypatch)
    assert pool_data._GECKO_RATE_LIMIT == 400  # held under Analyst's 500
    assert pool_data._GECKO_MAX_CONCURRENCY == 12
    assert pool_data._GECKO_FILTER_WALK_PAGES == 10
    assert pool_data._GECKO_SCOPED_MAX_REQUESTS == 30


def test_the_walk_never_exceeds_the_page_gecko_will_serve(monkeypatch):
    """Gecko answers 401 past page 10 — that is its limit, not the budget's."""
    with_key(monkeypatch)
    assert pool_data._GECKO_FILTER_WALK_PAGES <= pool_data.GECKO_MAX_PAGE


def test_only_two_plans_exist():
    """Every plan table agrees on the same two supported setups."""
    for table in (
        pool_data._GECKO_PLAN_RATE_LIMITS,
        pool_data._GECKO_PLAN_CONCURRENCY,
        pool_data._GECKO_PLAN_FILTER_WALK,
        pool_data._GECKO_PLAN_SCOPED_MAX,
    ):
        assert set(table) == {"public", "analyst"}


# ── A key the paid host refuses ──


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_falls_back_to_keyless(monkeypatch, status):
    """A bad key must not be worse than no key.

    Without the fallback the 401 propagates, every caller degrades to an empty
    list, and the whole DEX surface reads as a chain with no pools.
    """
    with_key(monkeypatch)
    rejecting = _Client(error=_http_error(status))
    keyless = _Client()
    clients = iter([rejecting, keyless])
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: next(clients, keyless))

    assert run(pool_data.gecko_call("api_request", "GET", "networks")) == {"data": []}
    assert rejecting.calls == 1 and keyless.calls == 1
    assert pool_data.gecko_plan() == "public"


def test_a_rejected_key_resizes_the_guards(monkeypatch):
    """The fallback is not just a URL: the paid budget is no longer ours."""
    with_key(monkeypatch)
    assert pool_data._GECKO_RATE_LIMIT == 400
    pool_data._note_key_rejected()
    assert pool_data._GECKO_RATE_LIMIT == 25
    assert pool_data._GECKO_FILTER_WALK_PAGES == 3


def test_the_key_is_not_retried_once_rejected(monkeypatch):
    """Sticky for the process: re-asking spends budget to relearn the same 401."""
    with_key(monkeypatch)
    pool_data._note_key_rejected()
    client = _Client()
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)

    run(pool_data.gecko_call("api_request", "GET", "networks"))
    run(pool_data.gecko_call("api_request", "GET", "networks"))
    assert client.calls == 2  # no extra probe of the paid host
    base_url, headers = pool_data.coingecko_access()
    assert base_url == pool_data._GECKO_PUBLIC_URL
    assert headers == {}


def test_a_401_without_a_key_is_still_a_final_answer(monkeypatch):
    """Keyless, a 401 is gecko refusing page 10 — not a credential problem."""
    client = _Client(error=_http_error(401))
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)

    with pytest.raises(httpx.HTTPStatusError):
        run(pool_data.gecko_call("api_request", "GET", "networks"))
    assert client.calls == 1  # raised on the first attempt, not retried


def test_a_404_is_not_treated_as_a_bad_key(monkeypatch):
    """A pool that does not exist must not disable the key for the process."""
    with_key(monkeypatch)
    client = _Client(error=_http_error(404))
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)

    with pytest.raises(httpx.HTTPStatusError):
        run(pool_data.gecko_call("api_request", "GET", "networks"))
    assert pool_data.gecko_plan() == "analyst"


# ── Reconfiguration and reporting ──


def test_changing_the_plan_rebuilds_the_client(monkeypatch):
    """A cached client holds the old host, so it cannot survive a plan change."""
    before = pool_data._gecko_client()
    with_key(monkeypatch)
    after = pool_data._gecko_client()
    assert after is not before
    assert after.base_url != before.base_url


def test_health_names_the_plan_but_never_the_key(monkeypatch):
    """ "Throttled" reads differently on 25/min keyless than on a paid tier."""
    with_key(monkeypatch)
    health = pool_data.gecko_health()
    assert health["plan"] == "analyst"
    assert health["budget"] == 400
    assert health["key_rejected"] is False
    assert "CG-secret" not in str(health)


def test_health_reports_a_rejected_key(monkeypatch):
    """Otherwise a refused key is indistinguishable from having set none."""
    with_key(monkeypatch)
    pool_data._note_key_rejected()
    health = pool_data.gecko_health()
    assert health["plan"] == "public"
    assert health["key_rejected"] is True


def test_the_client_gets_the_timeout_it_asks_for():
    """httpx defaults to 5s, which a slow chain listing exceeds outright."""
    timeout = pool_data._gecko_client().client.timeout
    assert timeout.read == pool_data._GECKO_TIMEOUT


# ── The market surface, shared with the smart_money_flow routine ──


def _onchain_flow():
    """Import ``agents/smart_money_flow/routines/onchain_flow.py`` from its file.

    An agent routine lives outside any package, exactly like the shared ones
    ``tests.conftest.load_shared_routine`` loads, so it has no dotted import path
    and production loads it this same way.
    """
    name = "smart_money_flow_routine_onchain_flow"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _FLOW_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_without_a_key_the_market_surface_is_the_public_host():
    """Keyless is the default on both surfaces, and sends no auth header."""
    assert pool_data.coingecko_access("market") == (
        "https://api.coingecko.com/api/v3",
        {},
    )


def test_a_key_routes_the_market_surface_to_the_pro_host(monkeypatch):
    """One key configures both surfaces: /onchain pool data and market data."""
    with_key(monkeypatch)
    base_url, headers = pool_data.coingecko_access("market")
    assert base_url == "https://pro-api.coingecko.com/api/v3"
    assert headers["x-cg-pro-api-key"] == "CG-secret"


def test_a_rejected_key_stops_being_sent_to_the_market_surface_too(monkeypatch):
    """The fallback is the module's, so every surface falls back together."""
    with_key(monkeypatch)
    pool_data._note_key_rejected()
    assert pool_data.coingecko_access("market") == (
        "https://api.coingecko.com/api/v3",
        {},
    )


def test_an_unknown_surface_is_a_programming_error():
    """A typo must not silently resolve to the pool-data host."""
    with pytest.raises(ValueError):
        pool_data.coingecko_access("markets")


def test_the_flow_routine_takes_its_hosts_from_pool_data(monkeypatch):
    """ARCH-305: the routine must not keep its own copy of the plan branch.

    It used to import the private ``_gecko_key`` and re-derive the
    analyst/public split, which drifts silently the next time this module
    changes how the key is sent. Asserting agreement under *both* plans fails if
    either side grows a branch of its own.
    """
    flow = _onchain_flow()
    assert flow._cg() == pool_data.coingecko_access("market")
    with_key(monkeypatch)
    assert flow._cg() == pool_data.coingecko_access("market")
    assert flow._cg()[0] == "https://pro-api.coingecko.com/api/v3"


def test_the_flow_routine_imports_nothing_private_from_pool_data():
    """A routine bound to an underscore name breaks on the next rename here."""
    tree = ast.parse(_FLOW_PATH.read_text())
    private = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "condor.pool_data"
        for alias in node.names
        if alias.name.startswith("_")
    ]
    assert private == []
