"""A woken turn streams into an open dashboard tab (FEAT-034, slice 4).

The point of the web sink is that there is nothing new on the wire: a turn
started by a finished background task produces exactly the frames a typed turn
produces, so the shipped dashboard renders it without knowing it happened. These
tests drive both and compare.
"""

import asyncio
import json

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import SessionKey, conversations
from condor.runtime import sessions as session_module
from condor.runtime import wake
from condor.web.routes import chat_ws
from condor.web.routes.chat_ws import _handle_send_message, _handle_start_session

USER = 333


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def names(self) -> list[str]:
        return [e.get("event") for e in self.sent]


class _EchoClient:
    def __init__(self, **kwargs):
        self.alive = True
        self.prompts: list[str] = []
        type(self).last = self

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def abort_prompt(self):
        pass

    async def prompt_stream(self, text):
        self.prompts.append(text)
        yield TextChunk(text="on it")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _EchoClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    monkeypatch.setattr(chat_ws, "_attached_sockets", {})
    monkeypatch.setattr(wake, "_in_flight", {})
    return chat_ws


async def _start(ws: _FakeWS) -> str:
    await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
    return [e for e in ws.sent if e.get("event") == "session_started"][0]["slot_id"]


def _attach(ws: _FakeWS) -> None:
    """What ``chat_websocket()`` does around its receive loop."""
    chat_ws._attached_sockets.setdefault(USER, set()).add(ws)


def test_a_woken_turn_produces_the_frames_a_typed_turn_produces(ws_env):
    async def scenario():
        typed_ws = _FakeWS()
        slot_id = await _start(typed_ws)
        before = len(typed_ws.sent)
        await _handle_send_message(typed_ws, USER, {"slot_id": slot_id, "text": "hi"})
        typed_frames = typed_ws.sent[before:]

        woken_ws = _FakeWS()
        _attach(woken_ws)
        woke = await wake.resume_session(
            session_key=str(SessionKey.web(USER, slot_id)),
            conversation_id=slot_id,
            text="[background task complete] 3 pools",
            kind="resume",
        )
        return typed_frames, woken_ws.sent, woke, slot_id

    typed_frames, woken_frames, woke, slot_id = asyncio.run(scenario())

    assert woke is True
    assert woken_frames == typed_frames
    assert [f["event"] for f in woken_frames] == ["text_chunk", "prompt_done"]
    # Addressed like every other chat event, so the client routes it into the
    # conversation it belongs to rather than into whichever is on screen.
    assert {f["slot_id"] for f in woken_frames} == {slot_id}


def test_every_open_tab_sees_the_woken_turn(ws_env):
    async def scenario():
        opener = _FakeWS()
        slot_id = await _start(opener)
        first, second = _FakeWS(), _FakeWS()
        _attach(first)
        _attach(second)
        await wake.resume_session(
            session_key=str(SessionKey.web(USER, slot_id)),
            conversation_id=slot_id,
            text="done",
            kind="resume",
        )
        return first, second

    first, second = asyncio.run(scenario())

    assert first.names() == ["text_chunk", "prompt_done"]
    assert second.sent == first.sent


def test_a_closed_tab_does_not_stop_the_turn(ws_env):
    """No sink is not a failure: the turn runs, and the dashboard picks it up
    from the transcript when the user comes back."""

    async def scenario():
        opener = _FakeWS()
        slot_id = await _start(opener)
        client = _EchoClient.last
        # Nobody attached — the state after the last tab closed.
        woke = await wake.resume_session(
            session_key=str(SessionKey.web(USER, slot_id)),
            conversation_id=slot_id,
            text="done",
            kind="resume",
        )
        return client, woke, slot_id

    client, woke, slot_id = asyncio.run(scenario())

    assert woke is True
    assert client.prompts == ["done"]
    turns = conversations.read_transcript(USER, slot_id, limit=0)
    assert [(t.role, t.kind) for t in turns] == [
        ("system", "resume"),
        ("assistant", ""),
    ]


def test_a_note_reaches_every_open_tab_without_prompting_the_session(ws_env):
    """The cheap delivery: shown, not answered.

    A finished routine's outcome is text the transcript already holds, so
    ``deliver_note`` must reach the tabs without spending a model turn — which
    is what makes it safe to fire on every run.
    """

    async def scenario():
        opener = _FakeWS()
        slot_id = await _start(opener)
        client = _EchoClient.last
        first, second = _FakeWS(), _FakeWS()
        _attach(first)
        _attach(second)
        shown = await wake.deliver_note(
            session_key=str(SessionKey.web(USER, slot_id)),
            conversation_id=slot_id,
            text="❌ Routine backtest_chart failed: HTTP 429",
            kind="routine",
        )
        return client, shown, first, second, slot_id

    client, shown, first, second, slot_id = asyncio.run(scenario())

    assert shown is True
    assert client.prompts == []
    assert first.sent == [
        {
            "event": "system_note",
            "slot_id": slot_id,
            "text": "❌ Routine backtest_chart failed: HTTP 429",
            "kind": "routine",
        }
    ]
    assert second.sent == first.sent


def test_a_note_for_a_conversation_the_session_left_is_not_shown(ws_env):
    """Same guard as a wake: a run started before ``/new`` must not report into
    a chat about something else."""

    async def scenario():
        opener = _FakeWS()
        slot_id = await _start(opener)
        watcher = _FakeWS()
        _attach(watcher)
        moved = await wake.deliver_note(
            session_key=str(SessionKey.web(USER, slot_id)),
            conversation_id="some-older-conversation",
            text="done",
            kind="routine",
        )
        gone = await wake.deliver_note(
            session_key=str(SessionKey.web(USER, "no-such-slot")),
            conversation_id="no-such-slot",
            text="done",
            kind="routine",
        )
        return moved, gone, watcher

    moved, gone, watcher = asyncio.run(scenario())

    assert (moved, gone) == (False, False)
    assert watcher.sent == []


def test_the_sink_is_only_offered_while_a_socket_is_attached(ws_env):
    key = SessionKey.web(USER, "conv-1")
    assert chat_ws._wake_sink(key, USER) is None

    ws = _FakeWS()
    _attach(ws)
    assert chat_ws._wake_sink(key, USER) is not None
    # An unauthenticated/unknown owner never resolves to someone else's tabs.
    assert chat_ws._wake_sink(key, None) is None


def test_a_note_still_reaches_the_tab_after_the_session_is_reaped(ws_env):
    """The gate CORR-263 removed: the tab is open, the note was not shown.

    A note is not a turn — it needs a socket, not a subprocess — but it used to
    require the originating session to still be registered and alive. The idle
    reaper, the per-user cap and a client that simply died all leave the tab on
    screen with the socket still attached, which is why the bell for the very
    same event arrived while the chat note did not.
    """

    async def scenario():
        opener = _FakeWS()
        slot_id = await _start(opener)
        watcher = _FakeWS()
        _attach(watcher)
        # What the idle reaper leaves behind: socket attached, session gone.
        session_module._sessions.clear()
        reaped = await wake.deliver_note(
            session_key=str(SessionKey.web(USER, slot_id)),
            conversation_id=slot_id,
            text="✅ scout finished: 3 pools",
            kind="delegation",
            user_id=USER,
        )
        return reaped, watcher, slot_id

    reaped, watcher, slot_id = asyncio.run(scenario())

    assert reaped is True
    assert watcher.sent == [
        {
            "event": "system_note",
            "slot_id": slot_id,
            "text": "✅ scout finished: 3 pools",
            "kind": "delegation",
        }
    ]


def test_a_note_for_a_dead_session_still_reaches_the_tab(ws_env):
    """The same sweep drops a session whose client stopped answering; the tab
    it belonged to is still open and still wants the line."""

    async def scenario():
        opener = _FakeWS()
        slot_id = await _start(opener)
        watcher = _FakeWS()
        _attach(watcher)
        _EchoClient.last.alive = False
        shown = await wake.deliver_note(
            session_key=str(SessionKey.web(USER, slot_id)),
            conversation_id=slot_id,
            text="done",
            kind="delegation",
            user_id=USER,
        )
        return shown, watcher, slot_id

    shown, watcher, slot_id = asyncio.run(scenario())

    assert shown is True
    assert [e["event"] for e in watcher.sent] == ["system_note"]
    assert watcher.sent[0]["slot_id"] == slot_id


def test_a_reaped_session_with_no_tab_open_pushes_nothing_and_raises_nothing(ws_env):
    """The transcript turn stays the only record when nobody is watching."""

    async def scenario():
        opener = _FakeWS()
        slot_id = await _start(opener)
        before = len(opener.sent)
        session_module._sessions.clear()
        # Nobody attached — the state after the last tab closed.
        await wake.deliver_note(
            session_key=str(SessionKey.web(USER, slot_id)),
            conversation_id=slot_id,
            text="done",
            kind="delegation",
            user_id=USER,
        )
        return opener, before

    opener, before = asyncio.run(scenario())

    assert opener.sent[before:] == []
    assert chat_ws._attached_sockets == {}


def test_the_conversation_guard_survives_on_telegram(ws_env, monkeypatch):
    """``tg:{chat_id}`` is stable but the conversation behind it is not, so a
    task started before ``/new`` must still stay quiet there — the check that
    the web surface no longer needs, because there the slot *is* the
    conversation.

    Driven through the *registered* Telegram sink (CORR-266): this used to have
    to inject ``_note_sinks["tg"]`` by hand, because production registered only
    the wake half — so the note it asserted was never delivered was being
    dropped for the wrong reason. The guard now has to be what stops it.
    """
    from condor.agents import delegate as delegate_module
    from handlers.agents import wake as tg_wake

    assert wake._note_sinks.get("tg") is tg_wake._deliver_note

    delivered: list[dict] = []

    class _Recorder:
        async def send_message(self, **kw):
            delivered.append(kw)
            return None

    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: _Recorder())

    async def scenario():
        return await wake.deliver_note(
            session_key="tg:42",
            conversation_id="a-conversation-that-chat-has-left",
            text="done",
            kind="delegation",
            user_id=USER,
        )

    assert asyncio.run(scenario()) is False
    assert delivered == []
