"""The DEX panel is told when GeckoTerminal is throttling Condor.

Everything the panel draws — pool rows, TVL, a pool page, its chart — comes from
one per-IP GeckoTerminal budget, and every failure behind it degrades to an empty
list. Without a state to read, "this chain has no pools" and "the budget is spent,
ask again in ten seconds" arrive at the browser looking identical.

Two shapes are pinned here. The refusal *counters*, because a spent per-minute
budget refuses without opening the breaker — ``rate_limited`` stays False while
the rows are still missing, so only a counter delta proves it happened. And the
pool lookup answering 503 rather than 404 when it was refused: one of those is
worth retrying and the other never resolves.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.dex as dex_routes
from condor import pool_data
from condor.web.auth import get_current_user
from condor.web.models import WebUser

POOL = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"
USER = WebUser(id=222, username="u", first_name="U", role="user")


class FakeConfigManager:
    def has_server_access(self, user_id, name, *a, **kw):
        return True

    async def get_client(self, name):
        raise AssertionError("no Gateway call belongs in these paths")


@pytest.fixture
def client(monkeypatch):
    cm = FakeConfigManager()
    monkeypatch.setattr(dex_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(dex_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_budget():
    pool_data.reset_gecko_throttle()
    yield
    pool_data.reset_gecko_throttle()


def test_a_healthy_budget_reports_itself_as_not_limited(client):
    body = client.get("/servers/srv/dex/upstream").json()

    assert body["rate_limited"] is False
    assert body["retry_in"] == 0
    assert body["budget"] == pool_data._GECKO_RATE_LIMIT


def test_an_open_breaker_reports_a_cooldown_to_count_down(client):
    pool_data._trip_breaker()

    body = client.get("/servers/srv/dex/upstream").json()

    assert body["rate_limited"] is True
    assert 0 < body["retry_in"] <= pool_data._GECKO_COOLDOWN
    assert body["count_429"] == 1


def test_the_counters_move_when_a_request_is_refused_without_a_429(client):
    """A spent budget refuses silently — the counter is the only evidence."""
    before = client.get("/servers/srv/dex/upstream").json()

    # Spend the minute, then ask for one more slot than it holds.
    async def spend():
        for _ in range(pool_data._GECKO_RATE_LIMIT):
            await pool_data._acquire_rate_slot()
        with pytest.raises(pool_data.GeckoRateLimited):
            await pool_data._acquire_rate_slot()

    asyncio.run(spend())

    after = client.get("/servers/srv/dex/upstream").json()
    assert after["rate_limited"] is False, "a spent budget must not open the breaker"
    assert after["throttled_calls"] > before["throttled_calls"]
    assert after["requests_last_minute"] == pool_data._GECKO_RATE_LIMIT


def test_an_empty_page_says_whether_the_fetch_was_refused(client, monkeypatch):
    async def refused(*a, **kw):
        pool_data._trip_breaker()
        return {"pools": [], "has_more": False}

    monkeypatch.setattr(pool_data, "list_gecko_pools_page", refused)

    body = client.get("/servers/srv/dex/pools?source=gecko").json()

    assert body["pools"] == []
    assert body["upstream"]["throttled_request"] is True
    assert body["upstream"]["rate_limited"] is True


def test_a_page_that_simply_has_no_rows_is_not_blamed_on_the_budget(
    client, monkeypatch
):
    async def empty(*a, **kw):
        return {"pools": [], "has_more": False}

    monkeypatch.setattr(pool_data, "list_gecko_pools_page", empty)

    body = client.get("/servers/srv/dex/pools?source=gecko").json()

    assert body["upstream"]["throttled_request"] is False
    assert body["upstream"]["rate_limited"] is False


def test_a_refused_pool_lookup_is_a_503_the_browser_can_retry(client, monkeypatch):
    async def refused(*a, **kw):
        pool_data._trip_breaker()
        return None

    monkeypatch.setattr(pool_data, "fetch_pool_by_address", refused)

    res = client.get(f"/servers/srv/dex/pools/{POOL}")

    assert res.status_code == 503
    assert "rate limit" in res.json()["detail"].lower()


def test_an_unknown_pool_is_still_a_404(client, monkeypatch):
    async def missing(*a, **kw):
        return None

    monkeypatch.setattr(pool_data, "fetch_pool_by_address", missing)

    res = client.get(f"/servers/srv/dex/pools/{POOL}")

    assert res.status_code == 404


def test_favourites_carry_the_same_state_as_the_browser(client, monkeypatch):
    async def refused(*a, **kw):
        pool_data._trip_breaker()
        return []

    monkeypatch.setattr(pool_data, "fetch_pools_by_addresses", refused)

    body = client.get(f"/servers/srv/dex/pools-by-address?addresses={POOL}").json()

    assert body["pools"] == []
    assert body["upstream"]["throttled_request"] is True
