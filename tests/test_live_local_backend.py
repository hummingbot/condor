"""One live chat turn and one live loop tick against a local model (TASK-025).

Everything else in the suite stubs the model out, so the mcp 2.x /
pydantic-ai 2.x port was only ever exercised against fakes: nothing proved an
``ollama:``/``lmstudio:`` key still reaches a real backend, that the MCP stdio
transport still mounts the condor server, or that a tool return still comes
back as a follow-up model request rather than ending the turn.

Opt-in on purpose. These are the only tests here that need a model loaded and
answering, which costs tens of seconds and cannot be deterministic, so the gate
skips them unless ``CONDOR_LIVE_BACKEND_CHECK=1`` *and* a backend is actually
reachable:

    CONDOR_LIVE_BACKEND_CHECK=1 uv run pytest tests/test_live_local_backend.py

``CONDOR_LIVE_BACKEND_MODEL`` picks the agent key (default ``ollama:``, which
resolves the backend's first model). It must be a tool-capable model -- the
tick test asks for an MCP call, and a model without tool support cannot make
one.
"""

import os

import pytest

from condor.acp.client import PromptDone, TextChunk, ToolCallEvent, ToolCallUpdate
from condor.acp.pydantic_ai_client import (
    PydanticAIClient,
    healthcheck_local_backend,
)

MODEL = os.environ.get("CONDOR_LIVE_BACKEND_MODEL", "ollama:")

#: The condor MCP server, spawned exactly as ``condor.runtime.toolsets`` spawns
#: it for an unattended tick seat. ``--profile tick`` is the narrowest ring, and
#: the ids are zero because this seat belongs to no chat and no user.
CONDOR_MCP = {
    "name": "condor",
    "command": "uv",
    "args": [
        "run",
        "python",
        "-m",
        "mcp_servers.condor",
        "--chat-id",
        "0",
        "--user-id",
        "0",
        "--profile",
        "tick",
    ],
}

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("CONDOR_LIVE_BACKEND_CHECK") != "1",
        reason="live backend check is opt-in: set CONDOR_LIVE_BACKEND_CHECK=1",
    ),
]


async def _backend() -> str:
    """The agent key, once a backend has answered for it.

    Skips rather than fails when nothing is listening: an opt-in run on a
    machine whose Ollama is down is a missing backend, not a regression.
    """
    err = await healthcheck_local_backend(MODEL)
    if err:
        pytest.skip(f"no local backend for {MODEL!r}: {err}")
    return MODEL


async def _drive(client: PydanticAIClient, prompt: str) -> dict:
    """Run one turn and fold its stream into what the assertions read."""
    seen: dict = {"text": [], "tool_calls": [], "tool_returns": [], "stop": ""}
    await client.start()
    try:
        async for event in client.prompt_stream(prompt):
            if isinstance(event, TextChunk):
                seen["text"].append(event.text)
            elif isinstance(event, ToolCallEvent):
                seen["tool_calls"].append(event.title)
            elif isinstance(event, ToolCallUpdate):
                if event.output is not None:
                    seen["tool_returns"].append(event.output)
            elif isinstance(event, PromptDone):
                seen["stop"] = event.stop_reason
    finally:
        await client.stop()
    seen["answer"] = "".join(seen["text"]).strip()
    return seen


async def test_a_live_chat_turn_answers():
    """The chat seat: ``Session.prompt_stream`` over a real local model."""
    client = PydanticAIClient(
        model=await _backend(),
        mcp_servers=[CONDOR_MCP],
        system_prompt="You are Condor. Answer in one short sentence.",
    )

    seen = await _drive(client, "In one short sentence, what is a market maker?")

    assert seen["stop"] == "end_turn", seen
    assert seen["answer"], seen


async def test_a_live_tick_calls_an_mcp_tool_and_answers_from_its_result():
    """The tick seat, which is the chat seat plus two 2.x surfaces.

    A ``permission_callback`` puts ``PermissionGatedToolset`` (pydantic-ai's
    ``WrapperToolset``) in the chain and ``allowed_tools`` puts the
    ``PrepareTools`` capability in it -- neither of which a plain chat turn
    touches. Then the tool's result has to come back as a *follow-up* model
    request: a turn that stops at the tool return is the failure this catches.
    """
    approved: list[str] = []

    async def approve(tool_call_info: dict, options: list[dict]) -> dict:
        approved.append(str(tool_call_info.get("tool")))
        return {"outcome": {"outcome": "selected", "optionId": "allow"}}

    client = PydanticAIClient(
        model=await _backend(),
        mcp_servers=[CONDOR_MCP],
        permission_callback=approve,
        allowed_tools=["manage_memory"],
        system_prompt=(
            "You are a trading agent running one unattended tick. Call the "
            "manage_memory tool with action='list' before answering, then "
            "report how many memories it returned."
        ),
    )

    seen = await _drive(
        client,
        "Tick: list your stored memories with the manage_memory tool, then "
        "state the count in one sentence.",
    )

    assert seen["stop"] == "end_turn", seen
    assert approved == ["manage_memory"], seen
    assert seen["tool_calls"] == ["manage_memory"], seen
    assert seen["tool_returns"], seen
    # The turn continued past the tool return instead of ending on it.
    assert seen["answer"], seen
