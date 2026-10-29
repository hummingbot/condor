"""An emergency shutdown stops the bots the session deployed (MM-3).

The winddown stopped executors, swept resting orders and verified positions, and
never stopped a *bot*. For a controller-mode session that leaves the thing
actually trading untouched — its controllers run inside the bot's own Hummingbot
instance, which none of the other passes can reach. QA watched a session report
itself stopped while its bot grew a position from 0.05 to 0.10 SOL.

The second half of the same finding was that nobody ever saw the result.
``control_agent(action="shutdown")`` reaches the route over the condor MCP
bridge, which gave it ``TIMEOUTS.mcp_call`` — fifteen seconds — while the route
awaits the whole winddown, LLM cleanup turn included, before answering. So the
call always expired and the tool reported ``Failed to reach main process API``.
The operator read that as "the kill switch did not fire"; what had happened was
that nobody waited for it.
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from condor.agents.ownership import BotLedger, bot_namespace
from condor.agents.shutdown import (
    ShutdownPolicy,
    _stop_owned_bots,
    cancel_resting_orders,
)
from condor.runtime.timeouts import TIMEOUTS

NS = bot_namespace("mm", "pmm_op")
#: What Hummingbot actually deploys under: the requested name plus a timestamp.
INSTANCE = f"{NS}-jto-20261022-101500"


class _Orchestration:
    def __init__(self, names, fail=(), list_raises=False):
        self.names = list(names)
        self.fail = set(fail)
        self.list_raises = list_raises
        self.stopped: list[tuple[str, bool]] = []

    async def get_active_bots_status(self):
        if self.list_raises:
            raise RuntimeError("the API is unreachable")
        return {"data": {name: {} for name in self.names}}

    async def stop_and_archive_bot(self, bot_name, skip_order_cancellation=True):
        if bot_name in self.fail:
            raise RuntimeError("the bot would not stop")
        self.stopped.append((bot_name, skip_order_cancellation))
        return {"status": "ok"}


class _Client:
    def __init__(self, names, fail=(), list_raises=False):
        self.bot_orchestration = _Orchestration(names, fail, list_raises)


def _engine(*bots: str) -> SimpleNamespace:
    """A stand-in engine carrying only what the bot pass binds."""
    ledger = BotLedger(NS)
    for bot in bots:
        ledger.note_deploy(bot)
    return SimpleNamespace(ledger=ledger, agent_id="mm.pmm_op_1")


# ── the bots are stopped ─────────────────────────────────────────────────────


def test_a_running_instance_of_an_owned_bot_is_stopped():
    """The regression. The ledger holds a base; the venue runs an instance."""
    client = _Client([INSTANCE])

    stopped, failures = asyncio.run(_stop_owned_bots(_engine(f"{NS}-jto"), client))

    assert stopped == [INSTANCE]
    assert failures == []


def test_the_stop_cancels_the_bots_own_orders():
    """``skip_order_cancellation=False`` — the opposite of the API default.

    A controller session's orders live inside its bot and the ``/trading``
    routes cannot reach them, so this stop is the only thing that cancels them.
    An ordinary ``manage_bots(stop_bot)`` would leave them resting.
    """
    client = _Client([INSTANCE])

    asyncio.run(_stop_owned_bots(_engine(f"{NS}-jto"), client))

    assert client.bot_orchestration.stopped == [(INSTANCE, False)]


def test_another_sessions_bot_is_left_alone():
    """Scoped by the ledger's own membership test, not by a name pattern: a
    session may stop only what it was allowed to start."""
    client = _Client([INSTANCE, "someone-elses-bot", "mm-other_loop-sol-20261022"])

    stopped, _ = asyncio.run(_stop_owned_bots(_engine(f"{NS}-jto"), client))

    assert stopped == [INSTANCE]


def test_a_session_that_deployed_nothing_asks_nothing():
    """Executor-mode sessions are the common case and must pay for no lookup."""
    client = _Client([INSTANCE])

    assert asyncio.run(_stop_owned_bots(_engine(), client)) == ([], [])
    assert client.bot_orchestration.stopped == []


def test_an_engine_with_no_ledger_is_tolerated():
    """Stand-in engines carry only what they bind, the convention the rest of
    this module already follows."""
    assert asyncio.run(_stop_owned_bots(SimpleNamespace(), _Client([]))) == ([], [])


# ── failures are reported, never raised ──────────────────────────────────────


def test_a_bot_that_will_not_stop_is_recorded_not_raised():
    """A stop has to finish even when part of it fails; the operator is told."""
    client = _Client([INSTANCE], fail={INSTANCE})

    stopped, failures = asyncio.run(_stop_owned_bots(_engine(f"{NS}-jto"), client))

    assert stopped == []
    assert len(failures) == 1
    assert INSTANCE in failures[0]


def test_an_unlistable_fleet_falls_back_to_the_ledgers_names():
    """A base is not an instance, so the fallback may miss — and says so.

    It still stops a bot deployed under its exact name, which is better than
    abandoning the pass, and it records that it could not list the fleet so the
    operator does not read a clean result as a complete one.
    """
    client = _Client([], list_raises=True)
    client.bot_orchestration.names = [f"{NS}-jto"]

    stopped, failures = asyncio.run(_stop_owned_bots(_engine(f"{NS}-jto"), client))

    assert stopped == [f"{NS}-jto"]
    assert any("could not list running bots" in f for f in failures)


# ── the order of the passes ──────────────────────────────────────────────────


def test_the_bots_are_stopped_before_anything_else_acts():
    """The MM-3 property, not just the MM-3 call.

    A bot that is still up keeps quoting while the other passes work, which is
    how a position grew *during* its own shutdown. Pinned on the source order
    because the alternative — standing a whole winddown up and watching two
    fake APIs interleave — would test the fakes.
    """
    from condor.agents import shutdown as shutdown_mod

    source = inspect.getsource(shutdown_mod.run_shutdown)
    bots_at = source.index("_stop_owned_bots(")
    baseline_at = source.index("_deterministic_baseline(")
    cancel_at = source.index("cancel_resting_orders(")

    assert bots_at < baseline_at < cancel_at

    # ...and the executor snapshot is taken first of all, since archiving a bot
    # takes its controllers' rows with it.
    assert source.index("_get_running_executors(") < bots_at


# ── what the order sweep then reports ────────────────────────────────────────


def _cancel(engine, stopped_bots):
    return asyncio.run(
        cancel_resting_orders(
            engine,
            _Client([]),
            ShutdownPolicy(cancel_open_orders=True),
            [],
            stopped_bots=stopped_bots,
        )
    )


def test_a_stopped_bot_is_not_reported_as_unswept():
    """Its own instance cancelled its orders on the way down, so naming it would
    describe orders that are not resting."""
    outcome = _cancel(_engine(f"{NS}-jto"), {INSTANCE})

    assert outcome.unsupported == []


def test_a_bot_that_could_not_be_stopped_is_still_reported():
    """The whole point of the field is to say which orders nobody swept."""
    outcome = _cancel(_engine(f"{NS}-jto"), set())

    assert outcome.unsupported == [f"bot {NS}-jto (own instance)"]


def test_only_the_bots_that_stopped_are_excused():
    engine = _engine(f"{NS}-jto", f"{NS}-sol")

    outcome = _cancel(engine, {INSTANCE})

    assert outcome.unsupported == [f"bot {NS}-sol (own instance)"]


# ── the budget the call is given ─────────────────────────────────────────────


def test_the_shutdown_budget_is_far_above_a_plain_tool_call():
    """Fifteen seconds could never cover a winddown that waits on an LLM turn."""
    assert TIMEOUTS.mcp_shutdown > TIMEOUTS.mcp_call * 10


def test_the_budget_is_derived_from_the_turn_it_waits_on():
    """Derived, not written down: the cleanup turn runs under ``tick_default``,
    and raising that without raising this would reproduce the same lie."""
    assert TIMEOUTS.mcp_shutdown > TIMEOUTS.tick_default


def test_raising_the_turn_budget_raises_the_call_budget():
    """The derivation, not just today's numbers."""
    from condor.runtime.timeouts import TimeoutPolicy

    raised = TimeoutPolicy(tick_default=TIMEOUTS.tick_default * 2)

    assert raised.mcp_shutdown > TIMEOUTS.mcp_shutdown


def test_the_shutdown_tool_asks_for_that_budget():
    """The edge: without it the route is waited on with the 15-second default."""
    from mcp_servers.condor.tools import trading_agent

    source = inspect.getsource(trading_agent)

    assert 'TIMEOUTS.mcp_shutdown if verb == "shutdown"' in source


def test_only_the_shutdown_verb_gets_the_longer_budget():
    """A stop, pause or resume returns at once and keeps the ordinary budget."""
    from mcp_servers.condor.tools import trading_agent

    source = inspect.getsource(trading_agent)

    assert "else None" in source.split("TIMEOUTS.mcp_shutdown")[1][:40]


# ── scoped to what the session recorded (review follow-up) ──────────────────


def test_a_recorded_bot_outside_the_namespace_is_still_stopped():
    """Recording a deploy is unconditional; namespace enforcement is not.

    An executor-mode session can therefore record a bot whose name sits outside
    the prefix. Scoping the winddown by ``ledger.owns()`` — a prefix test —
    walked straight past it and left it trading.
    """
    legacy = "jto-mm-20261022-101500"
    client = _Client([legacy])

    stopped, _ = asyncio.run(_stop_owned_bots(_engine("jto-mm"), client))

    assert stopped == [legacy]


def test_a_namespace_sibling_this_session_never_deployed_is_left_alone():
    """The converse, and the worse half: the prefix also matches another live
    session's bots, so a winddown scoped that way could stop someone else's —
    the harm CORR-726's dead-owner conjunction exists to prevent, one object
    along."""
    client = _Client([INSTANCE, f"{NS}-never-deployed-20261022-101500"])

    stopped, _ = asyncio.run(_stop_owned_bots(_engine(f"{NS}-jto"), client))

    assert stopped == [INSTANCE]


def test_scope_comes_from_the_recorded_bases_not_the_prefix():
    """Pinned so the cheaper prefix test cannot creep back in."""
    import inspect

    from condor.agents import shutdown as shutdown_mod

    source = inspect.getsource(shutdown_mod._stop_owned_bots)
    # Past the docstring, which names ``ledger.owns`` precisely to say it is the
    # wrong test — the prose is not the code.
    body = source.split('"""')[2]

    assert "in_namespace(str(n), base) for base in bases" in body
    assert "ledger.owns(" not in body
