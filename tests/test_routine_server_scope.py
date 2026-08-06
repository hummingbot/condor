"""A routine runs against the server it was launched with.

``RoutineStore`` hands every non-Telegram run a ``WebRoutineContext`` built from
the run's ``server_name`` — that is the whole point of the class. But the server
never reached the client:

  * the context stored it under the key ``"preferences"`` while the preference API
    reads ``"user_preferences"``, so ``get_effective_server`` resolved nothing; and
  * ``get_client_for_chat`` dropped ``preferred_server`` outright on its no-``user_id``
    branch — the branch every routine run takes.

Either bug alone is enough to silently run against the chat default instead. With
three servers configured, a backtest picked in the dashboard for ``brigado_2`` ran
against ``local``, and its results were filed under the wrong server.
"""

import asyncio

import pytest

import config_manager as cm_module
from condor.preferences import get_active_server
from condor.routine_store import WebRoutineContext

LAUNCHED_WITH = "brigado_2"
CHAT_DEFAULT = "local"


class FakeConfigManager:
    """Only what the resolution path touches, plus a record of what it asked for."""

    def __init__(self):
        self._data = {
            "servers": {LAUNCHED_WITH: {}, CHAT_DEFAULT: {}},
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


def test_the_context_exposes_the_launch_server_as_a_preference():
    """The preference API must be able to read what the context wrote."""
    ctx = WebRoutineContext(LAUNCHED_WITH, bot=object(), chat_id=0)

    assert ctx.server_name == LAUNCHED_WITH
    assert get_active_server(ctx.user_data) == LAUNCHED_WITH


def test_a_routine_run_reaches_the_server_it_was_launched_with(cm):
    """The full chain: context → get_effective_server → get_client_for_chat."""
    ctx = WebRoutineContext(LAUNCHED_WITH, bot=object(), chat_id=0)

    client = asyncio.run(cm_module.get_client(ctx._chat_id, context=ctx))

    assert client == f"client:{LAUNCHED_WITH}"
    assert cm.asked_for == LAUNCHED_WITH, "the chat default must not win here"


def test_an_explicit_server_wins_over_the_chat_default_without_a_user_id(cm):
    """The no-user_id branch used to ignore preferred_server entirely."""
    client = asyncio.run(
        cm.get_client_for_chat(0, None, preferred_server=LAUNCHED_WITH)
    )

    assert client == f"client:{LAUNCHED_WITH}"


def test_an_unknown_preferred_server_still_falls_back(cm):
    """A stale preference must not break the run — nor invent a server."""
    client = asyncio.run(cm.get_client_for_chat(0, None, preferred_server="deleted"))

    assert client == f"client:{CHAT_DEFAULT}"


def test_the_user_id_branch_is_unchanged(cm):
    """With a user_id, access is still checked before the preference is honoured."""
    cm._data["servers"].pop(LAUNCHED_WITH)

    client = asyncio.run(cm.get_client_for_chat(0, 111, preferred_server=LAUNCHED_WITH))

    assert client == f"client:{CHAT_DEFAULT}", "an inaccessible server is not honoured"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
