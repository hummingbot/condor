"""Security-critical tool-call classification (ARCH-190).

Which tool calls need a human, which are refused outright, and how a pending
call is summarized for the confirmation prompt. This used to live in the
Telegram package (``handlers/agents/_shared.py`` / ``confirmation.py``), which
``main.py`` hot-reloads via watchfiles — so the runtime's trade gate depended
on reload timing. It is platform-neutral and belongs to the runtime; the
handlers modules re-export from here for their Telegram callers.

Stdlib-only on purpose: everything that dispatches on a tool name (the danger
list, the risk gate, the confirmation summary) imports this module, so it must
never grow an import edge back into the runtime or the handlers.
"""

from __future__ import annotations

import json
import math
from typing import Any

#: Every executor-creating tool, by name. The typed split (FEAT-062) gave each
#: executor type its own tool, so creating one is no longer an ``action`` inside a
#: mega-tool — reaching any of these names at all is the create.
CREATE_EXECUTOR_TOOLS = frozenset(
    {
        "create_position_executor",
        "create_grid_executor",
        "create_dca_executor",
        "create_order_executor",
        "create_lp_executor",
    }
)

#: The create tools that take a ``leverage`` parameter, and so the ones a
#: leverage limit can stand in front of (SEC-558). ``create_lp_executor`` opens
#: a CLMM position, which is not margined, and takes no leverage at all.
LEVERAGED_EXECUTOR_TOOLS = CREATE_EXECUTOR_TOOLS - {"create_lp_executor"}

#: The account-wide leverage control. It is scoped to an (account, connector,
#: trading pair) and to nothing else, so raising leverage with it moves the
#: liquidation price under *every* position on that pair -- including ones the
#: caller never opened, and including a human's (SEC-558).
LEVERAGE_TOOL = "set_account_position_mode_and_leverage"

# Tools that require user confirmation before execution
DANGEROUS_TOOLS = {
    "place_order",
    "execute_swap",  # every call signs; quote/status/search are separate tools
    "manage_clmm",  # every action that moves liquidity
    "manage_amm",  # every action that moves liquidity
    "manage_gateway_config",  # only writes to networks/connectors; see below
    "control_agent",  # only `start`, which launches an unattended trading loop
    # The executor family is gated by NAME (FEAT-062), the same way the swap family
    # is: a create and a stop each have their own tool, so there is no `action` to
    # read out of the arguments and no fail-closed ambiguity. Every other executor
    # tool (list_executors, get_executor, list_positions_held, get_performance_report,
    # list_orphaned_positions, resolve_orphaned_position, clear_position_held,
    # executor_defaults) is safe by name and never reaches a human.
    *CREATE_EXECUTOR_TOOLS,
    "stop_executor",
    # Account-wide leverage. It opens no position of its own, which is exactly
    # why it was missed: it re-prices the ones already open. A create is gated
    # on the capital it commits, and leverage is what decides how far the market
    # has to move before that capital is gone -- so the call that changes it
    # belongs in front of the same human (SEC-558). Loop mode checks it against
    # `max_leverage` in condor.agents.risk.
    LEVERAGE_TOOL,
}

# Tools that are always blocked (RBAC bypass prevention)
BLOCKED_TOOLS: set[str] = set()

# Actions within manage_bots that deploy/mutate a live bot (status/logs/get_config
# are read-only and excluded). manage_controllers itself is excluded entirely — it
# only writes controller templates/saved configs, never a running bot (see its own
# tool docstring: "Does NOT affect running bots").
DANGEROUS_BOT_ACTIONS = {
    "deploy",
    "stop_bot",
    "stop_controllers",
    "start_controllers",
    "update_config",
}

# There is no DANGEROUS_SWAP_ACTIONS: the swap family is gated by NAME. `execute_swap`
# is its own tool (FEAT-064), so the gate no longer has to read an `action` out of the
# arguments to tell a free quote from a signature — reaching `execute_swap` at all is
# the signature, and `quote_swap` / `get_swap_status` / `search_swaps` are never gated.

# Actions within manage_clmm that require confirmation. These are the tool's
# own action literals — a name that does not match one lets the call through
# ungated, so they are asserted against the registered tool in the tests.
DANGEROUS_CLMM_ACTIONS = {
    "open",
    "close",
    "add_liquidity",
    "remove_liquidity",
    "collect_fees",
    "create_pool",
}

# Actions within manage_amm that require confirmation
DANGEROUS_AMM_ACTIONS = {"add_liquidity", "remove_liquidity", "create_pool"}

# Actions within control_agent that require confirmation (SEC-275). `start` is
# the third capital path and the widest of them: it launches a TickEngine that
# trades unattended every N seconds until stopped, spawning executors and
# deploying controller-mode bots on its own — and the caller picks the
# execution_mode, total_amount_quote and risk_limits it runs with. Creating one
# executor already needs a human, so starting the loop that creates hundreds
# does too.
#
# Everything else stays on the fast path. `list`, `get_state` and `set_state`
# read or scribble on an instance's own scratch namespace. `stop`, `pause` and
# `resume` are the brakes, and a confirmation in front of a brake is a
# confirmation in front of the user stopping their own loop.
#
# `shutdown` is deliberately ungated too, though it does wind positions down: it
# is the emergency exit, and the failure mode of prompting for it (a human is
# away, the wind-down waits) is worse than the failure mode of not prompting (an
# agent exits the market early). Exposure-reducing calls are let through
# elsewhere for the same reason — see check_dex_action's remove_liquidity note.
#
# The legacy `*_agent` spellings are accepted by the tool's own _resolve_action,
# so the gate has to know both or `start_agent` walks straight past it.
DANGEROUS_CONTROL_ACTIONS = {"start", "start_agent"}

# Resource types within manage_gateway_config whose *writes* require confirmation
# (SEC-566). This set used to be empty, justified by "everything it touches is
# Gateway's own symbol/address mapping". That is true of two of the four resources
# and false of the other two, which is the correction:
#
# - `tokens` and `pools` stay ungated. Adding or deleting one edits a symbol →
#   address mapping. It moves no funds and changes nothing on-chain, so gating it
#   would put a human in front of a config edit while the trades that edit enables
#   stay where they are. `chains` and `wallets` stay ungated too — both are
#   read-only over MCP since FEAT-065 (a wallet is imported in the dashboard, and
#   `add` no longer takes a private key anywhere the model can reach).
# - `networks` and `connectors` are gated, because an `update` there is not a
#   mapping: a network config carries `nodeURL`, the RPC endpoint every transaction
#   from this server is signed against and broadcast through, and a connector config
#   carries settings such as allowed slippage that every later swap inherits. The
#   dashboard already treats exactly this write as privileged — the web route calls
#   it "a server-wide change" and demands OWNER (condor/web/routes/settings.py) —
#   while the MCP path let a model repoint it with no human in the loop. Tool output
#   is untrusted input, so "the prompt says don't call this" is not a control
#   (SEC-253).
#
# The gate is resource *and* action: `list`/`get` on a gated resource stay on the
# fast path, because reading which RPC a chain is on is how a model diagnoses a
# failed swap, and a prompt in front of a read buys nothing. An unreadable
# `resource_type` — and, on a gated resource, an unreadable `action` — still fails
# closed. See :func:`_is_dangerous_config_call`.
DANGEROUS_CONFIG_RESOURCES: set[str] = {"networks", "connectors"}


# ── What changed the world (FEAT-097) ──
#
# The sets above answer "should a human approve this". The log asks a different
# question — "did this change anything" — and the two deliberately differ:
# `manage_gateway_config` gates only its two funds-path resources (SEC-566), and
# the brakes (`stop`, `pause`, `resume`, `shutdown`) are ungated on purpose. A
# log built on the confirmation predicate would therefore be silent about every
# config edit and every brake, which is the exact silence the log exists to end.
#
# So: a sibling predicate over the *same* sets, so the two read side by side and
# a newly gated action cannot become an unrecorded one (pinned by a test).
# Everything gated is recorded; a few recorded things are deliberately ungated.

#: Mutating actions of the dispatch tools. Identical to their confirmation sets
#: where the confirmation set is already "everything that writes".
MUTATING_BOT_ACTIONS = DANGEROUS_BOT_ACTIONS
MUTATING_CLMM_ACTIONS = DANGEROUS_CLMM_ACTIONS
MUTATING_AMM_ACTIONS = DANGEROUS_AMM_ACTIONS

#: `control_agent`'s writes, including the brakes the gate lets through. The
#: legacy `*_agent` spellings are accepted by the tool's own `_resolve_action`,
#: so the log has to know both or a `stop_agent` goes unrecorded.
MUTATING_CONTROL_ACTIONS = {
    "start",
    "start_agent",
    "stop",
    "stop_agent",
    "pause",
    "pause_agent",
    "resume",
    "resume_agent",
    "shutdown",
    "shutdown_agent",
    "set_state",
}

# The reads of each dispatch tool, named explicitly. They are what keeps the
# fail-open rule below from recording a `manage_bots(action="status")`: an
# action in neither set is one this module has not heard of, and *that* is what
# gets recorded.
READ_ONLY_BOT_ACTIONS = {"status", "logs", "get_config"}
#: The liquidity tools' reads, shared because they overlap and because this is
#: the same set ``test_dangerous_gate_names_resolve`` holds the gate to.
READ_ONLY_LIQUIDITY_ACTIONS = {
    "pool_info",
    "position_info",
    "positions_owned",
    "quote_liquidity",
}
READ_ONLY_CONTROL_ACTIONS = {"list", "list_agents", "get_state"}
#: `manage_controllers`' writes and reads. The tool is outside the *gate*
#: entirely and stays there (it writes controller templates and saved configs,
#: never a running bot), but a fleet is *built* out of these calls: the twelve
#: that assembled `pmm-king-btcbrl-20260903-181000`, six of them rejected, left
#: no trace at all. Recording them is the log's question, not the gate's.
MUTATING_CONTROLLER_ACTIONS = {"upsert", "delete"}
READ_ONLY_CONTROLLER_ACTIONS = {"list", "describe"}
#: The snippet runner. Deliberately *not* in ``DANGEROUS_TOOLS`` and not to be
#: added: since ARCH-308 a tick reads a market it can compute on only through
#: ``client.market_data.*`` inside a snippet, so a name gate here would put a
#: confirmation in front of every tick's candle read (SEC-616). What it does get
#: is a log row, and a refusal in the one mode that promises nothing mutates.
CODE_RUN_TOOL = "run_code"
#: ``run_code``'s three actions split by what they touch: ``run`` executes a
#: snippet holding the unrestricted API client, and the other two only read runs
#: already stored.
MUTATING_CODE_RUN_ACTIONS = {"run"}
READ_ONLY_CODE_RUN_ACTIONS = {"history", "get"}
#: The routine library. The *other* door onto arbitrary Python, and for the same
#: reason as ``run_code``: a routine is a Python file with an ``async run()``,
#: ``create_routine`` writes one and ``run`` executes it holding the same
#: unrestricted API client. Deliberately not in ``DANGEROUS_TOOLS`` either —
#: running a routine is ordinary tick work and a name gate would prompt a human
#: for every one of them (SEC-626).
ROUTINE_TOOL = "manage_routines"
#: ``manage_routines``' twelve actions split by what they touch. The writes are
#: the two halves of the same capability: ``create_routine`` / ``edit_routine``
#: put Python on disk, ``run`` / ``run_async`` / ``start`` execute it, and
#: ``delete_routine`` takes it away again.
#:
#: ``stop`` is here rather than with the reads, which is the one place this set
#: parts company with the module's rule that a brake is never stood in front of.
#: A brake is a session stopping *its own* activity, and a dry run has none: it
#: cannot reach ``start`` or ``run_async``, so the only instance it could stop
#: belongs to a live seat, and killing that is a mutation of the shared runtime
#: rather than this session braking.
MUTATING_ROUTINE_ACTIONS = {
    "run",
    "run_async",
    "start",
    "stop",
    "create_routine",
    "edit_routine",
    "delete_routine",
}
#: The routine library's reads: what exists, what it takes, what it says, and
#: what a run already produced. None of them execute anything.
READ_ONLY_ROUTINE_ACTIONS = {
    "list",
    "describe",
    "read_routine",
    "get_instance",
    "list_instances",
}
#: The candle reader, and the answer to what refusing the two tools above costs
#: a rehearsal (CORR-625). Both of those are refused in dry-run for holding the
#: unrestricted API client, and since ARCH-308 they were between them the only
#: structured market read a tick had — so a dry run could not rehearse a
#: candle-driven decision at all, which is the whole point of one.
#:
#: This tool is the read-only path back. It takes parameters rather than code,
#: so unlike a snippet there is nothing in it to write with, and it is listed
#: here rather than left unnamed for one reason: an *unclassified* action on it
#: is refused in dry-run. A future action that does more than read cannot become
#: callable in a rehearsal by being added to the tool and forgotten here.
MARKET_DATA_TOOL = "get_market_data"
#: Empty on purpose, and the assertion this whole entry exists to make: the tool
#: has no write half. It is kept as a name rather than inlined as ``set()`` so
#: that the day one is added, there is somewhere obvious to put it.
MUTATING_MARKET_DATA_ACTIONS: set[str] = set()
#: Reading candles, reading a candle range, and asking which connectors serve
#: OHLCV at all. Pinned against the tool's registered ``Literal`` by a test, the
#: same way the routine and snippet sets are.
READ_ONLY_MARKET_DATA_ACTIONS = {"candles", "historical_candles", "connectors"}
#: How much of a snippet's first line the log row carries. A summary is one line
#: on a page, and the whole source is in the code-run store anyway.
MAX_SNIPPET_HEAD_CHARS = 80
#: `manage_gateway_config` is recorded on its *action*, not its resource type:
#: what it edits is what the gate weighs, and whether it edited at all is what
#: the log weighs.
READ_ONLY_CONFIG_ACTIONS = {"list", "get"}


def tool_call_name(tool_call: dict[str, Any]) -> str:
    """The bare tool name, with any MCP prefix stripped.

    ACP names a tool by its wire name (``mcp__mcp-hummingbot__manage_bots``)
    where the local surfaces use the bare one, so everything that dispatches on
    a tool name — the danger list, the risk gate, the confirmation summary —
    has to normalize identically or they disagree about the same call.
    """
    raw_name = tool_call.get("tool", "") or tool_call.get("title", "")
    return raw_name.rsplit("__", 1)[-1] if "__" in raw_name else raw_name


def tool_call_input(tool_call: dict[str, Any]) -> dict[str, Any] | None:
    """A tool call's arguments as a mapping, or ``None`` when unreadable.

    ``None`` means "I cannot tell what this call does", and every caller has to
    fail closed on it: an action-gated tool whose action can't be read is
    treated as dangerous rather than waved through (SEC-093). Callers must not
    reach for other spellings of the arguments — the ACP wire's ``rawInput`` is
    translated once, at the boundary in :func:`condor.acp.client.normalize_tool_call`.

    A JSON string is parsed: OpenAI-compatible providers deliver tool arguments
    that way.
    """
    args = tool_call.get("input")
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _has_dangerous_action(
    tool_call: dict[str, Any], dangerous_actions: set[str]
) -> bool:
    """Whether an action-gated tool call selects one of its dangerous actions.

    Fails closed (SEC-093): unreadable arguments, or a missing/non-string
    ``action``, count as dangerous. ``action`` is required by every one of
    these tools, so an unreadable one is never a benign read — it is a call we
    failed to understand, and those belong in front of a human.
    """
    input_data = tool_call_input(tool_call)
    if input_data is None:
        return True
    action = input_data.get("action")
    if not isinstance(action, str) or not action:
        return True
    return action in dangerous_actions


def _short_address(value: Any) -> str:
    """An on-chain address abbreviated for a confirmation line.

    The human approving a signature needs to recognize the pool, not read 44
    base58 characters, and a missing address has to render as "?" rather than
    crash the summary of a call that is about to move funds.
    """
    if not isinstance(value, str) or not value:
        return "?"
    return f"{value[:8]}..." if len(value) > 8 else value


def _quote_ladder_total(values: list[Any]) -> str | None:
    """The sum of a DCA ladder's quote amounts, or ``None`` if a rung is unreadable.

    The summary reads *wire* arguments: ``normalize_tool_call`` hands the raw
    ``rawInput`` through and the MCP server's pydantic coercion of
    ``amounts_quote: list[float]`` happens later, so a perfectly valid call can
    arrive as ``["100", "100"]``. Summing that raw raises inside the permission
    callback, and an exception there is turned into a *cancellation* by the ACP
    client — the human never sees the prompt and a legitimate create is denied
    (CORR-294). So each rung is parsed the tolerant way ``risk._quote_amount``
    already parses one, and anything that will not parse returns ``None`` so the
    caller can drop the total rather than invent or misstate one.
    """
    total = 0.0
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        if isinstance(value, str):
            value = value.strip().removeprefix("$")
        try:
            total += float(value)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(total):
        return None
    # Integral ladders keep their bare form ("100", not "100.0"): the figure a
    # human approves should read the way the model wrote it.
    return str(int(total)) if total.is_integer() else str(total)


def _executor_amount(tool_name: str, input_data: dict[str, Any]) -> str:
    """The size of a pending executor create, for the confirmation line.

    Each executor type denominates its size in a different field and a different
    currency -- base for a position, quote for a grid, a ladder of quote amounts for a
    DCA, one or both legs for an LP position. A human approving a create is approving
    that number, so it is spelled out per type rather than omitted.
    """
    if tool_name == "create_grid_executor":
        return f" for {input_data.get('total_amount_quote', '?')} quote"
    if tool_name == "create_dca_executor":
        amounts = input_data.get("amounts_quote")
        if isinstance(amounts, list) and amounts:
            total = _quote_ladder_total(amounts)
            if total is None:
                # A rung we cannot read means we cannot state the size. Say what
                # is certain — the ladder's depth — instead of a partial total
                # that would understate what the human is approving.
                return f" over {len(amounts)} levels"
            return f" for {total} quote over {len(amounts)} levels"
        return ""
    if tool_name == "create_lp_executor":
        base = input_data.get("base_amount") or 0
        quote = input_data.get("quote_amount") or 0
        return f" with {base} base / {quote} quote"
    amount = input_data.get("amount")
    return f" of {amount}" if amount is not None else ""


def _is_dangerous_config_call(tool_call: dict[str, Any]) -> bool:
    """Whether a ``manage_gateway_config`` call writes a funds-path resource (SEC-566).

    Resource *and* action, because neither half alone is the right gate. Resource
    alone would prompt on `get networks`, the read a model does to diagnose a
    failed swap; action alone would prompt on `add tokens`, a symbol → address
    mapping that moves nothing. What needs a human is a *write* to `networks` or
    `connectors`: the RPC every transaction is broadcast through, and the slippage
    every later swap inherits.

    Fails closed twice over (SEC-093). Unreadable arguments, or a missing or
    non-string ``resource_type``, are dangerous whatever the action claims to be —
    a call we cannot classify is never let through on the strength of the half of
    it we can read. On a resource we *can* read and that is gated, a missing or
    non-string ``action`` is dangerous too, so a write cannot hide behind an
    unparseable action string.
    """
    input_data = tool_call_input(tool_call)
    if input_data is None:
        return True
    resource = input_data.get("resource_type")
    if not isinstance(resource, str) or not resource:
        return True
    if resource not in DANGEROUS_CONFIG_RESOURCES:
        return False
    action = input_data.get("action")
    if not isinstance(action, str) or not action:
        return True
    return action not in READ_ONLY_CONFIG_ACTIONS


def is_dangerous_tool_call(tool_call: dict[str, Any]) -> bool:
    """Check if a tool call requires user confirmation."""
    tool_name = tool_call_name(tool_call)

    # Direct dangerous tools
    if tool_name in DANGEROUS_TOOLS:
        # For the LP tools, only the actions that move liquidity are dangerous
        if tool_name == "manage_clmm":
            return _has_dangerous_action(tool_call, DANGEROUS_CLMM_ACTIONS)

        if tool_name == "manage_amm":
            return _has_dangerous_action(tool_call, DANGEROUS_AMM_ACTIONS)

        if tool_name == "manage_gateway_config":
            return _is_dangerous_config_call(tool_call)

        if tool_name == "control_agent":
            return _has_dangerous_action(tool_call, DANGEROUS_CONTROL_ACTIONS)

        return True

    # manage_bots with deploy/stop/update actions (a bot deploy places real
    # capital via a controller, a different path than the executor tools)
    if tool_name == "manage_bots":
        return _has_dangerous_action(tool_call, DANGEROUS_BOT_ACTIONS)

    return False


def _is_mutating_action(
    tool_call: dict[str, Any], mutating: set[str], read_only: set[str]
) -> bool:
    """Whether an action-gated tool call selects an action that writes.

    The fail-open twin of :func:`_has_dangerous_action`: an action in neither
    set — a new one, or one this module has not heard of — is treated as a
    write, and so are unreadable arguments. A missing row is invisible; a
    spurious one is a line a reader can see and a maintainer can fix.
    """
    input_data = tool_call_input(tool_call)
    if input_data is None:
        return True
    action = input_data.get("action")
    if not isinstance(action, str) or not action:
        return True
    if action in mutating:
        return True
    return action not in read_only


def is_mutating_tool_call(tool_call: dict[str, Any]) -> bool:
    """Did this call change something? The log's question, not the gate's.

    Unlike :func:`is_dangerous_tool_call` this fails **open**: an action this
    module has not heard of is recorded rather than dropped. Every call the gate
    accepts, this accepts too — the sets are shared and a test pins the
    inclusion — plus the config edits and the brakes the gate deliberately lets
    through ungated.

    Only the tools this module already knows are classified. A genuinely new
    mutating tool that reaches neither predicate is unrecorded *and* unconfirmed,
    which is one exposure and not two.
    """
    tool_name = tool_call_name(tool_call)

    if tool_name == "manage_clmm":
        return _is_mutating_action(
            tool_call, MUTATING_CLMM_ACTIONS, READ_ONLY_LIQUIDITY_ACTIONS
        )

    if tool_name == "manage_amm":
        return _is_mutating_action(
            tool_call, MUTATING_AMM_ACTIONS, READ_ONLY_LIQUIDITY_ACTIONS
        )

    if tool_name == "manage_bots":
        return _is_mutating_action(
            tool_call, MUTATING_BOT_ACTIONS, READ_ONLY_BOT_ACTIONS
        )

    if tool_name == "control_agent":
        return _is_mutating_action(
            tool_call, MUTATING_CONTROL_ACTIONS, READ_ONLY_CONTROL_ACTIONS
        )

    if tool_name == "manage_gateway_config":
        # Recorded unless it is one of the two reads. The resource type is read
        # only to stay a superset of the gate, which fails closed on a missing
        # one: a call neither of us can parse is recorded rather than dropped.
        input_data = tool_call_input(tool_call)
        if input_data is None:
            return True
        resource = input_data.get("resource_type")
        if not isinstance(resource, str) or not resource:
            return True
        return _is_mutating_action(tool_call, set(), READ_ONLY_CONFIG_ACTIONS)

    # Gated by name, and every one of them writes: an order, a signature, an
    # executor create, an executor stop.
    return tool_name in CREATE_EXECUTOR_TOOLS or tool_name in {
        "place_order",
        "execute_swap",
        "stop_executor",
        LEVERAGE_TOOL,
    }


def is_code_execution_call(tool_call: dict[str, Any]) -> bool:
    """Does this call *execute* a snippet? (SEC-616)

    ``run_code(action="run")`` hands arbitrary Python the unrestricted API
    client, so it can do anything any other tool can do and several things none
    of them can. ``history`` and ``get`` only read runs already stored.

    True on an action this module cannot read, which is both the fail-open rule
    its siblings follow *and* the tool's own default: ``action`` omitted means
    ``run``. The log uses this as an extra row; the unattended gate uses it to
    refuse in dry-run, where failing this way is failing closed.
    """
    if tool_call_name(tool_call) != CODE_RUN_TOOL:
        return False
    return _is_mutating_action(
        tool_call, MUTATING_CODE_RUN_ACTIONS, READ_ONLY_CODE_RUN_ACTIONS
    )


def is_mutating_routine_call(tool_call: dict[str, Any]) -> bool:
    """Does this call *write or execute* a routine? (SEC-626)

    The sibling of :func:`is_code_execution_call` over the other door onto
    arbitrary Python. A routine is a Python file, ``create_routine`` writes one
    and ``run`` executes it holding the same unrestricted API client, so the two
    tools have the same reach and get the same answer.

    Fails closed the same way, and for the same two reasons: an action this
    module has not heard of is a new one, and a newly added write must not
    default to allowed.
    """
    if tool_call_name(tool_call) != ROUTINE_TOOL:
        return False
    return _is_mutating_action(
        tool_call, MUTATING_ROUTINE_ACTIONS, READ_ONLY_ROUTINE_ACTIONS
    )


def dry_run_refusal(tool_call: dict[str, Any]) -> str | None:
    """Why a dry run must not auto-approve this call, or ``None`` to let it by.

    Dry-run's promise is that nothing mutates, and the *gate* refuses every
    named write there already. This is the rest of that promise: the tools the
    gate deliberately does not name, because naming them would put a
    confirmation in front of ordinary tick work, yet which can each mutate
    anything at all once they run.

    Kept as one function returning one reason rather than a branch per tool in
    the caller, because it is a policy and not a special case: SEC-616 refused
    ``run_code`` here and SEC-626 found the identical hole one door over in
    ``manage_routines``. A third such tool is a line in this function, and the
    unattended gate does not have to learn about it.

    The refusal is per *action*, not per tool, so what a dry run needs in order
    to rehearse at all — listing routines, reading their source and their config
    schema, reading back a past run or a past snippet, reading candles — stays
    free. Widening that read-only half is how a dry run gets a new capability
    without getting the ability to write, and ``get_market_data`` below is that
    widening rather than an exception to it (CORR-625): it appears here not to
    be allowed — an unnamed tool is already allowed — but so that an action
    added to it later has to be classified before a rehearsal can call it.
    """
    if is_code_execution_call(tool_call):
        return (
            "this session runs in dry-run mode, where nothing mutates, and a "
            "snippet holds the unrestricted API client — read the market with "
            "get_market_data and the other read-only tools instead"
        )

    if is_mutating_routine_call(tool_call):
        return (
            "this session runs in dry-run mode, where nothing mutates, and a "
            "routine is Python holding the same unrestricted API client as a "
            "snippet — 'list', 'describe' and 'read_routine' still work"
        )

    if tool_call_name(tool_call) == MARKET_DATA_TOOL and _is_mutating_action(
        tool_call, MUTATING_MARKET_DATA_ACTIONS, READ_ONLY_MARKET_DATA_ACTIONS
    ):
        # Every action this module knows on this tool reads, so reaching here at
        # all means an action it does *not* know — a new one, or an unreadable
        # argument. Fails closed like its siblings: the cost is a rehearsal that
        # says so, against a write that a rehearsal promised could not happen.
        return (
            "this session runs in dry-run mode, and get_market_data was asked "
            "for something this build does not know is a read — use "
            "'candles', 'historical_candles' or 'connectors'"
        )

    return None


def is_recordable_tool_call(tool_call: dict[str, Any]) -> bool:
    """Should the action log keep a row for this call? (FEAT-102)

    :func:`is_mutating_tool_call` plus the writes that reach neither predicate
    because the *gate* deliberately ignores their tool. Defined as
    ``is_mutating_tool_call(...) or <explicit extras>`` on purpose: two
    functions answering nearly the same question will drift, and this shape
    makes the gate's set structurally a subset that cannot fall behind.

    Its extras are ``manage_controllers``, ``run_code`` and ``manage_routines``.
    The gate excludes all three tools entirely and should keep excluding them —
    widening the gate would put a new confirmation prompt in front of a running
    fleet, and in front of every tick's market read — but a bot's controllers
    are *written* by exactly these calls, so a log that drops them cannot say
    how a fleet was built or which of its config writes were rejected, and a
    snippet can change anything at all, so a log that drops it is silent about
    the one tool that can (SEC-616). A routine is the same Python behind a
    different door, so it is recorded on the same argument (SEC-626): "wrote a
    routine and ran it" is precisely the sequence a reader needs to see.

    Fails open the same way its siblings do: an action this module has not heard
    of is recorded rather than dropped.
    """
    if is_mutating_tool_call(tool_call):
        return True

    if tool_call_name(tool_call) == "manage_controllers":
        return _is_mutating_action(
            tool_call, MUTATING_CONTROLLER_ACTIONS, READ_ONLY_CONTROLLER_ACTIONS
        )

    return is_code_execution_call(tool_call) or is_mutating_routine_call(tool_call)


def format_tool_summary(tool_call: dict[str, Any]) -> str:
    """Format a tool call into a human-readable summary for the confirmation message."""
    # The bare name, or an ACP call (``mcp__mcp-hummingbot__manage_bots``) would
    # match none of the branches below and be approved as an opaque string.
    tool_name = tool_call_name(tool_call) or "Unknown"
    input_data = tool_call_input(tool_call)
    if input_data is None:
        # The gate sends unreadable calls here on purpose (SEC-093). Say so,
        # rather than rendering a row of "?" that looks like a parsed call.
        return f"{tool_name} (arguments could not be read)"

    if tool_name == "place_order":
        side = input_data.get("trade_type", "?")
        pair = input_data.get("trading_pair", "?")
        amount = input_data.get("amount", "?")
        order_type = input_data.get("order_type", "MARKET")
        price = input_data.get("price", "")
        connector = input_data.get("connector_name", "?")
        summary = f"{side} {amount} {pair} ({order_type})"
        if price:
            summary += f" @ {price}"
        summary += f" on {connector}"
        return summary

    if tool_name in CREATE_EXECUTOR_TOOLS:
        # The typed tools put the numbers the human is approving at the top level,
        # so the summary reads them straight instead of digging through a config
        # blob. Each type sizes itself differently, hence the per-type amount.
        pair = input_data.get("trading_pair", "?")
        kind = tool_name.removeprefix("create_").removesuffix("_executor")
        amount = _executor_amount(tool_name, input_data)
        return f"Create {kind} executor on {pair}{amount}"

    if tool_name == "stop_executor":
        exec_id = str(input_data.get("executor_id", "") or "")
        keep = input_data.get("keep_position", False)
        if not exec_id:
            return "Stop executor (id could not be read)"
        suffix = ", keeping the position" if keep else ""
        return f"Stop executor {exec_id[:12]}...{suffix}"

    if tool_name == LEVERAGE_TOOL:
        # Both halves of the call are named because either can be the one that
        # matters, and the line says who it lands on: this tool is scoped to an
        # account and a pair, not to one position.
        connector = input_data.get("connector_name", "?")
        pair = input_data.get("trading_pair") or ""
        leverage = input_data.get("leverage")
        mode = input_data.get("position_mode")
        changes = []
        if leverage is not None:
            changes.append(f"leverage to {leverage}x")
        if mode:
            changes.append(f"position mode to {mode}")
        what = " and ".join(changes) if changes else "nothing"
        target = f"{pair} on {connector}" if pair else connector
        return (
            f"Set {what} for {target} (every position on it, not just this session's)"
        )

    if tool_name == "manage_bots":
        action = input_data.get("action", "?")
        bot_name = input_data.get("bot_name", "?")
        if action == "deploy":
            controllers = input_data.get("controllers_config", [])
            return f"Deploy bot '{bot_name}' with controllers {controllers}"
        if action == "update_config":
            config_name = input_data.get("config_name", "?")
            return f"Update config '{config_name}' on bot '{bot_name}'"
        return f"Bot '{bot_name}': {action}"

    if tool_name == "control_agent":
        # The human is approving an unattended loop, so the line has to name the
        # strategy it will run and — when the caller overrode them — the two
        # numbers that decide how much it can lose. Without this the prompt says
        # "control_agent" and a config dict.
        action = input_data.get("action", "?")
        if action in DANGEROUS_CONTROL_ACTIONS:
            strategy = input_data.get("strategy_id") or "?"
            overrides = input_data.get("config")
            overrides = overrides if isinstance(overrides, dict) else {}
            summary = f"Start a live agent loop on '{strategy}'"
            mode = overrides.get("execution_mode")
            if mode:
                summary += f" in {mode} mode"
            amount = overrides.get("total_amount_quote")
            if amount is not None:
                summary += f", sized {amount} quote"
            return summary
        agent_id = str(input_data.get("agent_id") or "?")
        return f"Agent instance {agent_id}: {action}"

    if tool_name == "execute_swap":
        pair = input_data.get("trading_pair", "?")
        side = input_data.get("side", "?")
        amount = input_data.get("amount", "?")
        return f"Swap {side} {amount} {pair}"

    if tool_name == "manage_gateway_config":
        # The wallet import/remove summaries lived here until the tool stopped
        # accepting a private key at all (FEAT-065); wallets are read-only now.
        resource = input_data.get("resource_type", "?")
        action = input_data.get("action", "?")
        if resource in DANGEROUS_CONFIG_RESOURCES and action not in (
            READ_ONLY_CONFIG_ACTIONS
        ):
            # The gated half (SEC-566). "update networks" is not what the human is
            # approving — the target and the keys are, because one of those keys is
            # `nodeURL`, the RPC every later transaction is broadcast through.
            target = (
                input_data.get("network_id") or input_data.get("connector_name") or "?"
            )
            updates = input_data.get("config_updates")
            keys = (
                ", ".join(str(key) for key in updates)
                if isinstance(updates, dict) and updates
                else "?"
            )
            return f"Gateway config: {action} {resource} '{target}', setting {keys}"
        return f"Gateway config: {action} {resource}"

    if tool_name in ("manage_clmm", "manage_amm"):
        action = input_data.get("action", "?")
        kind = "CLMM" if tool_name == "manage_clmm" else "AMM"
        connector = input_data.get("connector", "?")
        pool = _short_address(
            input_data.get("pool_address") or input_data.get("position_address")
        )
        if action == "open":
            lower = input_data.get("lower_price", "?")
            upper = input_data.get("upper_price", "?")
            return (
                f"Open {kind} position on {connector} pool {pool} over {lower}-{upper}"
            )
        if action == "close":
            return f"Close {kind} position {pool} on {connector}"
        if action == "add_liquidity":
            base = input_data.get("base_token_amount", "?")
            quote = input_data.get("quote_token_amount", "?")
            return f"Add {base} base / {quote} quote to {kind} {pool} on {connector}"
        if action == "remove_liquidity":
            pct = input_data.get("percentage_to_remove", "?")
            return f"Remove {pct}% from {kind} position {pool} on {connector}"
        if action == "collect_fees":
            return f"Collect fees from {kind} position {pool} on {connector}"
        if action == "create_pool":
            base = input_data.get("base_token", "?")
            quote = input_data.get("quote_token", "?")
            return f"Create {kind} pool {base}-{quote} on {connector}"
        return f"{kind}: {action}"

    if tool_name == "manage_controllers":
        # Never gated, so this line is written for the log rather than for a
        # human deciding (FEAT-102). It still has to name what was written: a
        # tick that builds a fleet makes a dozen of these, and "manage_controllers"
        # twelve times over says nothing about which config failed.
        action = input_data.get("action", "?")
        target = input_data.get("target") or "controller"
        name = (
            input_data.get("config_name")
            or input_data.get("controller_name")
            or input_data.get("controller_type")
            or "?"
        )
        return f"Controller {target}: {action} '{name}'"

    if tool_name == CODE_RUN_TOOL:
        # Never gated either, so this line is written for the log (SEC-616). The
        # label is what the caller said the snippet was for and the first line is
        # what it actually starts doing — enough to tell a candle read from a
        # `client.gateway.start(...)` without carrying a whole script into the
        # log; the full source is in the code-run store (`condor/code_runs.py`).
        action = input_data.get("action") or "run"
        if action not in MUTATING_CODE_RUN_ACTIONS:
            return f"Code run: {action}"
        label = str(input_data.get("label") or "").strip()
        code = str(input_data.get("code") or "")
        head = next((line.strip() for line in code.splitlines() if line.strip()), "")
        if len(head) > MAX_SNIPPET_HEAD_CHARS:
            head = head[:MAX_SNIPPET_HEAD_CHARS] + "…"
        what = f"Run snippet '{label}'" if label else "Run snippet"
        return f"{what}: {head}" if head else f"{what} (no code)"

    if tool_name == ROUTINE_TOOL:
        # Never gated either, so this line too is written for the log (SEC-626).
        # It has to name the routine: a tick that writes one and runs it makes
        # two calls that "manage_routines" twice over describes as nothing, and
        # which library it landed in is half of what a routine *is* — an
        # agent-local script and a shared one under the same name are different
        # code. For `stop` and `get_instance` the name is an instance id, which
        # is the right thing to print for those anyway.
        action = input_data.get("action", "?")
        name = str(input_data.get("name") or "").strip() or "?"
        agent = input_data.get("agent")
        if input_data.get("shared"):
            where = " (shared)"
        elif isinstance(agent, str) and agent:
            where = f" ({agent})"
        else:
            where = ""
        return f"Routine {action} '{name}'{where}"

    # Generic fallback
    return tool_name
