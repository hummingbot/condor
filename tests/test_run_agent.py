"""Unit tests for the run_agent primitive (refactor-02).

One execution core under tick / delegation / consult: event folding into both
views (transcript events + tool_calls), timeout semantics, and client reaping
(incl. the on_client cancellation hook). ACP is the only model runner (§9.3).
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

    import condor.agents.context as shared

    monkeypatch.setattr(shared, "build_mcp_servers_for_session", lambda *a, **k: [])
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


def test_on_tool_call_fires_at_terminal_status_with_streamed_input():
    """MCP arguments stream in on tool_call_update AFTER the create event —
    the audit hook must fire once, at terminal status, with input+output
    present (firing at create recorded `input: {}`)."""
    _FakeClient._script = [
        ToolCallEvent(
            tool_call_id="t1",
            title="mcp__condor__record_learning",
            status="pending",
            kind="other",
            input=None,  # not yet streamed
        ),
        ToolCallUpdate(tool_call_id="t1", input={"text": "JTO thins after 22:00"}),
        ToolCallUpdate(tool_call_id="t1", status="completed", output="ok"),
        # a second call that never reaches terminal status → flushed at end
        ToolCallEvent(
            tool_call_id="t2", title="slow_tool", status="in_progress", kind="other"
        ),
        PromptDone(stop_reason="end_turn"),
    ]

    persisted: list[dict] = []
    asyncio.run(
        run_agent(
            _agent(),
            "tick",
            permission_policy=None,
            on_tool_call=lambda tc: persisted.append(dict(tc)),
        )
    )

    assert [tc["id"] for tc in persisted] == ["t1", "t2"]
    t1 = persisted[0]
    assert t1["status"] == "completed"
    assert t1["input"] == {"text": "JTO thins after 22:00"}
    assert t1["output"] == "ok"
    t2 = persisted[1]  # flushed as-is, exactly once
    assert t2["status"] == "in_progress"


def test_run_agent_timeout_marks_result_and_reaps():
    _FakeClient._script = [TextChunk(text="partial "), "hang"]

    result = asyncio.run(
        run_agent(
            _agent(),
            "slow task",
            permission_policy=None,
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
            on_client=seen.append,
        )
    )
    # Hook fires with the live client, then with None after the reap.
    assert seen[0] is _FakeClient.instances[0]
    assert seen[1] is None


def test_model_override_wins_over_agent_default():
    _FakeClient._script = [PromptDone(stop_reason="end_turn")]

    result = asyncio.run(
        run_agent(
            _agent(agent_key="claude-code"),
            "x",
            permission_policy=None,
            model="claude-acp:sonnet",
        )
    )
    assert result.model == "claude-acp:sonnet"


def test_permission_policy_reaches_the_client():
    _FakeClient._script = [PromptDone(stop_reason="end_turn")]

    async def gate(tool_call, options):
        return {"outcome": {"outcome": "cancelled"}}

    asyncio.run(
        run_agent(
            _agent(),
            "x",
            permission_policy=gate,
        )
    )
    assert _FakeClient.instances[0].kwargs["permission_callback"] is gate
