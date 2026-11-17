"""A connection the manager already dropped cannot start a subscription (review R5).

``_fan_out`` disconnects a subscriber whose send timed out and closes its
socket in the background. A ``subscribe`` the client had queued before the
close was still processed: it started the SDS poll for a connection no longer
in ``_connections``, and the ``disconnect`` that followed the close was then a
no-op -- so the poll for that channel never stopped.
"""

import asyncio
import json

import pytest
from fastapi import WebSocketDisconnect

from condor.web import ws_manager as ws_module
from condor.web.ws_manager import WebSocketManager, _Connection

USER = 4242
CHANNEL = "portfolio:main"


class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(raw)

    async def close(self, code=1000, reason="") -> None:
        return None


@pytest.fixture
def manager(monkeypatch):
    from config_manager import get_config_manager

    cls = type(get_config_manager())
    monkeypatch.setattr(cls, "has_server_access", lambda self, uid, name, *a: True)
    monkeypatch.setattr(ws_module, "is_active_user", lambda uid: True)

    mgr = WebSocketManager()
    started: list[str] = []
    monkeypatch.setattr(mgr, "_start_sds_subscription", started.append)
    mgr.started = started
    return mgr


def _subscribe() -> str:
    return json.dumps({"action": "subscribe", "channel": CHANNEL})


def test_a_registered_connection_still_subscribes(manager):
    conn = _Connection(_FakeWS(), USER)
    manager._connections.append(conn)

    asyncio.run(manager.handle_message(conn, _subscribe()))

    assert manager.started == [CHANNEL]
    assert CHANNEL in conn.channels


def test_a_dropped_connection_cannot_start_a_subscription(manager):
    conn = _Connection(_FakeWS(), USER)
    manager._connections.append(conn)
    manager.disconnect(conn)  # what the send-timeout path does

    with pytest.raises(WebSocketDisconnect):
        asyncio.run(manager.handle_message(conn, _subscribe()))

    assert manager.started == []
    assert CHANNEL not in conn.channels


def test_a_connection_dropped_during_the_snapshot_send_starts_nothing(manager):
    """The drop can also land while the subscribe is awaiting its first send."""

    class _DropsMidSend(_FakeWS):
        async def send_text(self, raw: str) -> None:
            manager.disconnect(conn)

    conn = _Connection(_DropsMidSend(), USER)
    manager._connections.append(conn)
    manager._last_data[CHANNEL] = {"total_usd": 1}

    with pytest.raises(WebSocketDisconnect):
        asyncio.run(manager.handle_message(conn, _subscribe()))

    assert manager.started == []
