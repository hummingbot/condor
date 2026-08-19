"""Every safety-gate name must resolve to a real MCP tool and a real action.

The gates are string sets matched against tool names and action literals. A name
that no longer matches anything does not fail loudly — it silently stops gating,
so the call executes unconfirmed and, in dry-run mode, executes for real. That is
exactly what happened: the lists named ``manage_gateway_clmm`` (registered as
``manage_clmm``) with actions ``open_position``/``close_position`` (registered as
``open``/``close``), and omitted ``manage_amm`` altogether, so every AMM and CLMM
write bypassed both gates.

These assert the gate strings against the tool registry itself, so a rename on
either side fails here instead of in production.
"""

import inspect
import typing

from handlers.agents._shared import (
    DANGEROUS_AMM_ACTIONS,
    DANGEROUS_BOT_ACTIONS,
    DANGEROUS_CLMM_ACTIONS,
    DANGEROUS_EXECUTOR_ACTIONS,
    DANGEROUS_SWAP_ACTIONS,
    DANGEROUS_TOOLS,
    is_dangerous_tool_call,
)
from mcp_servers.hummingbot_api import server as mcp_server

# Gate names that belong to a different MCP server than hummingbot_api.
_FOREIGN_TOOLS = {"place_order"}


def _registered_tools() -> dict:
    """Every function the hummingbot_api MCP server registers as a tool."""
    return {
        name: obj.fn if hasattr(obj, "fn") else obj
        for name, obj in vars(mcp_server).items()
        if callable(obj) and not name.startswith("_")
    }


def _action_literals(tool_name: str) -> set[str]:
    """The action literals a registered tool actually accepts."""
    fn = _registered_tools()[tool_name]
    annotation = inspect.signature(fn).parameters["action"].annotation
    # `action` is either a bare Literal[...] or `Literal[...] | None`.
    literals = {str(v) for v in typing.get_args(annotation) if isinstance(v, str)}
    for arg in typing.get_args(annotation):
        literals.update(str(v) for v in typing.get_args(arg) if isinstance(v, str))
    return literals


def test_every_gated_tool_name_is_registered():
    for tool_name in DANGEROUS_TOOLS - _FOREIGN_TOOLS:
        assert (
            tool_name in _registered_tools()
        ), f"{tool_name} is gated but not registered — the gate matches nothing"


def test_gated_actions_exist_on_their_tools():
    for tool_name, actions in (
        ("manage_clmm", DANGEROUS_CLMM_ACTIONS),
        ("manage_amm", DANGEROUS_AMM_ACTIONS),
        ("manage_gateway_swaps", DANGEROUS_SWAP_ACTIONS),
        ("manage_executors", DANGEROUS_EXECUTOR_ACTIONS),
        ("manage_bots", DANGEROUS_BOT_ACTIONS),
    ):
        available = _action_literals(tool_name)
        unknown = actions - available
        assert not unknown, f"{tool_name} has no such action(s): {sorted(unknown)}"


def test_every_liquidity_moving_action_is_gated():
    """Read-only actions stay ungated; anything that moves funds must be gated."""
    read_only = {"pool_info", "position_info", "positions_owned", "quote_liquidity"}

    for tool_name, gated in (
        ("manage_clmm", DANGEROUS_CLMM_ACTIONS),
        ("manage_amm", DANGEROUS_AMM_ACTIONS),
    ):
        writes = _action_literals(tool_name) - read_only
        assert (
            writes <= gated
        ), f"{tool_name} write action(s) ungated: {sorted(writes - gated)}"


def test_lp_writes_require_confirmation():
    for tool_name, action in (
        ("manage_clmm", "open"),
        ("manage_clmm", "close"),
        ("manage_clmm", "remove_liquidity"),
        ("manage_amm", "add_liquidity"),
        ("manage_amm", "create_pool"),
    ):
        call = {"tool": tool_name, "input": {"action": action}}
        assert is_dangerous_tool_call(call), f"{tool_name}({action}) was not gated"


def test_lp_reads_do_not_require_confirmation():
    for tool_name, action in (
        ("manage_clmm", "position_info"),
        ("manage_amm", "pool_info"),
        ("manage_amm", "quote_liquidity"),
    ):
        call = {"tool": tool_name, "input": {"action": action}}
        assert not is_dangerous_tool_call(call), f"{tool_name}({action}) gated a read"


def test_gated_calls_render_a_specific_confirmation_summary():
    """A gated call must describe itself in the approval prompt.

    The summary renderer branched on ``manage_gateway_clmm`` too, so once the
    gate started matching, the prompt fell through to the generic branch and
    asked the user to approve the bare string "manage_clmm" — a confirmation
    that shows nothing to confirm.
    """
    from handlers.agents.confirmation import format_tool_summary

    for tool_name, action, expected in (
        ("manage_clmm", "open", "Open CLMM position"),
        ("manage_clmm", "close", "Close CLMM position"),
        ("manage_clmm", "remove_liquidity", "Remove"),
        ("manage_amm", "add_liquidity", "Add"),
        ("manage_amm", "create_pool", "Create AMM pool"),
    ):
        summary = format_tool_summary(
            {"tool": tool_name, "input": {"action": action, "connector": "meteora"}}
        )
        assert expected in summary, f"{tool_name}({action}) rendered {summary!r}"
        assert summary != tool_name
