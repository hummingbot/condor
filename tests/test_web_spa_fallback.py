"""Tests for SEC-044: SPA fallback must not serve files outside frontend/dist.

The catch-all route decodes percent-encoded traversal (``%2e%2e``, ``..%2f``)
before it reaches the handler, and a bare ``FileResponse`` applies no
containment guard. The route must confine resolution to ``frontend/dist`` and
fall back to ``index.html`` for anything that escapes it.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "frontend" / "dist"
INDEX_HTML = DIST / "index.html"

pytestmark = pytest.mark.skipif(
    not INDEX_HTML.is_file(),
    reason="frontend/dist not built; SPA fallback route is not mounted",
)


@pytest.fixture(scope="module")
def client():
    from starlette.testclient import TestClient

    from condor.web.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path",
    [
        # pyproject.toml sits two levels above dist (repo root), like
        # config.yml / .env — the files the traversal originally exposed.
        "/%2e%2e/%2e%2e/pyproject.toml",
        "/..%2f..%2fpyproject.toml",
        "/%2e%2e/%2e%2e/%2e%2e/etc/hosts",
    ],
)
def test_encoded_traversal_does_not_escape_dist(client, path):
    """Encoded `..` sequences must serve index.html, never host files."""
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.content == INDEX_HTML.read_bytes()
    assert b"requires-python" not in resp.content


def test_in_dist_file_still_served(client):
    """Legitimate files inside dist keep resolving."""
    resp = client.get("/index.html")
    assert resp.status_code == 200
    assert resp.content == INDEX_HTML.read_bytes()


def test_deep_link_falls_back_to_index(client):
    """SPA deep-link routes (no matching file) still get index.html."""
    resp = client.get("/portfolio/some/deep/route")
    assert resp.status_code == 200
    assert resp.content == INDEX_HTML.read_bytes()
