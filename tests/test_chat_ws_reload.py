"""A page reload must not kill the turn it interrupts (CORR-278).

The disconnect handler cancelled every task the socket had spawned, the
answering turn included. The recorder's ``finally`` wrote the half of the reply
that existed, so nothing was *lost* — but the turn was over, the agent's work
stopped mid-sentence, and the reloaded page showed a truncated answer that
would never grow. These tests drive the real endpoint through a disconnect.
"""

import asyncio
import json

import pytest
from fastapi import WebSocketDisconnect

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import SessionKey, conversations
from condor.runtime import sessions as session_module
from condor.runtime.conversations import read_transcript
from condor.web.routes import chat_ws

USER = 333


class _FakeWS:
    """A socket the test feeds frames into and can hang up on."""

    def __init__(self):
        self.sent: list[dict] = []
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._closed = False

    # -- endpoint side ----------------------------------------------------
    async def accept(self, subprotocol=None) -> None:
        return None

    async def close(self, code=1000, reason="") -> None:
        self._closed = True

    async def send_text(self, raw: str) -> None:
        if self._closed:
            raise RuntimeError("socket is closed")
        self.sent.append(json.loads(raw))

    async def receive_text(self) -> str:
        raw = await self._inbox.get()
        if raw is None:
            raise WebSocketDisconnect(code=1001)
        return raw

    # -- test side --------------------------------------------------------
    def feed(self, frame: dict) -> None:
        self._inbox.put_nowait(json.dumps(frame))

    def hang_up(self) -> None:
        """What a reload does: the socket goes away, unannounced."""
        self._inbox.put_nowait(None)
        self._closed = True

    def events(self, name: str) -> list[dict]:
        return [e for e in self.sent if e.get("event") == name]

    async def wait_for(self, name: str) -> dict:
        for _ in range(400):
            found = self.events(name)
            if found:
                return found[0]
            await asyncio.sleep(0.005)
        raise AssertionError(f"no {name} frame arrived")


class _GatedClient:
    """A turn that hangs until released, so a reload can land mid-answer."""

    def __init__(self, **kwargs):
        self.alive = True
        self.prompts: list[str] = []
        self.finished: list[str] = []
        self.cancelled: list[str] = []
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
        self._cancelled = True
        self._release.set()

    def finish_turn(self):
        self._release.set()

    async def prompt_stream(self, text):
        self.prompts.append(text)
        self._release.clear()
        self._cancelled = False
        yield TextChunk(text="I read the routines")
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.cancelled.append(text)
            raise
        if self._cancelled:
            yield PromptDone(stop_reason="cancelled")
        else:
            yield TextChunk(text=" and here is the plan")
            self.finished.append(text)
            yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def ws_env(tmp_path, monkeypatch):
    from config_manager import UserRole, get_config_manager

    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _GatedClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    monkeypatch.setattr(chat_ws, "_attached_sockets", {})
    monkeypatch.setattr(chat_ws, "_orphaned_turns", set())
    monkeypatch.setattr(chat_ws, "extract_ws_token", lambda ws, token: ("t", None))
    monkeypatch.setattr(chat_ws, "decode_jwt", lambda token: {"sub": USER})
    # Only the role gate is stubbed: the rest of the config manager is what
    # spawns a session, so replacing the whole singleton breaks the spawn.
    monkeypatch.setattr(
        type(get_config_manager()),
        "get_user_role",
        lambda self, uid: UserRole.ADMIN,
    )
    return chat_ws


async def _until_busy(slot_id: str) -> None:
    key = SessionKey.web(USER, slot_id)
    for _ in range(400):
        session = session_module.get_session(key)
        if session and session.is_busy:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("session never became busy")


async def _connect(ws: _FakeWS) -> asyncio.Task:
    task = asyncio.create_task(chat_ws.chat_websocket(ws, token="t"))
    await ws.wait_for("sessions_list")
    return task


async def _open_chat_mid_answer(ws: _FakeWS) -> tuple[asyncio.Task, str]:
    """Connect, start a chat, and leave a turn hanging half-answered."""
    conn = await _connect(ws)
    ws.feed({"action": "start_session", "agent_key": "claude-code"})
    slot_id = (await ws.wait_for("session_started"))["slot_id"]
    ws.feed(
        {"action": "send_message", "slot_id": slot_id, "text": "write the strategy"}
    )
    await _until_busy(slot_id)
    return conn, slot_id


def test_a_reload_mid_answer_leaves_the_turn_running(ws_env):
    async def scenario():
        ws = _FakeWS()
        conn, slot_id = await _open_chat_mid_answer(ws)
        client = _GatedClient.last

        ws.hang_up()
        await asyncio.wait_for(conn, timeout=5)

        # The socket's cleanup is done and the turn is still alive.
        assert len(chat_ws._orphaned_turns) == 1
        turn = next(iter(chat_ws._orphaned_turns))
        assert not turn.done()

        client.finish_turn()
        await asyncio.wait_for(turn, timeout=5)
        return client, slot_id, turn

    client, slot_id, turn = asyncio.run(scenario())

    # The agent finished the work rather than being cut off...
    assert client.finished == ["write the strategy"]
    assert client.cancelled == []
    assert not turn.cancelled()
    # ...and the transcript holds the whole answer, not the half on screen.
    assert [t.text for t in read_transcript(USER, slot_id)] == [
        "write the strategy",
        "I read the routines and here is the plan",
    ]
    # Nothing is left holding a reference to a task that is over.
    assert chat_ws._orphaned_turns == set()
    assert chat_ws._active_prompt_tasks == {}


def test_the_reloaded_tab_picks_the_answer_back_up(ws_env):
    """The rest of an orphaned turn is addressed to the tabs still open."""

    async def scenario():
        first = _FakeWS()
        conn, slot_id = await _open_chat_mid_answer(first)
        client = _GatedClient.last

        first.hang_up()
        await asyncio.wait_for(conn, timeout=5)

        # The page comes back on a new socket, as a reload does.
        second = _FakeWS()
        reloaded = await _connect(second)
        turn = next(iter(chat_ws._orphaned_turns))

        client.finish_turn()
        await asyncio.wait_for(turn, timeout=5)
        await second.wait_for("prompt_done")

        second.hang_up()
        await asyncio.wait_for(reloaded, timeout=5)
        return first, second, slot_id

    first, second, slot_id = asyncio.run(scenario())

    # The tail of the answer lands in the new tab, addressed to the same slot...
    assert [e["text"] for e in second.events("text_chunk")] == [" and here is the plan"]
    assert [e["slot_id"] for e in second.events("text_chunk")] == [slot_id]
    assert [e["stop_reason"] for e in second.events("prompt_done")] == ["end_turn"]
    # ...and the dead socket was not written to after it hung up.
    assert [e["text"] for e in first.events("text_chunk")] == ["I read the routines"]


def test_a_disconnect_still_cancels_work_that_is_not_a_turn(ws_env, monkeypatch):
    """Only a turn outlives the socket; the connection's own work is reaped."""

    reached = asyncio.Event()
    outcome: list[str] = []

    async def _never_returns(ws, user_id, msg):
        reached.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            outcome.append("cancelled")
            raise

    monkeypatch.setattr(chat_ws, "_handle_start_session", _never_returns)

    async def scenario():
        ws = _FakeWS()
        conn = await _connect(ws)
        ws.feed({"action": "start_session", "agent_key": "claude-code"})
        await asyncio.wait_for(reached.wait(), timeout=5)

        ws.hang_up()
        await asyncio.wait_for(conn, timeout=5)

    asyncio.run(scenario())

    assert outcome == ["cancelled"]
    assert chat_ws._orphaned_turns == set()
