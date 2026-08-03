"""A permission denial must actually stop the tool from running (SEC-080).

The pydantic-ai client used to consult ``permission_callback`` from the loop
that *observes* the graph run, so a "cancelled" outcome skipped our own wire
events while pydantic-graph went on to execute the tool anyway. Every guardrail
built on that callback — dry-run mode, the risk limits, human confirmations, bot
ownership — was therefore cosmetic for every pydantic-ai model.

These tests drive a real ``Agent`` run over a stub model and a real toolset whose
tool records that it executed. The assertion that matters everywhere below is
that the tool function was NOT called — not merely that a denial was logged.
"""

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from condor.acp.client import PromptDone, ToolCallEvent, ToolCallUpdate
from condor.acp.pydantic_ai_client import (
    REFUSAL_PREFIX,
    PermissionGate,
    PydanticAIClient,
)
from condor.agents.ownership import BotLedger
from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)

NS = "brigado-ema_trend"


class Spy:
    """Records whether the underlying tool actually ran."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def ran(self) -> bool:
        return bool(self.calls)


def _one_tool_call_model(tool_name: str, args: dict) -> FunctionModel:
    """A model that calls ``tool_name`` once, then answers with text."""

    def respond(messages: list, info: AgentInfo) -> ModelResponse:
        already_called = any(
            isinstance(part, ToolCallPart)
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if already_called:
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id="call-1")]
        )

    return FunctionModel(respond)


def _client_with(tool_name: str, args: dict, callback) -> tuple[PydanticAIClient, Spy]:
    """A started-enough client whose single tool records its executions.

    Bypasses ``start()`` (which would spawn MCP stdio subprocesses) but wires the
    toolset through the very same ``_gate_toolsets`` production uses.
    """
    spy = Spy()
    toolset = FunctionToolset()

    async def tool(**kwargs) -> str:
        spy.calls.append(kwargs)
        return "executed"

    toolset.add_function(tool, name=tool_name)

    client = PydanticAIClient("openai:gpt-4o", permission_callback=callback)
    client._agent = Agent(
        _one_tool_call_model(tool_name, args),
        toolsets=client._gate_toolsets([toolset]),
    )
    return client, spy


def _drive(client: PydanticAIClient) -> list:
    async def run() -> list:
        return [event async for event in client.prompt_stream("go")]

    return asyncio.run(run())


def _statuses(events: list, status: str) -> list:
    return [
        e
        for e in events
        if isinstance(e, (ToolCallEvent, ToolCallUpdate)) and e.status == status
    ]


# ---------------------------------------------------------------------------
# The core hole: a denial must prevent execution
# ---------------------------------------------------------------------------


def test_cancelled_outcome_prevents_the_tool_from_running():
    """The sentinel is never set: the tool did not execute."""

    async def deny(tool_call: dict, options: list[dict]) -> dict:
        return {"outcome": {"outcome": "cancelled"}}

    client, spy = _client_with("manage_executors", {"action": "create"}, deny)
    events = _drive(client)

    assert not spy.ran, "denied tool executed anyway"
    assert _statuses(events, "blocked"), "no blocked event emitted"
    assert any(isinstance(e, PromptDone) for e in events), "run did not complete"


def test_blocked_call_is_not_also_reported_completed():
    """The refusal result must not overwrite 'blocked' with 'completed'."""

    async def deny(tool_call: dict, options: list[dict]) -> dict:
        return {"outcome": {"outcome": "cancelled"}}

    client, _ = _client_with("manage_executors", {"action": "create"}, deny)
    events = _drive(client)

    blocked = _statuses(events, "blocked")
    assert len(blocked) == 1
    assert not _statuses(events, "completed")


def test_approved_call_still_executes():
    """The gate must not block what the callback allowed."""

    async def allow(tool_call: dict, options: list[dict]) -> dict:
        return {"outcome": {"outcome": "selected", "optionId": "allow"}}

    client, spy = _client_with("get_market_data", {"pair": "BTC-USDT"}, allow)
    events = _drive(client)

    assert spy.ran, "approved tool never ran"
    assert not _statuses(events, "blocked")


def test_no_callback_leaves_tools_ungated():
    """Clients without a permission callback keep running tools as before."""
    client, spy = _client_with("get_market_data", {"pair": "BTC-USDT"}, None)
    _drive(client)

    assert spy.ran


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def test_permission_check_that_raises_blocks_the_call():
    """An exception in the gate is a denial, never a pass."""

    async def explode(tool_call: dict, options: list[dict]) -> dict:
        raise RuntimeError("confirmation backend down")

    client, spy = _client_with("place_order", {"amount": 1}, explode)
    events = _drive(client)

    assert not spy.ran, "tool ran despite the permission check erroring"
    assert _statuses(events, "blocked")
    assert any(isinstance(e, PromptDone) for e in events)


def test_malformed_callback_result_blocks_the_call():
    """Anything that isn't an explicit 'selected' outcome is a denial."""

    async def garbage(tool_call: dict, options: list[dict]) -> dict:
        return {"unexpected": "shape"}

    client, spy = _client_with("place_order", {"amount": 1}, garbage)
    _drive(client)

    assert not spy.ran


def test_gate_refuses_a_call_it_never_decided():
    """Default deny: an unrecorded call is refused, so no path silently allows."""
    gate = PermissionGate()

    allowed, reason = gate.consume("unknown-id", "manage_bots")

    assert not allowed
    assert "no permission decision" in reason


def test_gate_decision_cannot_be_replayed():
    """A consumed allow does not authorize a second call with the same id."""
    gate = PermissionGate()
    gate.record("call-1", "manage_bots", True)

    assert gate.consume("call-1", "manage_bots")[0] is True
    assert gate.consume("call-1", "manage_bots")[0] is False


def test_reset_clears_decisions_between_turns():
    gate = PermissionGate()
    gate.record("call-1", "manage_bots", True)
    gate.reset()

    assert gate.consume("call-1", "manage_bots")[0] is False


# ---------------------------------------------------------------------------
# The guardrails that were voided: dry-run, risk limits, bot ownership
# ---------------------------------------------------------------------------


def test_dry_run_cannot_execute_a_real_trading_tool():
    """A dry-run agent must not reach manage_executors(create)."""
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits()), RiskState(), execution_mode="dry_run"
    )
    args = {
        "action": "create",
        "executor_config": {
            "controller_id": "test_controller",
            "total_amount_quote": 100.0,
        },
    }
    client, spy = _client_with("manage_executors", args, callback)
    events = _drive(client)

    assert not spy.ran, "dry-run agent executed a real create"
    assert _statuses(events, "blocked")


def test_dry_run_cannot_place_an_order():
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits()), RiskState(), execution_mode="dry_run"
    )
    client, spy = _client_with(
        "place_order", {"trading_pair": "BTC-USDT", "amount": 1}, callback
    )
    _drive(client)

    assert not spy.ran


def test_risk_limit_denial_prevents_the_create():
    """Over the exposure limit, the executor create must not reach the tool."""
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_position_size_quote=100.0)),
        RiskState(total_exposure=0.0),
    )
    args = {
        "action": "create",
        "executor_config": {
            "controller_id": "test_controller",
            "total_amount_quote": 5000.0,
        },
    }
    client, spy = _client_with("manage_executors", args, callback)
    events = _drive(client)

    assert not spy.ran, "an over-limit create reached the backend"
    assert _statuses(events, "blocked")


def test_bot_ownership_denial_blocks_the_deploy(tmp_path):
    """A deploy outside the session namespace never reaches manage_bots."""
    ledger = BotLedger(NS, tmp_path)
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits()), RiskState(), ledger=ledger
    )
    args = {
        "action": "deploy",
        "bot_name": "someone-elses-bot",
        "max_global_drawdown_quote": 100.0,
    }
    client, spy = _client_with("manage_bots", args, callback)
    events = _drive(client)

    assert not spy.ran, "deployed a bot outside the session's namespace"
    assert _statuses(events, "blocked")
    violations = ledger.drain_violations()
    assert violations and violations[0]["name"] == "someone-elses-bot"


def test_refused_call_tells_the_model_it_did_not_run():
    """The model gets an in-band refusal instead of a silent success."""

    async def deny(tool_call: dict, options: list[dict]) -> dict:
        return {"outcome": {"outcome": "cancelled"}}

    client, _ = _client_with("manage_bots", {"action": "deploy"}, deny)
    _drive(client)

    returns = [
        part
        for message in client._message_history
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_call_id", None) == "call-1"
        and hasattr(part, "content")
        and isinstance(part.content, str)
    ]
    assert any(REFUSAL_PREFIX in part.content for part in returns)
