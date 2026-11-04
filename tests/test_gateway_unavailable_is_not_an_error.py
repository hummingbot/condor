"""A server without Gateway is a configuration, not a failure (#215).

Condor reads the Gateway-backed panels for whichever API server is selected. A
server that is not running Gateway answers ``503 Gateway service is not
available``; the wallets and networks routes logged that as ERROR with a full
traceback and answered 502, so switching between a Gateway server and a
non-Gateway one filled the log with tracebacks. ``/gateway/status`` already
reports the same state as ``running: false`` without a word.
"""

import logging

import aiohttp
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.settings as settings_routes
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import ServerPermission

SERVER = "alpha"
OWNER = WebUser(id=1, username="owner", first_name="O", role="user")


def _response_error(status: int, message: str) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(
        request_info=None, history=(), status=status, message=message
    )


class FakeAccounts:
    def __init__(self, outcome):
        self._outcome = outcome

    async def list_gateway_wallets(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class FakeGateway:
    def __init__(self, outcome):
        self._outcome = outcome

    async def list_networks(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class FakeClient:
    def __init__(self, outcome):
        self.accounts = FakeAccounts(outcome)
        self.gateway = FakeGateway(outcome)


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


def _http(monkeypatch, outcome):
    cm = FakeConfigManager(FakeClient(outcome))
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    return TestClient(app)


GATEWAY_DOWN = "Gateway service is not available"


@pytest.mark.parametrize(
    "path,key",
    [
        ("/settings/gateway/wallets", "wallets"),
        ("/settings/gateway/networks", "networks"),
    ],
)
def test_gateway_not_running_is_an_empty_panel_and_no_error_log(
    monkeypatch, caplog, path, key
):
    http = _http(monkeypatch, _response_error(503, GATEWAY_DOWN))

    with caplog.at_level(logging.INFO):
        resp = http.get(path, params={"server": SERVER})

    assert resp.status_code == 200
    assert resp.json() == {key: [], "gateway_available": False}
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert any("Gateway is not running" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "path", ["/settings/gateway/wallets", "/settings/gateway/networks"]
)
def test_a_503_that_is_not_about_gateway_stays_an_error(monkeypatch, caplog, path):
    http = _http(monkeypatch, _response_error(503, "Service Unavailable"))

    with caplog.at_level(logging.INFO):
        resp = http.get(path, params={"server": SERVER})

    assert resp.status_code == 502
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    "path", ["/settings/gateway/wallets", "/settings/gateway/networks"]
)
def test_an_upstream_500_stays_an_error(monkeypatch, path):
    http = _http(monkeypatch, _response_error(500, "Gateway blew up"))

    resp = http.get(path, params={"server": SERVER})

    assert resp.status_code == 502


def test_wallets_are_returned_with_gateway_available(monkeypatch):
    http = _http(monkeypatch, [{"chain": "solana", "default_address": "abc"}])

    resp = http.get("/settings/gateway/wallets", params={"server": SERVER})

    assert resp.status_code == 200
    assert resp.json() == {
        "wallets": [{"chain": "solana", "default_address": "abc"}],
        "gateway_available": True,
    }
