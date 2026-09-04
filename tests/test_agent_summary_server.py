"""``GET /agents`` says which server an agent's records live on (ARCH-324).

``AgentSummary`` carried the *rollup* of what an agent's runs earned but never
the server those runs traded on, and a fold — every record the agent owns, as it
stands now — is computed over a server's records. So the home overview could
roll an agent's runs up and could not fold them, and FEAT-109's last acceptance
criterion ("a row on the home overview shows the same money as the Money
headline") had to ship unmet.

Both halves of the workspace's own rule are pinned here, because the reader on
the other side applies both: the strategy's configured server wins, and the
agent's pin is the fallback. An empty pin stays empty — it means "follow
whichever server the chat is on", and substituting one would have a row fold a
fleet nobody said was the agent's.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents import agent as agent_module
from condor.agents import strategy as strategy_module
from condor.agents.agent import AgentStore
from condor.agents.config import AgentConfig, save_agent_config
from condor.agents.strategy import StrategyStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def has_server_access(self, user_id, server_name, *a, **k):
        return True

    async def get_client(self, server_name):
        raise RuntimeError("no server in this test")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    AgentStore().create(name="Brigado", description="BRL market making")
    return tmp_path


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _brigado() -> dict:
    res = _client().get("/agents")
    assert res.status_code == 200
    return next(a for a in res.json() if a["slug"] == "brigado")


def test_the_summary_carries_the_agents_pin(env):
    store = AgentStore()
    agent = store.get("brigado")
    agent.server_name = "prod"
    store.update(agent)

    assert _brigado()["server_name"] == "prod"


def test_an_unpinned_agent_reports_an_empty_server_not_a_guess(env):
    # Most agents declare no pin at all — it means "follow the chat's server".
    # Reporting a plausible one here would have a reader fold somebody's fleet.
    assert _brigado()["server_name"] == ""


def test_each_strategy_carries_the_server_its_records_were_read_from(env):
    strategy = StrategyStore().create(agent_slug="brigado", name="BRL MM")
    save_agent_config(strategy.home, AgentConfig(server_name="brigado_2"))

    summary = _brigado()
    listed = next(s for s in summary["strategies"] if s["slug"] == "brl_mm")
    assert listed["server_name"] == "brigado_2"


def test_a_strategy_that_declares_no_server_reports_none(env):
    strategy = StrategyStore().create(agent_slug="brigado", name="BRL MM")
    save_agent_config(strategy.home, AgentConfig(server_name=""))

    listed = next(s for s in _brigado()["strategies"] if s["slug"] == "brl_mm")
    assert listed["server_name"] == ""
