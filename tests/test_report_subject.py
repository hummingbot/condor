"""FEAT-078: a report records what it is *about*, and is found again by it.

A report generated for one specific thing — an archived run, one controller
inside it — was findable only by who made it, what made it and free text, so a
caller wanting "the report for this controller" had to regenerate one it already
had. Now ``ReportBuilder.subject()`` stamps an opaque key from
``condor.reports.subjects`` on the index entry and ``list_reports(subject=...)``
matches it exactly: the right report, nothing else, or nothing at all.
"""

import asyncio
import json

import pytest

import condor.reports as reports
from condor.reports import ReportBuilder, store, subjects

RUN = subjects.bot_run("brigado", "/data/bots/archive/run.sqlite")
CONTROLLER = subjects.bot_run("brigado", "/data/bots/archive/run.sqlite", "pmm_1")


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(reports, "INDEX_FILE", tmp_path / "reports_index.json")
    return tmp_path


def _save(title: str, subject: str = "", owner: int | None = None) -> str:
    builder = ReportBuilder(title).kpi("PnL", "1")
    if subject:
        builder.subject(subject)
    with store.attribute_owner(owner):
        return asyncio.run(builder.save())


def _index(reports_dir) -> list[dict]:
    return json.loads((reports_dir / "reports_index.json").read_text(encoding="utf-8"))


# ── Keys are built in one place ──


def test_a_subject_key_is_stable_and_distinguishes_its_parts():
    assert subjects.bot_run("s", "/db") == subjects.bot_run("s", "/db")
    assert subjects.bot_run("s", "/db") != subjects.bot_run("s", "/db", "ctrl")
    assert subjects.bot_run("s", "/db") != subjects.bot_run("other", "/db")


# ── Stamping and finding ──


def test_a_stamped_report_is_found_by_its_key(reports_dir):
    report_id = _save("Run summary", RUN)

    found, total = store.list_reports(subject=RUN)

    assert [entry["id"] for entry in found] == [report_id]
    assert total == 1


def test_a_different_key_finds_nothing(reports_dir):
    _save("Run summary", RUN)

    found, total = store.list_reports(subject=CONTROLLER)

    assert found == []
    assert total == 0


def test_a_report_without_a_subject_is_never_returned_by_one(reports_dir):
    _save("Ad-hoc report")

    assert store.list_reports(subject=RUN) == ([], 0)


def test_a_legacy_entry_with_no_subject_key_at_all_still_lists(reports_dir):
    """Entries written before the field existed keep listing exactly as before."""
    _save("Legacy", RUN)
    entries = _index(reports_dir)
    del entries[0]["subject"]
    (reports_dir / "reports_index.json").write_text(
        json.dumps(entries), encoding="utf-8"
    )

    listed, total = store.list_reports()

    assert [entry["id"] for entry in listed] == [entries[0]["id"]]
    assert total == 1
    assert store.list_reports(subject=RUN) == ([], 0)


def test_the_subject_survives_an_update_in_place(reports_dir):
    report_id = _save("Run summary", RUN)

    builder = ReportBuilder("Run summary").subject(RUN).kpi("PnL", "2")
    asyncio.run(builder.save(report_id=report_id))

    found, _ = store.list_reports(subject=RUN)
    assert [entry["id"] for entry in found] == [report_id]


# ── Scoping is not widened ──


def test_a_subject_lookup_is_still_scoped_to_its_owner(reports_dir):
    report_id = _save("Run summary", RUN, owner=111)

    assert store.list_reports(subject=RUN, owner_id=111)[0][0]["id"] == report_id
    assert store.list_reports(subject=RUN, owner_id=222) == ([], 0)


# ── Pruning ──


def test_a_pruned_reports_key_returns_nothing(reports_dir, monkeypatch):
    """The index keeps only the newest MAX_REPORTS; a dangling key is an
    ordinary empty answer, not an error — the caller regenerates."""
    _save("Run summary", RUN)
    for index in range(3):
        _save(f"Later {index}")

    async def prune():
        async with store._index_lock:
            store._cleanup_locked(max_reports=2)

    asyncio.run(prune())

    assert store.list_reports(subject=RUN) == ([], 0)


# ── The summary model carries it ──


def test_the_report_summary_round_trips_the_subject(reports_dir):
    from condor.web.models import ReportSummary

    _save("Run summary", RUN)
    entry = _index(reports_dir)[0]

    assert ReportSummary(**entry).subject == RUN
    entry.pop("subject")
    assert ReportSummary(**entry).subject == ""
