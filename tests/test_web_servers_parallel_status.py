"""GET /servers resolves every server's status concurrently (PERF-134).

An unreachable server caches an error-only entry, so its status is re-fetched on
every request and pays a full connect timeout. Resolved serially, the landing
endpoint blocks for the *sum* of those timeouts. These tests pin the fan-out, the
shape of the response, and that one broken server never sinks the batch.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.servers as servers_routes
from condor.server_data_service import ServerDataType
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=7, username="u", first_name="U", role="user")

DELAY = 0.3


class FakePermission:
    def __init__(self, value):
        self.value = value


class FakeConfigManager:
    def __init__(self, servers, permission="owner", default=None):
        self._servers = servers
        self._permission = permission
        self._default = default

    def list_accessible_servers(self, user_id):
        return self._servers

    def get_server_permission(self, user_id, name):
        return FakePermission(self._permission) if self._permission else None

    def get_chat_default_server(self, chat_id):
        return self._default


#: How long a fetch waits for its siblings before deciding they are not coming.
#: Only ever reached by a *serial* implementation, so it bounds the failure
#: rather than the success — a concurrent one releases the moment the last
#: fetch arrives, however loaded the machine is.
RENDEZVOUS_TIMEOUT = 5.0


class FakeSDS:
    """Cache always misses; each fetch sleeps like a connect timeout would.

    ``rendezvous`` makes the fan-out provable instead of merely likely: every
    fetch waits until that many are in flight at once, which a serial resolver
    can never satisfy.
    """

    def __init__(self, statuses, delay=DELAY, raises=(), rendezvous=0):
        self._statuses = statuses
        self._delay = delay
        self._raises = set(raises)
        self._barrier = asyncio.Barrier(rendezvous) if rendezvous else None
        self.concurrent = 0
        self.max_concurrent = 0

    def get(self, name, data_type, **params):
        return None

    async def get_or_fetch(self, name, data_type, **params):
        assert data_type is ServerDataType.SERVER_STATUS
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self._barrier is not None:
                await asyncio.wait_for(self._barrier.wait(), RENDEZVOUS_TIMEOUT)
            else:
                await asyncio.sleep(self._delay)
            if name in self._raises:
                raise RuntimeError("connection refused")
            return self._statuses[name]
        finally:
            self.concurrent -= 1


def build_client(monkeypatch, cm, sds):
    monkeypatch.setattr(servers_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr(servers_routes, "get_server_data_service", lambda: sds)
    app = FastAPI()
    app.include_router(servers_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def three_servers():
    return {
        "alpha": {"host": "a.local", "port": 8000},
        "bravo": {"host": "b.local", "port": 8001},
        "charlie": {"host": "c.local", "port": 8002},
    }


def test_three_slow_servers_are_all_in_flight_at_once(monkeypatch):
    """Two unreachable servers must not stack their timeouts on the third.

    Asserted by rendezvous rather than by the clock. The wall-clock form
    (``elapsed < DELAY * 1.5``) measured the machine as much as the code: it
    passed alone and failed under a loaded full-suite run, which is the worst
    kind of red — it says nothing about the property and trains people to
    re-run. Here every fetch blocks until all three have arrived, so a serial
    resolver cannot reach the assertion at all and a slow one still can.
    """
    sds = FakeSDS(
        statuses={
            "alpha": {"status": "online"},
            "bravo": None,
            "charlie": None,
        },
        rendezvous=3,
    )
    client = build_client(monkeypatch, FakeConfigManager(three_servers()), sds)

    resp = client.get("/servers")

    assert resp.status_code == 200
    assert sds.max_concurrent == 3, (
        "the three fetches never overlapped — they were resolved one after "
        "another, so the endpoint pays the sum of the connect timeouts"
    )


def test_all_online_response_is_unchanged(monkeypatch):
    """Names, hosts, ports, online flags, permission and sort order hold.

    ``shared_with`` joined the payload with FEAT-088 and is additive: it is
    populated only for a server's owner, and this fixture's permission object is
    not the real enum, so every row takes the empty branch.
    """
    sds = FakeSDS(
        statuses={n: {"status": "online"} for n in three_servers()},
        delay=0,
    )
    client = build_client(monkeypatch, FakeConfigManager(three_servers()), sds)

    assert client.get("/servers").json() == [
        {
            "name": "alpha",
            "host": "a.local",
            "port": 8000,
            "online": True,
            "permission": "owner",
            "is_default": False,
            "shared_with": [],
        },
        {
            "name": "bravo",
            "host": "b.local",
            "port": 8001,
            "online": True,
            "permission": "owner",
            "is_default": False,
            "shared_with": [],
        },
        {
            "name": "charlie",
            "host": "c.local",
            "port": 8002,
            "online": True,
            "permission": "owner",
            "is_default": False,
            "shared_with": [],
        },
    ]


def test_offline_servers_sort_after_online_ones(monkeypatch):
    """Sort key stays (not online, name) across the fan-out."""
    sds = FakeSDS(
        statuses={
            "alpha": None,
            "bravo": {"status": "online"},
            "charlie": {"status": "offline"},
        },
        delay=0,
    )
    client = build_client(monkeypatch, FakeConfigManager(three_servers()), sds)

    rows = client.get("/servers").json()
    assert [(r["name"], r["online"]) for r in rows] == [
        ("bravo", True),
        ("alpha", False),
        ("charlie", False),
    ]


def test_a_raising_fetch_renders_offline_instead_of_failing_the_endpoint(monkeypatch):
    """One server blowing up must not take the other two down with it."""
    sds = FakeSDS(
        statuses={n: {"status": "online"} for n in three_servers()},
        delay=0,
        raises=["bravo"],
    )
    client = build_client(monkeypatch, FakeConfigManager(three_servers()), sds)

    resp = client.get("/servers")
    assert resp.status_code == 200
    assert {r["name"]: r["online"] for r in resp.json()} == {
        "alpha": True,
        "bravo": False,
        "charlie": True,
    }


def test_a_cache_hit_never_reaches_the_fetch(monkeypatch):
    """The get()-first fallback survives: fresh cache short-circuits the fetch."""
    sds = FakeSDS(statuses={}, delay=0)
    sds.get = lambda name, data_type, **params: {"status": "online"}

    async def boom(*a, **kw):
        raise AssertionError("get_or_fetch must not run on a cache hit")

    sds.get_or_fetch = boom
    client = build_client(monkeypatch, FakeConfigManager(three_servers()), sds)

    assert all(r["online"] for r in client.get("/servers").json())


def test_missing_permission_falls_back_to_trader(monkeypatch):
    sds = FakeSDS(statuses={n: {"status": "online"} for n in three_servers()}, delay=0)
    cm = FakeConfigManager(three_servers(), permission=None)
    client = build_client(monkeypatch, cm, sds)

    assert {r["permission"] for r in client.get("/servers").json()} == {"trader"}


def test_the_chat_default_server_is_the_only_one_flagged(monkeypatch):
    """The selector seeds itself from this flag, so exactly one row carries it.

    Without it the dashboard could not tell which server Telegram's
    ``chat_defaults`` points at, and the Settings star had nothing to fill in —
    the click looked like it did nothing at all.
    """
    sds = FakeSDS(statuses={n: {"status": "online"} for n in three_servers()}, delay=0)
    cm = FakeConfigManager(three_servers(), default="bravo")
    client = build_client(monkeypatch, cm, sds)

    assert {r["name"]: r["is_default"] for r in client.get("/servers").json()} == {
        "alpha": False,
        "bravo": True,
        "charlie": False,
    }


def test_no_default_flags_nothing(monkeypatch):
    sds = FakeSDS(statuses={n: {"status": "online"} for n in three_servers()}, delay=0)
    client = build_client(monkeypatch, FakeConfigManager(three_servers()), sds)

    assert not any(r["is_default"] for r in client.get("/servers").json())


# ── The settings listing is this same listing (ARCH-285) ──


def build_settings_client(monkeypatch, cm, sds):
    """Mount only the settings router; it reads ``servers_routes.server_rows``."""
    import condor.web.routes.settings as settings_routes

    monkeypatch.setattr(servers_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr(servers_routes, "get_server_data_service", lambda: sds)
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def test_settings_servers_survives_a_raising_fetch(monkeypatch):
    """The settings copy fetched unguarded, so one bad server 500'd the page."""
    sds = FakeSDS(
        statuses={n: {"status": "online"} for n in three_servers()},
        delay=0,
        raises=["bravo"],
    )
    client = build_settings_client(monkeypatch, FakeConfigManager(three_servers()), sds)

    resp = client.get("/settings/servers")
    assert resp.status_code == 200
    assert {r["name"]: r["online"] for r in resp.json()} == {
        "alpha": True,
        "bravo": False,
        "charlie": True,
    }


def test_both_server_listings_return_the_same_payload(monkeypatch):
    """One builder, so the settings page can never render a staler shape."""
    cm = FakeConfigManager(three_servers(), default="bravo")

    def rows(build, path):
        sds = FakeSDS(
            statuses={
                "alpha": {"status": "online"},
                "bravo": {"status": "online"},
                "charlie": None,
            },
            delay=0,
        )
        return build(monkeypatch, cm, sds).get(path).json()

    assert rows(build_settings_client, "/settings/servers") == rows(
        build_client, "/servers"
    )
