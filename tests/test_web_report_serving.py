"""SEC-112: report bodies are served only to authenticated callers.

Report HTML carries portfolio value, PnL, positions and strategy internals. It
used to be served by an unauthenticated ``/reports/{filename:path}`` mount, so
anyone who could guess a filename could read it. Bodies now go through
``GET /api/v1/reports/{report_id}/html`` behind ``get_current_user``.
"""

import json

import pytest
from starlette.testclient import TestClient

import condor.reports as reports
from condor.web.app import create_app
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")

BODY = "<h1>Report</h1>"
# Distinctive markers so a leak is unmistakable and cannot be confused with a
# 404 detail message that happens to echo the requested path back.
NON_REPORT_BODY = "SECRET-NON-REPORT-FILE"
OUTSIDE_BODY = "SECRET-FILE-BEYOND-REPORTS-DIR"


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    """A reports directory holding one indexed report, plus decoy files."""
    directory = tmp_path / "reports"
    directory.mkdir()
    (directory / "sample.html").write_text(BODY, encoding="utf-8")
    (directory / "private.txt").write_text(NON_REPORT_BODY, encoding="utf-8")
    (tmp_path / "outside.html").write_text(OUTSIDE_BODY, encoding="utf-8")

    index = directory / "reports_index.json"
    index.write_text(
        json.dumps(
            [
                {
                    "id": "abc123",
                    "title": "Sample",
                    "filename": "sample.html",
                    "created_at": "2026-08-04T00:00:00+00:00",
                    "source_type": "routine",
                    "source_name": "sample",
                    "tags": [],
                },
                # Hostile index entries: the filename must never escape the
                # reports directory, and must be .html, even if the index lies.
                {
                    "id": "escape",
                    "title": "Escape",
                    "filename": "../outside.html",
                    "created_at": "2026-08-04T00:00:00+00:00",
                    "source_type": "routine",
                    "source_name": "escape",
                    "tags": [],
                },
                {
                    "id": "nothtml",
                    "title": "Not HTML",
                    "filename": "private.txt",
                    "created_at": "2026-08-04T00:00:00+00:00",
                    "source_type": "routine",
                    "source_name": "nothtml",
                    "tags": [],
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(reports, "CHARTS_DIR", directory)
    monkeypatch.setattr(reports, "INDEX_FILE", index)
    return directory


def test_legacy_unauthenticated_report_path_no_longer_serves_bodies(reports_dir):
    """The old public mount is gone: no path under /reports/ returns a report."""
    with TestClient(create_app()) as client:
        for path in (
            "/reports/sample.html",
            "/reports/private.txt",
            "/reports/%2e%2e/outside.html",
        ):
            response = client.get(path)
            assert response.status_code == 404, path
            assert BODY not in response.text, path


def test_report_html_requires_authentication(reports_dir):
    """Unauthenticated callers get no body from the replacement route."""
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/reports/abc123/html")
    assert response.status_code in (401, 403)
    assert BODY not in response.text


def test_report_html_served_to_authenticated_user(reports_dir):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: USER
    with TestClient(app) as client:
        response = client.get("/api/v1/reports/abc123/html")
    assert response.status_code == 200
    assert response.text == BODY
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "report_id",
    [
        "escape",  # index entry pointing outside the reports directory
        "nothtml",  # index entry pointing at a non-.html file
        "../outside",  # traversal attempt through the id itself
        "missing",
    ],
)
def test_report_html_rejects_ids_that_do_not_resolve_to_a_contained_html_file(
    reports_dir, report_id
):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: USER
    with TestClient(app) as client:
        response = client.get(f"/api/v1/reports/{report_id}/html")
    assert response.status_code == 404
    assert OUTSIDE_BODY not in response.text
    assert NON_REPORT_BODY not in response.text
    assert BODY not in response.text
