"""Upgrading a server's hummingbot-api from the dashboard (FEAT-122).

The three routes that let an owner replace a remote server's API container with the
published image. Almost everything worth pinning here is a refusal, because the failure
mode of this feature is a trading API server that does not come back:

* **The server decides whether it is safe, and Condor does not second-guess it.** The
  preflight's ``can_upgrade: false`` — a pinned image, a registry that cannot be read, a
  Docker daemon that is not there — is a normal 200 answer that the panel renders as a
  reason. A second opinion here is a second opinion that can disagree with the one that
  actually runs.
* **Only an owner may start one, and the refusal happens before the server is asked.**
  It rewrites what code runs on the owner's host and closes every running executor on the
  way, which is the line the gateway pull already draws.
* **Consent is not assumed.** An absent body means ``acknowledge_executor_loss: false``,
  and the server answers 409 rather than upgrading. That 409 reaches the browser as a
  4xx carrying the server's own reason, never as a 502 that reads like an outage.
* **A server that is restarting is not a server that is down.** The upgrade's whole point
  is to replace the container answering the status poll, so a transport failure there is
  ``phase: "restarting"``. Anything that actually answered over HTTP keeps its status.

The doubles are a fake aiohttp session hung off ``client.bot_orchestration``, the shape
``condor.fetchers.raw_api`` reaches through and the shape the real client has.
"""

import asyncio

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
TRADER = WebUser(id=2, username="trader", first_name="T", role="user")

P = {"server": SERVER}

PREFLIGHT_PATH = "/system/upgrade/preflight"
UPGRADE_PATH = "/system/upgrade"
STATUS_PATH = "/system/upgrade/status"

READY = {
    "service": "hummingbot-api",
    "helper_image": "docker:27-cli",
    "image_ref": "hummingbot/hummingbot-api:latest",
    "current_digest": "hummingbot/hummingbot-api@sha256:aaa",
    "available_digest": "sha256:bbb",
    "up_to_date": False,
    "pinned": False,
    "pinned_reason": None,
    "override_file": None,
    "compose": {
        "project": "hummingbot-api",
        "working_dir": "/root/hummingbot-api",
        "config_files": ["/root/hummingbot-api/docker-compose.yml"],
    },
    "running_executors": 4,
    "running_bots": 2,
    "can_upgrade": True,
    "blocked_reason": None,
}

PINNED = dict(
    READY,
    pinned=True,
    pinned_reason="compose override file docker-compose.override.yml can pin the image",
    override_file="docker-compose.override.yml",
    can_upgrade=False,
    blocked_reason=(
        "This server's image is pinned: compose override file docker-compose.override.yml "
        "can pin the image. Pulling the published image would undo that, so it is refused. "
        "Upgrade this server over SSH."
    ),
)

DONE = {
    "run_id": "abc123",
    "phase": "done",
    "detail": "Upgrade finished; this API is the new container.",
    "previous_digest": "hummingbot/hummingbot-api@sha256:aaa",
    "new_digest": "hummingbot/hummingbot-api@sha256:bbb",
    "exit_code": 0,
    "log_tail": ["Container hummingbot-api Started"],
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


class Raises:
    """A request that never gets an HTTP answer at all — the restart window."""

    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        raise self.exc

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, responses=None):
        self.responses = responses if responses is not None else {}
        self.calls = []

    def _answer(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        for path, response in self.responses.items():
            if url.endswith(path):
                return response
        return FakeResponse(404, {"detail": "Not Found"})

    def get(self, url, **kwargs):
        return self._answer("get", url, kwargs)

    def post(self, url, **kwargs):
        return self._answer("post", url, kwargs)

    def put(self, url, **kwargs):
        return self._answer("put", url, kwargs)


class FakeClient:
    def __init__(self, session, base_url="http://api:8000"):
        self.bot_orchestration = type(
            "R", (), {"session": session, "base_url": base_url}
        )()


class FakeConfigManager:
    """Owner id 1, trader id 2, anyone else a stranger."""

    def __init__(self, client, get_client_error=None):
        self._client = client
        self._get_client_error = get_client_error

    def get_server_permission(self, user_id, server_name):
        if server_name != SERVER:
            return None
        if user_id == OWNER.id:
            return ServerPermission.OWNER
        if user_id == TRADER.id:
            return ServerPermission.TRADER
        return None

    def has_server_access(self, user_id, server_name, min_permission=None):
        return self.get_server_permission(user_id, server_name) is not None

    def is_admin(self, user_id):
        return False

    async def get_client(self, server_name):
        if self._get_client_error is not None:
            raise self._get_client_error
        return self._client


def build(monkeypatch, responses=None, get_client_error=None):
    if responses is None:
        responses = {
            PREFLIGHT_PATH: FakeResponse(200, READY),
            STATUS_PATH: FakeResponse(200, DONE),
            UPGRADE_PATH: FakeResponse(202, {"run_id": "abc123", "phase": "pulling"}),
        }
    session = FakeSession(responses)
    cm = FakeConfigManager(FakeClient(session), get_client_error)
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


# ── Preflight ──


def test_preflight_reports_what_the_upgrade_would_cost(env):
    app, _ = env
    body = as_user(app, OWNER).get("/settings/api/upgrade/preflight", params=P).json()

    assert body["can_upgrade"] is True
    assert body["running_executors"] == 4
    assert body["running_bots"] == 2
    assert body["available_digest"] == "sha256:bbb"


def test_a_refusal_is_an_answer_not_an_error(monkeypatch):
    """A pinned server is the normal case on a hand-managed host, not a fault."""
    app, _ = build(monkeypatch, {PREFLIGHT_PATH: FakeResponse(200, PINNED)})

    response = as_user(app, OWNER).get("/settings/api/upgrade/preflight", params=P)

    assert response.status_code == 200
    body = response.json()
    assert body["can_upgrade"] is False
    assert body["override_file"] == "docker-compose.override.yml"
    assert "docker-compose.override.yml" in body["blocked_reason"]


def test_a_trader_may_read_the_preflight(env):
    app, _ = env
    assert (
        as_user(app, TRADER)
        .get("/settings/api/upgrade/preflight", params=P)
        .status_code
        == 200
    )


def test_a_stranger_reaches_nothing(env):
    app, _ = env
    stranger = WebUser(id=99, username="nobody", first_name="N", role="user")
    client = as_user(app, stranger)

    assert client.get("/settings/api/upgrade/preflight", params=P).status_code == 403
    assert client.post("/settings/api/upgrade", params=P, json={}).status_code == 403
    assert client.get("/settings/api/upgrade/status", params=P).status_code == 403


# ── Starting ──


def test_only_an_owner_may_start_one_and_the_server_is_never_asked(env):
    """The refusal happens here, before anything reaches the server."""
    app, session = env

    response = as_user(app, TRADER).post("/settings/api/upgrade", params=P, json={})

    assert response.status_code == 403
    assert session.calls == []


def test_an_empty_body_acknowledges_nothing(env):
    """Consent is never assumed: what the server receives is an explicit False."""
    app, session = env

    as_user(app, OWNER).post("/settings/api/upgrade", params=P, json={})

    method, url, kwargs = session.calls[-1]
    assert method == "post"
    assert url.endswith(UPGRADE_PATH)
    assert kwargs["json"] == {"acknowledge_executor_loss": False}


def test_an_acknowledged_executor_loss_is_forwarded(env):
    app, session = env

    response = as_user(app, OWNER).post(
        "/settings/api/upgrade", params=P, json={"acknowledge_executor_loss": True}
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == "abc123"
    assert session.calls[-1][2]["json"] == {"acknowledge_executor_loss": True}


def test_the_servers_refusal_reaches_the_caller_with_its_reason(monkeypatch):
    """A 409 is the caller's request being refused, not this gateway failing."""
    app, _ = build(
        monkeypatch,
        {
            UPGRADE_PATH: FakeResponse(
                409,
                {"detail": "4 executor(s) are running. Confirm that loss to proceed."},
            )
        },
    )

    response = as_user(app, OWNER).post("/settings/api/upgrade", params=P, json={})

    assert response.status_code == 400
    assert "4 executor(s) are running" in response.json()["detail"]


def test_an_upstream_body_is_never_echoed(monkeypatch):
    """An upstream error page can name the backend host; only `detail` is forwarded."""
    app, _ = build(
        monkeypatch,
        {
            UPGRADE_PATH: FakeResponse(
                502, "<html>nginx: upstream api-internal:8000</html>"
            )
        },
    )

    response = as_user(app, OWNER).post("/settings/api/upgrade", params=P, json={})

    assert response.status_code == 502
    assert "api-internal" not in response.json()["detail"]


# ── An older server ──


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/settings/api/upgrade/preflight"),
        ("post", "/settings/api/upgrade"),
        ("get", "/settings/api/upgrade/status"),
    ],
)
def test_a_server_without_the_routes_is_told_to_upgrade_over_ssh(
    monkeypatch, method, path
):
    """404 is a capability answer: this server is too old to upgrade itself."""
    app, _ = build(monkeypatch, {})  # every path 404s
    client = as_user(app, OWNER)

    response = (
        client.post(path, params=P, json={})
        if method == "post"
        else client.get(path, params=P)
    )

    assert response.status_code == 501
    assert "SSH" in response.json()["detail"]


# ── Following it across the restart ──


def test_status_serves_the_collected_run(env):
    app, _ = env
    body = as_user(app, OWNER).get("/settings/api/upgrade/status", params=P).json()

    assert body["phase"] == "done"
    assert body["exit_code"] == 0
    assert body["log_tail"] == ["Container hummingbot-api Started"]


def test_status_is_owner_only(env):
    app, _ = env
    assert (
        as_user(app, TRADER).get("/settings/api/upgrade/status", params=P).status_code
        == 403
    )


@pytest.mark.parametrize(
    "failure",
    [
        aiohttp.ClientConnectionError("connection refused"),
        asyncio.TimeoutError(),
        OSError("host is down"),
    ],
)
def test_a_server_that_does_not_answer_is_restarting_not_broken(monkeypatch, failure):
    """The upgrade replaces the container answering this poll; that is the point."""
    app, _ = build(monkeypatch, {STATUS_PATH: Raises(failure)})

    response = as_user(app, OWNER).get("/settings/api/upgrade/status", params=P)

    assert response.status_code == 200
    assert response.json()["phase"] == "restarting"


def test_a_client_that_cannot_be_built_is_also_restarting(monkeypatch):
    """The config manager fails fast on a host that just refused; still the restart."""
    app, _ = build(
        monkeypatch, get_client_error=ConnectionError("'alpha' is unreachable")
    )

    body = as_user(app, OWNER).get("/settings/api/upgrade/status", params=P).json()

    assert body["phase"] == "restarting"


def test_an_unknown_server_is_not_reported_as_restarting(monkeypatch):
    """ "No such server" is a configuration mistake, and waiting it out never helps."""
    app, _ = build(monkeypatch, get_client_error=ValueError("Server 'alpha' not found"))

    response = as_user(app, OWNER).get("/settings/api/upgrade/status", params=P)

    assert response.status_code == 502
    assert "not found" in response.json()["detail"]


def test_a_server_that_answered_with_an_error_is_not_restarting(monkeypatch):
    """It replied, so it is up: a 500 here is a real failure and must look like one."""
    app, _ = build(monkeypatch, {STATUS_PATH: FakeResponse(500, {"detail": "boom"})})

    response = as_user(app, OWNER).get("/settings/api/upgrade/status", params=P)

    assert response.status_code == 502
    assert "restarting" not in str(response.json())
