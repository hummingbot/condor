"""A dashboard socket loses a server's frames the moment access is revoked (SEC-592).

`handle_message` gates a subscription once, at subscribe time (the SEC-019 fix),
and `broadcast` used to fan out purely on channel membership. A dashboard socket
is long-lived and auto-reconnecting, so an owner revoking a trader's share — or
an admin blocking the user — did not take effect until the tab reloaded: the
connection kept receiving `portfolio:<server>` / `bots_ws:<server>` /
`executors:<server>` payloads, possibly for the rest of the day. Every REST route
re-checks per request; this pins the same behaviour on the socket.
"""

import asyncio
import json

import pytest

from condor.web import ws_manager as ws_manager_module
from condor.web.ws_manager import WebSocketManager, _Connection


class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, raw: str) -> None:
        self.sent.append(raw)

    async def close(self, **kwargs) -> None:  # pragma: no cover - must not run
        self.closed = True

    def channels(self) -> list[str]:
        return [json.loads(raw)["channel"] for raw in self.sent]


class _FakeCM:
    """The two dicts the real `ConfigManager` consults, and nothing else."""

    def __init__(self, shares: dict[int, set[str]], roles: dict[int, object]):
        self._shares = shares
        self._roles = roles

    def get_user_role(self, user_id: int):
        return self._roles.get(user_id)

    def has_server_access(self, user_id: int, server_name: str, *_a, **_kw) -> bool:
        return server_name in self._shares.get(user_id, set())


@pytest.fixture
def cm(monkeypatch):
    import config_manager

    manager = _FakeCM(
        shares={1: {"srv"}, 2: {"srv"}},
        roles={1: config_manager.UserRole.USER, 2: config_manager.UserRole.USER},
    )
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: manager)
    return manager


def _manager_with(channel: str, *conns: _Connection) -> WebSocketManager:
    manager = WebSocketManager()
    for conn in conns:
        conn.channels.add(channel)
        manager._connections.append(conn)
    return manager


def _conn(user_id: int) -> _Connection:
    return _Connection(_FakeWS(), user_id=user_id)


def test_revoked_share_stops_the_frames_without_a_reconnect(cm):
    """The regression: the same open socket, before and after the revoke."""
    keeps, loses = _conn(1), _conn(2)
    manager = _manager_with("portfolio:srv", keeps, loses)

    asyncio.run(manager.broadcast("portfolio:srv", {"total": 1}))
    assert len(loses.ws.sent) == 1, "subscribe-time access should deliver the frame"

    cm._shares[2] = set()  # owner revokes the share; the socket stays open

    asyncio.run(manager.broadcast("portfolio:srv", {"total": 2}))

    assert len(loses.ws.sent) == 1, "revoked subscriber still receiving broadcasts"
    assert len(keeps.ws.sent) == 2, "a subscriber that kept access lost its stream"


def test_blocking_the_user_stops_the_frames_too(cm):
    """A blocked user keeps their `shared_with` entry, so the role half matters."""
    import config_manager

    blocked = _conn(2)
    manager = _manager_with("portfolio:srv", blocked)

    asyncio.run(manager.broadcast("portfolio:srv", {"total": 1}))
    cm._roles[2] = config_manager.UserRole.BLOCKED

    asyncio.run(manager.broadcast("portfolio:srv", {"total": 2}))

    assert len(blocked.ws.sent) == 1, "blocked user still receiving broadcasts"


def test_revoked_subscription_is_dropped_and_the_stream_stops(cm):
    """Dropping the last subscriber must release the upstream stream too."""

    async def scenario():
        loses = _conn(2)
        manager = _manager_with("executors:srv", loses)

        async def forever():
            await asyncio.Event().wait()

        task = asyncio.create_task(forever())
        manager._executor_tasks["executors:srv"] = task

        cm._shares[2] = set()
        await manager.broadcast("executors:srv", [{"id": "e1"}])

        assert "executors:srv" not in loses.channels
        assert "executors:srv" not in manager._executor_tasks
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()
        assert not loses.ws.closed, "the socket itself must stay open"

    asyncio.run(scenario())


def test_the_socket_keeps_its_other_channels(cm):
    """Revocation is per server: it unsubscribes a channel, it does not disconnect."""
    conn = _conn(2)
    cm._shares[2] = {"srv", "other"}
    manager = _manager_with("portfolio:srv", conn)
    conn.channels.add("portfolio:other")

    cm._shares[2] = {"other"}
    asyncio.run(manager.broadcast("portfolio:srv", {"total": 2}))
    asyncio.run(manager.broadcast("portfolio:other", {"total": 3}))

    assert conn.ws.channels() == ["portfolio:other"]
    assert conn in manager._connections
    assert conn.channels == {"portfolio:other"}


def test_the_frame_is_still_encoded_once_for_the_survivors(cm, monkeypatch):
    """PERF-210 preserved: the re-check must not push encoding per connection."""
    conns = [_conn(1), _conn(1), _conn(2)]
    manager = _manager_with("portfolio:srv", *conns)
    cm._shares[2] = set()

    calls = []
    real_dumps = json.dumps

    def counting_dumps(obj, **kwargs):
        calls.append(obj)
        return real_dumps(obj, **kwargs)

    monkeypatch.setattr(ws_manager_module.json, "dumps", counting_dumps)
    asyncio.run(manager.broadcast("portfolio:srv", {"total": 1}))

    assert len(calls) == 1, f"payload encoded {len(calls)} times"
    assert [len(c.ws.sent) for c in conns] == [1, 1, 0]


def test_the_check_is_memoised_per_user_not_per_connection(cm):
    """Six tabs of the same user cost one permission lookup per frame, not six."""
    lookups: list[int] = []
    inner = cm.has_server_access

    def counting(user_id, server_name, *a, **kw):
        lookups.append(user_id)
        return inner(user_id, server_name, *a, **kw)

    cm.has_server_access = counting
    manager = _manager_with("portfolio:srv", *[_conn(1) for _ in range(6)])

    asyncio.run(manager.broadcast("portfolio:srv", {"total": 1}))

    assert lookups == [1], f"one frame cost {len(lookups)} permission lookups"
