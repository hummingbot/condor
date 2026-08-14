"""Gateway container, wallet and network mutations are owner-only (SEC-166).

SEC-153 drew the line for exchange credentials: mutating a server's
*configuration* is not a trading action, so it needs OWNER. The gateway routes
sat at TRADER, which asserted the opposite for changes that are at least as
consequential — a shared trader could stop the container every bot on the
server trades DEX through, import or delete private keys in the owner's
keystore, silently repoint the default signing wallet, or rewrite the RPC
endpoint every transaction is broadcast to.

These tests pin the three route classes separately, and pin the reads that
deliberately *stay* at TRADER: a trader must still be able to see whether the
gateway is up and which wallets exist, otherwise they cannot even tell the
owner what is broken.
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

CHAIN = "solana"
ADDRESS = "So11111111111111111111111111111111111111112"


class FakeGateway:
    def __init__(self):
        self.calls = []

    async def start(self, cfg):
        self.calls.append(("start", cfg))
        return {"ok": True}

    async def stop(self):
        self.calls.append(("stop", None))
        return {"ok": True}

    async def restart(self):
        self.calls.append(("restart", None))
        return {"ok": True}

    async def update_network_config(self, network_id, config):
        self.calls.append(("update_network", network_id, config))
        return {"ok": True}


class FakeDocker:
    def __init__(self):
        self.pulled = []

    async def pull_image(self, image_name, tag):
        self.pulled.append((image_name, tag))
        return {"ok": True}


class FakeAccounts:
    def __init__(self):
        self.calls = []

    async def list_gateway_wallets(self):
        return [{"chain": CHAIN, "default_address": ADDRESS, "wallets": [ADDRESS]}]

    async def add_gateway_wallet(self, chain, private_key):
        self.calls.append(("add", chain))
        return {"address": ADDRESS}

    async def set_default_gateway_wallet(self, chain, address):
        self.calls.append(("set_default", chain, address))
        return {"ok": True}

    async def remove_gateway_wallet(self, chain, address):
        self.calls.append(("remove", chain, address))
        return {"ok": True}


class FakeClient:
    def __init__(self):
        self.gateway = FakeGateway()
        self.docker = FakeDocker()
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


@pytest.fixture
def env(monkeypatch):
    client = FakeClient()
    cm = FakeConfigManager(client)
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(settings_routes.router)
    return app, client


def as_user(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


P = {"server": SERVER}


# ── The mutating calls, one per route, grouped by class ──

LIFECYCLE = {
    "pull": lambda http: http.post(
        "/settings/gateway/pull", params=P, json={"image": "hummingbot/gateway:latest"}
    ),
    "start": lambda http: http.post(
        "/settings/gateway/start",
        params=P,
        json={"image": "hummingbot/gateway:latest", "port": 15888},
    ),
    "stop": lambda http: http.post("/settings/gateway/stop", params=P),
    "restart": lambda http: http.post("/settings/gateway/restart", params=P),
}

WALLETS = {
    "add": lambda http: http.post(
        "/settings/gateway/wallets",
        params=P,
        json={"chain": CHAIN, "private_key": "s3cret", "set_default": True},
    ),
    "set_default": lambda http: http.post(
        "/settings/gateway/wallets/default",
        params=P,
        json={"chain": CHAIN, "address": ADDRESS},
    ),
    "remove": lambda http: http.delete(
        f"/settings/gateway/wallets/{CHAIN}/{ADDRESS}", params=P
    ),
}

NETWORKS = {
    "update": lambda http: http.post(
        "/settings/gateway/networks/mainnet-beta",
        params=P,
        json={"config": {"nodeURL": "https://evil.example/rpc"}},
    ),
}

ALL_MUTATIONS = {**LIFECYCLE, **WALLETS, **NETWORKS}


@pytest.mark.parametrize("name", sorted(LIFECYCLE))
def test_trader_cannot_touch_gateway_lifecycle(env, name):
    """start/stop/restart/pull run containers on the owner's host."""
    app, client = env
    resp = LIFECYCLE[name](as_user(app, TRADER))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Owner access required"
    # Refused before the backend was reached.
    assert client.gateway.calls == []
    assert client.docker.pulled == []


@pytest.mark.parametrize("name", sorted(WALLETS))
def test_trader_cannot_mutate_gateway_wallets(env, name):
    """add/remove/set-default all touch the owner's keystore."""
    app, client = env
    resp = WALLETS[name](as_user(app, TRADER))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Owner access required"
    assert client.accounts.calls == []


@pytest.mark.parametrize("name", sorted(NETWORKS))
def test_trader_cannot_rewrite_network_config(env, name):
    """The RPC endpoint every transaction is broadcast through is config."""
    app, client = env
    resp = NETWORKS[name](as_user(app, TRADER))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Owner access required"
    assert client.gateway.calls == []


@pytest.mark.parametrize("name", sorted(ALL_MUTATIONS))
def test_owner_can_perform_every_gateway_mutation(env, name):
    """The gate must not lock the owner out of their own infrastructure."""
    app, _ = env
    assert ALL_MUTATIONS[name](as_user(app, OWNER)).status_code == 200


@pytest.mark.parametrize("name", sorted(ALL_MUTATIONS))
def test_admin_keeps_the_owner_bypass(env, name):
    """`_require_owner` lets admins through everywhere else in this module."""
    app, _ = env
    assert ALL_MUTATIONS[name](as_user(app, ADMIN)).status_code == 200


def test_owner_mutations_actually_reach_the_backend(env):
    """Guard against a gate that 200s without doing the work."""
    app, client = env
    http = as_user(app, OWNER)
    for call in ALL_MUTATIONS.values():
        assert call(http).status_code == 200

    assert client.docker.pulled == [("hummingbot/gateway", "latest")]
    kinds = [c[0] for c in client.gateway.calls]
    assert kinds == ["start", "stop", "restart", "update_network"]
    assert ("add", CHAIN) in client.accounts.calls
    assert ("remove", CHAIN, ADDRESS) in client.accounts.calls


# ── Reads stay at TRADER on purpose ──


def test_trader_can_still_read_gateway_state(env):
    """A trader keeps the visibility they need to trade and to report breakage."""
    app, client = env
    http = as_user(app, TRADER)

    async def get_pull_status():
        return {"status": "idle"}

    client.docker.get_pull_status = get_pull_status

    assert http.get("/settings/gateway/pull-status", params=P).status_code == 200

    wallets = http.get("/settings/gateway/wallets", params=P)
    assert wallets.status_code == 200
    assert wallets.json()["wallets"][0]["chain"] == CHAIN
