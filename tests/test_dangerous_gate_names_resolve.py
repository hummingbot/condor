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
    DANGEROUS_CONFIG_RESOURCES,
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


def test_every_signing_swap_action_is_gated():
    """The swap twin of :func:`test_every_liquidity_moving_action_is_gated`.

    ``manage_gateway_swaps`` registers four actions and only ``execute`` signs,
    so ``DANGEROUS_SWAP_ACTIONS`` covers the surface today. The reason to pin it
    is what sits one layer down: the implementation also handles
    ``execute_quote`` — it signs a quote taken earlier by ``action="quote"`` —
    and that action is simply not in the registered ``Literal``, so nothing can
    reach it. Adding it to that Literal would be a one-word change with no
    obvious connection to this gate, and the swap would sign unconfirmed and
    unpriced. This test is that connection.
    """
    read_only = {"quote", "search", "get_status"}
    writes = _action_literals("manage_gateway_swaps") - read_only
    assert writes <= DANGEROUS_SWAP_ACTIONS, (
        "manage_gateway_swaps signing action(s) ungated: "
        f"{sorted(writes - DANGEROUS_SWAP_ACTIONS)}"
    )


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


def test_gateway_config_gates_wallets_and_only_wallets():
    """manage_gateway_config is gated on resource_type, not action.

    `wallets` + `add` takes a private key, so it needs a human. Everything else the
    tool edits is Gateway's own symbol/address mapping — deleting a token moves no
    funds and changes nothing on-chain, so gating it would stop a config edit while
    leaving the trades it enables ungated.
    """
    fn = _registered_tools()["manage_gateway_config"]
    resources = {
        str(v)
        for v in typing.get_args(
            inspect.signature(fn).parameters["resource_type"].annotation
        )
        if isinstance(v, str)
    }
    assert DANGEROUS_CONFIG_RESOURCES <= resources, (
        f"gated resource(s) the tool has no such value for: "
        f"{sorted(DANGEROUS_CONFIG_RESOURCES - resources)}"
    )
    assert DANGEROUS_CONFIG_RESOURCES == {"wallets"}

    assert is_dangerous_tool_call(
        {
            "tool": "manage_gateway_config",
            "input": {"resource_type": "wallets", "action": "add"},
        }
    )
    for resource in resources - DANGEROUS_CONFIG_RESOURCES:
        for action in ("list", "add", "delete"):
            assert not is_dangerous_tool_call(
                {
                    "tool": "manage_gateway_config",
                    "input": {"resource_type": resource, "action": action},
                }
            ), f"{resource}/{action} should not need confirmation"


def test_gateway_config_fails_closed_on_an_unreadable_resource():
    """SEC-093: a call whose resource_type cannot be read is treated as dangerous."""
    for bad in ({}, {"resource_type": None}, {"resource_type": 7}, {"action": "add"}):
        assert is_dangerous_tool_call({"tool": "manage_gateway_config", "input": bad})
