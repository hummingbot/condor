"""FEAT-082: the web manifest and its icons are served, and served correctly.

The feature adds no route: ``frontend/public`` is copied verbatim into
``frontend/dist`` by Vite, so the manifest and the icons come out of the file
arm of the SPA catch-all in ``condor/web/app.py``. That is the whole design, and
it is also the whole risk — a file that silently falls through to ``index.html``
would leave the browser parsing the SPA shell as JSON and reporting nothing more
than "no manifest", which is why each of these is asserted on bytes rather than
on a status code alone.

The icons themselves are generated once from ``frontend/public/condor.png`` and
committed; to regenerate them see the commit that introduced this file.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "frontend" / "dist"
INDEX_HTML = DIST / "index.html"

pytestmark = pytest.mark.skipif(
    not INDEX_HTML.is_file(),
    reason="frontend/dist not built; the static file route is not mounted",
)

ICONS = ("/icon-192.png", "/icon-512.png", "/icon-maskable-512.png")


@pytest.fixture(scope="module")
def client():
    from starlette.testclient import TestClient

    from condor.web.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def manifest(client) -> dict:
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    return json.loads(resp.content)


def test_manifest_is_served_as_a_manifest(client):
    """Not the SPA shell, and with the media type browsers expect.

    ``mimetypes`` maps ``.webmanifest`` itself, so this is really a guard that
    nobody has to add a route or an ``add_type`` call for it later.
    """
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.content != INDEX_HTML.read_bytes()
    assert resp.headers["content-type"].startswith("application/manifest+json")


def test_manifest_declares_an_installable_app(manifest):
    """The keys a browser reads before it will offer to install."""
    assert manifest["name"] == "Condor"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    # Pinned so a later `start_url` change does not read as a different app and
    # orphan an installed copy.
    assert manifest["id"] == "/"


def test_manifest_icons_all_resolve(client, manifest):
    """Every declared icon is a real PNG, at the size it claims.

    A 404 here is invisible in the browser: Chrome just stops offering to
    install, with the reason buried in the Application panel.
    """
    declared = {icon["src"] for icon in manifest["icons"]}
    assert declared == set(ICONS)

    for icon in manifest["icons"]:
        resp = client.get(icon["src"])
        assert resp.status_code == 200, icon["src"]
        assert resp.headers["content-type"] == "image/png"
        assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")

    maskable = [i for i in manifest["icons"] if i.get("purpose") == "maskable"]
    assert len(maskable) == 1, "one maskable icon, or the dock icon gets cropped"


def test_manifest_shortcuts_are_real_routes(manifest):
    """The jump list only lands where it says it lands.

    Each URL is a route in `App.tsx` that renders a page, and none of them is
    one of its `Navigate` redirects — a shortcut into a redirect would open the
    app on a screen the user did not ask for.
    """
    urls = [s["url"] for s in manifest["shortcuts"]]
    assert urls == ["/portfolio", "/bots", "/trade", "/routines", "/settings"]


def test_manifest_focuses_the_existing_window(manifest):
    """`navigate-existing` is load-bearing, not polish.

    `ChatProvider` opens the chat WebSocket on every route, so a second window
    is a second live subscription for the same user.
    """
    assert manifest["launch_handler"]["client_mode"] == "navigate-existing"


def test_manifest_follows_the_shell_caching_rule(client):
    """An unhashed file beside the shell revalidates like the shell.

    Left to the browser's heuristic, an installed app could keep an old
    manifest — old shortcuts, an old start URL — with no build to blame.
    """
    resp = client.get("/manifest.webmanifest")
    assert "no-cache" in resp.headers["cache-control"]


@pytest.mark.parametrize("path", ICONS)
def test_icons_are_served_directly(client, path):
    """Each icon is reachable at its own path, not just via the manifest."""
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_no_service_worker_is_shipped(client):
    """FEAT-082 registers none; `app.py`'s cache contract stays the only one.

    FEAT-083 introduces one for Web Push — with no `fetch` handler — and this
    assertion is expected to be revisited there, deliberately.
    """
    assert not (DIST / "sw.js").exists()
    assert b"serviceWorker" not in INDEX_HTML.read_bytes()
