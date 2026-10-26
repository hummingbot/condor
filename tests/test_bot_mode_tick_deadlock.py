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
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    monkeypatch.setattr(engine, "_notify", _async(None))
    monkeypatch.setattr(
        engine.risk,
        "get_state",
        lambda tracker, **kw: RiskState(is_blocked=True, block_reason="held"),
    )
    # What the providers read at the top of this tick, before the gate ran.
    engine._last_skill_data = {
        "total_pnl": 12.5,
        "total_volume": 400.0,
        "executors": [{"id": "e1"}],
        "total_exposure": 250.0,
        "reading": "ok",
    }

    assert asyncio.run(engine._gather_tick_context(object())) is None

    pct, known = engine.journal.get_drawdown_pct_checked()
    assert known is True, "the blocked tick's reading is on record"
    assert pct == 0.0
    assert engine.journal.get_pnl_series()[-1]["pnl"] == 12.5


def test_blocked_tick_that_read_nothing_stays_unknown(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    monkeypatch.setattr(engine, "_notify", _async(None))
    monkeypatch.setattr(
        engine.risk,
        "get_state",
        lambda tracker, **kw: RiskState(is_blocked=True, block_reason="held"),
    )
    engine._last_skill_data = {"reading": "missing"}

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
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    assert engine.ledger is not None and engine.ledger.bases() == []
    # The first tick read nothing, because there was no bot to read.
    engine._last_skill_data = {"reading": "missing"}
    engine.journal.record_tick("surveyed the market")
    engine.journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")
    assert engine.journal.tick_count == 1

    ctx = asyncio.run(engine._gather_tick_context(object()))

    assert ctx is not None, "a session with no bot deployed may still take a tick"
    assert ctx.risk_state.is_blocked is False


def test_a_recorded_bot_ends_the_exemption(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, bot_mode="bot", bot_name="brigado-grid")
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    monkeypatch.setattr(engine, "_notify", _async(None))
    assert engine.ledger is not None
    engine.ledger.note_deploy("brigado-grid")
    assert engine.ledger.bases() == ["brigado-grid"]
    # A bot is out there and the performance read cannot see it: a real blind
    # spot, and the block the guard exists for.
    engine._last_skill_data = {"reading": "missing"}
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
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    monkeypatch.setattr(engine, "_notify", _async(None))
    assert engine.ledger is not None
    assert engine.ledger.enforced is False, "no bot_name is executor mode"
    assert engine.ledger.bases() == []
    engine._last_skill_data = {"reading": "missing"}
    engine.journal.record_tick("provider failed")
    engine.journal.record_snapshot(0.0, 0.0, 0, 0.0, reading="missing")

    assert asyncio.run(engine._gather_tick_context(object())) is None
    assert "no drawdown reading on record" in engine.journal._path.read_text()
