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
from condor.runtime.danger import format_tool_summary
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
            "mcp__mcp-hummingbot__create_grid_executor",
            {
                "controller_id": "c",
                "connector_name": "binance",
                "trading_pair": "SOL-USDT",
                "total_amount_quote": 100,
            },
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
    call = normalize_tool_call(_acp_request("create_grid_executor", None))
    result = asyncio.run(_risk_callback()(call, OPTIONS))
    assert result["outcome"]["outcome"] == "cancelled"


def test_dry_run_blocks_an_acp_shaped_deploy():
    call = normalize_tool_call(
        _acp_request("manage_bots", {"action": "deploy", "bot_name": "x"})
    )
    result = asyncio.run(_risk_callback(execution_mode="dry_run")(call, OPTIONS))
    assert result["outcome"]["outcome"] == "cancelled"


# ---------------------------------------------------------------------------
# manage_amm moves real funds: it must be gated like any other trade (SEC-206)
# ---------------------------------------------------------------------------

AMM = "mcp__mcp-hummingbot__manage_amm"
EXECUTE_SWAP = "mcp__mcp-hummingbot__execute_swap"


def test_amm_signing_actions_are_dangerous():
    for action in ("add_liquidity", "remove_liquidity", "create_pool"):
        call = normalize_tool_call(_acp_request(AMM, {"action": action}))
        assert is_dangerous_tool_call(call), f"manage_amm({action}) was auto-approved"


def test_swap_signing_action_is_dangerous():
    """The swap left manage_amm for its own tool, and stayed gated the whole way."""
    call = normalize_tool_call(_acp_request(EXECUTE_SWAP, {"trading_pair": "SOL-USDC"}))
    assert is_dangerous_tool_call(call), "execute_swap was auto-approved"


def test_swap_reads_are_not_gated():
    """Only the writer is dangerous by name; a free quote must not need a human."""
    for name in ("quote_swap", "get_swap_status", "search_swaps"):
        call = normalize_tool_call(_acp_request(f"mcp__mcp-hummingbot__{name}", {}))
        assert not is_dangerous_tool_call(call), f"{name} needlessly gated"


def test_amm_read_actions_stay_on_the_fast_path():
    for action in (
        "pool_info",
        "position_info",
        "positions_owned",
        "quote_swap",
        "quote_liquidity",
    ):
        call = normalize_tool_call(_acp_request(AMM, {"action": action}))
        assert not is_dangerous_tool_call(
            call
        ), f"manage_amm({action}) needlessly gated"


def test_amm_with_unreadable_arguments_fails_closed():
    for raw in (None, "not json", ["execute_swap"], {}):
        call = normalize_tool_call(_acp_request(AMM, raw))
        assert is_dangerous_tool_call(call), f"{raw!r} slipped past the gate"


def test_a_swap_reaches_a_human_with_a_readable_summary():
    channel = _CapturingChannel(answer=False)
    result = _drive_acp(
        _acp_request(
            EXECUTE_SWAP,
            {
                "connector": "meteora",
                "side": "SELL",
                "amount": "12.5",
                "trading_pair": "SOL-USDC",
            },
        ),
        channel,
    )

    assert len(channel.delivered) == 1, "a swap was never put in front of a human"
    assert channel.delivered[0].summary == "Swap SELL 12.5 SOL-USDC"
    assert result["outcome"]["outcome"] == "cancelled"


def test_amm_summaries_name_what_is_being_moved():
    def summary(**args):
        return format_tool_summary({"tool": AMM, "input": args})

    assert (
        summary(
            action="add_liquidity",
            connector="raydium",
            base_token_amount="1",
            quote_token_amount="200",
            pool_address="8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj",
        )
        == "Add 1 base / 200 quote to AMM 8sLbNZoA... on raydium"
    )
    assert (
        summary(
            action="remove_liquidity",
            percentage_to_remove="100",
            position_address="9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
        )
        == "Remove 100% from AMM position 9xQeWvG8... on ?"
    )
    assert (
        summary(
            action="create_pool",
            connector="uniswap",
            base_token="WETH",
            quote_token="USDC",
            base_token_amount="0.5",
        )
        == "Create AMM pool WETH-USDC on uniswap"
    )
    # An address we were not given must not blow up a confirmation prompt.
    assert summary(action="add_liquidity") == "Add ? base / ? quote to AMM ? on ?"
    assert summary(action="quote_liquidity") == "AMM: quote_liquidity"


def test_dry_run_cancels_a_swap_but_not_a_quote():
    swap = normalize_tool_call(_acp_request(EXECUTE_SWAP, {"connector": "meteora"}))
    assert (
        asyncio.run(_risk_callback(execution_mode="dry_run")(swap, OPTIONS))["outcome"][
            "outcome"
        ]
        == "cancelled"
    )

    quote = normalize_tool_call(
        _acp_request("mcp__mcp-hummingbot__quote_swap", {"connector": "meteora"})
    )
    assert asyncio.run(_risk_callback(execution_mode="dry_run")(quote, OPTIONS))[
        "outcome"
    ] == {"outcome": "selected", "optionId": "allow"}


# ---------------------------------------------------------------------------
# Every mutating MCP action is classified, so the next tool can't reopen the hole
# ---------------------------------------------------------------------------

# manage_amm shipped ungated because nothing tied the MCP server's tool surface
# to danger.py (SEC-206). These two tests are that tie: a newly registered tool,
# or a new mutating action on an existing one, fails CI until someone classifies
# it here. danger.py itself stays stdlib-only, so the import lives in the test.

#: Tools whose actions can move funds or a live bot. Every mutating action of
#: these must be classified dangerous.
FUND_MOVING_TOOLS = {
    "manage_amm",
    "manage_bots",
    "manage_clmm",
    "manage_gateway_config",  # the wallets resource takes a private key
}

#: Tools that read, or that only write config the trading loop must be told to
#: pick up. Listed explicitly so a new tool belongs to neither set and trips
#: ``test_every_action_gated_tool_is_classified`` below.
NON_FUND_MOVING_TOOLS = {
    "manage_controllers",  # writes controller templates, never a running bot
    "manage_gateway_container",  # starts and stops Gateway; signs nothing
    "executor_defaults",  # edits a local preferences file; creates nothing
    "explore_dex_pools",
    "explore_geckoterminal",
}

#: Verbs that mean "this call changes something out in the world".
MUTATING_PREFIXES = (
    # Bare "execute", not "execute_": a tool that names its signing action
    # `execute` would match nothing under the underscore spelling. The swap that
    # motivated this is name-gated now (`execute_swap`, FEAT-064) and no longer
    # enumerated here at all, but the loose prefix stays so the next one is caught.
    "execute",
    "add_",
    "remove_",
    "create",
    "deploy",
    "stop",
    "start_",
    "update_",
)


def _action_literals() -> dict[str, list[str]]:
    """Each registered MCP tool's ``action`` values, read off its signature."""
    import inspect
    import typing

    from mcp_servers.hummingbot_api import server

    actions: dict[str, list[str]] = {}
    for tool in asyncio.run(server.mcp.list_tools()):
        fn = getattr(server, tool.name, None)
        param = inspect.signature(fn).parameters.get("action") if fn else None
        if param is None:
            continue
        annotation = param.annotation
        members: list[str] = []
        for candidate in (annotation, *typing.get_args(annotation)):
            if typing.get_origin(candidate) is typing.Literal:
                members.extend(typing.get_args(candidate))
        actions[tool.name] = members
    return actions


def test_every_action_gated_tool_is_classified():
    """A newly registered tool has to be sorted into one of the two sets."""
    registered = set(_action_literals())
    unclassified = registered - FUND_MOVING_TOOLS - NON_FUND_MOVING_TOOLS
    assert not unclassified, (
        f"MCP tools {sorted(unclassified)} are action-gated but classified nowhere; "
        "decide whether they move funds and, if so, gate them in condor/runtime/danger.py"
    )
    assert FUND_MOVING_TOOLS <= registered, (
        f"{sorted(FUND_MOVING_TOOLS - registered)} is gated but no longer registered "
        "by the MCP server — the gate for it is dead code"
    )


def test_every_mutating_action_of_a_fund_moving_tool_is_dangerous():
    checked = 0
    for tool_name, actions in _action_literals().items():
        if tool_name not in FUND_MOVING_TOOLS:
            continue
        for action in actions:
            if not action.startswith(MUTATING_PREFIXES):
                continue
            checked += 1
            call = {
                "tool": f"mcp__mcp-hummingbot__{tool_name}",
                "input": {"action": action},
            }
            assert is_dangerous_tool_call(call), (
                f"{tool_name}({action}) mutates but is auto-approved; "
                "add it to the matching DANGEROUS_* set in condor/runtime/danger.py"
            )
    # 3 AMM + 3 CLMM + 5 bot today: a floor, so a signature refactor that silently
    # stops yielding actions fails instead of passing vacuously. Neither the swap
    # nor the executor family is counted: they have no `action` since FEAT-064 and
    # FEAT-062 and are gated by name instead (see test_swap_signing_action_is_dangerous
    # and tests/test_dangerous_gate_names_resolve.py).
    assert checked >= 11, f"only {checked} mutating actions found — enumeration broke"


# ---------------------------------------------------------------------------
# control_agent(start) launches an unattended loop: it must ask first (SEC-275)
# ---------------------------------------------------------------------------

CONTROL = "mcp__condor__control_agent"


def test_starting_a_loop_asks_a_human_and_names_the_strategy():
    """Starting a loop opens hundreds of positions; one executor already asks."""
    channel = _CapturingChannel(answer=False)
    result = _drive_acp(
        _acp_request(
            CONTROL,
            {
                "action": "start",
                "strategy_id": "acme.momentum",
                "config": {"execution_mode": "loop", "total_amount_quote": 500},
            },
        ),
        channel,
    )

    assert len(channel.delivered) == 1, "the loop started with no confirmation"
    assert channel.delivered[0].summary == (
        "Start a live agent loop on 'acme.momentum' in loop mode, sized 500 quote"
    )
    assert result["outcome"]["outcome"] == "cancelled"


def test_stopping_a_loop_never_asks():
    """The brakes stay on the fast path: no prompt between a user and their stop."""
    for action in ("list", "stop", "pause", "resume", "shutdown"):
        channel = _CapturingChannel(answer=True)
        result = _drive_acp(
            _acp_request(CONTROL, {"action": action, "agent_id": "acme.momentum.1"}),
            channel,
        )
        assert not channel.delivered, f"control_agent({action}) raised a confirmation"
        assert result["outcome"]["outcome"] == "selected"


def test_control_agent_with_unreadable_arguments_fails_closed():
    for raw in (None, "not json", ["start"], {}, {"action": 7}):
        call = normalize_tool_call(_acp_request(CONTROL, raw))
        assert is_dangerous_tool_call(call), f"{raw!r} slipped past the gate"
