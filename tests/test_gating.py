"""Tool-call classification across producer shapes (§9.5.4 regression).

Three dict shapes reach the gates: the web chat's ``tool``/``input``, the raw
ACP permission payload's ``title``/``rawInput``, and the engine's folded
stream dict's ``name``/``input``. Reading only one shape made the audit
``mutating`` flag permanently False and the risk gate a no-op for MCP-named
executor calls.
"""

from condor.agents.gating import (
    is_dangerous_tool_call,
    tool_call_input,
    tool_call_name,
)


def test_name_resolution_across_shapes():
    assert tool_call_name({"tool": "manage_executors"}) == "manage_executors"
    assert (
        tool_call_name({"title": "mcp__condor__manage_executors"})
        == "manage_executors"
    )
    assert (
        tool_call_name({"name": "mcp__condor__manage_executors"})
        == "manage_executors"
    )
    assert tool_call_name({}) == ""


def test_input_resolution_across_shapes():
    assert tool_call_input({"rawInput": {"action": "create"}}) == {"action": "create"}
    assert tool_call_input({"input": {"action": "stop"}}) == {"action": "stop"}
    assert tool_call_input({}) is None
    assert tool_call_input({"input": "not-a-dict"}) is None


def test_folded_stream_create_is_dangerous():
    # The engine's audit shape — run 4 logged mutating: False for creates.
    assert is_dangerous_tool_call(
        {"name": "mcp__condor__manage_executors", "input": {"action": "create"}}
    )


def test_acp_permission_create_is_dangerous():
    assert is_dangerous_tool_call(
        {"title": "mcp__condor__manage_executors", "rawInput": {"action": "stop"}}
    )


def test_read_only_actions_are_safe():
    assert not is_dangerous_tool_call(
        {"name": "mcp__condor__manage_executors", "input": {"action": "list"}}
    )
    assert not is_dangerous_tool_call(
        {"name": "mcp__condor__record_learning", "input": {"text": "x"}}
    )


def test_missing_arguments_fail_toward_dangerous():
    # Arguments unavailable → the gate must check, never auto-approve.
    assert is_dangerous_tool_call({"name": "mcp__condor__manage_executors"})
    assert is_dangerous_tool_call({"title": "mcp__condor__manage_executors"})
