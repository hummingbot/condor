"""Subscribing must not park the socket's receive loop on an upstream fetch (WS-12).

An SDS-backed channel (``portfolio``, ``bots``, ``prices``) was subscribed with
an ``await`` on the cache prime, inside the loop that reads the client's
messages. On a cold key — a new pair, or a server that is down and costing a
connect timeout — every later message on that socket waited behind it.
"""

import asyncio
import json

import pytest

import condor.web.ws_manager as ws_manager_mod
from condor.server_data_service import CacheKey, ServerDataType
from condor.web.ws_manager import WebSocketManager, _Connection

pytestmark = pytest.mark.usefixtures("ws_access_granted")

PRICES = "prices:srv:binance:SOL-USDC"
KEY = CacheKey.make(
    "srv", ServerDataType.PRICES, connector_name="binance", trading_pair="SOL-USDC"
)


class _Tab:
    def __init__(self):
        self.frames: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.frames.append(json.loads(raw))


class _SlowSDS:
    """An SDS whose prime blocks until released, like a server that is down."""

    def __init__(self):
        self.release = asyncio.Event()
        self.subscribed: list[CacheKey] = []
        self.unsubscribed: list[CacheKey] = []
        self.value = {"price": 150.0}

    async def subscribe(self, server, data_type, subscriber_id, **params):
        key = CacheKey.make(server, data_type, **params)
        self.subscribed.append(key)
        await self.release.wait()
        return key

    def unsubscribe(self, key, subscriber_id):
        self.unsubscribed.append(key)

    def get(self, server, data_type, **params):
        return self.value


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(ws_manager_mod, "is_active_user", lambda user_id: True)

    def build():
        sds = _SlowSDS()
        monkeypatch.setattr(ws_manager_mod, "get_server_data_service", lambda: sds)
        mgr = WebSocketManager()
        started: list[str] = []
        mgr._ensure_stream = lambda prefix, channel: started.append(channel)
        tab = _Tab()
        conn = _Connection(tab, user_id=1)
        mgr._connections.append(conn)
        return mgr, sds, tab, conn, started

    return build


async def _say(mgr, conn, action, channel):
    await mgr.handle_message(conn, json.dumps({"action": action, "channel": channel}))


def test_a_later_message_is_not_queued_behind_a_cold_prime(world):
    async def scenario():
        mgr, sds, tab, conn, started = world()

        await asyncio.wait_for(_say(mgr, conn, "subscribe", PRICES), timeout=1)
        await asyncio.wait_for(
            _say(mgr, conn, "subscribe", "positions_ws:srv"), timeout=1
        )

        assert started == ["positions_ws:srv"]
        assert PRICES in conn.channels
        assert mgr._sds_subscriptions[PRICES] == KEY, "not registered synchronously"
        assert tab.frames == []

        sds.release.set()
        await asyncio.gather(*mgr._oneshot_tasks)
        return tab

    tab = asyncio.run(scenario())

    assert [(f["channel"], f["data"]) for f in tab.frames] == [
        (PRICES, {"price": 150.0})
    ], "the primed snapshot never reached the subscriber"


def test_a_second_subscriber_does_not_start_a_second_prime(world):
    async def scenario():
        mgr, sds, tab, conn, _ = world()
        other = _Connection(_Tab(), user_id=1)
        mgr._connections.append(other)

        await _say(mgr, conn, "subscribe", PRICES)
        await _say(mgr, other, "subscribe", PRICES)
        await asyncio.sleep(0)
        subscribed = list(sds.subscribed)

        sds.release.set()
        await asyncio.gather(*mgr._oneshot_tasks)
        return subscribed

    assert asyncio.run(scenario()) == [KEY]


def test_unsubscribing_mid_prime_does_not_leave_the_poll_running(world):
    async def scenario():
        mgr, sds, tab, conn, _ = world()

        await _say(mgr, conn, "subscribe", PRICES)
        await asyncio.sleep(0)  # the prime is now in flight
        await _say(mgr, conn, "unsubscribe", PRICES)
        assert PRICES not in mgr._sds_subscriptions

        sds.release.set()
        await asyncio.gather(*mgr._oneshot_tasks)
        return mgr, sds, tab

    mgr, sds, tab = asyncio.run(scenario())

    # Once when the client left, and again when the prime landed on a channel
    # nobody holds any more — the one that actually stops the poll.
    assert sds.unsubscribed == [KEY, KEY]
    assert tab.frames == []
    assert PRICES not in mgr._sds_subscriptions


def test_a_failed_subscribe_is_forgotten_so_the_next_one_retries(world):
    async def scenario():
        mgr, sds, tab, conn, _ = world()

        async def boom(server, data_type, subscriber_id, **params):
            raise RuntimeError("unknown data type")

        sds.subscribe = boom
        await _say(mgr, conn, "subscribe", PRICES)
        await asyncio.gather(*mgr._oneshot_tasks)
        return mgr

    assert PRICES not in asyncio.run(scenario())._sds_subscriptions
