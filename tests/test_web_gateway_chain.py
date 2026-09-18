"""``GET /settings/gateway/chain``: surfpool or node, from Gateway's own RPC.

Pins the answers the route may give — a classification, and a 502 that names
the server or the host but never a URL — and that a TRADER may read it, since
knowing which chain you trade on is not a mutation. There is no "which
Gateway" answer to pin: the route reaches the server's own through its
hummingbot-api, which is the one its bots trade through.

The answer carries the RPC *URL* as well as its host: the browser's wallet
layer configures a Solana cluster with one and reads the chain itself. What is
pinned here is that the two are separate fields, so a surface that only wants
something to show still has a host to show.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.settings as settings_routes
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import ServerPermission

SERVER = "vaults"
OWNER = WebUser(id=1, username="owner", first_name="O", role="user")
TRADER = WebUser(id=2, username="trader", first_name="T", role="user")


class FakeConfigManager:
    def __init__(self, record):
        self._record = record

    def get_server(self, name):
        return self._record if name == SERVER else None

    async def get_client(self, name):
        if name != SERVER:
            raise ValueError(f"Server '{name}' not found")
        return object()

    def get_server_permission(self, user_id, server_name):
        if server_name != SERVER:
            return None
        return {OWNER.id: ServerPermission.OWNER, TRADER.id: ServerPermission.TRADER}.get(user_id)

    def has_server_access(self, user_id, server_name, min_permission=None):
        return self.get_server_permission(user_id, server_name) is not None

    def is_admin(self, user_id):
        return False


def make_app(monkeypatch, record):
    cm = FakeConfigManager(record)
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(settings_routes.router)
    return app


def as_user(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def configured(monkeypatch):
    app = make_app(monkeypatch, {"host": "localhost", "port": 8001})

    async def fake_rpc_url(self, network="mainnet-beta"):
        return "http://127.0.0.1:8899"

    monkeypatch.setattr(settings_routes.GatewayClient, "solana_rpc_url", fake_rpc_url)
    return app


def test_surfpool_answer_carries_the_url_and_the_host(configured, monkeypatch):
    async def fake_probe(url):
        assert url == "http://127.0.0.1:8899"
        return {"kind": "surfpool", "rpc_host": "127.0.0.1:8899", "rpc_url": url,
                "surfnet_version": "1.5.0", "solana_core": "4.1.2", "slot": 42}

    monkeypatch.setattr(settings_routes, "probe_rpc", fake_probe)
    r = as_user(configured, TRADER).get("/settings/gateway/chain", params={"server": SERVER})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "surfpool"
    # The URL is what a cluster is configured with; the host is what a badge
    # shows. Both, separately, so neither surface has to derive the other.
    assert body["rpc_url"] == "http://127.0.0.1:8899"
    assert body["rpc_host"] == "127.0.0.1:8899"
    assert "http://" not in body["rpc_host"]


def test_rpc_down_is_a_502_naming_the_host_not_the_url(configured, monkeypatch):
    async def down(url):
        raise ConnectionError("refused")

    monkeypatch.setattr(settings_routes, "probe_rpc", down)
    r = as_user(configured, OWNER).get("/settings/gateway/chain", params={"server": SERVER})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "127.0.0.1:8899" in detail
    assert "http://" not in detail


def test_gateway_that_cannot_name_its_rpc_is_a_502_naming_the_server(monkeypatch):
    app = make_app(monkeypatch, {"host": "localhost", "port": 8001})

    async def no_rpc(self, network="mainnet-beta"):
        raise RuntimeError("Gateway reports no RPC")

    monkeypatch.setattr(settings_routes.GatewayClient, "solana_rpc_url", no_rpc)
    r = as_user(app, OWNER).get("/settings/gateway/chain", params={"server": SERVER})
    assert r.status_code == 502
    assert SERVER in r.json()["detail"]


def test_other_chains_are_refused(configured):
    r = as_user(configured, OWNER).get("/settings/gateway/chain", params={"server": SERVER, "chain": "ethereum"})
    assert r.status_code == 400


def test_a_stranger_cannot_read_it(configured):
    stranger = WebUser(id=9, username="s", first_name="S", role="user")
    r = as_user(configured, stranger).get("/settings/gateway/chain", params={"server": SERVER})
    assert r.status_code in (403, 404)
