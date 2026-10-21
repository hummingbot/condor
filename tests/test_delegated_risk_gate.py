"""A delegated run trades behind the risk gate, like every other live seat (MM-1).

``run_agent_to_completion`` built its client with ``permission_callback=None``.
The comment on that line read "unattended: nobody is watching, so there is no one
to ask" — which is true, and was the wrong conclusion. Nothing was asked: every
deploy, create and swap a delegation made reached the venue with no risk check,
no refusal record and no bound of any kind, while the identical call from a tick
was gated. QA deployed a live ``pmm_mister`` through this door carrying neither
``max_global_drawdown_quote`` nor ``max_controller_drawdown_quote``, and the
platform had no kill switch on it at all.

Nobody watching is the reason to gate the seat, not the reason not to.

These pin the gate's behaviour through the envelope a delegation actually gets,
including the things it deliberately still allows — a gate that refused a status
read or a candle fetch would be swapped out the first time it annoyed someone.
"""

import asyncio

import pytest

from condor.agents.agent_run import _delegated_envelope, _refusal_note
from condor.agents.risk import RefusalLog, RiskLimits, auto_approve_with_risk_check

#: What a real bridge offers. The gate's approve tail needs one: with no option
#: it refuses, so a test passing ``[]`` reads every call as refused and proves
#: nothing.
OPTIONS = [
    {"optionId": "allow", "kind": "allow_once"},
    {"optionId": "deny", "kind": "reject_once"},
]


def _gate(refusals: RefusalLog | None = None):
    """The callback a delegation runs behind, built as ``agent_run`` builds it."""
    engine, state = _delegated_envelope()
    return auto_approve_with_risk_check(
        engine,
        state,
        execution_mode="loop",
        agent_id="",
        ledger=None,
        price_client=None,
        refusals=refusals,
    )


def _call(gate, tool: str, **args) -> dict:
    return asyncio.run(gate({"tool": tool, "input": args}, OPTIONS))


def _outcome(result: dict) -> str:
    return result["outcome"]["outcome"]


# ── MM-1: the deploy that started this ───────────────────────────────────────


def test_a_delegated_deploy_with_no_loss_cap_is_refused():
    """The regression, in the shape QA actually deployed."""
    result = _call(_gate(), "manage_bots", action="deploy", bot_name="jto-mm")

    assert _outcome(result) == "cancelled"
    assert "max_global_drawdown_quote" in result["reason"]


def test_a_loss_cap_above_the_envelope_is_refused():
    """A kill switch set above the capital it guards never fires."""
    result = _call(
        _gate(),
        "manage_bots",
        action="deploy",
        bot_name="jto-mm",
        max_global_drawdown_quote=600,
    )

    assert _outcome(result) == "cancelled"
    assert "exceeds position limit" in result["reason"]


def test_a_deploy_inside_the_envelope_goes_through():
    """The gate bounds the deploy; it does not forbid delegated deploying."""
    result = _call(
        _gate(),
        "manage_bots",
        action="deploy",
        bot_name="jto-mm",
        max_global_drawdown_quote=100,
    )

    assert _outcome(result) == "selected"


# ── what a delegation must still be able to do ───────────────────────────────


@pytest.mark.parametrize(
    "tool,args",
    [
        ("manage_bots", {"action": "status"}),
        ("manage_bots", {"action": "logs", "bot_name": "jto-mm"}),
        ("get_market_data", {"action": "candles", "trading_pair": "SOL-USDC"}),
        ("get_portfolio_overview", {}),
        ("list_executors", {}),
    ],
)
def test_reads_are_untouched(tool, args):
    """A gate that stood in front of a status read would be removed by whoever
    it annoyed first, taking the deploy check with it."""
    assert _outcome(_call(_gate(), tool, **args)) == "selected"


def test_a_create_inside_the_envelope_goes_through():
    result = _call(
        _gate(), "create_grid_executor", total_amount_quote=200, controller_id="d1"
    )
    assert _outcome(result) == "selected"


# ── the rest of the envelope ─────────────────────────────────────────────────


def test_a_create_beyond_the_envelope_is_refused():
    result = _call(
        _gate(), "create_grid_executor", total_amount_quote=900, controller_id="d1"
    )

    assert _outcome(result) == "cancelled"
    assert "position limit" in result["reason"]


def test_a_runs_own_creates_accumulate_against_the_envelope():
    """The state is not frozen: two creates that each fit may not both fit.

    What it cannot see is exposure opened before this run — a delegation has no
    tracker, so the envelope bounds this run's appetite, not the agent's total
    position. That limit is documented on ``_delegated_envelope``; this pins the
    half that does work.
    """
    gate = _gate()

    assert (
        _outcome(
            _call(
                gate, "create_grid_executor", total_amount_quote=300, controller_id="d1"
            )
        )
        == "selected"
    )
    second = _call(
        gate, "create_grid_executor", total_amount_quote=300, controller_id="d1"
    )

    assert _outcome(second) == "cancelled"
    assert "position limit" in second["reason"]


def test_an_untagged_create_is_refused():
    """Without a controller_id the position it opens is unattributable."""
    result = _call(_gate(), "create_grid_executor", total_amount_quote=100)

    assert _outcome(result) == "cancelled"
    assert "controller_id" in result["reason"]


def test_direct_order_placement_is_refused():
    result = _call(_gate(), "place_order", amount=1)

    assert _outcome(result) == "cancelled"
    assert "create_*_executor" in result["reason"]


def test_the_envelope_is_the_unconfigured_default():
    """$500 and five executors — the same an unconfigured loop gets.

    Pinned because it is a *choice*: a delegation has no session and no strategy
    to read limits from, and inventing a looser envelope for the one seat nobody
    watches would defeat the fix.
    """
    engine, state = _delegated_envelope()

    assert engine.limits == RiskLimits()
    assert engine.limits.max_position_size_quote == 500.0
    assert engine.limits.max_open_executors == 5
    assert state.total_exposure == 0.0
    # Nothing is blocked before a single call: a bare state must not read as a
    # breached one, or the first thing a delegation does is refuse everything.
    assert not state.is_blocked
    assert state.book_trusted
    assert state.drawdown_known


# ── the caller is told ───────────────────────────────────────────────────────


def test_a_refusal_reaches_the_caller():
    """The ACP bridge carries the outcome and never the reason, and a
    delegation has no journal or next prompt to carry it instead — so the one
    return value has to."""
    refusals = RefusalLog()
    gate = _gate(refusals)
    _call(gate, "manage_bots", action="deploy", bot_name="jto-mm")

    note = _refusal_note(refusals)

    assert "risk gate refused 1 call" in note
    assert "manage_bots" in note
    assert "max_global_drawdown_quote" in note


def test_a_clean_run_appends_nothing():
    """No footer on an answer that had nothing refused."""
    refusals = RefusalLog()
    _call(_gate(refusals), "manage_bots", action="status")

    assert _refusal_note(refusals) == ""


def test_several_refusals_are_all_reported():
    refusals = RefusalLog()
    gate = _gate(refusals)
    _call(gate, "manage_bots", action="deploy", bot_name="a")
    _call(gate, "place_order", amount=1)

    note = _refusal_note(refusals)

    assert "refused 2 calls" in note
    assert "manage_bots" in note and "place_order" in note


# ── the edge itself ──────────────────────────────────────────────────────────


def test_the_delegated_run_builds_a_permission_callback():
    """Without this edge every assertion above holds and every delegation still
    runs ungated, which is exactly the shape of the original defect."""
    import inspect

    from condor.agents import agent_run

    source = inspect.getsource(agent_run.run_agent_to_completion)

    assert "permission_callback=permission_cb" in source
    assert "permission_callback=None" not in source


# ── one server for the tools and the gate (review follow-up) ────────────────


def test_the_gate_prices_against_the_server_the_tools_resolved():
    """A gate checking the wrong book can refuse a valid call, or approve one
    that is over the limit on the book that matters.

    The two used to resolve separately on different inputs — the toolset builder
    with ``chat_id``, the price client without — so a run whose owner had no
    explicit server could price a create against the first accessible server
    while the create landed on the chat's default.
    """
    import inspect

    from condor.agents import agent_run

    source = inspect.getsource(agent_run.run_agent_to_completion)

    assert "resolved = resolve_stored_server(" in source
    assert "server_name=resolved," in source
    assert "_price_client(resolved, slug)" in source


def test_the_price_client_resolves_nothing_of_its_own():
    """It takes the answer; two resolutions of one question are two chances to
    disagree."""
    import inspect

    from condor.agents import agent_run

    source = inspect.getsource(agent_run._price_client)

    assert "resolve_stored_server" not in source


def test_a_serverless_run_gets_no_price_client():
    import asyncio as _asyncio

    from condor.agents.agent_run import _price_client

    assert _asyncio.run(_price_client("", "lp")) is None


# ── refusals survive a failed run (review follow-up) ────────────────────────


def test_a_failed_run_still_reports_what_was_refused():
    """A session that dies afterwards does not make the refusals less true, and
    the raise becomes the caller's error record — the only account it gets."""
    import inspect

    from condor.agents import agent_run

    source = inspect.getsource(agent_run.run_agent_to_completion)
    failure_arm = source[source.index("if stop_reason in FAILED_STOP_REASONS:") :]

    assert "_refusal_note(refusals)" in failure_arm[:600]
