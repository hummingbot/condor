"""A routine run remembers the report it rendered.

The instance store holds what the caller got back as text; the report index holds
the page the routine actually wrote. Without the link between them, a reader who
opens a run in the chat dock sees only the summary handed to the LLM.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import condor.reports as rep
from condor.routine_store import RoutineStore


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports_index.json")
    return tmp_path


def _routine(run_fn):
    class Config(BaseModel):
        pass

    return SimpleNamespace(
        name="probe", source="global", config_class=Config, run_fn=run_fn
    )


def _run(store: RoutineStore, routine) -> dict:
    asyncio.run(
        store._execute_and_record(
            "i1", routine, {}, "srv", status_after="completed", fire_hooks=False
        )
    )
    return store._instances["i1"]


def _store() -> RoutineStore:
    store = RoutineStore()
    store._instances["i1"] = store._new_instance_meta(
        "probe", {}, "srv", 0, source="web"
    )
    return store


async def _writes_report(config, ctx):
    builder = rep.ReportBuilder("Run report")
    builder.source("routine", "probe")
    builder.markdown("body")
    await builder.save()
    return "done"


async def _writes_nothing(config, ctx):
    return "text only"


def test_new_instance_has_no_report(reports_dir):
    assert _store()._instances["i1"]["report_id"] is None


def test_run_records_the_report_it_rendered(reports_dir):
    meta = _run(_store(), _routine(_writes_report))
    assert meta["report_id"] == rep.list_reports()[0][0]["id"]


def test_a_run_that_renders_nothing_claims_no_report(reports_dir):
    """Otherwise the second run of a routine would point at the first run's page."""
    store = _store()
    _run(store, _routine(_writes_report))
    meta = _run(store, _routine(_writes_nothing))
    assert meta["report_id"] is None
