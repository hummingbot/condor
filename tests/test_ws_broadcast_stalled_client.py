"""One stalled tab must not freeze a channel for everyone else (WS-02 stop-gap).

``broadcast`` returns when its slowest ``send_text`` returns, and it is awaited
inside the single task that reads the upstream socket. A browser that stops
reading with TCP still open blocks that send once the write buffer fills, so
the stream task read nothing more — for every subscriber — until the keepalive
ping killed the connection. Each send is now bounded and a subscriber that
misses the bound is dropped.
"""

import asyncio
import json

import pytest

import condor.web.ws_manager as ws_manager_mod
from condor.web.ws_manager import WebSocketManager, _Connection

CHANNEL = "positions_ws:srv"


class _Tab:
    def __init__(self, stalled: bool = False):
        self.stalled = stalled
        self.frames: list = []
        self.closed_with: int | None = None

    async def send_text(self, payload: str) -> None:
        if self.stalled:
            await asyncio.Event().wait()
        self.frames.append(json.loads(payload)["data"])

    async def close(self, code=1000, reason="") -> None:
        if self.stalled:
            await asyncio.Event().wait()
        self.closed_with = code


class _Cm:
    def get_user_role(self, user_id):
        from config_manager import UserRole

        return UserRole.USER

    def has_server_access(self, user_id, server_name, *args):
        return True


@pytest.fixture
def mgr(monkeypatch):
    import config_manager

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _Cm())
    monkeypatch.setattr(ws_manager_mod, "WS_SEND_TIMEOUT", 0.05)
    return WebSocketManager()


def _subscribe(mgr: WebSocketManager, tab: _Tab, user_id: int) -> _Connection:
    conn = _Connection(tab, user_id)
    conn.channels.add(CHANNEL)
    mgr._connections.append(conn)
    return conn


def test_a_stalled_tab_does_not_block_the_broadcast(mgr):
    healthy, stalled = _Tab(), _Tab(stalled=True)
    _subscribe(mgr, healthy, 1)
    stalled_conn = _subscribe(mgr, stalled, 2)

    async def scenario():
        await asyncio.wait_for(mgr.broadcast(CHANNEL, [{"id": 1}]), timeout=2)
        await asyncio.wait_for(mgr.broadcast(CHANNEL, [{"id": 2}]), timeout=2)
        await asyncio.gather(*mgr._oneshot_tasks)

    asyncio.run(scenario())

    assert healthy.frames == [[{"id": 1}], [{"id": 2}]]
    assert stalled_conn not in mgr._connections, "the stalled tab was kept"
    assert stalled_conn.channels == set()


def test_a_tab_that_was_only_slow_is_closed_so_it_reconnects(mgr):
    """Unsubscribed but left open, it would show frozen data with no error."""

    class _SlowOnce(_Tab):
        async def send_text(self, payload: str) -> None:
            await asyncio.Event().wait()

    slow = _SlowOnce()
    conn = _subscribe(mgr, slow, 2)

    async def scenario():
        await mgr.broadcast(CHANNEL, [{"id": 1}])
        await asyncio.gather(*mgr._oneshot_tasks)

    asyncio.run(scenario())

    assert conn not in mgr._connections
    assert slow.closed_with == 1013


def test_a_prompt_subscriber_is_untouched(mgr):
    tab = _Tab()
    conn = _subscribe(mgr, tab, 1)

    asyncio.run(mgr.broadcast(CHANNEL, [{"id": 1}]))

    assert conn in mgr._connections
    assert tab.frames == [[{"id": 1}]]
    assert tab.closed_with is None
