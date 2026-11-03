"""Tests for the two things ``/meta`` says about this process.

``/env`` is the build identity the "Report an issue" dialog stamps on every
report: only useful if the fields are real, only safe if it answers nobody who
is not logged in.

``/relaunch`` is the other half — whether this process is older than the code on
disk. It feeds a banner on every page for every seat, admin or not, because the
mismatch it describes (new bundle, old API) is what a non-admin actually walks
into. So unlike the update routes it must NOT be admin-gated, and it must still
refuse an anonymous caller.
"""

import pytest
from starlette.testclient import TestClient

from condor.updates import run as run_mod
from condor.web.app import create_app
from condor.web.auth import get_current_user
from condor.web.models import WebUser


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def test_env_requires_authentication(client):
    """No token, no answer — an install's commit is not for anonymous callers."""
    res = client.get("/api/v1/meta/env")
    assert res.status_code in (401, 403)


def test_env_returns_build_identity(app):
    """The fields the diagnostics block renders are all present and typed."""
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=1, username="u", first_name="f", role="user"
    )
    try:
        with TestClient(app) as client:
            res = client.get("/api/v1/meta/env")
    finally:
        app.dependency_overrides.clear()

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"version", "branch", "python", "os", "arch", "in_docker"}
    assert isinstance(body["in_docker"], bool)
    # An install deployed from a tarball has no git metadata; the context layer
    # reports "unknown" rather than failing, and either is a valid answer.
    assert body["version"]
    assert body["python"].startswith("3.")


def test_env_leaks_no_configuration(app):
    """Only version and platform. Never keys, hosts, users, or server names."""
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=1, username="u", first_name="f", role="user"
    )
    try:
        with TestClient(app) as client:
            body = client.get("/api/v1/meta/env").json()
    finally:
        app.dependency_overrides.clear()

    for banned in ("llm_providers", "user_count", "server_count", "has_gateway"):
        assert banned not in body


# ---------------------------------------------------------------------------
# /relaunch: is this process running the code on disk?
# ---------------------------------------------------------------------------


@pytest.fixture()
def _no_relaunch():
    """The engine's flag is process-wide module state; do not leak it."""
    run_mod._relaunch = None
    yield
    run_mod._relaunch = None


@pytest.fixture()
def seat(app):
    def _login(role: str = "user"):
        app.dependency_overrides[get_current_user] = lambda: WebUser(
            id=1, username="u", first_name="f", role=role
        )
        return TestClient(app)

    yield _login
    app.dependency_overrides.clear()


def test_relaunch_requires_authentication(client, _no_relaunch):
    assert client.get("/api/v1/meta/relaunch").status_code in (401, 403)


def test_nothing_owed_is_the_ordinary_answer(seat, _no_relaunch):
    """An install that has not updated in place says so in one field."""
    res = seat().get("/api/v1/meta/relaunch")
    assert res.status_code == 200
    assert res.json() == {"required": False}


def test_a_pending_relaunch_names_both_commits(seat, _no_relaunch):
    run_mod._relaunch = {
        "run_id": "u-1",
        "from_commit": "aaaaaaa1111111111111111111111111111111111",
        "target_commit": "bbbbbbb2222222222222222222222222222222222",
        "branch": "main",
        "at": 1756300000.0,
    }
    body = seat().get("/api/v1/meta/relaunch").json()

    assert body["required"] is True
    assert body["branch"] == "main"
    # Short shas: the banner renders them inline, and nobody reads forty chars.
    assert body["from_commit"] == "aaaaaaa"
    assert body["target_commit"] == "bbbbbbb"


def test_a_non_admin_seat_is_told_too(seat, _no_relaunch):
    """The stale-backend mismatch is not an admin-only experience."""
    run_mod._relaunch = {
        "run_id": "u-1",
        "from_commit": "aaaaaaa",
        "target_commit": "bbbbbbb",
        "branch": "main",
        "at": 1.0,
    }
    for role in ("user", "pending", "admin"):
        assert seat(role).get("/api/v1/meta/relaunch").json()["required"] is True
