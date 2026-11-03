"""CORR-260: the history route's ``limit`` matches what upstream will serve.

The route used to advertise ``le=5000`` while the Hummingbot API caps the page
at 1000, so a limit of 1001..5000 — a value Condor itself validated as fine —
came back from upstream as a 422, was swallowed by the route's except block and
reached the dashboard as ``server_online=false``: a parameter mistake rendered
as an offline server. The other half of the mismatch was the missing default —
an omitted ``limit`` was never forwarded, so upstream applied its own default of
100 rows and the caller got minutes of history with no sign of the truncation.

The ceiling is now 1000 (rejected up front by FastAPI, with a message that says
which parameter is wrong) and the default is a full page.
"""

import asyncio

from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.controller_performance as cp
from condor.web.auth import require_server_access
from condor.web.models import WebUser

USER = WebUser(id=7, username="u", first_name="U", role="user")

ROW = {
    "timestamp": "2026-08-27T10:05:00+00:00",
    "controller_id": "ctrl-0",
    "performance": {"realized_pnl_quote": 1.0, "volume_traded": 10.0},
}


class _Client:
    """Upstream, recording the query it was asked for."""

    def __init__(self):
        self.calls: list[dict] = []

    @property
    def bot_orchestration(self):
        return self

    async def get_controller_performance_history(self, **kw):
        self.calls.append(kw)
        return {"status": "success", "data": [ROW]}


class _Cm:
    def __init__(self, client):
        self._client = client

    async def get_client(self, name):
        return self._client


def _app(monkeypatch):
    client = _Client()
    monkeypatch.setattr(cp, "get_config_manager", lambda: _Cm(client), raising=True)
    app = FastAPI()
    app.include_router(cp.router)
    app.dependency_overrides[require_server_access] = lambda: USER
    return TestClient(app), client


def test_limit_above_upstreams_cap_is_rejected_as_a_bad_parameter(monkeypatch):
    """2000 is a 422 naming ``limit``, not a 200 body claiming the server is down."""
    api, client = _app(monkeypatch)

    r = api.get("/servers/srv/controller-performance/history?limit=2000")

    assert r.status_code == 422
    assert "limit" in str(r.json())
    # Rejected before the client was ever asked, so nothing could be mistaken
    # for a connection failure.
    assert client.calls == []


def test_the_ceiling_upstream_serves_is_still_accepted(monkeypatch):
    """1000 — what every frontend call asks for — goes through untouched."""
    api, client = _app(monkeypatch)

    r = api.get("/servers/srv/controller-performance/history?limit=1000")

    assert r.status_code == 200
    assert r.json()["server_online"] is True
    assert client.calls[0]["limit"] == 1000


def test_an_omitted_limit_asks_for_a_full_page(monkeypatch):
    """No ``limit`` forwards 1000, instead of letting upstream default to 100."""
    api, client = _app(monkeypatch)

    r = api.get("/servers/srv/controller-performance/history")

    assert r.status_code == 200
    assert client.calls[0]["limit"] == 1000


def test_the_route_still_reports_a_real_connection_failure(monkeypatch):
    """A raising client is what ``server_online=false`` is reserved for."""

    class _Boom(_Client):
        async def get_controller_performance_history(self, **kw):
            raise RuntimeError("connection refused")

    client = _Boom()
    monkeypatch.setattr(cp, "get_config_manager", lambda: _Cm(client), raising=True)

    body = asyncio.run(
        cp.get_controller_performance_history(
            "srv",
            bot_name=None,
            controller_id=None,
            start_time=None,
            end_time=None,
            interval="5m",
            limit=1000,
            cursor=None,
            user=USER,
        )
    )

    assert body.server_online is False
    assert "connection refused" in body.error_hint
