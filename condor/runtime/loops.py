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

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from condor.runtime.registry_file import (
    BOOT_ID,
    STATUS_FILENAME,
    LoopState,
    is_stale,
    is_suspended,
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
class OrphanedExecutor:
    """One executor still RUNNING under a session that is not alive (CORR-726).

    Deliberately not the same thing as ``list_orphaned_positions`` reports: that
    is a *terminated* executor whose on-chain position was stranded, and the
    only recovery is to close the position by hand. This one is still running —
    still polling, still spending — and the recovery is for a live session of
    the same strategy to adopt and stop it.
    """

    executor_id: str
    controller_id: str
    agent_slug: str
    strategy_slug: str
    session_num: int
    pair: str = ""
    kind: str = ""
    chat_id: int = 0

    @property
    def label(self) -> str:
        return (
            f"{self.executor_id} ({self.kind or 'executor'} {self.pair or '?'}) "
            f"left running by {self.agent_slug}.{self.strategy_slug} "
            f"session {self.session_num}"
        )


@dataclass
class ReconcileReport:
    """What the boot pass found and did."""

    interrupted: list[InterruptedRun] = field(default_factory=list)
    restarted: list[InterruptedRun] = field(default_factory=list)
    # Runs the last process wound down on its way out and this one brought back.
    # Kept apart from ``interrupted``: nothing went wrong, so the owner gets no
    # "interrupted run" message for a restart they asked for.
    resumed: list[InterruptedRun] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    delegations: int = 0
    orphan_state: int = 0
    # Executors the previous process's sessions left running with nobody to
    # operate them (CORR-726).
    orphaned_executors: list[OrphanedExecutor] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.interrupted)

    def summary(self) -> str:
        """One message for the owner, not N."""
        lines: list[str] = []
        if self.interrupted:
            lines.append(
                f"Found {len(self.interrupted)} interrupted run(s) after restart:"
            )
            for run in self.interrupted:
                suffix = " — restarted" if run.restarted else ""
                lines.append(f"• {run.label} (last tick {run.last_tick}){suffix}")
        if self.orphaned_executors:
            if lines:
                lines.append("")
            lines.append(
                f"⚠️ {len(self.orphaned_executors)} executor(s) still RUNNING with "
                "no live session — they keep trading and polling until stopped:"
            )
            lines.extend(f"• {o.label}" for o in self.orphaned_executors)
        if not lines:
            return ""
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
        # Imported here, not at module scope: this module is deliberately
        # importable without the agent stack behind ``condor.agents``.
        from condor.agents.config import pin_snapshot

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
            # When this run last obtained a reading and last did something that
            # worked (FEAT-137). Both live in memory on the engine, so without
            # persisting them here a restart erases the only two facts that tell
            # a quiet strategy from a dead one — and nothing could read them at
            # all without a live engine. Re-written every tick, like ``pins``.
            last_reading_at=float(getattr(engine, "_last_reading_at", 0.0) or 0.0),
            last_successful_action_at=float(
                getattr(engine, "_last_successful_action_at", 0.0) or 0.0
            ),
            restart_on_boot=bool(engine.config.get("restart_on_boot", False)),
            # What this run was pinned to, so a restart can bring back the same
            # server and the same model instead of whatever the strategy config
            # happens to inherit now (CORR-728). Re-written every tick, so it
            # stays the freshest copy of the pins on disk.
            pins=pin_snapshot(engine.config),
        )

    def record_tick(self, engine) -> None:
        """Cheap per-tick counter update; keeps the last tick honest on a crash.

        Never for an engine that already left the registry: stop/shutdown wrote
        its final state on the way out, and rewriting RUNNING over it would make
        the next boot read a finished run as interrupted — and restart it when
        the session opted into ``restart_on_boot``.
        """
        if self._engines.get(engine.agent_id) is not engine:
            return
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

        Each engine records STOPPED on its way out, which is the same thing a
        run ended by its owner writes — so the next boot found nothing to
        resume and ``restart_on_boot`` fired only after a crash, which is the
        opposite of what the flag reads like. Overwrite that with SUSPENDED:
        the process ended this run, and the next one settles it.

        An engine already in an emergency winddown (risk kill-switch or manual
        /shutdown) is not cancelled: its ``stop()`` waits for the winddown to
        finish, bounded by the LLM cleanup timeout (``resolve_tick_timeout``,
        default 10 min), and returns False. That run was ended by the winddown,
        which recorded STOPPED itself, so it is not overwritten with SUSPENDED.
        """
        for engine in list(self._engines.values()):
            try:
                stopped = await engine.stop()
            except Exception:
                log.exception("Error stopping engine %s", engine.agent_id)
            else:
                if stopped:
                    self.record(engine, LoopState.SUSPENDED)

    # ── Boot reconciliation ──

    async def reconcile_boot(self, agents_root: Path | None = None) -> ReconcileReport:
        """Settle what the previous process left running.

        Two kinds of leftovers, told apart by the state on disk. A status file
        in a *live* state under a foreign boot id belongs to a process that
        died: mark it interrupted and note it in the journal. A SUSPENDED one
        was wound down by the last shutdown, so nothing is wrong with it and
        the owner hears nothing about it — it is simply retired here.

        Either way the session opting into ``restart_on_boot`` gets a *new*
        session started. An old session number is never resurrected: its
        journal is closed history. One restart per strategy — the newest
        session that opted in, so the pins and the owner that come back are the
        ones that were last live — replacing any live engine rather than adding
        to it, and each one reported only once it has a first tick to its name
        (CORR-728).

        Settling the *session* is not settling what it was operating: a
        graceful stop keeps positions and executors running by design, so every
        strategy touched here is then scanned for executors still RUNNING under
        a session that is gone (CORR-726). They are reported, never stopped —
        winding a position down is an emergency action, not a boot chore — so
        that the owner hears about an orphan instead of discovering it as an
        unexplained RPC bill.
        """
        report = ReconcileReport()
        settled: dict[tuple[str, str], dict] = {}
        # One restart per strategy per boot, and it is the NEWEST session that
        # opted in. Two settled sessions of one strategy both opted in would
        # otherwise restart twice — the second replacing the first the moment it
        # had been built (CORR-728) — and taking the first one met made that the
        # *oldest*, because the session directories are visited ascending by
        # number. A strategy stopped and started again before the crash would
        # then come back on the retired session's server and model pins and its
        # owner, with the newer session that was actually running skipped.
        chosen: dict[tuple[str, str], tuple[InterruptedRun, bool, Path, dict]] = {}

        for session_dir, status, crashed in self._sessions_to_settle(agents_root):
            run = InterruptedRun(
                agent_slug=status.get("agent_slug", "?"),
                strategy_slug=status.get("strategy_slug", "?"),
                session_num=int(status.get("session_num", 0) or 0),
                last_tick=int(status.get("tick", 0) or 0),
                session_dir=session_dir,
            )
            key = (run.agent_slug, run.strategy_slug)
            # Newest here too: this is what the orphan scan resolves its client
            # from, so it has to be the pins of the session that was last live.
            kept = settled.get(key)
            if kept is None or run.session_num >= int(kept.get("session_num", 0) or 0):
                settled[key] = status

            if crashed:
                self._mark_interrupted(session_dir, status, run)
                report.interrupted.append(run)
            else:
                # The shutdown already closed the journal and the ownership
                # window, so all that is left is to retire the record — before
                # the restart, so that a resume which throws cannot leave a
                # SUSPENDED file for every later boot to try again.
                write_status(session_dir, state=LoopState.STOPPED, boot_id=BOOT_ID)

            if status.get("restart_on_boot"):
                held = chosen.get(key)
                if held is None or run.session_num >= held[0].session_num:
                    chosen[key] = (run, crashed, session_dir, status)

        # Started as tasks, settled below: each restart waits for its own first
        # tick (CORR-728), and this pass runs inside startup, so a fleet's waits
        # have to overlap rather than add up. After the settling loop, which is
        # also what keeps every status file written before any restart reads one.
        pending: list[tuple[InterruptedRun, bool, asyncio.Task]] = [
            (run, crashed, asyncio.create_task(self._restart(status, session_dir)))
            for run, crashed, session_dir, status in chosen.values()
        ]

        for run, crashed, task in pending:
            try:
                if await task:
                    run.restarted = True
                    bucket = report.restarted if crashed else report.resumed
                    bucket.append(run)
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                log.exception("Could not restart %s", run.label)
                report.errors.append(f"{run.label}: restart failed ({exc})")

        # After the restarts above, so a session this boot brought back counts
        # as a live owner and its own executors are not reported as orphaned.
        report.orphaned_executors = await self._scan_orphaned_executors(settled)

        report.delegations = self._reconcile_delegations(agents_root)

        # Forget state belonging to strategies that no longer exist.
        try:
            from condor.runtime.state import cleanup_orphans

            report.orphan_state = cleanup_orphans(agents_root)
        except Exception:
            log.warning("State cleanup failed during boot", exc_info=True)

        if (
            report.total
            or report.resumed
            or report.delegations
            or report.orphaned_executors
        ):
            log.warning(
                "Boot reconciliation: %d interrupted, %d restarted, "
                "%d resumed, %d delegations, %d orphaned executors",
                report.total,
                len(report.restarted),
                len(report.resumed),
                report.delegations,
                len(report.orphaned_executors),
            )
        return report

    async def _scan_orphaned_executors(
        self, settled: dict[tuple[str, str], dict], client_factory=None
    ) -> list[OrphanedExecutor]:
        """Executors of the settled strategies still RUNNING under a dead session.

        One API read per strategy, best-effort: a server that cannot be reached
        or has no client configured costs this boot its orphan report and
        nothing else — the sweep must never be able to hold up startup or
        change what the rest of reconciliation did.

        ``client_factory`` takes a session status dict and returns an API
        client (or ``None``); tests pass their own, and the default resolves
        the server the way every other boot-time consumer of a status file
        does, through the owner's accessible servers.
        """
        if not settled:
            return []

        from condor.agents.executor_adoption import (
            lineage_session_num,
            orphaned_running_rows,
        )
        from condor.fetchers.executors import build_executor_row, fetch_all_executors

        factory = client_factory or _client_for_status
        found: list[OrphanedExecutor] = []
        for (agent_slug, strategy_slug), status in sorted(settled.items()):
            try:
                client = await factory(status)
                if client is None:
                    continue
                # The API's own executor status, not a ``LoopState``: the two
                # vocabularies are unrelated and only this one filters the search.
                rows = await fetch_all_executors(client, status="RUNNING")
            except Exception:
                log.warning(
                    "Orphaned-executor scan failed for %s.%s",
                    agent_slug,
                    strategy_slug,
                    exc_info=True,
                )
                continue

            live = {
                int(getattr(e, "session_num", 0) or 0)
                for e in self.for_strategy(agent_slug, strategy_slug)
            }
            for row in orphaned_running_rows(rows, agent_slug, strategy_slug, live):
                built = build_executor_row(row)
                found.append(
                    OrphanedExecutor(
                        executor_id=built["id"],
                        controller_id=built["controller_id"],
                        agent_slug=agent_slug,
                        strategy_slug=strategy_slug,
                        session_num=lineage_session_num(
                            built["controller_id"], agent_slug, strategy_slug
                        )
                        or 0,
                        pair=built["pair"],
                        kind=built["type"],
                        chat_id=int(status.get("chat_id") or 0),
                    )
                )
        for orphan in found:
            log.warning("Boot reconciliation: orphaned executor %s", orphan.label)
        return found

    def _reconcile_delegations(self, agents_root: Path | None = None) -> int:
        """Mark delegations the previous process was still running as interrupted.

        Never restarted: a delegation is one-shot, and re-running it could
        duplicate whatever side effects it already had.

        Walks the delegation store, which is keyed by user (FEAT-051), plus the
        pre-FEAT-051 agent directories: an unowned legacy record has no user
        directory to have been migrated into, and it can be stale too.
        """
        from condor import paths

        count = 0
        for user_id in paths.iter_user_ids():
            delegations = paths.delegations_dir(user_id)
            if not delegations.is_dir():
                continue
            for record_dir in sorted(p for p in delegations.iterdir() if p.is_dir()):
                count += self._interrupt_if_stale(record_dir, STATUS_FILENAME)

        count += self._reconcile_legacy_delegations(agents_root)
        return count

    def _reconcile_legacy_delegations(self, agents_root: Path | None) -> int:
        """The same sweep over ``agents/{slug}/delegations/{task}.status.json``."""
        from condor.paths import local_agents_root

        root = Path(agents_root) if agents_root is not None else local_agents_root()
        if not root.is_dir():
            return 0

        count = 0
        for agent_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            delegations = agent_dir / "delegations"
            if not delegations.is_dir():
                continue
            for path in sorted(delegations.glob("*.status.json")):
                count += self._interrupt_if_stale(delegations, path.name)
        return count

    @staticmethod
    def _interrupt_if_stale(directory: Path, filename: str) -> int:
        """1 when this status was left ``running`` by a process that is gone."""
        status = read_status(directory, filename)
        if not status or not is_stale(status):
            return 0
        write_status(directory, filename, state=LoopState.INTERRUPTED, boot_id=BOOT_ID)
        return 1

    def _sessions_to_settle(self, agents_root: Path | None):
        """Yield (session_dir, status, crashed) for every run a boot must settle.

        ``crashed`` is True for a run the last process left in a live state (it
        died holding it) and False for one that process wound down on its way
        out — the difference between a loss to report and a restart to honour.
        """
        from condor.agents.sessions_index import iter_session_dirs
        from condor.agents.strategy import LOOPS_DIRNAME
        from condor.paths import local_agents_root

        root = Path(agents_root) if agents_root is not None else local_agents_root()
        if not root.is_dir():
            return

        # <local>/*/loops/*/sessions/session_*/status.json — the layout is
        # owned by the journal, so the directory names come from there.
        for agent_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            strategies = agent_dir / LOOPS_DIRNAME
            if not strategies.is_dir():
                continue
            for strategy_dir in sorted(p for p in strategies.iterdir() if p.is_dir()):
                for _, session_dir in iter_session_dirs(strategy_dir):
                    status = read_status(session_dir)
                    if not status:
                        continue
                    if is_stale(status):
                        yield session_dir, status, True
                    elif is_suspended(status):
                        yield session_dir, status, False

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

        # A crashed process never ran stop(), so the session's ownership window is
        # still open and would keep accruing a surviving bot's PnL to a run that
        # ended at boot. Close it here — the best instant we can honestly claim is
        # the last one the dead process recorded (``updated_at``, bumped every
        # tick), not now: everything between the crash and this reboot was traded
        # by a bot nobody was operating, and must not land on this session.
        self._release_ownership(session_dir, float(status.get("updated_at") or 0.0))

        write_status(session_dir, state=LoopState.INTERRUPTED, boot_id=BOOT_ID)

    @staticmethod
    def _release_ownership(session_dir: Path, at: float = 0.0) -> None:
        """Close an interrupted session's bot-ownership window, if it kept one.

        ``at`` is the instant to close it at; 0 (nothing recorded) falls back to
        now, which is what a session with no status timestamp can honestly claim.
        """
        try:
            from condor.agents.ownership import LEDGER_FILENAME, BotLedger

            if not (session_dir / LEDGER_FILENAME).exists():
                return  # executor-mode session — never owned a bot
            BotLedger("", session_dir).release(at if at > 0 else None)
        except Exception:
            log.warning(
                "Could not release bot ownership at %s", session_dir, exc_info=True
            )

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

    async def _restart(self, status: dict, session_dir: Path | None = None) -> bool:
        """Start a fresh session for an opted-in run. Returns True if started.

        A restart has to be the same thing a start is, or "restarted" means
        something weaker than the operator read it as (CORR-728). So this
        rebuilds the config from disk *and* carries the dead session's pins
        across, makes the checks the start route makes, replaces any live engine
        for the strategy instead of adding to it, and reports only once the run
        has a first tick to its name.

        Raises ``RuntimeError`` when the restart is refused or its first tick
        failed: ``reconcile_boot`` turns that into a line the owner reads, which
        is the point — a server name that no longer exists has to be said out
        loud, not resolved to something else.
        """
        from condor.agents.agent import AgentStore
        from condor.agents.config import load_full_config
        from condor.agents.engine import FIRST_TICK_WAIT_SEC, TickEngine
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
        config = load_full_config(strategy.home, strategy.default_config)
        config["restart_on_boot"] = True
        # The dead session's *pins* are not the dead session's *config*: the
        # revalidation above is what the user edited, while the server and the
        # model were chosen for this run and live nowhere the strategy config
        # can see them. Overlaid, so a restart comes back where it was.
        pins = _pins_of(status, session_dir)
        config.update(pins)
        # A pin the dead session carried is a name *chosen* for this run, so it
        # is held to the same bar a start request's is — here and, through
        # ``server_pinned`` below, for the whole life of the restarted loop
        # (CORR-729).
        pinned_server = "server_name" in pins

        user_id = self._owner_of(status, agent, strategy)
        await self._check_restartable(
            config, strategy, agent, user_id, pinned_server=pinned_server
        )

        # A restart replaces its predecessor. Adding to it gave one strategy two
        # engines sharing one LLM budget, which is the fleet-wide tick
        # contention the race saw.
        for live in self.for_strategy(agent.slug, strategy.slug):
            log.warning(
                "Restart of %s.%s replaces live engine %s",
                agent.slug,
                strategy.slug,
                live.agent_id,
            )
            await live.stop()

        engine = TickEngine(
            agent=agent,
            strategy=strategy,
            config=config,
            chat_id=status.get("chat_id") or 0,
            user_id=user_id,
            server_pinned=pinned_server,
        )
        await engine.start()
        problem = await engine.wait_first_tick(FIRST_TICK_WAIT_SEC)
        if problem:
            # Nothing is left running under a restart that reported a failure:
            # an engine ticking on behind a "restart failed" line is the same
            # lie in the other direction.
            await engine.stop()
            raise RuntimeError(f"first tick failed: {problem}")
        log.info("Restarted %s as session %s", engine.agent_id, engine.session_num)
        return True

    @staticmethod
    async def _check_restartable(
        config: dict, strategy, agent, user_id: int, *, pinned_server: bool
    ) -> None:
        """The start route's own up-front checks, before a restart commits.

        ``_start`` refuses an unknown server with a 404 and a model that cannot
        run with a 422 (``condor/web/routes/agents.py``); a restart used to make
        neither check, so a pin naming a server deleted since came back as a run
        that could not trade. Raises ``RuntimeError`` with the same reason the
        route reports.

        ``pinned_server`` is the route's ``asked_for_it``: a name this run chose
        must resolve, while an inherited one that does not resolve is left alone
        — the engine then falls through to the owner's accessible servers, which
        is scoped already and is what ``_start`` allows too.

        Deliberately not the route's ``check_server_access``: the run already
        passed it when it was started, the owner here comes off the status file
        rather than a request, and ``_resolve_server`` scopes the credentials to
        that owner anyway.
        """
        from condor.agents.engine import resolve_agent_key
        from condor.runtime.llm_client import agent_key_error
        from config_manager import get_config_manager

        server_name = config.get("server_name")
        if (
            server_name
            and pinned_server
            and not get_config_manager().get_server(server_name)
        ):
            raise RuntimeError(f"server '{server_name}' no longer exists")

        agent_key = resolve_agent_key(config, strategy, agent)
        problem = await agent_key_error(
            agent_key,
            user_id=user_id or None,
            base_url_override=config.get("model_base_url") or None,
        )
        if problem:
            raise RuntimeError(f"model '{agent_key}' cannot run: {problem}")


def _pins_of(status: dict, session_dir: Path | None) -> dict[str, Any]:
    """What the dead session was pinned to: server, model, model base URL.

    The recorded snapshot first — ``record`` rewrites it every tick, so it is
    the freshest copy — then the session's own ``config.yml``, which is where a
    start request's overrides land and the only copy a session written before
    the snapshot existed has. Empty when neither knows, which leaves the
    strategy config's own values standing.
    """
    from condor.agents.config import load_session_pins, pin_snapshot

    recorded = status.get("pins")
    if isinstance(recorded, dict):
        pins = pin_snapshot(recorded)
        if pins:
            return pins
    if session_dir is not None:
        return load_session_pins(session_dir)
    return {}


async def _client_for_status(status: dict):
    """An API client for the server the session in ``status`` was trading on.

    The server it *recorded*, through :func:`_pins_of` — the same copy
    ``_restart`` comes back on. A session pinned to one server would otherwise
    be scanned on whichever the chat defaults to today, and its executors left
    running on the pinned one reported as nothing at all: the scan would answer
    "no orphans" about a host it never asked.

    Still resolved through the owner's accessible servers, the route a chat
    takes, so the pin narrows the search and never widens the access: a pin
    naming a server this owner cannot reach (or that is gone) falls through to
    the accessible list exactly as it did. ``None`` when nothing can be
    resolved, which the caller reads as "no orphan report for this strategy",
    never as "no orphans".
    """
    from config_manager import get_config_manager

    cm = get_config_manager()
    chat_id = int(status.get("chat_id") or 0)
    user_id = int(status.get("user_id") or 0)
    # The status dict alone: this is handed one by the scan, with no session
    # directory, and ``record`` rewrites the snapshot every tick anyway.
    pinned = str(_pins_of(status, None).get("server_name") or "") or None
    # A pin naming a server deleted since is dropped rather than carried: the
    # no-chat door below raises on an unknown name, and the whole scan is
    # best-effort — it must not cost a boot its orphan report over a stale pin.
    if pinned and cm.get_server(pinned) is None:
        pinned = None
    if chat_id:
        return await cm.get_client_for_chat(
            chat_id, user_id or None, preferred_server=pinned
        )
    return await cm.get_client(pinned)


# Process-global supervisor. Not in main.py's reload list: it holds live engines.
_supervisor = LoopSupervisor()


def get_supervisor() -> LoopSupervisor:
    return _supervisor
