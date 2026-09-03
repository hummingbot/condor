"""What the dashboard's send frame does with a picture (FEAT-098).

The frame carries **ids**, never bytes. That is the whole reason this file
exists: uvicorn runs without ``ws_max_size`` (``main.py``), so the websockets
default of 16 MiB applies and an oversize frame *closes the socket* rather than
failing the message — one pasted screenshot would drop the user's chat
connection mid-answer with no error naming a cause. The bytes go over HTTP, to
the routes pinned in ``tests/test_conversations_api.py``; here only the
resolution and its refusals matter.

For a web slot the slot id *is* the conversation id, which is what makes the
resolution's ownership check a path rather than a rule: an id from someone else's
conversation is looked up under the caller's own tree, where it is not.
"""

import asyncio
import json

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import attachments, conversations
from condor.runtime import sessions as session_module
from condor.web.routes import chat_ws
from condor.web.routes.chat_ws import _handle_send_message, _handle_start_session

USER = 606
OTHER = 607
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 48


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def events(self, name: str) -> list[dict]:
        return [e for e in self.sent if e.get("event") == name]


class _Client:
    def __init__(self, **kwargs):
        self.alive = True
        self.accepts_images = True
        type(self).calls = getattr(type(self), "calls", [])

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def prompt_stream(self, text, **kwargs):
        type(self).calls.append({"text": text, **kwargs})
        yield TextChunk(text="ok")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def ws_env(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _Client)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    monkeypatch.setattr(chat_ws, "_secret_notices_sent", {})
    return chat_ws


def _session(ws: _FakeWS) -> str:
    return ws.events("session_started")[0]["slot_id"]


def _run(frames, *, user_id: int = USER):
    """Start a slot, then send each frame into it. Returns (ws, slot_id)."""

    async def scenario():
        ws = _FakeWS()
        await _handle_start_session(ws, user_id, {"agent_key": "claude-code"})
        slot_id = _session(ws)
        for frame in frames(slot_id):
            await _handle_send_message(ws, user_id, {"slot_id": slot_id, **frame})
        return ws, slot_id

    return asyncio.run(scenario())


def test_an_id_on_the_frame_becomes_bytes_for_the_model(ws_env):
    stored: dict = {}

    def frames(slot_id):
        stored["att"] = attachments.save(USER, slot_id, PNG)
        return [{"text": "what is wrong here?", "attachments": [stored["att"].id]}]

    ws, slot_id = _run(frames)

    assert ws.events("error") == []
    (call,) = _Client.calls
    (image,) = call["images"]
    assert image.data == PNG
    assert image.mime == "image/png"
    assert image.id == stored["att"].id


def test_a_frame_with_no_text_is_answered(ws_env):
    """An image with no words is a complete message — the refusal is gone."""

    def frames(slot_id):
        att = attachments.save(USER, slot_id, PNG)
        return [{"text": "", "attachments": [att.id]}]

    ws, _ = _run(frames)

    assert ws.events("error") == []
    (call,) = _Client.calls
    assert call["text"] == ""
    assert len(call["images"]) == 1


def test_a_frame_with_neither_is_still_refused(ws_env):
    ws, _ = _run(lambda slot_id: [{"text": "   "}])
    assert [e["message"] for e in ws.events("error")] == ["Empty message"]
    assert _Client.calls == []


def test_a_text_only_frame_is_the_call_it_always_was(ws_env):
    """No ``attachments`` key means no keyword: the wire is unchanged."""
    _run(lambda slot_id: [{"text": "how is SOL-USDC doing?"}])
    assert _Client.calls == [{"text": "how is SOL-USDC doing?"}]


def test_an_id_from_someone_elses_conversation_resolves_to_nothing(ws_env):
    """The lookup *is* the ownership check: the path is per-user."""
    theirs = conversations.new_conversation(OTHER, surface="web")
    stolen = attachments.save(OTHER, theirs.id, PNG)

    ws, _ = _run(lambda slot_id: [{"text": "read this", "attachments": [stolen.id]}])

    assert _Client.calls == [], "the turn must not be answered without the picture"
    assert "no longer available" in ws.events("error")[0]["message"]


def test_an_unknown_id_refuses_the_whole_turn(ws_env):
    """All-or-nothing: an answer to the wrong question is worse than an error."""

    def frames(slot_id):
        att = attachments.save(USER, slot_id, PNG)
        return [{"text": "compare these", "attachments": [att.id, "nope.png"]}]

    ws, _ = _run(frames)

    assert _Client.calls == []
    assert ws.events("error")


def test_a_traversal_on_the_frame_is_refused(ws_env):
    ws, _ = _run(lambda slot_id: [{"text": "read", "attachments": ["../../meta.json"]}])
    assert _Client.calls == []
    assert ws.events("error")


def test_more_than_the_per_turn_cap_is_trimmed_not_answered_wholesale(ws_env):
    """The bound on how much resolution one frame can ask for."""

    def frames(slot_id):
        ids = [
            attachments.save(USER, slot_id, PNG).id
            for _ in range(attachments.MAX_PER_TURN + 2)
        ]
        return [{"text": "all of these", "attachments": ids}]

    ws, _ = _run(frames)

    assert ws.events("error") == []
    (call,) = _Client.calls
    assert len(call["images"]) == attachments.MAX_PER_TURN


def test_the_turn_is_recorded_with_its_references(ws_env):
    stored: dict = {}

    def frames(slot_id):
        stored["att"] = attachments.save(USER, slot_id, PNG)
        return [{"text": "look", "attachments": [stored["att"].id]}]

    _, slot_id = _run(frames)
    conversations.flush_all()

    (user_turn,) = [
        turn
        for turn in conversations.read_transcript(USER, slot_id)
        if turn.role == "user"
    ]
    assert user_turn.attachments == [
        {"id": stored["att"].id, "mime": "image/png", "bytes": len(PNG)}
    ]
