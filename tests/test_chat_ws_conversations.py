"""Tests for the chat WS conversation actions (FEAT-015).

The dashboard's whole recovery story runs through these two handlers: a new
chat mints a conversation and *is* keyed on it, and ``resume_conversation``
brings one back after its subprocess is gone. The slot id is the conversation
id, which is what makes ``web:{user}:{conv}`` stable across restarts.
"""

import asyncio
import json

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.runtime.conversations import (
    get_conversation,
    list_conversations,
    read_transcript,
)
from condor.web.routes.chat_ws import _handle_resume_conversation, _handle_start_session

USER = 111


class _FakeWS:
    """Collects what the handler would have sent to the browser."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def events(self, name: str) -> list[dict]:
        return [e for e in self.sent if e.get("event") == name]


class _ScriptedClient:
    script: list = [TextChunk(text="the answer"), PromptDone(stop_reason="end_turn")]

    def __init__(self, **kwargs):
        self.alive = True
        self.prompts: list[str] = []
        type(self).last = self

    async def start(self):
        pass

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
    monkeypatch.setattr(session_module, "ACPClient", _ScriptedClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    return tmp_path


async def _chat(slot_id: str, text: str) -> None:
    key = SessionKey.web(USER, slot_id)
    async for _ in runtime.prompt(key, PromptRequest(text=text)):
        pass


def test_starting_a_chat_mints_a_conversation_and_keys_the_slot_on_it(runtime_env):
    ws = _FakeWS()
    asyncio.run(_handle_start_session(ws, USER, {"agent_key": "claude-code"}))

    started = ws.events("session_started")
    assert len(started) == 1
    slot_id = started[0]["slot_id"]
    assert started[0]["conversation_id"] == slot_id, "the slot IS the conversation"
    assert started[0]["restored"] is False

    metas = list_conversations(USER)
    assert [m.id for m in metas] == [slot_id]
    assert metas[0].surface == "web"


def test_a_web_chat_is_recorded_and_survives_its_session(runtime_env):
    ws = _FakeWS()

    async def scenario():
        await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
        slot_id = ws.events("session_started")[0]["slot_id"]
        await _chat(slot_id, "what is my pnl?")
        # The subprocess dies — a restart, a reap, a budget detach.
        await runtime.destroy(SessionKey.web(USER, slot_id))
        return slot_id

    slot_id = asyncio.run(scenario())

    assert asyncio.run(runtime.get_info(SessionKey.web(USER, slot_id))) is None
    turns = read_transcript(USER, slot_id)
    assert [t.text for t in turns] == ["what is my pnl?", "the answer"]


def test_resume_brings_the_conversation_back_with_its_context(runtime_env):
    ws = _FakeWS()

    async def scenario():
        await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
        slot_id = ws.events("session_started")[0]["slot_id"]
        await _chat(slot_id, "my favourite pair is SOL-USDC")
        await runtime.destroy(SessionKey.web(USER, slot_id))

        resume_ws = _FakeWS()
        await _handle_resume_conversation(resume_ws, USER, {"conversation_id": slot_id})
        # The replay is injected lazily, so it lands on the next prompt rather
        # than blocking the resume on a model round trip.
        await _chat(slot_id, "what was it again?")
        return slot_id, resume_ws, _ScriptedClient.last.prompts

    slot_id, resume_ws, prompts = asyncio.run(scenario())

    restored = resume_ws.events("session_started")
    assert len(restored) == 1
    assert restored[0]["slot_id"] == slot_id, "same key, so the client keeps its slot"
    assert restored[0]["restored"] is True
    # The new subprocess was handed the old transcript.
    assert "SOL-USDC" in prompts[0], "the new brain reads what the old one was told"
    assert "what was it again?" in prompts[0]
    assert asyncio.run(runtime.get_info(SessionKey.web(USER, slot_id))) is not None
    assert len(list_conversations(USER)) == 1, "resumed, not duplicated"
    # And the continuation appends to the same transcript.
    assert len(read_transcript(USER, slot_id)) == 4


def test_resume_reuses_the_conversations_last_model(runtime_env):
    ws = _FakeWS()

    async def scenario():
        await _handle_start_session(ws, USER, {"agent_key": "gemini"})
        slot_id = ws.events("session_started")[0]["slot_id"]
        await runtime.destroy(SessionKey.web(USER, slot_id))

        resume_ws = _FakeWS()
        await _handle_resume_conversation(resume_ws, USER, {"conversation_id": slot_id})
        return resume_ws

    resume_ws = asyncio.run(scenario())
    assert resume_ws.events("session_started")[0]["agent_key"] == "gemini"


def test_resuming_an_unknown_conversation_is_an_error_not_a_new_chat(runtime_env):
    ws = _FakeWS()
    asyncio.run(_handle_resume_conversation(ws, USER, {"conversation_id": "nope"}))

    assert ws.events("session_started") == []
    assert ws.events("error")[0]["message"] == "No such conversation"
    assert list_conversations(USER) == []


def test_resuming_someone_elses_conversation_is_refused(runtime_env):
    other = conversations.new_conversation(999, "web")
    ws = _FakeWS()
    asyncio.run(_handle_resume_conversation(ws, USER, {"conversation_id": other.id}))

    assert ws.events("session_started") == []
    assert ws.events("error")


def test_a_malformed_conversation_id_is_refused_not_a_traversal(runtime_env):
    ws = _FakeWS()
    asyncio.run(_handle_resume_conversation(ws, USER, {"conversation_id": "../../etc"}))

    assert ws.events("session_started") == []
    assert ws.events("error")


def test_an_abandoned_prompt_still_records_its_partial_reply(runtime_env):
    """The reload-mid-answer case, driven through the real generator."""
    ws = _FakeWS()

    async def scenario():
        await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
        slot_id = ws.events("session_started")[0]["slot_id"]

        # Consume one chunk, then walk away — exactly what a page reload or an
        # abort_prompt cancellation does to this generator.
        stream = runtime.prompt(
            SessionKey.web(USER, slot_id), PromptRequest(text="explain the strategy")
        )
        await stream.__anext__()
        await stream.aclose()
        return slot_id

    slot_id = asyncio.run(scenario())

    turns = read_transcript(USER, slot_id)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[1].text == "the answer", "the partial reply is not lost"
    assert get_conversation(USER, slot_id).turn_count == 2
