"""SEC-147: the server-access guard is a dependency, and no route may skip it.

The 403 preamble used to be hand-copied into ~88 endpoints, which made access
control opt-in: SEC-007, SEC-019 and SEC-045 were each a patch for one endpoint
that simply forgot to type it. Centralising it only helps if a *new* endpoint
cannot forget it either, so the regression here is structural rather than
per-endpoint: it walks the real FastAPI app and asserts that every server-scoped
route carries one of the ``require_server_access*`` dependencies, with a small
explicit allowlist for the routes that deliberately answer differently.

Three shapes exist, and they are not interchangeable:

* ``/servers/{name}/...``   → ``require_server_access``
* ``/servers/{server_name}/...`` → ``require_server_access_by_server_name``
* ``?server=...``          → ``require_server_access_query``

and a fourth that no path/query dependency can cover — the server name arriving
in the request **body** — which uses the shared ``check_server_access`` helper
the dependencies are built on. Those sites are pinned by source inspection
below, because there is no signature for FastAPI to reflect on.
"""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from condor.web.app import create_app
from condor.web.auth import (
    check_server_access,
    get_current_user,
    require_server_access,
    require_server_access_by_server_name,
    require_server_access_query,
)
from condor.web.models import WebUser

GUARDS = {
    require_server_access,
    require_server_access_by_server_name,
    require_server_access_query,
}

USER = WebUser(id=7, username="u", first_name="U", role="user")
OWNED = "mine"
FOREIGN = "not-mine"


# ── The reviewed exceptions ──

# Server-scoped routes that deliberately do NOT use the dependency. Each entry
# was reviewed one by one; anything not listed here must carry the guard.
ALLOWLIST: dict[tuple[str, str], str] = {
    # These four answer 404 "Server not found" instead of 403 "No access" on
    # purpose: a caller must not be able to tell an existing server they cannot
    # touch from one that does not exist (no enumeration). The dependency's 403
    # would leak exactly that, so they keep an inline check.
    ("GET", "/api/v1/servers/{name}/status"): "404-shaped, non-enumerable",
    ("PUT", "/api/v1/settings/servers/{name}"): "404-shaped, non-enumerable",
    ("DELETE", "/api/v1/settings/servers/{name}"): "404-shaped, non-enumerable",
    ("POST", "/api/v1/settings/servers/{name}/default"): "404-shaped, non-enumerable",
}

# Routes that read a server but have never had an access check. NOT a licence:
# each is a live gap, listed so the sweep above stays green while they are
# fixed under their own item rather than silently smuggled into SEC-147.
KNOWN_GAPS: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/routines/options/{source}"): (
        "reads controller configs off any ?server= with no access check"
    ),
}

# The body-param sites: "<module>.<function>" → the attribute holding the name.
# A path/query dependency cannot see a request body, so these call the shared
# helper instead. Listed explicitly so deleting one is a test failure.
BODY_SITES = [
    ("condor.web.routes.routines", "run_routine_v2"),
    ("condor.web.routes.routines", "start_continuous_v2"),
    ("condor.web.routes.routines", "schedule_routine_v2"),
    ("condor.web.routes.code", "run_code"),
    ("condor.web.routes.sessions", "_respawn"),
    ("condor.web.routes.agents", "update_agent_config"),
    ("condor.web.routes.agents", "consult_agent"),
    ("condor.web.routes.agents", "delegate_agent"),
    ("condor.web.routes.agents", "_start"),
]


@pytest.fixture(scope="module")
def app():
    return create_app()


def _flatten(dependant, acc=None):
    acc = set() if acc is None else acc
    for sub in dependant.dependencies:
        acc.add(sub.call)
        _flatten(sub, acc)
    return acc


def _server_scoped_routes(app):
    """Yield ``(route, methods, why)`` for every route that names a server."""
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = sorted(m for m in route.methods if m != "HEAD")
        if re.search(r"/servers/\{name\}(/|$)", route.path):
            yield route, methods, "path {name}"
        elif "{server_name}" in route.path:
            yield route, methods, "path {server_name}"
        elif any(p.name == "server" for p in route.dependant.query_params):
            yield route, methods, "query ?server="


# ── The structural sweep ──


def test_every_server_scoped_route_carries_the_guard(app):
    """A new server-scoped endpoint cannot forget the access check."""
    missing = []
    for route, methods, why in _server_scoped_routes(app):
        if _flatten(route.dependant) & GUARDS:
            continue
        for method in methods:
            key = (method, route.path)
            if key in ALLOWLIST or key in KNOWN_GAPS:
                continue
            missing.append(f"{method} {route.path} ({why}) -> {route.name}")
    assert not missing, (
        "server-scoped routes with no require_server_access* dependency:\n  "
        + "\n  ".join(missing)
        + "\nAdd the dependency, or justify the route in ALLOWLIST."
    )


def test_each_shape_uses_the_dependency_that_matches_it(app):
    """A guard whose parameter does not match the route fails closed, loudly.

    ``require_server_access`` declares ``name``; on a route with no ``{name}``
    path parameter FastAPI would demote it to a required *query* parameter and
    every request would 422. That is safe but broken, so pin that each guard
    reads the parameter it was meant to read.
    """
    wrong = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for sub in route.dependant.dependencies:
            if sub.call not in GUARDS:
                continue
            path_params = {p.name for p in sub.path_params}
            query_params = {p.name for p in sub.query_params}
            if sub.call is require_server_access and "name" not in path_params:
                wrong.append(f"{route.path}: require_server_access has no {{name}}")
            if (
                sub.call is require_server_access_by_server_name
                and "server_name" not in path_params
            ):
                wrong.append(f"{route.path}: no {{server_name}} path param")
            if sub.call is require_server_access_query and "server" not in query_params:
                wrong.append(f"{route.path}: no ?server= query param")
    assert not wrong, wrong


def test_the_sweep_actually_finds_the_migrated_routes(app):
    """Guard against the sweep silently matching nothing and passing."""
    guarded = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and _flatten(r.dependant) & GUARDS
    ]
    assert len(guarded) >= 70, len(guarded)


def test_allowlisted_routes_still_check_access_inline(app):
    """The 404-shaped exceptions keep their own check — they are not unguarded."""
    by_key = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            by_key[(method, route.path)] = route
    for key in ALLOWLIST:
        route = by_key.get(key)
        assert route is not None, f"allowlisted route no longer exists: {key}"
        source = inspect.getsource(route.endpoint)
        assert "has_server_access" in source, key
        assert "Server not found" in source, key


def test_body_param_sites_call_the_shared_helper():
    """The body shape has no signature to reflect on, so pin it by source."""
    import importlib

    for module_name, func_name in BODY_SITES:
        module = importlib.import_module(module_name)
        source = inspect.getsource(getattr(module, func_name))
        assert "check_server_access(" in source, f"{module_name}.{func_name}"


# ── The guards actually refuse ──


class _Cm:
    """Grants ``OWNED`` to ``USER`` and nothing else."""

    def has_server_access(self, user_id, server_name, *a, **kw):
        return user_id == USER.id and server_name == OWNED


@pytest.fixture
def deny(monkeypatch):
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: _Cm())


def test_path_guard_refuses_a_foreign_server(deny):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_server_access(FOREIGN, user=USER))
    assert exc.value.status_code == 403
    assert exc.value.detail == "No access"


def test_path_guard_returns_the_user_on_an_owned_server(deny):
    assert asyncio.run(require_server_access(OWNED, user=USER)) is USER


def test_server_name_alias_guard_refuses_a_foreign_server(deny):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_server_access_by_server_name(FOREIGN, user=USER))
    assert exc.value.status_code == 403
    assert asyncio.run(require_server_access_by_server_name(OWNED, user=USER)) is USER


def test_query_guard_refuses_a_foreign_server(deny):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_server_access_query(FOREIGN, user=USER))
    assert exc.value.status_code == 403
    assert asyncio.run(require_server_access_query(OWNED, user=USER)) is USER


def test_the_shared_helper_refuses_a_foreign_server(deny):
    with pytest.raises(HTTPException) as exc:
        check_server_access(USER.id, FOREIGN)
    assert exc.value.status_code == 403
    assert exc.value.detail == "No access"
    assert check_server_access(USER.id, OWNED) is None


# ── End to end, through the real app, one route per shape ──


@pytest.fixture
def client(app, deny):
    app.dependency_overrides[get_current_user] = lambda: USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "method,url",
    [
        # {name} path param
        ("GET", f"/api/v1/servers/{FOREIGN}/bots"),
        ("GET", f"/api/v1/servers/{FOREIGN}/market/connectors"),
        ("GET", f"/api/v1/servers/{FOREIGN}/portfolio"),
        # aliased ?server= query param (settings.py)
        ("GET", f"/api/v1/settings/gateway/status?server={FOREIGN}"),
        ("GET", f"/api/v1/settings/credentials?server={FOREIGN}"),
        # {server_name} path param
        ("POST", f"/api/v1/routines/servers/{FOREIGN}/some_routine/run"),
    ],
)
def test_a_foreign_server_is_refused_over_http(client, method, url):
    resp = client.request(method, url, json={"config": {}})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "No access"


def test_a_foreign_server_in_the_body_is_refused_over_http(client):
    resp = client.post(
        "/api/v1/routines/run",
        json={"routine_name": "agent/some_routine", "server_name": FOREIGN},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "No access"
