"""A background chat-WS handler that crashes must say so (CORR-583).

Every action on ``/ws/chat`` is dispatched as a fire-and-forget task. The
tracker attached only ``discard``, so nobody ever read the task's exception:
a handler that raised — a disk error minting the conversation, a dead ACP
subprocess on abort — reached the operator only as asyncio's GC-time "Task
exception was never retrieved", on no module's logger and naming no action,
while the tab that asked sat there forever.

These tests drive the real endpoint and assert on the log record it emits, and
on the silence that a plain disconnect still has to keep.
"""

import asyncio
import json
import logging

import pytest
from fastapi import WebSocketDisconnect

from condor.web.routes import chat_ws

USER = 909
LOGGER = "condor.web.routes.chat_ws"


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


@pytest.fixture
def ws_env(monkeypatch):
    """Just enough of the world to open the socket; no session machinery."""
    from config_manager import UserRole, get_config_manager

    monkeypatch.setattr(chat_ws, "_attached_sockets", {})
    monkeypatch.setattr(chat_ws, "_orphaned_turns", set())
    monkeypatch.setattr(chat_ws, "extract_ws_token", lambda ws, token: ("t", None))
    monkeypatch.setattr(chat_ws, "decode_jwt", lambda token: {"sub": USER})
    monkeypatch.setattr(
        type(get_config_manager()),
        "get_user_role",
        lambda self, uid: UserRole.ADMIN,
    )

    async def _no_sessions(*args, **kwargs):
        return []

    monkeypatch.setattr(chat_ws.runtime, "list_sessions", _no_sessions)
    return chat_ws


async def _connect(ws: _FakeWS) -> asyncio.Task:
    conn = asyncio.create_task(chat_ws.chat_websocket(ws, token="t"))
    await ws.wait_for("sessions_list")
    return conn


def _failures(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER]


def test_a_handler_that_raises_is_logged_with_its_action_and_user(
    ws_env, monkeypatch, caplog
):
    async def boom(ws, user_id, msg):
        raise OSError("no space left on device")

    monkeypatch.setattr(chat_ws, "_handle_start_session", boom)

    async def scenario():
        ws = _FakeWS()
        conn = await _connect(ws)
        ws.feed({"action": "start_session", "agent_key": "claude-code"})
        for _ in range(400):
            if _failures(caplog):
                break
            await asyncio.sleep(0.005)
        ws.hang_up()
        await asyncio.wait_for(conn, timeout=5)

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        asyncio.run(scenario())

    records = _failures(caplog)
    assert len(records) == 1, "the crash was swallowed by the background tracker"
    message = records[0].getMessage()
    assert "start_session" in message, message
    assert str(USER) in message, message
    # ...with the traceback attached, so the log names the disk error itself.
    assert records[0].exc_info is not None
    assert isinstance(records[0].exc_info[1], OSError)


def test_a_disconnect_cancelling_a_handler_logs_nothing(ws_env, monkeypatch, caplog):
    """Cancellation is not a failure: closing a tab must stay silent."""
    entered = asyncio.Event()

    async def hang(ws, user_id, msg):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(chat_ws, "_handle_destroy_session", hang)

    async def scenario():
        ws = _FakeWS()
        conn = await _connect(ws)
        ws.feed({"action": "destroy_session", "slot_id": "slot-1"})
        await asyncio.wait_for(entered.wait(), timeout=5)
        # The disconnect path cancels every non-turn task it started.
        ws.hang_up()
        await asyncio.wait_for(conn, timeout=5)
        await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        asyncio.run(scenario())

    assert _failures(caplog) == []
