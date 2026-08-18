"""Tests for SEC-150: routine instance endpoints must enforce ownership.

SEC-045 gated *starting* a routine behind server access but left the instance
endpoints wide open: any approved user could GET another user's instance (with
its last result), fetch its chart PNG, stop it, and enumerate every instance in
the store. Ownership lives in the instance meta as ``user_id``
(RoutineStore._new_instance_meta), so each endpoint now checks it — admins keep
reaching everything, mirroring ``_require_ownership`` in routes/sessions.py.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.routines as routines_module
from condor.web.auth import get_current_user
from condor.web.models import WebUser

OWNER = WebUser(id=111, username="owner", first_name="O", role="user")
STRANGER = WebUser(id=222, username="stranger", first_name="S", role="user")
ADMIN = WebUser(id=999, username="admin", first_name="A", role="admin")

OWNED = "inst-owned"
FOREIGN = "inst-foreign"
UNATTRIBUTED = "inst-orphan"


class FakeResult:
    def __init__(self, chart_image=b"PNG-BYTES"):
        self.chart_image = chart_image


class FakeRoutineStore:
    """Holds one instance per user; records which ones were actually stopped."""

    def __init__(self):
        self._instances = {
            OWNED: {"instance_id": OWNED, "user_id": OWNER.id, "last_result": "mine"},
            FOREIGN: {
                "instance_id": FOREIGN,
                "user_id": STRANGER.id,
                "last_result": "secret",
            },
            # Telegram-synced / MCP instances land in the store without an owner.
            UNATTRIBUTED: {"instance_id": UNATTRIBUTED, "last_result": "nobody's"},
        }
        self.stopped = []

    def list_instances(self):
        return list(self._instances.values())

    def get_instance(self, instance_id):
        return self._instances.get(instance_id)

    def get_result(self, instance_id):
        return FakeResult() if instance_id in self._instances else None

    def stop(self, instance_id):
        if instance_id not in self._instances:
            return False
        self.stopped.append(instance_id)
        return True


class FakeConfigManager:
    def is_admin(self, user_id):
        return user_id == ADMIN.id

    def has_server_access(self, user_id, server_name, *args, **kwargs):
        return True


@pytest.fixture
def make_client(monkeypatch):
    store = FakeRoutineStore()
    monkeypatch.setattr(routines_module, "get_routine_store", lambda: store)
    monkeypatch.setattr(routines_module, "get_config_manager", FakeConfigManager)

    def _make(user: WebUser):
        app = FastAPI()
        app.include_router(routines_module.router)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return _make, store


# ── GET /routines/instances/{id} ──


def test_get_foreign_instance_is_refused(make_client):
    make, _store = make_client
    resp = make(OWNER).get(f"/routines/instances/{FOREIGN}")
    assert resp.status_code == 403
    assert "secret" not in resp.text


def test_get_own_instance_still_works(make_client):
    make, _store = make_client
    resp = make(OWNER).get(f"/routines/instances/{OWNED}")
    assert resp.status_code == 200
    assert resp.json()["last_result"] == "mine"


def test_admin_can_get_any_instance(make_client):
    make, _store = make_client
    resp = make(ADMIN).get(f"/routines/instances/{FOREIGN}")
    assert resp.status_code == 200


def test_unowned_instance_is_refused_to_non_admins(make_client):
    """No recorded owner -> nobody's but the admin's. Fails closed."""
    make, _store = make_client
    assert make(OWNER).get(f"/routines/instances/{UNATTRIBUTED}").status_code == 403
    assert make(ADMIN).get(f"/routines/instances/{UNATTRIBUTED}").status_code == 200


def test_missing_instance_is_still_a_404(make_client):
    make, _store = make_client
    assert make(OWNER).get("/routines/instances/nope").status_code == 404


# ── POST /routines/instances/{id}/stop ──


def test_stop_foreign_instance_is_refused_and_it_keeps_running(make_client):
    make, store = make_client
    resp = make(OWNER).post(f"/routines/instances/{FOREIGN}/stop")
    assert resp.status_code == 403
    assert store.stopped == []


def test_stop_own_instance_still_works(make_client):
    make, store = make_client
    resp = make(OWNER).post(f"/routines/instances/{OWNED}/stop")
    assert resp.status_code == 200
    assert resp.json() == {"stopped": True}
    assert store.stopped == [OWNED]


def test_admin_can_stop_any_instance(make_client):
    make, store = make_client
    resp = make(ADMIN).post(f"/routines/instances/{FOREIGN}/stop")
    assert resp.status_code == 200
    assert store.stopped == [FOREIGN]


# ── GET /routines/instances/{id}/image ──


def test_foreign_instance_image_is_refused(make_client):
    make, _store = make_client
    resp = make(OWNER).get(f"/routines/instances/{FOREIGN}/image")
    assert resp.status_code == 403
    assert resp.content != b"PNG-BYTES"


def test_own_instance_image_is_served(make_client):
    make, _store = make_client
    resp = make(OWNER).get(f"/routines/instances/{OWNED}/image")
    assert resp.status_code == 200
    assert resp.content == b"PNG-BYTES"


def test_admin_can_fetch_any_instance_image(make_client):
    make, _store = make_client
    resp = make(ADMIN).get(f"/routines/instances/{FOREIGN}/image")
    assert resp.status_code == 200
    assert resp.content == b"PNG-BYTES"


# ── GET /routines/instances ──


def test_list_shows_only_the_callers_instances(make_client):
    make, _store = make_client
    resp = make(OWNER).get("/routines/instances")
    assert resp.status_code == 200
    assert [i["instance_id"] for i in resp.json()] == [OWNED]


def test_admin_list_shows_every_instance(make_client):
    make, _store = make_client
    resp = make(ADMIN).get("/routines/instances")
    assert {i["instance_id"] for i in resp.json()} == {OWNED, FOREIGN, UNATTRIBUTED}
