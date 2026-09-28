"""The Settings → Hummingbot API panel's three routes (FEAT-121).

The panel reads what a server runs and writes the client defaults every bot deployed
from it next will inherit. Three things have to hold, and none of them is the happy path:

* **Writing is OWNER, reading is TRADER.** Rewriting ``conf_client.yml`` decides how the
  owner's next bot is configured — infrastructure, the same line SEC-153/SEC-166 drew for
  credentials and the gateway. Reading a rate oracle source is not a secret, and a trader
  who can deploy needs to see what the deploy will use.
* **A 404 is not an outage.** These routes are new. A server that has not been upgraded
  answers 404 to a perfectly well-formed request, and the panel must say "upgrade this
  server's API", not "the server is down" — two different actions for the operator.
* **An invalid value keeps its own status.** Upstream owns validation (its bundled
  hummingbot is the authority on which oracle sources exist), so its 422 has to reach the
  caller as a 4xx naming the value, not as a 502.

The routes go through ``condor.fetchers.raw_api`` rather than the pinned client, so the
doubles here are a fake aiohttp session hung off ``client.bot_orchestration`` — the same
shape ``tests/test_performance_history.py`` uses, and the shape the real client has.
"""

import json

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

P = {"server": SERVER}

SYSTEM_INFO = {
    "api_version": "1.0.1",
    "hummingbot_version": "20260616",
    "market_data": {"ticker_update_interval": 30, "ticker_max_age": 60},
    "docker_available": True,
    "container": {
        "image": "hummingbot/hummingbot-api:latest",
        "digest": "hummingbot/hummingbot-api@sha256:abc123",
        "compose_project": "hummingbot-api",
    },
    "pinned": False,
    "pinned_reason": None,
    "override_file": None,
}

CLIENT_CONFIG = {
    "account_name": "master_account",
    "rate_oracle_source": {"name": "gate_io"},
    "global_token": {"global_token_name": "USDT", "global_token_symbol": "$"},
    "rate_limits_share_pct": 100.0,
    "available_sources": ["binance", "gate_io", "kucoin"],
}


# ── Doubles ──


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = {} if body is None else body
        self.request_info = None
        self.history = ()
        self.headers = {}

    @property
    def ok(self):
        return 200 <= self.status < 400

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Answers each path from a canned table and records what it was asked."""

    def __init__(self, responses=None):
        # path -> FakeResponse, or a single FakeResponse for every path.
        self.responses = responses if responses is not None else {}
        self.calls = []

    def _answer(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(self.responses, FakeResponse):
            return self.responses
        for path, response in self.responses.items():
            if url.endswith(path) or path in url:
                return response
        return FakeResponse(404, {"detail": "Not Found"})

    def get(self, url, **kwargs):
        return self._answer("get", url, kwargs)

    def put(self, url, **kwargs):
        return self._answer("put", url, kwargs)


class FakeClient:
    def __init__(self, session, base_url="http://api:8000"):
        self.bot_orchestration = type(
            "R", (), {"session": session, "base_url": base_url}
        )()


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


def build(monkeypatch, responses=None):
    """An app wired to a fake server answering ``responses``."""
    if responses is None:
        responses = {
            "/system/info": FakeResponse(200, SYSTEM_INFO),
            "/bot-orchestration/rate-oracle/config": FakeResponse(200, CLIENT_CONFIG),
        }
    session = FakeSession(responses)
    cm = FakeConfigManager(FakeClient(session))
    monkeypatch.setattr(settings_routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(settings_routes.router)
    return app, session


@pytest.fixture
def env(monkeypatch):
    return build(monkeypatch)


def as_user(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# ── Reading ──


def test_info_reports_what_the_server_runs(env):
    app, _ = env
    body = as_user(app, OWNER).get("/settings/api/info", params=P).json()

    assert body["api_version"] == "1.0.1"
    assert body["hummingbot_version"] == "20260616"
    assert body["container"]["image"] == "hummingbot/hummingbot-api:latest"
    assert body["pinned"] is False
    assert body["market_data"]["ticker_update_interval"] == 30


def test_client_config_reports_the_defaults_and_the_choices(env):
    app, _ = env
    body = as_user(app, OWNER).get("/settings/api/client-config", params=P).json()

    assert body["rate_oracle_source"]["name"] == "gate_io"
    assert body["global_token"]["global_token_name"] == "USDT"
    assert body["rate_limits_share_pct"] == 100.0
    # The source list comes from the server, not a constant here: which sources exist is
    # whatever that server's bundled hummingbot knows.
    assert body["available_sources"] == ["binance", "gate_io", "kucoin"]


@pytest.mark.parametrize("path", ["/settings/api/info", "/settings/api/client-config"])
def test_a_trader_may_read(env, path):
    app, _ = env
    assert as_user(app, TRADER).get(path, params=P).status_code == 200


@pytest.mark.parametrize("path", ["/settings/api/info", "/settings/api/client-config"])
def test_a_stranger_reaches_neither(monkeypatch, path):
    app, _ = build(monkeypatch)
    stranger = WebUser(id=99, username="nobody", first_name="N", role="user")
    assert as_user(app, stranger).get(path, params=P).status_code == 403


def test_the_read_asks_for_the_account_condor_deploys_from(env):
    app, session = env
    as_user(app, OWNER).get("/settings/api/client-config", params=P)

    method, url, kwargs = session.calls[-1]
    assert method == "get"
    # Condor deploys with credentials_profile master_account; asking about any other
    # profile would report defaults no Condor deploy will ever use.
    assert kwargs["params"] == {"account_name": "master_account"}


# ── Writing ──


def test_owner_can_change_the_defaults(env):
    app, session = env
    resp = as_user(app, OWNER).put(
        "/settings/api/client-config",
        params=P,
        json={"rate_oracle_source": "binance", "rate_limits_share_pct": 50},
    )

    assert resp.status_code == 200
    method, url, kwargs = session.calls[-1]
    assert method == "put"
    assert url.endswith("/bot-orchestration/rate-oracle/config")
    assert kwargs["json"] == {
        "rate_oracle_source": {"name": "binance"},
        "rate_limits_share_pct": 50,
    }


def test_a_trader_cannot_write(env):
    app, session = env
    resp = as_user(app, TRADER).put(
        "/settings/api/client-config", params=P, json={"rate_oracle_source": "binance"}
    )

    assert resp.status_code == 403
    # Refused before the wire, not after: nothing was asked of the server.
    assert not any(method == "put" for method, _, _ in session.calls)


def test_an_admin_without_a_grant_may_still_write(env):
    # Mirrors _require_owner, which lets an admin through on any server.
    app, _ = env
    resp = as_user(app, ADMIN).put(
        "/settings/api/client-config", params=P, json={"rate_oracle_source": "binance"}
    )
    assert resp.status_code == 200


def test_only_the_named_fields_are_sent(env):
    app, session = env
    as_user(app, OWNER).put(
        "/settings/api/client-config", params=P, json={"global_token_name": "USDC"}
    )

    _, _, kwargs = session.calls[-1]
    # A save that moves the token must not restate the oracle source, or it would write
    # back whatever the form happened to be holding.
    assert kwargs["json"] == {"global_token": {"global_token_name": "USDC"}}


@pytest.mark.parametrize("bad", [0, -1, 100.5, 1000])
def test_a_share_outside_hummingbots_bound_never_reaches_the_server(env, bad):
    app, session = env
    resp = as_user(app, OWNER).put(
        "/settings/api/client-config", params=P, json={"rate_limits_share_pct": bad}
    )

    assert resp.status_code == 422
    assert not any(method == "put" for method, _, _ in session.calls)


def test_a_blank_value_is_refused_rather_than_written(env):
    app, session = env
    resp = as_user(app, OWNER).put(
        "/settings/api/client-config", params=P, json={"global_token_name": "   "}
    )

    assert resp.status_code == 422
    assert not any(method == "put" for method, _, _ in session.calls)


def test_a_padded_value_is_trimmed_before_it_is_sent(env):
    app, session = env
    as_user(app, OWNER).put(
        "/settings/api/client-config",
        params=P,
        json={"rate_oracle_source": " binance "},
    )

    _, _, kwargs = session.calls[-1]
    assert kwargs["json"] == {"rate_oracle_source": {"name": "binance"}}


# ── An old server, and a broken one ──


@pytest.mark.parametrize(
    "call",
    [
        lambda http: http.get("/settings/api/info", params=P),
        lambda http: http.get("/settings/api/client-config", params=P),
        lambda http: http.put(
            "/settings/api/client-config",
            params=P,
            json={"rate_oracle_source": "binance"},
        ),
    ],
    ids=["info", "read-config", "write-config"],
)
def test_a_server_whose_api_predates_the_routes_says_upgrade(monkeypatch, call):
    # 404 everywhere: the default table in FakeSession answers 404 for unknown paths.
    app, _ = build(monkeypatch, responses={})
    resp = call(as_user(app, OWNER))

    # 501, not 502: the server is fine, the panel is newer than it. The operator's
    # action is "upgrade this server", not "find out why it is down".
    assert resp.status_code == 501
    assert "older than this panel" in resp.json()["detail"]


def test_an_invalid_value_keeps_the_servers_own_status_and_message(monkeypatch):
    app, _ = build(
        monkeypatch,
        responses={
            "/bot-orchestration/rate-oracle/config": FakeResponse(
                422, {"detail": "Invalid rate oracle source: nope"}
            )
        },
    )
    resp = as_user(app, OWNER).put(
        "/settings/api/client-config", params=P, json={"rate_oracle_source": "nope"}
    )

    # A 4xx, not a 502: the caller sent a bad value, the server is not broken.
    assert 400 <= resp.status_code < 500
    assert "nope" in resp.json()["detail"]


def test_an_upstream_failure_never_echoes_the_raw_body(monkeypatch):
    # An upstream error page can be anything -- a stack trace, a page naming the backend
    # host. raw_api.detail exists so none of it reaches a browser.
    app, _ = build(
        monkeypatch,
        responses={
            "/system/info": FakeResponse(500, "<html>nginx at api-internal:8000</html>")
        },
    )
    resp = as_user(app, OWNER).get("/settings/api/info", params=P)

    assert resp.status_code == 502
    assert "nginx" not in json.dumps(resp.json())
    assert "api-internal" not in json.dumps(resp.json())
