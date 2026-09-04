"""The switch that makes "resume after a restart" reachable.

``LoopSupervisor.reconcile_boot`` has been able to restart an interrupted run
since FEAT-012, but only for a session whose status file carries
``restart_on_boot`` — and **nothing in the product ever set it**. The flag was
absent from ``AgentConfig``, so ``AgentConfig.from_dict`` (which filters to
known model fields) dropped it, no start request could carry it, and every loop
died at a restart no matter what its owner wanted. A live session on disk read
``"restart_on_boot": false`` with no UI anywhere able to make it true.

These tests pin the whole path rather than the flag alone, because the flag on
its own was never the bug: the bug was that the four hops between a person
saying yes and the boot pass reading yes had a hole in them.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from condor.agents.config import AgentConfig, load_full_config, save_full_config
from condor.runtime.loops import LoopSupervisor
from condor.runtime.registry_file import LoopState, read_status


def _engine(session_dir: Path, *, restart_on_boot: bool):
    """A stand-in with just the attributes the supervisor records."""
    return SimpleNamespace(
        agent_id="brigado.mm_1",
        agent=SimpleNamespace(slug="brigado"),
        strategy=SimpleNamespace(slug="mm"),
        session_num=1,
        session_dir=session_dir,
        chat_id=555,
        user_id=4242,
        journal=SimpleNamespace(tick_count=3),
        config={"restart_on_boot": restart_on_boot},
    )


# ── The config hop ──


def test_agent_config_carries_the_optin():
    """It is a real field, so ``from_dict``'s filter keeps it.

    This is the hop that was missing. Every other field in a start request
    survives because it is declared here; ``restart_on_boot`` was not, so a
    caller that sent it had it silently dropped on the floor.
    """
    assert AgentConfig().restart_on_boot is False
    assert AgentConfig.from_dict({"restart_on_boot": True}).restart_on_boot is True
    assert AgentConfig.from_dict({}).to_engine_dict()["restart_on_boot"] is False


def test_a_saved_optin_survives_a_round_trip(tmp_path):
    """What the boot pass re-reads is what the toggle wrote.

    ``_restart`` deliberately rebuilds from ``load_full_config`` — the config as
    it stands *now*, not as the dead session had it — so the opt-in has to live
    somewhere that survives the process, not only in engine memory.
    """
    config = load_full_config(tmp_path, {})
    assert config["restart_on_boot"] is False

    config["restart_on_boot"] = True
    save_full_config(tmp_path, config)

    assert load_full_config(tmp_path, {})["restart_on_boot"] is True


# ── The status-file hop ──


def test_the_supervisor_records_the_optin(tmp_path):
    """``record`` copies the engine's answer onto disk, where boot reads it."""
    supervisor = LoopSupervisor()

    supervisor.record(_engine(tmp_path, restart_on_boot=True), LoopState.RUNNING)
    assert read_status(tmp_path)["restart_on_boot"] is True

    supervisor.record(_engine(tmp_path, restart_on_boot=False), LoopState.RUNNING)
    assert read_status(tmp_path)["restart_on_boot"] is False


def test_flipping_a_live_engine_rewrites_its_status_without_moving_its_state(tmp_path):
    """What the route does to a running loop: the flag changes, nothing else.

    Re-recording is the point — a loop on an hourly cadence would otherwise
    carry the stale answer on disk for an hour, which is exactly the window a
    restart happens in. But recording the flag must not also claim the run is
    something it is not, so the state goes back down verbatim.
    """
    supervisor = LoopSupervisor()
    engine = _engine(tmp_path, restart_on_boot=False)

    supervisor.record(engine, LoopState.PAUSED)
    assert read_status(tmp_path)["state"] == LoopState.PAUSED

    engine.config["restart_on_boot"] = True
    supervisor.record(engine, LoopState.PAUSED)

    status = read_status(tmp_path)
    assert status["restart_on_boot"] is True
    assert status["state"] == LoopState.PAUSED  # not silently promoted to running


# ── The boot hop ──


@pytest.mark.asyncio
async def test_boot_restarts_only_the_run_that_opted_in(tmp_path, monkeypatch):
    """Two interrupted runs, one opted in: exactly one is restarted.

    The whole feature, end to end, at the seam that decides. ``_restart`` is
    stubbed because constructing a real ``TickEngine`` starts an ACP subprocess;
    what is under test is the *choice*, which is the half that was unreachable.
    """
    supervisor = LoopSupervisor()
    restarted: list[str] = []

    async def fake_restart(status):
        restarted.append(status["strategy_slug"])
        return True

    monkeypatch.setattr(supervisor, "_restart", fake_restart)

    for sslug, opted_in in (("keeps_going", True), ("stays_down", False)):
        session_dir = (
            tmp_path / "brigado" / "strategies" / sslug / "sessions" / "session_1"
        )
        session_dir.mkdir(parents=True)
        (session_dir / "journal.md").write_text("# Journal\n\n## Decisions\n\n")
        (session_dir / "status.json").write_text(
            json.dumps(
                {
                    "state": LoopState.RUNNING,
                    # A boot id that is not ours is what "the process died" means.
                    "boot_id": "00000000-dead-beef-0000-000000000000",
                    "agent_id": f"brigado.{sslug}_1",
                    "agent_slug": "brigado",
                    "strategy_slug": sslug,
                    "session_num": 1,
                    "chat_id": 555,
                    "user_id": 4242,
                    "tick": 14,
                    "restart_on_boot": opted_in,
                }
            )
        )

    report = await supervisor.reconcile_boot(tmp_path)

    # Both are recognised as interrupted; only the opted-in one comes back.
    assert {run.strategy_slug for run in report.interrupted} == {
        "keeps_going",
        "stays_down",
    }
    assert restarted == ["keeps_going"]
    assert [run.strategy_slug for run in report.restarted] == ["keeps_going"]
