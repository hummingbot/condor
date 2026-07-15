"""Unit tests for the run_agent primitive (refactor-02).

One execution core under tick / delegation / consult: event folding into both
views (transcript events + tool_calls), timeout semantics, client reaping (incl.
the on_client cancellation hook), and model healthcheck/fallback behavior.
"""

import asyncio

import pytest

from condor.acp.client import (
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
)
from condor.agents import run as run_module
from condor.agents.agent import Agent
from condor.agents.run import run_agent


class _FakeClient:
    """Scripted client: yields canned events from prompt_stream."""

    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.script = list(type(self)._script)
        type(self).instances.append(self)

    _script: list = []

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def prompt_stream(self, prompt):
        for ev in self.script:
            if ev == "hang":
                await asyncio.sleep(60)
            else:
                yield ev


@pytest.fixture(autouse=True)
def _wire_fakes(monkeypatch):
    """Stub the client classes and MCP wiring; every test gets a clean slate."""
    _FakeClient.instances = []
    _FakeClient._script = []
    monkeypatch.setattr(run_module, "ACPClient", _FakeClient)
    monkeypatch.setattr(run_module, "PydanticAIClient", _FakeClient)

    import condor.agents.context as shared

    monkeypatch.setattr(shared, "build_mcp_servers_for_session", lambda *a, **k: [])
    monkeypatch.setattr(shared, "build_mcp_servers_for_agent", lambda *a, **k: [])
    yield


def _agent(**kw):
    kw.setdefault("slug", "acme")
    kw.setdefault("name", "Acme")
    kw.setdefault("agent_key", "claude-code")  # ACP path → _FakeClient via ACPClient
    return Agent(**kw)


def test_run_agent_folds_both_views_and_reaps():
    _FakeClient._script = [
        ThoughtChunk(text="thinking… "),
        ThoughtChunk(text="more"),
        ToolCallEvent(
            tool_call_id="t1",
            title="get_market_data",
            status="in_progress",
            kind="other",
            input={"pair": "SOL-USDC"},
        ),
        ToolCallUpdate(tool_call_id="t1", status="completed", output="ok"),
        TextChunk(text="Answer "),
        TextChunk(text="here."),
        PromptDone(stop_reason="end_turn"),
    ]

    result = asyncio.run(
        run_agent(
            _agent(),
            "do the thing",
            permission_policy=None,
            user_id=1,
            chat_id=2,
        )
    )

    assert result.text == "Answer here."
    # tool_calls view: one folded call with the final status/output
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["status"] == "completed"
    assert result.tool_calls[0]["output"] == "ok"
    # events view: consecutive chunks merged, chronological order kept
    assert [e["type"] for e in result.events] == ["thought", "tool", "text"]
    assert result.events[0]["text"] == "thinking… more"
    assert result.error == ""
    # client always reaped
    client = _FakeClient.instances[0]
    assert client.started and client.stopped


def test_run_agent_timeout_marks_result_and_reaps():
    _FakeClient._script = [TextChunk(text="partial "), "hang"]

    result = asyncio.run(
        run_agent(
            _agent(),
            "slow task",
            permission_policy=None,
            user_id=1,
            chat_id=2,
            timeout_s=0,
        )
    )
    assert result.timed_out is True
    assert "Timed out" in result.error
    assert result.text.endswith("(timed out)")
    assert _FakeClient.instances[0].stopped


def test_run_agent_on_client_hook_for_cancellation_backstop():
    _FakeClient._script = [PromptDone(stop_reason="end_turn")]
    seen = []

    asyncio.run(
        run_agent(
            _agent(),
            "x",
            permission_policy=None,
            user_id=1,
            chat_id=2,
            on_client=seen.append,
        )
    )
    # Hook fires with the live client, then with None after the reap.
    assert seen[0] is _FakeClient.instances[0]
    assert seen[1] is None


def test_dead_backend_raises_without_fallback(monkeypatch):
    async def dead(model_key):
        return "backend unreachable."

    monkeypatch.setattr(run_module, "healthcheck_local_backend", dead)
    monkeypatch.setattr(run_module, "is_pydantic_ai_model", lambda k: True)
    monkeypatch.setenv("CONSULT_FALLBACK_MODEL", "claude-code")

    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(
            run_agent(
                _agent(agent_key="ollama:qwen3:32b"),
                "x",
                permission_policy=None,
                user_id=1,
                chat_id=2,
                fallback_on_unhealthy=False,  # tick semantics: never silently switch
            )
        )


def test_dead_backend_falls_back_with_note(monkeypatch):
    async def dead(model_key):
        return "backend unreachable."

    monkeypatch.setattr(run_module, "healthcheck_local_backend", dead)
    monkeypatch.setattr(
        run_module, "is_pydantic_ai_model", lambda k: k.startswith("ollama:")
    )
    monkeypatch.setenv("CONSULT_FALLBACK_MODEL", "claude-code")
    _FakeClient._script = [TextChunk(text="hi"), PromptDone(stop_reason="end_turn")]

    result = asyncio.run(
        run_agent(
            _agent(agent_key="ollama:qwen3:32b"),
            "x",
            permission_policy=None,
            user_id=1,
            chat_id=2,
            fallback_on_unhealthy=True,  # consult/delegate semantics
        )
    )
    assert result.model == "claude-code"
    assert "fallback" in result.fallback_note
    assert result.text == "hi"


def test_permission_policy_reaches_the_client():
    _FakeClient._script = [PromptDone(stop_reason="end_turn")]

    async def gate(tool_call, options):
        return {"outcome": {"outcome": "cancelled"}}

    asyncio.run(
        run_agent(
            _agent(),
            "x",
            permission_policy=gate,
            user_id=1,
            chat_id=2,
        )
    )
    assert _FakeClient.instances[0].kwargs["permission_callback"] is gate
