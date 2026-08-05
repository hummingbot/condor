"""A finished background task wakes the chat that asked for it (FEAT-034).

``wake.resume_session`` is the first thing in this codebase that starts a turn
nobody typed. These tests pin the three properties that make that safe: it only
ever prompts a session that is *alive and still on the same conversation*, it
never raises at the caller (a wake that cannot happen degrades to the passive
notification that already went out), and the turn it drives is recorded as a
system note rather than as words the user said.
"""

import asyncio

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.runtime import wake
from condor.runtime.events import EventType

KEY = SessionKey.telegram(7)


class _EchoClient:
    """A client that answers one chunk per prompt and ends the turn."""

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
        yield TextChunk(text="continuing where I left off")
        yield PromptDone(stop_reason="end_turn")


class _RecordingSink:
    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.events: list = []

    async def open(self):
        self.opened += 1

    async def on_event(self, event):
        self.events.append(event)

    async def close(self):
        self.closed += 1


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _EchoClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    # Sink factories and the in-flight set are process-global, exactly like the
    # confirmation registry: reset both so tests cannot leak into each other.
    monkeypatch.setattr(wake, "_sink_factories", {})
    monkeypatch.setattr(wake, "_in_flight", set())
    return session_module


async def _open_session() -> tuple[_EchoClient, str]:
    """Start a session and return its client plus the conversation behind it."""
    info = await runtime.create_session(
        SessionSpec(key=str(KEY), agent_key="claude-code", chat_id=7, user_id=7)
    )
    return _EchoClient.last, info.conversation_id


def _turns(conv_id: str) -> list:
    return conversations.read_transcript(7, conv_id, limit=0)


# ── Liveness ──


def test_a_live_session_gets_exactly_one_turn(registry):
    async def scenario():
        client, conv_id = await _open_session()
        woke = await wake.resume_session(
            session_key=str(KEY),
            conversation_id=conv_id,
            text="[background task complete] done",
            kind="resume",
        )
        return client, conv_id, woke

    client, conv_id, woke = asyncio.run(scenario())

    assert woke is True
    assert client.prompts == ["[background task complete] done"]
    assert not wake.is_waking(conv_id)


def test_a_missing_session_is_not_woken_and_does_not_raise(registry):
    woke = asyncio.run(
        wake.resume_session(
            session_key=str(KEY),
            conversation_id="conv-nobody-has",
            text="done",
            kind="resume",
        )
    )
    assert woke is False


def test_a_dead_session_is_not_woken(registry):
    """No respawn: the outcome is already in the chat and in the transcript."""

    async def scenario():
        client, conv_id = await _open_session()
        client.alive = False
        woke = await wake.resume_session(
            session_key=str(KEY), conversation_id=conv_id, text="done", kind="resume"
        )
        return client, woke

    client, woke = asyncio.run(scenario())

    assert woke is False
    assert client.prompts == []


def test_a_session_that_moved_on_is_not_woken(registry):
    """The check that matters on Telegram: ``tg:{chat_id}`` outlives its chat.

    The key is stable but the conversation behind it changes on ``/new``, so
    without this a task started before the switch would wake a chat about
    something else entirely.
    """

    async def scenario():
        client, _conv_id = await _open_session()
        woke = await wake.resume_session(
            session_key=str(KEY),
            conversation_id="the-conversation-this-task-was-started-in",
            text="done",
            kind="resume",
        )
        return client, woke

    client, woke = asyncio.run(scenario())

    assert woke is False
    assert client.prompts == []


def test_a_malformed_key_returns_false(registry):
    woke = asyncio.run(
        wake.resume_session(
            session_key="not-a-key", conversation_id="c1", text="done", kind="resume"
        )
    )
    assert woke is False


# ── Sinks ──


def test_the_surface_sink_sees_the_whole_turn(registry):
    sink = _RecordingSink()

    async def scenario():
        _client, conv_id = await _open_session()
        wake.register_sink_factory("tg", lambda key, user_id: sink)
        await wake.resume_session(
            session_key=str(KEY), conversation_id=conv_id, text="done", kind="resume"
        )

    asyncio.run(scenario())

    assert sink.opened == 1
    assert sink.closed == 1
    assert [e.type for e in sink.events] == [EventType.TEXT, EventType.DONE]


def test_a_sink_that_blows_up_does_not_kill_the_turn(registry):
    """The transcript is the record; a surface failing is not a failed turn."""

    class _BrokenSink(_RecordingSink):
        async def on_event(self, event):
            raise RuntimeError("socket died mid-turn")

    async def scenario():
        client, conv_id = await _open_session()
        wake.register_sink_factory("tg", lambda key, user_id: _BrokenSink())
        woke = await wake.resume_session(
            session_key=str(KEY), conversation_id=conv_id, text="done", kind="resume"
        )
        return client, conv_id, woke

    client, conv_id, woke = asyncio.run(scenario())

    assert woke is True
    assert client.prompts == ["done"]
    assert [t.role for t in _turns(conv_id)] == ["system", "assistant"]


def test_no_sink_registered_still_runs_and_records_the_turn(registry):
    """Nobody attached is not a failure -- the dashboard picks it up on reload."""

    async def scenario():
        client, conv_id = await _open_session()
        woke = await wake.resume_session(
            session_key=str(KEY), conversation_id=conv_id, text="done", kind="resume"
        )
        return client, conv_id, woke

    client, conv_id, woke = asyncio.run(scenario())

    assert woke is True
    assert client.prompts == ["done"]
    assert [t.role for t in _turns(conv_id)] == ["system", "assistant"]


# ── Truthful recording ──


def test_the_woken_turn_is_recorded_as_a_system_note(registry):
    """Recording it as ``user`` would put words in the user's mouth -- and
    ``replay_context`` would then read them back to the next session as
    something they said."""

    async def scenario():
        _client, conv_id = await _open_session()
        await wake.resume_session(
            session_key=str(KEY),
            conversation_id=conv_id,
            text="[background task complete] 3 pools found",
            kind="resume",
        )
        return conv_id

    conv_id = asyncio.run(scenario())
    opening, reply = _turns(conv_id)

    assert opening.role == "system"
    assert opening.kind == "resume"
    assert opening.text == "[background task complete] 3 pools found"
    assert reply.role == "assistant"
    assert reply.text == "continuing where I left off"

    # A system turn replays as a parenthetical, not as a line the user typed.
    assert "[user]" not in conversations.replay_context(7, conv_id)


def test_a_typed_turn_is_still_recorded_as_the_user(registry):
    async def scenario():
        _client, conv_id = await _open_session()
        async for _ in runtime.prompt(KEY, PromptRequest(text="hello")):
            pass
        return conv_id

    conv_id = asyncio.run(scenario())
    opening, _reply = _turns(conv_id)

    assert opening.role == "user"
    assert opening.kind == ""
    assert opening.text == "hello"


# ── Depth-1 guard ──


def test_the_conversation_is_marked_in_flight_only_while_it_is_waking(registry):
    """What bounds the recursion: a delegation started from inside a wake turn
    is forced back to ``notify`` by this flag."""
    seen: list[bool] = []

    class _Watcher(_RecordingSink):
        async def on_event(self, event):
            seen.append(wake.is_waking(self.conv_id))

    async def scenario():
        _client, conv_id = await _open_session()
        watcher = _Watcher()
        watcher.conv_id = conv_id
        wake.register_sink_factory("tg", lambda key, user_id: watcher)
        assert not wake.is_waking(conv_id)
        await wake.resume_session(
            session_key=str(KEY), conversation_id=conv_id, text="done", kind="resume"
        )
        return conv_id

    conv_id = asyncio.run(scenario())

    assert seen and all(seen)
    assert not wake.is_waking(conv_id)
