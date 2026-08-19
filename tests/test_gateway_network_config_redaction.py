"""Gateway network config reads redact RPC credentials for non-owners (SEC-197).

Reading gateway state stays at TRADER (SEC-166 pinned that line: a trader must
see what they trade against), but the network config's ``nodeURL`` embeds the
owner's paid RPC provider API key — Helius puts it in the query string,
Infura/Alchemy in the path. GET /settings/gateway/networks/{id} therefore
masks every secret-bearing part of URL-shaped values server-side for anyone
below the OWNER line, with the same admin bypass `_require_owner` grants.
The secret must never reach a non-owner's browser to be hidden in the UI.
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
TRADER = WebUser(id=2, username="trader", first_name="T", role="user")
ADMIN = WebUser(id=3, username="admin", first_name="A", role="admin")

NETWORK_ID = "solana-mainnet-beta"

# One URL per place a provider hides the key, plus values that must survive.
CONFIG = {
    "nodeURL": "https://mainnet.helius-rpc.com/?api-key=super-secret-key",
    "wsURL": "wss://eth-mainnet.g.alchemy.com/v2/another-secret",
    "basicAuthURL": "https://user:pass@rpc.example.com",
    "bareURL": "https://api.mainnet-beta.solana.com",
    "chain_id": 101,
    "native_currency_symbol": "SOL",
    "retries": {"docsURL": "https://mainnet.infura.io/v3/path-secret"},
}


class FakeGateway:
    async def get_network_config(self, network_id):
        return dict(CONFIG)


class FakeClient:
    def __init__(self):
        self.gateway = FakeGateway()


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
        if self.is_admin(user_id):
            return server_name == SERVER
        return self.get_server_permission(user_id, server_name) is not None

    def is_admin(self, user_id):
        return user_id == ADMIN.id

    async def get_client(self, server_name):
        return self._client


@pytest.fixture
def env(monkeypatch):
    client = FakeClient()
    cm = FakeConfigManager(client)
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(settings_routes.router)
    return app


def as_user(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def get_config(app, user):
    resp = as_user(app, user).get(
        f"/settings/gateway/networks/{NETWORK_ID}", params={"server": SERVER}
    )
    assert resp.status_code == 200
    return resp.json()


def test_trader_never_receives_credential_bearing_url_parts(env):
    """Query keys, path keys and userinfo are all masked before the wire."""
    body = get_config(env, TRADER)
    config = body["config"]

    assert body["redacted"] is True
    # Helius-style query-string key.
    assert config["nodeURL"] == "https://mainnet.helius-rpc.com/…"
    assert "super-secret-key" not in str(config)
    # Alchemy/Infura-style path key — including one nested a level down.
    assert config["wsURL"] == "wss://eth-mainnet.g.alchemy.com/…"
    assert "another-secret" not in str(config)
    assert config["retries"]["docsURL"] == "https://mainnet.infura.io/…"
    assert "path-secret" not in str(config)
    # Userinfo credentials.
    assert config["basicAuthURL"] == "https://rpc.example.com/…"
    assert "pass" not in config["basicAuthURL"]


def test_trader_still_sees_the_endpoint_host_and_plain_values(env):
    """Redaction hides the secret, not the network: hosts and scalars survive."""
    config = get_config(env, TRADER)["config"]

    # A bare endpoint carries no secret and stays fully readable.
    assert config["bareURL"] == "https://api.mainnet-beta.solana.com"
    assert config["chain_id"] == 101
    assert config["native_currency_symbol"] == "SOL"


@pytest.mark.parametrize("user", [OWNER, ADMIN], ids=["owner", "admin"])
def test_owner_line_reads_full_values(env, user):
    """Owners edit these values, so they receive them verbatim; admins keep
    the bypass `_require_owner` grants everywhere else in this module."""
    body = get_config(env, user)

    assert body["redacted"] is False
    assert body["config"] == CONFIG
