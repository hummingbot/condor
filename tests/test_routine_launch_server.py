"""CORR-221: a Telegram routine runs against the server it was launched with.

Both Telegram runners capture ``get_effective_server()`` when the instance is
created — ``user_data`` is keyed by user id, so a job or a continuous task
ticking later in a group chat cannot look the room's server up for itself.
Both then wrote the captured name into a bare ``preferences`` key that nothing
in the repo reads: the preference API reads ``USER_PREFERENCES_KEY``. So the
injection was inert, its guard was inert with it (it read the same dead key,
so it never fired and the write always ran), and the run resolved whatever
ambient default it found — the very case the capture was added to cover. It is
the same key mismatch ``WebRoutineContext`` carried, left behind in Telegram.

The binding is *pinned*: the captured name is the server the user was looking
at when they pressed Run, and a scheduled or continuous run ticks long after
that, so a ``chat_defaults`` entry recorded in the meantime must not move a
running routine onto another server.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import config_manager as cm_module
import handlers.routines as hr
from condor.preferences import SERVER_PIN_KEY, USER_PREFERENCES_KEY, get_active_server

GROUP_ID = -1001234
OWNER_ID = 7

LAUNCHED_WITH = "brigado_2"
CHAT_DEFAULT = "local"
OWN_CHOICE = "prod"


class Config(BaseModel):
    """A test routine."""


class FakeBot:
    async def send_message(self, chat_id, text, **kwargs):
        return SimpleNamespace(message_id=1)


class FakeConfigManager:
    """Only what the resolution chain touches, plus what it asked for."""

    def __init__(self):
        self._data = {
            "servers": {LAUNCHED_WITH: {}, CHAT_DEFAULT: {}, OWN_CHOICE: {}},
            "chat_defaults": {},
            "default_server": CHAT_DEFAULT,
        }
        self.asked_for = None

    async def get_client(self, name=None):
        self.asked_for = name
        return f"client:{name}"

    def get_default_server(self):
        return self._data["default_server"]

    def get_accessible_servers(self, user_id):
        return list(self._data["servers"])

    # The methods under test, borrowed from the real class.
    get_client_for_chat = cm_module.ConfigManager.get_client_for_chat
    get_chat_default_server = cm_module.ConfigManager.get_chat_default_server


@pytest.fixture
def cm(monkeypatch):
    fake = FakeConfigManager()
    monkeypatch.setattr(cm_module, "get_config_manager", lambda: fake)
    return fake


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


def _owner_bucket(server=None):
    """The starter's real PTB bucket, with or without a server of their own."""
    bucket = {"routine_instances": {}}
    if server:
        bucket[USER_PREFERENCES_KEY] = {"general": {"active_server": server}}
    return bucket


def _ptb_context(bucket):
    return SimpleNamespace(
        bot=FakeBot(),
        user_data=None,
        application=SimpleNamespace(user_data={OWNER_ID: bucket}, bot=FakeBot()),
    )


def _resolver(seen):
    """A routine that reaches for a client exactly as a real routine does."""

    async def _run(config, context):
        seen["client"] = await cm_module.get_client(GROUP_ID, context=context)
        return "done"

    return _run


def _run_one_shot(context):
    return asyncio.run(
        hr._execute_routine(
            context,
            "i1",
            "probe",
            {},
            GROUP_ID,
            active_server=LAUNCHED_WITH,
            owner_id=OWNER_ID,
        )
    )


def _run_continuous(application):
    asyncio.run(
        hr._run_continuous_routine(
            application,
            "i1",
            "probe",
            {},
            GROUP_ID,
            active_server=LAUNCHED_WITH,
            owner_id=OWNER_ID,
        )
    )


def test_a_group_run_resolves_the_server_it_was_launched_with(monkeypatch, quiet, cm):
    """The whole chain: capture → user_data → get_effective_server → client."""
    seen = {}
    _library(monkeypatch, _resolver(seen))

    _run_one_shot(_ptb_context(_owner_bucket()))

    assert seen["client"] == f"client:{LAUNCHED_WITH}"


def test_a_chat_default_recorded_after_launch_does_not_steal_the_run(
    monkeypatch, quiet, cm
):
    """The pin: the room's default may change mid-run; the run may not."""
    seen = {}
    _library(monkeypatch, _resolver(seen))
    bucket = _owner_bucket()
    context = _ptb_context(bucket)

    hr._bind_launch_server(bucket, LAUNCHED_WITH)
    # ...and only now does someone point the room at another server.
    cm._data["chat_defaults"][GROUP_ID] = CHAT_DEFAULT

    _run_one_shot(context)

    assert seen["client"] == f"client:{LAUNCHED_WITH}", "the chat default lost"
    assert bucket[SERVER_PIN_KEY] is True


def test_an_owner_who_already_picked_a_server_keeps_it(monkeypatch, quiet, cm):
    """The guard is a real check now, so it must not clobber a real preference."""
    seen = {}
    _library(monkeypatch, _resolver(seen))
    bucket = _owner_bucket(OWN_CHOICE)

    _run_one_shot(_ptb_context(bucket))

    assert get_active_server(bucket) == OWN_CHOICE
    assert SERVER_PIN_KEY not in bucket, "an untouched bucket is not pinned"
    assert seen["client"] == f"client:{OWN_CHOICE}"


def test_a_continuous_run_binds_the_launch_server_the_same_way(monkeypatch, quiet, cm):
    """The continuous runner carried its own copy of the same broken walk."""
    seen = {}
    _library(monkeypatch, _resolver(seen), continuous=True)
    bucket = _owner_bucket()
    application = SimpleNamespace(user_data={OWNER_ID: bucket}, bot=FakeBot())
    cm._data["chat_defaults"][GROUP_ID] = CHAT_DEFAULT

    _run_continuous(application)

    assert seen["client"] == f"client:{LAUNCHED_WITH}"
    assert get_active_server(bucket) == LAUNCHED_WITH
    assert bucket[SERVER_PIN_KEY] is True


def test_no_captured_server_leaves_the_bucket_alone(monkeypatch, quiet, cm):
    """Capture can fail (it is wrapped in a bare except); that must bind nothing."""
    bucket = _owner_bucket()

    hr._bind_launch_server(bucket, None)

    assert get_active_server(bucket) is None
    assert SERVER_PIN_KEY not in bucket


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
