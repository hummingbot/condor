"""Tests for the warm-session gaps in the chat WS (FEAT-016).

Two things make a dashboard chat feel ready the moment it is opened: a session
that can be born already bound to a domain Agent (one spawn, not start-then-
switch), and a ``send_message`` that waits for an in-flight spawn instead of
reading the not-yet-registered session as dead.
"""

import asyncio
import json

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import SessionKey, conversations
from condor.runtime import sessions as session_module
from condor.runtime.conversations import get_conversation, read_transcript
from condor.web.routes import chat_ws
from condor.web.routes.chat_ws import (
    _handle_resume_conversation,
    _handle_send_message,
    _handle_start_session,
)

USER = 222


class _FakeWS:
    """Collects what the handler would have sent to the browser."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def events(self, name: str) -> list[dict]:
        return [e for e in self.sent if e.get("event") == name]


class _SlowClient:
    """An ACP client whose ``start()`` takes a scheduler turn or two."""

    start_delay = 0.05
    script: list = [TextChunk(text="the answer"), PromptDone(stop_reason="end_turn")]

    def __init__(self, **kwargs):
        self.alive = False
        self.prompts: list[str] = []
        type(self).last = self

    async def start(self):
        await asyncio.sleep(type(self).start_delay)
        self.alive = True

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        self.prompts.append(text)
        return "ok"

    async def prompt_stream(self, text):
        self.prompts.append(text)
        for event in type(self).script:
            yield event


@pytest.fixture
def runtime_env(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _SlowClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(chat_ws, "_pending_spawns", {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    return tmp_path


@pytest.fixture
def bound_agent(monkeypatch):
    """A domain Agent the runtime's binding layer will resolve."""

    class _Agent:
        slug = "brigado"
        name = "Brigado"
        instructions = "You watch the BRL desk."
        agent_key = ""
        tools: list[str] = []
        server_name = ""
        server_required = False

    class _Store:
        def get(self, slug):
            return _Agent() if slug == _Agent.slug else None

    monkeypatch.setattr("condor.agents.agent.AgentStore", _Store)
    monkeypatch.setattr(
        "condor.runtime.binding.agent_identity_context", lambda *a, **k: ""
    )
    return _Agent


def test_a_chat_can_be_born_bound_to_an_agent(runtime_env, bound_agent):
    """The Chat button on an agent's page costs exactly one spawn."""
    ws = _FakeWS()
    asyncio.run(
        _handle_start_session(
            ws, USER, {"agent_key": "claude-code", "agent_slug": "brigado"}
        )
    )

    started = ws.events("session_started")
    assert len(started) == 1
    assert started[0]["agent_slug"] == "brigado"
    assert started[0]["label"] == "Brigado", "the header names the Agent, not the model"

    # And the binding is on the conversation, so resuming it later comes back
    # as the same brain rather than as the plain assistant.
    assert get_conversation(USER, started[0]["slot_id"]).agent_slug == "brigado"


def test_an_unbound_chat_is_still_the_plain_assistant(runtime_env):
    ws = _FakeWS()
    asyncio.run(_handle_start_session(ws, USER, {"agent_key": "claude-code"}))

    started = ws.events("session_started")[0]
    assert started["agent_slug"] == ""
    assert started["label"] == "Condor"


def test_a_message_sent_mid_spawn_waits_for_it(runtime_env):
    """The whole point of the warm session: typing through the spawn works."""
    ws = _FakeWS()

    async def scenario():
        conv = conversations.new_conversation(USER, "web", agent_key="claude-code")
        resume = asyncio.create_task(
            _handle_resume_conversation(ws, USER, {"conversation_id": conv.id})
        )
        # Let the resume register its spawn, but not finish it.
        await asyncio.sleep(0)
        assert not resume.done(), "the spawn must still be in flight for this test"

        await _handle_send_message(ws, USER, {"slot_id": conv.id, "text": "hello?"})
        await resume
        return conv.id

    conv_id = asyncio.run(scenario())

    assert ws.events("error") == [], "no 'Session ended' for a message that raced"
    assert [e["text"] for e in ws.events("text_chunk")] == ["the answer"]
    assert [t.text for t in read_transcript(USER, conv_id)] == ["hello?", "the answer"]


def test_a_message_to_a_slot_with_no_spawn_still_reports_the_dead_session(runtime_env):
    """The wait is a grace period, not a way to swallow a real error."""
    ws = _FakeWS()
    asyncio.run(_handle_send_message(ws, USER, {"slot_id": "ghost", "text": "hi"}))

    assert ws.events("error")[0]["message"] == "Session ended. Start a new one."
    assert ws.events("session_destroyed")


def test_a_client_ref_comes_back_so_an_optimistic_tab_can_be_reconciled(runtime_env):
    ws = _FakeWS()
    asyncio.run(
        _handle_start_session(
            ws, USER, {"agent_key": "claude-code", "client_ref": "n1"}
        )
    )

    started = ws.events("session_started")[0]
    assert started["client_ref"] == "n1"
    assert started["slot_id"] != "n1", "the real id is the conversation's"


def test_a_failed_spawn_names_the_tab_it_could_not_fill(runtime_env, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("no such model")

    monkeypatch.setattr(chat_ws.runtime, "create_session", _boom)
    ws = _FakeWS()
    asyncio.run(
        _handle_start_session(
            ws, USER, {"agent_key": "claude-code", "client_ref": "n1"}
        )
    )

    err = ws.events("error")[0]
    assert err["client_ref"] == "n1"
    assert "no such model" in err["message"]
    assert chat_ws._pending_spawns == {}, "a failed spawn is not left pending forever"


def test_the_pending_spawn_is_cleared_once_the_session_is_up(runtime_env):
    ws = _FakeWS()
    asyncio.run(_handle_start_session(ws, USER, {"agent_key": "claude-code"}))

    assert chat_ws._pending_spawns == {}
    slot_id = ws.events("session_started")[0]["slot_id"]
    assert asyncio.run(chat_ws.runtime.get_info(SessionKey.web(USER, slot_id)))
