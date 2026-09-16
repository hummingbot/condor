"""ARCH-692: report lookups by producer match ``source_name`` in the store.

The session, strategy and routine report routes used to ask ``list_reports``
for a substring ``search`` page and re-filter it in Python, so an exact match
ranked past ``limit`` was silently lost and ``total`` counted the page. The
store now filters on ``source_name`` / ``source_prefix`` before slicing.
"""

import json

import pytest
from starlette.testclient import TestClient

import condor.reports as rep
import condor.web.auth as web_auth
import condor.web.routes.agents as agents_routes
from condor.web.app import create_app
from condor.web.auth import get_current_user
from condor.web.models import WebUser

ADMIN = WebUser(id=999, username="a", first_name="A", role="admin")


class _AdminConfig:
    def is_admin(self, user_id):
        return user_id == ADMIN.id


def _entry(i: int, source: str, title: str = "") -> dict:
    return {
        "id": f"r{i:05d}",
        "title": title or f"Report {i}",
        "filename": f"r{i:05d}.html",
        # Larger i = newer; ISO strings sort lexically.
        "created_at": f"2026-09-01T00:00:00.{i:06d}+00:00",
        "source_type": "routine",
        "source_name": source,
        "tags": [],
        "agent": "condor",
    }


@pytest.fixture
def write_index(tmp_path, monkeypatch):
    directory = tmp_path / "reports"
    directory.mkdir()
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(directory))
    monkeypatch.setattr(web_auth, "get_config_manager", lambda: _AdminConfig())

    def write(entries):
        (directory / "reports_index.json").write_text(
            json.dumps(entries), encoding="utf-8"
        )

    return write


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    return TestClient(app)


# ── Store ──


def test_source_name_is_exact_not_substring_or_prefix(write_index):
    write_index(
        [
            _entry(1, "a.b/session_1"),
            _entry(2, "a.bc/session_1"),
            _entry(3, "a.b/session_10"),
        ]
    )
    reports, total = rep.list_reports(source_name="a.b/session_1")
    assert [r["source_name"] for r in reports] == ["a.b/session_1"]
    assert total == 1


def test_source_name_accepts_several_names(write_index):
    write_index([_entry(1, "x/pmm"), _entry(2, "pmm"), _entry(3, "pmm_king")])
    reports, total = rep.list_reports(source_name=("x/pmm", "pmm"))
    assert {r["source_name"] for r in reports} == {"x/pmm", "pmm"}
    assert total == 2


def test_source_prefix_total_counts_every_match_not_the_page(write_index):
    write_index(
        [_entry(i, f"a.b/session_{i}") for i in range(1, 8)]
        + [_entry(100, "a.bc/session_1"), _entry(101, "other")]
    )
    reports, total = rep.list_reports(source_prefix="a.b/", limit=3)
    assert len(reports) == 3
    assert total == 7
    assert all(r["source_name"].startswith("a.b/") for r in reports)


# ── Routes ──


def test_session_and_strategy_routes_see_past_the_old_page_cap(
    write_index, monkeypatch
):
    """150 sessions (index larger than the default prune) plus older noise."""
    monkeypatch.setattr(rep.store, "MAX_REPORTS", 1000)
    monkeypatch.setattr(agents_routes, "_get_strategy", lambda slug, sslug: object())
    older = [_entry(i, f"brigado.grid_other/session_{i}") for i in range(1, 21)]
    sessions = [_entry(100 + n, f"brigado.grid/session_{n}") for n in range(1, 151)]
    write_index(older + sessions)

    with _client() as client:
        # session_1 is the OLDEST of 150 run-key hits: ranked past any cap of 100.
        first = client.get("/api/v1/agents/brigado/strategies/grid/sessions/1/report")
        page = client.get("/api/v1/agents/brigado/strategies/grid/reports?limit=50")

    assert first.status_code == 200
    assert first.json()["report"]["source_name"] == "brigado.grid/session_1"
    body = page.json()
    assert len(body["reports"]) == 50
    assert body["total"] == 150


def test_routine_route_keeps_exact_matches_ranked_past_substring_hits(write_index):
    # 60 newer `pmm_king` reports would fill a 50-row `search="pmm"` page first.
    king = [_entry(100 + i, "pmm_king") for i in range(60)]
    pmm = [_entry(i, "pmm") for i in range(5)]
    write_index(king + pmm)

    with _client() as client:
        body = client.get("/api/v1/routines/pmm/reports").json()

    assert len(body["reports"]) == 5
    assert {r["source_name"] for r in body["reports"]} == {"pmm"}
    assert body["total"] == 5


def test_routine_route_matches_prefixed_and_base_names(write_index):
    write_index([_entry(1, "myagent/pmm"), _entry(2, "pmm"), _entry(3, "pmm_king")])

    with _client() as client:
        body = client.get("/api/v1/routines/myagent/pmm/reports").json()

    assert {r["source_name"] for r in body["reports"]} == {"myagent/pmm", "pmm"}
    assert body["total"] == 2
