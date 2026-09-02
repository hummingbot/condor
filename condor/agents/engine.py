"""TickEngine -- main orchestrator for autonomous trading agents.

One TickEngine instance per running agent.  Each tick:
1. Pre-compute core data providers (active executors)
2. Read journal (learnings + summary + recent decisions)
3. Build prompt with strategy + data + risk state + loop state
4. Spawn a fresh ACP session, stream events, capture tool calls
5. Save full snapshot and update journal
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from condor.acp.client import (
    ACPClient,
    Heartbeat,
    PromptDone,
    TextChunk,
    ToolCallEvent,
    ToolCallUpdate,
    fold_tool_call_event,
)
from condor.acp.pydantic_ai_client import PydanticAIClient
from condor.runtime import toolsets
from condor.runtime.registry_file import LoopState
from condor.runtime.timeouts import resolve_tick_timeout
from condor.telemetry import taps as telemetry_taps

from .agent import Agent
from .journal import JournalManager, next_experiment_number, next_session_number
from .prompts import build_tick_prompt
from .providers import ProviderRegistry
from .risk import RiskEngine, RiskLimits, RiskState, auto_approve_with_risk_check
from .strategy import Strategy

log = logging.getLogger(__name__)

# The running-engine registry lives in the supervisor (condor.runtime.loops),
# which is the single place that mutates it and records each transition to
# disk. These stay as thin delegations for existing callers.
#
# NOTE: neither this module nor condor.runtime.* belongs in main.py's
# modules_to_reload — they hold live process state (running tick tasks, ACP
# subprocesses), and re-executing them would orphan every running loop.


class _NullTracker:
    """Stub tracker for experiments (no journal)."""

    def get_total_exposure(self) -> float:
        return 0.0

    def get_open_executor_count(self) -> int:
        return 0

    def get_drawdown_pct(self) -> float:
        return 0.0


def _supervisor():
    from condor.runtime.loops import get_supervisor

    return get_supervisor()


def get_engine(agent_id: str) -> TickEngine | None:
    return _supervisor().get(agent_id)


def get_all_engines() -> dict[str, "TickEngine"]:
    return _supervisor().all()


@dataclass
class TickEngine:
    agent: Agent  # owning Agent: identity + shared brain (memory/skills)
    strategy: Strategy  # the playbook this run loops (tactics + config)
    config: dict[str, Any]
    chat_id: int
    user_id: int

    # Derived identity (set in __post_init__)
    agent_id: str = field(init=False)
    session_num: int = field(init=False)
    is_experiment: bool = field(default=False, init=False)

    # Components (created in __post_init__)
    journal: JournalManager = field(init=False)
    risk: RiskEngine = field(init=False)
    provider_registry: ProviderRegistry = field(init=False)
    session_dir: "Path | None" = field(default=None, init=False)
    # Which bots this session owns (FEAT-017). Controller mode only; None for
    # executor-mode runs, which are unaffected by ownership enforcement.
    ledger: "BotLedger | None" = field(default=None, init=False, repr=False)
    # Scratch KV scoped to this (agent, strategy) — see condor.runtime.state.
    state: "BoundState" = field(init=False, repr=False)

    # Runtime state
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)
    _paused: bool = field(default=False, init=False)
    _shutting_down: bool = field(default=False, init=False)
    _last_tick_at: float = field(default=0.0, init=False)
    _last_error: str = field(default="", init=False)
    _last_skill_data: dict[str, Any] = field(default_factory=dict, init=False)
    _adoption_done: bool = field(default=False, init=False, repr=False)
    _mode_mismatch_noted: bool = field(default=False, init=False, repr=False)
    # Why the loop ended, for the strategy_run telemetry event: "user" unless
    # something in the loop set it first.
    _last_stop_reason: str = field(default="user", init=False, repr=False)
    # Session canvas + live report (FEAT-036). Both None for experiments, which
    # keep no journal and therefore no narrative to render.
    _session_report: "SessionReport | None" = field(
        default=None, init=False, repr=False
    )
    _nudge: "NudgeTracker | None" = field(default=None, init=False, repr=False)
    # The live per-tick ACP client, held so stop() can reap it if the tick's own
    # finally is skipped (e.g. cancelled mid-await). None between ticks.
    _active_client: "ACPClient | PydanticAIClient | None" = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self):
        # The journal/sessions/learnings hang off the *strategy* dir (one level
        # below the Agent), so each playbook keeps its own operational history
        # while the Agent's brain (memory/skills) stays shared at the parent.
        strategy_dir = self.strategy.dir
        mode = self.config.get("execution_mode", "loop")
        self.is_experiment = mode in ("dry_run", "run_once")

        # agent_id == controller_id tag: "{agent_slug}.{strategy_slug}_{N}" (and
        # "..._e{N}" for experiments). The dot separates the two slugs cleanly —
        # slugs never contain a dot.
        run_key = f"{self.agent.slug}.{self.strategy.slug}"

        # Controller mode (FEAT-017): resolve the effective bot name BEFORE the
        # session config is persisted, so the session record — which per-session
        # PnL attribution reads back — carries the name this run actually used.
        from .ownership import (
            BotLedger,
            bot_namespace,
            declared_names,
            resolve_bot_name,
        )

        self.config["bot_name"] = resolve_bot_name(
            self.config, self.agent.slug, self.strategy.slug
        )

        if self.is_experiment:
            self.session_num = next_experiment_number(strategy_dir)
            self.agent_id = f"{run_key}_e{self.session_num}"
            # Experiments: flat folder, no session dir or journal
            self.session_dir = None
            self.journal = None
        else:
            self.session_num = next_session_number(strategy_dir)
            self.agent_id = f"{run_key}_{self.session_num}"
            self.session_dir = strategy_dir / "sessions" / f"session_{self.session_num}"
            self.session_dir.mkdir(parents=True, exist_ok=True)

            # Save config per session
            from .config import save_full_config

            save_full_config(self.session_dir, self.config)

            self.journal = JournalManager(
                self.agent_id,
                session_dir=self.session_dir,
                agent_dir=strategy_dir,
            )

        # Every session gets a ledger. Declaring a bot_name decides whether the
        # namespace rule is *enforced*, not whether deploys are *recorded*: an
        # agent in executor mode can still call manage_bots (nothing stops it, and
        # strategy playbooks routinely tell it to), and an unrecorded deploy is a
        # bot whose PnL no surface can attribute to the session that placed it.
        namespace = bot_namespace(self.agent.slug, self.strategy.slug)
        self.ledger = BotLedger(
            namespace,
            self.session_dir,
            declared=declared_names(self.config, namespace),
            enforced=bool(self.config["bot_name"]),
        )

        # The canvas and its report exist only for loop sessions: an experiment
        # has no session dir to write a canvas to and no history worth charting.
        if not self.is_experiment and self.config.get("canvas_enabled", True):
            from .canvas import NudgeTracker
            from .session_report import SessionReport

            self._nudge = NudgeTracker(
                nudge_ticks=self.config.get("canvas_nudge_ticks", 12),
                band_usd=self.config.get("canvas_band_usd", 25.0),
            )
            self._session_report = SessionReport(
                self.agent.slug,
                self.strategy.slug,
                self.session_num,
                frequency_sec=self.config.get("frequency_sec", 60),
                owner_id=self.user_id,
            )

        risk_limits = RiskLimits.from_dict(self.config.get("risk_limits", {}))
        self.risk = RiskEngine(risk_limits)
        self.provider_registry = ProviderRegistry()

        # Scratch KV for cheap facts this strategy carries across ticks (a
        # cursor, a cooldown deadline). Keyed on (agent, strategy) rather than
        # the session, so it survives into the next session — which is the
        # point of persisting it. Anything worth *remembering* goes to memory.
        from condor.runtime.state import BoundState, namespace_for

        self.state = BoundState(namespace_for(self))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, bot=None) -> None:
        """Start the tick loop as an asyncio task."""
        if self._running:
            return
        self._running = True
        self._bot = bot
        self._task = asyncio.create_task(self._loop())
        _supervisor().register(self)
        log.info(
            "TickEngine %s started (freq=%ss)",
            self.agent_id,
            self.config.get("frequency_sec", 60),
        )

    async def stop(self) -> None:
        """Stop gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Backstop: if the tick was cancelled mid-await, its own finally may not
        # have reaped the ACP subprocess. stop() is idempotent, so a double call
        # after a clean tick is a harmless no-op.
        client = self._active_client
        if client is not None:
            try:
                await client.stop()
            except Exception:
                log.exception(
                    "TickEngine %s: error reaping active client", self.agent_id
                )
            self._active_client = None
        # Close the ownership window before the journal: from here on this session
        # operates nothing, so a bot left running must stop accruing to it. The
        # next session adopts the bot on its first tick and picks the timeline up
        # from there; the gap in between belongs to no session, which is the truth.
        if self.ledger is not None:
            self.ledger.release()
        # Shape of the session for telemetry (FEAT-023): mode, cadence and tick
        # count. Never the playbook, the journal, the pairs or the positions.
        telemetry_taps.strategy_run(
            self.config,
            ticks=getattr(self.journal, "tick_count", 0) or 0,
            stopped_by=self._last_stop_reason,
        )
        if self.journal:
            self.journal.close()
        _supervisor().unregister(self.agent_id, LoopState.STOPPED)
        log.info("TickEngine %s stopped", self.agent_id)

    async def _run_shutdown(self, reason: str) -> None:
        """Emergency winddown of this session's positions/executors, then self-stop.

        This is the escalation above the plain graceful :meth:`stop` (which keeps
        positions): it runs the deterministic + LLM winddown in
        :func:`condor.agents.shutdown.run_shutdown` and always ends stopped.

        Idempotent and re-entrancy-safe via ``_shutting_down`` (a concurrent auto
        trigger + manual call runs the winddown at most once). Safe from inside the
        tick task (hard auto-trigger) or outside it (manual stop): it cancels the
        in-flight tick only when called from a *different* task — cancelling our own
        task would abort the winddown.
        """
        if self._shutting_down:
            return
        self._shutting_down = True
        self._last_stop_reason = "shutdown"
        # Halt the loop so no next/concurrent tick fights the winddown.
        self._running = False
        self._paused = True

        current = asyncio.current_task()
        if (
            self._task is not None
            and self._task is not current
            and not self._task.done()
        ):
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Reap any live per-tick client (mirrors stop()'s backstop).
        client = self._active_client
        if client is not None:
            try:
                await client.stop()
            except Exception:
                log.exception(
                    "TickEngine %s: error reaping active client during shutdown",
                    self.agent_id,
                )
            self._active_client = None

        from .shutdown import run_shutdown

        try:
            await run_shutdown(self, reason)
        except Exception:
            log.exception("TickEngine %s: shutdown sequence error", self.agent_id)
            await self._notify(
                f"🚨 Agent {self.agent_id}: shutdown sequence errored — "
                f"verify positions manually! ({reason})"
            )
        finally:
            # Mirrors stop(): the session operates nothing past this point, so its
            # ownership window closes here too. run_shutdown() may have wound the
            # bot down, but it also may have failed — either way the window ends.
            if self.ledger is not None:
                self.ledger.release()
            if self.journal:
                self.journal.close()
            _supervisor().unregister(self.agent_id, LoopState.STOPPED)
            log.info("TickEngine %s shut down (%s)", self.agent_id, reason)

    def pause(self) -> None:
        self._paused = True
        _supervisor().record(self, LoopState.PAUSED)

    def resume(self) -> None:
        self._paused = False
        _supervisor().record(self, LoopState.RUNNING)

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    @property
    def status(self) -> str:
        if not self._running:
            return "stopped"
        if self._paused:
            return "paused"
        return "running"

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        freq = self.config.get("frequency_sec", 60)
        mode = self.config.get("execution_mode", "loop")
        while self._running:
            if not self._paused:
                try:
                    await self._tick()
                    self._last_error = ""
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._last_error = str(e)
                    log.exception("TickEngine %s tick error", self.agent_id)
                    if self.journal:
                        self.journal.append_error(str(e))
                    await self._notify(f"Agent {self.agent_id} tick error: {e}")

                # A shutdown that started *inside* the tick (the hard risk
                # kill-switch) already ran its winddown, wrote the terminal
                # state and unregistered this run — it only returns here
                # because it must not cancel its own task. Recording a tick
                # now would rewrite state=running over that STOPPED, and the
                # next boot would read a live run to reconcile and possibly
                # restart the very strategy the risk engine just wound down.
                if self._shutting_down or not self._running:
                    return

                # Record the tick we just finished. A tiny atomic write, so if
                # the process dies mid-sleep the boot pass can say which tick
                # this run reached instead of guessing.
                _supervisor().record_tick(self)

                # Single-tick modes: stop after first tick
                if mode in ("dry_run", "run_once"):
                    label = "Dry run" if mode == "dry_run" else "Run-once"
                    log.info(
                        "TickEngine %s: %s complete, self-stopping",
                        self.agent_id,
                        label,
                    )
                    await self._notify(f"Agent {self.agent_id}: {label} complete.")
                    self._last_stop_reason = "complete"
                    self._running = False
                    _supervisor().unregister(self.agent_id, LoopState.COMPLETED)
                    return

                # max_ticks limit (loop mode only)
                max_ticks = self.config.get("max_ticks", 0)
                if max_ticks > 0 and self.journal.tick_count >= max_ticks:
                    log.info(
                        "TickEngine %s: reached max_ticks=%d, self-stopping",
                        self.agent_id,
                        max_ticks,
                    )
                    await self._notify(
                        f"Agent {self.agent_id}: completed {max_ticks} ticks (max_ticks limit)."
                    )
                    self._last_stop_reason = "max_ticks"
                    self._running = False
                    self.journal.close()
                    _supervisor().unregister(self.agent_id, LoopState.COMPLETED)
                    return

            try:
                await asyncio.sleep(freq)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        self._last_tick_at = time.time()
        mode = self.config.get("execution_mode", "loop")

        # 1. Get API client
        client = await self._get_client()
        if not client:
            if self.journal:
                self.journal.append_error("No API client available")
            return

        # 1b. Adopt any bot of ours already running (first tick only). A crash
        # restart always mints a NEW session (see condor/runtime/loops.py), so the
        # live bot must be taken over rather than orphaned and redeployed.
        await self._adopt_running_bots(client)

        # 2. Run core data providers (executors only -- agent uses MCP for market data)
        skill_results = await self.provider_registry.run_core_providers(
            client,
            self.config,
            agent_id=self.agent_id,
            # Adoption above just refreshed the ledger, so its bases are exactly
            # the bots this session operates right now — including any extra one
            # it deployed beyond the configured name.
            bot_names=self.ledger.bases() if self.ledger else None,
            # Earliest takeover across those bases. Bot PnL earned before it was
            # inherited, not produced by this session, so it is sliced off rather
            # than reported back to the agent as its own.
            since=(
                min((b.since for b in self.ledger.owned() if b.since > 0), default=0.0)
                if self.ledger
                else 0.0
            ),
        )

        # Extract structured data from providers for tracking
        executors_result = skill_results.get("executors")
        if executors_result:
            self._last_skill_data = executors_result.data
        positions_result = skill_results.get("positions")
        if positions_result:
            self._last_skill_data["positions"] = positions_result.data

        # Convert provider results to summary strings
        core_data_summaries: dict[str, str] = {
            name: result.summary for name, result in skill_results.items()
        }

        # 3. Read journal context (sessions only)
        learnings = self.journal.read_learnings() if self.journal else ""
        recent_decisions = (
            self.journal.get_recent_decisions(count=3) if self.journal else ""
        )
        summary = self.journal.read_summary() if self.journal else ""

        # 4. Get risk state (experiments pass None — returns clean state)
        risk_state = self.risk.get_state(self.journal or _NullTracker())
        live_executors = self._last_skill_data.get("executors", [])
        live_open_count = len(live_executors) if isinstance(live_executors, list) else 0
        risk_state.executor_count = live_open_count
        risk_state.total_exposure = float(
            self._last_skill_data.get("total_exposure", 0.0) or 0.0
        )

        # Hard kill-switch: escalate to an emergency winddown before the soft
        # pause below. Experiments never trade for real, so they never shut down.
        if risk_state.should_shutdown and not self.is_experiment:
            await self._run_shutdown(reason=risk_state.shutdown_reason)
            return

        if risk_state.is_blocked and not self.is_experiment:
            with self.journal.batch():
                self.journal.append_action(
                    self.journal.tick_count + 1,
                    "tick_blocked",
                    risk_state.block_reason,
                )
                self.journal.record_tick("blocked: " + risk_state.block_reason)
            await self._notify(
                f"Agent {self.agent_id} blocked: {risk_state.block_reason}"
            )
            return

        # 5. Build prompt (server credentials are injected via env into MCP process)
        # Routine discovery is read fresh each tick, like the skills index right
        # below it. It used to be cached on the first tick on the grounds that
        # "routines rarely change mid-session" — FEAT-090 made that false: an
        # operator switching a routine off for this agent expects it to leave
        # the loop's prompt, and a loop that has been running for a week would
        # otherwise never notice. Discovery is a directory walk, once per tick.
        from .prompts import _build_routines_section

        try:
            routines_section = _build_routines_section(self.strategy)
        except Exception:
            routines_section = ""

        # User memory index (advisory) — read fresh each tick so memory written
        # by the chat or by the agent itself shows up promptly. It's a small file
        # read, like learnings/summary above; failure never blocks a tick.
        user_memory = ""
        skills_index = ""
        try:
            from condor.memory import MemoryStore, SkillStore

            # Per-Agent memory (FEAT-003): the Agent's shared brain, keyed by the
            # *Agent* slug — shared across all its strategies and consults, not by
            # the per-strategy run.
            slug = self.agent.slug
            user_memory = MemoryStore(self.user_id, slug).list_index()
            # Skills are read-only playbooks shipped with this Agent (keyed by the
            # Agent slug only — not per-user, not learned).
            skills_index = SkillStore(slug).list_index()
        except Exception:
            pass

        next_tick = self.journal.tick_count + 1 if self.journal else 1

        # Session canvas (FEAT-036): the agent's own narrative, echoed back so it
        # can revise what is now wrong. The nudge is pure bookkeeping over state
        # we already hold, so deciding to nudge costs nothing.
        canvas_text = ""
        canvas_nudge = ""
        if self._nudge is not None:
            from . import canvas as canvas_mod

            try:
                canvas_text = canvas_mod.read_canvas(self.session_dir)
                canvas_nudge = self._nudge.next(
                    tick=next_tick,
                    last_revised_tick=canvas_mod.last_revised_tick(self.session_dir),
                    open_count=live_open_count,
                    total_pnl=float(self._last_skill_data.get("total_pnl", 0.0) or 0.0),
                    had_error=bool(self._last_error),
                )
            except Exception:
                log.exception("TickEngine %s: canvas read failed", self.agent_id)

        # Scratch cursors this strategy owns (condor.runtime.state). Whatever
        # the dashboard or an attended session left there is shown to the tick;
        # an unreadable state file must not take the tick down with it.
        try:
            loop_state = self.state.list()
        except Exception:
            log.exception("TickEngine %s: loop state read failed", self.agent_id)
            loop_state = {}

        prompt = build_tick_prompt(
            agent=self.agent,
            strategy=self.strategy,
            config=self.config,
            core_data=core_data_summaries,
            learnings=learnings,
            summary=summary,
            recent_decisions=recent_decisions,
            risk_state=risk_state.to_dict(),
            tick_number=next_tick,
            agent_id=self.agent_id,
            cached_routines_section=routines_section or None,
            user_memory=user_memory,
            skills_index=skills_index,
            ledger=self.ledger,
            canvas=canvas_text,
            canvas_nudge=canvas_nudge,
            loop_state=loop_state,
        )

        # 6. Create a fresh agent client per tick (clean context window)
        acp_client = await self._create_client(risk_state, client)
        self._active_client = acp_client

        response_chunks: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_call_map: dict[str, dict[str, Any]] = {}

        await acp_client.start()
        # Wall-clock budget for this tick's agent session. Comes from the shared
        # policy (10 min default, CONDOR_TIMEOUT_TICK_DEFAULT) unless the run
        # config sets ``tick_timeout_sec`` -- a slower model or a tick that does
        # real research needs more room than a quoting loop does.
        tick_timeout = resolve_tick_timeout(
            execution_mode=mode, strategy=self.config.get("tick_timeout_sec")
        )
        try:
            async with asyncio.timeout(tick_timeout):
                async for event in self._collect_stream(acp_client, prompt):
                    if isinstance(event, TextChunk):
                        response_chunks.append(event.text)
                    elif isinstance(event, (ToolCallEvent, ToolCallUpdate)):
                        new_tc = fold_tool_call_event(tool_call_map, event)
                        if new_tc is not None:
                            tool_calls.append(new_tc)
        except asyncio.TimeoutError:
            log.warning(
                "TickEngine %s: ACP prompt timed out after %ds",
                self.agent_id,
                tick_timeout,
            )
            response_chunks.append("(timed out)")
        finally:
            await acp_client.stop()
            self._active_client = None

        response_text = "".join(response_chunks)
        tick_duration = time.time() - self._last_tick_at

        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        executors_summary = core_data_summaries.get("executors", "No executor data.")

        if self.is_experiment:
            # Experiments: save a single snapshot file, no journal
            from .journal import save_experiment_snapshot

            save_experiment_snapshot(
                agent_dir=self.strategy.dir,
                experiment_num=self.session_num,
                execution_mode=mode,
                timestamp=timestamp,
                system_prompt=prompt,
                response_text=response_text,
                tool_calls=tool_calls,
                executors_data=executors_summary,
                risk_state=risk_state.to_dict(),
                duration=tick_duration,
                agent_key=self._agent_key(),
            )
            log.info(
                "TickEngine %s experiment #%d complete (tools=%d, response=%d chars)",
                self.agent_id,
                self.session_num,
                len(tool_calls),
                len(response_text),
            )
        else:
            # Sessions: full journal tracking. Every journal.md update of this
            # tick goes into one batch, so the file is rewritten once instead of
            # three-to-five times (PERF-136).
            with self.journal.batch():
                tick_num = self.journal.record_tick(
                    response_summary=response_text[:500],
                )

                self._journal_ownership_violations(tick_num)
                self._journal_mode_mismatch(tick_num)

                skill_pnl = self._last_skill_data.get("total_pnl", 0.0)
                skill_volume = self._last_skill_data.get("total_volume", 0.0)
                skill_executors = len(self._last_skill_data.get("executors", []))
                skill_exposure = self._last_skill_data.get("total_exposure", 0.0)
                self.journal.record_snapshot(
                    total_pnl=skill_pnl,
                    total_volume=skill_volume,
                    open_count=skill_executors,
                    position_size=skill_exposure,
                )

                action_brief = (
                    response_text[:100].replace("\n", " ")
                    if response_text
                    else "No response"
                )
                self.journal.write_summary(
                    tick=tick_num,
                    status="Running",
                    pnl=skill_pnl,
                    open_count=skill_executors,
                    last_action=action_brief,
                )

            self.journal.save_full_snapshot(
                tick=tick_num,
                timestamp=timestamp,
                system_prompt=prompt,
                response_text=response_text,
                tool_calls=tool_calls,
                executors_data=executors_summary,
                risk_state=risk_state.to_dict(),
                duration=tick_duration,
            )

            # Live session report (FEAT-036). Deterministic render over data we
            # already hold — no tokens. The guard is load-bearing: a charting or
            # report-index failure must never take down a trading tick.
            if self._session_report is not None:
                try:
                    await self._session_report.update(
                        info=self.get_info(),
                        journal=self.journal,
                        session_dir=self.session_dir,
                        executors=self._last_skill_data.get("all_executors")
                        or self._last_skill_data.get("executors")
                        or [],
                        pnl_series=await self._pnl_series(),
                    )
                except Exception:
                    log.exception(
                        "TickEngine %s: session report update failed", self.agent_id
                    )

            log.info(
                "TickEngine %s tick #%d complete (tools=%d, response=%d chars)",
                self.agent_id,
                tick_num,
                len(tool_calls),
                len(response_text),
            )

    async def _adopt_running_bots(self, client) -> None:
        """Record the bots already live that belong to us (FEAT-017).

        Runs once, on the first tick — not in ``__post_init__``, which is sync and
        must not do network I/O. Reaching the API is best-effort: a failure means
        "own nothing yet" and is retried on the next tick, never fatal.

        A namespace-enforcing session recognises its bots by name. One that does
        not enforce has no such proof, so it adopts by *lineage* instead: a live
        bot whose base an earlier session of this same strategy recorded owning is
        this strategy's bot, and the session that just replaced that one inherits
        it. Both rules are conservative — an unrecognised bot is left alone.
        """
        if self._adoption_done or self.ledger is None:
            return
        from condor.fetchers.bot_performance import fetch_all_bot_performance

        try:
            all_perf = await fetch_all_bot_performance(client)
        except Exception as e:
            log.warning("TickEngine %s: bot adoption deferred (%s)", self.agent_id, e)
            return

        from .ownership import prior_session_bases, strip_deploy_suffix

        inherited: set[str] = set()
        if not self.ledger.enforced and self.session_dir is not None:
            inherited = prior_session_bases(self.session_dir.parent)

        now = time.time()
        for instance_name in all_perf:
            base = strip_deploy_suffix(instance_name)
            if self.ledger.owns(instance_name) or base in inherited:
                self.ledger.adopt(instance_name, now)
        self._adoption_done = True
        if self.ledger.bases():
            log.info(
                "TickEngine %s adopted bots: %s",
                self.agent_id,
                ", ".join(self.ledger.bases()),
            )

    async def _pnl_series(self) -> list[dict]:
        """This session's realized curve for the live report, or ``[]``.

        Only meaningful once the session owns a bot: an executor-only session has
        no bot history to derive from and the report falls back to the journal's
        snapshots. Best-effort — a charting input must never cost a tick.
        """
        if not self.ledger or not self.ledger.bases():
            return []
        try:
            from .performance import fetch_agent_pnl_series

            client = await self._get_client()
            since = min(
                (b.since for b in self.ledger.owned() if b.since > 0), default=0.0
            )
            return await fetch_agent_pnl_series(client, self.ledger.bases(), since)
        except Exception:
            log.warning(
                "TickEngine %s: pnl series failed", self.agent_id, exc_info=True
            )
            return []

    def _journal_mode_mismatch(self, tick_num: int) -> None:
        """Flag a session configured for executors that is actually running bots.

        The config says how the session will trade; the strategy playbook says what
        the model will do — and nothing reconciles them. A session left in executor
        mode whose playbook says ``manage_bots(action="deploy", …)`` deploys bots
        that no namespace protects, and before the ledger recorded them, no PnL
        surface could attribute either. It is recorded now, so the numbers are
        right; this says so once rather than leaving the operator to wonder why an
        executor-mode agent reports a bot's PnL.
        """
        if self._mode_mismatch_noted or not self.journal or self.ledger is None:
            return
        if self.ledger.enforced or not self.ledger.bases():
            return
        self._mode_mismatch_noted = True
        bases = ", ".join(self.ledger.bases())
        log.warning(
            "TickEngine %s: configured bot_mode=%s with no bot_name (executor "
            "mode) but operates bots: %s — set bot_mode='bot' to enforce the "
            "'%s' namespace on them",
            self.agent_id,
            self.config.get("bot_mode", "auto"),
            bases,
            self.ledger.namespace,
        )
        self.journal.append_error(
            f"Config/behaviour mismatch: session runs in executor mode "
            f"(bot_name empty) but deployed bots: {bases}. Their PnL IS "
            f"attributed to this session, but they are outside the "
            f"'{self.ledger.namespace}' namespace and so are not ownership-"
            f"protected. Set bot_mode='bot' to enforce it."
        )

    def _journal_ownership_violations(self, tick_num: int) -> None:
        """Surface refused bot calls in the journal.

        The permission callback only allows or cancels. On the pydantic-ai path
        the refused call now gets a refusal string as its tool result (SEC-080),
        but on the ACP path the model still learns nothing mid-tick, so the
        correction reaches the agent here and via the next tick's
        [CONTROLLER MODE] block.
        """
        if not self.journal or self.ledger is None:
            return
        for v in self.ledger.drain_violations():
            self.journal.append_action(
                tick_num,
                "bot_ownership_blocked",
                f"manage_bots({v['action']}) on '{v['name']}' refused — outside "
                f"namespace '{self.ledger.namespace}'",
            )

    async def _collect_stream(self, acp_client: ACPClient, prompt: str):
        """Wrapper to make prompt_stream compatible with wait_for."""
        async for event in acp_client.prompt_stream(prompt):
            yield event
            if isinstance(event, PromptDone):
                break

    # ------------------------------------------------------------------
    # Client factory
    # ------------------------------------------------------------------

    async def _create_client(
        self, risk_state: RiskState, price_client: Any
    ) -> "ACPClient | PydanticAIClient":
        """Build an ACP or PydanticAI client (does NOT start it).

        ``risk_state`` is computed once in ``_tick`` and threaded through here
        (it only feeds the auto-approve callback and cannot change between the
        two points), avoiding a redundant per-tick journal re-parse.
        """
        mode = self.config.get("execution_mode", "loop")

        # A configured server pins the toolset; None falls back to the chat's.
        # tick=True narrows both subprocesses to the loop profile (FEAT-066).
        # This seat runs unattended behind an auto-approving permission callback,
        # so what it can reach at all is decided here, by what gets mounted.
        mcp_servers = toolsets.build_mcp_servers_for_session(
            self.user_id,
            self.chat_id,
            server_name=self.config.get("server_name"),
            agent_slug=self.agent.slug,
            tick=True,
        )
        permission_cb = auto_approve_with_risk_check(
            self.risk,
            risk_state,
            execution_mode=mode,
            ledger=self.ledger,
            agent_id=self.agent_id,
            price_client=price_client,
        )

        # Shared factory (ARCH-192). Engine specifics: an explicit model_base_url
        # in the run config still wins over the owner's saved custom endpoint,
        # and the run config's tool_filter_mode beats the env fallback. Same
        # allowlist the agent gets on consult; empty => unrestricted.
        from condor.runtime.llm_client import build_llm_client

        return build_llm_client(
            self._agent_key(),
            mcp_servers=mcp_servers,
            permission_callback=permission_cb,
            allowed_tools=self.agent.tools or None,
            user_id=self.user_id,
            base_url_override=self.config.get("model_base_url") or None,
            tool_filter_mode=self.config.get("tool_filter_mode"),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _agent_key(self) -> str:
        """Resolve the model for this run: config override > strategy override > Agent."""
        return (
            self.config.get("agent_key")
            or self.strategy.agent_key
            or self.agent.agent_key
        )

    def _resolve_server(self) -> tuple[str | None, dict | None]:
        """Resolve the server for this run, keyed on ``user_id`` (SEC-164).

        Every candidate — the configured name, the subject's own default, the
        accessible list — is held to the same bar here: the server must exist
        *and* ``user_id`` must be able to reach it. Two properties fall out of
        checking it at resolution rather than only at start:

        * the run never inherits identity it was handed. ``chat_id`` arrives
          from the request body, so resolving through ``get_effective_server``
          on *it* let a caller name a chat they do not own and pick up that
          chat's default server, with no server name for the start gate
          (SEC-149) to check. The subject's own default is keyed by user id
          anyway, both on the web (``set_chat_default_server(user.id, ...)``)
          and in a private Telegram chat, where chat id == user id.
        * a name that matched nothing at start stays unusable. The loop is
          long-running, so a configured ``server_name`` that resolved to
          nothing — the ``"local"`` ``AgentConfig`` fills in, typically — must
          not silently bind to whatever another user creates under that name
          later.

        Falls back to the subject's accessible servers, as before, so a run
        with no server configured still works.
        """
        from config_manager import get_config_manager, get_effective_server

        cm = get_config_manager()

        def usable(name: str | None) -> bool:
            return bool(name and cm.get_server(name)) and cm.has_server_access(
                self.user_id, name
            )

        server_name = self.config.get("server_name")
        if not usable(server_name):
            server_name = get_effective_server(self.user_id)
        if not usable(server_name):
            accessible = cm.get_accessible_servers(self.user_id)
            server_name = accessible[0] if accessible else None
        if not server_name:
            return None, None

        server = cm.get_server(server_name)
        return server_name, server

    async def _get_client(self):
        """Get the Hummingbot API client for this agent."""
        try:
            server_name, server = self._resolve_server()
            if not server:
                # Nothing to fall back to: ``_resolve_server`` already tried
                # every server this run's subject may use, so the only servers
                # left are other people's. ``get_bots_client(self.chat_id)``
                # used to stand here — it ignores the chat id for server
                # selection and, with no ``user_data`` to scope it, picks the
                # first *globally* enabled server instead (SEC-164).
                log.warning(
                    "Agent %s: no accessible server for user %s, no API client",
                    self.agent_id,
                    self.user_id,
                )
                return None

            from config_manager import get_config_manager

            cm = get_config_manager()
            return await cm.get_client(server_name)
        except Exception:
            log.exception("Failed to get API client for agent %s", self.agent_id)
            return None

    async def _notify(self, message: str) -> None:
        """Send a notification to the user via Telegram."""
        if hasattr(self, "_bot") and self._bot:
            try:
                await self._bot.send_message(chat_id=self.chat_id, text=message)
            except Exception:
                log.exception("Failed to send notification to chat %s", self.chat_id)

    def get_info(self) -> dict[str, Any]:
        """Return a summary dict for display."""
        sd = self._last_skill_data
        risk_limits = self.config.get("risk_limits", {})

        if self.journal:
            summary = self.journal.get_summary_dict()
        else:
            summary = {
                "total_ticks": 0,
                "daily_pnl": 0,
                "total_volume": 0,
                "total_exposure": 0,
                "open_executors": 0,
            }

        return {
            "agent_id": self.agent_id,
            "strategy": self.strategy.name,
            "strategy_slug": self.strategy.slug,
            "session_num": self.session_num,
            "status": self.status,
            "tick_count": summary["total_ticks"],
            "daily_pnl": sd.get("total_pnl", summary["daily_pnl"]),
            "realized_pnl": sd.get("realized_pnl", 0.0),
            "unrealized_pnl": sd.get("unrealized_pnl", 0.0),
            "total_volume": sd.get("total_volume", summary.get("total_volume", 0)),
            "total_exposure": sd.get("total_exposure", summary["total_exposure"]),
            "open_executors": len(sd.get("executors", [])) or summary["open_executors"],
            # What the PnL above is made of, and what it is missing. A session
            # operating bots earns through them, so naming them is the difference
            # between a number and an auditable one.
            "bot_names": sd.get("bot_names", []),
            "bot_instances": sd.get("bot_instances", []),
            "unresolved_bases": sd.get("unresolved_bases", []),
            "controllers": sd.get("controllers", []),
            "close_type_counts": sd.get("close_type_counts", {}),
            "fees_known": sd.get("fees_known", True),
            "frequency_sec": self.config.get("frequency_sec", 60),
            "tick_timeout_sec": resolve_tick_timeout(
                execution_mode=self.config.get("execution_mode", "loop"),
                strategy=self.config.get("tick_timeout_sec"),
            ),
            "server_name": self.config.get("server_name", ""),
            "total_amount_quote": self.config.get("total_amount_quote", 100),
            "trading_context": self.config.get("trading_context", ""),
            "risk_limits": (
                risk_limits
                if isinstance(risk_limits, dict)
                else (
                    risk_limits.model_dump()
                    if hasattr(risk_limits, "model_dump")
                    else {}
                )
            ),
            "agent_key": self._agent_key(),
            "execution_mode": self.config.get("execution_mode", "loop"),
            "max_ticks": self.config.get("max_ticks", 0),
            "last_tick_at": self._last_tick_at,
            "last_error": self._last_error,
            "session_dir": str(self.session_dir) if self.session_dir else "",
            "is_experiment": self.is_experiment,
        }
