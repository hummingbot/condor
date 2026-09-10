"""A conversation knows how many tokens it has spent (FEAT-120).

Both agent backends already receive exact usage — the ACP adapter on every
``session/prompt`` response and ``usage_update``, pydantic-ai on every run — and
used to throw it away. The design splits the work three ways and these tests
pin each part where it lives:

- **counting** in the client, a lifetime counter that sees every token,
  cancelled and abandoned turns included;
- **attribution** in the funnel, which charges each turn the counter's delta
  at DONE and again in its ``finally``;
- **persistence** in the Recorder and ``append_turn``, beside ``turn_count``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from condor.acp.client import ACPClient, PromptDone, TextChunk
from condor.acp.pydantic_ai_client import PydanticAIClient
from condor.acp.usage import TokenUsage
from condor.llm import openrouter_models
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as sessions_module
from condor.runtime.conversations import (
    TurnEntry,
    append_turn,
    get_conversation,
    new_conversation,
    read_transcript,
)
from condor.runtime.events import EventType, RuntimeEvent

USER = 4120


# ── The type ──


def test_adding_sums_counters_and_keeps_the_latest_context_reading():
    a = TokenUsage(
        input_tokens=100,
        output_tokens=10,
        cost_usd=0.5,
        context_used=100,
        context_size=200_000,
    )
    b = TokenUsage(input_tokens=50, output_tokens=5, unpriced_turns=1, context_used=150)

    total = a + b

    assert (total.input_tokens, total.output_tokens) == (150, 15)
    assert total.cost_usd == 0.5
    assert total.unpriced_turns == 1
    assert total.context_used == 150, "latest reading, never a sum"
    assert total.context_size == 200_000, "an absent reading keeps the last one"
    assert total.total_tokens == 165


def test_subtracting_floors_at_zero_and_carries_the_newer_context():
    now = TokenUsage(input_tokens=100, cost_usd=0.41, context_used=900)
    before = TokenUsage(input_tokens=120, cost_usd=0.40, context_used=100)

    delta = now - before

    assert delta.input_tokens == 0
    assert delta.to_dict()["cost_usd"] == pytest.approx(0.01)
    assert delta.context_used == 900


def test_the_dict_round_trip_is_tolerant_both_ways():
    usage = TokenUsage(
        input_tokens=3,
        output_tokens=4,
        cache_read_tokens=1,
        cost_usd=0.25,
        context_size=8,
    )
    assert TokenUsage.from_dict(usage.to_dict()) == usage
    assert TokenUsage.from_dict(None) == TokenUsage()
    assert TokenUsage.from_dict({}) == TokenUsage()
    # A newer build's key and a garbled value are ignored, not fatal.
    assert TokenUsage.from_dict(
        {"input_tokens": 5, "mystery": 1, "context_used": "lots"}
    ) == TokenUsage(input_tokens=5)


def test_acp_input_is_made_inclusive_of_cache():
    """Anthropic's ``inputTokens`` excludes both cache counters; Condor's includes them."""
    wire = {
        "inputTokens": 10,
        "outputTokens": 5,
        "cachedReadTokens": 1000,
        "cachedWriteTokens": 200,
        "totalTokens": 1215,
    }

    usage = TokenUsage.from_acp(wire)

    assert usage.input_tokens == 1210
    assert usage.cache_read_tokens == 1000
    assert usage.cache_write_tokens == 200
    assert usage.total_tokens == wire["totalTokens"]


# ── pydantic-ai ──


def _answer(messages: list, info: AgentInfo) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart("SOL-USDC")],
        usage=RequestUsage(input_tokens=1000, output_tokens=50, cache_read_tokens=400),
    )


def _pai(model: str = "openai:gpt-4o") -> PydanticAIClient:
    client = PydanticAIClient(model)
    client._agent = Agent(FunctionModel(_answer))
    return client


def _drive(client: PydanticAIClient) -> list:
    async def run() -> list:
        return [event async for event in client.prompt_stream("go")]

    return asyncio.run(run())


def test_pydantic_ai_tokens_accumulate_across_runs():
    client = _pai()

    _drive(client)
    _drive(client)

    # Already inclusive on this backend: cache is not added a second time.
    assert client.usage.input_tokens == 2000
    assert client.usage.cache_read_tokens == 800
    assert client.usage.output_tokens == 100
    assert client.usage.context_used == 1050


def test_an_unpriceable_model_counts_tokens_and_no_cost():
    """``FunctionModel`` stamps an id no price table knows, like any local model."""
    client = _pai()

    _drive(client)

    assert client.usage.total_tokens == 1050
    assert client.usage.cost_usd == 0
    assert client.usage.unpriced_turns == 1


def test_a_priced_model_sums_its_cost(monkeypatch):
    monkeypatch.setattr(
        ModelResponse,
        "cost",
        lambda self: SimpleNamespace(total_price=Decimal("0.0125")),
    )
    client = _pai()

    _drive(client)
    _drive(client)

    assert client.usage.cost_usd == pytest.approx(0.025)
    assert client.usage.unpriced_turns == 0


def test_a_stopped_run_still_counts():
    client = _pai()

    async def run() -> list:
        events = []
        async for event in client.prompt_stream("go"):
            events.append(event)
            if isinstance(event, TextChunk):
                await client.abort_prompt()
        return events

    events = asyncio.run(run())

    assert events[-1] == PromptDone(stop_reason="cancelled")
    assert client.usage.input_tokens == 1000


def test_a_run_whose_consumer_walked_away_still_counts():
    """A WS drop closes the generator at a ``yield``, before the node loop ends."""
    client = _pai()

    async def run() -> None:
        stream = client.prompt_stream("go")
        async for event in stream:
            if isinstance(event, TextChunk):
                break
        # Unwinding pydantic-ai's run from a closed generator raises on its own
        # (its anyio cancel scope; it did before FEAT-120 too, and in
        # production the finalizer only logs it). What this pins is that the
        # usage was folded first: the fold is the innermost exit.
        with contextlib.suppress(RuntimeError, GeneratorExit):
            await stream.aclose()

    asyncio.run(run())

    assert client.usage.input_tokens == 1000


def test_the_openrouter_window_comes_from_the_cached_catalog_only(monkeypatch):
    monkeypatch.setattr(openrouter_models, "_cache", None)
    assert openrouter_models.cached_context_length("x/y") is None, "never fetches"

    catalog = [openrouter_models.OpenRouterModel("x/y", "Y", 128_000, 0.0, 0.0)]
    monkeypatch.setattr(openrouter_models, "_cache", (0.0, catalog))
    client = _pai("openrouter:x/y")

    _drive(client)

    assert client.usage.context_size == 128_000
    assert _pai("ollama:qwen3")._context_size() is None


# ── ACP ──


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


def _acp() -> tuple[ACPClient, asyncio.StreamReader]:
    """A real client over a fake pipe: the peer, the read loop and the done callback are real."""
    stdout = asyncio.StreamReader()
    client = ACPClient(command="true")
    client._process = _FakeProcess(stdout)  # type: ignore[assignment]
    client._session_id = "s1"
    return client, stdout


async def _prompt_id(client: ACPClient) -> int:
    for _ in range(500):
        for message in client._process.stdin.written:
            if message.get("method") == "session/prompt":
                return message["id"]
        await asyncio.sleep(0)
    raise AssertionError("session/prompt was never sent")


def _response(req_id: int, result: dict) -> bytes:
    return (
        json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n"
    ).encode()


_WIRE_USAGE = {
    "inputTokens": 3,
    "outputTokens": 40,
    "cachedReadTokens": 30_000,
    "cachedWriteTokens": 2_000,
    "totalTokens": 32_043,
}


def test_a_prompt_responses_usage_is_counted_inclusively():
    async def scenario() -> tuple[ACPClient, list]:
        client, stdout = _acp()
        reader = asyncio.create_task(client._read_loop())
        stream_events: list = []

        async def consume() -> None:
            async for event in client.prompt_stream("hi"):
                stream_events.append(event)

        task = asyncio.create_task(consume())
        req_id = await _prompt_id(client)
        stdout.feed_data(
            _response(req_id, {"stopReason": "end_turn", "usage": _WIRE_USAGE})
        )
        await asyncio.wait_for(task, timeout=5)
        reader.cancel()
        return client, stream_events

    client, events = asyncio.run(scenario())

    assert events[-1] == PromptDone(stop_reason="end_turn")
    assert client.usage.input_tokens == 32_003
    assert client.usage.total_tokens == _WIRE_USAGE["totalTokens"]


def test_a_stale_response_from_a_cancelled_turn_still_counts():
    """The screen ended at the local cancel; the tokens arrive afterwards."""

    async def scenario() -> tuple[TokenUsage, TokenUsage, list]:
        client, stdout = _acp()
        reader = asyncio.create_task(client._read_loop())
        stream_events: list = []

        async def consume() -> None:
            async for event in client.prompt_stream("hi"):
                stream_events.append(event)

        task = asyncio.create_task(consume())
        req_id = await _prompt_id(client)
        # The agent ignored session/cancel: the fallback ends the turn here.
        client._cancel_locally(req_id)
        await asyncio.wait_for(task, timeout=5)
        before = client.usage

        stdout.feed_data(
            _response(req_id, {"stopReason": "cancelled", "usage": _WIRE_USAGE})
        )
        for _ in range(500):
            if not client.usage.is_zero():
                break
            await asyncio.sleep(0)
        reader.cancel()
        return before, client.usage, stream_events

    before, after, events = asyncio.run(scenario())

    assert events[-1] == PromptDone(stop_reason="cancelled")
    assert before.is_zero()
    assert after.output_tokens == 40
    assert after.input_tokens == 32_003


def _usage_update(amount: float, used: int = 45_000, size: int = 200_000) -> dict:
    return {
        "sessionUpdate": "usage_update",
        "used": used,
        "size": size,
        "cost": {"amount": amount, "currency": "USD"},
    }


def test_a_usage_update_with_no_turn_in_flight_still_moves_the_counter():
    """A background task's result lands after the turn settled — its cost is only here."""
    client = ACPClient(command="true")
    assert client._current_req_id is None

    client._on_session_update("s1", _usage_update(0.41))

    assert client.usage.cost_usd == 0.41
    assert client.usage.context_used == 45_000
    assert client.usage.context_size == 200_000
    assert client._event_queue.empty(), "the counter moves; nothing is relayed"


def test_acp_cost_is_cumulative_and_never_goes_down():
    client = ACPClient(command="true")
    client._current_req_id = 7

    client._on_session_update("s1", _usage_update(0.41))
    client._on_session_update("s1", _usage_update(0.30))
    assert client.usage.cost_usd == 0.41

    client._on_session_update("s1", _usage_update(0.55, used=60_000))
    assert client.usage.cost_usd == 0.55
    assert client.usage.context_used == 60_000
    assert client._event_queue.empty()


# ── Attribution and persistence ──


class _MeteredClient:
    """A chat client whose counter moves while it answers, like a real one's.

    A script step is either an event to yield or a ``TokenUsage`` to spend.
    """

    def __init__(self) -> None:
        self.alive = True
        self.usage = TokenUsage()
        self.script: list = []

    async def prompt_stream(self, text, images=None):
        for step in self.script:
            await asyncio.sleep(0)
            if isinstance(step, TokenUsage):
                self.usage = self.usage + step
            elif isinstance(step, Exception):
                raise step
            else:
                yield step

    async def abort_prompt(self) -> None:
        pass


def _spend(i: int, o: int) -> TokenUsage:
    return TokenUsage(input_tokens=i, output_tokens=o)


@pytest.fixture
def chat(monkeypatch):
    """One live web session over a metered client, answering into a real conversation."""
    monkeypatch.setattr(conversations, "_live_recorders", set())
    meta = new_conversation(USER, "web", agent_key="claude-code")
    key = SessionKey.web(USER, meta.id)
    client = _MeteredClient()
    session = sessions_module.AgentSession(
        key=key,
        agent_key="claude-code",
        client=client,
        user_id=USER,
        conversation_id=meta.id,
    )
    # No spec, so the funnel's staleness check has nothing to rebuild from.
    monkeypatch.setattr(sessions_module, "_sessions", {str(key): session})
    return SimpleNamespace(key=key, client=client, conv_id=meta.id)


def _turn(chat, text: str = "go") -> list[RuntimeEvent]:
    async def run() -> list[RuntimeEvent]:
        return [e async for e in runtime.prompt(chat.key, PromptRequest(text=text))]

    return asyncio.run(run())


def _done(events: list[RuntimeEvent]) -> RuntimeEvent:
    return [e for e in events if e.type is EventType.DONE][-1]


def test_the_done_event_carries_what_the_turn_spent(chat):
    chat.client.script = [
        TextChunk(text="hi"),
        _spend(1000, 50),
        PromptDone(stop_reason="end_turn"),
    ]

    events = _turn(chat)

    assert _done(events).field("usage")["total_tokens"] == 1050


def test_the_prompt_done_frame_carries_the_turns_usage_as_an_added_key():
    from condor.web.routes.chat_ws import _to_ws_message

    done = RuntimeEvent.done("end_turn", session_key="k")
    done.data["usage"] = _spend(10, 2).to_dict()

    frame = _to_ws_message(done, "s1")

    assert frame["event"] == "prompt_done"
    assert frame["stop_reason"] == "end_turn"
    assert frame["usage"]["total_tokens"] == 12
    # A DONE the funnel never charged still makes a frame, with nothing to add.
    assert _to_ws_message(RuntimeEvent.done("cancelled"), "s1")["usage"] is None


def test_two_turns_add_up_on_the_conversation(chat):
    chat.client.script = [_spend(1000, 50), TextChunk(text="a"), PromptDone("end_turn")]
    _turn(chat)
    chat.client.script = [
        _spend(2000, 100),
        TextChunk(text="b"),
        PromptDone("end_turn"),
    ]
    _turn(chat)

    meta = get_conversation(USER, chat.conv_id)
    assert meta.usage["input_tokens"] == 3000
    assert meta.usage["output_tokens"] == 150
    answers = [t for t in read_transcript(USER, chat.conv_id) if t.role == "assistant"]
    assert [t.usage["total_tokens"] for t in answers] == [1050, 2100]
    user_turns = [t for t in read_transcript(USER, chat.conv_id) if t.role == "user"]
    assert all(t.usage == {} for t in user_turns), "stamped once, on the last entry"


def test_an_abandoned_turn_is_still_charged(chat):
    """A page reload mid-answer: the funnel's generator only ever sees GeneratorExit."""
    chat.client.script = [
        TextChunk(text="half an ans"),
        _spend(700, 7),
        TextChunk(text="wer"),
        TextChunk(text=" never finished"),
    ]

    async def walk_away() -> None:
        stream = runtime.prompt(chat.key, PromptRequest(text="go"))
        seen = 0
        async for event in stream:
            if event.type is EventType.TEXT:
                seen += 1
                if seen == 2:
                    break
        await stream.aclose()

    asyncio.run(walk_away())

    (answer,) = [
        t for t in read_transcript(USER, chat.conv_id) if t.role == "assistant"
    ]
    assert answer.usage["total_tokens"] == 707
    assert get_conversation(USER, chat.conv_id).usage["total_tokens"] == 707


def test_a_turn_that_errored_is_charged_on_its_error_entry(chat):
    chat.client.script = [_spend(300, 0), RuntimeError("upstream 500")]

    events = _turn(chat)

    assert _done(events).stop_reason == "error"
    assert _done(events).field("usage")["input_tokens"] == 300
    last = read_transcript(USER, chat.conv_id)[-1]
    assert (last.role, last.kind) == ("system", "error")
    assert last.usage["input_tokens"] == 300
    assert get_conversation(USER, chat.conv_id).usage["input_tokens"] == 300


def test_tokens_spent_between_turns_land_on_the_next_one(chat):
    """``/compact`` prompts the client outside the funnel; its tokens are not lost."""
    chat.client.script = [_spend(100, 0), TextChunk(text="a"), PromptDone("end_turn")]
    _turn(chat)
    chat.client.usage = chat.client.usage + _spend(500, 20)  # the /compact summary
    chat.client.script = [_spend(10, 1), TextChunk(text="b"), PromptDone("end_turn")]
    _turn(chat)

    answers = [t for t in read_transcript(USER, chat.conv_id) if t.role == "assistant"]
    assert [t.usage["input_tokens"] for t in answers] == [100, 510]
    assert get_conversation(USER, chat.conv_id).usage["total_tokens"] == 631


def test_a_client_that_does_not_count_records_nothing(chat):
    """A test double or a future backend: no ``usage`` attribute, no stamp, no crash."""
    del chat.client.usage
    chat.client.script = [TextChunk(text="a"), PromptDone("end_turn")]

    events = _turn(chat)

    assert _done(events).field("usage")["total_tokens"] == 0
    assert all(t.usage == {} for t in read_transcript(USER, chat.conv_id))
    assert get_conversation(USER, chat.conv_id).usage == {}


def test_a_meta_written_before_usage_existed_loads_and_merges_from_zero():
    meta = new_conversation(USER, "web", agent_key="claude-code")
    meta_path = conversations._conv_dir(USER, meta.id) / conversations.META_FILENAME
    on_disk = json.loads(meta_path.read_text())
    on_disk.pop("usage", None)
    meta_path.write_text(json.dumps(on_disk))

    assert get_conversation(USER, meta.id).usage == {}

    append_turn(
        USER,
        meta.id,
        TurnEntry(role="assistant", text="x", usage=_spend(7, 3).to_dict()),
    )

    assert get_conversation(USER, meta.id).usage["total_tokens"] == 10


def test_a_transcript_line_older_than_usage_still_parses():
    assert TurnEntry.model_validate({"role": "user", "text": "hi"}).usage == {}
