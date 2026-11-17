"""Bringing an agent up has a deadline (CORR-333).

``ACPClient.start()`` awaited ``initialize`` and then ``session/new`` with no
bound, and ``JSONRPCPeer.send_request`` ended in a bare ``return await future``
with no timeout anywhere in the peer. A child that spawns but never answers --
an ``npx`` fetch that stalls, a CLI waiting on an interactive prompt it will
never get, a bridge blocked on auth -- therefore parked the caller forever.
Every call site opens its own budget only *after* ``start()`` returns, and
``_spawn_session`` holds the per-key creation lock while it waits, so one mute
child blocked every later session for that key.

The pydantic-ai client's ``await self._ready_event.wait()`` had the same shape
and is bounded by the same policy field.
"""

import asyncio
import contextlib
import dataclasses

import pytest

from condor.acp.client import ACPClient
from condor.acp.jsonrpc import JSONRPCPeer
from condor.acp.pydantic_ai_client import PydanticAIClient
from condor.runtime import timeouts


def _fast(seconds: float):
    """The real policy with only the handshake deadline pulled into test range."""
    return dataclasses.replace(timeouts.TIMEOUTS, agent_handshake=seconds)


class _FakeWriter:
    """Subprocess stdin that swallows everything and never answers."""

    def __init__(self):
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


def test_send_request_timeout_drops_the_pending_entry():
    """An abandoned request must not leak the future that only shutdown clears."""
    peer = JSONRPCPeer()

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await peer.send_request("initialize", {}, _FakeWriter(), timeout=0.05)

    asyncio.run(scenario())
    assert peer._pending == {}, "the expired request left an entry behind"


def test_start_gives_up_on_an_agent_that_never_answers(monkeypatch):
    """A spawned-but-mute agent fails start() at the deadline, killed."""
    monkeypatch.setattr(timeouts, "TIMEOUTS", _fast(0.3))
    # A real subprocess that holds the pipes open and says nothing: exactly the
    # shape of a bridge stuck fetching itself.
    client = ACPClient(command="sleep 30")

    async def scenario():
        with pytest.raises(TimeoutError) as excinfo:
            # Without the deadline this never returns -- the outer wait_for is
            # what keeps the unfixed code from hanging the suite.
            await client.start()
        return excinfo.value

    error = asyncio.run(asyncio.wait_for(scenario(), timeout=10))

    assert "handshake" in str(error), f"unhelpful error: {error}"
    assert client.alive is False, "the mute subprocess was left running"
    assert client._process is None, "stop() did not run on the timeout path"
    assert client._peer._pending == {}, "the abandoned handshake leaked a future"


class _NeverReadyAgent:
    """A pydantic-ai agent whose MCP servers never finish coming up."""

    async def __aenter__(self):
        await asyncio.Event().wait()  # never entered

    async def __aexit__(self, *exc_info):  # pragma: no cover
        return None


def test_pydantic_ai_start_gives_up_on_mcp_servers_that_never_come_up(monkeypatch):
    """The same deadline bounds the pydantic-ai ready wait."""
    monkeypatch.setattr(timeouts, "TIMEOUTS", _fast(0.3))
    client = PydanticAIClient(model="ollama:llama3.1")
    client._agent = _NeverReadyAgent()
    client._mcp_servers = [object()]
    client._ready_event = asyncio.Event()
    client._shutdown_event = asyncio.Event()
    client._startup_error = None
    client._lifecycle_error = None

    async def scenario():
        task = asyncio.create_task(client._run_mcp_lifecycle())
        client._mcp_task = task
        with pytest.raises(TimeoutError):
            await client._await_ready()
        return task

    task = asyncio.run(asyncio.wait_for(scenario(), timeout=10))

    assert task.cancelled(), "the MCP lifecycle task was left running"
    assert client.alive is False, "a client that never came up must not look alive"


_PINNABLE_SESSION = {
    "sessionId": "s",
    "configOptions": [
        {
            "id": "model",
            "category": "model",
            "currentValue": "opus",
            "options": [{"value": "opus"}, {"value": "sonnet"}],
        }
    ],
}


def test_the_model_pin_is_sent_under_the_handshake_deadline(monkeypatch):
    """A bridge that answers session/new and then goes quiet must not park start() (RT-10)."""
    import time
    from types import SimpleNamespace

    monkeypatch.setattr(timeouts, "TIMEOUTS", _fast(0.3))
    client = ACPClient("claude-agent-acp", model="sonnet")
    client._session_id = "s"
    client._process = SimpleNamespace(stdin=_FakeWriter(), returncode=0)
    # The real peer: nothing ever answers, so only a timeout can end the wait.
    client._peer = JSONRPCPeer()

    async def scenario():
        with pytest.raises(TimeoutError) as excinfo:
            await client._select_model(_PINNABLE_SESSION, time.monotonic() + 0.2)
        return excinfo.value

    # Without the deadline this never returns; the outer wait_for is what keeps
    # the unfixed code from hanging the suite.
    error = asyncio.run(asyncio.wait_for(scenario(), timeout=10))

    assert "handshake" in str(error), f"unhelpful error: {error}"
    assert client._peer._pending == {}, "the abandoned pin request leaked a future"
    assert client.active_model_id == "opus", "a pin that timed out was recorded as set"


def test_start_hands_its_deadline_to_the_model_pin(monkeypatch):
    """start() passes what is left of its one budget, not a fresh one."""
    import time

    monkeypatch.setattr(timeouts, "TIMEOUTS", _fast(5.0))
    seen = {}

    class _Peer:
        async def send_request(self, method, params, stdin, timeout=None):
            if method == "session/new":
                return dict(_PINNABLE_SESSION)
            return {}

        def cancel_all(self):
            pass

    client = ACPClient(command="sleep 30", model="sonnet")
    client._peer = _Peer()

    async def fake_select(session, deadline=None):
        seen["remaining"] = deadline - time.monotonic()

    client._select_model = fake_select

    async def scenario():
        try:
            await client.start()
        finally:
            await client.stop()

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))
    assert 0 < seen["remaining"] <= 5.0
