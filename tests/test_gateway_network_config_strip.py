"""Network config saves strip surrounding whitespace (hummingbot-api#233).

Gateway stores a network config's string values verbatim. A nodeURL saved as
" https://..." passes Gateway's schema, then the Solana client rejects it on
the next connection — surfacing as "Endpoint URL must start with http: or
https:" from /wallet/add, far from the save that caused it. The Telegram
flows already strip their input; the dashboard route must too.
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
NETWORK_ID = "solana-mainnet-beta"


class FakeGateway:
    def __init__(self):
        self.calls = []

    async def update_network_config(self, network_id, config):
        self.calls.append((network_id, config))
        return {"ok": True}


class FakeClient:
    def __init__(self):
        self.gateway = FakeGateway()


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


@pytest.fixture
def env(monkeypatch):
    client = FakeClient()
    cm = FakeConfigManager(client)
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    return TestClient(app), client


def test_string_values_reach_gateway_stripped(env):
    http, client = env
    resp = http.post(
        f"/settings/gateway/networks/{NETWORK_ID}",
        params={"server": SERVER},
        json={
            "config": {
                "nodeURL": " https://api.mainnet-beta.solana.com\n",
                "nativeCurrencySymbol": "SOL ",
            }
        },
    )
    assert resp.status_code == 200
    assert client.gateway.calls == [
        (
            NETWORK_ID,
            {
                "nodeURL": "https://api.mainnet-beta.solana.com",
                "nativeCurrencySymbol": "SOL",
            },
        )
    ]


def test_non_string_values_pass_through_untouched(env):
    http, client = env
    config = {"chainID": 101, "useHeliusRestRPC": False, "defaultNetworks": ["a"]}
    resp = http.post(
        f"/settings/gateway/networks/{NETWORK_ID}",
        params={"server": SERVER},
        json={"config": config},
    )
    assert resp.status_code == 200
    assert client.gateway.calls == [(NETWORK_ID, config)]
