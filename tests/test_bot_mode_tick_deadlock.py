"""CORR-750: a bot-mode loop must not be held forever by its own first tick.

Reported against #289 from a live run: a strategy switched to ``bot_mode: bot``
deployed its bot on the first cycle and then skipped every cycle after it with
"no drawdown reading on record", while the bot carried on trading with nobody
managing it.

Two separate faults made that one symptom, and both are covered here:

1. A bot-mode performance read starts at ``missing`` and is raised by each bot
   that answers (``condor/agents/performance.py``), so before the first deploy
   there is nothing to raise it. The drawdown guard treats a record with no
   reading in it as a blind spot and blocks -- blocking the one tick that would
   have produced a reading. A session that has deployed nothing has nothing for
   the missing reading to be hiding, and says so with ``nothing_at_stake``.

2. The providers read the book at the top of a tick, before the risk gate runs,
   but only the arm that reached the model wrote the snapshot. A blocked tick
   therefore threw away the reading it had already fetched -- so a block caused
   by a missing reading closed the only path that records one.

The guard itself is not widened: a ledger holding a base ends the exemption, and
a tick that genuinely could not read still writes a non-reading line and stays
blocked.
"""

import asyncio

from condor.agents.journal import JournalManager
from condor.agents.risk import RiskEngine, RiskLimits, RiskState


class _Tracker:
    """A tracker that reports the checked pair, like ``JournalManager`` does."""

    def __init__(self, pct: float, known: bool):
        self._pct = pct
        self._known = known

    def get_drawdown_pct_checked(self) -> tuple[float, bool]:
        return self._pct, self._known

    def get_drawdown_pct(self) -> float:
        return self._pct


def _engine(tmp_path, monkeypatch, **config):
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug="brigado", name="Grid")
    strategy.home.mkdir(parents=True, exist_ok=True)
    return TickEngine(
        agent=Agent(slug="brigado", name="Brigado"),
        strategy=strategy,
        config={"execution_mode": "loop", **config},
        chat_id=1,
        user_id=1,
    )


def _providers(**data):
    """``run_core_providers`` stubbed as the executors provider answering.

    The engine takes this tick's reading off THIS tick's provider result, not
    off the carried ``_last_skill_data`` -- so a test that wants a reading has
    to hand one back the way the provider does ([[CORR-750]]).
    """
    from condor.agents.providers.base import ProviderResult

    async def go(*args, **kwargs):
        return {
            "executors": ProviderResult(
                name="executors", data=dict(data), summary="Active Executors: stub"
            )
        }

    return go


def _async(result):
    async def go(*args, **kwargs):
        return result

    return go


# ----------------------------------------------------------------------
# 1. The exemption, and its limits
# ----------------------------------------------------------------------


def test_unknown_drawdown_blocks_by_default():
    state = RiskEngine(RiskLimits()).get_state(_Tracker(0.0, known=False))

    assert state.is_blocked is True
    assert "no drawdown reading on record" in state.block_reason


def test_nothing_at_stake_exempts_an_unknown_drawdown():
    state = RiskEngine(RiskLimits()).get_state(
        _Tracker(0.0, known=False), nothing_at_stake=True
    )

    assert state.is_blocked is False, "nothing deployed, so nothing has fallen"
    assert state.block_reason == ""


def test_nothing_at_stake_does_not_excuse_a_breach_that_was_read():
    # The exemption is about an *absent* reading only. A drawdown the tracker
    # could actually report is compared against the limits either way.
    state = RiskEngine(RiskLimits(max_drawdown_pct=10.0)).get_state(
        _Tracker(25.0, known=True), nothing_at_stake=True
    )

    assert state.is_blocked is True
    assert "exceeds limit" in state.block_reason


# ----------------------------------------------------------------------
# 2. A blocked tick records what it read
# ----------------------------------------------------------------------


def test_blocked_tick_records_the_reading_it_already_fetched(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    assert engine.journal is not None, "session mode keeps a journal"
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine, "_notify", _async(None))
    monkeypatch.setattr(
        engine.risk,
        "get_state",
        lambda tracker, **kw: RiskState(is_blocked=True, block_reason="held"),
    )
    # What the providers read at the top of this tick, before the gate ran.
    monkeypatch.setattr(
        engine.provider_registry,
        "run_core_providers",
        _providers(
            total_pnl=12.5,
            total_volume=400.0,
            executors=[{"id": "e1"}],
            total_exposure=250.0,
            reading="ok",
        ),
    )

    assert asyncio.run(engine._gather_tick_context(object())) is None

    pct, known = engine.journal.get_drawdown_pct_checked()
    assert known is True, "the blocked tick's reading is on record"
    assert pct == 0.0
    assert engine.journal.get_pnl_series()[-1]["pnl"] == 12.5


def test_blocked_tick_that_read_nothing_stays_unknown(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", _providers(reading="missing")
    )
    monkeypatch.setattr(engine, "_notify", _async(None))
    monkeypatch.setattr(
        engine.risk,
        "get_state",
        lambda tracker, **kw: RiskState(is_blocked=True, block_reason="held"),
    )

    assert asyncio.run(engine._gather_tick_context(object())) is None

    assert engine.journal.tick_count == 1
    assert engine.journal.get_drawdown_pct_checked() == (
        0.0,
        False,
    ), "a line that records no reading is still not a reading"


# ----------------------------------------------------------------------
# 3. The reported symptom, end to end
# ----------------------------------------------------------------------


def test_fresh_bot_mode_session_is_not_blocked_before_its_first_deploy(
    tmp_path, monkeypatch
):
    engine = _engine(tmp_path, monkeypatch, bot_mode="bot", bot_name="brigado-grid")
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", _providers(reading="missing")
    )
    assert engine.ledger is not None and engine.ledger.bases() == []
    # The first tick read nothing, because there was no bot to read.
    engine.journal.record_tick("surveyed the market")
    engine.journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")
    assert engine.journal.tick_count == 1

    ctx = asyncio.run(engine._gather_tick_context(object()))

    assert ctx is not None, "a session with no bot deployed may still take a tick"
    assert ctx.risk_state.is_blocked is False


def test_a_recorded_bot_ends_the_exemption(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, bot_mode="bot", bot_name="brigado-grid")
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", _providers(reading="missing")
    )
    monkeypatch.setattr(engine, "_notify", _async(None))
    assert engine.ledger is not None
    engine.ledger.note_deploy("brigado-grid")
    assert engine.ledger.bases() == ["brigado-grid"]
    # A bot is out there and the performance read cannot see it: a real blind
    # spot, and the block the guard exists for.
    engine.journal.record_tick("deployed")
    engine.journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")

    assert asyncio.run(engine._gather_tick_context(object())) is None
    assert "no drawdown reading on record" in engine.journal._path.read_text()


def test_the_block_clears_once_a_reading_lands(tmp_path):
    journal = JournalManager(agent_id="brigado", session_dir=tmp_path)
    journal.record_tick("deployed")
    journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")
    risk = RiskEngine(RiskLimits())
    assert risk.get_state(journal).is_blocked is True

    # The next tick's providers answer, and that tick writes its line whether or
    # not it was the blocked arm that wrote it.
    journal.record_snapshot(3.0, 90.0, 1, 150.0, reading="ok")

    assert risk.get_state(journal).is_blocked is False


def test_executor_mode_gets_no_exemption(tmp_path, monkeypatch):
    """The narrowing, and why it is not optional.

    In executor mode the book is executors, which the ledger does not track, so
    an empty ledger is that mode's ordinary steady state. Resting the exemption
    on the ledger alone would therefore switch the drawdown guard off for every
    executor-mode agent, permanently -- a failed core provider returns zeros
    that were never read, and that is the case [[CORR-725]] exists for.
    """
    engine = _engine(tmp_path, monkeypatch)  # no bot_name declared
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", _providers(reading="missing")
    )
    monkeypatch.setattr(engine, "_notify", _async(None))
    assert engine.ledger is not None
    assert engine.ledger.enforced is False, "no bot_name is executor mode"
    assert engine.ledger.bases() == []
    engine.journal.record_tick("provider failed")
    engine.journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")

    assert asyncio.run(engine._gather_tick_context(object())) is None
    assert "no drawdown reading on record" in engine.journal._path.read_text()


# ----------------------------------------------------------------------
# 4. A deploy that failed is a name, not a position (review follow-up)
# ----------------------------------------------------------------------


def test_a_failed_deploy_does_not_pin_the_loop(tmp_path, monkeypatch):
    """The first fix's own trap, found in review.

    The permission gate records a deploy on the way *in*, before the call runs,
    so a deploy that then fails leaves the name in the ledger with no bot behind
    it. Counting it as something at stake put the loop straight back where it
    started: the bot that was never created can never supply a reading, so every
    later tick stopped before the model ran -- and the retry that would have
    fixed it is something only the model can do.
    """
    engine = _engine(tmp_path, monkeypatch, bot_mode="bot", bot_name="brigado-grid")
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", _providers(reading="missing")
    )
    assert engine.ledger is not None
    engine.ledger.note_claim("brigado-grid")  # the gate, on the way in
    # ...and no note_deploy, because the call came back a failure.
    assert engine.ledger.bases() == ["brigado-grid"], "the session still owns the name"
    assert engine.ledger.confirmed_bases() == []
    engine.journal.record_tick("deploy failed")
    engine.journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")

    ctx = asyncio.run(engine._gather_tick_context(object()))

    assert ctx is not None, "the loop must reach the model to retry the deploy"
    assert ctx.risk_state.is_blocked is False


def test_the_deploy_that_landed_still_ends_the_exemption(tmp_path, monkeypatch):
    """The other side of it: a confirmed bot the read cannot see blocks."""
    engine = _engine(tmp_path, monkeypatch, bot_mode="bot", bot_name="brigado-grid")
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", _providers(reading="missing")
    )
    monkeypatch.setattr(engine, "_notify", _async(None))
    assert engine.ledger is not None
    engine.ledger.note_claim("brigado-grid")
    engine.ledger.note_deploy("brigado-grid")  # the engine, on the way out
    assert engine.ledger.confirmed_bases() == ["brigado-grid"]
    engine.journal.record_tick("deployed")
    engine.journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")

    assert asyncio.run(engine._gather_tick_context(object())) is None
    assert "no drawdown reading on record" in engine.journal._path.read_text()


def test_an_adopted_bot_counts_without_waiting_to_be_confirmed(tmp_path, monkeypatch):
    """Adoption is bounded by the live listing, so an adopted bot was seen
    running -- it is evidence, not a claim, and must not be exempted."""
    engine = _engine(tmp_path, monkeypatch, bot_mode="bot", bot_name="brigado-grid")
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", _providers(reading="missing")
    )
    monkeypatch.setattr(engine, "_notify", _async(None))
    assert engine.ledger is not None
    engine.ledger.adopt("brigado-grid-20261027-101500")
    assert engine.ledger.confirmed_bases() == ["brigado-grid"]
    engine.journal.record_tick("adopted")
    engine.journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")

    assert asyncio.run(engine._gather_tick_context(object())) is None


# ----------------------------------------------------------------------
# 5. The reading in hand clears the block on the same tick
# ----------------------------------------------------------------------


def test_this_ticks_reading_clears_the_block_without_costing_a_cycle(
    tmp_path, monkeypatch
):
    """Ralph's third cycle: the API *had* the bot's numbers and it still blocked.

    The journal is written after the gate runs, so a tick whose providers
    answered presents an empty history to the very check that reads it. Recording
    the blocked tick's snapshot makes the block clear on the cycle after -- this
    makes it clear on the cycle itself, which for a loop on a five-minute
    frequency is the difference between a bot nobody manages for five minutes
    and for fifteen.
    """
    engine = _engine(tmp_path, monkeypatch, bot_mode="bot", bot_name="brigado-grid")
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", _providers(reading="missing")
    )
    monkeypatch.setattr(engine, "_notify", _async(None))
    assert engine.ledger is not None
    engine.ledger.note_claim("brigado-grid")
    engine.ledger.note_deploy("brigado-grid")
    # Cycle 1 deployed and read nothing: the API had not recorded the bot yet.
    engine.journal.record_tick("deployed")
    engine.journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")
    # Cycle 2's providers answer.
    monkeypatch.setattr(
        engine.provider_registry,
        "run_core_providers",
        _providers(
            total_pnl=-1.46,
            total_volume=120.0,
            executors=[{"id": "e1"}],
            total_exposure=50.0,
            reading="ok",
        ),
    )

    ctx = asyncio.run(engine._gather_tick_context(object()))

    assert ctx is not None, "a tick that read the book is not flying blind"
    assert ctx.risk_state.is_blocked is False


def test_a_reading_in_hand_does_not_excuse_a_drawdown_that_breached(tmp_path):
    """The exemptions gate the *absent* reading only."""
    journal = JournalManager(agent_id="brigado", session_dir=tmp_path)
    journal.record_tick("peak")
    journal.record_snapshot(100.0, 1000.0, 1, 200.0, reading="ok")
    journal.record_tick("fell")
    journal.record_snapshot(20.0, 1200.0, 1, 200.0, reading="ok")
    risk = RiskEngine(RiskLimits(max_drawdown_pct=10.0))

    state = risk.get_state(journal, nothing_at_stake=True, reading_in_hand=True)

    assert state.is_blocked is True
    assert "exceeds limit" in state.block_reason


def test_a_tick_running_without_a_reading_says_so_in_its_prompt():
    """A tick that proceeds under either exemption must not show a measured 0.0%.

    Both exemptions used to block, so the "BLOCKED" line carried the fact. Now
    the drawdown figure has to carry it, or the agent plans against a floor
    nobody measured.
    """
    from condor.agents.journal import render_risk_lines

    unknown = render_risk_lines(
        {"drawdown_known": False, "drawdown_pct": 0.0, "max_drawdown_pct": 15.0}
    )
    known = render_risk_lines(
        {"drawdown_known": True, "drawdown_pct": 0.0, "max_drawdown_pct": 15.0}
    )

    assert any("not measured yet" in line for line in unknown)
    assert not any("not measured yet" in line for line in known)
    assert any("0.0% / 15.0% limit" in line for line in known)


def test_a_tick_whose_provider_did_not_run_carries_no_reading(tmp_path, monkeypatch):
    """``_last_skill_data`` survives a tick that did not refresh it.

    It is only replaced when the executors provider answers, so a tick where it
    did not run at all still holds the previous tick's stamp. That was harmless
    while nothing but a display timestamp read it; once the risk gate and the
    journal snapshot read it, a stale ``ok`` would clear a block no fresh read
    justified and write a reading nobody took.
    """
    engine = _engine(tmp_path, monkeypatch, bot_mode="bot", bot_name="brigado-grid")
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine, "_notify", _async(None))
    assert engine.ledger is not None
    engine.ledger.note_claim("brigado-grid")
    engine.ledger.note_deploy("brigado-grid")

    # Tick 1: the provider answers, and its reading is recorded.
    monkeypatch.setattr(
        engine.provider_registry,
        "run_core_providers",
        _providers(total_pnl=5.0, total_exposure=50.0, reading="ok"),
    )
    assert asyncio.run(engine._gather_tick_context(object())) is not None
    assert engine._tick_reading == "ok"

    # Tick 2: no executors provider at all. The carried dict still says "ok".
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    monkeypatch.setattr(
        engine.risk,
        "get_state",
        lambda tracker, **kw: RiskState(is_blocked=True, block_reason="held"),
    )
    assert engine._last_skill_data.get("reading") == "ok", "the dict is stale by design"

    assert asyncio.run(engine._gather_tick_context(object())) is None
    assert engine._tick_reading == "", "this tick read nothing, whatever the dict holds"
    snapshots = engine.journal._parse_snapshots()
    assert str(snapshots[-1].get("reading")) == "missing"
