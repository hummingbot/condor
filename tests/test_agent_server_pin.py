"""The Agent's server pin is editable without hand-writing front matter (FEAT-027).

``AgentStore.update`` already re-rendered the whole front matter and the MCP
tool already exposed ``server_name``; the web layer just had no door to it,
which is why the dashboard could only offer a raw AGENT.md text editor.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents import agent as agent_module
from condor.agents.agent import AgentStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")

REACHABLE = {"local", "prod"}


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def has_server_access(self, user_id, server_name, *a, **k):
        return server_name in REACHABLE


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    AgentStore().create(name="Brigado", description="BRL market making")
    return tmp_path


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def test_the_pin_can_be_set_and_survives_a_reread(env):
    res = _client().patch("/agents/brigado/config", json={"server_name": "prod"})

    assert res.status_code == 200
    assert res.json()["server_name"] == "prod"
    assert AgentStore().get("brigado").server_name == "prod", "front matter re-rendered"


def test_the_pin_can_be_cleared_so_the_agent_follows_the_chat(env):
    client = _client()
    client.patch("/agents/brigado/config", json={"server_name": "prod"})

    res = client.patch("/agents/brigado/config", json={"server_name": ""})

    assert res.status_code == 200
    assert AgentStore().get("brigado").server_name == ""


def test_pinning_to_an_unreachable_server_is_refused(env):
    res = _client().patch(
        "/agents/brigado/config", json={"server_name": "someone-else"}
    )

    assert res.status_code == 403
    assert AgentStore().get("brigado").server_name == "", "nothing was written"


def test_omitted_fields_are_left_alone(env):
    client = _client()
    client.patch("/agents/brigado/config", json={"server_name": "prod"})

    res = client.patch("/agents/brigado/config", json={"server_required": False})

    assert res.status_code == 200
    assert res.json()["server_required"] is False
    agent = AgentStore().get("brigado")
    assert agent.server_name == "prod", "a request about one field left the other"
    assert agent.server_required is False


def test_patching_an_unknown_agent_is_a_404(env):
    res = _client().patch("/agents/nope/config", json={"server_name": "prod"})

    assert res.status_code == 404
