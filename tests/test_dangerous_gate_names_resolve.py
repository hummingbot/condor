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

from condor.runtime.danger import READ_ONLY_CONFIG_ACTIONS, is_mutating_tool_call
from handlers.agents._shared import (
    CREATE_EXECUTOR_TOOLS,
    DANGEROUS_AMM_ACTIONS,
    DANGEROUS_BOT_ACTIONS,
    DANGEROUS_CLMM_ACTIONS,
    DANGEROUS_CONTROL_ACTIONS,
    DANGEROUS_TOOLS,
    is_dangerous_tool_call,
)
from mcp_servers.condor import server as condor_mcp_server
from mcp_servers.hummingbot_api import server as mcp_server

# Gate names that no MCP server registers as a tool of its own.
# ``place_order`` is gated by name without a tool behind it.
_FOREIGN_TOOLS = {"place_order"}


def _registered_tools() -> dict:
    """Every function either MCP server registers as a tool.

    Both are read because the gate spans them: ``control_agent`` lives on the
    condor orchestration server and the rest on hummingbot_api. The two share
    no tool name, so one flat mapping resolves every gated name.
    """
    return {
        name: obj.fn if hasattr(obj, "fn") else obj
        for server in (mcp_server, condor_mcp_server)
        for name, obj in vars(server).items()
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
        # Not a gate but the log's read set: a rename of `get` would record every
        # config read as a write, so the reads have to keep resolving too.
        ("manage_gateway_config", READ_ONLY_CONFIG_ACTIONS),
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


def _config_resource_literals() -> set[str]:
    """Every ``resource_type`` ``manage_gateway_config`` actually accepts."""
    fn = _registered_tools()["manage_gateway_config"]
    return {
        str(v)
        for v in typing.get_args(
            inspect.signature(fn).parameters["resource_type"].annotation
        )
        if isinstance(v, str)
    }


def test_gateway_config_has_no_write_left_that_needs_a_human():
    """The funds-path writes are gone from the tool rather than gated on it.

    `networks` and `connectors` used to be gated on `update` (SEC-566): a network
    config carries `nodeURL`, the RPC every transaction is broadcast through, and
    a connector config the slippage every swap inherits. Those writes now belong
    to the server owner in Condor and the tool cannot name them, so every call it
    accepts is a read or a token/pool mapping edit, and none of them asks.
    """
    assert "update" not in _action_literals("manage_gateway_config")
    assert "manage_gateway_config" not in DANGEROUS_TOOLS

    for resource in _config_resource_literals():
        for action in _action_literals("manage_gateway_config"):
            assert not is_dangerous_tool_call(
                {
                    "tool": "manage_gateway_config",
                    "input": {"resource_type": resource, "action": action},
                }
            ), f"{resource}/{action} should not need confirmation"


# ---------------------------------------------------------------------------
# control_agent: starting a loop is the third capital path (SEC-275)
# ---------------------------------------------------------------------------


def _control_actions() -> set[str]:
    """Every action string ``control_agent`` actually accepts.

    Read off the signature like every other gated tool (ARCH-568). The
    ``Literal`` carries both the short spelling (``start``) and the legacy
    internal one (``start_agent``), because ``_resolve_action`` still answers
    to both and they reach the same lifecycle call — so the gate has to know
    both, and the schema has to advertise both.
    """
    return _action_literals("control_agent")


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


# ---------------------------------------------------------------------------
# The log's predicate: what changed the world (FEAT-097)
# ---------------------------------------------------------------------------
#
# ``is_mutating_tool_call`` asks a different question than the gate over the
# same sets. These pin the two places it must differ (the ungated brakes, the
# ungated config edits), the fail-open rule, and — the load-bearing one — that
# it is a superset of the gate, so a newly gated action can never become an
# unrecorded one.


def _call(tool: str, **args) -> dict:
    return {"tool": tool, "input": args}


def test_a_create_and_a_stop_are_recorded():
    for tool in CREATE_EXECUTOR_TOOLS:
        assert is_mutating_tool_call(_call(tool, trading_pair="SOL-USDC"))
    assert is_mutating_tool_call(_call("stop_executor", executor_id="abc"))
    assert is_mutating_tool_call(_call("place_order", trading_pair="SOL-USDC"))
    assert is_mutating_tool_call(_call("execute_swap", trading_pair="SOL-USDC"))


def test_a_bot_deploy_is_recorded_and_a_bot_status_is_not():
    assert is_mutating_tool_call(_call("manage_bots", action="deploy", bot_name="b"))
    assert not is_mutating_tool_call(
        _call("manage_bots", action="status", bot_name="b")
    )
    assert not is_mutating_tool_call(_call("manage_bots", action="logs", bot_name="b"))


def test_the_ungated_brakes_are_recorded():
    """The gate lets a stop through; the log must still say it happened."""
    for action in ("stop", "pause", "resume", "shutdown", "stop_agent"):
        call = _call("control_agent", action=action, agent_id="a.b_1")
        assert not is_dangerous_tool_call(call), f"{action} became gated"
        assert is_mutating_tool_call(call), f"{action} went unrecorded"
    assert not is_mutating_tool_call(_call("control_agent", action="list"))
    assert not is_mutating_tool_call(_call("control_agent", action="get_state"))


def test_an_ungated_config_edit_is_recorded():
    """A token edit is ungated on purpose; the log keeps it anyway."""
    for action in ("add", "delete", "save"):
        call = _call("manage_gateway_config", action=action, resource_type="tokens")
        assert not is_dangerous_tool_call(call)
        assert is_mutating_tool_call(call)
    for action in ("list", "get"):
        assert not is_mutating_tool_call(
            _call("manage_gateway_config", action=action, resource_type="tokens")
        )


def test_the_log_fails_open_where_the_gate_fails_closed():
    """An action nobody has heard of is recorded, not dropped."""
    assert is_mutating_tool_call({"tool": "manage_bots", "input": None})
    assert is_mutating_tool_call({"tool": "manage_bots", "input": "not json"})
    assert is_mutating_tool_call(_call("manage_bots", bot_name="b"))
    # A dispatch tool that grew an action neither set names.
    assert is_mutating_tool_call(_call("manage_bots", action="teleport"))
    assert is_mutating_tool_call(_call("manage_clmm", action="rebalance"))
    assert is_mutating_tool_call(_call("control_agent", action="reincarnate"))
    assert is_mutating_tool_call(_call("manage_gateway_config", action="mutate"))


def test_a_read_only_tool_is_not_recorded():
    for tool in ("get_prices", "list_executors", "quote_swap", "manage_controllers"):
        assert not is_mutating_tool_call(_call(tool, action="list"))


def _every_plausible_call() -> list[dict]:
    """One call per (tool, action/resource) the registry accepts, plus the junk.

    Built from the tools themselves rather than a hand-written list, so an
    action added to a gated tool lands in the subset assertion below on its own.
    """
    calls: list[dict] = []
    for tool in (
        "manage_bots",
        "manage_clmm",
        "manage_amm",
        "manage_gateway_config",
    ):
        for action in _action_literals(tool):
            calls.append(_call(tool, action=action, resource_type="tokens"))
            calls.append(_call(tool, action=action))
    for resource in _config_resource_literals():
        calls.append(_call("manage_gateway_config", resource_type=resource))
        # Every real (resource, action) pair, held to `dangerous ⊆ mutating`.
        for action in _action_literals("manage_gateway_config"):
            calls.append(
                _call("manage_gateway_config", resource_type=resource, action=action)
            )
    for action in _control_actions():
        calls.append(_call("control_agent", action=action, agent_id="a.b_1"))
    for tool in sorted(DANGEROUS_TOOLS | {"manage_bots"}):
        calls.append(_call(tool))
        calls.append({"tool": tool, "input": None})
        calls.append({"tool": tool, "input": {"action": None}})
        calls.append({"tool": f"mcp__mcp-hummingbot__{tool}", "input": {}})
    return calls


def test_everything_the_gate_stops_the_log_records():
    """``dangerous ⊆ mutating``, over every call either predicate can see.

    The whole risk of having two predicates over one set of tools is drift. A
    call that needs a human and leaves no trace is the drift that matters: the
    band would confidently show the *previous* deed while a human was approving
    a new one.
    """
    for call in _every_plausible_call():
        if is_dangerous_tool_call(call):
            assert is_mutating_tool_call(call), f"gated but unrecorded: {call}"


def test_every_liquidity_moving_action_is_recorded():
    """The log twin of :func:`test_every_liquidity_moving_action_is_gated`."""
    read_only = {"pool_info", "position_info", "positions_owned", "quote_liquidity"}
    for tool in ("manage_clmm", "manage_amm", "manage_bots"):
        for action in (
            _action_literals(tool) - read_only - {"status", "logs", "get_config"}
        ):
            assert is_mutating_tool_call(
                _call(tool, action=action)
            ), f"{tool}:{action} writes but is not recorded"
