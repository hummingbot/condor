"""TickEngine -- scheduler for autonomous trading agents.

One TickEngine instance per running agent session. It no longer owns an
execution stack (refactor-02): each tick is pre-flight → ``run_agent`` →
journal write-back. What lives here is the loop/pause/max_ticks logic, the
``_engines`` registry entry, directive injection, the risk pre-flight, the
post-run journal block, and the shutdown escalation hook.

Each tick:
1. Pre-compute core data providers (active executors)
2. Read journal (learnings + summary + recent decisions)
3. Risk pre-flight (soft block / hard kill-switch)
4. Build prompt with strategy + data + risk state
5. run_agent under a risk_gate policy (fresh client, clean context window)
6. Save full snapshot and update journal
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .agent import Agent
from .journal import (
    JournalManager,
    allocate_session_dir,
    finalize_session_meta,
    next_experiment_number,
    write_session_meta,
)
from .prompts import build_tick_prompt
from .providers import ProviderRegistry
from .risk import RiskEngine, RiskLimits, RiskState, risk_gate
from .run import run_agent
from .strategy import Strategy

log = logging.getLogger(__name__)

# Module-level registry of running engines
_engines: dict[str, "TickEngine"] = {}


class _NullTracker:
    """Stub tracker for experiments (no journal)."""

    def get_total_exposure(self) -> float:
        return 0.0

    def get_open_executor_count(self) -> int:
        return 0

    def get_drawdown_pct(self) -> float:
        return 0.0


def get_engine(agent_id: str) -> TickEngine | None:
    return _engines.get(agent_id)


def get_all_engines() -> dict[str, "TickEngine"]:
    return dict(_engines)


@dataclass
class TickEngine:
    agent: Agent  # owning Agent: identity + shared brain (memory/skills)
    strategy: Strategy  # the playbook this session loops (tactics + config)
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

    # Runtime state
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)
    _paused: bool = field(default=False, init=False)
    _shutting_down: bool = field(default=False, init=False)
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
        # All operational state hangs off the *agent* dir (refactor-01b); the
        # strategy is a start-time selector recorded as session metadata.
        agent_dir = self.agent.agent_dir

        # run_once is not a storage mode: it's an ordinary tick session capped
        # at one tick — journal, frozen config, risk pre-flight, attribution.
        if self.config.get("execution_mode") == "run_once":
            self.config["execution_mode"] = "loop"
            self.config["max_ticks"] = 1

        mode = self.config.get("execution_mode", "loop")
        self.is_experiment = mode == "experiment"

        # Tick sessions whose config declares no risk_limits fall back to the
        # agent-level baseline (AGENT.md `risk_limits:`), the same numbers that
        # govern this agent's delegations.
        if not self.config.get("risk_limits") and self.agent.risk_limits:
            self.config["risk_limits"] = dict(self.agent.risk_limits)

        # agent_id == controller_id tag: "{agent_slug}_{N}" (sessions) or
        # "{agent_slug}_e{N}" (experiments).
        if self.is_experiment:
            self.session_num = next_experiment_number(agent_dir)
            self.agent_id = f"{self.agent.slug}_e{self.session_num}"
            # Experiments: flat snapshot file, no session dir or journal
            self.session_dir = None
            self.journal = None
        else:
            self.session_num, self.session_dir = allocate_session_dir(agent_dir)
            self.agent_id = f"{self.agent.slug}_{self.session_num}"

            # Save config per session
            from .config import save_full_config

            save_full_config(self.session_dir, self.config)

            write_session_meta(
                self.session_dir,
                {
                    "strategy": self.strategy.slug,
                    "status": "running",
                    "model": self._agent_key(),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            self.journal = JournalManager(
                self.agent_id,
                strategy_name=self.strategy.name,
                strategy_description=self.strategy.description,
                session_dir=self.session_dir,
                agent_dir=agent_dir,
            )

        risk_limits = RiskLimits.from_dict(self.config.get("risk_limits", {}))
        self.risk = RiskEngine(risk_limits)
        self.provider_registry = ProviderRegistry()

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
        _engines[self.agent_id] = self
        log.info(
            "TickEngine %s started (freq=%ss)",
            self.agent_id,
            self.config.get("frequency_sec", 60),
        )

    def _finalize_meta(self, status: str) -> None:
        """Record the terminal status in the session's meta.yml."""
        if self.session_dir is None:
            return
        try:
            finalize_session_meta(self.session_dir, status)
        except Exception:
            log.exception("TickEngine %s: failed to finalize meta.yml", self.agent_id)

    async def stop(self) -> None:
        """Stop gracefully."""
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
        # Stop this session's native executors too — a stopped agent must not
        # leave executors polling Gateway. The agent config's
        # `keep_position_on_stop` decides fate: default True detaches (positions
        # stay in the wallet, unmanaged — stop is position-preserving; /shutdown
        # is the liquidating escalation); an explicit False swaps positions back
        # to the quote token. Either way the executor loops stop.
        from condor.executors.service import peek_executor_runtime

        runtime = peek_executor_runtime()
        if runtime is not None:
            keep_position = bool(self.config.get("keep_position_on_stop", True))
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
        if self.journal:
            self.journal.close()
        if _engines.pop(self.agent_id, None) is not None:
            self._finalize_meta("stopped")
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
            if self.journal:
                self.journal.close()
            _engines.pop(self.agent_id, None)
            self._finalize_meta("stopped")
            log.info("TickEngine %s shut down (%s)", self.agent_id, reason)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def inject_directive(self, text: str) -> None:
        """Queue a user directive to be included in the next tick's prompt."""
        self._pending_directives.append(text)
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
                    if self.journal:
                        self.journal.append_error(str(e))
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
                    return

                # max_ticks limit (covers run_once via max_ticks=1)
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
                    self._running = False
                    self.journal.close()
                    _engines.pop(self.agent_id, None)
                    self._finalize_meta("stopped")
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

        # 1. Get the hummingbot-api client IF one is configured — but treat it as
        #    OPTIONAL. Gateway-native agents (condor-native position/swap/lp
        #    executors) run in-process against Hummingbot Gateway and are read via
        #    the native_executors provider, which needs no hummingbot-api client.
        #    Only the hummingbot-api providers (executors/positions) use it, and
        #    run_core_providers isolates their failure. So a missing client must
        #    NOT abort the tick — that would strand a fully-working Gateway-only
        #    agent (no hummingbot-api required).
        client = await self._get_client()

        # 2. Run core data providers (executors only -- agent uses MCP for market data)
        native_active, core_data_summaries = await self._collect_provider_state(client)

        # 3. Read journal context (sessions only)
        learnings = self.journal.read_learnings() if self.journal else ""
        recent_decisions = (
            self.journal.get_recent_decisions(count=3) if self.journal else ""
        )
        summary = self.journal.read_summary() if self.journal else ""

        # 4. Get risk state (experiments pass None — returns clean state)
        risk_state = self.risk.get_state(self.journal or _NullTracker())

        # Hard kill-switch: escalate to an emergency winddown before the soft
        # pause below. Experiments never trade for real, so they never shut down.
        if risk_state.should_shutdown and not self.is_experiment:
            await self._run_shutdown(reason=risk_state.shutdown_reason)
            return

        if risk_state.is_blocked and not self.is_experiment:
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
        # Cache routine discovery on first tick — routines rarely change mid-session
        if self._cached_routines_section is None:
            from .prompts import _build_routines_section

            try:
                self._cached_routines_section = _build_routines_section(
                    self.agent.slug
                )
            except Exception:
                self._cached_routines_section = ""

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

        # 6. One run_agent call per tick (fresh client, clean context window).
        # risk_state was computed once above and threads into the gate so the
        # per-call checks accumulate against this tick's running totals.
        def _hold_client(c):
            self._active_client = c

        result = await run_agent(
            self.agent,
            prompt,
            permission_policy=risk_gate(self.risk, risk_state, experiment=self.is_experiment),
            user_id=self.user_id,
            chat_id=self.chat_id,
            server_name=self.config.get("server_name") or None,
            execution_mode=mode,
            model=self._agent_key(),
            model_base_url=self.config.get("model_base_url") or None,
            tool_filter_mode=self.config.get("tool_filter_mode") or None,
            timeout_s=300,
            on_client=_hold_client,
            agent_id=self.agent_id,
            strategy=self.strategy.slug,
        )

        # The attempt completed — acknowledge the directives it carried.
        # (Directives queued DURING the run stay pending for the next tick.)
        for d in directives:
            try:
                self._pending_directives.remove(d)
            except ValueError:
                pass

        response_text = result.text
        tool_calls = result.tool_calls
        tick_duration = time.time() - self._last_tick_at

        # Post-tick refresh: tool calls mutate executor state, so the snapshot
        # must record the COMPLETED tick (an order created this tick shows
        # open=1, not the pre-action open=0). Failure keeps the pre-run view.
        if tool_calls:
            try:
                native_active, core_data_summaries = (
                    await self._collect_provider_state(client)
                )
            except Exception:
                log.exception(
                    "TickEngine %s: post-tick provider refresh failed", self.agent_id
                )

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        executors_summary = core_data_summaries.get(
            "native_executors" if native_active else "executors",
            "No executor data.",
        )

        if self.is_experiment:
            # Experiments: save a single snapshot file, no journal
            from .journal import save_experiment_snapshot

            save_experiment_snapshot(
                agent_dir=self.agent.agent_dir,
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
            # Sessions: full journal tracking
            tick_num = self.journal.record_tick(
                response_summary=response_text[:500],
                actions=len(tool_calls),
            )

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

            log.info(
                "TickEngine %s tick #%d complete (tools=%d, response=%d chars)",
                self.agent_id,
                tick_num,
                len(tool_calls),
                len(response_text),
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _collect_provider_state(self, client) -> tuple[bool, dict[str, str]]:
        """Run core data providers and refresh ``_last_skill_data`` + journal
        executor sync. Returns (native_active, per-provider summary strings).

        Called once before the model runs (prompt data) and again after a tick
        that made tool calls — the persisted snapshot must record the COMPLETED
        tick's state, not the pre-action view.
        """
        skill_results = await self.provider_registry.run_core_providers(
            client, self.config, agent_id=self.agent_id
        )

        # Extract structured data from providers for tracking. Prefer the
        # native_executors provider whenever it carries data: a Gateway-native
        # agent's real positions/PnL live there, while the hummingbot-api
        # `executors` provider is empty (no API client) — so reading it would
        # record pnl=0/volume=0/open=0 snapshots for an agent that is actually
        # trading. Fall back to the hummingbot-api provider for API-backed agents.
        native_result = skill_results.get("native_executors")
        executors_result = skill_results.get("executors")
        nd = native_result.data if native_result else {}
        native_active = bool(nd) and "error" not in nd and (
            nd.get("executors")
            or nd.get("open_count")
            or nd.get("closed_count")
            or nd.get("failed_count")
        )
        if native_active:
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
        elif executors_result:
            self._last_skill_data = executors_result.data
        # Keep the journal's Executors section (and thus risk exposure/count) in
        # sync with live native positions — nothing else populates it on the
        # Gateway-native path.
        if native_active and self.journal:
            try:
                self.journal.sync_open_executors(nd.get("executors", []))
            except Exception:
                log.exception("sync_open_executors failed for %s", self.agent_id)
        positions_result = skill_results.get("positions")
        if positions_result:
            self._last_skill_data["positions"] = positions_result.data

        core_data_summaries: dict[str, str] = {
            name: result.summary for name, result in skill_results.items()
        }
        return native_active, core_data_summaries

    def _agent_key(self) -> str:
        """Resolve the model for this run: config override > strategy override > Agent."""
        return (
            self.config.get("agent_key")
            or self.strategy.agent_key
            or self.agent.agent_key
        )

    def _resolve_server(self) -> tuple[str | None, dict | None]:
        """Resolve the server for this agent."""
        from config_manager import get_config_manager, get_effective_server

        cm = get_config_manager()
        server_name = self.config.get("server_name")

        if not server_name:
            server_name = get_effective_server(self.chat_id)
        if not server_name:
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
                from handlers.bots._shared import get_bots_client

                client, _ = await get_bots_client(self.chat_id)
                return client

            from config_manager import get_config_manager

            cm = get_config_manager()
            return await cm.get_client(server_name)
        except Exception:
            log.exception("Failed to get API client for agent %s", self.agent_id)
            return None

    async def _notify(self, message: str) -> None:
        """Notify the user: outbox + Telegram mirror (condor.notifications)."""
        from condor.notifications import notify

        try:
            await notify(
                message,
                user_id=self.user_id,
                chat_id=self.chat_id if isinstance(self.chat_id, int) else 0,
                agent_id=self.agent_id,
                kind="session",
                bot=getattr(self, "_bot", None),
            )
        except Exception:
            log.exception("Failed to send notification for %s", self.agent_id)

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
            "total_volume": sd.get("total_volume", summary.get("total_volume", 0)),
            "total_exposure": sd.get("total_exposure", summary["total_exposure"]),
            "open_executors": len(sd.get("executors", [])) or summary["open_executors"],
            "frequency_sec": self.config.get("frequency_sec", 60),
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
