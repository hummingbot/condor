"""A tick that raises is still a tick.

Found on a live desk: loop ``self-improving-market-making-fleet.mm_cup_operator_2``
deployed a bot and left 46 orders on Gate, yet ``status.json`` said ``tick: 0`` and
``journal.md``'s Ticks section was empty. The supervisor derives that counter from
``journal.tick_count``, and ``record_tick`` is only reached from ``_persist_session`` —
so any tick that raised went to ``append_error`` and left the counter where it was.
A session that traded read exactly like one that never started.

These tests pin the two halves of the fix: a failed tick is counted and says why,
and a tick that already persisted before raising is not counted twice.
"""

import asyncio


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


def test_a_failed_tick_is_counted_and_says_why(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    ran = {"n": 0}

    async def exploding_tick():
        ran["n"] += 1
        engine._running = False
        raise RuntimeError("model exploded")

    monkeypatch.setattr(engine, "_tick", exploding_tick)
    engine._running = True
    asyncio.run(engine._run_ticks(0, "loop"))

    assert ran["n"] == 1
    assert engine.journal.tick_count == 1, (
        "a tick that raised must still be counted; otherwise status.json reports "
        "tick 0 for a session that may already have placed orders"
    )
    assert "failed: model exploded" in engine.journal.read_full()


def test_a_tick_already_persisted_is_not_counted_twice(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)

    async def failing_after_persist():
        # What the model-turn failure path does: _persist_tick runs, then _tick
        # raises so the error reaches the owner. The tick is already recorded.
        engine.journal.record_tick("the turn failed, but the tick was recorded")
        engine._ticked_this_iteration = True
        engine._running = False
        raise RuntimeError("turn failed")

    monkeypatch.setattr(engine, "_tick", failing_after_persist)
    engine._running = True
    asyncio.run(engine._run_ticks(0, "loop"))

    text = engine.journal.read_full()
    assert engine.journal.tick_count == 1, "the same tick must not be counted twice"
    assert "failed: turn failed" not in text


def test_persisting_a_tick_marks_the_iteration(tmp_path, monkeypatch):
    """The guard is only safe if _persist_tick actually sets the flag."""
    engine = _engine(tmp_path, monkeypatch)
    engine._ticked_this_iteration = False

    async def fake_persist(*args, **kwargs):
        return None

    monkeypatch.setattr(engine, "_persist_session", fake_persist)

    class _Ctx:
        prompt = "p"
        executors_summary = []
        risk_state = type("R", (), {"to_dict": lambda self: {}})()

    monkeypatch.setattr(engine, "_ingest_throttle_refusals", lambda: None)
    monkeypatch.setattr(
        engine, "_refusals", type("F", (), {"drain": lambda self: []})()
    )
    monkeypatch.setattr(engine, "_record_readings", lambda *a, **k: None, raising=False)

    asyncio.run(engine._persist_tick(_Ctx(), "text", [], 0.1))
    assert engine._ticked_this_iteration is True
