"""A stream dies for good only when retrying really cannot help (CORR-279).

The verdict used to be reached by looking for "401", "403" or "404" anywhere in
the stringified exception. A stream's error text is full of numbers that are
not statuses — the trading pair, the connection id, a timestamp, the port — so
an ordinary transient failure could read as an authorization refusal and kill
the stream with no retry, and the candle stream tore down its REST poll
fallback on the same signal. These tests pin the classification to the status
aiohttp actually puts on a refused handshake, and pin the fallback to staying
up.
"""

import asyncio

import aiohttp
import pytest
from multidict import CIMultiDict
from yarl import URL

import condor.web.streams.candles as candles_mod
from condor.web.streams.hummingbot_ws import (
    WS_PERMANENT,
    WS_RETRY,
    WS_RETRY_CAP,
    WS_SLOW_RETRY,
    WS_SLOW_RETRY_CAP,
)
from condor.web.ws_manager import WebSocketManager

CHANNEL = "candles:alpha:binance:SOL-USDC:1m"


def run(coro):
    return asyncio.run(coro)


def handshake_error(status: int) -> aiohttp.WSServerHandshakeError:
    """The exception aiohttp raises when the upstream refuses the upgrade."""
    url = URL("http://api.internal:8000/ws/market-data")
    info = aiohttp.RequestInfo(url, "GET", CIMultiDict(), url)
    return aiohttp.WSServerHandshakeError(
        info, (), status=status, message="Invalid API credentials"
    )


# -- Classification --


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_refused_handshake_is_retried_slowly_not_abandoned(status):
    """Credentials are rotated by hand, so a refusal has to be able to heal.

    Giving up meant a key fixed five seconds later took effect only at the next
    Condor restart.
    """
    assert WebSocketManager._classify_ws_error(handshake_error(status)) == WS_SLOW_RETRY


def test_a_handshake_that_failed_for_another_reason_is_an_ordinary_retry():
    assert WebSocketManager._classify_ws_error(handshake_error(500)) == WS_RETRY


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionResetError("Cannot connect to host api.internal:8404 ssl:default"),
        RuntimeError("Subscribe failed: no feed for MEME404-USDT on binance"),
        TimeoutError("connection 401f3c timed out after 1735689600"),
        RuntimeError("WebSocket auth failed: token expired at 1735689403"),
    ],
    ids=["port", "pair", "connection-id", "timestamp"],
)
def test_a_digit_bearing_message_is_not_a_refusal(exc):
    """None of these was refused; every one used to be killed as permanent."""
    assert any(
        code in str(exc) for code in ("401", "403", "404")
    ), "the case only bites if the old substring rule would have fired on it"
    assert WebSocketManager._classify_ws_error(exc) == WS_RETRY


@pytest.mark.parametrize(
    "text",
    [
        "Trading pair SOLL-USDC appears to be invalid for binance",
        "Invalid symbol XYZ",
    ],
)
def test_an_unknown_trading_pair_stays_permanent(text):
    """A wrong symbol is the one failure no retry can fix (issue #134)."""
    assert WebSocketManager._classify_ws_error(RuntimeError(text)) == WS_PERMANENT


# -- The shared skeleton acts on the verdict --


def _drive_skeleton(monkeypatch, exc, attempts_before_cancel):
    """Run `_run_ws_stream` against a connection that always raises `exc`.

    Returns the attempts made and every backoff it slept.
    """
    attempts: list[int] = []
    slept: list[float] = []

    class _Cm:
        async def get_client(self, name):
            return object()

    import config_manager

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _Cm())

    real_sleep = asyncio.sleep

    async def instant(seconds):
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", instant)

    def open_ws(_client):
        attempts.append(1)
        if len(attempts) > attempts_before_cancel:
            raise asyncio.CancelledError
        raise exc

    async def subscribe(_ws):  # pragma: no cover - never reached
        return None

    async def on_message(_msg):  # pragma: no cover - never reached
        return None

    mgr = WebSocketManager()
    run(
        mgr._run_ws_stream(
            "bots:alpha",
            "alpha",
            label="Bots",
            open_ws=open_ws,
            subscribe=subscribe,
            on_message=on_message,
        )
    )
    return attempts, slept


def test_a_digit_bearing_failure_reconnects_instead_of_dying(monkeypatch):
    """The bug in the field: one unlucky error message and the tab froze."""
    exc = ConnectionResetError("Cannot connect to host api.internal:8404")
    attempts, slept = _drive_skeleton(monkeypatch, exc, attempts_before_cancel=12)

    assert len(attempts) == 13, "the stream gave up instead of reconnecting"
    assert max(slept) == WS_RETRY_CAP


def test_a_refusal_reconnects_on_a_longer_leash(monkeypatch):
    attempts, slept = _drive_skeleton(
        monkeypatch, handshake_error(401), attempts_before_cancel=12
    )

    assert len(attempts) == 13
    assert max(slept) == WS_SLOW_RETRY_CAP
    assert max(slept) > WS_RETRY_CAP


def test_an_unknown_trading_pair_still_stops_the_stream(monkeypatch):
    attempts, slept = _drive_skeleton(
        monkeypatch,
        RuntimeError("Trading pair SOLL-USDC appears to be invalid"),
        attempts_before_cancel=12,
    )

    assert attempts == [1], "a wrong symbol must not be retried forever"
    assert slept == []


# -- The candle REST fallback outlives the socket --


def _drive_candle_stream(monkeypatch, exc):
    """Run `_candle_stream` once against a client that raises `exc`.

    Returns the manager and the poll task that was running beside the socket.
    """

    class _Cm:
        async def get_client(self, name):
            raise exc

    import config_manager

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _Cm())
    monkeypatch.setattr(candles_mod.dex_candles, "uses_gecko_candles", lambda c: False)

    real_sleep = asyncio.sleep

    async def instant(_seconds):
        # Anything that reconnects would loop forever here; only the permanent
        # verdict reaches the end of this stream.
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", instant)

    mgr = WebSocketManager()

    async def main():
        async def poll_forever():
            await real_sleep(30)

        poll = asyncio.create_task(poll_forever())
        mgr._candle_poll_tasks[CHANNEL] = poll
        await mgr._candle_stream(CHANNEL)
        # Read the task's fate before the loop closes and cancels it for us.
        return poll, poll.cancelled(), poll.done()

    poll, cancelled, done = run(main())
    return mgr, poll, cancelled, done


def test_the_rest_fallback_outlives_a_candle_socket_given_up_on(monkeypatch):
    """The socket is dead; REST is a different endpoint and may still work.

    Cancelling the poll used to remove the one mechanism that could have kept
    the chart drawing.
    """
    mgr, poll, cancelled, done = _drive_candle_stream(
        monkeypatch, RuntimeError("Trading pair SOLL-USDC appears to be invalid")
    )

    assert mgr._candle_poll_tasks.get(CHANNEL) is poll, "the fallback was dropped"
    assert not cancelled and not done, "the fallback was cancelled with the socket"


def test_a_digit_bearing_candle_failure_does_not_end_the_stream(monkeypatch):
    """It reconnects, so it never reaches the give-up branch at all."""
    with pytest.raises(asyncio.CancelledError):
        _drive_candle_stream(
            monkeypatch, ConnectionResetError("Cannot connect to host api:8404")
        )
