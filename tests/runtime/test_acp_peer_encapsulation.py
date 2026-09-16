"""The peer owns id allocation, framing and the pending table (ARCH-332).

``session/prompt`` used to be the one request the client framed and sent by
hand, because it needs the future rather than the answer. ``begin_request`` is
the seam that gives it the future *and* keeps it on the peer's write path, so
the hottest request is logged and id-allocated like every other one — and,
because the future is now registered before the write rather than after the
drain, a child that answers during that drain is no longer dropped on the floor.
"""

import asyncio
import contextlib
import json
import logging

import pytest

from condor.acp.client import ACPClient
from condor.acp.jsonrpc import JSONRPCPeer


class _EagerStdin:
    """A child that answers inside our own ``drain()``.

    Not a contrivance: ``drain`` yields to the event loop, and that is exactly
    where the read loop gets to dispatch a response to a request whose write
    already went out.
    """

    def __init__(self, peer: JSONRPCPeer, result):
        self.peer = peer
        self.result = result
        self.sent: list[dict] = []

    def write(self, data: bytes) -> None:
        self.sent.append(json.loads(data.decode()))

    async def drain(self) -> None:
        msg = self.sent[-1]
        if "id" in msg:
            await self.peer.handle_line(
                json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": self.result}),
                self,
            )


class _SilentStdin:
    def __init__(self):
        self.sent: list[dict] = []

    def write(self, data: bytes) -> None:
        self.sent.append(json.loads(data.decode()))

    async def drain(self) -> None:
        pass


def test_a_response_that_lands_during_the_drain_is_not_dropped():
    """The future is registered before the write, so no answer arrives too early."""
    peer = JSONRPCPeer()
    stdin = _EagerStdin(peer, {"ok": True})

    async def scenario():
        return await asyncio.wait_for(
            peer.send_request("initialize", {}, stdin, timeout=1), timeout=2
        )

    assert asyncio.run(scenario()) == {"ok": True}
    assert peer._pending == {}


def test_a_failed_write_does_not_leak_a_pending_future():
    """Registering early must not turn a broken pipe into a stuck entry."""
    peer = JSONRPCPeer()

    class _BrokenStdin(_SilentStdin):
        async def drain(self) -> None:
            raise BrokenPipeError("gone")

    async def scenario():
        with pytest.raises(BrokenPipeError):
            await peer.send_request("initialize", {}, _BrokenStdin(), timeout=1)

    asyncio.run(scenario())
    assert peer._pending == {}


def test_session_prompt_is_framed_and_logged_by_the_peer(caplog):
    """The hottest request goes out through the same write path as the rest."""
    client = ACPClient(command="fake-agent")
    stdin = _SilentStdin()
    client._process = type("_P", (), {"stdin": stdin, "returncode": None})()
    client._session_id = "sess-1"

    async def scenario():
        agen = client.prompt_stream("hello")
        started = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)
        started.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await started
        await agen.aclose()

    with caplog.at_level(logging.DEBUG, logger="condor.acp.jsonrpc"):
        asyncio.run(scenario())

    prompt = next(m for m in stdin.sent if m.get("method") == "session/prompt")
    assert prompt["jsonrpc"] == "2.0"
    assert prompt["id"] == 1  # allocated by the peer, from its own counter
    assert prompt["params"]["prompt"] == [{"type": "text", "text": "hello"}]
    assert "-> session/prompt (id=1)" in caplog.text
