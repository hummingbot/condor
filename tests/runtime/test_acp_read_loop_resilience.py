"""One bad line must not deafen the ACP connection (CORR-328).

The read loop used to treat any per-line failure as fatal for the whole
connection: a non-UTF-8 byte, an oversized line or a malformed JSON-RPC error
member ended the loop for good while the subprocess kept running -- so
``alive`` still said True and the session cache kept handing back a client
that could no longer hear a word.
"""

import asyncio
import json

import pytest

from condor.acp.client import ACPClient


class _FakeStdin:
    """Subprocess stdin: records whatever the peer writes back."""

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
        self.returncode = None  # still running, exactly as in the bug report


def _client(stdout: asyncio.StreamReader) -> ACPClient:
    client = ACPClient(command="true")
    client._process = _FakeProcess(stdout)  # type: ignore[assignment]
    return client


async def _run_loop(client: ACPClient) -> None:
    await asyncio.wait_for(client._read_loop(), timeout=5)


@pytest.mark.asyncio
async def test_invalid_utf8_line_is_skipped_and_the_next_one_dispatched():
    seen: list[dict] = []
    stdout = asyncio.StreamReader()
    client = _client(stdout)
    client._peer.register_handler("probe", lambda **kw: seen.append(kw))

    # A tool result carrying one raw non-UTF-8 byte, then a good line.
    stdout.feed_data(b'{"jsonrpc":"2.0","method":"probe","params":{"v":"\xff"}}\n')
    stdout.feed_data(b'{"jsonrpc":"2.0","method":"probe","params":{"v":"ok"}}\n')
    stdout.feed_eof()

    await _run_loop(client)

    # Both lines land: the bad byte is replaced, not fatal.
    assert [s["v"] for s in seen] == ["�", "ok"]


@pytest.mark.asyncio
async def test_string_error_member_settles_the_future_without_killing_the_loop():
    stdout = asyncio.StreamReader()
    client = _client(stdout)
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    client._peer._pending[1] = future
    seen: list[dict] = []
    client._peer.register_handler("probe", lambda **kw: seen.append(kw))

    # `error` as a bare string instead of the object the spec mandates.
    stdout.feed_data(b'{"jsonrpc":"2.0","id":1,"error":"boom"}\n')
    stdout.feed_data(b'{"jsonrpc":"2.0","method":"probe","params":{"v":"ok"}}\n')
    stdout.feed_eof()

    await _run_loop(client)

    assert "boom" in str(future.exception())
    assert [s["v"] for s in seen] == ["ok"]


@pytest.mark.asyncio
async def test_oversized_line_is_skipped_and_the_next_one_dispatched():
    seen: list[dict] = []
    stdout = asyncio.StreamReader(limit=64)
    client = _client(stdout)
    client._peer.register_handler("probe", lambda **kw: seen.append(kw))

    huge = json.dumps({"jsonrpc": "2.0", "method": "probe", "params": {"v": "x" * 500}})
    stdout.feed_data(huge.encode() + b"\n")
    stdout.feed_data(b'{"jsonrpc":"2.0","method":"probe","params":{"v":"ok"}}\n')
    stdout.feed_eof()

    await _run_loop(client)

    assert [s["v"] for s in seen] == ["ok"]


@pytest.mark.asyncio
async def test_client_is_not_alive_once_the_read_loop_is_over():
    stdout = asyncio.StreamReader()
    client = _client(stdout)

    assert client.alive is True

    # Stream ends while the subprocess is still up (returncode is None).
    stdout.feed_eof()
    await _run_loop(client)

    # Nothing the process writes can reach us any more, so the session layer
    # must not be told the client is usable.
    assert client._process is not None and client._process.returncode is None
    assert client.alive is False
