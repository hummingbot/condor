"""Sending into a slot whose session is gone reattaches it (CORR-228).

The runtime is allowed to take a session away from an open tab — the budget
does exactly that when it detaches the least recently used idle one, and says
the conversation is kept. These tests pin that the chat behaves like it was
kept: the message the user typed is answered by a respawned session on the same
conversation, and "Session ended. Start a new one." is left for the one case
where there really is nothing to resume.
"""

import asyncio
import json

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.runtime.conversations import read_transcript
from condor.web.routes import chat_ws
from condor.web.routes.chat_ws import _handle_send_message, _handle_start_session

USER = 333


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def events(self, name: str) -> list[dict]:
        return [e for e in self.sent if e.get("event") == name]

    def names(self) -> list[str]:
        return [e.get("event") for e in self.sent]


class _ScriptedClient:
    def __init__(self, **kwargs):
        self.alive = True
        self.prompts: list[str] = []
        type(self).spawns = getattr(type(self), "spawns", 0) + 1
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
        yield TextChunk(text="the answer")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _ScriptedClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_pending_spawns", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    _ScriptedClient.spawns = 0
    return chat_ws


async def _chat(slot_id: str, text: str) -> None:
    async for _ in runtime.prompt(
        SessionKey.web(USER, slot_id), PromptRequest(text=text)
    ):
        pass


def test_a_detached_slot_reattaches_and_answers_the_message(ws_env):
    """The eviction case: the session is taken away, the conversation is not."""
    ws = _FakeWS()

    async def scenario():
        await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
        slot_id = ws.events("session_started")[0]["slot_id"]
        await _chat(slot_id, "my favourite pair is SOL-USDC")
        # The slot loses its subprocess, and the runtime keeps no memory of it
        # at all — the harshest version of what the budget does to an idle
        # victim, and the one a bot restart leaves behind.
        await runtime.destroy(SessionKey.web(USER, slot_id))
        assert await runtime.get_info(SessionKey.web(USER, slot_id)) is None

        after = _FakeWS()
        await _handle_send_message(
            after, USER, {"slot_id": slot_id, "text": "what was it?"}
        )
        return slot_id, after

    slot_id, after = asyncio.run(scenario())

    assert "error" not in after.names(), after.sent
    assert "session_destroyed" not in after.names()
    # The slot came back on the same conversation, announced as a resume.
    restarted = after.events("session_started")
    assert len(restarted) == 1
    assert restarted[0]["slot_id"] == slot_id
    assert restarted[0]["restored"] is True
    # ...and the message the user typed was answered, not dropped.
    assert [e["text"] for e in after.events("text_chunk")] == ["the answer"]
    assert after.events("prompt_done")[-1]["stop_reason"] == "end_turn"
    # Two subprocesses over the slot's life: the original and the reattach.
    assert _ScriptedClient.spawns == 2

    # The transcript is one conversation, not two.
    turns = read_transcript(USER, slot_id)
    assert [t.text for t in turns] == [
        "my favourite pair is SOL-USDC",
        "the answer",
        "what was it?",
        "the answer",
    ]


def test_a_dead_session_reattaches_too(ws_env):
    """A crashed subprocess leaves the same durable conversation behind."""
    ws = _FakeWS()

    async def scenario():
        await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
        slot_id = ws.events("session_started")[0]["slot_id"]
        await _chat(slot_id, "hello")
        # Registered but not alive: the health monitor has not swept it yet.
        _ScriptedClient.last.alive = False

        after = _FakeWS()
        await _handle_send_message(
            after, USER, {"slot_id": slot_id, "text": "still there?"}
        )
        return slot_id, after

    slot_id, after = asyncio.run(scenario())

    assert "error" not in after.names(), after.sent
    assert after.events("session_started")[0]["slot_id"] == slot_id
    assert [e["text"] for e in after.events("text_chunk")] == ["the answer"]


def test_an_unknown_slot_is_still_told_to_start_a_new_chat(ws_env):
    """Nothing to resume is the one case that keeps the dead end."""
    after = _FakeWS()
    asyncio.run(
        _handle_send_message(
            after, USER, {"slot_id": "no-such-conversation", "text": "hi"}
        )
    )

    assert [e["message"] for e in after.events("error")] == [
        "Session ended. Start a new one."
    ]
    assert after.events("session_destroyed")[0]["had_session"] is True
    assert not after.events("session_started")
    assert _ScriptedClient.spawns == 0


def test_a_live_slot_does_not_respawn(ws_env):
    """The reattach is only for a slot with no session — a warm one is untouched."""
    ws = _FakeWS()

    async def scenario():
        await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
        slot_id = ws.events("session_started")[0]["slot_id"]
        after = _FakeWS()
        await _handle_send_message(after, USER, {"slot_id": slot_id, "text": "hi"})
        return after

    after = asyncio.run(scenario())

    assert not after.events("session_started"), "a live slot must not be respawned"
    assert [e["text"] for e in after.events("text_chunk")] == ["the answer"]
    assert _ScriptedClient.spawns == 1


def test_an_idle_reap_reattaches_when_the_tab_speaks_again(ws_env, monkeypatch):
    """The TTL (PERF-226) composes with this reattach instead of dead-ending.

    An idle detach is the eviction case made routine: the tab is still open,
    the session behind it is gone, and the very next message has to bring it
    back. This drives the real health sweep rather than calling ``destroy``,
    so the two halves are pinned together and not just side by side.
    """
    from dataclasses import replace
    from datetime import timedelta

    monkeypatch.setattr(
        session_module,
        "TIMEOUTS",
        replace(session_module.TIMEOUTS, session_idle=3600),
    )

    async def scenario():
        ws = _FakeWS()
        await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
        slot_id = ws.events("session_started")[0]["slot_id"]
        await _chat(slot_id, "my favourite pair is SOL-USDC")

        session = session_module._sessions[str(SessionKey.web(USER, slot_id))]
        session.last_prompt_at = session_module._utcnow() - timedelta(seconds=3601)
        await session_module._sweep_sessions()
        assert await runtime.get_info(SessionKey.web(USER, slot_id)) is None

        after = _FakeWS()
        await _handle_send_message(
            after, USER, {"slot_id": slot_id, "text": "what was it?"}
        )
        return slot_id, after

    slot_id, after = asyncio.run(scenario())

    assert "error" not in after.names(), after.sent
    assert after.events("session_started")[0]["restored"] is True
    assert [e["text"] for e in after.events("text_chunk")] == ["the answer"]
    # One conversation across the reap, so the user lost no context.
    assert [t.text for t in read_transcript(USER, slot_id)] == [
        "my favourite pair is SOL-USDC",
        "the answer",
        "what was it?",
        "the answer",
    ]
