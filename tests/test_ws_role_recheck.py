"""Both dashboard sockets re-check the user's role on every action (WS-04).

REST re-reads the role per request, and the data socket re-checks server access
per broadcast, but each socket checked the *role* only at the handshake. A user
blocked or demoted while a tab was open kept an authenticated chat socket —
the surface that starts sessions, prompts agents holding trading tools and
approves their tool calls — until the tab was closed.
"""

import asyncio
import json

import pytest
from fastapi import WebSocketDisconnect

from condor.web import auth
from condor.web.routes import chat_ws
from condor.web.ws_manager import WebSocketManager, _Connection

USER = 4242


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed_with: int | None = None
        self._inbox: asyncio.Queue = asyncio.Queue()

    async def accept(self, subprotocol=None) -> None:
        return None

    async def close(self, code=1000, reason="") -> None:
        self.closed_with = code

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def receive_text(self) -> str:
        raw = await self._inbox.get()
        if raw is None:
            raise WebSocketDisconnect(code=1001)
        return raw

    def feed(self, frame: dict) -> None:
        self._inbox.put_nowait(json.dumps(frame))

    def hang_up(self) -> None:
        self._inbox.put_nowait(None)


@pytest.fixture
def roles(monkeypatch):
    """A mutable role table behind the real ConfigManager's lookups."""
    from config_manager import UserRole, get_config_manager

    table = {USER: UserRole.USER}
    cls = type(get_config_manager())
    monkeypatch.setattr(cls, "get_user_role", lambda self, uid: table.get(uid))
    monkeypatch.setattr(cls, "has_server_access", lambda self, uid, name, *a: True)
    return table


# -- The shared gate --


@pytest.mark.parametrize("role", ["USER", "ADMIN"])
def test_an_approved_role_is_active(roles, role):
    from config_manager import UserRole

    roles[USER] = UserRole[role]
    assert auth.is_active_user(USER) is True


@pytest.mark.parametrize("role", ["BLOCKED", "PENDING", None])
def test_any_other_role_is_not(roles, role):
    from config_manager import UserRole

    roles[USER] = UserRole[role] if role else None
    assert auth.is_active_user(USER) is False


def test_a_failing_role_lookup_denies(monkeypatch):
    import config_manager

    class _Broken:
        def get_user_role(self, user_id):
            raise RuntimeError("config.yml is being rewritten")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _Broken())
    assert auth.is_active_user(USER) is False


# -- The chat socket --


@pytest.fixture
def chat_env(monkeypatch, roles):
    monkeypatch.setattr(chat_ws, "_attached_sockets", {})
    monkeypatch.setattr(chat_ws, "_orphaned_turns", set())
    monkeypatch.setattr(chat_ws, "extract_ws_token", lambda ws, token: ("t", None))
    monkeypatch.setattr(chat_ws, "decode_jwt", lambda token: {"sub": USER})

    async def _no_sessions(*args, **kwargs):
        return []

    monkeypatch.setattr(chat_ws.runtime, "list_sessions", _no_sessions)

    handled: list[str] = []

    def _recorder(name):
        async def handler(*args, **kwargs):
            handled.append(name)

        return handler

    for name in (
        "_handle_start_session",
        "_handle_resume_conversation",
        "_handle_send_message",
        "_handle_destroy_session",
        "_handle_resolve_permission",
        "_handle_abort_prompt",
    ):
        monkeypatch.setattr(chat_ws, name, _recorder(name))
    return handled


@pytest.mark.parametrize(
    "action",
    ["send_message", "start_session", "resume_conversation", "resolve_permission"],
)
def test_a_user_blocked_mid_session_cannot_act_on_the_chat_socket(
    chat_env, roles, action
):
    from config_manager import UserRole

    async def scenario():
        ws = _FakeWS()
        conn = asyncio.create_task(chat_ws.chat_websocket(ws, token="t"))
        ws.feed({"action": "list_sessions"})
        for _ in range(200):
            if len(ws.sent) >= 2:
                break
            await asyncio.sleep(0.005)
        assert len(ws.sent) == 2, "an approved user's action was refused"

        roles[USER] = UserRole.BLOCKED
        ws.feed({"action": action, "text": "buy everything"})
        await asyncio.wait_for(conn, timeout=5)
        return ws

    ws = asyncio.run(scenario())

    assert chat_env == [], "a blocked user's action reached its handler"
    assert ws.closed_with == 4003
    assert USER not in chat_ws._attached_sockets


def test_an_approved_user_keeps_working(chat_env, roles):
    async def scenario():
        ws = _FakeWS()
        conn = asyncio.create_task(chat_ws.chat_websocket(ws, token="t"))
        ws.feed({"action": "send_message", "text": "hi"})
        for _ in range(200):
            if chat_env:
                break
            await asyncio.sleep(0.005)
        ws.hang_up()
        await asyncio.wait_for(conn, timeout=5)
        return ws

    ws = asyncio.run(scenario())

    assert chat_env == ["_handle_send_message"]
    assert ws.closed_with is None


# -- The data socket --


def test_a_user_blocked_mid_session_is_dropped_from_the_data_socket(roles):
    from config_manager import UserRole

    mgr = WebSocketManager()
    mgr._ensure_stream = lambda prefix, channel: None
    ws = _FakeWS()
    conn = _Connection(ws, USER)
    mgr._connections.append(conn)

    async def scenario():
        await mgr.handle_message(
            conn, json.dumps({"action": "subscribe", "channel": "positions_ws:srv"})
        )
        assert conn.channels == {"positions_ws:srv"}

        roles[USER] = UserRole.BLOCKED
        with pytest.raises(WebSocketDisconnect):
            await mgr.handle_message(
                conn, json.dumps({"action": "subscribe", "channel": "executors:srv"})
            )

    asyncio.run(scenario())

    assert ws.closed_with == 4003
    assert conn not in mgr._connections
    assert conn.channels == set(), "the blocked user's subscriptions survived"
