"""A default server picked on one surface is the default on the other.

Both surfaces write ``chat_defaults`` in config.yml when the user picks a
default. Telegram *also* writes the choice into its pickle ``user_data``, and
``get_effective_server`` used to read that pickle first — so a default set in the
web dashboard was invisible to a live Telegram session: config.yml said one
server, the in-memory pickle said the one chosen before it, and the pickle won
every lookup until the bot process restarted. Logging out and back in on the web
made the web agree with itself; nothing made Telegram agree with the web.

So config.yml is read first now, and the pickle is kept as a cache of it. The one
thing that must survive the inversion is a *pinned* server: a routine or agent run
launched against a named server must not be re-resolved to the chat's default.
"""

import asyncio

import pytest

import config_manager as cm_module
from condor.preferences import SERVER_PIN_KEY, USER_PREFERENCES_KEY, set_active_server
from condor.routine_store import WebRoutineContext

CHAT_ID = 4242
WEB_PICK = "brigado_2"
TELEGRAM_PICK = "local"


class FakeConfigManager:
    """Only the state ``get_effective_server`` reads, plus the preference writes."""

    def __init__(self):
        self._data = {
            "servers": {WEB_PICK: {}, TELEGRAM_PICK: {}},
            "chat_defaults": {},
        }
        self.preference_writes: list[tuple[int, str]] = []

    def set_user_preference(self, user_id, key, value):
        self.preference_writes.append((user_id, key))

    def get_user_preferences(self, user_id):
        return {}


@pytest.fixture
def cm(monkeypatch):
    fake = FakeConfigManager()
    monkeypatch.setattr(cm_module, "get_config_manager", lambda: fake)
    return fake


def _telegram_user_data(server: str) -> dict:
    """What a live Telegram session holds after the user picked ``server``."""
    user_data: dict = {}
    set_active_server(user_data, server)
    return user_data


def test_a_default_set_on_the_web_reaches_a_live_telegram_session(cm):
    user_data = _telegram_user_data(TELEGRAM_PICK)

    # The web dashboard's "set default": config.yml only, no access to the pickle.
    cm._data["chat_defaults"][CHAT_ID] = WEB_PICK

    assert cm_module.get_effective_server(CHAT_ID, user_data) == WEB_PICK


def test_the_pickle_is_corrected_rather_than_left_disagreeing(cm):
    user_data = _telegram_user_data(TELEGRAM_PICK)
    cm._data["chat_defaults"][CHAT_ID] = WEB_PICK

    cm_module.get_effective_server(CHAT_ID, user_data)

    assert user_data[USER_PREFERENCES_KEY]["general"]["active_server"] == WEB_PICK


def test_agreeing_with_config_costs_no_write(cm):
    # This runs on every server lookup, and the setter persists the whole
    # preference section — so a pickle that already agrees must stay silent.
    user_data = _telegram_user_data(WEB_PICK)
    user_data["_user_id"] = 7
    cm._data["chat_defaults"][CHAT_ID] = WEB_PICK
    cm.preference_writes.clear()

    cm_module.get_effective_server(CHAT_ID, user_data)

    assert cm.preference_writes == []


def test_a_chat_with_no_default_still_uses_the_users_own_pick(cm):
    user_data = _telegram_user_data(TELEGRAM_PICK)

    assert cm_module.get_effective_server(CHAT_ID, user_data) == TELEGRAM_PICK


def test_a_deleted_server_named_in_config_does_not_win(cm):
    user_data = _telegram_user_data(TELEGRAM_PICK)
    cm._data["chat_defaults"][CHAT_ID] = "deleted"

    assert cm_module.get_effective_server(CHAT_ID, user_data) == TELEGRAM_PICK


def test_a_run_pinned_to_a_server_is_never_re_resolved(cm):
    # A routine launched against WEB_PICK from a chat whose default is
    # TELEGRAM_PICK: the launch server is the answer, or the run files its
    # results under a server it never touched.
    cm._data["chat_defaults"][CHAT_ID] = TELEGRAM_PICK
    ctx = WebRoutineContext(WEB_PICK, bot=object(), chat_id=CHAT_ID)

    assert ctx.user_data[SERVER_PIN_KEY] is True
    assert cm_module.get_effective_server(CHAT_ID, ctx.user_data) == WEB_PICK


def test_nothing_configured_resolves_to_nothing(cm):
    assert cm_module.get_effective_server(CHAT_ID, {}) is None
    assert cm_module.get_effective_server(CHAT_ID, None) is None


# ---------------------------------------------------------------------------
# The gate that keeps the pickle honest for handlers that never resolve.
#
# Most handlers (/portfolio, /bots, the gateway screens) read the pickle copy
# via ``get_active_server`` instead of resolving through
# ``get_effective_server``. Nothing corrected that copy on their path, so a
# default set on the web stayed invisible to /portfolio until the user happened
# to open /servers, which resolves as a side effect. ``@restricted`` runs on
# every Telegram update, so the correction belongs there.
# ---------------------------------------------------------------------------


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeUser:
    id = 7
    username = "fede"


class _FakeUpdate:
    def __init__(self, chat_id):
        self.effective_chat = _FakeChat(chat_id)
        self.effective_user = _FakeUser()
        self.message = None
        self.callback_query = None


class _FakeContext:
    def __init__(self, user_data):
        self.user_data = user_data


@pytest.fixture
def auth(monkeypatch, cm):
    """``utils.auth`` with the config manager it imported swapped for the fake."""
    import utils.auth as auth_module
    from config_manager import UserRole

    cm.get_user_role = lambda user_id: UserRole.USER
    monkeypatch.setattr(auth_module, "get_config_manager", lambda: cm)
    return auth_module


def test_a_handler_reading_the_pickle_sees_the_new_default_on_the_next_command(
    auth, cm
):
    """What /portfolio does: read ``get_active_server``, never resolve."""
    from condor.preferences import get_active_server

    user_data = _telegram_user_data(TELEGRAM_PICK)
    seen = []

    @auth.restricted
    async def portfolio_like(update, context):
        seen.append(get_active_server(context.user_data))

    cm._data["chat_defaults"][CHAT_ID] = WEB_PICK  # the web dashboard's pick
    asyncio.run(portfolio_like(_FakeUpdate(CHAT_ID), _FakeContext(user_data)))

    assert seen == [WEB_PICK]


def test_a_pinned_run_is_not_rewritten_by_the_gate(auth, cm):
    from condor.preferences import get_active_server

    cm._data["chat_defaults"][CHAT_ID] = TELEGRAM_PICK
    ctx = WebRoutineContext(WEB_PICK, bot=object(), chat_id=CHAT_ID)

    @auth.restricted
    async def handler(update, context):
        return None

    asyncio.run(handler(_FakeUpdate(CHAT_ID), ctx))

    assert get_active_server(ctx.user_data) == WEB_PICK


def test_the_refresh_never_blocks_the_command(auth, cm, monkeypatch):
    import config_manager as broken

    monkeypatch.setattr(
        broken,
        "get_config_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("config unreadable")),
    )
    ran = []

    @auth.restricted
    async def handler(update, context):
        ran.append(True)

    asyncio.run(handler(_FakeUpdate(CHAT_ID), _FakeContext({})))

    assert ran == [True]
