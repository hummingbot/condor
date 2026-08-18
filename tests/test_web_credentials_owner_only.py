"""Exchange credential add/delete is owner-only (SEC-153).

`has_server_access` defaults to TRADER, so a user the owner shared a server with
for trading could delete the owner's exchange API keys off the backend — killing
the owner's running bots — or overwrite them by re-adding the same connector with
their own keys. Credential management is server configuration, like
update_server/delete_server, and must draw the same `_require_owner` line.

These tests pin all three principals: owner passes, non-owner trader is refused,
admin keeps the bypass `_require_owner` already grants everywhere else.
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
ADMIN = WebUser(id=3, username="admin", first_name="A", role="admin")


class FakeAccounts:
    def __init__(self):
        self.added = []
        self.deleted = []

    async def add_credential(self, account_name, connector_name, credentials):
        self.added.append((account_name, connector_name, credentials))
        return {"ok": True}

    async def delete_credential(self, account_name, connector_name):
        self.deleted.append((account_name, connector_name))
        return {"ok": True}


class FakeClient:
    def __init__(self):
        self.accounts = FakeAccounts()


class FakeConfigManager:
    """Owner id 1, trader id 2 shared, admin id 3 with no grant on the server."""

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
        # Admins reach every server, mirroring the real manager.
        if self.is_admin(user_id):
            return server_name == SERVER
        return self.get_server_permission(user_id, server_name) is not None

    def is_admin(self, user_id):
        return user_id == ADMIN.id

    async def get_client(self, server_name):
        return self._client


class FakeSDS:
    def __init__(self):
        self.invalidated = []

    def invalidate(self, server, data_type):
        self.invalidated.append((server, data_type))


@pytest.fixture
def env(monkeypatch):
    client = FakeClient()
    cm = FakeConfigManager(client)
    sds = FakeSDS()
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    monkeypatch.setattr(
        "condor.server_data_service.get_server_data_service", lambda: sds
    )
    app = FastAPI()
    app.include_router(settings_routes.router)
    return app, client, sds


def as_user(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def post_credential(http):
    return http.post(
        "/settings/credentials",
        params={"server": SERVER},
        json={"connector_name": "binance", "credentials": {"api_key": "k"}},
    )


def delete_credential(http):
    return http.delete("/settings/credentials/binance", params={"server": SERVER})


def test_owner_can_add_and_delete_credentials(env):
    app, client, sds = env

    assert post_credential(as_user(app, OWNER)).status_code == 200
    assert client.accounts.added == [("master_account", "binance", {"api_key": "k"})]

    assert delete_credential(as_user(app, OWNER)).status_code == 200
    assert client.accounts.deleted == [("master_account", "binance")]
    assert (SERVER, ServerDataType.PORTFOLIO) in sds.invalidated


def test_shared_trader_cannot_add_or_delete_credentials(env):
    app, client, _ = env
    http = as_user(app, TRADER)

    add = post_credential(http)
    assert add.status_code == 403
    assert add.json()["detail"] == "Owner access required"

    delete = delete_credential(http)
    assert delete.status_code == 403
    assert delete.json()["detail"] == "Owner access required"

    # The backend was never touched on either attempt.
    assert client.accounts.added == []
    assert client.accounts.deleted == []


def test_admin_keeps_the_owner_bypass(env):
    """`_require_owner` lets admins through everywhere else in this file."""
    app, client, _ = env
    http = as_user(app, ADMIN)

    assert post_credential(http).status_code == 200
    assert delete_credential(http).status_code == 200
    assert client.accounts.added and client.accounts.deleted


def test_trader_can_still_list_credentials(env):
    """Listing returns connector names only and stays at TRADER level."""
    app, client, _ = env

    async def list_account_credentials(account_name):
        return ["binance"]

    client.accounts.list_account_credentials = list_account_credentials

    resp = as_user(app, TRADER).get("/settings/credentials", params={"server": SERVER})
    assert resp.status_code == 200
    assert resp.json()["credentials"] == [
        {"connector_name": "binance", "connector_type": ""}
    ]
