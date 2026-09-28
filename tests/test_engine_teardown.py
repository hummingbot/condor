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
