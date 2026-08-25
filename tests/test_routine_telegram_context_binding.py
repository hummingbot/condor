"""CORR-205: a routine started from Telegram publishes its own context.

FEAT-052 made routines composable, and a nested ``call_routine()`` inherits the
caller's server, bot and user from the context ``primitives.bind_context``
publishes. Two of the three runners published one; the Telegram runner never
did, so a ``/routines`` run that composed fell back to a bare
``WebRoutineContext("")`` — the ambient-default server FEAT-051 closed, and a
report stamped with owner 0 that its own starter could not read.

The owner is threaded explicitly as ``_owner_id`` because the Telegram runner
separates the delivery chat (negative in a group) from the person the run
belongs to; binding the context alone would attribute a nested run to the room.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import handlers.routines as hr
from condor import primitives
from condor.preferences import SERVER_PIN_KEY, USER_PREFERENCES_KEY

GROUP_ID = -1001234
OWNER_ID = 7


class Config(BaseModel):
    """A test routine."""


class FakeBot:
    async def send_message(self, chat_id, text, **kwargs):
        return SimpleNamespace(message_id=1)


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


def _owner_bucket(server="prod"):
    return {
        USER_PREFERENCES_KEY: {"general": {"active_server": server}},
        SERVER_PIN_KEY: True,
        "routine_instances": {},
    }


def _ptb_context(bucket):
    return SimpleNamespace(
        bot=FakeBot(),
        user_data=None,
        application=SimpleNamespace(user_data={OWNER_ID: bucket}, bot=FakeBot()),
    )


def test_a_one_shot_run_publishes_the_context_its_routine_receives(monkeypatch, quiet):
    seen = {}

    async def _run(config, context):
        seen["inherited"] = primitives._resolve_context(None)
        seen["given"] = context
        return "done"

    _library(monkeypatch, _run)
    context = _ptb_context(_owner_bucket())

    asyncio.run(
        hr._execute_routine(context, "i1", "probe", {}, GROUP_ID, owner_id=OWNER_ID)
    )

    assert seen["inherited"] is seen["given"] is context


def test_a_one_shot_run_hands_a_nested_call_the_starter_and_the_server(
    monkeypatch, quiet
):
    seen = {}

    async def _run(config, context):
        ctx = primitives._resolve_context(None)
        seen["owner"] = primitives._owner_of(ctx)
        seen["server"] = primitives._server_of(ctx)
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

    assert seen == {"owner": OWNER_ID, "server": "prod"}


def test_a_continuous_run_publishes_the_context_too(monkeypatch, quiet):
    seen = {}

    async def _run(config, context):
        ctx = primitives._resolve_context(None)
        seen["inherited"] = ctx
        seen["given"] = context
        seen["owner"] = primitives._owner_of(ctx)
        seen["server"] = primitives._server_of(ctx)
        return "done"

    _library(monkeypatch, _run, continuous=True)
    application = SimpleNamespace(user_data={OWNER_ID: _owner_bucket()}, bot=FakeBot())

    asyncio.run(
        hr._run_continuous_routine(
            application, "i1", "probe", {}, GROUP_ID, owner_id=OWNER_ID
        )
    )

    assert seen["inherited"] is seen["given"]
    assert seen["owner"] == OWNER_ID
    assert seen["server"] == "prod"


def test_the_published_context_is_dropped_once_the_run_ends(monkeypatch, quiet):
    async def _run(config, context):
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

    assert primitives._CONTEXT.get() is None
