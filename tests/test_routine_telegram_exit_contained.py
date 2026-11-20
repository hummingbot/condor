"""XC-04 (stopgap): the Telegram runner contains ``exit()`` like the store runner.

``RoutineStore._execute_and_record`` wraps a run in ``code_guard.guarded`` and
``code_guard.contain_exit``; the Telegram runner had neither, so a routine
calling ``sys.exit()`` from the ``/routines`` menu raised ``SystemExit`` out of
the job callback — which asyncio re-raises out of the event loop.
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import handlers.routines as hr
from condor.runtime import code_guard

OWNER_ID = 7


class Config(BaseModel):
    """A test routine."""


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
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


def _library(monkeypatch, run_fn):
    monkeypatch.setattr(
        hr,
        "get_routine",
        lambda name: SimpleNamespace(
            name="probe", is_continuous=False, config_class=Config, run_fn=run_fn
        ),
    )


def _context():
    bucket = {"routine_instances": {"i1": {}}}
    return SimpleNamespace(
        bot=None,
        user_data=None,
        application=SimpleNamespace(user_data={OWNER_ID: bucket}, bot=None),
    )


def test_exit_in_a_one_shot_run_is_an_error_string(monkeypatch):
    async def _run(config, context):
        sys.exit(0)

    _library(monkeypatch, _run)

    result, _, _ = asyncio.run(
        hr._execute_routine(_context(), "i1", "probe", {}, OWNER_ID)
    )

    assert result.startswith("Error:")
    assert "SystemExit" in result


def test_a_one_shot_run_executes_under_the_guard_scope(monkeypatch):
    seen = {}

    async def _run(config, context):
        seen["scope"] = code_guard._SCOPE.get()
        return "done"

    _library(monkeypatch, _run)

    asyncio.run(hr._execute_routine(_context(), "i1", "probe", {}, OWNER_ID))

    assert seen["scope"] is not None and seen["scope"].label == "i1"
    assert code_guard._SCOPE.get() is None


def test_exit_in_a_continuous_run_ends_the_routine_not_the_loop(monkeypatch):
    async def _run(config, context):
        raise SystemExit(0)

    _library(monkeypatch, _run)
    context = _context()

    # Returns normally: the SystemExit never leaves the task.
    asyncio.run(
        hr._run_continuous_routine(context.application, "i1", "probe", {}, OWNER_ID)
    )

    assert "i1" not in context.application.user_data[OWNER_ID]["routine_instances"]
