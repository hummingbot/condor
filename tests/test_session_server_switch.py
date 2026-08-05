"""Tests for moving a chat to another server, mid-conversation (FEAT-027).

The header chip used to show a server name and nothing else, hiding two
different facts behind one word: a server the chat *inherited* from the
top-right selector, and one the bound Agent *pinned* in its front matter. What
these cover is the difference being on the wire, the switch actually taking
effect (subprocess and conversation both), and the pin winning over anything
the chat asks for.
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

USER = WebUser(id=444, username="u", first_name="U", role="user")

REACHABLE = {"local", "prod"}


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def get_accessible_servers(self, user_id):
        return sorted(REACHABLE)

    def has_server_access(self, user_id, server_name, *a, **k):
        return server_name in REACHABLE


class _Client:
    def __init__(self, **kwargs):
        self.alive = True

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def prompt_stream(self, text):
        yield TextChunk(text="ok")
        yield PromptDone(stop_reason="end_turn")


class _Agent:
    """Ambient: no pin, so it follows whatever the chat is pointed at."""

    slug = "brigado"
    name = "Brigado"
    instructions = ""
    agent_key = ""
    tools: list[str] = []
    server_name = ""
    server_required = True


class _PinnedAgent(_Agent):
    """Pinned: its front matter names the server, and that wins."""

    slug = "vaultkeeper"
    name = "Vaultkeeper"
    server_name = "prod"


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
            return {_Agent.slug: _Agent(), _PinnedAgent.slug: _PinnedAgent()}.get(slug)

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


def _live_session(server: str = "local", agent_slug: str = "") -> tuple[str, str]:
    """A started web session on a fresh conversation. Returns (key, conv_id)."""
    conv = conversations.new_conversation(
        USER.id,
        "web",
        agent_key="claude-code",
        agent_slug=agent_slug,
        server_name=server,
    )
    key = str(SessionKey.web(USER.id, conv.id))
    asyncio.run(
        runtime.create_session(
            SessionSpec(
                key=key,
                agent_key="claude-code",
                user_id=USER.id,
                server_name=server,
                agent_slug=agent_slug,
                conversation_id=conv.id,
                lazy_context=True,
            )
        )
    )
    return key, conv.id


# ── The chip can tell the two cases apart ──


def test_an_ambient_session_reports_its_server_as_unpinned(env):
    key, _ = _live_session()

    info = _client().get(f"/sessions/{key}").json()

    assert info["server_name"] == "local"
    assert (
        info["server_pinned"] is False
    ), "the chat chose it, so the chat may change it"


def test_a_pinned_agent_reports_its_server_as_pinned(env):
    key, _ = _live_session(agent_slug=_PinnedAgent.slug)

    info = _client().get(f"/sessions/{key}").json()

    assert info["server_name"] == "prod", "front matter beat the chat's selection"
    assert info["server_pinned"] is True


# ── Switching an ambient chat ──


def test_switching_the_server_respawns_on_it_and_marks_the_transcript(env):
    key, conv_id = _live_session()

    res = _client().post(
        f"/sessions/{key}/action", json={"action": "switch", "server_name": "prod"}
    )

    assert res.status_code == 200
    session = res.json()["session"]
    assert session["server_name"] == "prod"
    assert session["conversation_id"] == conv_id, "same chat, different account"
    assert [(t.role, t.kind, t.text) for t in read_transcript(USER.id, conv_id)] == [
        ("system", "switch", "Now using server prod")
    ], "tool results either side of this line came from different accounts"


def test_the_switch_survives_a_resume(env):
    key, conv_id = _live_session()

    _client().post(
        f"/sessions/{key}/action", json={"action": "switch", "server_name": "prod"}
    )

    # What a reload reads: chat_ws resumes from the conversation, not from the
    # subprocess it happened to find.
    assert conversations.get_conversation(USER.id, conv_id).server_name == "prod"


def test_switching_to_the_same_server_adds_no_divider(env):
    key, conv_id = _live_session()

    _client().post(
        f"/sessions/{key}/action", json={"action": "switch", "server_name": "local"}
    )

    assert read_transcript(USER.id, conv_id) == [], "nothing moved, nothing to mark"


def test_a_switch_that_names_no_server_keeps_the_current_one(env):
    key, conv_id = _live_session()

    res = _client().post(
        f"/sessions/{key}/action", json={"action": "switch", "agent_key": "gemini"}
    )

    assert res.json()["session"]["server_name"] == "local"
    assert read_transcript(USER.id, conv_id) == []


# ── What a switch may not do ──


def test_a_server_the_user_cannot_reach_is_refused(env):
    key, conv_id = _live_session()

    res = _client().post(
        f"/sessions/{key}/action",
        json={"action": "switch", "server_name": "someone-else"},
    )

    assert res.status_code == 403
    info = _client().get(f"/sessions/{key}").json()
    assert info["server_name"] == "local", "the refused switch left the session alone"
    assert info["alive"] is True, "and did not tear it down on the way out"
    assert read_transcript(USER.id, conv_id) == []


def test_a_pinned_agents_server_is_unchanged_by_the_chat(env):
    key, conv_id = _live_session(agent_slug=_PinnedAgent.slug)

    res = _client().post(
        f"/sessions/{key}/action", json={"action": "switch", "server_name": "local"}
    )

    session = res.json()["session"]
    assert session["server_name"] == "prod", "the pin wins wherever the ask came from"
    assert session["server_pinned"] is True
    assert read_transcript(USER.id, conv_id) == [], "nothing moved, so no divider lies"
