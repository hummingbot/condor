"""ARCH-646 — every way a TickEngine run ends goes through one teardown.

A user stop, a shutdown, a single-tick run that completed or failed, and a loop
that hit ``max_ticks`` all end in ``TickEngine._finish``: reap the client,
release the ownership ledger, emit ``strategy_run`` telemetry, close the
journal, unregister with the final state. The self-stop paths used to skip the
ledger release and the telemetry tap, so a ``max_ticks`` loop kept owning its
bots (and their PnL) after it was over.
"""

import asyncio

import pytest

from condor.agents import engine as engine_module
from condor.agents import shutdown as shutdown_module
from condor.agents.agent import Agent
from condor.agents.ownership import read_owned
from condor.agents.strategy import Strategy
from condor.runtime.loops import LoopSupervisor
from condor.runtime.registry_file import LoopState, read_status
from condor.telemetry import taps as telemetry_taps


@pytest.fixture
def supervisor(monkeypatch):
    sup = LoopSupervisor()
    monkeypatch.setattr(engine_module, "_supervisor", lambda: sup)
    return sup


@pytest.fixture
def runs(monkeypatch):
    """Every strategy_run telemetry event, as its ``stopped_by``."""
    calls: list[str] = []

    def record(config, *, ticks=0, stopped_by=""):
        calls.append(stopped_by)

    monkeypatch.setattr(telemetry_taps, "strategy_run", record)
    return calls


def _async(result):
    async def go(*args, **kwargs):
        return result

    return go


def _engine(tmp_path, monkeypatch, *, mode, **config):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug="acme", name="Scalp")
    strategy.home.mkdir(parents=True, exist_ok=True)
    engine = engine_module.TickEngine(
        agent=Agent(slug="acme", name="Acme"),
        strategy=strategy,
        config={
            "execution_mode": mode,
            "frequency_sec": 0,
            "canvas_enabled": False,
            **config,
        },
        chat_id=1,
        user_id=42,
    )
    monkeypatch.setattr(engine, "_notify", _async(None))
    return engine


def _loop_engine_that_owns_a_bot(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, mode="loop", max_ticks=1)
    engine.ledger.adopt("acme-scalp-bot", now=100.0)
    assert read_owned(engine.session_dir)[0].until == 0.0

    async def one_tick():
        engine.journal.record_tick("tick")

    monkeypatch.setattr(engine, "_tick", one_tick)
    return engine


def _run(engine):
    async def go():
        await engine.start()
        await engine._task

    asyncio.run(go())


def test_max_ticks_completion_releases_the_ledger_and_reports_telemetry(
    tmp_path, monkeypatch, supervisor, runs
):
    engine = _loop_engine_that_owns_a_bot(tmp_path, monkeypatch)
    supervisor.register(engine)
    engine._running = True

    asyncio.run(engine._loop())

    assert read_owned(engine.session_dir)[0].until > 0
    assert runs == ["max_ticks"]
    assert read_status(engine.session_dir)["state"] == LoopState.COMPLETED
    assert supervisor.all() == {}
    assert engine._running is False


def test_run_once_completion_reports_telemetry(tmp_path, monkeypatch, supervisor, runs):
    engine = _engine(tmp_path, monkeypatch, mode="run_once")
    monkeypatch.setattr(engine, "_tick", _async(None))
    finals = []
    monkeypatch.setattr(supervisor, "record", lambda eng, state: finals.append(state))

    _run(engine)

    assert runs == ["complete"]
    assert finals[-1] == LoopState.COMPLETED
    assert supervisor.all() == {}


def test_run_once_failure_reports_telemetry(tmp_path, monkeypatch, supervisor, runs):
    engine = _engine(tmp_path, monkeypatch, mode="run_once")

    async def boom():
        raise RuntimeError("model down")

    monkeypatch.setattr(engine, "_tick", boom)
    monkeypatch.setattr(engine, "_record_failed_experiment", lambda error: None)
    finals = []
    monkeypatch.setattr(supervisor, "record", lambda eng, state: finals.append(state))

    _run(engine)

    assert runs == ["error"]
    assert finals[-1] == LoopState.ERROR
    assert supervisor.all() == {}


class _OrderedClient:
    def __init__(self, order):
        self.order = order

    async def stop(self):
        self.order.append("reap")


def test_shutdown_reports_telemetry_once(tmp_path, monkeypatch, supervisor, runs):
    engine = _engine(tmp_path, monkeypatch, mode="loop")
    order: list[str] = []
    engine._active_client = _OrderedClient(order)

    async def fake_run_shutdown(eng, reason):
        order.append("winddown")

    monkeypatch.setattr(shutdown_module, "run_shutdown", fake_run_shutdown)
    supervisor.register(engine)

    async def drive():
        await engine._run_shutdown("breach")
        await engine._run_shutdown("again")  # guarded

    asyncio.run(drive())

    assert order == ["reap", "winddown"]  # client dead before the winddown
    assert runs == ["shutdown"]
    assert read_status(engine.session_dir)["state"] == LoopState.STOPPED
    assert supervisor.all() == {}


def test_user_stop_reports_telemetry(tmp_path, monkeypatch, supervisor, runs):
    engine = _engine(tmp_path, monkeypatch, mode="loop")
    engine.ledger.adopt("acme-scalp-bot", now=100.0)
    supervisor.register(engine)

    asyncio.run(engine.stop())

    assert runs == ["user"]
    assert read_owned(engine.session_dir)[0].until > 0
    assert read_status(engine.session_dir)["state"] == LoopState.STOPPED


def test_stop_after_self_completion_is_a_noop(tmp_path, monkeypatch, supervisor, runs):
    engine = _loop_engine_that_owns_a_bot(tmp_path, monkeypatch)
    supervisor.register(engine)
    engine._running = True
    asyncio.run(engine._loop())
    until = read_owned(engine.session_dir)[0].until

    asyncio.run(engine.stop())

    assert runs == ["max_ticks"]  # no second event
    assert read_owned(engine.session_dir)[0].until == until
    assert read_status(engine.session_dir)["state"] == LoopState.COMPLETED


# ── AG1-11: stop() racing a run that is finishing itself ──


def _self_finishing_engine(tmp_path, monkeypatch, sweep_events):
    """A ``max_ticks=1`` loop whose teardown sweep is slow enough to race."""
    engine = _loop_engine_that_owns_a_bot(tmp_path, monkeypatch)
    in_sweep = asyncio.Event()

    async def slow_sweep():
        sweep_events.append("start")
        in_sweep.set()
        await asyncio.sleep(0.2)
        sweep_events.append("done")

    monkeypatch.setattr(engine, "_cancel_resting_orders", slow_sweep)
    monkeypatch.setattr(engine, "_emit_closeout", _async(None))
    monkeypatch.setattr(engine, "_preflight_gate", _async(None))
    return engine, in_sweep


def test_stop_during_a_runs_own_teardown_does_not_cancel_it(
    tmp_path, monkeypatch, supervisor, runs
):
    sweep_events: list[str] = []
    engine, in_sweep = _self_finishing_engine(tmp_path, monkeypatch, sweep_events)

    async def go():
        await engine.start()
        await asyncio.wait_for(in_sweep.wait(), timeout=5)
        assert engine._finished is True and not engine._task.done()
        return await engine.stop()

    stopped = asyncio.run(go())

    assert stopped is False, "the run ended itself; this call did not stop it"
    assert sweep_events == ["start", "done"], "the sweep ran to completion"
    assert engine._task.cancelled() is False
    assert runs == ["max_ticks"]
    assert read_status(engine.session_dir)["state"] == LoopState.COMPLETED
    assert supervisor.all() == {}


def test_stop_all_does_not_suspend_a_run_that_completed_itself(
    tmp_path, monkeypatch, supervisor, runs
):
    """SUSPENDED over COMPLETED made a ``restart_on_boot`` strategy that had
    just reached ``max_ticks`` start again on the next boot."""
    sweep_events: list[str] = []
    engine, in_sweep = _self_finishing_engine(tmp_path, monkeypatch, sweep_events)

    async def go():
        await engine.start()
        await asyncio.wait_for(in_sweep.wait(), timeout=5)
        await supervisor.stop_all()

    asyncio.run(go())

    assert sweep_events == ["start", "done"]
    assert read_status(engine.session_dir)["state"] == LoopState.COMPLETED


def test_a_cancelled_stop_does_not_cancel_the_teardown_it_waits_on(
    tmp_path, monkeypatch, supervisor, runs
):
    sweep_events: list[str] = []
    engine, in_sweep = _self_finishing_engine(tmp_path, monkeypatch, sweep_events)

    async def go():
        await engine.start()
        await asyncio.wait_for(in_sweep.wait(), timeout=5)
        waiter = asyncio.create_task(engine.stop())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await engine._task

    asyncio.run(go())

    assert sweep_events == ["start", "done"]
    assert read_status(engine.session_dir)["state"] == LoopState.COMPLETED


# ── Review R2: a stop the stop_all deadline cut off is not a clean STOPPED ──


def test_a_stop_cut_off_by_the_deadline_is_left_for_boot_to_reconcile(
    tmp_path, monkeypatch, supervisor, runs
):
    """The deadline's cancel lands in ``_finish``, whose ``finally`` unregisters
    as STOPPED -- what an owner's stop writes. A ``restart_on_boot`` loop then
    stayed down after the restart and was never reported as interrupted."""
    from condor.runtime import loops as loops_module
    from condor.runtime.registry_file import is_stale

    monkeypatch.setattr(loops_module, "STOP_ALL_DEADLINE_SEC", 0.2)
    engine = _engine(tmp_path, monkeypatch, mode="loop", restart_on_boot=True)
    in_sweep = asyncio.Event()

    async def hung_sweep():
        in_sweep.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(engine, "_tick", lambda: asyncio.sleep(3600))
    monkeypatch.setattr(engine, "_cancel_resting_orders", hung_sweep)
    monkeypatch.setattr(engine, "_emit_closeout", _async(None))
    monkeypatch.setattr(engine, "_preflight_gate", _async(None))

    async def go():
        await engine.start()
        await asyncio.sleep(0)
        await asyncio.wait_for(supervisor.stop_all(), timeout=5)

    asyncio.run(go())

    assert in_sweep.is_set(), "the stop was cut off inside its own teardown"
    status = read_status(engine.session_dir)
    assert status["state"] == LoopState.RUNNING
    assert status["restart_on_boot"] is True
    # What the next process sees: a run to report and (opted in) to restart.
    assert is_stale({**status, "boot_id": "the-previous-boot"})


def test_the_deadline_does_not_rewrite_a_run_that_was_ending_itself(
    tmp_path, monkeypatch, supervisor, runs
):
    from condor.runtime import loops as loops_module

    monkeypatch.setattr(loops_module, "STOP_ALL_DEADLINE_SEC", 0.05)
    sweep_events: list[str] = []
    engine, in_sweep = _self_finishing_engine(tmp_path, monkeypatch, sweep_events)

    async def go():
        await engine.start()
        await asyncio.wait_for(in_sweep.wait(), timeout=5)
        await supervisor.stop_all()
        await engine._task

    asyncio.run(go())

    assert sweep_events == ["start", "done"]
    assert read_status(engine.session_dir)["state"] == LoopState.COMPLETED


# ── Review R1: a cancelled /shutdown request must not abandon the winddown ──


def test_a_cancelled_shutdown_request_does_not_abandon_the_winddown(
    tmp_path, monkeypatch, supervisor, runs
):
    """uvicorn cancels in-flight requests when its graceful-shutdown timeout
    expires. The winddown ran inside the request task, so the cancel skipped
    the rest of it, recorded STOPPED and released ``_shutdown_finished`` --
    teardown's ``stop_all`` then had nothing to wait for and the process exited
    mid-close with nobody told."""
    from condor.web.routes import agents as routes

    engine = _engine(tmp_path, monkeypatch, mode="loop")
    gate = asyncio.Event()
    started = asyncio.Event()
    events: list[str] = []

    async def fake_run_shutdown(eng, reason):
        events.append("start")
        started.set()
        await gate.wait()
        events.append("done")

    monkeypatch.setattr(shutdown_module, "run_shutdown", fake_run_shutdown)
    monkeypatch.setattr(routes, "_target_engines", lambda *a, **k: [engine])
    supervisor.register(engine)

    async def go():
        request = asyncio.create_task(
            routes.shutdown_strategy("acme", "scalp", agent_id=None, user=None)
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        for _ in range(5):
            await asyncio.sleep(0)

        # The winddown is still in flight and the run is not recorded as over.
        assert events == ["start"]
        assert not engine._shutdown_finished.is_set()
        assert not engine._shutdown_task.done()
        assert runs == []
        assert read_status(engine.session_dir)["state"] != LoopState.STOPPED

        # Process teardown waits for it instead of finding nothing to wait on.
        teardown = asyncio.create_task(supervisor.stop_all())
        for _ in range(5):
            await asyncio.sleep(0)
        assert not teardown.done()

        gate.set()
        await asyncio.wait_for(teardown, timeout=5)

    asyncio.run(go())

    assert events == ["start", "done"]
    assert runs == ["shutdown"]
    assert read_status(engine.session_dir)["state"] == LoopState.STOPPED


def test_a_second_shutdown_request_joins_the_winddown_in_flight(
    tmp_path, monkeypatch, supervisor, runs
):
    engine = _engine(tmp_path, monkeypatch, mode="loop")
    gate = asyncio.Event()
    events: list[str] = []

    async def fake_run_shutdown(eng, reason):
        events.append(reason)
        await gate.wait()

    monkeypatch.setattr(shutdown_module, "run_shutdown", fake_run_shutdown)
    supervisor.register(engine)

    async def go():
        first = asyncio.create_task(engine.shutdown("one"))
        await asyncio.sleep(0)
        second = asyncio.create_task(engine.shutdown("two"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not first.done() and not second.done()
        gate.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=5)

    asyncio.run(go())

    assert events == ["one"]
    assert runs == ["shutdown"]


# ── AG2-01 (engine half): a journal failure in the error path ──


def test_a_journal_failure_while_recording_a_tick_error_does_not_end_the_loop(
    tmp_path, monkeypatch, supervisor, runs
):
    """``append_error`` raising inside the except handler was unhandled: the
    loop task ended with ``_running`` still True, so the run read "running"
    while nothing ticked. The loop carries on and the owner is still told."""
    engine = _engine(tmp_path, monkeypatch, mode="loop")
    monkeypatch.setattr(engine, "_preflight_gate", _async(None))
    monkeypatch.setattr(engine, "_cancel_resting_orders", _async(None))
    monkeypatch.setattr(engine, "_emit_closeout", _async(None))
    ticks: list[int] = []
    notices: list[str] = []

    async def tick():
        ticks.append(len(ticks) + 1)
        if len(ticks) == 1:
            raise RuntimeError(r"bad escape \d at position 53")
        engine._running = False

    async def notify(message):
        notices.append(message)

    def append_error(text):
        raise ValueError("journal is poisoned")

    monkeypatch.setattr(engine, "_tick", tick)
    monkeypatch.setattr(engine, "_notify", notify)
    monkeypatch.setattr(engine.journal, "append_error", append_error)

    async def go():
        await engine.start()
        await asyncio.wait_for(engine._task, timeout=5)

    asyncio.run(go())

    assert ticks == [1, 2], "the loop ticked again after the failed journal write"
    assert engine._task.exception() is None
    assert len(notices) == 1 and "bad escape" in notices[0]
