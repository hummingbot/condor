"""Stop actually stops the agent (FEAT-029).

The old abort only stopped *us listening*: the subprocess ran the turn to
completion and its session history kept an assistant turn the user never saw,
so the next prompt landed in a context that disagreed with the screen. These
tests pin the two halves of the fix — the protocol-level ``session/cancel``
that the agent answers, and the fallback for an agent that ignores it — plus
the local-model equivalent, where committing the partial turn is what keeps
the context honest.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace

from condor.acp.client import ACPClient, PromptDone
from condor.acp.pydantic_ai_client import PydanticAIClient
from condor.runtime import SessionKey
from condor.runtime import sessions as session_module
from condor.runtime import timeouts


class _FakeStdin:
    """Subprocess stdin that plays the agent's side of the conversation."""

    def __init__(self, *, answers_cancel: bool):
        self.answers_cancel = answers_cancel
        self.sent: list[dict] = []
        self.prompt_id: int | None = None
        self.peer = None
        self._tasks: list[asyncio.Task] = []

    def write(self, data: bytes) -> None:
        msg = json.loads(data.decode())
        self.sent.append(msg)
        if msg.get("method") == "session/prompt":
            self.prompt_id = msg.get("id")
        elif msg.get("method") == "session/cancel" and self.answers_cancel:
            self._tasks.append(asyncio.create_task(self._reply_cancelled()))

    async def drain(self) -> None:
        pass

    async def _reply_cancelled(self) -> None:
        """What a conforming agent does: settle the prompt as cancelled."""
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


def _client(*, answers_cancel: bool) -> ACPClient:
    """An ACPClient wired to a fake subprocess — no spawn, real JSON-RPC peer."""
    client = ACPClient(command="fake-agent")
    stdin = _FakeStdin(answers_cancel=answers_cancel)
    stdin.peer = client._peer
    client._process = _FakeProcess(stdin)
    client._session_id = "sess-1"
    return client


def _session(client: ACPClient, slot: str):
    return session_module.AgentSession(
        key=SessionKey.web(7, slot), agent_key="claude-code", client=client
    )


# ── ACP: the agent answers session/cancel ──


def test_cancel_is_sent_and_the_agent_settles_the_turn():
    """abort_prompt notifies the agent and rides its 'cancelled' reply out."""
    client = _client(answers_cancel=True)
    stdin = client._process.stdin

    async def scenario():
        events = []
        agen = client.prompt_stream("write me an essay")
        # Start the turn: the generator sends session/prompt, registers the
        # pending future and parks on the event queue.
        pending = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)

        await client.abort_prompt()

        events.append(await asyncio.wait_for(pending, timeout=5))
        async for event in agen:
            events.append(event)
        return events

    events = asyncio.run(scenario())

    assert "session/cancel" in stdin.methods()
    cancel = next(m for m in stdin.sent if m.get("method") == "session/cancel")
    assert cancel["params"] == {"sessionId": "sess-1"}
    assert "id" not in cancel  # a notification, not a request

    done = events[-1]
    assert isinstance(done, PromptDone)
    assert done.stop_reason == "cancelled"
    # Nothing left behind for the next prompt to trip over.
    assert client._current_req_id is None
    assert client._peer._pending == {}


def test_abort_releases_the_session_lock():
    """The turn ends, is_busy clears and the next prompt can acquire the lock."""
    client = _client(answers_cancel=True)
    session = _session(client, "slot-a")

    async def scenario():
        events = []

        async def consume():
            async for event in session.prompt_stream("write me an essay"):
                events.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await session.abort()
        await asyncio.wait_for(task, timeout=5)
        return events

    events = asyncio.run(scenario())

    assert isinstance(events[-1], PromptDone)
    assert events[-1].stop_reason == "cancelled"
    assert session.is_busy is False
    assert session._lock.locked() is False


# ── ACP: the agent ignores session/cancel ──


def test_unanswered_cancel_falls_back_within_the_timeout(monkeypatch):
    """An agent that never answers must not hang Stop — or hold the lock."""
    monkeypatch.setattr(
        timeouts, "TIMEOUTS", replace(timeouts.TIMEOUTS, prompt_cancel=0.2)
    )
    client = _client(answers_cancel=False)
    session = _session(client, "slot-b")

    async def scenario():
        events = []

        async def consume():
            async for event in session.prompt_stream("write me an essay"):
                events.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)

        loop = asyncio.get_event_loop()
        started = loop.time()
        await session.abort()
        elapsed = loop.time() - started

        await asyncio.wait_for(task, timeout=5)
        return events, elapsed

    events, elapsed = asyncio.run(scenario())

    assert "session/cancel" in client._process.stdin.methods()
    # Bounded by prompt_cancel, not by the 30s heartbeat.
    assert elapsed < 2
    assert isinstance(events[-1], PromptDone)
    assert events[-1].stop_reason == "cancelled"
    assert session.is_busy is False
    assert session._lock.locked() is False
    assert client._current_req_id is None
    assert client._peer._pending == {}


def test_abort_with_nothing_in_flight_is_a_no_op():
    client = _client(answers_cancel=True)
    asyncio.run(client.abort_prompt())
    assert client._process.stdin.sent == []


# ── pydantic-ai: cancelling the run, keeping the context honest ──


class _FakeRun:
    """Minimal AgentRun stand-in: yields nodes, remembers the partial turn."""

    def __init__(self, nodes, partial, on_node=None):
        self._nodes = list(nodes)
        self._partial = partial
        self._on_node = on_node
        self.consumed = 0
        self.result = None  # an interrupted run never produces one

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.consumed >= len(self._nodes):
            raise StopAsyncIteration
        node = self._nodes[self.consumed]
        self.consumed += 1
        if self._on_node:
            await self._on_node(self.consumed)
        return node

    def new_messages(self):
        return list(self._partial[: self.consumed])


class _FakeAgent:
    def __init__(self, run: _FakeRun):
        self._run = run
        self.history_seen = None

    @asynccontextmanager
    async def iter(self, text, message_history=None):
        self.history_seen = message_history
        yield self._run


def test_pydantic_ai_abort_commits_the_partial_turn():
    """Aborting mid-run still lands what the model produced in the history.

    Skipping this is exactly the screen/context divergence that
    ``session/cancel`` removes on the ACP path — the local one needs its own
    equivalent, or the next prompt answers a turn the user never saw.
    """
    client = PydanticAIClient(model="ollama:qwen")
    nodes = ["n1", "n2", "n3", "n4"]
    partial = ["msg-1", "msg-2", "msg-3", "msg-4"]

    async def on_node(index):
        if index == 1:
            await client.abort_prompt()

    run = _FakeRun(nodes, partial, on_node=on_node)
    client._agent = _FakeAgent(run)

    async def scenario():
        return [event async for event in client.prompt_stream("write me an essay")]

    events = asyncio.run(scenario())

    done = events[-1]
    assert isinstance(done, PromptDone)
    assert done.stop_reason == "cancelled"
    # Stopped early rather than running the graph to the end...
    assert run.consumed < len(nodes)
    # ...and the turn it did produce is in the context the next prompt sees.
    assert client._message_history == partial[: run.consumed]


def test_pydantic_ai_abort_flag_does_not_leak_into_the_next_prompt():
    """A stale abort must not cancel the turn after it."""
    client = PydanticAIClient(model="ollama:qwen")
    run = _FakeRun(["n1", "n2"], ["msg-1", "msg-2"])
    client._agent = _FakeAgent(run)

    async def scenario():
        await client.abort_prompt()
        return [event async for event in client.prompt_stream("hello")]

    events = asyncio.run(scenario())

    assert events[-1].stop_reason == "end_turn"
    assert run.consumed == 2
