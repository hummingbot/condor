"""CORR-211: a Telegram-started routine stamps its reports with its own name.

``reports.default_source`` is the safety net that keeps a report visible when
the routine never called ``ReportBuilder.source()``: ``list_reports_grouped``
skips entries without a ``source_name``, and both the per-routine web list and
``RoutineStore._get_report_counts`` match on it exactly. The routine store, the
code runner and the ``primitives`` nested runner all wrapped their runs with it;
the Telegram runner wrapped only ``attribute_owner``, so the same routine
started from ``/routines`` produced a report nothing on the Routines page could
find — while starting it from the dashboard worked.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import condor.reports as rep
import handlers.routines as hr
from condor.preferences import SERVER_PIN_KEY, USER_PREFERENCES_KEY

GROUP_ID = -1001234
OWNER_ID = 7


class Config(BaseModel):
    """A test routine."""


class FakeBot:
    async def send_message(self, chat_id, text, **kwargs):
        return SimpleNamespace(message_id=1)


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports_index.json")
    return tmp_path


@pytest.fixture
def quiet(monkeypatch):
    """Silence the parts of a run that talk to the store and to Telegram."""
    monkeypatch.setattr(
        hr,
        "get_routine_store",
        lambda: SimpleNamespace(
            store_result=lambda *a, **kw: None, remove_instance=lambda *a: None
        ),
    )

    async def _dispatch(*a, **kw):
        return None

    monkeypatch.setattr(hr.routine_hooks, "dispatch", _dispatch)


def _library(monkeypatch, run_fn, *, continuous=False):
    monkeypatch.setattr(
        hr,
        "get_routine",
        lambda name: SimpleNamespace(
            name="probe",
            is_continuous=continuous,
            config_class=Config,
            run_fn=run_fn,
        ),
    )


def _owner_bucket():
    return {
        USER_PREFERENCES_KEY: {"general": {"active_server": "prod"}},
        SERVER_PIN_KEY: True,
        "routine_instances": {},
    }


def _ptb_context(bucket):
    return SimpleNamespace(
        bot=FakeBot(),
        user_data=None,
        application=SimpleNamespace(user_data={OWNER_ID: bucket}, bot=FakeBot()),
    )


async def _save_sourceless(config, context):
    """A routine that forgets ``.source()`` — the case the net exists for."""
    builder = rep.ReportBuilder("Sourceless")
    builder.markdown("body")
    await builder.save()
    return "done"


def _entry():
    entries, _ = rep.list_reports()
    assert len(entries) == 1
    return entries[0]


def test_one_shot_run_stamps_the_routine_as_the_report_source(
    monkeypatch, quiet, reports_dir
):
    _library(monkeypatch, _save_sourceless)

    asyncio.run(
        hr._execute_routine(
            _ptb_context(_owner_bucket()),
            "i1",
            "probe",
            {},
            GROUP_ID,
            owner_id=OWNER_ID,
        )
    )

    entry = _entry()
    assert entry["source_type"] == "routine"
    assert entry["source_name"] == "probe"


def test_a_telegram_started_report_is_grouped_for_its_starter(
    monkeypatch, quiet, reports_dir
):
    _library(monkeypatch, _save_sourceless)

    asyncio.run(
        hr._execute_routine(
            _ptb_context(_owner_bucket()),
            "i1",
            "probe",
            {},
            GROUP_ID,
            owner_id=OWNER_ID,
        )
    )

    groups = rep.list_reports_grouped(owner_id=OWNER_ID)
    assert [g["source_name"] for g in groups] == ["probe"]


def test_continuous_run_stamps_the_routine_too(monkeypatch, quiet, reports_dir):
    _library(monkeypatch, _save_sourceless, continuous=True)

    asyncio.run(
        hr._run_continuous_routine(
            SimpleNamespace(user_data={OWNER_ID: _owner_bucket()}, bot=FakeBot()),
            "i1",
            "probe",
            {},
            GROUP_ID,
            owner_id=OWNER_ID,
        )
    )

    entry = _entry()
    assert (entry["source_type"], entry["source_name"]) == ("routine", "probe")


def test_an_explicit_source_still_wins(monkeypatch, quiet, reports_dir):
    async def _run(config, context):
        builder = rep.ReportBuilder("Explicit")
        builder.source("routine", "chosen_by_hand")
        builder.markdown("body")
        await builder.save()
        return "done"

    _library(monkeypatch, _run)

    asyncio.run(
        hr._execute_routine(
            _ptb_context(_owner_bucket()),
            "i1",
            "probe",
            {},
            GROUP_ID,
            owner_id=OWNER_ID,
        )
    )

    assert _entry()["source_name"] == "chosen_by_hand"


def test_the_default_source_is_dropped_once_the_run_ends(
    monkeypatch, quiet, reports_dir
):
    _library(monkeypatch, _save_sourceless)

    asyncio.run(
        hr._execute_routine(
            _ptb_context(_owner_bucket()),
            "i1",
            "probe",
            {},
            GROUP_ID,
            owner_id=OWNER_ID,
        )
    )

    assert rep.store._report_source.get() is None
