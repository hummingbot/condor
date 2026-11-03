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


class _GatedClient(_EchoClient):
    """A turn that hangs until released — or until it is cancelled."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.finished: list[str] = []
        self.aborts = 0
        self._release = asyncio.Event()
        self._cancelled = False

    async def abort_prompt(self):
        self.aborts += 1
        self._cancelled = True
        self._release.set()

    def finish_turn(self):
        self._release.set()

    async def prompt_stream(self, text):
        self.prompts.append(text)
        self._release.clear()
        self._cancelled = False
        yield TextChunk(text=f"answering {text}")
        await self._release.wait()
        if self._cancelled:
            yield PromptDone(stop_reason="cancelled")
        else:
            self.finished.append(text)
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
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _EchoClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    # Sink factories and the in-flight counts are process-global, exactly like the
    # confirmation registry: reset both so tests cannot leak into each other.
    monkeypatch.setattr(wake, "_sink_factories", {})
    monkeypatch.setattr(wake, "_in_flight", {})
    return session_module


async def _open_session() -> tuple[_EchoClient, str]:
    """Start a session and return its client plus the conversation behind it."""
    info = await runtime.create_session(
        SessionSpec(key=str(KEY), agent_key="claude-code", chat_id=7, user_id=7)
    )
    return _EchoClient.last, info.conversation_id


async def _collect(stream) -> list:
    return [event async for event in stream]


def _turns(conv_id: str) -> list:
    return conversations.read_transcript(7, conv_id, limit=0)


async def _until_busy() -> None:
    for _ in range(400):
        session = session_module.get_session(KEY)
        if session and session.is_busy:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("session never became busy")


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


# ── Sharing the session with the human ──


@pytest.fixture
def gated(registry, monkeypatch):
    monkeypatch.setattr("condor.acp.client.ACPClient", _GatedClient)
    return registry


def test_a_wake_waits_its_turn_behind_an_answer_in_flight(gated):
    """``queue``, never ``steer``: a wake must not take a slot from the human."""

    async def scenario():
        _client, conv_id = await _open_session()
        client = _GatedClient.last

        typed = asyncio.create_task(
            _collect(runtime.prompt(KEY, PromptRequest(text="typed"), on_busy="queue"))
        )
        await _until_busy()

        woken = asyncio.create_task(
            wake.resume_session(
                session_key=str(KEY),
                conversation_id=conv_id,
                text="woken",
                kind="resume",
            )
        )
        await asyncio.sleep(0.05)
        # The agent has only ever been asked the human's question.
        assert client.prompts == ["typed"]

        client.finish_turn()
        await asyncio.wait_for(typed, timeout=5)
        await _until_busy()
        client.finish_turn()
        await asyncio.wait_for(woken, timeout=5)
        return client, conv_id

    client, conv_id = asyncio.run(scenario())

    assert client.finished == ["typed", "woken"]
    assert [(t.role, t.kind) for t in _turns(conv_id)] == [
        ("user", ""),
        ("assistant", ""),
        ("system", "resume"),
        ("assistant", ""),
    ]


def test_a_user_message_sent_during_a_wake_steers_it_aside(gated):
    """The human wins. Their message is the one that gets answered."""

    async def scenario():
        _client, conv_id = await _open_session()
        client = _GatedClient.last

        woken = asyncio.create_task(
            wake.resume_session(
                session_key=str(KEY),
                conversation_id=conv_id,
                text="woken",
                kind="resume",
            )
        )
        await _until_busy()

        typed = asyncio.create_task(
            _collect(
                runtime.prompt(
                    KEY, PromptRequest(text="actually, stop"), on_busy="steer"
                )
            )
        )
        await asyncio.wait_for(woken, timeout=5)

        await _until_busy()
        client.finish_turn()
        await asyncio.wait_for(typed, timeout=5)
        return client

    client = asyncio.run(scenario())

    assert client.aborts == 1
    assert client.prompts == ["woken", "actually, stop"]
    assert client.finished == ["actually, stop"]


def test_abort_ends_a_wake_turn(gated):
    """Dashboard Stop and /stop act on the session, so they reach a wake too."""

    async def scenario():
        _client, conv_id = await _open_session()
        client = _GatedClient.last

        woken = asyncio.create_task(
            wake.resume_session(
                session_key=str(KEY),
                conversation_id=conv_id,
                text="woken",
                kind="resume",
            )
        )
        await _until_busy()
        await runtime.abort(KEY)
        result = await asyncio.wait_for(woken, timeout=5)
        return client, result, conv_id

    client, result, conv_id = asyncio.run(scenario())

    assert result is True
    assert client.aborts == 1
    assert client.finished == []
    # The half-written turn is still recorded -- the same guarantee a typed
    # turn gets when the user stops it.
    assert [(t.role, t.kind) for t in _turns(conv_id)] == [
        ("system", "resume"),
        ("assistant", ""),
    ]


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


def test_overlapping_wakes_hold_the_guard_until_the_last_one_ends(gated):
    """Two tasks finishing onto one conversation share the mark.

    The second wake queues behind the first on the session lock, so the first
    one's cleanup runs while the second is still to come. If that cleanup
    cleared the mark, the queued turn would be free to start another resuming
    delegation — the depth-1 bound, gone.
    """

    async def scenario():
        _client, conv_id = await _open_session()
        client = _GatedClient.last

        first = asyncio.create_task(
            wake.resume_session(
                session_key=str(KEY),
                conversation_id=conv_id,
                text="first done",
                kind="resume",
            )
        )
        await _until_busy()
        second = asyncio.create_task(
            wake.resume_session(
                session_key=str(KEY),
                conversation_id=conv_id,
                text="second done",
                kind="resume",
            )
        )
        # Long enough for the second wake to reach the queue behind the first.
        await asyncio.sleep(0.05)

        client.finish_turn()
        await asyncio.wait_for(first, timeout=5)
        marked_between = wake.is_waking(conv_id)

        await _until_busy()
        client.finish_turn()
        await asyncio.wait_for(second, timeout=5)
        return conv_id, marked_between, client

    conv_id, marked_between, client = asyncio.run(scenario())

    assert client.finished == ["first done", "second done"]
    assert marked_between, "the wake still queued was left unguarded"
    assert not wake.is_waking(conv_id)
