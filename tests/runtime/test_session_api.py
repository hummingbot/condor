"""Tests for the session API (FEAT-009).

The point of this feature is that Telegram and the web dashboard stop having
their own idea of what an event is. So the tests that matter here are the ones
pinning the event mapping and the frozen WebSocket wire format.
"""

import asyncio

import pytest

from condor.acp.client import (
    Heartbeat,
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
)
from condor.runtime import SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import sessions as session_module
from condor.runtime.adapters.sse_to_queue import SSEToQueueAdapter
from condor.runtime.events import EventType, RuntimeEvent, serialize
from condor.runtime.sse import event_stream, format_event, parse_frame
from condor.web.routes.chat_ws import _to_ws_message


class _ScriptedClient:
    """Client that replays a fixed list of ACP events."""

    script: list = []

    def __init__(self, **kwargs):
        self.alive = True
        type(self).last = self

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "summary"

    async def prompt_stream(self, text):
        for event in type(self).script:
            yield event


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _ScriptedClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    # Lives in condor.runtime.binding now; patch it at the source so both the
    # bound and unbound resolution paths see the stub.
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    return session_module


# ── Event mapping ──


def test_event_mapping():
    """Every ACP dataclass maps to a typed RuntimeEvent."""
    cases = [
        (TextChunk(text="hi"), EventType.TEXT),
        (ThoughtChunk(text="hmm"), EventType.THOUGHT),
        (
            ToolCallEvent(tool_call_id="t1", title="Read", status="pending"),
            EventType.TOOL_CALL,
        ),
        (ToolCallUpdate(tool_call_id="t1", status="completed"), EventType.TOOL_UPDATE),
        (Heartbeat(elapsed_seconds=1.5), EventType.HEARTBEAT),
        (PromptDone(stop_reason="end_turn"), EventType.DONE),
    ]
    for acp_event, expected in cases:
        event = RuntimeEvent.from_acp(acp_event, session_key="tg:1")
        assert event.type is expected
        assert event.session_key == "tg:1"

    assert RuntimeEvent.from_acp(TextChunk(text="x")).text == "x"
    assert RuntimeEvent.from_acp(PromptDone(stop_reason="timeout")).stop_reason == (
        "timeout"
    )


def test_unknown_event_becomes_error_not_a_crash():
    """A new upstream event type must never kill a live stream."""
    event = RuntimeEvent.from_acp(object(), session_key="tg:1")
    assert event.type is EventType.ERROR
    assert "Unknown event type" in event.field("message")


def test_serializer_bytes():
    """Image bytes survive as base64; nested structures flatten."""
    assert serialize(b"hi") == "aGk="
    nested = serialize({"img": b"hi", "items": [b"a", {"deep": b"b"}]})
    assert nested == {"img": "aGk=", "items": ["YQ==", {"deep": "Yg=="}]}

    tool_call = ToolCallEvent(
        tool_call_id="t1", title="Read", status="pending", input={"blob": b"hi"}
    )
    event = RuntimeEvent.from_acp(tool_call)
    assert event.field("input") == {"blob": "aGk="}
    # to_wire() must be JSON-safe all the way down.
    import json

    json.dumps(event.to_wire())


# ── SSE framing ──


def test_sse_frame_roundtrip():
    event = RuntimeEvent(
        type=EventType.TEXT, session_key="web:1:s", data={"text": "hi"}
    )
    frame = format_event(event)
    assert frame.startswith("event: text\n")
    assert frame.endswith("\n\n")
    parsed = parse_frame(frame)
    assert parsed.type is EventType.TEXT
    assert parsed.text == "hi"
    # Comments/keepalives are not events.
    assert parse_frame(": keepalive\n\n") is None


def test_sse_stream_terminates():
    """3 chunks + done produce 4 frames and the stream closes."""

    async def produce():
        for text in ("a", "b", "c"):
            yield RuntimeEvent(type=EventType.TEXT, data={"text": text})
        yield RuntimeEvent.done("end_turn")

    async def collect():
        return [frame async for frame in event_stream(produce())]

    frames = asyncio.run(collect())
    assert len(frames) == 4
    assert frames[-1].startswith("event: done\n")


def test_sse_stream_always_closes_with_done():
    """A producer that raises still terminates the stream for the client."""

    async def explode():
        yield RuntimeEvent(type=EventType.TEXT, data={"text": "a"})
        raise RuntimeError("boom")

    async def collect():
        return [frame async for frame in event_stream(explode())]

    frames = asyncio.run(collect())
    assert "boom" in frames[1]
    assert frames[-1].startswith("event: done\n")


# ── The frozen WebSocket contract ──


def test_ws_contract_unchanged():
    """chat_ws must emit exactly the keys the shipped frontend reads.

    These shapes are a live contract with frontend/src/lib/api.ts. The FEAT-009
    refactor re-plumbed the inside of the endpoint; if this test changes, the
    dashboard breaks.
    """
    text = _to_ws_message(RuntimeEvent.from_acp(TextChunk(text="hi")), "slot1")
    assert text == {"event": "text_chunk", "slot_id": "slot1", "text": "hi"}

    thought = _to_ws_message(RuntimeEvent.from_acp(ThoughtChunk(text="hm")), "slot1")
    assert thought == {"event": "thought_chunk", "slot_id": "slot1", "text": "hm"}

    call = _to_ws_message(
        RuntimeEvent.from_acp(
            ToolCallEvent(tool_call_id="t1", title="Read", status="pending")
        ),
        "slot1",
    )
    assert call == {
        "event": "tool_call",
        "slot_id": "slot1",
        "tool_call_id": "t1",
        "title": "Read",
        "status": "pending",
    }

    update = _to_ws_message(
        RuntimeEvent.from_acp(ToolCallUpdate(tool_call_id="t1", status="completed")),
        "slot1",
    )
    assert update == {
        "event": "tool_call_update",
        "slot_id": "slot1",
        "tool_call_id": "t1",
        "status": "completed",
    }

    beat = _to_ws_message(
        RuntimeEvent.from_acp(Heartbeat(elapsed_seconds=2.0)), "slot1"
    )
    assert beat == {"event": "heartbeat", "slot_id": "slot1", "elapsed_seconds": 2.0}

    done = _to_ws_message(
        RuntimeEvent.from_acp(PromptDone(stop_reason="end_turn")), "s"
    )
    assert done == {"event": "prompt_done", "slot_id": "s", "stop_reason": "end_turn"}

    err = _to_ws_message(RuntimeEvent.error("nope"), "slot1")
    assert err == {"event": "error", "slot_id": "slot1", "message": "nope"}

    # Permission requests travel out-of-band through the permission callback.
    assert _to_ws_message(RuntimeEvent(type=EventType.PERMISSION), "slot1") is None


# ── Facade-level prompting ──


def test_prompt_streams_runtime_events(registry):
    """runtime.prompt() yields canonical events, not ACP dataclasses."""
    _ScriptedClient.script = [
        TextChunk(text="hello"),
        PromptDone(stop_reason="end_turn"),
    ]
    key = SessionKey.telegram(7)

    async def scenario():
        await runtime.create_session(
            SessionSpec(key=str(key), agent_key="claude-code", chat_id=7, user_id=7)
        )
        from condor.runtime import PromptRequest

        return [e async for e in runtime.prompt(key, PromptRequest(text="hi"))]

    events = asyncio.run(scenario())
    assert [e.type for e in events] == [EventType.TEXT, EventType.DONE]
    assert all(isinstance(e, RuntimeEvent) for e in events)
    assert events[0].session_key == str(key)


def test_prompt_on_missing_session_still_terminates(registry):
    """No session must yield error+done rather than raising at the caller."""
    from condor.runtime import PromptRequest

    async def scenario():
        return [
            e
            async for e in runtime.prompt(
                SessionKey.telegram(404), PromptRequest(text="hi")
            )
        ]

    events = asyncio.run(scenario())
    assert [e.type for e in events] == [EventType.ERROR, EventType.DONE]


def test_session_budget_is_shared_across_surfaces(registry):
    """The per-user cap lives in the runtime, so both surfaces draw on it.

    Since FEAT-015 the cap is an LRU rather than a wall, so the refusal only
    survives when every session is busy — see ``test_conversations.py`` for the
    detach path.
    """
    monkey_max = session_module.MAX_SESSIONS_PER_USER

    async def scenario():
        for i in range(monkey_max):
            await runtime.create_session(
                SessionSpec(
                    key=str(SessionKey.web(42, f"slot{i}")),
                    agent_key="claude-code",
                    user_id=42,
                )
            )
        for session in session_module._sessions.values():
            session.is_busy = True

        # One more, from the *other* surface, must be refused: there is no
        # idle session to give up.
        with pytest.raises(session_module.SessionLimitReached):
            await runtime.create_session(
                SessionSpec(
                    key=str(SessionKey.telegram(999)),
                    agent_key="claude-code",
                    chat_id=999,
                    user_id=42,
                )
            )

    asyncio.run(scenario())
    assert len(asyncio.run(runtime.list_sessions(42))) == monkey_max


def test_cross_surface_listing(registry):
    """A tg and a web session for one user both show up in one list."""

    async def scenario():
        await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.telegram(5)),
                agent_key="claude-code",
                chat_id=5,
                user_id=5,
            )
        )
        await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.web(5, "slotA")),
                agent_key="claude-code",
                user_id=5,
            )
        )
        return await runtime.list_sessions(user_id=5)

    infos = asyncio.run(scenario())
    assert {i.surface for i in infos} == {"tg", "web"}
    assert {i.slot for i in infos} == {"", "slotA"}
    assert all(i.user_id == 5 for i in infos)


# ── SSE -> queue adapter (used once the runtime is out of process) ──


def test_sse_to_queue_adapter_passes_heartbeats_and_captures_done():
    async def produce():
        yield RuntimeEvent(type=EventType.HEARTBEAT, data={"elapsed_seconds": 1})
        yield RuntimeEvent(type=EventType.TEXT, data={"text": "hi"})
        yield RuntimeEvent.done("end_turn")

    async def scenario():
        adapter = SSEToQueueAdapter()
        await adapter.pump(produce())
        return adapter, [e async for e in adapter.events()]

    adapter, drained = asyncio.run(scenario())
    # Heartbeats are forwarded: consumers use them to reset inactivity timers.
    assert [e.type for e in drained] == [
        EventType.HEARTBEAT,
        EventType.TEXT,
        EventType.DONE,
    ]
    assert adapter.done_event.stop_reason == "end_turn"
