"""Regression tests for CORR-139 — the kill switch must stay killed.

When the hard risk kill-switch fires *inside* a tick, ``_run_shutdown`` winds the
session down and records the terminal ``stopped`` state, but it deliberately does
not cancel its own task, so ``_loop`` resumes right after it. It used to run the
post-tick bookkeeping unconditionally, and ``record_tick`` rewrote ``running``
over that ``stopped`` — leaving a session that the next boot read as an
interrupted live run and, with ``restart_on_boot``, restarted the very strategy
the risk engine had just emergency-wound-down.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from condor.agents import engine as engine_module
from condor.agents import shutdown as shutdown_module
from condor.agents.engine import TickEngine
from condor.runtime.loops import LoopSupervisor
from condor.runtime.registry_file import LoopState, read_status

FOREIGN_BOOT = "00000000-dead-beef-0000-000000000000"


class _FakeJournal:
    def __init__(self):
        self.tick_count = 3
        self.closed = False

    def close(self):
        self.closed = True

    def append_error(self, msg):
        pass


def _session_dir(agents_root: Path) -> Path:
    d = agents_root / "acme" / "strategies" / "scalper" / "sessions" / "session_1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "journal.md").write_text("# Journal\n\n## Decisions\n\n")
    return d


def _stub_engine(session_dir: Path, *, restart_on_boot=True, mode="loop"):
    """A stand-in carrying exactly what ``_loop``/``_run_shutdown`` touch."""
    return SimpleNamespace(
        agent_id="acme.scalper_1",
        agent=SimpleNamespace(slug="acme"),
        strategy=SimpleNamespace(slug="scalper"),
        session_num=1,
        session_dir=session_dir,
        chat_id=555,
        user_id=4242,
        journal=_FakeJournal(),
        ledger=None,
        config={
            "frequency_sec": 0,
            "execution_mode": mode,
            "restart_on_boot": restart_on_boot,
        },
        _running=True,
        _paused=False,
        _shutting_down=False,
        _task=None,
        _active_client=None,
        _last_error="",
        _last_stop_reason="",
        _notify=_swallow,
    )


async def _swallow(msg):
    pass


@pytest.fixture
def supervisor(monkeypatch):
    """A fresh supervisor wired into the engine module."""
    sup = LoopSupervisor()
    monkeypatch.setattr(engine_module, "_supervisor", lambda: sup)
    return sup


@pytest.fixture
def no_op_winddown(monkeypatch):
    """The winddown itself is FEAT-007's business; here only bookkeeping matters."""

    async def fake_run_shutdown(engine, reason):
        return None

    monkeypatch.setattr(shutdown_module, "run_shutdown", fake_run_shutdown)


def _run_loop_with_in_tick_shutdown(stub, supervisor):
    """Drive one tick that trips the kill switch, exactly as ``_tick`` does."""
    supervisor.register(stub)

    async def _tick():
        # Mirrors engine._tick: hard kill-switch escalates, then returns.
        await TickEngine._run_shutdown(stub, "max drawdown breached")

    stub._tick = _tick
    asyncio.run(TickEngine._loop(stub))


def test_in_tick_shutdown_leaves_status_stopped(tmp_path, supervisor, no_op_winddown):
    """The post-tick bookkeeping must not resurrect a shut-down session."""
    session_dir = _session_dir(tmp_path)
    stub = _stub_engine(session_dir)

    _run_loop_with_in_tick_shutdown(stub, supervisor)

    assert read_status(session_dir)["state"] == LoopState.STOPPED
    assert supervisor.all() == {}
    assert stub._shutting_down is True
    assert stub._running is False


def test_record_tick_after_unregister_is_a_no_op(tmp_path, supervisor):
    """Defense in depth: no exit path can rewrite RUNNING over a final state."""
    session_dir = _session_dir(tmp_path)
    stub = _stub_engine(session_dir)

    supervisor.register(stub)
    supervisor.unregister(stub.agent_id, LoopState.STOPPED)
    before = read_status(session_dir)

    supervisor.record_tick(stub)

    assert read_status(session_dir) == before
    assert read_status(session_dir)["state"] == LoopState.STOPPED


def test_boot_does_not_restart_a_kill_switched_session(
    tmp_path, supervisor, no_op_winddown
):
    """A next boot sees a finished run: nothing interrupted, nothing restarted."""
    session_dir = _session_dir(tmp_path)
    stub = _stub_engine(session_dir, restart_on_boot=True)

    _run_loop_with_in_tick_shutdown(stub, supervisor)

    # Simulate the next process boot: the status file this run left behind now
    # carries a foreign boot id, which is the only thing that changes on reboot.
    status = json.loads((session_dir / "status.json").read_text())
    assert status["restart_on_boot"] is True  # the opt-in that made this dangerous
    status["boot_id"] = FOREIGN_BOOT
    (session_dir / "status.json").write_text(json.dumps(status))

    restarts = []

    async def _never(status_dict):
        restarts.append(status_dict)
        return True

    supervisor._restart = _never
    report = asyncio.run(supervisor.reconcile_boot(tmp_path))

    assert report.interrupted == []
    assert report.restarted == []
    assert restarts == []


def test_normal_tick_still_records_running(tmp_path, supervisor):
    """The guard is for terminal states only — a healthy tick still records."""
    session_dir = _session_dir(tmp_path)
    stub = _stub_engine(session_dir, mode="run_once")

    states = []
    real_record = supervisor.record

    def spy(engine, state):
        states.append(state)
        real_record(engine, state)

    supervisor.record = spy

    async def _tick():
        stub.journal.tick_count += 1

    stub._tick = _tick
    supervisor.register(stub)
    asyncio.run(TickEngine._loop(stub))

    # register → post-tick record_tick → single-tick completion.
    assert states == [LoopState.RUNNING, LoopState.RUNNING, LoopState.COMPLETED]
    assert read_status(session_dir)["tick"] == 4
