"""The two vectors SEC-149 left behind on agent strategy start (SEC-164).

SEC-149 gated ``POST /agents/{slug}/strategies/{sslug}/start`` on
``has_server_access`` for the server the request names. Two ways to reach a
server that name never covers:

1. **A caller-supplied ``chat_id``.** With no usable ``server_name``,
   ``TickEngine._resolve_server`` fell through to
   ``get_effective_server(self.chat_id)``. ``chat_id`` comes straight from the
   request body, so naming a chat you do not own handed you *that* chat's
   default server — and since no server name ever reached the route, the gate
   had nothing to check. The subject of the resolution is now ``user_id``, the
   run's authenticated principal, exactly as SEC-167 made respawn name the
   caller instead of the session's stored owner.

2. **A name that matches nothing.** The gate checked a body-supplied name and
   let it through, but ``has_server_access`` answers True for an admin on any
   string, so an unknown name started a loop with a server name pinned that
   nothing backed yet — and the loop outlives the check. The route now refuses
   it, and the engine re-checks at every resolution, so a server created later
   under that name is not adopted either.

The refusal order matters: the access check runs first, so a caller with no
access gets the same ``No access`` whether or not the server exists. Only
someone who already cleared access learns the name matched nothing.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents import agent as agent_module
from condor.agents import engine as engine_module
from condor.agents import strategy as strategy_module
from condor.agents.agent import AgentStore
from condor.agents.engine import TickEngine
from condor.agents.strategy import StrategyStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")
ADMIN = WebUser(id=1, username="a", first_name="A", role="admin")
STRANGER_ID = 777

MINE = "mine-prod"
MINE_ALT = "mine-alt"
THEIRS = "someone-else"
UNKNOWN = "not-a-server-yet"

# Every server that exists. ``UNKNOWN`` deliberately is not one of them.
SERVERS = {MINE, MINE_ALT, THEIRS}

# The chat whose default server the attacker is trying to inherit. Its owner is
# not USER, which is the whole point.
VICTIM_CHAT = -100200300


class FakeConfigManager:
    """Mirrors the real access semantics that make both vectors reachable.

    ``has_server_access`` answers True for an admin on *any* name, existing or
    not (``get_server_permission`` returns OWNER for admins before it ever
    looks the server up) — that is what let an unknown name through the gate.
    """

    # Read by the real ``get_effective_server``, which is what resolves a
    # chat's default server.
    _data = {
        "chat_defaults": {VICTIM_CHAT: THEIRS, USER.id: MINE_ALT},
        "servers": {name: {"name": name} for name in SERVERS},
    }

    def is_admin(self, user_id):
        return user_id == ADMIN.id

    def get_server(self, server_name):
        return {"name": server_name} if server_name in SERVERS else None

    def has_server_access(self, user_id, server_name, *a, **k):
        if user_id == ADMIN.id:
            return True
        return user_id == USER.id and server_name in (MINE, MINE_ALT)

    def get_accessible_servers(self, user_id):
        if user_id == ADMIN.id:
            return sorted(SERVERS)
        # Ordered so that ``accessible[0]`` is *not* the user's own default:
        # a test asserting MINE_ALT then proves the preference survived.
        return [MINE, MINE_ALT] if user_id == USER.id else []


@pytest.fixture
def cm(monkeypatch):
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    return FakeConfigManager()


# ----------------------------------------------------------------------
# Vector 1: the engine resolves servers for its subject, never for a chat
# ----------------------------------------------------------------------


class StubEngine:
    """The two real methods under test, without ``TickEngine.__post_init__``."""

    _resolve_server = TickEngine._resolve_server
    _get_client = TickEngine._get_client

    def __init__(self, *, config=None, chat_id=0, user_id=USER.id):
        self.config = config or {}
        self.chat_id = chat_id
        self.user_id = user_id
        self.agent_id = "brigado.scalp_1"


def test_a_foreign_chat_id_does_not_inherit_that_chats_default_server(cm):
    """The body picks the chat; it must not get to pick the credentials.

    Before SEC-164 this returned ``THEIRS`` — the victim chat's default —
    without the start gate ever seeing a server name to check.
    """
    name, server = StubEngine(chat_id=VICTIM_CHAT)._resolve_server()

    assert name != THEIRS, "a supplied chat_id must not license its server"
    assert name == MINE_ALT, "the subject's own default is what counts"
    assert server == {"name": MINE_ALT}


def test_the_subjects_own_default_server_is_still_honoured(cm):
    """Dropping chat_id must not flatten everyone onto ``accessible[0]``."""
    name, _ = StubEngine(chat_id=0)._resolve_server()

    assert name == MINE_ALT


def test_a_subject_with_no_default_still_falls_back_to_accessible_servers(cm):
    """The fallback SEC-149 was careful to preserve keeps working."""
    fake = FakeConfigManager()
    fake._data = {"chat_defaults": {}, "servers": FakeConfigManager._data["servers"]}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("config_manager.get_config_manager", lambda: fake)
        name, _ = StubEngine(config={"server_name": "local"})._resolve_server()

    assert name == MINE


def test_a_configured_server_the_subject_cannot_reach_is_dropped(cm):
    """The engine no longer trusts ``config['server_name']`` on its own."""
    name, _ = StubEngine(config={"server_name": THEIRS})._resolve_server()

    assert name == MINE_ALT


def test_a_server_created_later_under_the_configured_name_is_not_adopted(cm):
    """Vector 2, engine side: the check runs at resolution, not only at start.

    ``local`` named nothing when the loop started; here it exists and belongs
    to someone else. The already-running strategy must not pick it up.
    """
    servers = dict(FakeConfigManager._data["servers"])
    servers["local"] = {"name": "local"}
    fake = FakeConfigManager()
    fake._data = {"chat_defaults": {}, "servers": servers}
    fake.get_server = lambda n: servers.get(n)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("config_manager.get_config_manager", lambda: fake)
        name, _ = StubEngine(config={"server_name": "local"})._resolve_server()

    assert name != "local", "a foreign server appearing later is not inherited"
    assert name == MINE


def test_a_subject_with_no_accessible_server_gets_no_client_at_all(cm, monkeypatch):
    """Not the first globally enabled one.

    ``_get_client`` used to fall back to ``get_bots_client(self.chat_id)``,
    which ignores the chat id for server selection and, with no ``user_data``
    to scope it, hands back the first enabled server in the config.
    """
    called = []

    async def _spy(chat_id=None, user_data=None):
        called.append(chat_id)
        return object(), THEIRS

    monkeypatch.setattr("handlers.bots._shared.get_bots_client", _spy)

    engine = StubEngine(chat_id=VICTIM_CHAT, user_id=STRANGER_ID)

    assert engine._resolve_server() == (None, None)
    assert asyncio.run(engine._get_client()) is None
    assert called == [], "no chat-keyed client fallback may run"


# ----------------------------------------------------------------------
# Vector 2: the start gate refuses a name that matches nothing
# ----------------------------------------------------------------------


class FakeEngine:
    """Stands in for TickEngine: records every spawn, never touches the network."""

    spawned: list[dict] = []

    def __init__(self, agent, strategy, config, chat_id, user_id):
        self.config = config
        self.agent_id = "agent-1"
        self.session_num = 1
        FakeEngine.spawned.append(config)

    async def start(self):
        return None


@pytest.fixture
def env(tmp_path, monkeypatch, cm):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(engine_module, "TickEngine", FakeEngine)
    FakeEngine.spawned = []
    AgentStore().create(name="Brigado", description="BRL market making")
    StrategyStore().create(agent_slug="brigado", name="Scalp")
    return tmp_path


def _client(user: WebUser) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _start(user: WebUser, **config):
    return _client(user).post(
        "/agents/brigado/strategies/scalp/start",
        json={"config": config} if config else {},
    )


def test_an_unknown_server_name_is_refused(env):
    """An admin clears the access check on any string; existence still must hold."""
    res = _start(ADMIN, server_name=UNKNOWN)

    assert res.status_code == 404
    assert res.json()["detail"] == "Server not found"
    assert FakeEngine.spawned == [], "no loop starts pinned to a name nothing backs"


def test_the_refusal_tells_a_stranger_nothing_about_which_names_exist(env):
    """Same 403 for a real foreign server and for a name that matches nothing."""
    unknown = _start(USER, server_name=UNKNOWN)
    foreign = _start(USER, server_name=THEIRS)

    assert unknown.status_code == foreign.status_code == 403
    assert unknown.json()["detail"] == foreign.json()["detail"] == "No access"
    assert FakeEngine.spawned == []


def test_an_inherited_name_that_matches_nothing_still_falls_through(env):
    """The ``local`` AgentConfig fills in is not a request to use ``local``.

    Only a name the *body* asked for is held to existence; the inherited
    default keeps the accessible-servers fallback SEC-149 preserved.
    """
    res = _start(ADMIN)

    assert res.status_code == 200
    assert len(FakeEngine.spawned) == 1


def test_starting_on_a_server_the_caller_owns_still_works(env):
    """Sanity: the gate is not simply refusing everyone."""
    res = _start(USER, server_name=MINE)

    assert res.status_code == 200
    assert [c["server_name"] for c in FakeEngine.spawned] == [MINE]
