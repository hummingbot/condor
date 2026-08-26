"""One broadcast encodes its frame once, not once per subscriber (PERF-210).

`broadcast` used to call Starlette's `send_json` per connection, and that helper
runs `json.dumps` on every call — so the identical executor/bots payload was
re-encoded N times for N dashboard tabs on the one shared event loop. The frame
carries no per-connection state, so it is built once and fanned out as text.
"""

import asyncio
import json

import pytest

from condor.web import ws_manager as ws_manager_module
from condor.web.ws_manager import WebSocketManager, _Connection


class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(raw)


class _BoomWS(_FakeWS):
    async def send_text(self, raw: str) -> None:
        raise RuntimeError("socket is gone")


def _manager_with(channel: str, *sockets) -> WebSocketManager:
    manager = WebSocketManager()
    for i, ws in enumerate(sockets):
        conn = _Connection(ws, user_id=i)
        conn.channels.add(channel)
        manager._connections.append(conn)
    return manager


def test_broadcast_encodes_once_for_many_subscribers(monkeypatch):
    sockets = [_FakeWS(), _FakeWS(), _FakeWS()]
    manager = _manager_with("executors:srv", *sockets)

    calls = []
    real_dumps = json.dumps

    def counting_dumps(obj, **kwargs):
        calls.append(obj)
        return real_dumps(obj, **kwargs)

    monkeypatch.setattr(ws_manager_module.json, "dumps", counting_dumps)

    asyncio.run(manager.broadcast("executors:srv", [{"id": "e1"}]))

    assert len(calls) == 1, f"payload encoded {len(calls)} times for 3 connections"


def test_every_subscriber_gets_the_identical_frame():
    sockets = [_FakeWS(), _FakeWS(), _FakeWS()]
    manager = _manager_with("executors:srv", *sockets)

    asyncio.run(manager.broadcast("executors:srv", [{"id": "e1", "pnl": 1.5}]))

    frames = [ws.sent[0] for ws in sockets]
    assert frames[0] == frames[1] == frames[2], "recipients got divergent frames"
    # Same wire format Starlette's send_json produces, ts shared across recipients.
    assert frames[0].startswith('{"channel":"executors:srv","data":[{"id":"e1"')
    decoded = json.loads(frames[0])
    assert decoded["data"] == [{"id": "e1", "pnl": 1.5}]
    assert isinstance(decoded["ts"], float)


def test_one_failing_connection_does_not_starve_the_others(caplog):
    good_a, bad, good_b = _FakeWS(), _BoomWS(), _FakeWS()
    manager = _manager_with("executors:srv", good_a, bad, good_b)

    with caplog.at_level("WARNING", logger="condor.web.ws_manager"):
        asyncio.run(manager.broadcast("executors:srv", {"ok": True}))

    assert len(good_a.sent) == 1 and len(good_b.sent) == 1
    assert "Broadcast send failed" in caplog.text
    # The broken connection is reaped, the healthy ones survive.
    assert [c.ws for c in manager._connections] == [good_a, good_b]


def test_unserializable_payload_is_logged_and_does_not_escape(caplog):
    ws = _FakeWS()
    manager = _manager_with("executors:srv", ws)

    class NotJSON:
        pass

    with caplog.at_level("WARNING", logger="condor.web.ws_manager"):
        asyncio.run(manager.broadcast("executors:srv", NotJSON()))

    assert "Broadcast encode failed" in caplog.text
    assert ws.sent == [], "a bad payload must not reach the socket"
    # The caller (a long-lived stream task) is not killed by the failure.
    assert [c.ws for c in manager._connections] == [ws]


def test_snapshot_send_uses_the_same_encoder():
    """`_send` stays available for the single-connection snapshot path and must
    produce the exact same frame shape as a broadcast."""
    ws = _FakeWS()
    manager = _manager_with("candles:srv", ws)
    conn = manager._connections[0]

    asyncio.run(manager._send(conn, "candles:srv", {"type": "candles"}))

    decoded = json.loads(ws.sent[0])
    assert decoded["channel"] == "candles:srv"
    assert decoded["data"] == {"type": "candles"}
    assert "ts" in decoded


@pytest.mark.parametrize("payload", ["ünïcode", {"k": "ünïcode"}])
def test_non_ascii_is_not_escaped(payload):
    """Starlette sends `ensure_ascii=False`; the shared encoder must match."""
    ws = _FakeWS()
    manager = _manager_with("bots:srv", ws)

    asyncio.run(manager.broadcast("bots:srv", payload))

    assert "ünïcode" in ws.sent[0]
