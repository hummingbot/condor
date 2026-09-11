"""PERF-267: the shared plotly bundle is served as an asset, not per report.

A chart report used to inline 4.85 MB of plotly.js; it now references one
persisted copy under ``reports/_assets/``. That reference is only safe if the
route serving it is a fixed allowlist rather than a path reader (the shape
SEC-044 and SEC-112 removed), if a miss is a real 404 rather than the SPA's
``index.html`` (which a browser refuses to execute as a script, so the failure
would show up as a silently blank chart), and if every path that leaves the
origin inlines the bundle back in.
"""

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import condor.reports as reports
import condor.web.routes.reports as reports_routes
from condor.reports import rendering
from condor.web.app import create_app
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")

BUNDLE = "/**\n* plotly.js v0.0.0\n*/\nwindow.Plotly={};"
ASSET_NAME = "plotly-0.0.0.js"
SECRET_OUTSIDE = "SECRET-FILE-BEYOND-ASSETS-DIR"


class _NoAdmins:
    def is_admin(self, user_id):
        return False


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    """A reports directory holding one lean report plus its shared bundle."""
    directory = tmp_path / "reports"
    assets = directory / rendering.ASSETS_DIRNAME
    assets.mkdir(parents=True)
    (assets / ASSET_NAME).write_text(BUNDLE, encoding="utf-8")
    (assets / "notes.txt").write_text(SECRET_OUTSIDE, encoding="utf-8")
    (directory / "config.yml").write_text(SECRET_OUTSIDE, encoding="utf-8")

    body = (
        "<html><head>"
        f'<script src="{rendering.ASSET_URL_PREFIX}{ASSET_NAME}"></script>'
        "</head><body>chart</body></html>"
    )
    (directory / "sample.html").write_text(body, encoding="utf-8")

    index = directory / "reports_index.json"
    index.write_text(
        json.dumps(
            [
                {
                    "id": "abc123",
                    "title": "Sample",
                    "filename": "sample.html",
                    "created_at": "2026-08-28T00:00:00+00:00",
                    "source_type": "routine",
                    "source_name": "sample",
                    "tags": [],
                    "user_id": 111,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(reports, "CHARTS_DIR", directory)
    monkeypatch.setattr(reports, "INDEX_FILE", index)
    monkeypatch.setattr(reports_routes, "get_config_manager", lambda: _NoAdmins())
    return directory


def test_bundle_is_served_without_an_authorization_header(reports_dir):
    """A report in an opaque-origin frame carries no token; the asset needs none."""
    with TestClient(create_app()) as client:
        response = client.get(f"/api/v1/reports/assets/{ASSET_NAME}")
    assert response.status_code == 200
    assert response.text == BUNDLE
    assert "javascript" in response.headers["content-type"]
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize(
    "name",
    [
        "../../config.yml",
        "..%2f..%2fconfig.yml",
        "%2e%2e/%2e%2e/config.yml",
        "notes.txt",  # a real file in _assets, but not an allowlisted shape
        "plotly-0.0.0.js.map",
        "plotly.js",
        "sample.html",
    ],
)
def test_only_allowlisted_bundle_names_resolve(reports_dir, name):
    """Anything that is not one of our own bundles is a 404, never a file."""
    with TestClient(create_app()) as client:
        response = client.get(f"/api/v1/reports/assets/{name}")
    assert response.status_code == 404, name
    assert SECRET_OUTSIDE not in response.text, name


def test_unknown_version_is_a_json_404_not_the_spa_shell(reports_dir):
    """A missing bundle must fail loudly, not arrive as ``index.html``.

    Browsers refuse to execute ``text/html`` as a classic script, so falling
    through to the SPA catch-all would surface as an empty chart with nothing
    but a console warning.
    """
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/reports/assets/plotly-9.9.9.js")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "<!doctype html" not in response.text.lower()


def test_asset_route_does_not_shadow_the_report_detail_route(reports_dir):
    """``/assets/{filename}`` must not swallow ``/{report_id}`` traffic."""
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: USER
    with TestClient(app) as client:
        assert client.get("/api/v1/reports/abc123").status_code == 200


def test_viewer_gets_the_lean_body_and_download_gets_the_hydrated_one(reports_dir):
    """One endpoint, two shapes: the frame references, the download inlines."""
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: USER
    with TestClient(app) as client:
        lean = client.get("/api/v1/reports/abc123/html")
        hydrated = client.get("/api/v1/reports/abc123/html?hydrate=1")

    assert lean.status_code == 200
    assert f'src="{rendering.ASSET_URL_PREFIX}{ASSET_NAME}"' in lean.text
    assert BUNDLE not in lean.text

    assert hydrated.status_code == 200
    assert BUNDLE in hydrated.text
    assert "<script src=" not in hydrated.text


def test_asset_route_reads_the_configured_reports_dir_only(reports_dir, tmp_path):
    """The resolver is bound to ``reports/_assets``, wherever that is."""
    assert reports.resolve_report_asset(ASSET_NAME) == (
        reports_dir / rendering.ASSETS_DIRNAME / ASSET_NAME
    )
    assert reports.resolve_report_asset("plotly-9.9.9.js") is None
    assert reports.resolve_report_asset("../sample.html") is None
    assert isinstance(reports.resolve_report_asset(ASSET_NAME), Path)
