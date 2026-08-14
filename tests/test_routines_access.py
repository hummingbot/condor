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
        self.continuous_calls = []
        self.agents = []
        self.conversations = []
        self.session_keys = []

    async def execute(
        self,
        routine_name,
        config,
        server_name,
        user_id=0,
        agent="",
        conversation_id="",
        session_key="",
    ):
        self.execute_calls.append((routine_name, server_name, user_id))
        self.agents.append(agent)
        self.conversations.append(conversation_id)
        self.session_keys.append(session_key)
        return "inst-run"

    async def schedule(
        self, routine_name, config, server_name, interval_sec, user_id=0
    ):
        self.schedule_calls.append((routine_name, server_name, user_id))
        return "inst-sched"

    async def start_continuous(
        self,
        routine_name,
        config,
        server_name,
        user_id=0,
        agent="",
        conversation_id="",
        session_key="",
    ):
        self.continuous_calls.append((routine_name, server_name, user_id))
        self.agents.append(agent)
        self.conversations.append(conversation_id)
        self.session_keys.append(session_key)
        return "inst-cont"


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
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)

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


def test_start_v2_denied_on_foreign_server(client_and_store):
    """The continuous-start endpoint runs a routine too — same gate."""
    client, store = client_and_store
    resp = client.post(
        "/routines/start",
        json={"routine_name": "agent/watcher", "server_name": FOREIGN_SERVER},
    )
    assert resp.status_code == 403
    assert store.continuous_calls == []


def test_start_v2_allowed_on_owned_server(client_and_store):
    client, store = client_and_store
    resp = client.post(
        "/routines/start",
        json={"routine_name": "agent/watcher", "server_name": OWNED_SERVER},
    )
    assert resp.status_code == 200
    assert resp.json() == {"instance_id": "inst-cont"}
    assert store.continuous_calls == [("agent/watcher", OWNED_SERVER, USER.id)]


# ── FEAT-028: explicit report attribution travels with the run ──


def test_attribute_to_is_passed_through_to_the_store(client_and_store):
    client, store = client_and_store
    client.post(
        "/routines/run",
        json={
            "routine_name": "some_routine",
            "server_name": OWNED_SERVER,
            "attribute_to": "scout",
        },
    )
    assert store.agents == ["scout"]


def test_dashboard_runs_leave_attribution_to_the_routine(client_and_store):
    """Omitted -> empty -> the store derives it from the routine's source."""
    client, store = client_and_store
    client.post(
        "/routines/run",
        json={"routine_name": "some_routine", "server_name": OWNED_SERVER},
    )
    assert store.agents == [""]


# ── ARCH-089: the run remembers which conversation asked for it ──


def _resolves_to(monkeypatch, conversation_id: str):
    async def fake(session_key: str) -> str:
        return conversation_id if session_key else ""

    monkeypatch.setattr(
        routines_module.client, "conversation_for_session", fake, raising=False
    )


def test_a_session_key_reaches_the_store_as_a_conversation(
    client_and_store, monkeypatch
):
    """The middle link of the chain: the route is what turns a key into an id."""
    client, store = client_and_store
    _resolves_to(monkeypatch, "conv-1")

    client.post(
        "/routines/start",
        json={
            "routine_name": "agent/watcher",
            "server_name": OWNED_SERVER,
            "session_key": "web:1:slot-1",
        },
    )
    assert store.conversations == ["conv-1"]


def test_the_session_key_itself_reaches_the_store_too(client_and_store, monkeypatch):
    """The id says where to *record* the outcome; the key says where to *show*
    it while the tab is still open."""
    client, store = client_and_store
    _resolves_to(monkeypatch, "conv-1")

    client.post(
        "/routines/run",
        json={
            "routine_name": "some_routine",
            "server_name": OWNED_SERVER,
            "session_key": "web:1:slot-1",
        },
    )
    assert store.session_keys == ["web:1:slot-1"]


def test_a_dashboard_run_has_no_conversation_behind_it(client_and_store, monkeypatch):
    """Omitted -> the run reports nowhere, exactly as before ARCH-089."""
    client, store = client_and_store
    _resolves_to(monkeypatch, "conv-1")

    client.post(
        "/routines/run",
        json={"routine_name": "some_routine", "server_name": OWNED_SERVER},
    )
    assert store.conversations == [""]
