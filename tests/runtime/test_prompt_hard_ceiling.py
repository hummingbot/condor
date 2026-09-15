"""The ACP stream's wall-clock ceiling applies while events keep arriving (CORR-331).

``prompt_stream``'s hard stop used to be evaluated only in the idle branch —
the one reached after 30s of silence on the event queue. An agent stuck in a
tool-call loop, or a model that keeps narrating, never goes idle, so the
ceiling was never compared and the turn ran forever. Callers that enforce no
budget of their own (``run_agent_to_completion``, reflection, the eager
initial-context prompt) inherited that: they kept relaying, and the agent kept
auto-approving tool calls, long past the policy's deadline.
"""

import asyncio
import json

from condor.acp.client import ACPClient, Heartbeat, PromptDone, TextChunk
from condor.runtime import timeouts


class _FastCeiling(timeouts.TimeoutPolicy):
    """Real policy, sub-second stream ceiling.

    ``prompt_hard_stop`` is derived (``prompt_overall + 60``), so it cannot be
    lowered into test range with ``dataclasses.replace``; overriding the
    property keeps every other deadline exactly as shipped.
    """

    @property
    def prompt_hard_stop(self) -> float:
        return 0.3


class _FakeStdin:
    """Subprocess stdin that settles session/cancel like a conforming agent."""

    def __init__(self):
        self.sent: list[dict] = []
        self.prompt_id: int | None = None
        self.peer = None
        self._tasks: list[asyncio.Task] = []

    def write(self, data: bytes) -> None:
        msg = json.loads(data.decode())
        self.sent.append(msg)
        if msg.get("method") == "session/prompt":
            self.prompt_id = msg.get("id")
        elif msg.get("method") == "session/cancel":
            self._tasks.append(asyncio.create_task(self._reply_cancelled()))

    async def drain(self) -> None:
        pass

    async def _reply_cancelled(self) -> None:
        line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": self.prompt_id,
                "result": {"stopReason": "cancelled"},
            }
        )
        await self.peer.handle_line(line, self)

    def methods(self) -> list:
        return [m.get("method") for m in self.sent]


class _FakeProcess:
    def __init__(self, stdin: _FakeStdin):
        self.stdin = stdin
        self.returncode = None


def _client() -> ACPClient:
    client = ACPClient(command="fake-agent")
    stdin = _FakeStdin()
    stdin.peer = client._peer
    client._process = _FakeProcess(stdin)
    client._session_id = "sess-1"
    return client


def test_ceiling_stops_a_stream_that_never_goes_idle(monkeypatch):
    """A chatty agent is cut off at the ceiling, and cancelled at the agent."""
    monkeypatch.setattr(timeouts, "TIMEOUTS", _FastCeiling(prompt_cancel=0.2))
    client = _client()

    async def scenario():
        events: list = []

        async def chatter():
            """The stuck agent: an event every 20ms, so the queue never empties."""
            while True:
                client._event_queue.put_nowait(TextChunk(text="."))
                await asyncio.sleep(0.02)

        noise = asyncio.create_task(chatter())
        try:
            async for event in client.prompt_stream("loop forever"):
                events.append(event)
        finally:
            noise.cancel()
        return events

    # Without the fix this never returns: the ceiling is only read after 30s of
    # silence, which a stream emitting every 20ms never produces.
    events = asyncio.run(asyncio.wait_for(scenario(), timeout=10))

    assert any(isinstance(e, TextChunk) for e in events), "events were relayed"
    # The queue never went idle, so no heartbeat: this is the event path.
    assert not any(isinstance(e, Heartbeat) for e in events)

    done = events[-1]
    assert isinstance(done, PromptDone)
    assert done.stop_reason == "timeout"
    # Cancelled at the agent, not merely abandoned (CORR-140's reasoning).
    assert "session/cancel" in client._process.stdin.methods()
