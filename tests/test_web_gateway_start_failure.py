"""The Hummingbot API answers 200 {"success": false} when a container call fails.

Condor must surface that message instead of reporting the start as done.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.settings as settings_routes
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import ServerPermission

OWNER = WebUser(id=1, username="owner", first_name="O", role="user")
P = {"server": "alpha"}


class FakeGateway:
    def __init__(self, result):
        self.result = result
        self.started_with = None

    async def start(self, cfg):
        self.started_with = cfg
        return self.result

    async def stop(self):
        return self.result

    async def restart(self):
        return self.result


class FakeClient:
    def __init__(self, result):
        self.gateway = FakeGateway(result)


class FakeConfigManager:
    def __init__(self, client):
        self._client = client

    def get_server_permission(self, user_id, server_name):
        return ServerPermission.OWNER

    def has_server_access(self, user_id, server_name, min_permission=None):
        return True

    def is_admin(self, user_id):
        return False

    async def get_client(self, server_name):
        return self._client


def make_http(monkeypatch, result):
    client = FakeClient(result)
    cm = FakeConfigManager(client)
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    return TestClient(app), client


START_BODY = {"image": "hummingbot/gateway:development", "port": 15888}


def test_start_sends_image_and_port_and_reports_success(monkeypatch):
    http, client = make_http(
        monkeypatch, {"success": True, "message": "Gateway started successfully"}
    )
    r = http.post("/settings/gateway/start", params=P, json=START_BODY)
    assert r.status_code == 200
    assert r.json()["started"] is True
    assert client.gateway.started_with == START_BODY


@pytest.mark.parametrize(
    "path,verb",
    [("start", "start"), ("stop", "stop"), ("restart", "restart")],
)
def test_in_band_failure_becomes_an_error_with_the_api_message(monkeypatch, path, verb):
    http, _ = make_http(
        monkeypatch, {"success": False, "message": "Gateway is already running."}
    )
    r = http.post(
        f"/settings/gateway/{path}",
        params=P,
        json=START_BODY if path == "start" else None,
    )
    assert r.status_code == 502
    assert (
        r.json()["detail"] == f"Failed to {verb} gateway: Gateway is already running."
    )
