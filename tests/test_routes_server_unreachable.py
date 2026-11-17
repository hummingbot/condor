"""An unreachable trading server is a structured answer, never a bare 500 (WR-02).

``ConfigManager.get_client`` raises before a route's own ``try`` is entered: a
``ConnectionError`` for the whole failure TTL after a failed connect, and the
transport error itself on the attempt that failed. The handlers that were
written to answer ``server_online: false`` wrapped only the *second* await, so
the most common outage skipped them and reached the browser as
``500 Internal Server Error`` with no JSON detail.

Three shapes are pinned here, one per kind of route:

* a route with a degraded model answers 200 ``server_online: false``;
* a route without one answers the 502 ``upstream_error`` gives everything else;
* a route nobody converted is still caught by the app-level backstop.

None of them may carry the backend address.
"""

from types import SimpleNamespace

import pytest
from aiohttp import ClientConnectorError
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.bots as bots_module
import condor.web.routes.controller_performance as cperf_module
from condor.web.auth import get_current_user, require_server_access
from condor.web.models import WebUser

USER = WebUser(id=1, username="u", first_name="U", role="user")

BACKEND_HOST = "10.0.0.7"


def _ttl_error() -> ConnectionError:
    """What ``get_client`` raises while a recent connect failure is fresh."""
    return ConnectionError(
        "Server 's' is unreachable (last attempt 3s ago); not retrying yet"
    )


def _transport_error() -> ClientConnectorError:
    """What the failed attempt itself raises: the host is in the string."""
    return ClientConnectorError(
        SimpleNamespace(host=BACKEND_HOST, port=8000, ssl=None),
        OSError(61, "Connection refused"),
    )


class _DownConfigManager:
    def __init__(self, exc):
        self._exc = exc

    async def get_client(self, _name):
        raise self._exc


@pytest.fixture(params=[_ttl_error, _transport_error], ids=["ttl", "transport"])
def client(request, monkeypatch) -> TestClient:
    cm = _DownConfigManager(request.param())
    monkeypatch.setattr(bots_module, "get_config_manager", lambda: cm)
    monkeypatch.setattr(cperf_module, "get_config_manager", lambda: cm)
    cperf_module.clear_terminated_cache()

    app = FastAPI()
    app.include_router(bots_module.router)
    app.include_router(cperf_module.router)
    app.dependency_overrides[require_server_access] = lambda: USER
    app.dependency_overrides[get_current_user] = lambda: USER
    # A handler that raises must surface as a 500 response here, the way it
    # does in production, rather than as an exception out of the test client.
    return TestClient(app, raise_server_exceptions=False)


DEGRADED = [
    "/servers/s/controller-performance/latest",
    "/servers/s/controller-performance/history",
    "/servers/s/performance/history?subject=controller",
    "/servers/s/terminated/controllers",
]


@pytest.mark.parametrize("url", DEGRADED)
def test_a_route_with_a_degraded_shape_reports_the_server_offline(client, url):
    res = client.get(url)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["server_online"] is False
    assert body["error_hint"]
    assert BACKEND_HOST not in res.text


BAD_GATEWAY = [
    ("get", "/servers/s/bot-runs", None),
    ("delete", "/servers/s/bot-runs/7", None),
    ("get", "/servers/s/terminated/history?bot_name=b&deployed_at=2026-01-01", None),
    ("get", "/servers/s/bots/b", None),
    ("get", "/servers/s/controllers/configs", None),
    ("get", "/servers/s/controllers/configs/c", None),
    ("put", "/servers/s/controllers/configs/c", {"a": 1}),
    ("get", "/servers/s/controllers/generic/pmm/source", None),
    ("put", "/servers/s/controllers/generic/pmm/source", {"source": "x = 1"}),
    ("get", "/servers/s/controllers/generic/pmm/template", None),
    ("post", "/servers/s/controllers/configs", {"id": "c"}),
    ("delete", "/servers/s/controllers/configs/c", None),
    ("delete", "/servers/s/controllers/generic/pmm", None),
    (
        "post",
        "/servers/s/bots/deploy",
        {"bot_name": "b", "controllers_config": ["c"]},
    ),
    ("post", "/servers/s/bots/b/stop", None),
    ("post", "/servers/s/bots/b/controllers/stop", {"controller_names": ["c"]}),
    ("post", "/servers/s/bots/b/controllers/start", {"controller_names": ["c"]}),
    ("put", "/servers/s/bots/b/controllers/c/config", {"a": 1}),
]


@pytest.mark.parametrize("method,url,body", BAD_GATEWAY)
def test_a_route_without_one_answers_a_structured_502(client, method, url, body):
    res = client.request(method.upper(), url, json=body)

    assert res.status_code == 502, res.text
    assert "unreachable" in res.json()["detail"]
    assert BACKEND_HOST not in res.text


def test_an_unknown_server_is_a_404_not_a_500(monkeypatch):
    cm = _DownConfigManager(ValueError("Server 's' not found"))
    monkeypatch.setattr(bots_module, "get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(bots_module.router)
    app.dependency_overrides[require_server_access] = lambda: USER

    res = TestClient(app, raise_server_exceptions=False).get("/servers/s/bots/b")

    assert res.status_code == 404
    assert res.json()["detail"] == "Server 's' not found"


# ── The backstop: a route nobody converted ──


@pytest.fixture
def real_app(monkeypatch):
    from condor.web.app import create_app

    app = create_app()
    app.dependency_overrides[require_server_access] = lambda: USER
    app.dependency_overrides[get_current_user] = lambda: USER
    return app


@pytest.mark.parametrize("make_exc", [_ttl_error, _transport_error])
def test_the_app_turns_a_stray_connection_error_into_the_same_502(real_app, make_exc):
    @real_app.get("/api/v1/_wr02_probe")
    async def _probe():
        raise make_exc()

    # Registered after the SPA catch-all, so move it ahead of that route.
    real_app.router.routes.insert(0, real_app.router.routes.pop())

    res = TestClient(real_app, raise_server_exceptions=False).get("/api/v1/_wr02_probe")

    assert res.status_code == 502
    assert res.json() == {
        "detail": "Cannot connect to server: the trading API is unreachable"
    }
    assert BACKEND_HOST not in res.text


def _raise_from_a_helper(exc):
    raise exc


@pytest.mark.parametrize(
    "make_exc,level",
    [
        (_ttl_error, "WARNING"),
        (_transport_error, "WARNING"),
        # Not what get_client raises: possibly nothing to do with a trading
        # server at all, so it is an error in the log, not a routine warning.
        (lambda: BrokenPipeError(32, "Broken pipe"), "ERROR"),
        (lambda: ConnectionResetError(54, "Connection reset by peer"), "ERROR"),
    ],
)
def test_the_backstop_logs_the_traceback_it_swallows(real_app, caplog, make_exc, level):
    """Review R8: the handler replaced the bare 500 -- and with it the only
    traceback an unrelated BrokenPipeError / ConnectionResetError ever left."""

    @real_app.get("/api/v1/_r8_probe")
    async def _probe():
        _raise_from_a_helper(make_exc())

    real_app.router.routes.insert(0, real_app.router.routes.pop())

    with caplog.at_level("WARNING", logger="condor.web.app"):
        res = TestClient(real_app, raise_server_exceptions=False).get(
            "/api/v1/_r8_probe"
        )

    assert res.status_code == 502  # still never a bare 500
    assert BACKEND_HOST not in res.text
    records = [r for r in caplog.records if r.name == "condor.web.app"]
    assert len(records) == 1
    assert records[0].levelname == level
    assert records[0].exc_info is not None
    assert "_raise_from_a_helper" in caplog.text  # the frame that raised it


def test_the_backstop_covers_an_unconverted_executors_route(real_app, monkeypatch):
    """``executors.py`` still awaits ``get_client`` outside its ``try``."""
    import condor.web.routes.executors as executors_module

    cm = _DownConfigManager(_ttl_error())
    monkeypatch.setattr(executors_module, "get_config_manager", lambda: cm)

    res = TestClient(real_app, raise_server_exceptions=False).get(
        "/api/v1/servers/s/executors"
    )

    # Either the route's own degraded answer or the backstop — never a bare 500.
    assert res.status_code in (200, 502), res.text
    assert res.headers["content-type"].startswith("application/json")
