"""GET /servers resolves every server's status concurrently (PERF-134).

An unreachable server caches an error-only entry, so its status is re-fetched on
every request and pays a full connect timeout. Resolved serially, the landing
endpoint blocks for the *sum* of those timeouts. These tests pin the fan-out, the
shape of the response, and that one broken server never sinks the batch.
"""

import asyncio
import time

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
    def __init__(self, servers, permission="owner"):
        self._servers = servers
        self._permission = permission

    def list_accessible_servers(self, user_id):
        return self._servers

    def get_server_permission(self, user_id, name):
        return FakePermission(self._permission) if self._permission else None


class FakeSDS:
    """Cache always misses; each fetch sleeps like a connect timeout would."""

    def __init__(self, statuses, delay=DELAY, raises=()):
        self._statuses = statuses
        self._delay = delay
        self._raises = set(raises)
        self.concurrent = 0
        self.max_concurrent = 0

    def get(self, name, data_type, **params):
        return None

    async def get_or_fetch(self, name, data_type, **params):
        assert data_type is ServerDataType.SERVER_STATUS
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
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


def test_three_slow_servers_resolve_in_one_timeout_not_three(monkeypatch):
    """Two unreachable servers must not stack their timeouts on the third."""
    sds = FakeSDS(
        statuses={
            "alpha": {"status": "online"},
            "bravo": None,
            "charlie": None,
        }
    )
    client = build_client(monkeypatch, FakeConfigManager(three_servers()), sds)

    t0 = time.monotonic()
    resp = client.get("/servers")
    elapsed = time.monotonic() - t0

    assert resp.status_code == 200
    # Serial would be ~3x DELAY; concurrent stays well under 1.5x the slowest.
    assert elapsed < DELAY * 1.5, f"took {elapsed:.2f}s, expected ~{DELAY:.2f}s"
    assert sds.max_concurrent == 3


def test_all_online_response_is_unchanged(monkeypatch):
    """Names, hosts, ports, online flags, permission and sort order hold."""
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
        },
        {
            "name": "bravo",
            "host": "b.local",
            "port": 8001,
            "online": True,
            "permission": "owner",
        },
        {
            "name": "charlie",
            "host": "c.local",
            "port": 8002,
            "online": True,
            "permission": "owner",
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
