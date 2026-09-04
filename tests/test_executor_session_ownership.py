"""A loop may stop its own executors, and only its own (SEC-559).

Creates have been bound to the session that makes them for a while: the gate
refuses a ``create_*_executor`` whose ``controller_id`` is not this session's,
because the position it opens would be unattributable. Stops were bound to
nothing — ``stop_executor`` is dangerous by name and ``keep_position=False`` (the
default) closes the position at market, yet it matched no branch of the loop
callback and fell straight through to the auto-approve tail.

The id was not hard to come by either: ``list_executors`` is in the tick profile
and lists the whole fleet when no filter is passed. So an unattended tick could
enumerate every executor on the server and close one belonging to another
session, another strategy or a human — and the loop that owned it would see only
a position that had vanished.

What is pinned here: a foreign executor is refused with the foreign controller
named, this session's own executor is still stopped freely and without a round
trip, an executor created earlier in the same tick (so absent from the pre-tick
snapshot) is resolved through the API and allowed, an id nothing can attribute is
refused, and an attended seat is untouched.
"""

import asyncio

from condor.agents.risk import (
    RefusalLog,
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)

AGENT_ID = "adaptive_grid_trader.sol_usdt_adaptive_grid_7"
OPTIONS = [{"optionId": "allow", "kind": "allow_once"}, {"optionId": "deny"}]

#: The pre-tick snapshot: one executor of ours under the session's own tag, one
#: under a bot controller we operate (controller-mode rows never carry agent_id).
SNAPSHOT = {"ours-1": AGENT_ID, "ours-bot-1": "sol_usdt_grid_controller"}


class _Client:
    """Stands in for the Hummingbot API client, counting its lookups."""

    def __init__(self, detail=None, raises=False):
        self._detail = detail
        self._raises = raises
        self.calls: list[str] = []
        self.executors = self

    async def get_executor(self, executor_id: str, **kwargs):
        self.calls.append(executor_id)
        if self._raises:
            raise RuntimeError("API unreachable")
        return self._detail


def _stop_call(executor_id: str, keep_position: bool = False) -> dict:
    return {
        "tool": "stop_executor",
        "input": {"executor_id": executor_id, "keep_position": keep_position},
    }


def _gate(
    refusals: RefusalLog | None = None,
    client=None,
    agent_id: str = AGENT_ID,
    owners=SNAPSHOT,
):
    return auto_approve_with_risk_check(
        RiskEngine(RiskLimits()),
        RiskState(),
        execution_mode="loop",
        agent_id=agent_id,
        price_client=client,
        refusals=refusals,
        executor_owners=owners,
    )


# ── Somebody else's executor ──


def test_a_foreign_executor_is_refused_and_the_reason_names_its_controller():
    refusals = RefusalLog()
    client = _Client({"id": "theirs-9", "config": {"controller_id": "other_agent.3"}})
    gate = _gate(refusals, client)

    result = asyncio.run(gate(_stop_call("theirs-9"), OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert "other_agent.3" in result["reason"]
    recorded = refusals.drain()
    assert len(recorded) == 1
    assert recorded[0]["tool"] == "stop_executor"
    assert "other_agent.3" in recorded[0]["reason"]


def test_a_foreign_executor_is_refused_even_when_the_position_is_kept():
    """``keep_position=True`` still hands another session's position away."""
    client = _Client({"id": "theirs-9", "config": {"controller_id": "other_agent.3"}})
    gate = _gate(client=client)

    result = asyncio.run(gate(_stop_call("theirs-9", keep_position=True), OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"


# ── Our own executor ──


def test_our_own_executor_is_stopped_without_a_round_trip():
    refusals = RefusalLog()
    client = _Client()
    gate = _gate(refusals, client)

    result = asyncio.run(gate(_stop_call("ours-1"), OPTIONS))

    assert result["outcome"]["outcome"] == "selected"
    assert refusals.drain() == []
    # The snapshot answered: no new API call on the common path.
    assert client.calls == []


def test_a_bot_controllers_row_is_ours_though_its_tag_is_not_the_agent_id():
    """Controller mode tags executors with the bot's controller, not agent_id."""
    client = _Client()
    gate = _gate(client=client)

    result = asyncio.run(gate(_stop_call("ours-bot-1"), OPTIONS))

    assert result["outcome"]["outcome"] == "selected"
    assert client.calls == []


def test_an_executor_created_earlier_in_the_same_tick_is_still_ours():
    """It postdates the snapshot, so only the API can place it — and it does."""
    client = _Client({"id": "fresh-2", "config": {"controller_id": AGENT_ID}})
    gate = _gate(client=client)

    result = asyncio.run(gate(_stop_call("fresh-2"), OPTIONS))

    assert result["outcome"]["outcome"] == "selected"
    assert client.calls == ["fresh-2"]


def test_a_top_level_controller_id_attributes_the_executor_too():
    """Not every executor shape nests its tag under ``config``."""
    client = _Client({"id": "fresh-3", "controller_id": AGENT_ID})
    gate = _gate(client=client)

    result = asyncio.run(gate(_stop_call("fresh-3"), OPTIONS))

    assert result["outcome"]["outcome"] == "selected"


# ── An id nothing can place ──


def test_an_unknown_id_is_refused_as_unattributable():
    refusals = RefusalLog()
    client = _Client(None)  # a 404 shape: the API knows no such executor
    gate = _gate(refusals, client)

    result = asyncio.run(gate(_stop_call("ghost-7"), OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert "could not be attributed" in result["reason"]
    assert refusals.drain()[0]["reason"] == result["reason"]


def test_an_unreachable_api_refuses_rather_than_approves():
    """Fail closed: a lookup that errors is not evidence the executor is ours."""
    gate = _gate(client=_Client(raises=True))

    result = asyncio.run(gate(_stop_call("ghost-7"), OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert "could not be attributed" in result["reason"]


def test_an_executor_with_no_controller_tag_is_unattributable():
    gate = _gate(client=_Client({"id": "untagged-4", "config": {}}))

    result = asyncio.run(gate(_stop_call("untagged-4"), OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert "could not be attributed" in result["reason"]


def test_a_stop_with_no_executor_id_is_refused():
    gate = _gate(client=_Client({"config": {"controller_id": AGENT_ID}}))

    result = asyncio.run(gate({"tool": "stop_executor", "input": {}}, OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert "executor_id" in result["reason"]


# ── Attended seats are untouched ──


def test_an_attended_seat_stops_any_executor_as_before():
    """Empty agent_id — chat, consults, tests — where a human confirms the stop."""
    client = _Client({"id": "theirs-9", "config": {"controller_id": "other_agent.3"}})
    gate = _gate(client=client, agent_id="", owners=None)

    result = asyncio.run(gate(_stop_call("theirs-9"), OPTIONS))

    assert result["outcome"]["outcome"] == "selected"
    assert client.calls == []


def test_a_session_with_no_snapshot_still_resolves_through_the_api():
    """A first tick, or a provider that failed: the snapshot is empty, not absent."""
    client = _Client({"id": "fresh-2", "config": {"controller_id": AGENT_ID}})
    gate = _gate(client=client, owners={})

    result = asyncio.run(gate(_stop_call("fresh-2"), OPTIONS))

    assert result["outcome"]["outcome"] == "selected"
    assert client.calls == ["fresh-2"]


# ── The snapshot the engine hands the gate ──


def test_the_engine_builds_the_map_from_the_tick_snapshot():
    from condor.agents.engine import TickEngine

    engine = TickEngine.__new__(TickEngine)
    engine._last_skill_data = {
        "all_executors": [
            {"id": "a1", "controller_id": AGENT_ID},
            {"id": "a2", "controller_id": "sol_usdt_grid_controller"},
            {"id": "", "controller_id": "dropped"},  # no id, nothing to key on
            "not-a-row",
        ],
        "executors": [{"id": "running-only", "controller_id": AGENT_ID}],
    }

    assert engine._executor_owners() == {
        "a1": AGENT_ID,
        "a2": "sol_usdt_grid_controller",
    }


def test_the_engine_falls_back_to_the_running_executors():
    from condor.agents.engine import TickEngine

    engine = TickEngine.__new__(TickEngine)
    engine._last_skill_data = {"executors": [{"id": "r1", "controller_id": AGENT_ID}]}

    assert engine._executor_owners() == {"r1": AGENT_ID}


def test_the_engine_survives_a_provider_that_returned_no_executors():
    from condor.agents.engine import TickEngine

    engine = TickEngine.__new__(TickEngine)
    engine._last_skill_data = {"error": "fetch failed"}

    assert engine._executor_owners() == {}
