"""Tests for the loop supervisor (FEAT-012).

The one that matters most is ``test_reconcile_marks_interrupted``: before this,
a restart left a session dir looking half-finished and the read side reported a
hardcoded "idle" — a guess. The supervisor turns "the process died" into a
recorded fact.
"""

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from condor.agents.sessions_index import infer_latest_session_status
from condor.runtime import registry_file
from condor.runtime.loops import LoopSupervisor
from condor.runtime.registry_file import (
    BOOT_ID,
    LoopState,
    is_stale,
    read_status,
    write_status,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

FOREIGN_BOOT = "00000000-dead-beef-0000-000000000000"


def _fake_engine(
    session_dir: Path, agent_slug="brigado", sslug="mm", num=1, tick=0, user_id=4242
):
    """A stand-in with just the attributes the supervisor records."""
    return SimpleNamespace(
        agent_id=f"{agent_slug}.{sslug}_{num}",
        agent=SimpleNamespace(slug=agent_slug),
        strategy=SimpleNamespace(slug=sslug),
        session_num=num,
        session_dir=session_dir,
        chat_id=555,
        user_id=user_id,
        journal=SimpleNamespace(tick_count=tick),
        config={"restart_on_boot": False},
    )


def _seed_session(
    agents_root: Path,
    *,
    agent_slug="brigado",
    sslug="mm",
    num=1,
    state=LoopState.RUNNING,
    boot_id=FOREIGN_BOOT,
    tick=7,
    restart_on_boot=False,
    user_id=4242,
) -> Path:
    """Write a session dir with a status file, as a previous process would.

    ``user_id=None`` writes the pre-CORR-082 shape, which carried no owner.
    """
    session_dir = (
        agents_root / agent_slug / "strategies" / sslug / "sessions" / f"session_{num}"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "journal.md").write_text("# Journal\n\n## Decisions\n\n")
    status = {
        "state": state,
        "boot_id": boot_id,
        "agent_id": f"{agent_slug}.{sslug}_{num}",
        "agent_slug": agent_slug,
        "strategy_slug": sslug,
        "session_num": num,
        "chat_id": 555,
        "tick": tick,
        "restart_on_boot": restart_on_boot,
    }
    if user_id is not None:
        status["user_id"] = user_id
    (session_dir / "status.json").write_text(json.dumps(status))
    return session_dir


# ── Status file ──


def test_status_file_transitions(tmp_path):
    """start -> pause -> resume -> stop is recorded in order."""
    supervisor = LoopSupervisor()
    engine = _fake_engine(tmp_path)

    supervisor.register(engine)
    assert read_status(tmp_path)["state"] == LoopState.RUNNING

    supervisor.record(engine, LoopState.PAUSED)
    assert read_status(tmp_path)["state"] == LoopState.PAUSED

    supervisor.record(engine, LoopState.RUNNING)
    assert read_status(tmp_path)["state"] == LoopState.RUNNING

    supervisor.unregister(engine.agent_id, LoopState.STOPPED)
    final = read_status(tmp_path)
    assert final["state"] == LoopState.STOPPED
    assert final["boot_id"] == BOOT_ID
    assert final["session_num"] == 1
    # The owner is recorded too: a restart cannot rebuild the run without it.
    assert final["user_id"] == 4242
    assert supervisor.all() == {}


def test_status_write_is_atomic_and_tolerant(tmp_path):
    """A corrupt file reads as absent rather than raising, and is overwritten."""
    (tmp_path / "status.json").write_text("{ this is not json")
    assert read_status(tmp_path) is None

    write_status(tmp_path, state=LoopState.RUNNING)
    assert read_status(tmp_path)["state"] == LoopState.RUNNING
    # No temp file left behind by the atomic replace.
    assert list(tmp_path.glob("*.tmp")) == []


def test_experiments_write_no_status(tmp_path):
    """Experiments have no session dir, and must not grow one."""
    supervisor = LoopSupervisor()
    engine = _fake_engine(tmp_path)
    engine.session_dir = None

    supervisor.register(engine)  # must not raise
    assert list(tmp_path.iterdir()) == []


def test_is_stale_only_for_live_states():
    assert is_stale({"state": LoopState.RUNNING, "boot_id": FOREIGN_BOOT})
    assert is_stale({"state": LoopState.PAUSED, "boot_id": FOREIGN_BOOT})
    # Already finished — nothing to reconcile.
    assert not is_stale({"state": LoopState.STOPPED, "boot_id": FOREIGN_BOOT})
    # Ours, and still running: leave it alone.
    assert not is_stale({"state": LoopState.RUNNING, "boot_id": BOOT_ID})


# ── Boot reconciliation ──


def test_reconcile_marks_interrupted(tmp_path):
    """A run left 'running' by a dead process becomes 'interrupted'."""
    session_dir = _seed_session(tmp_path, tick=7)
    supervisor = LoopSupervisor()

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert report.total == 1
    run = report.interrupted[0]
    assert run.agent_slug == "brigado"
    assert run.session_num == 1
    assert run.last_tick == 7
    assert run.restarted is False

    status = read_status(session_dir)
    assert status["state"] == LoopState.INTERRUPTED
    assert status["boot_id"] == BOOT_ID

    # The journal carries the marker, written through the JournalManager API.
    journal = (session_dir / "journal.md").read_text()
    assert "Interrupted" in journal
    assert "tick 7" in journal

    # One summary for the user, not N messages.
    assert "brigado.mm session 1" in report.summary()


def test_reconcile_ignores_current_boot(tmp_path):
    """A live engine's status file is left untouched."""
    session_dir = _seed_session(tmp_path, boot_id=BOOT_ID)
    supervisor = LoopSupervisor()

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert report.total == 0
    assert read_status(session_dir)["state"] == LoopState.RUNNING


def test_reconcile_ignores_finished_sessions(tmp_path):
    """A cleanly stopped session is not resurrected as interrupted."""
    session_dir = _seed_session(tmp_path, state=LoopState.STOPPED)
    supervisor = LoopSupervisor()

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert report.total == 0
    assert read_status(session_dir)["state"] == LoopState.STOPPED


def test_reconcile_restart_is_opt_in(tmp_path, monkeypatch):
    """Without restart_on_boot, nothing is restarted."""
    _seed_session(tmp_path, restart_on_boot=False)
    supervisor = LoopSupervisor()

    called = []
    monkeypatch.setattr(
        supervisor, "_restart", lambda status: called.append(status) or True
    )

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert report.total == 1
    assert called == []
    assert report.restarted == []


def test_reconcile_restart_opt_in_starts_new_session(tmp_path, monkeypatch):
    """With the opt-in, a NEW run starts and the old session stays interrupted."""
    session_dir = _seed_session(tmp_path, restart_on_boot=True)
    supervisor = LoopSupervisor()

    started = []

    async def fake_restart(status):
        started.append(status["agent_slug"])
        return True

    monkeypatch.setattr(supervisor, "_restart", fake_restart)

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert started == ["brigado"]
    assert len(report.restarted) == 1
    assert report.interrupted[0].restarted is True
    # The old session number is never resurrected — it stays interrupted.
    assert read_status(session_dir)["state"] == LoopState.INTERRUPTED
    assert "restarted" in report.summary()


def test_reconcile_restart_failure_is_reported_not_fatal(tmp_path, monkeypatch):
    """A restart that blows up still leaves the run interrupted and reported."""
    session_dir = _seed_session(tmp_path, restart_on_boot=True)
    supervisor = LoopSupervisor()

    async def boom(status):
        raise RuntimeError("config no longer valid")

    monkeypatch.setattr(supervisor, "_restart", boom)

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert report.total == 1
    assert report.restarted == []
    assert any("config no longer valid" in e for e in report.errors)
    assert read_status(session_dir)["state"] == LoopState.INTERRUPTED


# ── The real restart path (CORR-082) ──
#
# Every test above monkeypatches ``_restart``, which is exactly why the missing
# ``user_id`` argument went unnoticed: the real constructor was never called,
# and reconcile_boot swallows the TypeError into report.errors, so the only
# symptom was a "restart failed" line nobody read. These drive the real thing.


def _seed_agent_and_strategy(
    agents_root: Path, monkeypatch, *, agent_slug="brigado", sslug="mm", created_by=0
):
    """Create a real Agent + Strategy on disk, so the stores can load them back."""
    from condor.agents import agent as agent_module
    from condor.agents import strategy as strategy_module
    from condor.agents.agent import AgentStore
    from condor.agents.strategy import StrategyStore

    monkeypatch.setattr(agent_module, "_DATA_ROOT", agents_root)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", agents_root)
    AgentStore().create(name=agent_slug, created_by=created_by)
    StrategyStore().create(agent_slug=agent_slug, name=sslug, created_by=created_by)


def _run_real_restart(tmp_path, monkeypatch, supervisor):
    """reconcile_boot with the genuine ``_restart``; only the tick body is inert."""
    from condor.agents.engine import TickEngine

    # The engine registers itself with the process-global supervisor on start().
    monkeypatch.setattr("condor.runtime.loops._supervisor", supervisor)

    ticked = []

    async def _inert_loop(self):
        ticked.append(self.agent_id)

    monkeypatch.setattr(TickEngine, "_loop", _inert_loop)

    async def _go():
        report = await supervisor.reconcile_boot(agents_root=tmp_path)
        await asyncio.sleep(0)  # let the inert tick task finish
        return report

    return asyncio.run(_go())


def test_restart_rebuilds_the_engine_with_the_recorded_owner(tmp_path, monkeypatch):
    """The opted-in run really restarts, under the same owner and chat."""
    old_dir = _seed_session(tmp_path, restart_on_boot=True, user_id=4242)
    _seed_agent_and_strategy(tmp_path, monkeypatch, created_by=999)
    supervisor = LoopSupervisor()

    report = _run_real_restart(tmp_path, monkeypatch, supervisor)

    # No swallowed TypeError: the restart genuinely went through.
    assert report.errors == []
    assert len(report.restarted) == 1
    assert report.interrupted[0].restarted is True

    # A real engine is registered — same owner and chat, a NEW session number.
    engines = list(supervisor.all().values())
    assert len(engines) == 1
    engine = engines[0]
    assert engine.user_id == 4242
    assert engine.chat_id == 555
    assert engine.session_num == 2
    assert engine.agent_id == "brigado.mm_2"

    # The old session is closed history; the new one records the owner again,
    # so the next boot can restart it too.
    assert read_status(old_dir)["state"] == LoopState.INTERRUPTED
    assert read_status(engine.session_dir)["user_id"] == 4242
    assert read_status(engine.session_dir)["state"] == LoopState.RUNNING


def test_restart_of_a_legacy_status_falls_back_to_the_creator(tmp_path, monkeypatch):
    """A status file written before user_id existed still restarts, not crashes."""
    _seed_session(tmp_path, restart_on_boot=True, user_id=None)
    _seed_agent_and_strategy(tmp_path, monkeypatch, created_by=777)
    supervisor = LoopSupervisor()

    report = _run_real_restart(tmp_path, monkeypatch, supervisor)

    assert report.errors == []
    engine = next(iter(supervisor.all().values()))
    # Not 0: user 0 owns no memory and no servers, so the run would come back
    # silently degraded. The creator on disk is the same person in every path
    # that can start a loop.
    assert engine.user_id == 777


def test_restart_without_any_known_owner_still_starts(tmp_path, monkeypatch):
    """Nothing on disk knows the owner: restart degraded rather than not at all."""
    _seed_session(tmp_path, restart_on_boot=True, user_id=None)
    _seed_agent_and_strategy(tmp_path, monkeypatch, created_by=0)
    supervisor = LoopSupervisor()

    report = _run_real_restart(tmp_path, monkeypatch, supervisor)

    assert report.errors == []
    assert next(iter(supervisor.all().values())).user_id == 0


def test_no_opt_in_means_the_real_restart_never_runs(tmp_path, monkeypatch):
    """Still opt-in once _restart is real: interrupted, and no engine started."""
    session_dir = _seed_session(tmp_path, restart_on_boot=False, user_id=4242)
    _seed_agent_and_strategy(tmp_path, monkeypatch, created_by=999)
    supervisor = LoopSupervisor()

    report = _run_real_restart(tmp_path, monkeypatch, supervisor)

    assert report.total == 1
    assert report.restarted == []
    assert supervisor.all() == {}
    assert read_status(session_dir)["state"] == LoopState.INTERRUPTED


# ── The read side stops guessing ──


def test_sessions_index_reports_real_status(tmp_path):
    """The API surfaces 'interrupted', not the old fabricated 'idle'."""
    _seed_session(tmp_path)
    supervisor = LoopSupervisor()
    asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    strategy_dir = tmp_path / "brigado" / "strategies" / "mm"
    status = infer_latest_session_status(strategy_dir, "brigado.mm")

    assert status["status"] == LoopState.INTERRUPTED
    assert status["session_num"] == 1


def test_sessions_index_falls_back_for_legacy_sessions(tmp_path):
    """Sessions written before status files existed still report idle."""
    session_dir = tmp_path / "brigado" / "strategies" / "mm" / "sessions" / "session_3"
    session_dir.mkdir(parents=True)
    (session_dir / "journal.md").write_text("# Journal\n")

    status = infer_latest_session_status(
        tmp_path / "brigado" / "strategies" / "mm", "brigado.mm"
    )
    assert status["status"] == "idle"
    assert status["session_num"] == 3


# ── Delegations ──


def test_reconcile_marks_delegations_interrupted(tmp_path):
    """An in-flight delegation is marked interrupted — and never restarted."""
    delegations = tmp_path / "brigado" / "delegations"
    delegations.mkdir(parents=True)
    name = "brigado-delegate-abc123.status.json"
    (delegations / name).write_text(
        json.dumps(
            {
                "state": LoopState.RUNNING,
                "boot_id": FOREIGN_BOOT,
                "task_id": "brigado-delegate-abc123",
                "agent_slug": "brigado",
                "chat_id": 555,
            }
        )
    )

    supervisor = LoopSupervisor()
    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert report.delegations == 1
    assert read_status(delegations, name)["state"] == LoopState.INTERRUPTED


# ── Registry ownership ──


def test_stop_all_stops_every_engine(tmp_path):
    """Shutdown stops each engine and records a final state for each."""
    supervisor = LoopSupervisor()
    stopped = []

    dirs = []
    for i in (1, 2):
        d = tmp_path / f"s{i}"
        d.mkdir()
        dirs.append(d)
        engine = _fake_engine(d, num=i)

        async def _stop(agent_id=engine.agent_id):
            stopped.append(agent_id)
            supervisor.unregister(agent_id, LoopState.STOPPED)

        engine.stop = _stop
        supervisor.register(engine)

    asyncio.run(supervisor.stop_all())

    assert sorted(stopped) == ["brigado.mm_1", "brigado.mm_2"]
    assert supervisor.all() == {}
    for d in dirs:
        assert read_status(d)["state"] == LoopState.STOPPED


def test_for_strategy_filters_by_pair(tmp_path):
    supervisor = LoopSupervisor()
    supervisor.register(_fake_engine(tmp_path / "a", sslug="mm", num=1))
    (tmp_path / "a").mkdir(exist_ok=True)
    supervisor.register(_fake_engine(tmp_path / "b", sslug="other", num=2))

    assert [e.agent_id for e in supervisor.for_strategy("brigado", "mm")] == [
        "brigado.mm_1"
    ]
    assert supervisor.for_strategy("brigado", "nope") == []


def test_single_registration_path():
    """Nothing outside the supervisor may mutate the engine registry.

    The bookkeeping used to be repeated at five exit paths inside the engine,
    which is exactly how a run leaks out of the registry without recording why.
    """
    result = subprocess.run(
        ["git", "grep", "-n", r"_engines\[", "--", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    offenders = [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith(("condor/runtime/loops.py", "tests/"))
    ]
    assert offenders == [], f"Registry mutated outside the supervisor: {offenders}"
