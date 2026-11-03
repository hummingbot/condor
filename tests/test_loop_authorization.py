"""An approved live loop trades inside its envelope without asking again.

The failure this pins: a session started with an explicitly approved
``control_agent(action="start", execution_mode="loop")`` submitted a valid,
in-limit ``create_grid_executor`` and then held it, reporting that per-trade
approval was missing. Nothing had asked for one — the loop's gate auto-approves
in-envelope calls (``condor.agents.risk``). Two things made the seat read its own
authorization wrongly:

* the house rules shared with the chat said, unconditionally, to confirm before
  moving money, and the tick prompt said nothing to place that rule in an
  attended seat; and
* a call the gate DID refuse came back as a bare "cancelled" — the permission
  protocol carries no reason — which from inside the model is indistinguishable
  from a confirmation nobody answered.

So: the envelope is enforced and silent when it approves, refusals say why, and
the prompt tells an unattended seat which rule it is under.
"""

import asyncio
from types import SimpleNamespace

from condor.acp.client import ACPClient
from condor.agents.engine import TickEngine
from condor.agents.prompts import build_tick_prompt
from condor.agents.risk import (
    RefusalLog,
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)

AGENT_ID = "adaptive_grid_trader.sol_usdt_adaptive_grid_7"
OPTIONS = [{"optionId": "allow", "kind": "allow_once"}, {"optionId": "deny"}]


def _grid_call(amount: float = 90.0, controller_id: str = AGENT_ID) -> dict:
    """The call from the report: a 90 USDT grid tagged with its own session."""
    return {
        "tool": "create_grid_executor",
        "input": {
            "controller_id": controller_id,
            "connector_name": "binance_perpetual",
            "trading_pair": "SOL-USDT",
            "total_amount_quote": amount,
            "leverage": 5,
        },
    }


def _gate(limits: RiskLimits, state: RiskState, refusals: RefusalLog | None = None):
    return auto_approve_with_risk_check(
        RiskEngine(limits),
        state,
        execution_mode="loop",
        agent_id=AGENT_ID,
        refusals=refusals,
    )


# ── The envelope approves, silently ──


def test_an_in_envelope_create_needs_no_second_approval():
    refusals = RefusalLog()
    gate = _gate(RiskLimits(max_position_size_quote=500.0), RiskState(), refusals)

    result = asyncio.run(gate(_grid_call(), OPTIONS))

    assert result["outcome"]["outcome"] == "selected"
    assert refusals.drain() == []


def test_an_out_of_envelope_create_is_still_refused():
    refusals = RefusalLog()
    gate = _gate(RiskLimits(max_position_size_quote=50.0), RiskState(), refusals)

    result = asyncio.run(gate(_grid_call(), OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert "position limit" in result["reason"]


def test_a_foreign_controller_id_is_still_refused():
    refusals = RefusalLog()
    gate = _gate(RiskLimits(), RiskState(), refusals)

    result = asyncio.run(gate(_grid_call(controller_id="someone-else_3"), OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert "unattributable" in result["reason"]


# ── A refusal says why, everywhere it can ──


def test_a_refusal_is_recorded_with_its_reason():
    refusals = RefusalLog()
    gate = _gate(
        RiskLimits(max_open_executors=1), RiskState(executor_count=1), refusals
    )

    asyncio.run(gate(_grid_call(), OPTIONS))

    recorded = refusals.drain()
    assert len(recorded) == 1
    assert recorded[0]["tool"] == "create_grid_executor"
    assert "Max open executors" in recorded[0]["reason"]
    # Drained once, gone: the next tick must not re-report last tick's refusal.
    assert refusals.drain() == []


def test_place_order_refusal_names_the_executor_tools():
    refusals = RefusalLog()
    gate = _gate(RiskLimits(), RiskState(), refusals)

    result = asyncio.run(
        gate({"tool": "place_order", "input": {"trading_pair": "SOL-USDT"}}, OPTIONS)
    )

    assert result["outcome"]["outcome"] == "cancelled"
    assert "create_*_executor" in result["reason"]


def test_the_reason_never_reaches_the_acp_wire():
    """ACP's permission response has no field for it; a strict bridge would 400."""
    client = ACPClient.__new__(ACPClient)
    client.permission_callback = _gate(
        RiskLimits(max_position_size_quote=50.0), RiskState()
    )

    response = asyncio.run(
        client._on_request_permission(
            sessionId="s",
            options=OPTIONS,
            toolCall={
                "title": "create_grid_executor",
                "rawInput": _grid_call()["input"],
            },
        )
    )

    assert response == {"outcome": {"outcome": "cancelled"}}


def test_an_approval_still_selects_its_option_over_the_wire():
    client = ACPClient.__new__(ACPClient)
    client.permission_callback = _gate(RiskLimits(), RiskState())

    response = asyncio.run(
        client._on_request_permission(
            sessionId="s",
            options=OPTIONS,
            toolCall={
                "title": "create_grid_executor",
                "rawInput": _grid_call()["input"],
            },
        )
    )

    assert response == {"outcome": {"outcome": "selected", "optionId": "allow"}}


# ── What the next tick is told ──


def _tick_prompt(mode: str = "loop", refusals: list[dict] | None = None) -> str:
    agent = SimpleNamespace(instructions="", agent_key="claude-code", slug="agt")
    strategy = SimpleNamespace(
        instructions="Run the grid.",
        agent_key="claude-code",
        slug="grid",
        agent_slug="agt",
        dir=None,
    )
    return build_tick_prompt(
        agent=agent,
        strategy=strategy,
        config={"execution_mode": mode},
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={},
        cached_routines_section="",
        refusals=refusals,
    )


def test_a_live_tick_is_told_its_launch_was_the_approval():
    prompt = _tick_prompt()

    assert "[AUTHORIZATION — this seat is unattended and already approved]" in prompt
    assert "ATTENDED rule" in prompt


def test_a_dry_run_is_never_told_it_may_act():
    assert "[AUTHORIZATION" not in _tick_prompt(mode="dry_run")


def test_last_ticks_refusals_reach_the_next_prompt():
    prompt = _tick_prompt(
        refusals=[
            {"tool": "create_grid_executor", "reason": "Max open executors (5) reached"}
        ]
    )

    assert "[REFUSED LAST TICK" in prompt
    assert "Max open executors (5) reached" in prompt


def test_a_tick_with_nothing_refused_says_nothing():
    assert "[REFUSED LAST TICK" not in _tick_prompt()


# ── The engine carries them from the gate to the journal ──


class _RecordingJournal:
    def __init__(self) -> None:
        self.actions: list[tuple[int, str, str]] = []

    def append_action(self, tick: int, action: str, reasoning: str) -> None:
        self.actions.append((tick, action, reasoning))


def test_the_engine_journals_what_the_gate_refused():
    engine = SimpleNamespace(
        journal=_RecordingJournal(),
        _last_refusals=[
            {"tool": "create_grid_executor", "reason": "Max open executors (5) reached"}
        ],
    )

    TickEngine._journal_refusals(engine, tick_num=7)

    assert engine.journal.actions == [
        (
            7,
            "risk_blocked",
            "create_grid_executor refused — Max open executors (5) reached",
        )
    ]


def test_an_experiment_without_a_journal_still_keeps_its_refusals():
    engine = SimpleNamespace(
        journal=None, _last_refusals=[{"tool": "x", "reason": "y"}]
    )

    TickEngine._journal_refusals(engine, tick_num=1)

    assert engine._last_refusals == [{"tool": "x", "reason": "y"}]


def test_an_ownership_refusal_is_not_written_down_twice():
    """The ledger already records it and the prompt already reports it."""
    from condor.agents.ownership import BotLedger

    refusals = RefusalLog()
    ledger = BotLedger(namespace="agt-grid", enforced=True)
    gate = auto_approve_with_risk_check(
        RiskEngine(RiskLimits()),
        RiskState(),
        execution_mode="loop",
        ledger=ledger,
        agent_id=AGENT_ID,
        refusals=refusals,
    )

    result = asyncio.run(
        gate(
            {
                "tool": "manage_bots",
                "input": {"action": "deploy", "bot_name": "someone-elses-bot"},
            },
            OPTIONS,
        )
    )

    assert result["outcome"]["outcome"] == "cancelled"
    assert "outside this session's namespace" in result["reason"]
    assert ledger.drain_violations()  # the ledger keeps it
    assert refusals.drain() == []  # and only the ledger
