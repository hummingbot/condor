"""The connector config map reaches the dashboard keyed by field name (#225).

Current hummingbot-api servers describe each credential field
({name: {type, required, prompt}}); older ones return a bare list of names.
The dashboard builds the form from the keys, so a list came through as fields
"0" and "1", the save sent those as attribute names, and hummingbot-api
answered "has no attribute '0'". The route normalises the list; an object
passes through untouched.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.settings as settings_routes
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import ServerPermission

SERVER = "alpha"
OWNER = WebUser(id=1, username="owner", first_name="O", role="user")


class FakeConnectors:
    def __init__(self, config_map):
        self._config_map = config_map

    async def get_config_map(self, name):
        return self._config_map


class FakeClient:
    def __init__(self, config_map):
        self.connectors = FakeConnectors(config_map)


class FakeConfigManager:
    def __init__(self, client):
        self._client = client

    def get_server_permission(self, user_id, server_name):
        return ServerPermission.OWNER if server_name == SERVER else None

    def has_server_access(self, user_id, server_name, min_permission=None):
        return server_name == SERVER

    def is_admin(self, user_id):
        return False

    async def get_client(self, server_name):
        return self._client


def _http(monkeypatch, config_map):
    cm = FakeConfigManager(FakeClient(config_map))
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    return TestClient(app)


def test_a_legacy_list_becomes_fields_keyed_by_name(monkeypatch):
    http = _http(monkeypatch, ["bybit_perpetual_api_key", "bybit_perpetual_secret_key"])

    resp = http.get(
        "/settings/connectors/bybit_perpetual/config-map", params={"server": SERVER}
    )

    assert resp.status_code == 200
    assert resp.json()["config_map"] == {
        "bybit_perpetual_api_key": {"type": "string", "required": True},
        "bybit_perpetual_secret_key": {"type": "string", "required": True},
    }


def test_a_described_map_passes_through_untouched(monkeypatch):
    described = {
        "bybit_perpetual_api_key": {
            "type": "SecretStr",
            "required": True,
            "prompt": "Enter your Bybit API key",
        },
        "bybit_perpetual_secret_key": {"type": "SecretStr", "required": True},
    }
    http = _http(monkeypatch, described)

    resp = http.get(
        "/settings/connectors/bybit_perpetual/config-map", params={"server": SERVER}
    )

    assert resp.status_code == 200
    assert resp.json()["config_map"] == described


def test_an_empty_list_is_an_empty_form(monkeypatch):
    http = _http(monkeypatch, [])

    resp = http.get("/settings/connectors/paper/config-map", params={"server": SERVER})

    assert resp.status_code == 200
    assert resp.json()["config_map"] == {}
