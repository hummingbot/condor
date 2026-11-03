"""A conversation reaped while the socket was down keeps its tab (CORR-265).

The roster the dashboard rebuilds its tab strip from used to list only the live
subprocesses, so a laptop that slept long enough for the idle sweep to run came
back to a chat that had vanished from the screen — transcript intact on disk,
nothing on the page pointing at it. CORR-258's reconnect resync could not reach
it either: it re-reads the slots the roster *lists*, and there was no entry left
to re-read.

These pin the distinction the fix turns on. A slot the runtime **reaped** is
still the user's conversation and is listed, with ``alive`` false, so the tab
and its messages survive and the next message reattaches. A slot the user
**destroyed** is over, and stays gone from the roster — the no-cross-talk
property ``_get_user_sessions`` is built on.
"""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.web.routes import chat_ws
from condor.web.routes.chat_ws import (
    _get_user_sessions,
    _handle_destroy_session,
    _handle_send_message,
    _handle_start_session,
)

USER = 606
TTL = 3600

# Every key the shipped dashboard already reads off a roster entry. The wire
# shape is a live contract: this list may gain members, never lose or rename
# one, or a bundle in somebody's open tab stops understanding the answer.
SHIPPED_KEYS = {
    "slot_id",
    "conversation_id",
    "agent_key",
    "is_busy",
    "server_name",
    "server_pinned",
    "agent_slug",
    "label",
    "last_prompt_at",
}


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
        type(self).spawns = getattr(type(self), "spawns", 0) + 1

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def prompt_stream(self, text):
        yield TextChunk(text="the answer")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def ws_env(monkeypatch):
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "_detached", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _ScriptedClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_pending_spawns", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    monkeypatch.setattr(
        session_module,
        "TIMEOUTS",
        replace(session_module.TIMEOUTS, session_idle=TTL),
    )
    _ScriptedClient.spawns = 0
    return chat_ws


async def _open_chat(ws: _FakeWS) -> str:
    """Start a session and say something in it. Returns the slot id."""
    await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
    slot_id = ws.events("session_started")[0]["slot_id"]
    async for _ in runtime.prompt(
        SessionKey.web(USER, slot_id),
        PromptRequest(text="my favourite pair is SOL-USDC"),
    ):
        pass
    return slot_id


async def _reap(slot_id: str) -> None:
    """Let the idle sweep take the subprocess, exactly as a slept laptop would."""
    session = session_module._sessions[str(SessionKey.web(USER, slot_id))]
    session.last_prompt_at = session_module._utcnow() - timedelta(seconds=TTL * 96)
    await session_module._sweep_sessions()
    assert await runtime.get_info(SessionKey.web(USER, slot_id)) is None


def _slot(roster: list[dict], slot_id: str) -> dict | None:
    return next((s for s in roster if s["slot_id"] == slot_id), None)


def test_a_reaped_slot_is_still_on_the_roster(ws_env):
    """The hole CORR-258's resync could not reach: an entry to re-read from."""

    async def scenario():
        ws = _FakeWS()
        slot_id = await _open_chat(ws)
        await _reap(slot_id)
        return slot_id, await _get_user_sessions(USER)

    slot_id, roster = asyncio.run(scenario())

    entry = _slot(roster, slot_id)
    assert entry is not None, "the reaped conversation fell off the roster"
    assert entry["alive"] is False
    # ...described well enough to render the tab it belongs to, unchanged.
    assert entry["conversation_id"] == slot_id
    assert entry["agent_key"] == "claude-code"
    assert entry["is_busy"] is False
    assert entry["last_prompt_at"] is not None


def test_the_narrow_list_still_means_what_is_running(ws_env):
    """Retention widens one question's answer, not every caller's.

    The REST session list and the teardown that walks live sessions ask about
    subprocesses; only the frontend roster asks about slots.
    """

    async def scenario():
        ws = _FakeWS()
        slot_id = await _open_chat(ws)
        await _reap(slot_id)
        return (
            await runtime.list_sessions(USER),
            await runtime.list_sessions(USER, include_detached=True),
        )

    running, everything = asyncio.run(scenario())

    assert running == []
    assert [i.alive for i in everything] == [False]


def test_sending_into_a_reaped_tab_resumes_it(ws_env):
    """One conversation continued, not a second one started beside it."""

    async def scenario():
        ws = _FakeWS()
        slot_id = await _open_chat(ws)
        await _reap(slot_id)

        after = _FakeWS()
        await _handle_send_message(
            after, USER, {"slot_id": slot_id, "text": "what was it?"}
        )
        return slot_id, after, await _get_user_sessions(USER)

    slot_id, after, roster = asyncio.run(scenario())

    assert "error" not in after.names(), after.sent
    restarted = after.events("session_started")
    assert [e["slot_id"] for e in restarted] == [slot_id]
    assert restarted[0]["restored"] is True
    assert [e["text"] for e in after.events("text_chunk")] == ["the answer"]
    # The transcript is one conversation the whole way through.
    turns = conversations.read_transcript(USER, slot_id)
    assert [t.text for t in turns] == [
        "my favourite pair is SOL-USDC",
        "the answer",
        "what was it?",
        "the answer",
    ]
    # And the roster says the slot is running again — listed once, not twice.
    assert [s["slot_id"] for s in roster] == [slot_id]
    assert _slot(roster, slot_id)["alive"] is True


def test_a_destroyed_slot_disappears(ws_env):
    """Destroying is the user saying the chat is over; it leaves no memory.

    Both orders matter: closing a live tab, and closing one that was already
    reaped — a remembered slot that outlived its own destruction would come
    back as a tab the user had explicitly shut.
    """

    async def scenario():
        ws = _FakeWS()
        live = await _open_chat(ws)
        reaped = await _open_chat(_FakeWS())
        await _reap(reaped)

        await _handle_destroy_session(_FakeWS(), USER, {"slot_id": live})
        await _handle_destroy_session(_FakeWS(), USER, {"slot_id": reaped})
        return await _get_user_sessions(USER)

    assert asyncio.run(scenario()) == []


def test_a_deleted_conversation_takes_its_reaped_slot_with_it(ws_env):
    """Nothing to resume is nothing to list, whoever did the deleting."""

    async def scenario():
        ws = _FakeWS()
        slot_id = await _open_chat(ws)
        await _reap(slot_id)
        assert conversations.delete_conversation(USER, slot_id) is True
        return await _get_user_sessions(USER)

    assert asyncio.run(scenario()) == []


def test_the_roster_only_gained_a_key(ws_env):
    """An older bundle still reads every field it knows, unchanged in meaning."""

    async def scenario():
        ws = _FakeWS()
        await _open_chat(ws)
        return await _get_user_sessions(USER)

    entry = asyncio.run(scenario())[0]

    assert SHIPPED_KEYS <= set(entry), "a key the dashboard reads went missing"
    assert set(entry) - SHIPPED_KEYS == {"alive"}


def test_only_so_many_reaped_slots_are_remembered(ws_env):
    """The memory is bounded, or a long-lived process grows a tab strip."""

    async def scenario():
        slots = []
        for _ in range(session_module.MAX_DETACHED_PER_USER + 2):
            slot_id = await _open_chat(_FakeWS())
            slots.append(slot_id)
            await _reap(slot_id)
        return slots, await _get_user_sessions(USER)

    slots, roster = asyncio.run(scenario())

    listed = {s["slot_id"] for s in roster}
    assert len(listed) == session_module.MAX_DETACHED_PER_USER
    # The oldest go first: what you were last in is what you want back.
    assert listed == set(slots[-session_module.MAX_DETACHED_PER_USER :])
