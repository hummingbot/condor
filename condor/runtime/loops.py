"""Supervisor for running loops.

``TickEngine`` knows *how* to tick. The supervisor owns *whether it is running*:
one place that mutates the registry, one place that records each transition to
disk, and one boot-time pass that reconciles what the last process left behind.

Before this, registry bookkeeping was repeated inline at five exit paths inside
the engine — easy to miss one — and a restart silently orphaned every loop.

Auto-restart is deliberately opt-in per session (``restart_on_boot``). A trading
loop that resumes unattended after a crash can be dangerous, so the default is
to mark the session interrupted and tell the user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from condor.runtime.registry_file import (
    BOOT_ID,
    LoopState,
    is_stale,
    read_status,
    write_status,
)

log = logging.getLogger(__name__)


@dataclass
class InterruptedRun:
    """One run the previous process left behind."""

    agent_slug: str
    strategy_slug: str
    session_num: int
    last_tick: int
    session_dir: Path
    restarted: bool = False

    @property
    def label(self) -> str:
        return f"{self.agent_slug}.{self.strategy_slug} session {self.session_num}"


@dataclass
class ReconcileReport:
    """What the boot pass found and did."""

    interrupted: list[InterruptedRun] = field(default_factory=list)
    restarted: list[InterruptedRun] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    delegations: int = 0
    orphan_state: int = 0

    @property
    def total(self) -> int:
        return len(self.interrupted)

    def summary(self) -> str:
        """One message for the owner, not N."""
        if not self.interrupted:
            return ""
        lines = [f"Found {len(self.interrupted)} interrupted run(s) after restart:"]
        for run in self.interrupted:
            suffix = " — restarted" if run.restarted else ""
            lines.append(f"• {run.label} (last tick {run.last_tick}){suffix}")
        if self.errors:
            lines.append("")
            lines.extend(f"⚠️ {e}" for e in self.errors)
        return "\n".join(lines)


class LoopSupervisor:
    """The single owner of the running-engine registry."""

    def __init__(self):
        self._engines: dict[str, Any] = {}

    # ── Registry ──

    def register(self, engine) -> None:
        self._engines[engine.agent_id] = engine
        self.record(engine, LoopState.RUNNING)

    def unregister(self, agent_id: str, final_state: str = LoopState.STOPPED) -> None:
        """Drop an engine and record why it ended.

        Every exit path funnels here, so a run can never vanish from the
        registry without leaving a final state on disk.
        """
        engine = self._engines.pop(agent_id, None)
        if engine is not None:
            self.record(engine, final_state)

    def get(self, agent_id: str):
        return self._engines.get(agent_id)

    def all(self) -> dict[str, Any]:
        return dict(self._engines)

    def for_strategy(self, agent_slug: str, strategy_slug: str) -> list:
        """All engines (running or paused) for one (agent, strategy) pair."""
        return [
            e
            for e in self._engines.values()
            if e.agent.slug == agent_slug and e.strategy.slug == strategy_slug
        ]

    # ── Status recording ──

    def record(self, engine, state: str) -> None:
        """Write this engine's current state next to its journal."""
        session_dir = getattr(engine, "session_dir", None)
        if session_dir is None:
            return  # experiments have no session dir, by design
        journal = getattr(engine, "journal", None)
        write_status(
            session_dir,
            state=state,
            agent_id=engine.agent_id,
            agent_slug=engine.agent.slug,
            strategy_slug=engine.strategy.slug,
            session_num=engine.session_num,
            chat_id=getattr(engine, "chat_id", None),
            # The owner is part of the run's identity, not decoration: a restart
            # needs it to rebuild the same memory/skill scope and the same
            # accessible-servers fallback. Without it here there is nothing to
            # restore from, and _restart cannot construct an engine at all.
            user_id=getattr(engine, "user_id", 0),
            tick=getattr(journal, "tick_count", 0) if journal else 0,
            restart_on_boot=bool(engine.config.get("restart_on_boot", False)),
        )

    def record_tick(self, engine) -> None:
        """Cheap per-tick counter update; keeps the last tick honest on a crash."""
        self.record(engine, LoopState.RUNNING)

    # ── Lifecycle ──

    async def stop(self, agent_id: str) -> bool:
        engine = self._engines.get(agent_id)
        if engine is None:
            return False
        await engine.stop()
        return True

    async def stop_all(self) -> None:
        """Graceful stop of every engine on shutdown.

        Deliberately ``stop()`` and not the shutdown sequence: winding down
        positions is an emergency action, not what a restart should do.
        """
        for engine in list(self._engines.values()):
            try:
                await engine.stop()
            except Exception:
                log.exception("Error stopping engine %s", engine.agent_id)

    # ── Boot reconciliation ──

    async def reconcile_boot(self, agents_root: Path | None = None) -> ReconcileReport:
        """Settle what the previous process left running.

        Any status file in a live state whose boot id is not ours belongs to a
        process that died. Mark it interrupted, note it in the journal, and —
        only when the session opted in — start a *new* session. An old session
        number is never resurrected: its journal is closed history.
        """
        report = ReconcileReport()

        for session_dir, status in self._stale_sessions(agents_root):
            run = InterruptedRun(
                agent_slug=status.get("agent_slug", "?"),
                strategy_slug=status.get("strategy_slug", "?"),
                session_num=int(status.get("session_num", 0) or 0),
                last_tick=int(status.get("tick", 0) or 0),
                session_dir=session_dir,
            )

            self._mark_interrupted(session_dir, status, run)
            report.interrupted.append(run)

            if status.get("restart_on_boot"):
                try:
                    if await self._restart(status):
                        run.restarted = True
                        report.restarted.append(run)
                except Exception as exc:  # noqa: BLE001 - reported, never fatal
                    log.exception("Could not restart %s", run.label)
                    report.errors.append(f"{run.label}: restart failed ({exc})")

        report.delegations = self._reconcile_delegations(agents_root)

        # Forget state belonging to strategies that no longer exist.
        try:
            from condor.runtime.state import cleanup_orphans

            report.orphan_state = cleanup_orphans(agents_root)
        except Exception:
            log.warning("State cleanup failed during boot", exc_info=True)

        if report.total or report.delegations:
            log.warning(
                "Boot reconciliation: %d interrupted, %d restarted, %d delegations",
                report.total,
                len(report.restarted),
                report.delegations,
            )
        return report

    def _reconcile_delegations(self, agents_root: Path | None = None) -> int:
        """Mark delegations the previous process was still running as interrupted.

        Never restarted: a delegation is one-shot, and re-running it could
        duplicate whatever side effects it already had.
        """
        from condor.agents.agent import _DATA_ROOT

        root = Path(agents_root) if agents_root is not None else _DATA_ROOT
        if not root.is_dir():
            return 0

        count = 0
        for agent_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            delegations = agent_dir / "delegations"
            if not delegations.is_dir():
                continue
            for path in sorted(delegations.glob("*.status.json")):
                status = read_status(delegations, path.name)
                if not status or not is_stale(status):
                    continue
                write_status(
                    delegations,
                    path.name,
                    state=LoopState.INTERRUPTED,
                    boot_id=BOOT_ID,
                )
                count += 1
        return count

    def _stale_sessions(self, agents_root: Path | None):
        """Yield (session_dir, status) for every run a dead process left live."""
        from condor.agents.agent import _DATA_ROOT
        from condor.agents.sessions_index import SESSION_DIRNAMES

        root = Path(agents_root) if agents_root is not None else _DATA_ROOT
        if not root.is_dir():
            return

        # agents/*/strategies/*/sessions/session_*/status.json — the layout is
        # owned by the journal, so the directory names come from there.
        for agent_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            strategies = agent_dir / "strategies"
            if not strategies.is_dir():
                continue
            for strategy_dir in sorted(p for p in strategies.iterdir() if p.is_dir()):
                for dirname in SESSION_DIRNAMES:
                    sessions = strategy_dir / dirname
                    if not sessions.is_dir():
                        continue
                    for session_dir in sorted(sessions.iterdir()):
                        if not session_dir.is_dir():
                            continue
                        status = read_status(session_dir)
                        if status and is_stale(status):
                            yield session_dir, status

    def _mark_interrupted(
        self, session_dir: Path, status: dict, run: InterruptedRun
    ) -> None:
        """Record the interruption in both the status file and the journal."""
        try:
            from condor.agents.journal import JournalManager

            journal = JournalManager(
                status.get("agent_id", ""),
                session_dir=session_dir,
                agent_dir=session_dir.parent.parent,
            )
            journal.append_error(
                f"Interrupted — the process ended without stopping this run "
                f"(last recorded tick {run.last_tick}). Marked interrupted on boot."
            )
            journal.close()
        except Exception:
            # A missing/!writable journal must not stop us recording the state.
            log.warning("Could not annotate journal at %s", session_dir, exc_info=True)

        write_status(session_dir, state=LoopState.INTERRUPTED, boot_id=BOOT_ID)

    @staticmethod
    def _owner_of(status: dict, agent, strategy) -> int:
        """Who this run belongs to, for a session written by any build.

        Sessions recorded before ``record`` persisted ``user_id`` have no owner
        in their status file. Restarting those as user 0 would be worse than
        useless — it silently scopes the run to a user that owns no memory and
        no servers — so we fall back to the creator recorded in the strategy's
        (then the agent's) frontmatter, which is the same person in every path
        that can start a loop. 0 only survives when nothing on disk knows, and
        it is never fatal: the restart still happens, degraded and logged.
        """
        user_id = int(status.get("user_id") or 0)
        if user_id:
            return user_id

        fallback = int(
            getattr(strategy, "created_by", 0) or getattr(agent, "created_by", 0) or 0
        )
        log.warning(
            "Status file for %s has no user_id (written before it was recorded); "
            "restarting as user %s from the creator on disk",
            status.get("agent_id", "?"),
            fallback,
        )
        return fallback

    async def _restart(self, status: dict) -> bool:
        """Start a fresh session for an opted-in run. Returns True if started."""
        from condor.agents.agent import AgentStore
        from condor.agents.config import load_full_config
        from condor.agents.engine import TickEngine
        from condor.agents.strategy import StrategyStore

        agent = AgentStore().get(status.get("agent_slug", ""))
        strategy = StrategyStore().get(
            status.get("agent_slug", ""), status.get("strategy_slug", "")
        )
        if agent is None or strategy is None:
            log.warning("Cannot restart %s: agent or strategy gone", status)
            return False

        # Revalidate against the config as it stands NOW, not as the dead
        # session had it: the user may have changed it precisely because the
        # last run misbehaved. load_full_config raises if it no longer validates.
        config = load_full_config(strategy.dir, strategy.default_config)
        config["restart_on_boot"] = True

        engine = TickEngine(
            agent=agent,
            strategy=strategy,
            config=config,
            chat_id=status.get("chat_id") or 0,
            user_id=self._owner_of(status, agent, strategy),
        )
        await engine.start()
        log.info("Restarted %s as session %s", engine.agent_id, engine.session_num)
        return True


# Process-global supervisor. Not in main.py's reload list: it holds live engines.
_supervisor = LoopSupervisor()


def get_supervisor() -> LoopSupervisor:
    return _supervisor
