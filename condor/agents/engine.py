"""TickEngine -- main orchestrator for autonomous trading agents.

One TickEngine instance per running agent.  _tick runs three phases:
1. Gather (_gather_tick_context): adopt running bots, run core data
   providers, compute risk state (may end the tick: shutdown or block), read
   the journal and build the prompt
2. Run model (_run_model): spawn a fresh ACP session, stream events under
   the tick timeout, capture tool calls, always reap the client
3. Persist (_persist_tick): experiment snapshot, or journal + full
   snapshot + action log + session report for a session
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

from . import actions as actions_mod
from .agent import Agent
from .agent_run import FAILED_STOP_REASONS
from .config import is_experiment_mode
from .journal import JournalManager, next_experiment_number, next_session_number
from .prompts import build_tick_prompt
from .providers import ProviderRegistry
from .risk import (
    RefusalLog,
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)
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

    def get_drawdown_pct(self) -> float:
        return 0.0


def _turn_failure(stop_reason: str, response_text: str) -> str:
    """The error a failed model turn raises, or "" when the turn answered.

    Only ``FAILED_STOP_REASONS`` count: a ``cancelled`` turn is the engine's own
    stop, not a failure. On ``error`` the first streamed line is kept, since
    that is where the pydantic client puts the provider's message.
    """
    if stop_reason not in FAILED_STOP_REASONS:
        return ""
    failure = f"agent session ended: {stop_reason}"
    first_line = next(
        (line.strip() for line in response_text.splitlines() if line.strip()), ""
    )
    if stop_reason == "error" and first_line:
        failure += f" — {first_line}"
    return failure


@dataclass(frozen=True)
class _TickContext:
    """What the gather phase hands the model and persist phases of one tick."""

    risk_state: RiskState
    core_data_summaries: dict[str, str]
    prompt: str

    @property
    def executors_summary(self) -> str:
        return self.core_data_summaries.get("executors", "No executor data.")


def _supervisor():
    from condor.runtime.loops import get_supervisor

    return get_supervisor()


def resolve_agent_key(config: dict[str, Any], strategy, agent) -> str:
    """The model a run uses: run config override > strategy override > Agent.

    Module-level so the start route can check the very key the engine will run
    before building one — building an engine allocates a session on disk.
    """
    return config.get("agent_key") or strategy.agent_key or agent.agent_key


# How long a caller that just started a run waits for its first tick before it
# answers (CORR-728). Short on purpose: a tick can honestly take minutes, so
# what this bound has to tell apart is "the run came up" from "it died on the
# way up", and dying is immediate. A tick still in flight at the bound counts
# as came-up — see ``TickEngine.wait_first_tick``.
FIRST_TICK_WAIT_SEC = 20.0


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
    # Whether ``config["server_name"]`` was *chosen* for this run rather than
    # inherited from the strategy's config: the start route's ``asked_for_it``,
    # a restart's carried pin. A chosen name must resolve or the run refuses
    # (CORR-729); an inherited one still falls through to the owner's own
    # servers, which is what ``_start`` and ``_check_restartable`` allow.
    server_pinned: bool = False

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
    # Set when an emergency winddown's own teardown has run, so a stop() that
    # arrives mid-winddown waits for it instead of cancelling it.
    _shutdown_finished: asyncio.Event | None = field(
        default=None, init=False, repr=False
    )
    _last_tick_at: float = field(default=0.0, init=False)
    # How long the last completed tick took, in seconds ([[FEAT-141]]). The
    # journal has always recorded it per tick; keeping the newest one here is
    # what lets the fleet view read it without opening a journal, and a tick
    # that is slow because its host is oversubscribed is the symptom the race
    # misread as a strategy bug. 0 = no tick has completed yet.
    _last_tick_duration: float = field(default=0.0, init=False)
    # ── Liveness, as outcomes rather than attempts ([[FEAT-137]]) ──
    # ``_last_tick_at`` above is set at tick *start*, so it advances even when
    # every call in the tick failed: a session whose model is broken looks
    # exactly as fresh as a healthy one, which is GitHub #272's failure mode.
    # These two are stamped only where the outcome is known -- a reading that
    # said something, and a mutating call that worked -- and are persisted
    # through ``LoopSupervisor.record_tick`` so they survive the process.
    _last_reading_at: float = field(default=0.0, init=False)
    _last_successful_action_at: float = field(default=0.0, init=False)
    _last_error: str = field(default="", init=False)
    # The first tick's two moments, so a caller that started this run can report
    # what the run did instead of that a task was created (CORR-728).
    # ``_first_tick_arrived`` is set as the tick is entered; ``_first_tick_done``
    # once it has returned, with ``_first_tick_error`` saying how. A loop that
    # ends before ticking sets both on its way out, so no waiter hangs.
    _first_tick_arrived: asyncio.Event = field(
        default_factory=asyncio.Event, init=False, repr=False
    )
    _first_tick_done: asyncio.Event = field(
        default_factory=asyncio.Event, init=False, repr=False
    )
    _first_tick_error: str = field(default="", init=False, repr=False)
    # Set once an experiment tick has written its own dry-run file.
    _experiment_written: bool = field(default=False, init=False)
    # The block the owner was last told about, so a block lasting many ticks is
    # announced once instead of on every one of them.
    _last_block_reason: str = field(default="", init=False, repr=False)
    _last_skill_data: dict[str, Any] = field(default_factory=dict, init=False)
    # The recorded-deploy bases the last adoption pass was matched against, so a
    # deploy written to the action log afterwards re-opens adoption for one more
    # pass. Whether adoption is *settled* is read off the ledger itself, by the
    # ``_adoption_done`` property — never latched by a pass having run (CORR-724).
    _adoption_deploys: set[str] = field(default_factory=set, init=False, repr=False)
    # Executor rows a dead session of this same strategy left RUNNING (CORR-726).
    # Merged into ``_executor_owners`` so the stop gate's existing membership
    # test lets this session close them, and shown in the prompt so it knows
    # their ids at all. Re-derived every tick, so one whose owner came back is
    # dropped again.
    _adopted_executors: list[dict[str, Any]] = field(
        default_factory=list, init=False, repr=False
    )
    # Ids among ``_adopted_executors`` nothing could put a quote size on —
    # either a pending market order, whose size ``build_executor_row`` leaves at
    # 0 because it is pure, or an executor whose own sizing field is absent (an
    # lp with no ``total_value_quote``), which no price lookup can rescue
    # (CORR-740). Unioned into the tick's unpriced set (CORR-735) so an
    # inherited row of unknown size refuses creates instead of reading as $0.
    _adopted_unpriced: list[str] = field(default_factory=list, init=False, repr=False)
    _mode_mismatch_noted: bool = field(default=False, init=False, repr=False)
    # What the risk gate refused, and why. The gate appends during the tick
    # (condor.agents.risk.RefusalLog); the tick drains it into the journal and
    # into `_last_refusals`, which the NEXT tick's prompt shows the agent — the
    # only channel that reaches it, since a permission response carries no
    # reason. Without it a refused call reads to the agent as an approval that
    # never came, and an unattended agent then waits for a human who is not there.
    _refusals: "RefusalLog" = field(default_factory=RefusalLog, init=False, repr=False)
    _last_refusals: list[dict[str, Any]] = field(
        default_factory=list, init=False, repr=False
    )
    # Why the run ended ("user" until _finish() records the real reason).
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
    # What the adapter said it is on, read off the last started client
    # ([[FEAT-136]]). The requested pin is ``agent_key``'s suffix; this is the
    # answer, which is what makes it an audit rather than an echo: a bridge that
    # could not match the pin keeps its own default and says so in
    # ``active_model_id``, and before this the only record of that was a log
    # line. Empty for a backend with no adapter to ask (pydantic-ai, where the
    # key *is* the model) and until the first tick has started a client.
    _active_model_id: str = field(default="", init=False, repr=False)
    # Set by _finish(): the run's teardown has happened, so any later exit path
    # (a stop() after the loop self-completed) is a no-op.
    _finished: bool = field(default=False, init=False, repr=False)
    # What the shutdown policy's order cancel achieved (CORR-727). None until a
    # sweep has run; a winddown sets it before its cleanup pass, which is how
    # _finish knows not to sweep the same orders a second time on the way out.
    _cancel_outcome: "CancelOutcome | None" = field(
        default=None, init=False, repr=False
    )
    # The close-out report this run's teardown recorded (FEAT-134). None until
    # _finish has run, which is also how a caller tells a run that has not
    # stopped yet from one whose report could not be built -- the latter is a
    # report carrying an ``error``, never an absent one.
    _closeout: "CloseoutReport | None" = field(default=None, init=False, repr=False)

    def __post_init__(self):
        # The journal/sessions/learnings hang off the *strategy* dir (one level
        # below the Agent), so each playbook keeps its own operational history
        # while the Agent's brain (memory/skills) stays shared at the parent.
        strategy_dir = self.strategy.home
        mode = self.config.get("execution_mode", "loop")
        self.is_experiment = is_experiment_mode(mode)

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

    async def _preflight_gate(self) -> None:
        """Refuse the start when a stated trading requirement is not met (FEAT-139).

        Here rather than in the start route so that every path into a live loop
        goes through it — the dashboard start, and the boot restart that has no
        request to answer. Raises
        :class:`~condor.preflight.PreflightFailed`, which the route turns into a
        422 naming the reason and the boot pass records as a failed restart.

        Two deliberate narrowings keep a working loop safe from a false
        negative, which is the one way this check could cost more than it saves:

        * a config that states no ``trading_requirements`` is not gated at all,
          and no API read is made for it — the preflight has nothing to check
          against, so there is nothing it could wrongly refuse;
        * an experiment (``dry_run``/``run_once``) commits no capital under the
          risk gate's refusals, so it is reported on by ``doctor`` but never
          stopped by this.
        """
        # Imported at call time, both ways round: ``condor.preflight`` resolves
        # this run's model through ``resolve_agent_key`` here, so neither module
        # may reach the other at import time.
        from condor.preflight import PreflightFailed, gate_start, requirements_of

        if is_experiment_mode(str(self.config.get("execution_mode") or "")):
            return
        if not requirements_of(self.config).declared:
            return

        try:
            report = await gate_start(
                await self._get_client(),
                self.config,
                label=self.agent_id,
                strategy=self.strategy,
                agent=self.agent,
                user_id=self.user_id,
                server_pinned=self.server_pinned,
            )
        except PreflightFailed as failed:
            # The session folder exists from construction, so its state is
            # recorded rather than left blank: a start that was refused must not
            # read on disk as a run still coming up. Not ``_finish`` — there is
            # nothing to wind down, and a close-out report for a loop that never
            # ticked would be a report of nothing.
            log.warning(
                "TickEngine %s refused by preflight:\n%s",
                self.agent_id,
                failed.report.render(),
            )
            _supervisor().record(self, LoopState.ERROR)
            raise
        for line in report.render().splitlines():
            log.info("TickEngine %s preflight: %s", self.agent_id, line.strip())

    async def start(self, bot=None) -> None:
        """Start the tick loop as an asyncio task.

        The trading preflight runs first: a loop whose stated capital, account
        mode or venue rules do not hold never reaches a tick (FEAT-139).
        """
        if self._running:
            return
        await self._preflight_gate()
        self._running = True
        self._bot = bot
        self._task = asyncio.create_task(self._loop())
        _supervisor().register(self)
        log.info(
            "TickEngine %s started (freq=%ss)",
            self.agent_id,
            self.config.get("frequency_sec", 60),
        )

    async def stop(self) -> bool:
        """Stop gracefully (positions are kept).

        Returns True when this call performed the stop. When an emergency
        winddown is already in flight (risk kill-switch inside the tick task, or
        a manual /shutdown from a request task) it is neither cancelled nor torn
        down here: stop() waits until the winddown's own teardown has recorded
        STOPPED and returns False. Cancelling the tick task at that point would
        abort run_shutdown mid-flight and leave positions stranded.
        """
        if self._shutting_down:
            finished = getattr(self, "_shutdown_finished", None)
            if finished is not None:
                await finished.wait()
            return False
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._finish(LoopState.STOPPED, "user")
        return True

    async def _reap_client(self, *, during: str = "") -> None:
        """Stop the live per-tick ACP client, if any.

        Backstop for a tick cancelled mid-await, whose own finally may not have
        reaped the subprocess. client.stop() is idempotent, so reaping after a
        clean tick (which already cleared ``_active_client``) is a no-op.
        """
        client = self._active_client
        if client is None:
            return
        try:
            await client.stop()
        except Exception:
            log.exception(
                "TickEngine %s: error reaping active client%s",
                self.agent_id,
                f" during {during}" if during else "",
            )
        self._active_client = None

    async def _cancel_resting_orders(self) -> None:
        """Execute ``cancel_open_orders`` for this run's end. Never raises.

        Lives in :meth:`_finish` because that is the one teardown every exit path
        goes through -- a graceful stop, a completed run, an error and a winddown
        all end here. A winddown has already swept before its cleanup pass and
        left its :class:`~condor.agents.shutdown.CancelOutcome` on the engine, so
        this sees it and does not sweep twice.

        A ``dry_run`` is skipped: the risk gate refuses every mutating call in
        that mode, so the session cannot have placed an order to cancel.

        ``getattr`` throughout, and a blanket ``except``: ``_finish`` is driven in
        tests by ``SimpleNamespace`` stand-ins that carry neither a strategy nor a
        client, and a teardown must finish either way.
        """
        if getattr(self, "_cancel_outcome", None) is not None:
            return
        from .config import is_experiment_mode
        from .shutdown import cancel_resting_orders, load_shutdown_policy

        try:
            config = getattr(self, "config", None) or {}
            if is_experiment_mode(str(config.get("execution_mode") or "")):
                return
            strategy = getattr(self, "strategy", None)
            if strategy is None:
                return
            policy, _ = load_shutdown_policy(strategy)
            if not policy.cancel_open_orders:
                return
            client = await self._get_client()
            if client is None:
                log.warning(
                    "TickEngine %s: could not reach the API to cancel resting "
                    "orders; any open order is STILL RESTING",
                    self.agent_id,
                )
                return
            # ``all_executors``, not just the running ones: an executor that
            # already terminated can be exactly the one that left an order
            # resting, and its market has to stay in scope.
            skill_data = self._last_skill_data or {}
            executors = list(skill_data.get("all_executors") or []) or list(
                skill_data.get("executors") or []
            )
            # And the markets of anything inherited from a dead session
            # (CORR-726): an adopted executor is not under this session's tag,
            # so the provider rows above do not carry its market and its
            # resting orders would be out of scope.
            executors += [
                row
                for row in (getattr(self, "_adopted_executors", None) or [])
                if isinstance(row, dict)
            ]
            outcome = await cancel_resting_orders(self, client, policy, executors)
            self._cancel_outcome = outcome
            if self.journal:
                self.journal.append_action(
                    self.journal.tick_count + 1,
                    "cancel_open_orders",
                    outcome.describe(),
                )
            if (
                outcome.failed
                or outcome.unattributed
                or outcome.unsupported
                or outcome.unknown
            ):
                log.warning("TickEngine %s: %s", self.agent_id, outcome.describe())
        except Exception:
            log.exception(
                "TickEngine %s: could not cancel resting orders on stop",
                getattr(self, "agent_id", "?"),
            )

    async def _emit_closeout(self) -> None:
        """Record what this run closed and what it left (FEAT-134). Never raises.

        A thin delegation: :func:`condor.agents.closeout.emit_closeout` owns the
        reads, the timeout and the catch, so the teardown holds no policy of its
        own. Imported lazily, like the sweep above it, to keep the venue-read
        module off the engine's import path.
        """
        from .closeout import emit_closeout

        await emit_closeout(self)

    async def _finish(self, final_state: str, reason: str) -> None:
        """End this run: the ONE teardown every exit path goes through.

        ``final_state`` is the LoopState recorded on disk; ``reason`` is the
        ``stopped_by`` of the strategy_run telemetry event ("user", "shutdown",
        "error", "complete", "max_ticks"). Runs at most once per engine, so a
        stop() after the loop already ended itself changes nothing.

        ``getattr`` on ``_finished``/``config``: tests drive the lifecycle
        methods with SimpleNamespace stand-ins that carry neither.
        """
        if getattr(self, "_finished", False):
            return
        self._finished = True
        self._last_stop_reason = reason
        self._running = False
        try:
            await self._reap_client()
            # The shutdown policy's order cancel, on EVERY exit path. A graceful
            # stop keeps positions but must not leave resting orders behind, and
            # until CORR-727 the flag only ever reached the LLM context as a
            # sentence -- so a stop's claim rested on whether a model acted on it.
            # ``getattr`` for the same reason the ones above it are: a stand-in
            # driven through the lifecycle methods carries only what it binds.
            sweep = getattr(self, "_cancel_resting_orders", None)
            if sweep is not None:
                await sweep()
            # The close-out report (FEAT-134), last and best-effort: the stop's
            # own work is done by here, so a report that fails or hangs can
            # neither fail nor delay it -- emit_closeout catches everything and
            # caps itself. Here and not in run_shutdown/stop(), because this is
            # the one teardown every exit path reaches, so one call site covers
            # the graceful stop, the emergency winddown, an error, and the stop
            # half of a restart. Before the ``finally`` below, which closes the
            # journal the report is recorded in. ``getattr`` for the same reason
            # the sweep above uses it.
            emit = getattr(self, "_emit_closeout", None)
            if emit is not None:
                await emit()
        finally:
            # Close the ownership window before the journal: from here on this
            # session operates nothing, so a bot left running must stop accruing
            # to it. The next session adopts the bot on its first tick and picks
            # the timeline up from there; the gap in between belongs to no
            # session, which is the truth.
            if self.ledger is not None:
                self.ledger.release()
            # Shape of the session for telemetry (FEAT-023): mode, cadence and
            # tick count. Never the playbook, the journal, the pairs or the
            # positions.
            telemetry_taps.strategy_run(
                getattr(self, "config", None),
                ticks=getattr(self.journal, "tick_count", 0) or 0,
                stopped_by=reason,
            )
            if self.journal:
                self.journal.close()
            _supervisor().unregister(self.agent_id, final_state)
            log.info("TickEngine %s ended: %s (%s)", self.agent_id, final_state, reason)

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
        self._shutdown_finished = asyncio.Event()
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
        # The client must be dead BEFORE the winddown runs, not after it.
        await self._reap_client(during="shutdown")

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
            # run_shutdown() may have wound the bot down or failed; either way
            # the run ends here.
            log.info("TickEngine %s shut down (%s)", self.agent_id, reason)
            try:
                await self._finish(LoopState.STOPPED, "shutdown")
            finally:
                # Release any stop() that arrived mid-winddown and is waiting.
                self._shutdown_finished.set()

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
        try:
            await self._run_ticks(freq, mode)
        finally:
            # A run that ended before any tick still has to release whoever is
            # waiting on its first one — with the truth, not with silence.
            self._note_first_tick(
                self._last_error or "the run ended before its first tick"
            )

    def _note_first_tick(self, error: str) -> None:
        """Record how the first tick went, once. Later ticks change nothing."""
        if self._first_tick_done.is_set():
            return
        self._first_tick_error = error
        self._first_tick_done.set()

    async def wait_first_tick(self, timeout: float) -> str:
        """Wait for this run's first tick. ``""`` when the run is answerable.

        A start that reports success on ``create_task`` alone says "running"
        for a run that died on its first tick where nobody was watching
        (CORR-728). Waiting here is what makes the answer a fact.

        Returns ``""`` when the first tick completed cleanly, or is still in
        flight at the bound — a tick that arrived and is thinking is a run that
        came back, and a tick can honestly take minutes, so the bound must not
        hold a request for one. Otherwise a reason fit to show a human: the
        first tick raised, or the run ended (or never started one) before it.
        """
        try:
            # ``asyncio.timeout``, the same shape as the tick budget in
            # ``_run_model`` — and the one timeout idiom this file uses
            # (READ-672).
            async with asyncio.timeout(timeout):
                await self._first_tick_done.wait()
        except TimeoutError:
            if self._first_tick_arrived.is_set():
                return ""
            return f"no tick had started {timeout:.0f}s after the run was created"
        return self._first_tick_error

    async def _run_ticks(self, freq: int, mode: str) -> None:
        """The tick loop itself; ``_loop`` wraps it to settle the first tick."""
        while self._running:
            if not self._paused:
                tick_error = ""
                self._first_tick_arrived.set()
                try:
                    await self._tick()
                    self._last_error = ""
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    tick_error = str(e) or type(e).__name__
                    # A loop with a broken model or server fails every tick the
                    # same way: tell the owner when the error starts or changes,
                    # not once a tick. A single-tick run reports below instead.
                    repeated = tick_error == self._last_error
                    self._last_error = tick_error
                    log.exception("TickEngine %s tick error", self.agent_id)
                    if self.journal:
                        self.journal.append_error(tick_error)
                    if self.is_experiment:
                        self._record_failed_experiment(tick_error)
                    elif not repeated:
                        await self._notify(
                            f"Agent {self.agent_id} tick error: {tick_error}"
                        )

                self._note_first_tick(tick_error)

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
                if self.is_experiment:
                    label = "Dry run" if mode == "dry_run" else "Run-once"
                    if tick_error:
                        # A tick that raised is a failed run, not a completed one.
                        log.info(
                            "TickEngine %s: %s failed, self-stopping",
                            self.agent_id,
                            label,
                        )
                        await self._notify(
                            f"Agent {self.agent_id}: {label} failed: {tick_error}"
                        )
                        await self._finish(LoopState.ERROR, "error")
                        return
                    log.info(
                        "TickEngine %s: %s complete, self-stopping",
                        self.agent_id,
                        label,
                    )
                    await self._notify(f"Agent {self.agent_id}: {label} complete.")
                    await self._finish(LoopState.COMPLETED, "complete")
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
                    await self._finish(LoopState.COMPLETED, "max_ticks")
                    return

            try:
                await asyncio.sleep(freq)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        """One tick, in three phases: gather, run the model, persist.

        Each phase is its own method so it can be read and tested on its own.
        The tick ends early only when the gather phase returns ``None`` (a hard
        shutdown or a risk block); past that, the model always runs and its
        outcome is always persisted.

        No API client is a *failure*, not an early return: a trading loop with
        nothing to trade through can do nothing at all, and journalling it and
        returning cleanly reported the run as ticking (CORR-729). Raising hands
        the reason to ``_run_ticks``, which journals it, tells the owner once per
        distinct error, and — on the first tick — makes the start or restart that
        created this run report the failure instead of "started".
        """
        self._last_tick_at = time.time()

        client = await self._get_client()
        if not client:
            raise RuntimeError("No API client available")

        ctx = await self._gather_tick_context(client)
        if ctx is None:
            return

        response_text, tool_calls, stop_reason = await self._run_model(
            ctx.prompt, ctx.risk_state, client
        )
        failure = _turn_failure(stop_reason, response_text)
        # A failed turn may already have run tool calls (a deploy), so it is
        # persisted like any other tick first; raising afterwards lets ``_loop``
        # journal the error, tell the owner and end a dry run as failed.
        self._last_tick_duration = time.time() - self._last_tick_at
        await self._persist_tick(
            ctx,
            response_text,
            tool_calls,
            self._last_tick_duration,
            failure=failure,
        )
        if failure:
            raise RuntimeError(failure)

    # ------------------------------------------------------------------
    # Tick phase 1: gather
    # ------------------------------------------------------------------

    async def _gather_tick_context(self, client) -> _TickContext | None:
        """Collect everything the model sees this tick and build its prompt.

        Adopts running bots, runs the core data providers, reads the journal,
        computes the risk state, and builds the prompt. Returns ``None`` when
        the tick must end here: the hard kill-switch escalated to a shutdown,
        or the risk gate blocked the tick (journalled, and announced once per
        reason).
        """
        # Adopt any bot of ours already running (first tick only). A crash
        # restart always mints a NEW session (see condor/runtime/loops.py), so the
        # live bot must be taken over rather than orphaned and redeployed.
        await self._adopt_running_bots(client)

        # And the executors a dead session of this same strategy left running
        # (CORR-726). Every tick, not just the first: a sibling session can end
        # at any point in this one's life, and a graceful stop leaves its
        # executors behind by design.
        await self._adopt_running_executors(client)

        # Run core data providers (executors only -- agent uses MCP for market data)
        skill_results = await self.provider_registry.run_core_providers(
            client,
            self.config,
            agent_id=self.agent_id,
            # Adoption above just refreshed the ledger, so its bases are exactly
            # the bots this session operates right now — including any extra one
            # it deployed beyond the configured name.
            bot_names=self.ledger.bases() if self.ledger else None,
            # The ledger's records, unflattened: each base is sliced to its own
            # takeover, so bot PnL earned before it was inherited is not reported
            # back to the agent as its own.
            owned=self.ledger.owned() if self.ledger else None,
        )

        # Extract structured data from providers for tracking
        executors_result = skill_results.get("executors")
        if executors_result:
            self._last_skill_data = executors_result.data
        positions_result = skill_results.get("positions")
        if positions_result:
            self._last_skill_data["positions"] = positions_result.data
        drift_result = skill_results.get("drift")
        if drift_result:
            self._last_skill_data["drift"] = drift_result.data

        # This tick obtained a reading ([[FEAT-137]]). ``ok`` only: ``empty`` is
        # a controller that answered and said nothing and ``missing`` is no
        # performance object at all ([[CORR-725]]), and a key that is absent
        # altogether is the provider's own error path. All three leave the stamp
        # where it was, which is what lets a reader tell a market maker that is
        # merely quiet from one whose strategy died.
        if str(self._last_skill_data.get("reading") or "") == "ok":
            self._last_reading_at = time.time()

        # Convert provider results to summary strings
        core_data_summaries: dict[str, str] = {
            name: result.summary for name, result in skill_results.items()
        }
        adopted_summary = self._adopted_executors_summary()
        if adopted_summary:
            core_data_summaries["adopted_executors"] = adopted_summary

        # Risk state (experiments pass None — returns clean state)
        risk_state = self.risk.get_state(self.journal or _NullTracker())
        live_executors = self._last_skill_data.get("executors", [])
        live_open_count = len(live_executors) if isinstance(live_executors, list) else 0
        # Inherited positions count against the limits (CORR-726). The provider
        # reads only this session's own ``controller_id``, so an adopted orphan
        # is exposure this session is responsible for -- it is in the prompt as
        # "YOURS to monitor and close" and in the stop allowlist -- that the
        # book could not see. Left out, a restarted session holding three
        # inherited executors could open its full ``max_open_executors`` on top
        # of them and never be refused.
        adopted = [
            row
            for row in (getattr(self, "_adopted_executors", None) or [])
            if isinstance(row, dict)
        ]
        risk_state.executor_count = live_open_count + len(adopted)
        # ``amount`` is the quote exposure ``build_executor_row`` computes, the
        # same field the provider sums for its own rows, so the two halves of
        # the total are in one unit.
        risk_state.total_exposure = float(
            self._last_skill_data.get("total_exposure", 0.0) or 0.0
        ) + sum(float(row.get("amount") or 0.0) for row in adopted)
        # A pending order nothing could price is exposure of unknown size, not
        # zero: the book cannot size a create, so it refuses them like any
        # other missing metric while every brake still passes.
        # The adopted rows carry their own unvalued ids: they are priced during
        # adoption, not by the provider, so the two halves of the book have two
        # halves of this list too (CORR-735).
        unpriced = list(
            dict.fromkeys(
                [
                    str(eid)
                    for eid in (self._last_skill_data.get("unpriced_exposure") or [])
                ]
                + [str(eid) for eid in (getattr(self, "_adopted_unpriced", None) or [])]
            )
        )
        if unpriced:
            risk_state.book_trusted = False
            risk_state.drift_reason = (
                f"no size for executor(s) {', '.join(unpriced)}, "
                "so their exposure is unknown"
            )
        self._apply_drift_verdict(risk_state, drift_result)

        # Hard kill-switch: escalate to an emergency winddown before the soft
        # pause below. Experiments never trade for real, so they never shut down.
        if risk_state.should_shutdown and not self.is_experiment:
            await self._run_shutdown(reason=risk_state.shutdown_reason)
            return None

        if risk_state.is_blocked and not self.is_experiment:
            with self.journal.batch():
                self.journal.append_action(
                    self.journal.tick_count + 1,
                    "tick_blocked",
                    risk_state.block_reason,
                )
                self.journal.record_tick("blocked: " + risk_state.block_reason)
            if risk_state.block_reason != self._last_block_reason:
                await self._notify(
                    f"Agent {self.agent_id} blocked: {risk_state.block_reason}"
                )
            self._last_block_reason = risk_state.block_reason
            return None
        self._last_block_reason = ""

        prompt = self._build_prompt(core_data_summaries, risk_state, live_open_count)
        return _TickContext(
            risk_state=risk_state,
            core_data_summaries=core_data_summaries,
            prompt=prompt,
        )

    def _build_prompt(
        self,
        core_data_summaries: dict[str, str],
        risk_state: RiskState,
        live_open_count: int,
    ) -> str:
        """Read the journal and the per-tick context, and render the tick prompt.

        Server credentials are injected via env into the MCP process, never the
        prompt. Every read here is fresh each tick; a failing optional read
        (routines, memory, canvas, loop state) degrades to empty, never fails
        the tick.
        """
        # Journal context (sessions only)
        learnings = self.journal.read_learnings() if self.journal else ""
        recent_decisions = (
            self.journal.get_recent_decisions(count=3) if self.journal else ""
        )
        summary = self.journal.read_summary() if self.journal else ""

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
            # *Agent* slug — shared across all its strategies and runs, not by
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

        return build_tick_prompt(
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
            refusals=self._last_refusals,
        )

    # ------------------------------------------------------------------
    # Tick phase 2: run the model
    # ------------------------------------------------------------------

    async def _run_model(
        self, prompt: str, risk_state: RiskState, client
    ) -> tuple[str, list[dict[str, Any]], str]:
        """Run one agent session over ``prompt``; return (response text, tool
        calls, the terminal ``PromptDone`` stop reason).

        A fresh client per tick (clean context window), streamed under the
        tick's wall-clock budget. A timeout is not an error: the partial
        response gets "(timed out)" appended. The client is always stopped and
        ``_active_client`` cleared, whatever happens. Neither client raises when
        the turn fails; it ends on a ``PromptDone`` whose reason says so, which
        ``_tick`` checks against ``FAILED_STOP_REASONS``.
        """
        acp_client = await self._create_client(risk_state, client)
        self._active_client = acp_client

        response_chunks: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_call_map: dict[str, dict[str, Any]] = {}
        stop_reason = "end_turn"

        await acp_client.start()
        # The handshake has just resolved the model, so this is where the
        # adapter's own answer is readable ([[FEAT-136]]). Kept on the engine
        # rather than read off the client, which is torn down at the end of
        # every tick — the status that reports it is served between ticks.
        self._active_model_id = getattr(acp_client, "active_model_id", "") or ""
        # Wall-clock budget for this tick's agent session. Comes from the shared
        # policy (10 min default, CONDOR_TIMEOUT_TICK_DEFAULT) unless the run
        # config sets ``tick_timeout_sec`` -- a slower model or a tick that does
        # real research needs more room than a quoting loop does.
        tick_timeout = resolve_tick_timeout(
            execution_mode=self.config.get("execution_mode", "loop"),
            strategy=self.config.get("tick_timeout_sec"),
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
                    elif isinstance(event, PromptDone):
                        stop_reason = event.stop_reason
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

        return "".join(response_chunks), tool_calls, stop_reason

    # ------------------------------------------------------------------
    # Tick phase 3: persist
    # ------------------------------------------------------------------

    async def _persist_tick(
        self,
        ctx: _TickContext,
        response_text: str,
        tool_calls: list[dict[str, Any]],
        tick_duration: float,
        failure: str = "",
    ) -> None:
        """Record what the tick did: a snapshot file for an experiment, the
        journal, snapshot, action log and session report for a session.

        ``failure`` is set when the model turn itself failed; the tick is still
        recorded in full, marked as failed.
        """
        # What the venue refused this tick, counted in the tool path by the MCP
        # subprocess, folded in here so it joins the gate's own refusals in one
        # surface (FEAT-138). Before the drain, so this tick's 429s reach the
        # journal and the next tick's prompt instead of lagging a tick behind.
        self._ingest_throttle_refusals()

        # What the gate refused this tick, taken before either branch below so a
        # dry run and a live tick both carry it. It reaches the agent through the
        # next tick's prompt; the journal entry below is for the human reading
        # the session afterwards.
        self._last_refusals = self._refusals.drain()

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if self.is_experiment:
            self._persist_experiment(
                ctx, response_text, tool_calls, tick_duration, timestamp, failure
            )
        else:
            await self._persist_session(
                ctx, response_text, tool_calls, tick_duration, timestamp, failure
            )

    def _persist_experiment(
        self,
        ctx: _TickContext,
        response_text: str,
        tool_calls: list[dict[str, Any]],
        tick_duration: float,
        timestamp: str,
        failure: str = "",
    ) -> None:
        """Experiments: save a single snapshot file, no journal.

        A failed turn leads its Agent Response with ``(error: ...)``, which is
        what marks the run failed in the Runs rail.
        """
        from .journal import save_experiment_snapshot

        if failure:
            response_text = f"(error: {failure})\n\n{response_text}"
        save_experiment_snapshot(
            agent_dir=self.strategy.home,
            experiment_num=self.session_num,
            execution_mode=self.config.get("execution_mode", "loop"),
            timestamp=timestamp,
            system_prompt=ctx.prompt,
            response_text=response_text,
            tool_calls=tool_calls,
            executors_data=ctx.executors_summary,
            risk_state=ctx.risk_state.to_dict(),
            duration=tick_duration,
            agent_key=self._agent_key(),
        )
        # The tick's own file is the record; ``_record_failed_experiment`` must
        # not replace it with an empty one when the failure is raised after.
        self._experiment_written = True
        log.info(
            "TickEngine %s experiment #%d complete (tools=%d, response=%d chars)",
            self.agent_id,
            self.session_num,
            len(tool_calls),
            len(response_text),
        )

    async def _persist_session(
        self,
        ctx: _TickContext,
        response_text: str,
        tool_calls: list[dict[str, Any]],
        tick_duration: float,
        timestamp: str,
        failure: str = "",
    ) -> None:
        """Sessions: full journal tracking, snapshot, action log, live report."""
        # What the tick actually *did*, as opposed to what it said (FEAT-097).
        # Derived here rather than at stream time because the outcome of a
        # call is only known once its terminal update has folded in, and a
        # log whose whole purpose is "what it did" must not record intent.
        # The tick number is the one ``record_tick`` is about to assign
        # (it increments and returns the counter), needed here because the
        # same call wants the action *count* for journal.md's Ticks line —
        # which has read ``actions=0`` on every tick ever written.
        tick_actions = actions_mod.actions_from_tool_calls(
            tool_calls, tick=self.journal.tick_count + 1, at=time.time()
        )

        # When it last did something that *worked* ([[FEAT-137]]). Read off the
        # same rows, so the stamp and the log can never disagree about what the
        # session did, and only ever moved forward: a tick whose every call
        # failed leaves it where the last successful one put it, where
        # ``_last_tick_at`` would have advanced.
        effect_at = actions_mod.last_effect_at(tick_actions)
        if effect_at > self._last_successful_action_at:
            self._last_successful_action_at = effect_at

        # And what it took ownership of (FEAT-102). Derived from the same
        # folded list, so a deploy that is logged is a deploy that is owned.
        # The risk gate already claims a deploy on its way *in*
        # (``condor.agents.risk``), but only when the permission callback
        # fires and only if it could read the arguments; this claims it on
        # the way out, from the call that actually completed. ``note_deploy``
        # is idempotent and never downgrades an adopted bot, so the two
        # claims cost nothing — and the gate's earlier ``since`` is the
        # correct attribution window, which is why it stays.
        if self.ledger is not None:
            for bot_name in actions_mod.deployed_bot_names(tool_calls):
                self.ledger.note_deploy(bot_name)

        # Every journal.md update of this tick goes into one batch, so the file
        # is rewritten once instead of three-to-five times (PERF-136).
        with self.journal.batch():
            tick_num = self.journal.record_tick(
                response_summary=response_text[:500],
                actions=len(tick_actions),
            )

            self._journal_ownership_violations(tick_num)
            self._journal_refusals(tick_num)
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
                # Whether the figures above are a reading at all ([[FEAT-137]],
                # [[CORR-725]]). Written onto the line because the four of them
                # are the same zeros either way: without it the guard reading
                # them back took a tick that read nothing for a flat book, and
                # a zero exposure clears the drawdown outright.
                #
                # An absent key is the provider's *own* error path -- it stamps
                # every answer it gives, including ``empty`` and ``missing`` --
                # so absence is the one case that must not read as ``ok``.
                reading=str(self._last_skill_data.get("reading") or "missing"),
            )

            if failure:
                action_brief = f"ERROR: {failure}"[:100].replace("\n", " ")
            elif response_text:
                action_brief = response_text[:100].replace("\n", " ")
            else:
                action_brief = "No response"
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
            system_prompt=ctx.prompt,
            response_text=response_text,
            tool_calls=tool_calls,
            executors_data=ctx.executors_summary,
            risk_state=ctx.risk_state.to_dict(),
            duration=tick_duration,
        )
        actions_mod.append_actions(self.session_dir, tick_actions)

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

    def _apply_drift_verdict(self, risk_state, drift_result) -> None:
        """Carry the venue check's verdict into the risk state ([[FEAT-113]]).

        The drift figures always reach the prompt, so the agent reads the same
        verdict the gate acted on. Whether they *bind* is the operator's call:
        with ``max_drift_quote`` at its -1 default nothing is ever blocked,
        because small drift is normal and an install that blocked on dust would
        be taught to raise the limit until it never fired.

        Enabled, it fails closed the way ``get_state`` already does for missing
        metrics: a venue that did not answer, a drift past the limit, or a drift
        provider that failed outright all leave the book untrusted — and an
        untrusted book refuses new exposure while every brake still passes.
        """
        if drift_result is None:
            return

        data = drift_result.data or {}
        worst = data.get("worst_quote")
        risk_state.drift_quote = worst

        limit = self.risk.limits.max_drift_quote
        if limit < 0:
            return

        if not data:
            risk_state.book_trusted = False
            risk_state.drift_reason = "the venue check did not run"
        elif not data.get("trusted", True):
            risk_state.book_trusted = False
            risk_state.drift_reason = (
                f"venue unanswered: {data.get('reason') or 'no reason given'}"
            )
        elif worst is not None and worst > limit:
            risk_state.book_trusted = False
            risk_state.drift_reason = (
                f"drift ${worst:,.2f} on your controllers exceeds the "
                f"${limit:,.2f} limit"
            )

    @property
    def _adoption_done(self) -> bool:
        """Adoption is settled: this session's fleet is on the ledger.

        Read off the ledger rather than latched by a pass having run. The two
        used to be the same flag, and a first pass that reached the API and
        found nothing to adopt — an empty server, a deployment still seconds
        away — closed adoption for the life of the session. Owning nothing is
        not a finished state; it is what every missed deployment leaves behind,
        and it is the state :meth:`_adopt_running_bots` exists to leave
        (CORR-724).
        """
        return bool(self.ledger is not None and self.ledger.bases())

    async def _adopt_running_bots(self, client) -> None:
        """Record the bots already live that belong to us (FEAT-017).

        Runs at most once per tick — not in ``__post_init__``, which is sync and
        must not do network I/O. Reaching the API is best-effort: a failure means
        "own nothing yet" and is retried on the next tick, never fatal.

        A namespace-enforcing session recognises its bots by name. One that does
        not enforce has no such proof, so it adopts by *lineage* instead: a live
        bot whose base an earlier session of this same strategy recorded owning is
        this strategy's bot, and the session that just replaced that one inherits
        it. Both rules are conservative — an unrecognised bot is left alone.

        All three rules match a *name*, and the names they are matched against are
        the orchestrator's live listing. The controller-performance snapshot is not
        a list of what is running — it keeps every bot the API ever orchestrated, a
        stopped one's frozen final row included — so it was only ever usable here
        once filtered through that listing (CORR-699), which left it with nothing
        to add: every name it contributed that survived the filter was already in
        the listing, and a bot that is live but has no performance row yet was
        dropped for the lack of it (CORR-724).

        Adoption is not a read: it opens an ownership window starting now and
        makes every later stop/resume/redeploy target a real bot. So an *unknown*
        liveness defers the whole pass rather than falling back to the unfiltered
        snapshot — the one failure this function used not to route through its own
        best-effort contract (CORR-723). The read surfaces keep the fallback.
        """
        if self.ledger is None:
            return

        from .ownership import prior_session_bases, read_disowned, strip_deploy_suffix

        # A third source, as conservative as the other two: the bases this
        # session's *own* action log records deploying (FEAT-102). Reading back
        # what we wrote is a record, not a guess, and it is what lets a restart
        # — or a session whose first ticks predate this feature — recover its
        # fleet on the next tick instead of never.
        recorded = {
            base
            for base in (
                strip_deploy_suffix(name)
                for name in actions_mod.recorded_deploy_names(self.session_dir)
            )
            if base
        }

        # "Nothing to adopt yet" and "adoption is finished" used to be the same
        # state: one successful pass closed adoption for the life of the session,
        # so a bot deployed — or first seen — on any later tick was never owned,
        # and the session then reported its lifetime totals instead of its own
        # window (CORR-724). A pass is worth repeating while the ledger owns
        # nothing, and again whenever this session's action log records a deploy
        # the last pass had not seen; a bot confirmed live on a later tick is
        # adopted then rather than never. Still one pass per tick at most, so
        # this stays off the hot path, and both reads below are server-cached.
        if self._adoption_done and not recorded - self._adoption_deploys:
            return

        from condor.fetchers.bot_performance import fetch_live_instance_names_checked

        # Fail closed: an unanswered orchestrator means nothing here is known to
        # be running, and adopting on the unfiltered snapshot would open an
        # ownership window on bots that may be dead. Deferring costs a tick and
        # is the same best-effort contract every other failure here uses.
        live, live_known = await fetch_live_instance_names_checked(client)
        if not live_known:
            log.warning(
                "TickEngine %s: bot adoption deferred (liveness unknown — the "
                "orchestrator did not answer the active-bots listing)",
                self.agent_id,
            )
            return

        # What a person has said is *not* ours, which outranks all three sources
        # below. Every one of them is an inference from a name or a log, and a
        # mis-attributed bot is one somebody has to be able to hand back — a
        # namespace match cannot be argued with, and a recorded deploy is a
        # permanent fact that would otherwise re-claim the bot on every restart
        # for the life of the strategy. Re-read on every pass, so a disown taken
        # mid-session is honoured by the next one rather than only by the next
        # process.
        disowned = read_disowned(getattr(self.strategy, "home", None))

        inherited: set[str] = set(recorded)
        if not self.ledger.enforced and self.session_dir is not None:
            inherited |= prior_session_bases(self.session_dir.parent)

        now = time.time()
        held = set(self.ledger.bases())
        for instance_name in sorted(live):
            base = strip_deploy_suffix(instance_name)
            if base in disowned:
                continue
            if self.ledger.owns(instance_name) or base in inherited:
                self.ledger.adopt(instance_name, now)
        self._adoption_deploys = recorded
        # Only what this pass added: with adoption no longer one-shot, logging
        # the whole book would repeat it on every tick that retries.
        adopted = sorted(set(self.ledger.bases()) - held)
        if adopted:
            log.info(
                "TickEngine %s adopted bots: %s", self.agent_id, ", ".join(adopted)
            )

    def _live_session_nums(self) -> set[int] | None:
        """Session numbers of this strategy the supervisor holds engines for.

        ``None`` when the registry cannot be trusted to answer: this engine is
        not in it, which in a real run only happens before ``start()``. Adoption
        reads that as "do not adopt" rather than "nothing is alive" — the whole
        safety of the pass rests on knowing which siblings are still running.
        """
        try:
            engines = _supervisor().for_strategy(self.agent.slug, self.strategy.slug)
        except Exception:  # pragma: no cover - the registry is a plain dict
            log.warning("TickEngine %s: supervisor unreadable", self.agent_id)
            return None
        nums = {int(getattr(e, "session_num", 0) or 0) for e in engines}
        if self.session_num not in nums:
            return None
        return nums

    async def _adopt_running_executors(self, client) -> None:
        """Take over the executors a dead session of this strategy left RUNNING.

        The gap :meth:`_adopt_running_bots` closes for bots, closed for
        executors (CORR-726). An executor's ``controller_id`` is the *session*
        tag, and a graceful ``stop()`` deliberately leaves positions and
        executors running, so the ordinary restart hands the next session a
        book it cannot touch: ``_stop_refusal`` refuses every id outside this
        tick's own snapshot. Feeding the orphans into that snapshot is the whole
        fix — the gate itself is left exactly as it is, because widening it
        would also allow stopping a *live* sibling's position.

        Conservative on three counts: same agent and strategy, owner session not
        alive in the supervisor, and RUNNING. Re-derived every tick rather than
        latched, so an id stops being adopted the moment its owner is back, and
        best-effort: a failed read owns nothing this tick and is retried on the
        next, never fatal.
        """
        self._adopted_executors = []
        self._adopted_unpriced = []
        if self.is_experiment:
            return
        live = self._live_session_nums()
        if live is None:
            return

        from condor.fetchers.executors import (
            build_executor_row,
            fetch_all_executors,
            price_pending_rows,
            unpriced_row_ids,
        )

        from .executor_adoption import orphaned_running_rows

        try:
            rows = await fetch_all_executors(client, status="RUNNING")
        except Exception:
            log.warning(
                "TickEngine %s: executor adoption deferred (the API did not "
                "answer the running-executor search)",
                self.agent_id,
                exc_info=True,
            )
            return

        orphans = orphaned_running_rows(rows, self.agent.slug, self.strategy.slug, live)
        if not orphans:
            return
        built = [row for row in map(build_executor_row, orphans) if row["id"]]
        # The provider prices its own pending rows this way; these rows never
        # reach it (it fetches under this session's tag), so they are priced
        # with the same helper rather than folded into the book at $0
        # (CORR-735). Best-effort like the rest of the pass: a read that fails
        # leaves the row unvalued and says so, and owns the orphan regardless.
        try:
            unpriced = await price_pending_rows(client, built)
        except Exception:
            log.warning(
                "TickEngine %s: adopted executor pricing failed; their exposure "
                "is unknown, not zero",
                self.agent_id,
                exc_info=True,
            )
            # The same set the success arm names, from the same predicate: this
            # arm used to report *every* unvalued row, so the two disagreed
            # over any row that is genuinely worth zero (CORR-740).
            unpriced = unpriced_row_ids(built)
        self._adopted_executors = built
        self._adopted_unpriced = unpriced
        log.warning(
            "TickEngine %s adopted %d orphaned executor(s) from dead session(s): %s",
            self.agent_id,
            len(built),
            ", ".join(f"{row['id']} ({row['controller_id']})" for row in built),
        )

    def _adopted_executors_summary(self) -> str:
        """The adopted orphans as a prompt section, or ``""`` when there are none.

        Without this the agent is told it has no executors while an inherited
        one is live and spending: the executors provider only reports rows under
        this session's own tag and the bots its ledger owns, so an adopted
        orphan appears nowhere else in the prompt — and an id the model never
        sees is one it can never stop.
        """
        rows = self._adopted_executors
        if not rows:
            return ""
        lines = [
            f"ADOPTED from a previous session ({len(rows)}) — these are YOURS to "
            "monitor and close. A previous session of this strategy opened them "
            "and is no longer running; stop_executor() on these ids is allowed.",
        ]
        for row in rows:
            lines.append(
                f"  {row.get('id')} {row.get('pair') or '?'} "
                f"{row.get('type') or 'executor'} ${row.get('pnl', 0.0):+.2f} "
                f"(opened by {row.get('controller_id') or '?'})"
            )
        return "\n".join(lines)

    async def _pnl_series(self) -> list[dict]:
        """This session's realized curve for the live report, or ``[]``.

        Only meaningful once the session owns a bot: an executor-only session has
        no bot history to derive from and the report falls back to the journal's
        snapshots. Best-effort — a charting input must never cost a tick.

        The executors provider already derives the curve from the histories it
        sliced at tick start, beside the KPIs the report shows; only a tick whose
        provider data carries none (a failed fetch, or inputs it could not match)
        walks the histories again here.
        """
        if not self.ledger or not self.ledger.bases():
            return []
        carried = self._last_skill_data.get("pnl_series")
        if carried is not None:
            return carried
        try:
            from .attribution import ownership_windows, window_span
            from .performance import fetch_agent_pnl_series

            client = await self._get_client()
            since, until = window_span(ownership_windows(self.ledger.owned()))
            return await fetch_agent_pnl_series(
                client, self.ledger.bases(), since, until=until
            )
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

    def _ingest_throttle_refusals(self) -> None:
        """Move this session's venue refusals into the tick's refusal log.

        The count is taken where the call is made — the ``hummingbot_api`` MCP
        subprocess, the one place that sees every agent call and its status —
        and travels back on the shared log
        (:mod:`condor.agents.throttle`). Here it becomes a refusal like any
        other, tagged ``throttle`` with the venue that refused, so the journal
        and the next tick's prompt both carry it.

        Deliberately *not* folded into ``_last_error``: the agent did nothing
        wrong, and recording a throttled venue as an agent failure is what made
        it read as a mysterious strategy bug (GitHub #276). Best-effort — an
        unreadable log leaves the tick exactly as it was.
        """
        try:
            from condor.agents.throttle import ledger

            book = ledger()
            book.ingest()
            for event in book.drain_refusals(self.agent_id):
                self._refusals.note(
                    event.get("tool", "") or "a venue call",
                    event.get("reason", "") or "the venue refused the call",
                    now=event.get("at") or None,
                    kind=RefusalLog.KIND_THROTTLE,
                    venue=event.get("venue", ""),
                    host=event.get("host", ""),
                )
        except Exception:  # noqa: BLE001 - accounting may never fail a tick
            log.debug("throttle ingest failed", exc_info=True)

    def _throttled_venues(self) -> list[dict[str, Any]]:
        """The venues that have refused this session, for the status payload.

        Reported as a venue/connector condition and never as this agent's
        error, which is the whole distinction FEAT-138 exists to draw. Empty
        when nothing has refused — and empty, not absent, so a reader can tell
        "checked, nothing" from "no counters at all".
        """
        try:
            from condor.agents.throttle import ledger

            return ledger().session_venues(self.agent_id)
        except Exception:  # noqa: BLE001
            return []

    def _journal_refusals(self, tick_num: int) -> None:
        """Record what the risk gate refused this tick, and why.

        The sibling of :meth:`_journal_ownership_violations`, for the rest of
        the gate. The same drained list goes into the next tick's prompt: a
        cancelled call is all the model sees at the time, and read bare it is
        indistinguishable from a confirmation nobody answered. Told the reason
        instead, the agent can resize, change tack, or stop — the three things an
        unattended seat can actually do about it.
        """
        if not self._last_refusals or not self.journal:
            return
        for entry in self._last_refusals:
            if entry.get("kind") == RefusalLog.KIND_THROTTLE:
                # A separate label on purpose: a reader scanning the journal for
                # why a session stalled must be able to tell "the venue refused
                # us" from "the gate refused the agent" without reading prose.
                venue = entry.get("venue") or "the venue"
                self.journal.append_action(
                    tick_num,
                    "venue_throttled",
                    f"{venue} refused {entry['tool']} — {entry['reason']}",
                )
                continue
            self.journal.append_action(
                tick_num,
                "risk_blocked",
                f"{entry['tool']} refused — {entry['reason']}",
            )

    async def _collect_stream(self, acp_client: ACPClient, prompt: str):
        """Test seam over ``acp_client.prompt_stream``: tests monkeypatch this to
        inject a canned stream.

        The tick's wall-clock budget is the ``asyncio.timeout`` block in
        ``_run_model``, not anything here. Both clients already end their stream
        on ``PromptDone`` (``agent_run`` iterates ``prompt_stream`` directly); the
        ``break`` is a guard for a client that does not.
        """
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
        return build_gated_client(
            self,
            risk_state,
            price_client,
            self.config.get("execution_mode", "loop"),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _executor_owners(self) -> dict[str, str]:
        """This session's executors as ``id -> controller_id`` (SEC-559).

        The gate binds ``stop_executor`` to the session with this: an id in here
        is one of ours, so the common stop costs no extra API call. It reads the
        snapshot the tick already fetched to build the prompt — ``all_executors``
        is the full set (the session's own tag plus every row of the bots its
        ledger owns), with the RUNNING-only ``executors`` as the fallback for a
        provider result that carries no full set.

        Plus whatever this tick adopted from a dead session of this same
        strategy (:meth:`_adopt_running_executors`, CORR-726). Those rows are
        ours by inheritance and by nothing the provider can see — it fetches
        under this session's own tag — so they are added here rather than
        by widening the gate itself.
        """
        rows = (
            self._last_skill_data.get("all_executors")
            or self._last_skill_data.get("executors")
            or []
        )
        if not isinstance(rows, list):
            rows = []
        # ``getattr``: the gate is also built for stand-in engines that carry
        # only ``_last_skill_data`` (tests, and the shutdown path's
        # SimpleNamespace), the same convention ``_finish`` documents.
        rows = list(rows) + list(getattr(self, "_adopted_executors", None) or [])
        owners: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            executor_id = str(row.get("id") or "")
            if executor_id:
                owners[executor_id] = str(row.get("controller_id") or "")
        return owners

    def _agent_key(self) -> str:
        """Resolve the model for this run (see :func:`resolve_agent_key`)."""
        return resolve_agent_key(self.config, self.strategy, self.agent)

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
          nothing must not silently bind to whatever another user creates under
          that name later.

        Falls back to the subject's accessible servers, as before, so a run
        with no server configured still works.

        The chain itself is ``config_manager.resolve_stored_server``, shared with
        the toolset builder, which open-coded the same three steps (CORR-729).
        ``server_pinned`` is what this run *asked for* — the start route's
        ``asked_for_it``, a restart's carried pin — and a pinned name raises
        ``ServerUnavailable`` rather than resolving to a different server. An
        inherited ``server_name`` keeps falling through, which is the leniency
        ``_start`` and ``_check_restartable`` deliberately allow.
        """
        from config_manager import get_config_manager, resolve_stored_server

        cm = get_config_manager()

        server_name = resolve_stored_server(
            cm,
            self.user_id,
            self.config.get("server_name"),
            pinned=self.server_pinned,
            chat_id=self.user_id,
        )
        if not server_name:
            return None, None

        server = cm.get_server(server_name)
        return server_name, server

    async def _get_client(self):
        """Get the Hummingbot API client for this agent.

        ``ServerUnavailable`` is deliberately not swallowed: a pinned server
        that cannot be used is the one failure here that no retry fixes and that
        the operator has to be told about by name (CORR-729). Every other
        failure stays best-effort — ``None``, and the caller decides.
        """
        from config_manager import ServerUnavailable

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
        except ServerUnavailable:
            raise
        except Exception:
            log.exception("Failed to get API client for agent %s", self.agent_id)
            return None

    async def _notify(self, message: str) -> None:
        """Tell the run's owner on Telegram *and* the dashboard bell, once each.

        The notice goes down :func:`condor.notifications.announce`, the path a
        delegation's notice takes: it resolves the sender ladder itself and
        owns the "don't file it twice when the bottom rung is the bell" rule
        (ARCH-212). Sending straight to the resolved bot put tick errors, risk
        blocks and the emergency-shutdown alerts on Telegram only, so a
        dashboard user on a Telegram-equipped install never saw them (ARCH-647).
        A dashboard launch carries no chat (``chat_id`` 0); its owner's private
        chat is the one their user id names. The bell entry links to the
        agent's page, named by the agent slug that leads ``agent_id``.
        """
        chat_id = self.chat_id or self.user_id
        if not chat_id:
            return
        from condor.notifications import announce

        slug = self.agent_id.partition(".")[0]
        try:
            await announce(
                self.user_id,
                chat_id,
                message,
                kind="agent",
                bot=getattr(self, "_bot", None),
                title=f"Loop · {slug}",
                link=f"/agents/{slug}",
            )
        except Exception:
            log.exception("Failed to send notification to chat %s", chat_id)

    def _record_failed_experiment(self, error: str) -> None:
        """Write the dry run's file for a tick that raised before writing it.

        An experiment keeps no journal and its engine is dropped the moment it
        stops, so a failed dry run used to leave nothing behind at all. The
        error goes where a failed model call already puts it — the Agent
        Response — which is what marks the run as failed in the Runs rail.
        """
        from .journal import save_experiment_snapshot

        if self._experiment_written:
            return
        try:
            save_experiment_snapshot(
                agent_dir=self.strategy.home,
                experiment_num=self.session_num,
                execution_mode=self.config.get("execution_mode", "loop"),
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                system_prompt="",
                response_text=f"(error: {error})",
                tool_calls=[],
                executors_data="The tick failed before collecting any.",
                risk_state={},
                duration=time.time() - self._last_tick_at,
                agent_key=self._agent_key(),
            )
        except Exception:
            log.exception(
                "TickEngine %s: could not record the failed run", self.agent_id
            )

    def get_info(self) -> dict[str, Any]:
        """Return a summary dict for display."""
        sd = self._last_skill_data
        risk_limits = self.config.get("risk_limits", {})

        if self.journal:
            summary = self.journal.get_summary_dict()
        else:
            summary = {"total_ticks": 0, "total_volume": 0}

        return {
            "agent_id": self.agent_id,
            "strategy": self.strategy.name,
            "strategy_slug": self.strategy.slug,
            "session_num": self.session_num,
            "status": self.status,
            "tick_count": summary["total_ticks"],
            "daily_pnl": sd.get("total_pnl", 0.0),
            "realized_pnl": sd.get("realized_pnl", 0.0),
            "unrealized_pnl": sd.get("unrealized_pnl", 0.0),
            "total_volume": sd.get("total_volume", summary.get("total_volume", 0)),
            "total_exposure": sd.get("total_exposure", 0.0),
            "open_executors": len(sd.get("executors") or []),
            # What the PnL above is made of, and what it is missing. A session
            # operating bots earns through them, so naming them is the difference
            # between a number and an auditable one.
            "bot_names": sd.get("bot_names", []),
            "bot_instances": sd.get("bot_instances", []),
            "unresolved_bases": sd.get("unresolved_bases", []),
            "controllers": sd.get("controllers", []),
            "close_type_counts": sd.get("close_type_counts", {}),
            "fees_known": sd.get("fees_known", True),
            # Which scope each figure has, and over which instances (FEAT-135).
            # The PnL/volume above are one number folded from both scopes, so the
            # session report renders these beside them rather than letting the
            # fold read as a single unlabelled total.
            "session_realized_pnl": sd.get("session_realized_pnl"),
            "session_volume": sd.get("session_volume"),
            "session_instances": sd.get("session_instances", []),
            "lifetime_realized_pnl": sd.get("lifetime_realized_pnl"),
            "lifetime_volume": sd.get("lifetime_volume"),
            "lifetime_instances": sd.get("lifetime_instances", []),
            "frequency_sec": self.config.get("frequency_sec", 60),
            "tick_timeout_sec": resolve_tick_timeout(
                execution_mode=self.config.get("execution_mode", "loop"),
                strategy=self.config.get("tick_timeout_sec"),
            ),
            "server_name": self.config.get("server_name", ""),
            # The third pin, beside ``server_name`` and ``agent_key`` below.
            # Carried so an effective-vs-file diff covers the whole pin set
            # rather than the two thirds of it a status happened to report
            # ([[FEAT-136]]).
            "model_base_url": self.config.get("model_base_url", ""),
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
            # The pin above is what was asked for; this is what the adapter
            # answered ([[FEAT-136]]). They differ exactly when a pin did not
            # apply, which is what a pin audit before a race start looks for.
            "active_model_id": self._active_model_id,
            "execution_mode": self.config.get("execution_mode", "loop"),
            "max_ticks": self.config.get("max_ticks", 0),
            "last_tick_at": self._last_tick_at,
            # How long the last completed tick took ([[FEAT-141]]) — the host
            # capacity reading a slow tick is the symptom of.
            "last_tick_duration": self._last_tick_duration,
            # Outcomes, beside the attempt above ([[FEAT-137]]).
            "last_reading_at": self._last_reading_at,
            "last_successful_action_at": self._last_successful_action_at,
            "last_error": self._last_error,
            #: Which venues refused *this* session, with the attempts /
            #: successes / refusals behind the claim ([[FEAT-138]]). A venue
            #: condition, deliberately beside ``last_error`` and not inside it.
            "throttled_venues": self._throttled_venues(),
            "session_dir": str(self.session_dir) if self.session_dir else "",
            "is_experiment": self.is_experiment,
        }


def build_gated_client(
    engine: "TickEngine",
    risk_state: RiskState,
    price_client: Any,
    execution_mode: str,
) -> "ACPClient | PydanticAIClient":
    """The unattended client for ``engine``: tick toolset behind the risk gate.

    The one place a loop's model client is built, so the tick and the emergency
    winddown's LLM cleanup (``execution_mode="shutdown"``, SEC-631) mount the
    same narrow profile and answer every dangerous call through the same
    ``auto_approve_with_risk_check`` — ledger, refusal log and executor
    ownership included. Does NOT start the client.

    Also the one place the run's server is decided for its *tools*, and it reads
    that answer off ``engine._resolve_server`` rather than deriving a second one
    (CORR-737), so a tick's data and its trading tools are always one fleet.
    """
    # Resolve the server ONCE per run and hand the name down (CORR-737). This
    # used to pass ``config["server_name"]`` and let the toolset builder resolve
    # its own answer from ``engine.chat_id``, while the data client resolved from
    # ``user_id`` (SEC-164's deliberate substitution in ``_resolve_server``). On
    # the same rule but different inputs the two legs diverge for every loop that
    # configures no server — which is every shipped one — so the prompt described
    # fleet A while ``create_executor`` landed on fleet B. Handing the name down
    # makes the disagreement unrepresentable: the venue a loop trades on is the
    # one its *owner* resolves, not the chat it happened to be started from.
    # Not cached on the engine: ``_resolve_server`` re-checks existence and reach
    # every tick, which is CORR-729's "a name that matched nothing at start stays
    # unusable" property for a long-running loop. The chain is dict lookups.
    resolved, _ = engine._resolve_server()
    # tick=True narrows both subprocesses to the loop profile (FEAT-066).
    # This seat runs unattended behind an auto-approving permission callback,
    # so what it can reach at all is decided here, by what gets mounted.
    # ``server_pinned``: a name resolved for THIS run must stick — if it has
    # since become unusable the toolset raises ``ServerUnavailable`` naming it
    # rather than quietly resolving to a second server.
    # ``server_required``: a tick with no mcp-hummingbot is a trading agent with
    # no trading tools, which used to be started on a log line (CORR-729). It
    # still carries the empty case, where nothing resolved for the owner at all.
    mcp_servers = toolsets.build_mcp_servers_for_session(
        engine.user_id,
        # Still the chat the condor MCP posts into and resolves its session from
        # (``_condor_mcp_args``) — it no longer selects the trading venue.
        engine.chat_id,
        server_name=resolved,
        agent_slug=engine.agent.slug,
        tick=True,
        server_pinned=bool(resolved),
        server_required=True,
        # So a 429 this seat's tools are handed is counted against this session
        # and not merely against the agent ([[FEAT-138]]).
        caller_id=engine.agent_id,
    )
    permission_cb = auto_approve_with_risk_check(
        engine.risk,
        risk_state,
        execution_mode=execution_mode,
        ledger=engine.ledger,
        agent_id=engine.agent_id,
        price_client=price_client,
        refusals=engine._refusals,
        executor_owners=engine._executor_owners(),
    )

    # Shared factory (ARCH-192). Engine specifics: an explicit model_base_url
    # in the run config still wins over the owner's saved custom endpoint.
    # Same allowlist the agent gets when delegated to; empty => unrestricted.
    from condor.runtime.llm_client import build_llm_client

    return build_llm_client(
        engine._agent_key(),
        mcp_servers=mcp_servers,
        permission_callback=permission_cb,
        allowed_tools=engine.agent.tools or None,
        user_id=engine.user_id,
        base_url_override=engine.config.get("model_base_url") or None,
    )
