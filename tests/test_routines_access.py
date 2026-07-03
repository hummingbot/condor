"""Tests for SEC-045: routine run/schedule endpoints must enforce server access.

The four execution endpoints (``/routines/servers/{server}/{routine}/run``,
``.../schedule``, ``/routines/run``, ``/routines/schedule``) accept an
arbitrary ``server_name``. Without a ``has_server_access`` gate, any approved
user could run/schedule routines against servers owned by other users, using
those servers' stored API credentials (cross-server IDOR).
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.routines as routines_module
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")
OWNED_SERVER = "my-server"
FOREIGN_SERVER = "other-users-server"


class FakeRoutineStore:
    """Records execute/schedule calls; a denied request must never reach it."""

    def __init__(self):
        self.execute_calls = []
        self.schedule_calls = []

    async def execute(self, routine_name, config, server_name, user_id=0):
        self.execute_calls.append((routine_name, server_name, user_id))
        return "inst-run"

    async def schedule(
        self, routine_name, config, server_name, interval_sec, user_id=0
    ):
        self.schedule_calls.append((routine_name, server_name, user_id))
        return "inst-sched"


class FakeConfigManager:
    def __init__(self, allowed):
        self._allowed = allowed  # set of (user_id, server_name)

    def has_server_access(self, user_id, server_name, *args, **kwargs):
        return (user_id, server_name) in self._allowed


@pytest.fixture
def client_and_store(monkeypatch):
    store = FakeRoutineStore()
    cm = FakeConfigManager(allowed={(USER.id, OWNED_SERVER)})
    monkeypatch.setattr(routines_module, "get_routine_store", lambda: store)
    monkeypatch.setattr(routines_module, "get_config_manager", lambda: cm)

    app = FastAPI()
    app.include_router(routines_module.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app), store


# ── Denied: no access to the target server ──


def test_run_denied_on_foreign_server(client_and_store):
    client, store = client_and_store
    resp = client.post(
        f"/routines/servers/{FOREIGN_SERVER}/some_routine/run",
        json={"config": {}},
    )
    assert resp.status_code == 403
    assert store.execute_calls == []


def test_schedule_denied_on_foreign_server(client_and_store):
    client, store = client_and_store
    resp = client.post(
        f"/routines/servers/{FOREIGN_SERVER}/some_routine/schedule",
        json={"config": {}, "interval_sec": 60},
    )
    assert resp.status_code == 403
    assert store.schedule_calls == []


def test_run_v2_denied_on_foreign_server(client_and_store):
    client, store = client_and_store
    resp = client.post(
        "/routines/run",
        json={"routine_name": "agent/some_routine", "server_name": FOREIGN_SERVER},
    )
    assert resp.status_code == 403
    assert store.execute_calls == []


def test_schedule_v2_denied_on_foreign_server(client_and_store):
    client, store = client_and_store
    resp = client.post(
        "/routines/schedule",
        json={
            "routine_name": "agent/some_routine",
            "server_name": FOREIGN_SERVER,
            "interval_sec": 60,
        },
    )
    assert resp.status_code == 403
    assert store.schedule_calls == []


# ── Allowed: user has access to the target server ──


def test_run_allowed_on_owned_server(client_and_store):
    client, store = client_and_store
    resp = client.post(
        f"/routines/servers/{OWNED_SERVER}/some_routine/run",
        json={"config": {}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"instance_id": "inst-run"}
    assert store.execute_calls == [("some_routine", OWNED_SERVER, USER.id)]


def test_schedule_allowed_on_owned_server(client_and_store):
    client, store = client_and_store
    resp = client.post(
        f"/routines/servers/{OWNED_SERVER}/some_routine/schedule",
        json={"config": {}, "interval_sec": 60},
    )
    assert resp.status_code == 200
    assert resp.json() == {"instance_id": "inst-sched"}
    assert store.schedule_calls == [("some_routine", OWNED_SERVER, USER.id)]


def test_run_v2_allowed_on_owned_server(client_and_store):
    client, store = client_and_store
    resp = client.post(
        "/routines/run",
        json={"routine_name": "agent/some_routine", "server_name": OWNED_SERVER},
    )
    assert resp.status_code == 200
    assert store.execute_calls == [("agent/some_routine", OWNED_SERVER, USER.id)]


def test_schedule_v2_allowed_on_owned_server(client_and_store):
    client, store = client_and_store
    resp = client.post(
        "/routines/schedule",
        json={
            "routine_name": "agent/some_routine",
            "server_name": OWNED_SERVER,
            "interval_sec": 60,
        },
    )
    assert resp.status_code == 200
    assert store.schedule_calls == [("agent/some_routine", OWNED_SERVER, USER.id)]
