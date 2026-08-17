"""Tests for the build-identity endpoint behind the dashboard's issue reporter.

The "Report an issue" dialog stamps every report with the commit it came from.
That is only useful if the endpoint answers with real fields, and only safe if
it answers nobody who is not logged in.
"""

import pytest
from starlette.testclient import TestClient

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
