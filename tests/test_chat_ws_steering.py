"""Steering a dashboard chat mid-answer (FEAT-030).

Every WS action is fire-and-forget, so two ``send_message`` frames for one slot
run concurrently. Before the per-slot gate, #2 registered itself in
``_active_prompt_tasks`` while #1 was still being cancelled: the entry was
overwritten, a later Stop cancelled the wrong task, and #1's was never reaped.
These tests drive both frames for real and pin the bookkeeping.
"""

import asyncio
import json

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import SessionKey, conversations
from condor.runtime import sessions as session_module
from condor.runtime.conversations import read_transcript
from condor.web.routes import chat_ws
from condor.web.routes.chat_ws import (
    _handle_abort_prompt,
    _handle_send_message,
    _handle_start_session,
)

USER = 222


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def events(self, name: str) -> list[dict]:
        return [e for e in self.sent if e.get("event") == name]

    def names(self) -> list[str]:
        return [e.get("event") for e in self.sent]


class _GatedClient:
    """A turn that hangs until released, so a second frame can land on it."""

    def __init__(self, **kwargs):
        self.alive = True
        self.prompts: list[str] = []
        self.finished: list[str] = []
        self.aborts = 0
        self._release = asyncio.Event()
        self._cancelled = False
        type(self).last = self

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

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


@pytest.fixture
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _GatedClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    return chat_ws


async def _until_busy(slot_id: str) -> None:
    key = SessionKey.web(USER, slot_id)
    for _ in range(400):
        session = session_module.get_session(key)
        if session and session.is_busy:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("session never became busy")


async def _start(ws: _FakeWS) -> str:
    await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
    return ws.events("session_started")[0]["slot_id"]


def _send(ws: _FakeWS, slot_id: str, text: str) -> asyncio.Task:
    return asyncio.create_task(
        _handle_send_message(ws, USER, {"slot_id": slot_id, "text": text})
    )


def test_a_message_sent_mid_answer_steers_instead_of_being_rejected(ws_env):
    async def scenario():
        ws = _FakeWS()
        slot_id = await _start(ws)
        client = _GatedClient.last

        first = _send(ws, slot_id, "how is my portfolio")
        await _until_busy(slot_id)

        second = _send(ws, slot_id, "no, check the bots instead")
        await asyncio.wait_for(first, timeout=5)

        await _until_busy(slot_id)
        client.finish_turn()
        await asyncio.wait_for(second, timeout=5)
        return ws, client, slot_id

    ws, client, slot_id = asyncio.run(scenario())

    # The old refusal is gone...
    assert not [e for e in ws.events("error") if e.get("message") == "Agent is busy"]
    # ...and the turn ahead was actually stopped, on the same session.
    assert client.aborts == 1
    assert client.prompts == ["how is my portfolio", "no, check the bots instead"]
    assert client.finished == ["no, check the bots instead"]

    # The partial is marked rather than left looking finished.
    interrupted = ws.events("prompt_interrupted")
    assert [e["slot_id"] for e in interrupted] == [slot_id]
    assert [e["slot_id"] for e in ws.events("queued")] == [slot_id]
    # The interrupted turn still closes its bubble.
    assert [e["stop_reason"] for e in ws.events("prompt_done")] == [
        "cancelled",
        "end_turn",
    ]

    # And a reload shows what was on screen: the partial is in the transcript,
    # not a gap where an abandoned turn used to be.
    assert [t.text for t in read_transcript(USER, slot_id)] == [
        "how is my portfolio",
        "answering how is my portfolio",
        "no, check the bots instead",
        "answering no, check the bots instead",
    ]


def test_two_frames_leave_exactly_one_live_task_and_no_orphan(ws_env):
    """The race the per-slot gate exists to close."""

    async def scenario():
        ws = _FakeWS()
        slot_id = await _start(ws)
        client = _GatedClient.last
        task_key = f"{USER}:{slot_id}"

        first = _send(ws, slot_id, "first")
        await _until_busy(slot_id)
        second = _send(ws, slot_id, "second")

        await asyncio.wait_for(first, timeout=5)
        await _until_busy(slot_id)

        # Exactly one entry, and it is the turn actually running.
        owner = chat_ws._active_prompt_tasks.get(task_key)
        assert len(chat_ws._active_prompt_tasks) == 1
        assert owner is second

        client.finish_turn()
        await asyncio.wait_for(second, timeout=5)
        return first, second

    first, second = asyncio.run(scenario())

    # The steered-aside turn finished on its own — not cancelled, not left
    # running, and it did not reap the entry that replaced it.
    assert first.done() and not first.cancelled()
    assert first.exception() is None
    assert second.exception() is None
    assert chat_ws._active_prompt_tasks == {}


def test_back_to_back_frames_in_one_tick_are_both_handled(ws_env):
    """Two frames in the same batch: neither is dropped, neither is orphaned.

    This is the shape that used to corrupt the task map — #2 arriving while #1
    was still being set up. Both reach the agent, in order, and the map is
    empty when they are done.
    """

    async def scenario():
        ws = _FakeWS()
        slot_id = await _start(ws)
        client = _GatedClient.last

        first = _send(ws, slot_id, "first")
        second = _send(ws, slot_id, "second")

        # Release whatever turn is in flight until both handlers return; which
        # of them is running when is exactly the timing this must not depend on.
        for _ in range(200):
            if first.done() and second.done():
                break
            client.finish_turn()
            await asyncio.sleep(0.01)

        await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
        return ws, client

    ws, client = asyncio.run(scenario())

    assert client.prompts == ["first", "second"]
    assert chat_ws._active_prompt_tasks == {}
    assert not [e for e in ws.events("error") if e.get("message") == "Agent is busy"]
    assert "error" not in ws.names()


def test_stop_after_a_steer_cancels_the_turn_that_is_actually_running(ws_env):
    """Stop must land on the turn that replaced the one it is chasing."""

    async def scenario():
        ws = _FakeWS()
        slot_id = await _start(ws)
        client = _GatedClient.last

        first = _send(ws, slot_id, "first")
        await _until_busy(slot_id)
        second = _send(ws, slot_id, "second")
        await asyncio.wait_for(first, timeout=5)
        await _until_busy(slot_id)

        await asyncio.wait_for(
            _handle_abort_prompt(ws, USER, {"slot_id": slot_id}), timeout=5
        )
        await asyncio.wait_for(
            asyncio.gather(second, return_exceptions=True), timeout=5
        )
        return ws, client

    ws, client = asyncio.run(scenario())

    # One abort for the steer, one for the Stop — and the second turn never
    # completed, which is the whole point of pressing it.
    assert client.aborts == 2
    assert client.finished == []
    assert chat_ws._active_prompt_tasks == {}
