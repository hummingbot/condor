"""A reasoning model's thinking must reach the shared event vocabulary (ARCH-333).

``PydanticAIClient`` folded a model response into ACPEvents by handling exactly
two part types — ``TextPart`` and ``ToolCallPart`` — so the ``ThinkingPart`` that
reasoning models return (deepseek-r1/qwq via ollama, gpt-oss via openrouter) fell
through both branches and was dropped. The thought panel that the ACP path fills
from ``agent_thought_chunk`` therefore stayed permanently empty for every
pydantic-ai model, even though ``ThoughtChunk`` is already a member of
``ACPEvent`` and is already rendered by both surfaces.

These tests drive a real ``Agent`` run over a stub model, the same way the
permission-gate tests do, and assert on the events the client actually yields.
"""

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from condor.acp.client import TextChunk, ThoughtChunk
from condor.acp.pydantic_ai_client import PydanticAIClient


def _client_returning(parts: list) -> PydanticAIClient:
    """A started-enough client whose model answers once with ``parts``."""

    def respond(messages: list, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=list(parts))

    client = PydanticAIClient("openai:gpt-4o")
    client._agent = Agent(FunctionModel(respond))
    return client


def _drive(client: PydanticAIClient) -> list:
    async def run() -> list:
        return [event async for event in client.prompt_stream("go")]

    return asyncio.run(run())


def test_thinking_part_is_yielded_as_a_thought_chunk():
    """The reasoning content reaches the caller instead of being dropped."""
    client = _client_returning(
        [ThinkingPart(content="weighing the two pools"), TextPart("done")]
    )

    events = _drive(client)

    thoughts = [e for e in events if isinstance(e, ThoughtChunk)]
    assert thoughts, "ThinkingPart produced no ThoughtChunk"
    assert thoughts[0].text == "weighing the two pools"


def test_thinking_is_not_confused_with_the_answer():
    """Thinking goes to ThoughtChunk; the answer still goes to TextChunk."""
    client = _client_returning(
        [ThinkingPart(content="weighing the two pools"), TextPart("SOL-USDC")]
    )

    events = _drive(client)

    assert [e.text for e in events if isinstance(e, ThoughtChunk)] == [
        "weighing the two pools"
    ]
    assert "SOL-USDC" in "".join(e.text for e in events if isinstance(e, TextChunk))


def test_empty_thinking_part_yields_nothing():
    """A content-less ThinkingPart must not open an empty thought bubble."""
    client = _client_returning([ThinkingPart(content=""), TextPart("done")])

    events = _drive(client)

    assert not [e for e in events if isinstance(e, ThoughtChunk)]


def test_run_without_thinking_streams_as_before():
    """No ThinkingPart, no behaviour change."""
    client = _client_returning([TextPart("done")])

    events = _drive(client)

    assert not [e for e in events if isinstance(e, ThoughtChunk)]
    assert [e.text for e in events if isinstance(e, TextChunk)] == ["done"]
