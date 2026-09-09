"""Gateway lifecycle must invalidate VENUES only when the answer can actually
change (CORR-614).

`_chartable_gateway_networks` (condor/fetchers/connectors.py:47) calls
`client.gateway.list_networks()`, and its result decides each venue's
`clmm_lp` trait and half of its `credentialed` trait. VENUES has no
subscriber (server_data_service.py) so its 600s TTL is the only thing that
ever clears it on its own — the lifecycle routes must invalidate it
explicitly, but only at the moment Gateway can truthfully answer:

- `gateway_stop` blocks until the container is down, so the new (gateway-less)
  answer is already true by the time the route returns — invalidate right away,
  and outside the try/except so an invalidation bug is never misreported as a
  failed stop.
- `gateway_start`/`gateway_restart` return right after a detached
  `containers.run` with no readiness wait — invalidating on their 200 would
  refetch mid-boot and cache a gateway-less list for a fresh 600s. They must
  invalidate nothing; `gateway_status` catches the real transition instead.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.settings as settings_routes
from condor.server_data_service import ServerDataType
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import ServerPermission

SERVER = "alpha"
OWNER = WebUser(id=1, username="owner", first_name="O", role="user")
TRADER = WebUser(id=2, username="trader", first_name="T", role="user")


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.status_sequence = []

    async def start(self, cfg):
        self.calls.append("start")
        return {"ok": True}

    async def stop(self):
        self.calls.append("stop")
        return {"ok": True}

    async def restart(self):
        self.calls.append("restart")
        return {"ok": True}

    async def get_status(self):
        self.calls.append("status")
        return self.status_sequence.pop(0)


class FakeClient:
    def __init__(self):
        self.gateway = FakeGateway()


class FakeConfigManager:
    def __init__(self, client):
        self._client = client

    def get_server_permission(self, user_id, server_name):
        if server_name != SERVER:
            return None
        if user_id == OWNER.id:
            return ServerPermission.OWNER
        if user_id == TRADER.id:
            return ServerPermission.TRADER
        return None

    def has_server_access(self, user_id, server_name, min_permission=None):
        return self.get_server_permission(user_id, server_name) is not None

    def is_admin(self, user_id):
        return False

    async def get_client(self, server_name):
        return self._client


class FakeSDS:
    def __init__(self):
        self.invalidated = []

    def invalidate(self, server, *data_types):
        for data_type in data_types:
            self.invalidated.append((server, data_type))


class ExplodingSDS:
    """Raises on invalidate — used to pin that the call sits outside the
    try/except around the upstream call."""

    def invalidate(self, server, *data_types):
        raise RuntimeError("cache backend unavailable")


@pytest.fixture
def env(monkeypatch):
    client = FakeClient()
    cm = FakeConfigManager(client)
    sds = FakeSDS()
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    monkeypatch.setattr(
        "condor.web.routes.settings.get_server_data_service", lambda: sds
    )
    # Module-level transition tracker — reset so tests don't bleed into each other.
    settings_routes._gateway_was_running.clear()
    app = FastAPI()
    app.include_router(settings_routes.router)
    return app, client, sds


def as_user(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


P = {"server": SERVER}


def test_stop_invalidates_venues(env):
    app, client, sds = env
    resp = as_user(app, OWNER).post("/settings/gateway/stop", params=P)
    assert resp.status_code == 200
    assert client.gateway.calls == ["stop"]
    assert sds.invalidated == [(SERVER, ServerDataType.VENUES)]


def test_stop_invalidation_sits_outside_the_try_except(env):
    """An invalidation failure must not be reported as a failed gateway stop —
    the stop itself already succeeded by the time invalidate() runs."""
    app, client, _ = env
    app.dependency_overrides[get_current_user] = lambda: OWNER
    http = TestClient(app, raise_server_exceptions=False)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "condor.web.routes.settings.get_server_data_service",
            lambda: ExplodingSDS(),
        )
        resp = http.post("/settings/gateway/stop", params=P)

    # The backend call happened and is not what failed; if invalidate() were
    # inside the try/except this would come back as a 502 with detail
    # "Failed to stop gateway: ...".
    assert client.gateway.calls == ["stop"]
    assert resp.status_code == 500
    assert "Failed to stop gateway" not in resp.text


def test_start_does_not_invalidate_venues_on_the_200(env):
    """Starting returns before Gateway is actually up — invalidating here would
    make fetch_venues cache a gateway-less answer for a fresh 600s."""
    app, client, sds = env
    resp = as_user(app, OWNER).post(
        "/settings/gateway/start",
        params=P,
        json={"image": "hummingbot/gateway:latest", "port": 15888},
    )
    assert resp.status_code == 200
    assert client.gateway.calls == ["start"]
    assert sds.invalidated == []


def test_restart_does_not_invalidate_venues_on_the_200(env):
    app, client, sds = env
    resp = as_user(app, OWNER).post("/settings/gateway/restart", params=P)
    assert resp.status_code == 200
    assert client.gateway.calls == ["restart"]
    assert sds.invalidated == []


def test_status_invalidates_venues_only_on_the_false_to_true_transition(env):
    app, client, sds = env
    client.gateway.status_sequence = [
        {"running": False},  # down: no transition, no invalidation
        {"running": False},  # still down
        {"running": True},  # <-- the transition: invalidate
        {"running": True},  # already known running: no repeat invalidation
    ]
    http = as_user(app, TRADER)  # reads stay at TRADER

    assert http.get("/settings/gateway/status", params=P).json()["running"] is False
    assert sds.invalidated == []

    assert http.get("/settings/gateway/status", params=P).json()["running"] is False
    assert sds.invalidated == []

    assert http.get("/settings/gateway/status", params=P).json()["running"] is True
    assert sds.invalidated == [(SERVER, ServerDataType.VENUES)]

    assert http.get("/settings/gateway/status", params=P).json()["running"] is True
    assert sds.invalidated == [(SERVER, ServerDataType.VENUES)]  # unchanged


def test_status_error_is_treated_as_not_running_for_the_transition(env):
    """A transient status-fetch failure must not get skipped over as if the
    gateway were still up, or the next real transition would be missed."""
    app, client, sds = env
    settings_routes._gateway_was_running[SERVER] = True
    http = as_user(app, TRADER)

    async def raising_get_status():
        raise RuntimeError("upstream unreachable")

    client.gateway.get_status = raising_get_status
    resp = http.get("/settings/gateway/status", params=P)
    assert resp.json() == {"running": False, "info": None}
    assert settings_routes._gateway_was_running[SERVER] is False
