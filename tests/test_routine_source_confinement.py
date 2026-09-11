"""CORR-585: the source viewer's allowlist must cover every root discovery reads.

``GET /routines/{name}/source`` confines the file it reads to an allowlist, and
that allowlist used to hold a single cwd-relative ``routines``. Discovery,
though, deliberately also returns the shared library and every agent's own
routines under the prefixed name ``slug/name`` — none of which live under the
root library, so "View source" 403'd for every one of them.

The allowlist stays a confinement check: it now names the roots
``RoutineStore._discover_all`` actually imports from, and nothing wider.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.routines as routines_module
import routines.base
from condor.memory.paths import (
    local_agents_root,
    shared_routines_roots,
    stock_agents_root,
)
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")

SOURCE = "async def run(config, context):\n    return 'ok'\n"


class _Routine:
    """The one attribute the source route reads off a discovered routine."""

    def __init__(self, run_fn):
        self.run_fn = run_fn


def _routine_at(path: Path):
    """Write a routine file and return it as a discovered routine.

    Compiled with the file as its name, so ``inspect.getfile(run_fn)`` reports
    that path exactly as it would for a really-imported routine module.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SOURCE)
    namespace: dict = {}
    exec(compile(SOURCE, str(path), "exec"), namespace)
    return _Routine(namespace["run"])


class FakeStore:
    def __init__(self, routines):
        self._routines = routines

    def _discover_all(self):
        return self._routines


@pytest.fixture
def client_for(monkeypatch):
    """Serve the routes with a store that returns exactly the given routines."""

    def _build(routines):
        monkeypatch.setattr(
            routines_module, "get_routine_store", lambda: FakeStore(routines)
        )
        app = FastAPI()
        app.include_router(routines_module.router)
        app.dependency_overrides[get_current_user] = lambda: USER
        return TestClient(app)

    return _build


# ── Admitted: the roots discovery reads ──


def test_a_shipped_agents_routine_serves_its_source(client_for):
    path = stock_agents_root() / "brigado" / "routines" / "bot_report.py"
    client = client_for({"brigado/bot_report": _routine_at(path)})

    resp = client.get("/routines/brigado/bot_report/source")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"filename": "bot_report.py", "source": SOURCE}


def test_a_locally_authored_agent_routine_serves_its_source(client_for):
    path = local_agents_root() / "mine" / "routines" / "scratch.py"
    client = client_for({"mine/scratch": _routine_at(path)})

    assert client.get("/routines/mine/scratch/source").status_code == 200


def test_a_shared_library_routine_serves_its_source(client_for):
    # ``_shared`` is skipped by iter_agent_slugs, so it has to be allowlisted
    # by name rather than inherited from the agents root.
    for root in shared_routines_roots():
        client = client_for({"shared_one": _routine_at(root / "shared_one.py")})
        assert client.get("/routines/shared_one/source").status_code == 200


def test_the_general_library_still_serves_its_source(client_for, tmp_path, monkeypatch):
    library = Path(routines.base.__file__).resolve().parent
    existing = next(
        p for p in sorted(library.glob("*.py")) if p.stem not in ("__init__", "base")
    )
    client = client_for({existing.stem: _Routine(_fn_named(existing))})
    # A cwd outside the repo used to 403 even this: the old root was relative.
    monkeypatch.chdir(tmp_path)

    resp = client.get(f"/routines/{existing.stem}/source")

    assert resp.status_code == 200, resp.text
    assert resp.json()["filename"] == existing.name


def _fn_named(path: Path):
    """A callable that ``inspect.getfile`` reports as living at ``path``."""
    namespace: dict = {}
    exec(compile(SOURCE, str(path), "exec"), namespace)
    return namespace["run"]


# ── Refused: everything else ──


def test_a_file_outside_every_root_is_refused(client_for, tmp_path):
    path = tmp_path / "elsewhere" / "secrets.py"
    client = client_for({"sneaky": _routine_at(path)})

    resp = client.get("/routines/sneaky/source")

    assert resp.status_code == 403
    assert "secret" not in resp.text.lower()


def test_traversal_out_of_a_root_is_refused(client_for):
    # Resolved before the check, so ``..`` never leaves the path it walks out of.
    path = stock_agents_root() / "brigado" / "routines" / ".." / ".." / ".." / "keys.py"
    client = client_for({"brigado/escape": _routine_at(path)})

    assert client.get("/routines/brigado/escape/source").status_code == 403


def test_a_symlink_pointing_out_of_a_root_is_refused(client_for, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "keys.py").write_text(SOURCE)
    routines_dir = stock_agents_root() / "brigado" / "routines"
    routines_dir.mkdir(parents=True, exist_ok=True)
    link = routines_dir / "linked.py"
    link.symlink_to(outside / "keys.py")
    client = client_for({"brigado/linked": _Routine(_fn_named(link))})

    assert client.get("/routines/brigado/linked/source").status_code == 403


def test_an_agent_home_next_to_its_routines_is_not_readable(client_for):
    # The allowlist admits ``<agent home>/routines``, not the home itself: a
    # journal or a memory store beside it stays out of reach.
    home = local_agents_root() / "mine"
    (home / "routines").mkdir(parents=True, exist_ok=True)
    client = client_for({"mine/journal": _routine_at(home / "store" / "journal.py")})

    assert client.get("/routines/mine/journal/source").status_code == 403


def test_a_sibling_sharing_a_root_s_prefix_is_refused(client_for):
    # ``startswith`` admitted ``…/routines_backup``; is_relative_to does not.
    routines_dir = stock_agents_root() / "brigado" / "routines"
    routines_dir.mkdir(parents=True, exist_ok=True)
    sibling = routines_dir.with_name("routines_backup") / "leak.py"
    client = client_for({"brigado/leak": _routine_at(sibling)})

    assert client.get("/routines/brigado/leak/source").status_code == 403
