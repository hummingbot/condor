"""TickEngine -- scheduler for autonomous trading agents.

One TickEngine instance per running agent run. It no longer owns an
execution stack (refactor-02): each tick is pre-flight → ``run_agent`` →
RunStore write-back. What lives here is the loop/pause/max_ticks logic, the
``_engines`` registry entry, directive injection, the risk pre-flight, the
per-tick event emission, and the shutdown escalation hook.

All operational history is RunStore events (§7.1) — the engine emits
``run_started``/``tick_started``/``tool_call``/``tick_completed``/
``state_snapshot``/``directive``/``error``/``run_ended`` on the run's
append-only stream; markdown views are generated exports.

Each tick:
1. Pre-compute core data providers (active executors)
2. Fold run context from the event stream (state + recent decisions)
3. Risk pre-flight (soft block / hard kill-switch)
4. Build prompt with agent spec + data + risk state
5. run_agent under a risk_gate policy (fresh client, clean context window)
6. Emit tick_completed + state_snapshot
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .agent import Agent
from .learnings import read_learnings
from .projections import RunMetricsTracker, run_projection
from .prompts import build_tick_prompt
from .providers import ProviderRegistry
from .risk import RiskEngine, RiskLimits, risk_gate
from .run import run_agent
from .runstore import get_run_store

log = logging.getLogger(__name__)

# Module-level registry of running engines, keyed by run_id.
_engines: dict[str, "TickEngine"] = {}


def get_engine(agent_id: str) -> TickEngine | None:
    return _engines.get(agent_id)


def get_all_engines() -> dict[str, "TickEngine"]:
    return dict(_engines)


@dataclass
class TickEngine:
    agent: Agent  # the spec: identity + strategy body + shared brain (§5.3)
    config: dict[str, Any]
    # "scheduled" marks a schedule-triggered launch (§5.4); the fire time is
    # persisted on run_started for fire-key dedup.
    kind_override: str = ""
    scheduled_for: str = ""

    # Derived identity (set in __post_init__)
    agent_id: str = field(init=False)  # the RunStore run_id (opaque ULID)
    session_num: int = field(init=False)  # display seq from run_started
    is_experiment: bool = field(default=False, init=False)

    # Components (created in __post_init__)
    frozen_spec: Any = field(default=None, init=False)
    risk: RiskEngine = field(init=False)
    metrics: RunMetricsTracker = field(init=False)
    provider_registry: ProviderRegistry = field(init=False)

    # Runtime state
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)
    _paused: bool = field(default=False, init=False)
    _shutting_down: bool = field(default=False, init=False)
    _ended: bool = field(default=False, init=False)
    _tick_count: int = field(default=0, init=False)
    _last_tick_at: float = field(default=0.0, init=False)
    _last_error: str = field(default="", init=False)
    _last_skill_data: dict[str, Any] = field(default_factory=dict, init=False)
    _pending_directives: list[str] = field(default_factory=list, init=False)
    _cached_routines_section: str | None = field(default=None, init=False, repr=False)
    # The live per-tick client (set via run_agent's on_client hook), held so
    # stop() can reap it if the tick's own finally is skipped (e.g. cancelled
    # mid-await). None between ticks.
    _active_client: Any = field(default=None, init=False, repr=False)

    def __post_init__(self):
        # run_once is not a storage mode: it's an ordinary tick run capped
        # at one tick — frozen config, risk pre-flight, attribution.
        if self.config.get("execution_mode") == "run_once":
            self.config["execution_mode"] = "loop"
            self.config["max_ticks"] = 1

        mode = self.config.get("execution_mode", "loop")
        self.is_experiment = mode == "experiment"

        # Tick runs whose config declares no risk_limits fall back to the
        # agent-level baseline (AGENT.md `risk_limits:`), the same numbers
        # that govern this agent's delegations.
        if not self.config.get("risk_limits") and self.agent.risk_limits:
            self.config["risk_limits"] = dict(self.agent.risk_limits)

        # Freeze the effective spec (§5.3) AFTER the config mutations above so
        # the frozen view is the config the run actually executes: validates
        # the agent spec (denomination/schedule rules) and computes both spec
        # hashes. Launch merging + stricter-only risk enforcement already
        # happened in lifecycle.start_session.
        from .spec import freeze_spec

        try:
            from .agent import AgentStore

            source_text = AgentStore().source_text(self.agent.slug)
        except Exception:
            source_text = ""
        self.frozen_spec = freeze_spec(
            self.agent, self.config, source_text=source_text
        )

        # Open the run stream: run_started carries the frozen spec, both
        # content hashes (§5.3), and the venue in play. The run id is an
        # opaque ULID — the legacy "{slug}_{N}" grammar is gone.
        store = get_run_store()
        kind = self.kind_override or ("experiment" if self.is_experiment else "session")
        payload: dict[str, Any] = {
            "model": self._agent_key(),
            "frozen_spec": self.frozen_spec.to_public_dict(),
            "source_spec_hash": self.frozen_spec.source_hash,
            "resolved_spec_hash": self.frozen_spec.resolved_hash,
            "venue": self.config.get("venue", ""),
            "execution_mode": mode,
            "risk_limits": dict(self.config.get("risk_limits") or {}),
        }
        if self.scheduled_for:
            payload["scheduled_for"] = self.scheduled_for
        self.agent_id = store.start_run(self.agent.slug, kind, payload=payload)
        self.session_num = store.run_meta(self.agent.slug, self.agent_id).get(
            "display_seq", 0
        )

        risk_limits = RiskLimits.from_dict(self.config.get("risk_limits", {}))
        self.risk = RiskEngine(risk_limits)
        self.metrics = RunMetricsTracker()
        self.provider_registry = ProviderRegistry()

    # ------------------------------------------------------------------
    # RunStore emission
    # ------------------------------------------------------------------

    def _emit(self, type_: str, payload: dict | None = None, **corr) -> None:
        """Best-effort event append — persistence failures never take down
        the trading loop (the executor store stays the financial authority)."""
        try:
            get_run_store().emit(self.agent_id, type_, payload, **corr)
        except Exception:
            log.exception("TickEngine %s: run event emit failed (%s)", self.agent_id, type_)

    def _end_run(self, status: str, reason: str = "") -> None:
        if self._ended:
            return
        self._ended = True
        try:
            # A run's pending approvals die with it (§4.2) — the grant could
            # never be consumed by a run that no longer exists.
            from condor.agents.approvals import get_approval_manager

            get_approval_manager().void_run(self.agent_id)
        except Exception:
            log.exception("TickEngine %s: approval voiding failed", self.agent_id)
        try:
            get_run_store().end_run(self.agent_id, status, reason)
        except Exception:
            log.exception("TickEngine %s: run_ended emit failed", self.agent_id)

    def record_decision(self, action: str, note: str = "") -> None:
        """Durable one-line decision entry (shutdown journal replacement)."""
        text = action if not note else f"{action} — {note}"
        self._emit("state_snapshot", {"decision": text}, tick=self._tick_count or None)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the tick loop as an asyncio task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        _engines[self.agent_id] = self
        log.info(
            "TickEngine %s (%s #%d) started (freq=%ss)",
            self.agent_id,
            self.agent.slug,
            self.session_num,
            self.config.get("frequency_sec", 60),
        )

    async def stop(self, close: bool = False) -> None:
        """Stop gracefully. ``close=True`` (control_run stop --close, §6.2)
        overrides the config's position-preserving default and closes this
        run's remaining owned inventory."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Backstop: if the tick was cancelled mid-await, run_agent's own finally
        # may not have reaped the client subprocess. stop() is idempotent, so a
        # double call after a clean tick is a harmless no-op.
        client = self._active_client
        if client is not None:
            try:
                await client.stop()
            except Exception:
                log.exception(
                    "TickEngine %s: error reaping active client", self.agent_id
                )
            self._active_client = None
        # Stop this run's native executors too — a stopped agent must not
        # leave executors polling Gateway. The agent config's
        # `keep_position_on_stop` decides fate: default True detaches (positions
        # stay in the wallet, unmanaged — stop is position-preserving; /shutdown
        # is the liquidating escalation); an explicit False swaps positions back
        # to the quote token. Either way the executor loops stop.
        from condor.executors.service import peek_executor_runtime

        runtime = peek_executor_runtime()
        if runtime is not None:
            keep_position = (
                False if close else bool(self.config.get("keep_position_on_stop", True))
            )
            stopped = runtime.stop_agent_executors(
                self.agent_id, keep_position=keep_position
            )
            if stopped:
                log.info(
                    "TickEngine %s: stopped %d native executor(s): %s",
                    self.agent_id,
                    len(stopped),
                    stopped,
                )
        if _engines.pop(self.agent_id, None) is not None:
            self._end_run("stopped", "manual stop")
        log.info("TickEngine %s stopped", self.agent_id)

    async def _run_shutdown(self, reason: str) -> None:
        """Emergency winddown of this run's positions/executors, then self-stop.

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
            _engines.pop(self.agent_id, None)
            self._end_run("stopped", f"shutdown: {reason}")
            log.info("TickEngine %s shut down (%s)", self.agent_id, reason)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def inject_directive(self, text: str) -> None:
        """Queue a user directive to be included in the next tick's prompt."""
        self._pending_directives.append(text)
        self._emit("directive", {"text": text, "acked": False})
        log.info("TickEngine %s: directive queued: %s", self.agent_id, text[:80])

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    @property
    def is_paused(self) -> bool:
        return self._paused

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
        import random

        freq = self.config.get("frequency_sec", 60)
        mode = self.config.get("execution_mode", "loop")
        # Startup jitter: concurrently-started agents would otherwise tick in
        # lockstep, stacking model + venue load at the same instant.
        if mode == "loop":
            try:
                await asyncio.sleep(random.uniform(0, min(freq, 10)))
            except asyncio.CancelledError:
                return
        while self._running:
            tick_started = time.monotonic()
            if not self._paused:
                try:
                    await self._tick()
                    self._last_error = ""
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._last_error = str(e)
                    log.exception("TickEngine %s tick error", self.agent_id)
                    self._emit("error", {"message": str(e)}, tick=self._tick_count)
                    await self._notify(f"Agent {self.agent_id} tick error: {e}")

                # Experiments: single tick, then self-stop
                if mode == "experiment":
                    log.info(
                        "TickEngine %s: experiment complete, self-stopping",
                        self.agent_id,
                    )
                    await self._notify(f"Agent {self.agent_id}: Experiment complete.")
                    self._running = False
                    _engines.pop(self.agent_id, None)
                    self._end_run("completed", "experiment complete")
                    return

                # max_ticks limit (covers run_once via max_ticks=1)
                max_ticks = self.config.get("max_ticks", 0)
                if max_ticks > 0 and self._tick_count >= max_ticks:
                    log.info(
                        "TickEngine %s: reached max_ticks=%d, self-stopping",
                        self.agent_id,
                        max_ticks,
                    )
                    await self._notify(
                        f"Agent {self.agent_id}: completed {max_ticks} ticks (max_ticks limit)."
                    )
                    self._running = False
                    _engines.pop(self.agent_id, None)
                    self._end_run("completed", f"max_ticks={max_ticks}")
                    return

            # Fixed-rate scheduling: frequency_sec is the interval between tick
            # STARTS, not a sleep appended after each tick — a 45s model run on
            # a 60s agent still ticks every 60s. An overrunning tick starts the
            # next one immediately and the lag is logged.
            elapsed = time.monotonic() - tick_started
            if not self._paused and elapsed > freq:
                log.warning(
                    "TickEngine %s: tick took %.1fs > frequency %ss (lag %.1fs)",
                    self.agent_id,
                    elapsed,
                    freq,
                    elapsed - freq,
                )
            try:
                await asyncio.sleep(max(0.0, freq - elapsed))
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        self._last_tick_at = time.time()
        mode = self.config.get("execution_mode", "loop")

        # 1. Run core data providers (native executors only — the agent uses
        #    MCP for market data). The provider reads the in-process executor
        #    store directly.
        core_data_summaries = await self._collect_provider_state()

        # 2. Fold run context from the event stream (state + recent decisions)
        learnings = read_learnings(self.agent.agent_dir)
        try:
            store = get_run_store()
            proj = run_projection(
                store.read_events(self.agent.slug, self.agent_id)
            )
        except Exception:
            log.exception("TickEngine %s: run projection failed", self.agent_id)
            proj = {"state": "", "recent_decisions": ""}
        summary = proj.get("state", "")
        recent_decisions = proj.get("recent_decisions", "")

        # 3. Get risk state — exposure/count are venue-record truth via the
        #    provider fold; the pnl series is this run's in-memory history.
        risk_state = self.risk.get_state(self.metrics)

        # Hard kill-switch: escalate to an emergency winddown before the soft
        # pause below. Experiments never trade for real, so they never shut down.
        if risk_state.should_shutdown and not self.is_experiment:
            await self._run_shutdown(reason=risk_state.shutdown_reason)
            return

        if risk_state.is_blocked and not self.is_experiment:
            self._tick_count += 1
            self._emit(
                "tick_completed",
                {
                    "actions": 0,
                    "blocked": True,
                    "block_reason": risk_state.block_reason,
                },
                tick=self._tick_count,
            )
            await self._notify(
                f"Agent {self.agent_id} blocked: {risk_state.block_reason}"
            )
            return

        # 4. Build prompt.
        # Cache routine discovery on first tick — routines rarely change mid-run.
        if self._cached_routines_section is None:
            from .prompts import _build_routines_section

            try:
                self._cached_routines_section = _build_routines_section(
                    self.agent.slug
                )
            except Exception:
                self._cached_routines_section = ""

        # Agent memory index (advisory) — read fresh each tick so memory written
        # by the chat or by the agent itself shows up promptly. It's a small file
        # read; failure never blocks a tick.
        user_memory = ""
        skills_index = ""
        try:
            from condor.memory import MemoryStore, SkillStore

            slug = self.agent.slug
            user_memory = MemoryStore(slug).list_index()
            skills_index = SkillStore(slug).list_index()
        except Exception:
            pass

        next_tick = self._tick_count + 1
        prompt = build_tick_prompt(
            agent=self.agent,
            config=self.config,
            core_data=core_data_summaries,
            learnings=learnings,
            summary=summary,
            recent_decisions=recent_decisions,
            risk_state=risk_state.to_dict(),
            tick_number=next_tick,
            agent_id=self.agent_id,
            cached_routines_section=self._cached_routines_section or None,
            user_memory=user_memory,
            skills_index=skills_index,
        )

        # Inject pending user directives. Acknowledged (dequeued) only AFTER the
        # tick's run completes — a timeout/crash mid-run must not lose them.
        directives = list(self._pending_directives)
        if directives:
            listing = "\n".join(f"- {d}" for d in directives)
            prompt += f"\n\nUSER DIRECTIVES (apply these on this tick):\n{listing}"

        self._emit("tick_started", {}, tick=next_tick)

        # 5. One run_agent call per tick (fresh client, clean context window).
        # risk_state was computed once above and threads into the gate so the
        # per-call checks accumulate against this tick's running totals.
        def _hold_client(c):
            self._active_client = c

        from condor.agents.gating import is_dangerous_tool_call

        def _persist_tool_call(tc: dict) -> None:
            self._emit(
                "tool_call",
                {
                    "name": tc.get("name") or tc.get("title", ""),
                    "status": tc.get("status", ""),
                    "input": tc.get("input"),
                    "output": tc.get("output"),
                    "mutating": is_dangerous_tool_call(tc),
                },
                tick=next_tick,
                tool_call_id=str(tc.get("id") or "") or None,
            )

        result = await run_agent(
            self.agent,
            prompt,
            permission_policy=risk_gate(self.risk, risk_state, experiment=self.is_experiment),
            execution_mode=mode,
            model=self._agent_key(),
            risk_limits=self.config.get("risk_limits") or None,
            timeout_s=300,
            on_client=_hold_client,
            agent_id=self.agent_id,
            on_tool_call=_persist_tool_call,
        )

        # The attempt completed — acknowledge the directives it carried.
        # (Directives queued DURING the run stay pending for the next tick.)
        for d in directives:
            try:
                self._pending_directives.remove(d)
            except ValueError:
                pass
            self._emit("directive", {"text": d, "acked": True}, tick=next_tick)

        response_text = result.text
        tool_calls = result.tool_calls
        tick_duration = time.time() - self._last_tick_at

        # Post-tick refresh: tool calls mutate executor state, so the snapshot
        # must record the COMPLETED tick (an order created this tick shows
        # open=1, not the pre-action open=0). Failure keeps the pre-run view.
        if tool_calls:
            try:
                core_data_summaries = await self._collect_provider_state()
            except Exception:
                log.exception(
                    "TickEngine %s: post-tick provider refresh failed", self.agent_id
                )

        self._tick_count = next_tick
        metrics = {
            "total_pnl": self._last_skill_data.get("total_pnl", 0.0),
            "total_volume": self._last_skill_data.get("total_volume", 0.0),
            "total_exposure": self._last_skill_data.get("total_exposure", 0.0),
            "open_count": len(self._last_skill_data.get("executors", [])),
        }
        self.metrics.update(
            total_exposure=metrics["total_exposure"],
            open_count=metrics["open_count"],
            total_pnl=metrics["total_pnl"],
        )
        self._emit(
            "state_snapshot",
            {
                "response": response_text[:2000],
                "metrics": metrics,
                "duration": tick_duration,
            },
            tick=next_tick,
        )
        self._emit(
            "tick_completed",
            {
                "actions": len(tool_calls),
                "metrics": metrics,
                "duration": tick_duration,
                "timed_out": result.timed_out,
            },
            tick=next_tick,
        )
        log.info(
            "TickEngine %s tick #%d complete (tools=%d, response=%d chars)",
            self.agent_id,
            next_tick,
            len(tool_calls),
            len(response_text),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _collect_provider_state(self) -> dict[str, str]:
        """Run core data providers and refresh ``_last_skill_data`` + the risk
        tracker inputs. Returns per-provider summary strings.

        Called once before the model runs (prompt data) and again after a tick
        that made tool calls — the persisted snapshot must record the COMPLETED
        tick's state, not the pre-action view.
        """
        skill_results = await self.provider_registry.run_core_providers(
            self.config, agent_id=self.agent_id, agent_slug=self.agent.slug
        )

        native_result = skill_results.get("native_executors")
        nd = native_result.data if native_result else {}
        if nd and "error" not in nd:
            self._last_skill_data = {
                "executors": nd.get("executors", []),
                "total_exposure": nd.get("total_exposure", 0.0),
                # Native store tracks realized (closed) + unrealized (open) PnL
                # separately; the snapshot wants a single total.
                "total_pnl": (nd.get("realized_pnl") or 0.0)
                + (nd.get("unrealized_pnl") or 0.0),
                "total_volume": nd.get("total_volume", 0.0),
                "realized_pnl": nd.get("realized_pnl", 0.0),
                "unrealized_pnl": nd.get("unrealized_pnl", 0.0),
                "open_count": nd.get("open_count", 0),
                "closed_count": nd.get("closed_count", 0),
                "failed_count": nd.get("failed_count", 0),
            }

        return {name: result.summary for name, result in skill_results.items()}

    def _agent_key(self) -> str:
        """Resolve the model for this run: config override > Agent default."""
        return self.config.get("agent_key") or self.agent.agent_key

    async def _notify(self, message: str) -> None:
        """Notify the user: outbox (+ run stream mirror)."""
        from condor.notifications import notify

        self._emit("notification", {"text": message})
        try:
            await notify(
                message,
                agent_id=self.agent_id,
                kind="session",
            )
        except Exception:
            log.exception("Failed to send notification for %s", self.agent_id)

    def get_info(self) -> dict[str, Any]:
        """Return a summary dict for display."""
        sd = self._last_skill_data
        risk_limits = self.config.get("risk_limits", {})

        return {
            "agent_id": self.agent_id,
            "run_id": self.agent_id,
            "agent_slug": self.agent.slug,
            "agent_name": self.agent.name,
            "session_num": self.session_num,
            "status": self.status,
            "tick_count": self._tick_count,
            "daily_pnl": sd.get("total_pnl", 0.0),
            "total_volume": sd.get("total_volume", 0.0),
            "total_exposure": sd.get("total_exposure", 0.0),
            "open_executors": len(sd.get("executors", [])),
            "frequency_sec": self.config.get("frequency_sec", 60),
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
            "is_experiment": self.is_experiment,
        }
