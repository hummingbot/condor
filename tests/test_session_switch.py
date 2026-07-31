"""Tests for switching who is answering, mid-conversation (FEAT-016).

The endpoint shipped with FEAT-011 and had no caller. What this covers is the
half the dashboard depends on: the transcript survives the handover, the switch
is *visible* in it, and a switch that could not spawn leaves no divider claiming
it happened.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.runtime.conversations import read_transcript
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import sessions as routes

USER = WebUser(id=333, username="u", first_name="U", role="user")


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def get_accessible_servers(self, user_id):
        return []


class _Client:
    def __init__(self, **kwargs):
        self.alive = True
        self.stop_calls = 0

    async def start(self):
        pass

    async def stop(self):
        self.stop_calls += 1
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def prompt_stream(self, text):
        yield TextChunk(text="ok")
        yield PromptDone(stop_reason="end_turn")


class _Agent:
    slug = "brigado"
    name = "Brigado"
    instructions = ""
    agent_key = ""
    tools: list[str] = []
    server_name = ""
    server_required = False


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _Client)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(routes, "get_config_manager", lambda: FakeConfigManager())
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})

    class _Store:
        def get(self, slug):
            return _Agent() if slug == _Agent.slug else None

    monkeypatch.setattr("condor.agents.agent.AgentStore", _Store)
    monkeypatch.setattr(
        "condor.runtime.binding.agent_identity_context", lambda *a, **k: ""
    )
    return tmp_path


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _live_session() -> tuple[str, str]:
    """A started web session on a fresh conversation. Returns (key, conv_id)."""
    conv = conversations.new_conversation(USER.id, "web", agent_key="claude-code")
    key = str(SessionKey.web(USER.id, conv.id))
    asyncio.run(
        runtime.create_session(
            SessionSpec(
                key=key,
                agent_key="claude-code",
                user_id=USER.id,
                conversation_id=conv.id,
                lazy_context=True,
            )
        )
    )
    return key, conv.id


def test_switching_to_an_agent_keeps_the_conversation_and_marks_the_handover(env):
    key, conv_id = _live_session()

    res = _client().post(
        f"/sessions/{key}/action", json={"action": "switch", "agent_slug": "brigado"}
    )

    assert res.status_code == 200
    session = res.json()["session"]
    assert session["agent_slug"] == "brigado"
    assert session["label"] == "Brigado"
    assert session["conversation_id"] == conv_id, "same chat, different brain"

    turns = read_transcript(USER.id, conv_id)
    assert [(t.role, t.kind, t.text) for t in turns] == [
        ("system", "switch", "Switched to Brigado")
    ], "the divider names the Agent as the header does"


def test_switching_only_the_model_keeps_the_bound_agent_and_adds_no_divider(env):
    key, conv_id = _live_session()
    client = _client()
    client.post(
        f"/sessions/{key}/action", json={"action": "switch", "agent_slug": "brigado"}
    )

    res = client.post(
        f"/sessions/{key}/action", json={"action": "switch", "agent_key": "gemini"}
    )

    session = res.json()["session"]
    assert session["agent_slug"] == "brigado", "the model changed, not who is answering"
    assert session["agent_key"] == "gemini"
    assert len(read_transcript(USER.id, conv_id)) == 1, "only the first switch divides"


def test_unbinding_back_to_the_assistant_is_also_a_handover(env):
    key, conv_id = _live_session()
    client = _client()
    client.post(
        f"/sessions/{key}/action", json={"action": "switch", "agent_slug": "brigado"}
    )

    res = client.post(
        f"/sessions/{key}/action", json={"action": "switch", "agent_slug": ""}
    )

    assert res.json()["session"]["agent_slug"] == ""
    assert [t.text for t in read_transcript(USER.id, conv_id)] == [
        "Switched to Brigado",
        "Switched to Condor",
    ]


def test_a_switch_that_cannot_spawn_leaves_no_divider(env):
    key, conv_id = _live_session()

    res = _client().post(
        f"/sessions/{key}/action", json={"action": "switch", "agent_slug": "nope"}
    )

    assert res.status_code == 404
    assert read_transcript(USER.id, conv_id) == [], "no handover was recorded"
