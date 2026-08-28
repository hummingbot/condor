"""Tests for the dashboard's update surface (FEAT-071).

Two things are worth holding still here. First the gate: these routes move the
checkout and can reap running executors, so every one of them must answer 403 to
a seat that is not an admin, whatever the client believes. Second the
shape: the panel is a view over ``condor.updates`` and must stay one — the
routes serialize the engine's dataclasses and start work in the background,
and the moment one of them grows its own idea of what an update is, Telegram
and the browser start disagreeing.
"""

import asyncio

import pytest
from starlette.testclient import TestClient

from condor import updates
from condor.updates import components as components_mod
from condor.updates import run as run_mod
from condor.web.app import create_app
from condor.web.auth import get_current_user
from condor.web.models import WebUser

ROUTES = [
    ("get", "/api/v1/updates", None),
    ("post", "/api/v1/updates/check", None),
    ("post", "/api/v1/updates/preflight", {"components": ["condor"]}),
    ("post", "/api/v1/updates/resolve", {"component": "condor", "action": "stash"}),
    ("post", "/api/v1/updates/start", {"components": ["condor"]}),
    ("get", "/api/v1/updates/run", None),
]


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def as_user(app):
    """Log a seat in without touching the JWT layer; role comes from ConfigManager."""

    def _login(user_id: int, role: str = "user"):
        app.dependency_overrides[get_current_user] = lambda: WebUser(
            id=user_id, username="u", first_name="f", role=role
        )
        return TestClient(app)

    yield _login
    app.dependency_overrides.clear()


@pytest.fixture()
def admin(monkeypatch):
    """Make exactly one user id an admin, as the routes ask ConfigManager."""

    class _CM:
        def __init__(self, admin_id):
            self.admin_id = admin_id

        def is_admin(self, user_id):
            return user_id == self.admin_id

    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: _CM(7), raising=True
    )
    return 7


@pytest.fixture(autouse=True)
def clean_run_state():
    """No run leaks between tests — the engine keys on process-wide state."""
    run_mod._current = None
    run_mod._task = None
    yield
    run_mod._current = None
    run_mod._task = None


# ── The gate ──


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_every_route_refuses_a_non_admin(as_user, admin, method, path, body):
    """A logged-in non-admin gets 403 from all six, not just the mutating ones."""
    client = as_user(999)
    res = getattr(client, method)(path, **({"json": body} if body else {}))
    assert res.status_code == 403, f"{method} {path} let a non-admin through"


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_every_route_refuses_an_anonymous_caller(app, method, path, body):
    """No token at all is refused before the admin check is even reached."""
    with TestClient(app) as client:
        res = getattr(client, method)(path, **({"json": body} if body else {}))
    assert res.status_code in (401, 403)


# ── Reads ──


def test_status_serializes_the_engines_dataclasses(as_user, admin, monkeypatch):
    """`GET /updates` is `check()` plus `to_wire()`, and nothing else."""
    status = components_mod.ComponentStatus(
        key="hummingbot-api",
        name="Hummingbot API",
        facets={
            "image": components_mod.Facet(
                kind="image",
                current="sha256:4ae0104d",
                available="sha256:62d70399",
                up_to_date=False,
            ),
            "repo": components_mod.Facet(
                kind="repo", current="main @ 73e5400", behind=57, up_to_date=False
            ),
        },
        mode="image",
        up_to_date=False,
    )

    async def fake_check(*, force=False):
        return [status]

    monkeypatch.setattr(updates, "check", fake_check)

    body = as_user(admin).get("/api/v1/updates").json()

    assert body == {"components": [status.to_wire()]}
    # Both facets survive the wire, including the mode that decides which one
    # the panel treats as authoritative.
    component = body["components"][0]
    assert set(component["facets"]) == {"image", "repo"}
    assert component["mode"] == "image"
    assert component["facets"]["image"]["available"] == "sha256:62d70399"
    assert component["facets"]["repo"]["behind"] == 57


def test_check_forces_past_the_cache(as_user, admin, monkeypatch):
    """Refresh means refresh: the button exists to defeat the 60s cache."""
    seen = {}

    async def fake_check(*, force=False):
        seen["force"] = force
        return []

    monkeypatch.setattr(updates, "check", fake_check)

    as_user(admin).post("/api/v1/updates/check")
    assert seen["force"] is True


def test_run_with_no_journal_returns_null(as_user, admin, monkeypatch):
    """A fresh install has never updated; that is `null`, not a 404."""
    monkeypatch.setattr(updates, "read_journal", lambda: None)

    res = as_user(admin).get("/api/v1/updates/run")

    assert res.status_code == 200
    assert res.json() == {"run": None}


def test_run_falls_back_to_the_journal_across_a_restart(as_user, admin, monkeypatch):
    """After a relaunch, in-process state is empty and the file is the truth."""
    journaled = run_mod.Run(
        id="u-1",
        started=1.0,
        actor={"user_id": 7, "chat_id": 7},
        components=["condor"],
        steps=[run_mod.Step("condor.frontend", "Rebuilding the dashboard", run_mod.OK)],
        state=run_mod.SUCCEEDED,
    )
    monkeypatch.setattr(updates, "read_journal", lambda: journaled)

    body = as_user(admin).get("/api/v1/updates/run").json()

    assert body["run"]["id"] == "u-1"
    assert body["run"]["state"] == "succeeded"


# ── Preflight and resolution ──


def test_preflight_passes_blocks_and_warnings_through(as_user, admin, monkeypatch):
    """The panel's blockers, file lists and plan all come from the engine verbatim."""
    preflight = components_mod.Preflight(
        components=["condor"],
        blocks=[
            components_mod.Block(
                component="condor",
                code="dirty_tree",
                message="Local changes would be overwritten",
                paths=["condor/paths.py", "main.py"],
                resolutions=["stash", "discard"],
            )
        ],
        warnings=[
            components_mod.Warning(
                component="hummingbot-api",
                code="executors_reaped",
                message="3 running executors will be reaped",
            )
        ],
        steps=["Fast-forward the Condor checkout", "Sync dependencies"],
    )

    async def fake_preflight(keys):
        assert keys == ["condor"]
        return preflight

    monkeypatch.setattr(updates, "preflight", fake_preflight)

    body = (
        as_user(admin)
        .post("/api/v1/updates/preflight", json={"components": ["condor"]})
        .json()
    )

    assert body["ok"] is False
    assert body["blocks"][0]["paths"] == ["condor/paths.py", "main.py"]
    assert body["blocks"][0]["resolutions"] == ["stash", "discard"]
    assert body["warnings"][0]["code"] == "executors_reaped"
    assert body["steps"] == ["Fast-forward the Condor checkout", "Sync dependencies"]


def test_resolve_sends_no_paths_to_the_engine(as_user, admin, monkeypatch):
    """The caller names a component and an action — never which files to destroy.

    The engine recomputes what conflicts at press time. A route that forwarded a
    path list would be a delete-arbitrary-files primitive wearing an update
    control's clothes, and would act on a screen that may be minutes stale.
    """
    calls = []

    async def fake_resolve(component_key, action):
        calls.append((component_key, action))
        return True, "Stashed 2 files."

    monkeypatch.setattr(updates, "resolve", fake_resolve)

    res = as_user(admin).post(
        "/api/v1/updates/resolve",
        # A client that invents a `paths` field is ignored, not obeyed.
        json={"component": "condor", "action": "discard", "paths": ["/etc/passwd"]},
    )

    assert res.status_code == 200
    assert res.json() == {"ok": True, "message": "Stashed 2 files."}
    assert calls == [("condor", "discard")]


def test_resolve_reports_an_unknown_action_as_a_failure(as_user, admin, monkeypatch):
    """The engine judges the action; the route reports its answer without dressing it up."""

    async def fake_resolve(component_key, action):
        return False, f"Unknown resolution: {action}"

    monkeypatch.setattr(updates, "resolve", fake_resolve)

    body = (
        as_user(admin)
        .post(
            "/api/v1/updates/resolve", json={"component": "condor", "action": "rm -rf"}
        )
        .json()
    )

    assert body["ok"] is False
    assert "Unknown resolution" in body["message"]


# ── Start ──


def test_start_returns_202_without_awaiting_the_run(as_user, admin, monkeypatch):
    """The request is a trigger, not a session — it must not wait for a 20-minute build."""
    started = asyncio.Event()

    async def fake_start(keys, *, actor_user_id=None, actor_chat_id=None):
        started.set()
        return run_mod.Run(
            id="u-42",
            started=1.0,
            actor={"user_id": actor_user_id, "chat_id": actor_chat_id},
            components=list(keys),
            steps=[],
            state=run_mod.RUNNING,
        )

    monkeypatch.setattr(updates, "start", fake_start)

    res = as_user(admin).post("/api/v1/updates/start", json={"components": ["condor"]})

    assert res.status_code == 202
    assert res.json() == {"run_id": "u-42", "state": "running"}
    assert started.is_set()


def test_start_records_the_admin_as_the_actor(as_user, admin, monkeypatch):
    """Who pressed it is journaled, so the run is attributable after a relaunch."""
    seen = {}

    async def fake_start(keys, *, actor_user_id=None, actor_chat_id=None):
        seen["actor"] = actor_user_id
        return run_mod.Run(
            id="u-1", started=1.0, actor={}, components=list(keys), steps=[]
        )

    monkeypatch.setattr(updates, "start", fake_start)

    as_user(admin).post("/api/v1/updates/start", json={"components": ["condor"]})

    assert seen["actor"] == admin


def test_start_during_a_live_run_returns_that_run(as_user, admin):
    """A second surface joins the run in flight; 409 would break Telegram→browser."""
    live = run_mod.Run(
        id="u-live",
        started=1.0,
        actor={"user_id": 1, "chat_id": 1},
        components=["condor"],
        steps=[],
        state=run_mod.RUNNING,
    )
    run_mod._current = live

    res = as_user(admin).post("/api/v1/updates/start", json={"components": ["condor"]})

    assert res.status_code == 202
    assert res.json()["run_id"] == "u-live"


def test_a_telegram_run_is_visible_to_the_panel(as_user, admin):
    """One run, two surfaces: `GET /updates/run` sees what `/update` started."""
    run_mod._current = run_mod.Run(
        id="u-from-telegram",
        started=1.0,
        actor={"user_id": 7, "chat_id": 7},
        components=["condor"],
        steps=[run_mod.Step("condor.deps", "Syncing dependencies", run_mod.RUNNING)],
        state=run_mod.RUNNING,
    )

    body = as_user(admin).get("/api/v1/updates/run").json()

    assert body["run"]["id"] == "u-from-telegram"
    assert body["run"]["steps"][0]["state"] == "running"


# ── The module's own discipline ──


def test_routes_module_shells_out_to_nothing():
    """A view imports the engine and nothing that could run an update itself.

    Checked over the AST rather than the text, so the module is free to *say*
    "no docker call lives here" in its own docstring while the test verifies it.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("condor/web/routes/updates.py").read_text())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    for banned in ("subprocess", "asyncio", "docker", "shutil", "utils"):
        assert banned not in imported, f"a view has no business importing {banned!r}"

    # The one thing it does import for update knowledge is the engine.
    assert "condor" in imported
