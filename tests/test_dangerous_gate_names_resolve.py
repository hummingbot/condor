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
    CREATE_EXECUTOR_TOOLS,
    DANGEROUS_AMM_ACTIONS,
    DANGEROUS_BOT_ACTIONS,
    DANGEROUS_CLMM_ACTIONS,
    DANGEROUS_CONFIG_RESOURCES,
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


def test_the_swap_family_is_gated_by_name():
    """The swap twin of :func:`test_every_liquidity_moving_action_is_gated`.

    The swap tools carry no ``action`` at all since FEAT-064: ``execute_swap``
    is the only one that signs and it is gated by name, so the three reads must
    stay off the gate and the writer must stay on it. What this pins is the
    layer below: the implementation also handles ``execute_quote`` — it signs a
    quote taken earlier — and no registered tool reaches it. Registering one
    would be a small change with no obvious connection to this gate, and the
    swap would sign unconfirmed and unpriced. This test is that connection.
    """
    registered = _registered_tools()
    assert "manage_gateway_swaps" not in registered, (
        "the multiplexed swap tool is back: a free quote and a signature share a "
        "name again, and the gate is back to sniffing an action string"
    )
    assert "execute_swap" in DANGEROUS_TOOLS
    assert is_dangerous_tool_call({"tool": "execute_swap", "input": {}})

    for name in ("quote_swap", "get_swap_status", "search_swaps"):
        assert name in registered, f"{name} is no longer registered"
        assert name not in DANGEROUS_TOOLS, f"{name} reads only; gating it is noise"
        assert not is_dangerous_tool_call(
            {"tool": f"mcp__mcp-hummingbot__{name}", "input": {}}
        ), f"{name} needlessly gated"


def test_the_executor_family_is_gated_by_name():
    """The executor twin of :func:`test_the_swap_family_is_gated_by_name`.

    The typed split (FEAT-062) gave every executor type its own create tool, so
    there is no ``action`` to sniff: the five creates and ``stop_executor`` are
    gated by name, and the nine read/control tools must stay off the gate. What
    this pins is that a create can never be reintroduced under a name the gate
    does not know — the old mega-tool is asserted gone, and every registered
    ``create_*``/``stop_*`` name has to be in ``DANGEROUS_TOOLS``.
    """
    registered = _registered_tools()
    assert "manage_executors" not in registered, (
        "the multiplexed executor tool is back: a create and a list share a name "
        "again, and the gate is back to sniffing an action string"
    )

    for name in sorted(CREATE_EXECUTOR_TOOLS | {"stop_executor"}):
        assert name in registered, f"{name} is gated but no longer registered"
        assert name in DANGEROUS_TOOLS, f"{name} moves funds and must be gated"
        assert is_dangerous_tool_call(
            {"tool": f"mcp__mcp-hummingbot__{name}", "input": {}}
        ), f"{name} was not gated"

    for name in (
        "list_executors",
        "get_executor",
        "list_positions_held",
        "clear_position_held",
        "get_performance_report",
        "list_orphaned_positions",
        "resolve_orphaned_position",
        "executor_defaults",
    ):
        assert name in registered, f"{name} is no longer registered"
        assert name not in DANGEROUS_TOOLS, f"{name} reads only; gating it is noise"
        assert not is_dangerous_tool_call(
            {"tool": f"mcp__mcp-hummingbot__{name}", "input": {}}
        ), f"{name} needlessly gated"

    # Every executor-creating name the server registers must be gated: a sixth
    # executor type added later without a gate entry fails here.
    creates = {
        name
        for name in registered
        if name.startswith("create_") and name.endswith("_executor")
    }
    assert creates == set(CREATE_EXECUTOR_TOOLS), (
        "registered create tools and the gate list disagree: "
        f"{sorted(creates ^ set(CREATE_EXECUTOR_TOOLS))}"
    )


def test_a_gated_executor_call_names_what_it_will_do():
    """The confirmation prompt must show the size, not the bare tool name."""
    from handlers.agents.confirmation import format_tool_summary

    for call, expected in (
        (
            {
                "tool": "create_grid_executor",
                "input": {"trading_pair": "SOL-USDT", "total_amount_quote": 500},
            },
            "Create grid executor on SOL-USDT for 500 quote",
        ),
        (
            {
                "tool": "create_position_executor",
                "input": {"trading_pair": "BTC-USDT", "amount": 0.01},
            },
            "Create position executor on BTC-USDT of 0.01",
        ),
        (
            {
                "tool": "create_dca_executor",
                "input": {"trading_pair": "ETH-USDT", "amounts_quote": [50, 50]},
            },
            "Create dca executor on ETH-USDT for 100 quote over 2 levels",
        ),
        (
            {
                "tool": "create_lp_executor",
                "input": {"trading_pair": "SOL-USDC", "quote_amount": 25},
            },
            "Create lp executor on SOL-USDC with 0 base / 25 quote",
        ),
        (
            {"tool": "stop_executor", "input": {"executor_id": "abcdef012345678"}},
            "Stop executor abcdef012345",
        ),
    ):
        summary = format_tool_summary(call)
        assert expected in summary, f"{call['tool']} rendered {summary!r}"
        assert summary != call["tool"]


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


def test_gateway_config_gates_nothing_now_that_wallets_are_read_only():
    """manage_gateway_config is gated on resource_type, not action — and gates nothing.

    `wallets` was the one gated resource because `add` took a private key; that path
    is gone (wallets are read-only over MCP, FEAT-065). Everything the tool still
    edits is Gateway's own symbol/address mapping — deleting a token moves no funds
    and changes nothing on-chain, so gating it would stop a config edit while leaving
    the trades it enables ungated.
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
    assert DANGEROUS_CONFIG_RESOURCES == set()

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
