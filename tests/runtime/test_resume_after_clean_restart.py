"""A loop that opted into ``restart_on_boot`` must come back after a *clean*
restart, not only after a crash.

``teardown`` stops every engine, and ``engine.stop()`` records STOPPED — the
same thing a run ended by its owner writes. The boot pass only ever looked at
runs left in a live state, so the flag the UI labels "resumes on restart" fired
exactly when the process had *died*, and never when the user restarted Condor.
``stop_all`` now records SUSPENDED over that, and the next boot settles it.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from condor.runtime.loops import LoopSupervisor
from condor.runtime.registry_file import (
    BOOT_ID,
    LoopState,
    is_stale,
    is_suspended,
    read_status,
)

FOREIGN_BOOT = "00000000-dead-beef-0000-000000000000"


def _seed_suspended(
    agents_root: Path,
    *,
    agent_slug="brigado",
    sslug="mm",
    num=2,
    restart_on_boot=True,
    state=LoopState.SUSPENDED,
) -> Path:
    """A session dir as the *previous* process's shutdown left it."""
    session_dir = (
        agents_root / agent_slug / "strategies" / sslug / "sessions" / f"session_{num}"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "journal.md").write_text("# Journal\n\n## Decisions\n\n")
    (session_dir / "status.json").write_text(
        json.dumps(
            {
                "state": state,
                "boot_id": FOREIGN_BOOT,
                "agent_id": f"{agent_slug}.{sslug}_{num}",
                "agent_slug": agent_slug,
                "strategy_slug": sslug,
                "session_num": num,
                "chat_id": 555,
                "user_id": 4242,
                "tick": 4,
                "restart_on_boot": restart_on_boot,
            }
        )
    )
    return session_dir


class _StoppableEngine(SimpleNamespace):
    """Enough of an engine for ``stop_all``: stop() unregisters, as the real one does."""

    async def stop(self):
        self.supervisor.unregister(self.agent_id, LoopState.STOPPED)


def _engine(supervisor, session_dir: Path) -> _StoppableEngine:
    return _StoppableEngine(
        supervisor=supervisor,
        agent_id="brigado.mm_2",
        agent=SimpleNamespace(slug="brigado"),
        strategy=SimpleNamespace(slug="mm"),
        session_num=2,
        session_dir=session_dir,
        chat_id=555,
        user_id=4242,
        journal=SimpleNamespace(tick_count=4),
        config={"restart_on_boot": True},
    )


# ── Shutdown ──


def test_shutdown_records_suspended_not_stopped(tmp_path):
    """The state on disk says who ended the run: the process, not the owner."""
    supervisor = LoopSupervisor()
    engine = _engine(supervisor, tmp_path)
    supervisor.register(engine)

    asyncio.run(supervisor.stop_all())

    status = read_status(tmp_path)
    assert status["state"] == LoopState.SUSPENDED
    assert status["restart_on_boot"] is True
    assert supervisor.all() == {}


def test_owner_stop_still_records_stopped(tmp_path):
    """Stopping one loop by hand is unchanged — it must never come back."""
    supervisor = LoopSupervisor()
    engine = _engine(supervisor, tmp_path)
    supervisor.register(engine)

    asyncio.run(supervisor.stop("brigado.mm_2"))

    assert read_status(tmp_path)["state"] == LoopState.STOPPED


def test_is_suspended_is_the_clean_counterpart_of_is_stale():
    suspended = {"state": LoopState.SUSPENDED, "boot_id": FOREIGN_BOOT}
    assert is_suspended(suspended)
    assert not is_stale(suspended)  # nothing was lost; do not report it
    # Written by us, this run: the shutdown that wrote it is our own, later.
    assert not is_suspended({"state": LoopState.SUSPENDED, "boot_id": BOOT_ID})
    assert not is_suspended({"state": LoopState.STOPPED, "boot_id": FOREIGN_BOOT})


# ── Boot ──


def test_boot_resumes_an_opted_in_run_after_a_clean_restart(tmp_path, monkeypatch):
    """The bug the user hit: "resumes on restart" now actually does."""
    session_dir = _seed_suspended(tmp_path, restart_on_boot=True)
    supervisor = LoopSupervisor()

    started = []

    async def fake_restart(status):
        started.append(status["session_num"])
        return True

    monkeypatch.setattr(supervisor, "_restart", fake_restart)

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert started == [2]
    assert len(report.resumed) == 1
    # Nothing was lost, so nothing is reported as interrupted — a restart the
    # user asked for must not arrive as a crash notice.
    assert report.interrupted == []
    assert report.total == 0
    assert report.summary() == ""
    # The old session is closed history; the resume is a new one.
    assert read_status(session_dir)["state"] == LoopState.STOPPED


def test_boot_retires_a_run_that_did_not_opt_in(tmp_path, monkeypatch):
    """Default off stays off — and the record stops being pending."""
    session_dir = _seed_suspended(tmp_path, restart_on_boot=False)
    supervisor = LoopSupervisor()

    called = []
    monkeypatch.setattr(supervisor, "_restart", lambda status: called.append(status))

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert called == []
    assert report.resumed == []
    assert report.interrupted == []
    assert read_status(session_dir)["state"] == LoopState.STOPPED


def test_a_resume_happens_once_not_on_every_later_boot(tmp_path, monkeypatch):
    """The settled record must not read as pending again."""
    _seed_suspended(tmp_path, restart_on_boot=True)
    supervisor = LoopSupervisor()

    started = []

    async def fake_restart(status):
        started.append(status["session_num"])
        return True

    monkeypatch.setattr(supervisor, "_restart", fake_restart)

    asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))
    asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert started == [2]


def test_a_failed_resume_does_not_stay_pending(tmp_path, monkeypatch):
    """A resume that throws is reported and the record is still retired."""
    session_dir = _seed_suspended(tmp_path, restart_on_boot=True)
    supervisor = LoopSupervisor()

    async def boom(status):
        raise RuntimeError("config no longer valid")

    monkeypatch.setattr(supervisor, "_restart", boom)

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert any("config no longer valid" in e for e in report.errors)
    assert read_status(session_dir)["state"] == LoopState.STOPPED


def test_a_crash_is_still_reported_as_interrupted(tmp_path, monkeypatch):
    """The clean path must not swallow the case it was carved out of."""
    session_dir = _seed_suspended(
        tmp_path, restart_on_boot=True, state=LoopState.RUNNING
    )
    supervisor = LoopSupervisor()

    async def fake_restart(status):
        return True

    monkeypatch.setattr(supervisor, "_restart", fake_restart)

    report = asyncio.run(supervisor.reconcile_boot(agents_root=tmp_path))

    assert report.total == 1
    assert len(report.restarted) == 1
    assert report.resumed == []
    assert read_status(session_dir)["state"] == LoopState.INTERRUPTED


# ── Read side ──


def test_a_suspended_session_reads_as_stopped(tmp_path):
    """ "suspended" is a word about the process; a reader sees a run that ended."""
    from condor.agents.sessions_index import _session_run

    session_dir = _seed_suspended(tmp_path)

    assert _session_run(session_dir, 2, "brigado.mm")["status"] == LoopState.STOPPED
