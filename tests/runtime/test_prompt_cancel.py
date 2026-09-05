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

from condor.acp.client import ACPClient, PromptDone, TextChunk
from condor.acp.pydantic_ai_client import PydanticAIClient
from condor.runtime import SessionKey
from condor.runtime import sessions as session_module
from condor.runtime import timeouts


class _FakeStdin:
    """Subprocess stdin that plays the agent's side of the conversation."""

    def __init__(self, *, answers_cancel: bool, cancel_delay: float = 0.0):
        self.answers_cancel = answers_cancel
        # How long the agent takes to settle the turn after being asked to.
        # Above ``prompt_cancel`` it models the real awkward case: an agent that
        # does honour the cancel, but not before Stop has given up waiting.
        self.cancel_delay = cancel_delay
        self.sent: list[dict] = []
        self.prompt_id: int | None = None
        self.settled: set[int] = set()
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
        req_id = self.prompt_id
        if self.cancel_delay:
            await asyncio.sleep(self.cancel_delay)
        # A turn settles once, however many times it was asked to stop.
        if req_id in self.settled:
            return
        self.settled.add(req_id)
        line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": req_id,
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


def _client(*, answers_cancel: bool, cancel_delay: float = 0.0) -> ACPClient:
    """An ACPClient wired to a fake subprocess — no spawn, real JSON-RPC peer."""
    client = ACPClient(command="fake-agent")
    stdin = _FakeStdin(answers_cancel=answers_cancel, cancel_delay=cancel_delay)
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
    # The request stays pending on purpose. An agent that ignored the cancel
    # may still be generating, and its settlement is the only signal that its
    # chunks have stopped arriving — dropping the future here is what used to
    # let the next turn open on top of this one.
    assert client._unsettled_req is not None
    assert set(client._peer._pending) == {client._unsettled_req}


# ── the tail of one turn never opens the next one ──


def _chunk(text: str) -> dict:
    """One ``agent_message_chunk`` as the bridge sends it: no request id."""
    return {"sessionUpdate": "agent_message_chunk", "content": {"text": text}}


async def _drive(agen, into: list):
    """Consume a prompt stream to its end, collecting events."""
    async for event in agen:
        into.append(event)


def _said(events: list) -> str:
    return "".join(e.text for e in events if isinstance(e, TextChunk))


async def _finish(client: ACPClient, reason: str = "end_turn") -> None:
    """Settle the live turn the way the agent does: answer its session/prompt."""
    stdin = client._process.stdin
    stdin.settled.add(stdin.prompt_id)
    line = json.dumps(
        {"jsonrpc": "2.0", "id": stdin.prompt_id, "result": {"stopReason": reason}}
    )
    await client._peer.handle_line(line, stdin)


def test_a_turn_that_ignored_cancel_never_bleeds_into_the_next_one(monkeypatch):
    """The reported bug: an answer that resumes inside the *following* answer.

    A long turn is steered aside, the agent does not settle it, and it keeps
    generating into the one event queue the session has. Because ACP
    ``session/update`` carries no request id, the next turn used to read those
    leftovers as its own opening words — the user saw the tail of a question
    they had already moved on from, glued to the front of the new answer.
    """
    monkeypatch.setattr(
        timeouts,
        "TIMEOUTS",
        replace(timeouts.TIMEOUTS, prompt_cancel=0.1, prompt_settle=0.2),
    )
    client = _client(answers_cancel=False)

    async def scenario():
        first: list = []
        agen = client.prompt_stream("give me everything you have")
        pending = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)
        client._on_session_update("sess-1", _chunk("PART-1 of the long answer "))
        first.append(await asyncio.wait_for(pending, timeout=5))

        # The user redirects mid-answer. The agent ignores session/cancel.
        await client.abort_prompt()
        await _drive(agen, first)

        # ...and keeps generating the turn nobody is listening to any more.
        client._on_session_update("sess-1", _chunk("PART-2, the abandoned tail "))

        second: list = []
        try:
            await _drive(
                client.prompt_stream("actually, what's the SOL price?"), second
            )
        except RuntimeError as exc:
            return first, second, str(exc)
        return first, second, ""

    first, second, error = asyncio.run(scenario())

    assert _said(first) == "PART-1 of the long answer "
    assert first[-1].stop_reason == "cancelled"
    # The new question is refused out loud rather than answered with someone
    # else's words in front of it.
    assert _said(second) == ""
    assert "still being written" in error
    # Asked to stop twice: once by Stop, once by the turn that wants the queue.
    assert client._process.stdin.methods().count("session/cancel") == 2


def test_a_late_settle_lets_the_next_turn_through_clean(monkeypatch):
    """The common case: the agent does settle, just not within Stop's budget.

    Stop stays snappy (``prompt_cancel``) and the screen ends immediately; the
    waiting is done by the next prompt (``prompt_settle``), which is the only
    place it buys anything.
    """
    monkeypatch.setattr(
        timeouts,
        "TIMEOUTS",
        replace(timeouts.TIMEOUTS, prompt_cancel=0.05, prompt_settle=5),
    )
    client = _client(answers_cancel=True, cancel_delay=0.2)

    async def scenario():
        first: list = []
        agen = client.prompt_stream("give me everything you have")
        pending = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)
        client._on_session_update("sess-1", _chunk("PART-1 of the long answer "))
        first.append(await asyncio.wait_for(pending, timeout=5))

        await client.abort_prompt()  # falls back locally: 0.2s > 0.05s
        await _drive(agen, first)

        second: list = []
        task = asyncio.create_task(
            _drive(client.prompt_stream("actually, what's the SOL price?"), second)
        )
        # The gate holds the new turn until the old one settles for real.
        await asyncio.sleep(0.3)
        client._on_session_update("sess-1", _chunk("SOL is $200"))
        await _finish(client)
        await asyncio.wait_for(task, timeout=5)
        return first, second

    first, second = asyncio.run(scenario())

    assert _said(first) == "PART-1 of the long answer "
    assert _said(second) == "SOL is $200"
    assert second[-1].stop_reason == "end_turn"
    assert client._unsettled_req is None


def test_walking_away_mid_answer_still_stops_the_agent():
    """A consumer torn down mid-turn (WS drop, page reload) cancels at the agent.

    ``prompt_stream`` used to unwind with the subprocess still generating and
    nothing ever telling it to stop, which is the same leak arriving by a
    different door — and one nobody could see, since the tab was gone.
    """
    client = _client(answers_cancel=True)

    async def scenario():
        events: list = []
        agen = client.prompt_stream("give me everything you have")
        pending = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)
        client._on_session_update("sess-1", _chunk("half an answer"))
        events.append(await asyncio.wait_for(pending, timeout=5))

        await agen.aclose()  # the socket dropped: nobody is reading any more
        await asyncio.sleep(0.05)
        # The abandoned turn's last words, produced after everyone left.
        client._on_session_update("sess-1", _chunk(" nobody asked for"))

        after: list = []
        task = asyncio.create_task(_drive(client.prompt_stream("still there?"), after))
        await asyncio.sleep(0.05)
        client._on_session_update("sess-1", _chunk("yes"))
        await _finish(client)
        await asyncio.wait_for(task, timeout=5)
        return events, after

    events, after = asyncio.run(scenario())

    assert _said(events) == "half an answer"
    assert "session/cancel" in client._process.stdin.methods()
    # The next turn starts on an empty queue, not on the old turn's leftovers.
    assert _said(after) == "yes"


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


# ── the session-level wall-clock timeout also cancels at the agent (CORR-140) ──


def test_prompt_overall_timeout_aborts_the_turn_at_the_agent(monkeypatch):
    """A runaway prompt is killed, not just stopped being listened to.

    The timeout branch used to break out of the relay loop only: the agent kept
    generating and kept running tools against a permission callback nobody was
    watching, and the lock was freed so the next prompt overlapped it at the
    subprocess. It must send ``session/cancel`` exactly like Stop does.
    """
    monkeypatch.setattr(session_module, "PROMPT_OVERALL_TIMEOUT", 0)
    client = _client(answers_cancel=True)
    stdin = client._process.stdin
    session = _session(client, "slot-timeout")

    async def scenario():
        events = []

        async def consume():
            async for event in session.prompt_stream("write me an essay"):
                events.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        # One event past the deadline is what trips the wall-clock check.
        client._event_queue.put_nowait(TextChunk(text="thinking..."))
        await asyncio.wait_for(task, timeout=5)
        return events

    events = asyncio.run(scenario())

    # The turn ended as a timeout for the caller...
    assert isinstance(events[-1], PromptDone)
    assert events[-1].stop_reason == "timeout"
    # ...and the agent was actually told to stop, exactly once.
    assert stdin.methods().count("session/cancel") == 1
    # Nothing in flight for the next prompt to overlap with.
    assert client._current_req_id is None
    assert client._peer._pending == {}
    assert session.is_busy is False
    assert session._lock.locked() is False
