"""The ACP wire shape reaches the dangerous-tool gate (SEC-093).

ACP delivers a tool call's arguments under ``rawInput``; every consumer in this
repo reads ``input``. Untranslated, ``manage_bots(deploy)`` and friends resolved
``action == ""``, ``is_dangerous_tool_call`` said "harmless", and the permission
callback took the auto-approve fast path — the Approve/Reject keyboard existed
but was never reached.

These drive the real entry point (``ACPClient._on_request_permission``) into the
real callback (``build_permission_callback``) so the translation is pinned where
it happens, not just in the helper. The other half is fail-closed behaviour: a
call whose arguments can't be read must be gated, never waved through.
"""

import asyncio
import json

from condor.acp.client import ACPClient, ToolCallEvent, normalize_tool_call
from condor.agents.ownership import BotLedger
from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)
from condor.runtime import confirmations as confirmations_module
from condor.runtime.confirmations import ConfirmationRegistry, build_permission_callback
from handlers.agents._shared import is_dangerous_tool_call, tool_call_input

USER_ID = 42
SESSION_KEY = "tg:42:main"
OPTIONS = [{"optionId": "allow", "kind": "allow_once"}, {"optionId": "deny"}]


def _acp_request(tool: str, raw_input) -> dict:
    """A toolCall exactly as the ACP bridge sends it: name in ``title``,
    arguments in ``rawInput``, and no ``input`` key at all."""
    return {
        "toolCallId": "1",
        "title": tool,
        "status": "pending",
        "rawInput": raw_input,
    }


class _CapturingChannel:
    """Stands in for Telegram/the dashboard: records what it was asked to render."""

    def __init__(self, answer: bool | None = True):
        self.answer = answer
        self.delivered = []

    async def deliver(self, pending):
        self.delivered.append(pending)
        if self.answer is not None:
            await confirmations_module._registry.resolve(
                pending.id, approved=self.answer, by_user_id=USER_ID
            )


def _drive_acp(tool_call: dict, channel) -> dict:
    """Feed an ACP-shaped request through the client into the shared gate."""

    async def run():
        fresh = ConfirmationRegistry()
        confirmations_module._registry = fresh
        try:
            client = ACPClient(
                command="true",
                permission_callback=build_permission_callback(
                    SESSION_KEY, USER_ID, channels=[channel], timeout_seconds=5
                ),
            )
            return await client._on_request_permission(
                sessionId="s", options=OPTIONS, toolCall=tool_call
            )
        finally:
            confirmations_module._registry = ConfirmationRegistry()

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# The wire shape reaches the gate
# ---------------------------------------------------------------------------


def test_acp_rawinput_is_translated_to_input():
    normalized = normalize_tool_call(
        _acp_request("mcp__mcp-hummingbot__manage_bots", {"action": "deploy"})
    )
    assert normalized["input"] == {"action": "deploy"}
    assert normalized["tool"] == "mcp__mcp-hummingbot__manage_bots"
    assert is_dangerous_tool_call(normalized)


def test_acp_deploy_asks_a_human_instead_of_auto_approving():
    """The regression: this used to return an allow outcome with nothing rendered."""
    channel = _CapturingChannel(answer=False)
    result = _drive_acp(
        _acp_request(
            "mcp__mcp-hummingbot__manage_bots",
            {"action": "deploy", "bot_name": "x", "max_global_drawdown_quote": 50},
        ),
        channel,
    )

    assert len(channel.delivered) == 1, "no confirmation was ever raised"
    # The human has to be able to see WHAT they are approving: an ACP tool name
    # that reached the summary unstripped rendered as a bare wire name.
    assert channel.delivered[0].summary == "Deploy bot 'x' with controllers []"
    assert result["outcome"]["outcome"] == "cancelled"


def test_acp_deploy_approved_by_a_human_runs():
    channel = _CapturingChannel(answer=True)
    result = _drive_acp(
        _acp_request(
            "mcp__mcp-hummingbot__manage_executors",
            {"action": "create", "executor_config": {"controller_id": "c"}},
        ),
        channel,
    )

    assert len(channel.delivered) == 1
    assert result["outcome"] == {"outcome": "selected", "optionId": "allow"}


def test_read_only_acp_call_still_takes_the_fast_path():
    """Translating the shape must not start gating harmless reads."""
    channel = _CapturingChannel()
    result = _drive_acp(
        _acp_request("mcp__mcp-hummingbot__manage_bots", {"action": "status"}), channel
    )

    assert channel.delivered == []
    assert result["outcome"] == {"outcome": "selected", "optionId": "allow"}


def test_session_update_records_the_arguments():
    """Transcripts recorded ``"input": null`` for all 123 ACP tool calls."""
    client = ACPClient(command="true")
    client._on_session_update(
        "s",
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "1",
            "title": "manage_bots",
            "rawInput": {"action": "deploy"},
        },
    )
    event = client._event_queue.get_nowait()
    assert isinstance(event, ToolCallEvent)
    assert event.input == {"action": "deploy"}


# ---------------------------------------------------------------------------
# Fail closed: arguments we cannot read are dangerous
# ---------------------------------------------------------------------------


def test_unreadable_arguments_are_gated_not_approved():
    for raw in (None, "not json", ["deploy"], {}):
        call = normalize_tool_call(_acp_request("manage_bots", raw))
        assert is_dangerous_tool_call(call), f"{raw!r} slipped past the gate"


def test_unreadable_arguments_reach_a_human():
    channel = _CapturingChannel(answer=False)
    result = _drive_acp(_acp_request("manage_bots", None), channel)

    assert len(channel.delivered) == 1
    assert "could not be read" in channel.delivered[0].summary
    assert result["outcome"]["outcome"] == "cancelled"


def test_json_string_arguments_are_parsed():
    """OpenAI-compatible providers send arguments as a JSON string."""
    call = normalize_tool_call(
        _acp_request("manage_bots", json.dumps({"action": "status"}))
    )
    assert tool_call_input(call) == {"action": "status"}
    assert not is_dangerous_tool_call(call)


def test_a_failing_callback_denies():
    """An exception in the gate must not escape as an RPC error and run the tool."""

    async def boom(tool_call, options):
        raise RuntimeError("gate exploded")

    client = ACPClient(command="true", permission_callback=boom)
    result = asyncio.run(
        client._on_request_permission(
            sessionId="s", options=OPTIONS, toolCall=_acp_request("manage_bots", {})
        )
    )
    assert result["outcome"]["outcome"] == "cancelled"


# ---------------------------------------------------------------------------
# The unattended gate (TickEngine) sees the same arguments
# ---------------------------------------------------------------------------


def _risk_callback(ledger=None, execution_mode="loop"):
    return auto_approve_with_risk_check(
        RiskEngine(RiskLimits()),
        RiskState(),
        execution_mode=execution_mode,
        ledger=ledger,
    )


def test_acp_deploy_outside_the_namespace_is_cancelled(tmp_path):
    ledger = BotLedger("brigado-ema_trend", session_dir=tmp_path)
    call = normalize_tool_call(
        _acp_request(
            "mcp__mcp-hummingbot__manage_bots",
            {
                "action": "deploy",
                "bot_name": "someone-elses-bot",
                "max_global_drawdown_quote": 10,
            },
        )
    )

    result = asyncio.run(_risk_callback(ledger)(call, OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert ledger.violations and ledger.violations[0]["name"] == "someone-elses-bot"


def test_risk_callback_cancels_unreadable_arguments():
    call = normalize_tool_call(_acp_request("manage_executors", None))
    result = asyncio.run(_risk_callback()(call, OPTIONS))
    assert result["outcome"]["outcome"] == "cancelled"


def test_dry_run_blocks_an_acp_shaped_deploy():
    call = normalize_tool_call(
        _acp_request("manage_bots", {"action": "deploy", "bot_name": "x"})
    )
    result = asyncio.run(_risk_callback(execution_mode="dry_run")(call, OPTIONS))
    assert result["outcome"]["outcome"] == "cancelled"
