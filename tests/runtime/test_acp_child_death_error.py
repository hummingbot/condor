"""A dead ACP child must surface as an error, not as a cancellation (CORR-329).

When the agent command cannot run -- bridge not installed, wrong node, a shell
printing "command not found" -- stdout hits EOF and the read loop sweeps the
pending futures on its way out. Sweeping them with ``cancel()`` raised
``CancelledError`` into the handshake, which is a ``BaseException``: the
``except Exception`` guard in ``start()`` did not catch it, so the subprocess it
promises to reap was left running, and every caller between here and the user
read the failure as "the user pressed Stop".
"""

import asyncio
import json

import pytest

from condor.acp.client import ACPClient
from condor.acp.jsonrpc import JSONRPCPeer

_MISSING = "condor-definitely-not-a-real-acp-binary-xyz"


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[dict] = []

    def write(self, data: bytes) -> None:
        self.written.append(json.loads(data.decode()))

    async def drain(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, stdout: asyncio.StreamReader) -> None:
        self.stdout = stdout
        self.stdin = _FakeStdin()
        self.returncode = None


def _client(stdout: asyncio.StreamReader) -> ACPClient:
    client = ACPClient(command="true")
    client._process = _FakeProcess(stdout)  # type: ignore[assignment]
    return client


@pytest.mark.asyncio
async def test_a_command_that_cannot_run_fails_start_with_a_catchable_error():
    client = ACPClient(command=_MISSING)

    with pytest.raises(Exception) as excinfo:  # noqa: B017 - the point is the type
        await asyncio.wait_for(client.start(), timeout=30)

    # Not a CancelledError: `except Exception` has to be able to see this.
    assert not isinstance(excinfo.value, asyncio.CancelledError)
    assert isinstance(excinfo.value, ConnectionError)
    assert _MISSING in str(excinfo.value)
    # ...and because it was catchable, start()'s guard reaped the subprocess.
    assert client._process is None


@pytest.mark.asyncio
async def test_the_read_loop_hands_a_pending_turn_the_real_error():
    stdout = asyncio.StreamReader()
    client = _client(stdout)
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    client._peer._pending[1] = future

    stdout.feed_eof()  # the child died mid-turn
    await asyncio.wait_for(client._read_loop(), timeout=5)

    assert not future.cancelled()
    assert isinstance(future.exception(), ConnectionError)
    assert client.command in str(future.exception())
    # The consumer of prompt_stream still gets its terminal event.
    assert client._event_queue.get_nowait().stop_reason == "disconnected"


@pytest.mark.asyncio
async def test_a_request_that_races_the_eof_fails_instead_of_parking():
    """The sweep can land between the write and the future's registration."""
    peer = JSONRPCPeer()
    peer.fail_all(ConnectionError("ACP agent exited: nope"))

    with pytest.raises(ConnectionError):
        await asyncio.wait_for(
            peer.send_request("initialize", {}, _FakeStdin(), timeout=5),  # type: ignore[arg-type]
            timeout=5,
        )


@pytest.mark.asyncio
async def test_our_own_shutdown_still_cancels_pending_futures():
    """``stop()`` is a cancellation, and must stay one -- no error logged."""
    stdout = asyncio.StreamReader()
    client = _client(stdout)
    client._process.returncode = 0  # already exited: nothing to reap
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    client._peer._pending[1] = future

    await client.stop()

    assert future.cancelled()
