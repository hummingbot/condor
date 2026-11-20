"""An upstream Hummingbot socket that goes quiet is a dead one (WS-03), and a
reconnect is never immediate (WS-05, second half of this file).

Nothing bounded the receive on the bridged sockets, so a connection whose path
died without a FIN/RST (NAT idle drop, a sleeping host) parked the stream task
forever: no reconnect, no error, and the last frame served to every new
subscriber as if it were live. The upstream heartbeats every 30s, which is
exactly the signal needed. These tests pin the watchdog, the reconnect it
triggers, and the ``stale`` / ``live`` frames that tell the dashboard about it.
"""

import asyncio
import json

import pytest

import condor.web.streams.candles as candles_mod
import condor.web.streams.hummingbot_ws as hb_ws
from condor.web.ws_manager import WebSocketManager, _Connection

CHANNEL = "positions_ws:alpha"
CANDLES = "candles:alpha:binance:SOL-USDC:1m"

HANG = object()
CLOSE = object()


def run(coro):
    return asyncio.run(coro)


class _UpstreamWs:
    """One upstream connection replaying a script of receive() outcomes.

    A dict is delivered, ``HANG`` never returns (the half-open socket) and
    ``CLOSE`` — or running off the end of the script — is the upstream closing.
    """

    def __init__(self, script):
        self._script = list(script)

    async def receive(self):
        step = self._script.pop(0) if self._script else CLOSE
        if step is HANG:
            await asyncio.Event().wait()
        if step is CLOSE:
            raise ConnectionError("WebSocket connection closed")
        return step

    async def subscribe_candles(self, *args, **kwargs):
        return "sub-1"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _Upstream:
    """Hands out one scripted connection per attempt, then cancels the stream."""

    def __init__(self, *scripts):
        self._scripts = list(scripts)
        self.attempts = 0

    def open(self, _client=None):
        self.attempts += 1
        if not self._scripts:
            raise asyncio.CancelledError
        return _UpstreamWs(self._scripts.pop(0))


class _BrowserWs:
    def __init__(self):
        self.frames: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.frames.append(json.loads(payload))


class _Cm:
    def __init__(self, upstream: _Upstream | None = None):
        self._upstream = upstream

    async def get_client(self, name):
        upstream = self._upstream

        class _Client:
            class ws:
                market_data = staticmethod(upstream.open)

        return _Client()

    def get_user_role(self, user_id):
        from config_manager import UserRole

        return UserRole.USER

    def has_server_access(self, user_id, server_name, *args):
        return True


@pytest.fixture
def harness(monkeypatch):
    """A manager with one subscribed tab, instant sleeps and a short watchdog."""
    import config_manager

    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def instant(seconds):
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", instant)
    monkeypatch.setattr(hb_ws, "WS_RECEIVE_TIMEOUT", 0.05)
    monkeypatch.setattr(candles_mod.dex_candles, "uses_gecko_candles", lambda c: False)

    def build(upstream: _Upstream, channel: str = CHANNEL):
        monkeypatch.setattr(config_manager, "get_config_manager", lambda: _Cm(upstream))
        mgr = WebSocketManager()
        tab = _BrowserWs()
        conn = _Connection(tab, user_id=7)
        conn.channels.add(channel)
        mgr._connections.append(conn)
        return mgr, tab, conn

    build.slept = slept
    return build


async def _run_positions(mgr: WebSocketManager, upstream: _Upstream) -> None:
    async def subscribe(_ws):
        return None

    async def on_message(msg):
        if msg.get("type") == "positions":
            await mgr._broadcast_update(CHANNEL, msg.get("data"))

    await mgr._run_ws_stream(
        CHANNEL,
        "alpha",
        label="Positions WS",
        open_ws=upstream.open,
        subscribe=subscribe,
        on_message=on_message,
    )


def _status_frames(tab: _BrowserWs) -> list[dict]:
    return [f["data"] for f in tab.frames if f["channel"] == "stream_status:alpha"]


# -- The watchdog --


def test_a_silent_upstream_socket_is_reconnected(harness):
    """The half-open case: one frame, then nothing, and no close either."""
    upstream = _Upstream([{"type": "positions", "data": [{"id": 1}]}, HANG])
    mgr, tab, _ = harness(upstream)

    run(_run_positions(mgr, upstream))

    assert upstream.attempts == 2, "the stream never left the dead socket"
    assert harness.slept, "it reconnected without backing off"


def test_heartbeats_keep_a_quiet_stream_connected(harness):
    """Silence is measured between messages, and a heartbeat is a message."""
    beats = [{"type": "heartbeat"}] * 5
    upstream = _Upstream([*beats, {"type": "positions", "data": [{"id": 1}]}, HANG])
    mgr, tab, _ = harness(upstream)

    run(_run_positions(mgr, upstream))

    assert [f["data"] for f in tab.frames if f["channel"] == CHANNEL] == [[{"id": 1}]]


def test_a_silent_candle_socket_is_reconnected(harness):
    upstream = _Upstream([HANG])
    mgr, tab, _ = harness(upstream, CANDLES)

    async def main():
        await mgr._candle_stream(CANDLES)
        for task in mgr._candle_poll_tasks.values():
            task.cancel()

    run(main())

    assert upstream.attempts == 2
    errors = [f["data"] for f in tab.frames if f["data"].get("type") == "error"]
    assert errors, "the chart was not told the stream dropped"


# -- Telling the dashboard --


def test_subscribers_are_told_the_data_went_stale(harness):
    upstream = _Upstream([{"type": "positions", "data": [{"id": 1}]}, HANG])
    mgr, tab, _ = harness(upstream)

    run(_run_positions(mgr, upstream))

    assert [(s["type"], s["channel"]) for s in _status_frames(tab)] == [
        ("stale", CHANNEL)
    ]
    assert mgr._last_data[CHANNEL] == [
        {"id": 1}
    ], "the status frame must not replace the channel's last payload"


def test_a_clean_upstream_close_is_stale_too(harness):
    upstream = _Upstream([{"type": "positions", "data": [{"id": 1}]}, CLOSE])
    mgr, tab, _ = harness(upstream)

    run(_run_positions(mgr, upstream))

    assert [s["type"] for s in _status_frames(tab)] == ["stale"]


def test_stale_is_announced_once_and_cleared_when_data_resumes(harness):
    """Three dead connections in a row are one outage, not three."""
    upstream = _Upstream(
        [{"type": "positions", "data": [{"id": 1}]}, HANG],
        [HANG],
        [CLOSE],
        # Identical to the last payload: the data channel sends nothing, so
        # only the status frame can tell the tab the numbers are live again.
        [{"type": "positions", "data": [{"id": 1}]}, HANG],
    )
    mgr, tab, _ = harness(upstream)

    run(_run_positions(mgr, upstream))

    assert [s["type"] for s in _status_frames(tab)] == ["stale", "live", "stale"]


def test_a_tab_subscribing_during_an_outage_is_told_the_snapshot_is_stale(harness):
    upstream = _Upstream()
    mgr, _, _ = harness(upstream)
    mgr._last_data[CHANNEL] = [{"id": 1}]
    mgr._stale_streams.add(CHANNEL)
    mgr._ensure_stream = lambda prefix, channel: None

    late = _BrowserWs()
    conn = _Connection(late, user_id=8)
    mgr._connections.append(conn)

    run(
        mgr.handle_message(
            conn, json.dumps({"action": "subscribe", "channel": CHANNEL})
        )
    )

    assert late.frames[0]["data"] == [{"id": 1}]
    assert late.frames[1]["channel"] == "stream_status:alpha"
    assert late.frames[1]["data"]["type"] == "stale"
    assert late.frames[1]["data"]["channel"] == CHANNEL


# -- Reconnecting after a clean close (WS-05) --
#
# The client turns an upstream close into the end of iteration, not an
# exception, so the except-path backoff never ran: five of the seven streams,
# and candles, redialled a server that accepts-then-drops with no pause at all.


def test_an_upstream_that_accepts_then_closes_is_backed_off(harness):
    upstream = _Upstream(*([[CLOSE]] * 6))
    mgr, _, _ = harness(upstream)

    run(_run_positions(mgr, upstream))

    assert upstream.attempts == 7
    assert harness.slept == [5, 10, 20, 40, 60, 60]


def test_a_heartbeat_only_connection_does_not_reset_the_backoff(harness):
    """Being subscribed and heartbeating says nothing about the stream."""
    upstream = _Upstream(*([[{"type": "heartbeat"}, CLOSE]] * 4))
    mgr, _, _ = harness(upstream)

    run(_run_positions(mgr, upstream))

    assert harness.slept == [5, 10, 20, 40]


def test_an_upstream_error_frame_is_backed_off(harness):
    """A subscription erroring every tick takes the same no-exception exit."""
    upstream = _Upstream(*([[{"type": "error", "message": "boom"}]] * 3))
    mgr, _, _ = harness(upstream)

    run(_run_positions(mgr, upstream))

    assert harness.slept == [5, 10, 20]


def test_a_close_after_real_data_still_pauses_but_resets_the_backoff(harness):
    upstream = _Upstream(
        [CLOSE],
        [CLOSE],
        [{"type": "positions", "data": [{"id": 1}]}, CLOSE],
        [CLOSE],
    )
    mgr, _, _ = harness(upstream)

    run(_run_positions(mgr, upstream))

    first, second, after_data, after_reset = harness.slept
    assert (first, second) == (5, 10)
    assert 1 <= after_data <= 2, "a healthy stream reconnects on a short floor"
    assert after_reset == 5, "real data did not reset the backoff"


@pytest.mark.parametrize(
    "stream, channel, kind",
    [
        ("_trade_stream", "trades:alpha:binance:SOL-USDC", "market_data"),
        ("_order_book_stream", "orderbook:alpha:binance:SOL-USDC", "market_data"),
        ("_bots_ws_stream", "bots_ws:alpha", "executors"),
        ("_positions_ws_stream", "positions_ws:alpha", "executors"),
        ("_performance_ws_stream", "performance_ws:alpha", "executors"),
        ("_candle_stream", CANDLES, "market_data"),
    ],
)
def test_every_stream_pauses_between_attempts(
    harness, monkeypatch, stream, channel, kind
):
    """Each stream family, through its real entry point."""

    class _AnyWs(_UpstreamWs):
        def __getattr__(self, name):
            if not name.startswith("subscribe_"):
                raise AttributeError(name)

            async def _subscribe(*args, **kwargs):
                return "sub-1"

            return _subscribe

    class _AnyUpstream(_Upstream):
        def open(self, _client=None):
            self.attempts += 1
            if not self._scripts:
                raise asyncio.CancelledError
            return _AnyWs(self._scripts.pop(0))

    upstream = _AnyUpstream([CLOSE], [CLOSE], [CLOSE])
    mgr, _, _ = harness(upstream, channel)

    class _Client:
        class ws:
            market_data = staticmethod(upstream.open)
            executors = staticmethod(upstream.open)

    import config_manager

    cm = config_manager.get_config_manager()

    async def get_client(name):
        return _Client()

    cm.get_client = get_client
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: cm)

    # The candle REST fallback sleeps on its own schedule beside the socket.
    mgr._ensure_candle_poll_fallback = lambda channel: None

    run(getattr(mgr, stream)(channel))

    assert upstream.attempts == 4
    assert harness.slept[:3] == [5, 10, 20], f"{stream} redialled without a pause"
