"""Starting a strategy cannot borrow a server the caller has no access to (SEC-149).

``StartStrategyRequest.config`` is a free-form dict merged straight into the
engine config, and ``TickEngine._resolve_server`` trades on its ``server_name``,
so before the gate any approved user could POST a foreign server name and run a
live loop on someone else's stored API credentials. The sibling routes in the
same file (config pin, consult, delegate) always checked ``has_server_access``;
only the start path did not.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents import agent as agent_module
from condor.agents import engine as engine_module
from condor.agents import strategy as strategy_module
from condor.agents.agent import AgentStore
from condor.agents.strategy import StrategyStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")

MINE = "mine-prod"
THEIRS = "someone-else"
KNOWN = {MINE, THEIRS}


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def get_server(self, server_name):
        return {"name": server_name} if server_name in KNOWN else None

    def has_server_access(self, user_id, server_name, *a, **k):
        return user_id == USER.id and server_name == MINE


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
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    # The server-access guard lives in condor.web.auth (SEC-147), which binds
    # get_config_manager at import time — patch it there too.
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(engine_module, "TickEngine", FakeEngine)
    FakeEngine.spawned = []
    AgentStore().create(name="Brigado", description="BRL market making")
    StrategyStore().create(agent_slug="brigado", name="Scalp")
    return tmp_path


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def test_starting_on_someone_elses_server_is_refused(env):
    res = _client().post(
        "/agents/brigado/strategies/scalp/start",
        json={"config": {"server_name": THEIRS}},
    )

    assert res.status_code == 403
    assert FakeEngine.spawned == [], "no engine spawned on a foreign server"


def test_starting_on_my_own_server_still_works(env):
    res = _client().post(
        "/agents/brigado/strategies/scalp/start",
        json={"config": {"server_name": MINE}},
    )

    assert res.status_code == 200
    assert res.json()["started"] is True
    assert [c["server_name"] for c in FakeEngine.spawned] == [MINE]


def test_starting_without_a_server_keeps_the_accessible_servers_fallback(env):
    """No server was asked for and the inherited default names nothing real."""
    res = _client().post("/agents/brigado/strategies/scalp/start", json={})

    assert res.status_code == 200
    assert len(FakeEngine.spawned) == 1


def test_a_foreign_server_baked_into_the_strategy_default_is_refused_too(env):
    StrategyStore().create(
        agent_slug="brigado", name="Borrowed", default_config={"server_name": THEIRS}
    )

    res = _client().post("/agents/brigado/strategies/borrowed/start", json={})

    assert res.status_code == 403
    assert FakeEngine.spawned == []


def test_the_unnamed_loop_route_is_gated_as_well(env):
    res = _client().post(
        "/agents/brigado/start", json={"config": {"server_name": THEIRS}}
    )

    assert res.status_code == 403
    assert FakeEngine.spawned == []
