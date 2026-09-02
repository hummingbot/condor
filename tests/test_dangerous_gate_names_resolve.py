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
    DANGEROUS_CONTROL_ACTIONS,
    DANGEROUS_TOOLS,
    is_dangerous_tool_call,
)
from mcp_servers.hummingbot_api import server as mcp_server

# Gate names that belong to a different MCP server than hummingbot_api.
# ``control_agent`` is on the condor orchestration server, and gets its own
# resolution test below rather than this file's Literal-reading one — its
# actions are a dict in the tool module, not an annotation.
_FOREIGN_TOOLS = {"place_order", "control_agent"}


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


# ---------------------------------------------------------------------------
# control_agent: starting a loop is the third capital path (SEC-275)
# ---------------------------------------------------------------------------


def _control_actions() -> set[str]:
    """Every action string ``control_agent`` actually accepts.

    Its actions are not a ``Literal`` on the signature — the tool takes a bare
    ``str`` and resolves it through ``_resolve_action``, which accepts both the
    short spelling (``start``) and the legacy internal one (``start_agent``).
    Both reach the same lifecycle call, so the gate has to know both.
    """
    from mcp_servers.condor.tools import trading_agent

    accepted = set(trading_agent._CONTROL_ACTIONS)
    accepted.update(
        action
        for action, (owner, _call) in trading_agent._ACTION_OWNER.items()
        if owner == "control_agent"
    )
    return accepted


def test_control_agent_is_registered_by_the_condor_server():
    from mcp_servers.condor import server as condor_server

    assert "control_agent" in DANGEROUS_TOOLS
    tool = getattr(condor_server, "control_agent", None)
    assert tool is not None, "control_agent is gated but the condor server drops it"
    # The ring's *names* live in ``profiles.py`` now (FEAT-091); what the server
    # actually mounts is the resolved tuple, which is the honest thing to assert.
    assert (
        condor_server.control_agent in condor_server.TOOL_PROFILES["agent"]
    ), "control_agent is gated but no seat mounts it — the gate is dead code"


def test_gated_control_actions_exist_on_the_tool():
    unknown = DANGEROUS_CONTROL_ACTIONS - _control_actions()
    assert not unknown, f"control_agent has no such action(s): {sorted(unknown)}"


def test_both_spellings_of_start_are_gated():
    """``start`` and its legacy alias ``start_agent`` reach the same live loop."""
    for action in ("start", "start_agent"):
        assert is_dangerous_tool_call(
            {
                "tool": "mcp__condor__control_agent",
                "input": {"action": action, "strategy_id": "acme.momentum"},
            }
        ), f"control_agent({action}) launches a live loop unconfirmed"


def test_control_reads_and_brakes_are_not_gated():
    """Reads and the brakes stay on the fast path — a prompt there is harmful."""
    for action in sorted(_control_actions() - DANGEROUS_CONTROL_ACTIONS):
        assert not is_dangerous_tool_call(
            {
                "tool": "mcp__condor__control_agent",
                "input": {"action": action, "agent_id": "acme.momentum.1"},
            }
        ), f"control_agent({action}) needlessly gated"


def test_a_dca_ladder_of_numeric_strings_still_states_its_size():
    """The MCP schema says list[float], but the summary sees the raw wire value.

    pydantic coerces ["100", "100"] happily, so the call is valid — but that
    coercion happens inside the MCP server, long after the confirmation is
    rendered. Summing the raw list raised, and a raise inside the permission
    callback is a silent cancellation of a funds call nobody ever saw.
    """
    from handlers.agents.confirmation import format_tool_summary

    summary = format_tool_summary(
        {
            "tool": "mcp__mcp-hummingbot__create_dca_executor",
            "input": {"trading_pair": "SOL-USDC", "amounts_quote": ["100", "100"]},
        }
    )
    assert summary == "Create dca executor on SOL-USDC for 200 quote over 2 levels"

    # The "$100" quote-denominated form risk._quote_amount already accepts.
    dollars = format_tool_summary(
        {
            "tool": "create_dca_executor",
            "input": {"trading_pair": "SOL-USDC", "amounts_quote": ["$50", 50.0]},
        }
    )
    assert dollars == "Create dca executor on SOL-USDC for 100 quote over 2 levels"


def test_an_unreadable_dca_rung_drops_the_total_instead_of_raising():
    """A rung we cannot parse means we cannot state the size — so we don't.

    The depth is still true and still rendered; the total is omitted rather than
    partially summed, because a summary that understates what is being approved
    is worse than one that says less.
    """
    from handlers.agents.confirmation import format_tool_summary

    for amounts in ([100, None], ["abc", 1], [{"amount": 1}], ["nan", 1], [1, "inf"]):
        summary = format_tool_summary(
            {
                "tool": "create_dca_executor",
                "input": {"trading_pair": "SOL-USDC", "amounts_quote": amounts},
            }
        )
        assert "Create dca executor on SOL-USDC" in summary, amounts
        assert f"over {len(amounts)} levels" in summary, amounts
        assert "quote" not in summary, f"{amounts!r} rendered a total it cannot know"


def test_every_other_summarized_number_survives_arriving_as_a_string():
    """Amounts are not the only figure a model can send quoted.

    Prices, levels, spreads and percentages ride the same uncoerced wire, so the
    whole summary path is pinned against string-typed numerics at once.
    """
    from handlers.agents.confirmation import format_tool_summary

    for call, expected in (
        (
            {
                "tool": "create_grid_executor",
                "input": {"trading_pair": "SOL-USDT", "total_amount_quote": "500"},
            },
            "Create grid executor on SOL-USDT for 500 quote",
        ),
        (
            {
                "tool": "create_position_executor",
                "input": {"trading_pair": "BTC-USDT", "amount": "0.01"},
            },
            "Create position executor on BTC-USDT of 0.01",
        ),
        (
            {
                "tool": "create_lp_executor",
                "input": {
                    "trading_pair": "SOL-USDC",
                    "base_amount": "1.5",
                    "quote_amount": "25",
                },
            },
            "Create lp executor on SOL-USDC with 1.5 base / 25 quote",
        ),
        (
            {
                "tool": "place_order",
                "input": {
                    "trade_type": "BUY",
                    "trading_pair": "SOL-USDC",
                    "amount": "1",
                    "order_type": "LIMIT",
                    "price": "100",
                    "connector_name": "binance",
                },
            },
            "BUY 1 SOL-USDC (LIMIT) @ 100 on binance",
        ),
        (
            {
                "tool": "execute_swap",
                "input": {"trading_pair": "SOL-USDC", "side": "BUY", "amount": "1.25"},
            },
            "Swap BUY 1.25 SOL-USDC",
        ),
        (
            {
                "tool": "manage_clmm",
                "input": {
                    "action": "open",
                    "connector": "meteora",
                    "pool_address": "poolpoolpool",
                    "lower_price": "180.5",
                    "upper_price": "220",
                },
            },
            "over 180.5-220",
        ),
        (
            {
                "tool": "manage_amm",
                "input": {
                    "action": "add_liquidity",
                    "connector": "raydium",
                    "base_token_amount": "2",
                    "quote_token_amount": "400",
                },
            },
            "Add 2 base / 400 quote",
        ),
        (
            {
                "tool": "manage_clmm",
                "input": {
                    "action": "remove_liquidity",
                    "connector": "meteora",
                    "percentage_to_remove": "50",
                },
            },
            "Remove 50% from",
        ),
        (
            {
                "tool": "mcp__condor__control_agent",
                "input": {
                    "action": "start",
                    "strategy_id": "acme.momentum",
                    "config": {"total_amount_quote": "500"},
                },
            },
            "sized 500 quote",
        ),
    ):
        summary = format_tool_summary(call)
        assert expected in summary, f"{call['tool']} rendered {summary!r}"
        assert summary != call["tool"]


def test_control_agent_fails_closed_on_an_unreadable_action():
    """SEC-093: a call we cannot classify is a call that goes to a human."""
    for bad in ({}, {"action": None}, {"action": 7}, {"action": ""}, "not json", None):
        assert is_dangerous_tool_call(
            {"tool": "mcp__condor__control_agent", "input": bad}
        ), f"{bad!r} slipped past the gate"


def test_a_gated_start_names_the_strategy_it_will_run():
    """The prompt must show the loop being started, not an opaque config blob."""
    from handlers.agents.confirmation import format_tool_summary

    summary = format_tool_summary(
        {
            "tool": "mcp__condor__control_agent",
            "input": {
                "action": "start",
                "strategy_id": "acme.momentum",
                "config": {"execution_mode": "loop", "total_amount_quote": 500},
            },
        }
    )
    assert "acme.momentum" in summary
    assert "loop" in summary
    assert "500" in summary
    assert summary != "control_agent"

    # A start with no overrides still names its strategy rather than rendering "?"
    bare = format_tool_summary(
        {
            "tool": "control_agent",
            "input": {"action": "start", "strategy_id": "acme.momentum"},
        }
    )
    assert "acme.momentum" in bare


def test_the_tick_seat_cannot_reach_control_agent():
    """A tick must not be able to launch another loop; it never mounts the tool."""
    from mcp_servers.condor import server as condor_server

    assert condor_server.control_agent not in condor_server.TOOL_PROFILES["tick"]
