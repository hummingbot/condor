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


def _fake_engine(session_dir: Path, agent_slug="brigado", sslug="mm", num=1, tick=0):
    """A stand-in with just the attributes the supervisor records."""
    return SimpleNamespace(
        agent_id=f"{agent_slug}.{sslug}_{num}",
        agent=SimpleNamespace(slug=agent_slug),
        strategy=SimpleNamespace(slug=sslug),
        session_num=num,
        session_dir=session_dir,
        chat_id=555,
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
) -> Path:
    """Write a session dir with a status file, as a previous process would."""
    session_dir = (
        agents_root / agent_slug / "strategies" / sslug / "sessions" / f"session_{num}"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "journal.md").write_text("# Journal\n\n## Decisions\n\n")
    (session_dir / "status.json").write_text(
        json.dumps(
            {
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
        )
    )
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
