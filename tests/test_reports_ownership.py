"""SEC-196: reports are owned, and the API authorizes reads/deletes by owner.

The reports index was the one server-data surface in condor/web not scoped per
user: any approved caller could list every report, read every HTML body (which
carries portfolio value, PnL and positions) and delete any of them. Now every
runner stamps the authenticated principal on the entry at save time, the list
endpoints filter by it, and the id-addressed endpoints follow the sessions
idiom: own passes, admins see everything, and legacy entries with no owner are
admin-only (fail closed), never world-readable.
"""

import asyncio
import json

import pytest
from starlette.testclient import TestClient

import condor.reports as rep
import condor.web.routes.reports as routes
from condor.web.app import create_app
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")
OTHER = WebUser(id=222, username="o", first_name="O", role="user")
ADMIN = WebUser(id=999, username="a", first_name="A", role="admin")


class FakeConfigManager:
    def is_admin(self, user_id):
        return user_id == ADMIN.id


def _entry(report_id: str, filename: str, owner=None, source="r"):
    entry = {
        "id": report_id,
        "title": f"Report {report_id}",
        "filename": filename,
        "created_at": "2026-08-19T00:00:00+00:00",
        "source_type": "routine",
        "source_name": source,
        "tags": [],
        "agent": "condor",
    }
    if owner is not None:
        entry["user_id"] = owner
    return entry


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    """One report per principal, plus a legacy entry with no owner at all."""
    directory = tmp_path / "reports"
    directory.mkdir()
    for name, body in (
        ("mine.html", "<h1>mine</h1>"),
        ("theirs.html", "<h1>theirs</h1>"),
        ("legacy.html", "<h1>legacy</h1>"),
    ):
        (directory / name).write_text(body, encoding="utf-8")
    index = directory / "reports_index.json"
    index.write_text(
        json.dumps(
            [
                _entry("mine01", "mine.html", owner=USER.id, source="mine"),
                _entry("their1", "theirs.html", owner=OTHER.id, source="theirs"),
                # Written before SEC-196: no user_id key at all.
                _entry("legacy", "legacy.html", source="legacy"),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rep, "CHARTS_DIR", directory)
    monkeypatch.setattr(rep, "INDEX_FILE", index)
    monkeypatch.setattr(routes, "get_config_manager", lambda: FakeConfigManager())
    return directory


def _client(user: WebUser) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# ── Listing ──


def test_list_returns_only_your_own_for_a_non_admin(reports_dir):
    with _client(USER) as client:
        payload = client.get("/api/v1/reports").json()
    assert [r["id"] for r in payload["reports"]] == ["mine01"]
    assert payload["total"] == 1


def test_list_returns_everything_for_an_admin(reports_dir):
    with _client(ADMIN) as client:
        payload = client.get("/api/v1/reports").json()
    assert sorted(r["id"] for r in payload["reports"]) == [
        "legacy",
        "mine01",
        "their1",
    ]


def test_grouped_listing_is_scoped_the_same_way(reports_dir):
    with _client(USER) as client:
        groups = client.get("/api/v1/reports/latest-by-source").json()
    assert [g["source_name"] for g in groups] == ["mine"]
    with _client(ADMIN) as client:
        groups = client.get("/api/v1/reports/latest-by-source").json()
    assert sorted(g["source_name"] for g in groups) == ["legacy", "mine", "theirs"]


# ── Id-addressed reads and deletes ──


def test_owner_reads_detail_html_and_deletes_their_own(reports_dir):
    with _client(USER) as client:
        assert client.get("/api/v1/reports/mine01").status_code == 200
        response = client.get("/api/v1/reports/mine01/html")
        assert response.status_code == 200
        assert response.text == "<h1>mine</h1>"
        assert client.delete("/api/v1/reports/mine01").json() == {"deleted": True}


def test_cross_owner_report_is_refused_everywhere(reports_dir):
    """A user cannot read the metadata, body, or delete another user's report."""
    with _client(USER) as client:
        assert client.get("/api/v1/reports/their1").status_code == 403
        response = client.get("/api/v1/reports/their1/html")
        assert response.status_code == 403
        assert "theirs" not in response.text
        assert client.delete("/api/v1/reports/their1").status_code == 403
    # And nothing was deleted.
    assert rep.get_report("their1") is not None


def test_ownerless_legacy_entry_is_admin_only(reports_dir):
    """No owner recorded -> fail closed: invisible to non-admins, not deleted."""
    with _client(USER) as client:
        assert client.get("/api/v1/reports/legacy").status_code == 403
        assert client.get("/api/v1/reports/legacy/html").status_code == 403
        assert client.delete("/api/v1/reports/legacy").status_code == 403
    assert rep.get_report("legacy") is not None
    with _client(ADMIN) as client:
        assert client.get("/api/v1/reports/legacy").status_code == 200
        assert client.get("/api/v1/reports/legacy/html").text == "<h1>legacy</h1>"


def test_admin_reads_and_deletes_across_owners(reports_dir):
    with _client(ADMIN) as client:
        assert client.get("/api/v1/reports/their1/html").status_code == 200
        assert client.delete("/api/v1/reports/their1").json() == {"deleted": True}


def test_missing_report_is_404_not_403(reports_dir):
    with _client(USER) as client:
        assert client.get("/api/v1/reports/nope42").status_code == 404


# ── Producer stamping ──


def test_save_records_the_owner_from_attribute_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports_index.json")

    async def go():
        with rep.attribute_owner(USER.id):
            b = rep.ReportBuilder("Owned")
            b.source("routine", "r")
            b.markdown("body")
            await b.save()
        # Outside the block the owner resets: a bare save is ownerless.
        b = rep.ReportBuilder("Bare")
        b.source("routine", "r")
        b.markdown("body")
        await b.save()

    asyncio.run(go())
    by_title = {e["title"]: e for e in rep.list_reports()[0]}
    assert by_title["Owned"]["user_id"] == USER.id
    assert by_title["Bare"]["user_id"] is None


def test_list_reports_owner_filter_drops_foreign_and_ownerless(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports_index.json")

    async def go():
        for owner, title in ((USER.id, "Mine"), (OTHER.id, "Theirs"), (None, "Old")):
            with rep.attribute_owner(owner):
                b = rep.ReportBuilder(title)
                b.source("routine", "r")
                b.markdown("body")
                await b.save()

    asyncio.run(go())
    mine, total = rep.list_reports(owner_id=USER.id)
    assert [e["title"] for e in mine] == ["Mine"]
    assert total == 1
    everything, _ = rep.list_reports()
    assert len(everything) == 3


def test_live_report_update_preserves_the_owner(tmp_path, monkeypatch):
    """An in-place update keeps the user_id stamped at first save."""
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports_index.json")

    async def go():
        live = rep.LiveReport("Live", source_name="loop")
        with rep.attribute_owner(USER.id):
            live.builder.markdown("tick 1")
            await live.update()
        live.clear()
        live.builder.markdown("tick 2")
        with rep.attribute_owner(USER.id):
            await live.update()
        return live.report_id

    report_id = asyncio.run(go())
    assert rep.get_report(report_id)["user_id"] == USER.id
