"""TickEngine -- main orchestrator for autonomous trading agents.

One TickEngine instance per running agent.  Each tick:
1. Pre-compute core data providers (active executors)
2. Read journal (learnings + summary + recent decisions)
3. Build prompt with strategy + data + risk state
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
    ACP_COMMANDS,
    ACPClient,
    Heartbeat,
    PromptDone,
    TextChunk,
    ToolCallEvent,
    ToolCallUpdate,
)
from condor.acp.cursor_sdk_client import CursorSdkClient, is_cursor_sdk_model
from condor.acp.pydantic_ai_client import PydanticAIClient, is_pydantic_ai_model

from .journal import JournalManager, next_experiment_number, next_session_number
from .prompts import build_tick_prompt
from .risk import RiskEngine, RiskLimits, auto_approve_with_risk_check
from .strategy import Strategy
from .providers import ProviderRegistry

log = logging.getLogger(__name__)


_TRIPLE_BARRIER_CLOSE_TYPES = frozenset({"STOP_LOSS", "TAKE_PROFIT"})


def _normalize_close_type(close_type: str) -> str:
    return (close_type or "").upper().replace(" ", "_").replace("-", "_")


def _is_barrier_close_type(close_type: str) -> bool:
    """True only for triple-barrier SL/TP — not EARLY_STOP, manual, or agent stop."""
    return _normalize_close_type(close_type) in _TRIPLE_BARRIER_CLOSE_TYPES


def _extract_agent_closed_executor_ids(tool_calls: list[dict[str, Any]]) -> set[str]:
    """Executor IDs the agent stopped this tick (manage_executors action=stop)."""
    closed: set[str] = set()
    for tc in tool_calls:
        name = (tc.get("name") or "").lower()
        if "manage_executors" not in name:
            continue
        inp = tc.get("input") or {}
        if isinstance(inp, str):
            try:
                import json

                inp = json.loads(inp)
            except Exception:
                continue
        if not isinstance(inp, dict):
            continue
        action = str(inp.get("action") or "").lower()
        if action not in ("stop", "close"):
            continue
        eid = inp.get("executor_id") or inp.get("id")
        if eid:
            closed.add(str(eid))
    return closed


def _detect_barrier_closes(
    all_executors: list[dict[str, Any]],
    last_running_ids: set[str],
    already_notified: set[str],
    agent_closed_ids: set[str],
) -> list[dict[str, Any]]:
    """Find executors that were RUNNING last tick and closed via SL/TP since then."""
    if not last_running_ids:
        return []

    running_ids = {
        e["id"] for e in all_executors if e.get("status") == "RUNNING" and e.get("id")
    }
    by_id = {e["id"]: e for e in all_executors if e.get("id")}

    closes: list[dict[str, Any]] = []
    for eid in last_running_ids:
        if eid in running_ids or eid in already_notified or eid in agent_closed_ids:
            continue
        ex = by_id.get(eid)
        if not ex or ex.get("status") == "RUNNING":
            continue
        if _is_barrier_close_type(str(ex.get("close_type") or "")):
            closes.append(ex)
    return closes


def _format_barrier_closes_section(closes: list[dict[str, Any]]) -> str:
    if not closes:
        return ""
    lines = [
        "[BARRIER CLOSES SINCE LAST TICK]",
        "Triple-barrier STOP_LOSS or TAKE_PROFIT only (not EARLY_STOP / agent exits). "
        "One send_notification per row — do not duplicate for the same executor_id:",
    ]
    for ex in closes:
        close_type = ex.get("close_type") or "UNKNOWN"
        pnl = float(ex.get("pnl") or 0)
        lines.append(
            f"- {ex.get('pair', '?')} {ex.get('side', '')} | {close_type} | "
            f"PnL ${pnl:+.2f} | id={ex.get('id', '')}"
        )
    return "\n".join(lines)


async def _notify_via_telegram_bot_api(chat_id: int, text: str) -> None:
    """Send plain text using TELEGRAM_TOKEN when no python-telegram-bot handle exists."""
    from condor.telegram_notify import prepare_agent_notification_text
    from utils.config import TELEGRAM_TOKEN

    if not TELEGRAM_TOKEN or not chat_id:
        return
    payload_text = prepare_agent_notification_text(text or "", max_chars=4090)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": payload_text}
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    log.warning(
                        "Telegram notify failed for chat_id=%s: %s",
                        chat_id,
                        data.get("description", data),
                    )
    except Exception:
        log.exception("Telegram notify HTTP failed for chat_id=%s", chat_id)


# Module-level registry of running engines
_engines: dict[str, "TickEngine"] = {}


class _NullTracker:
    """Stub tracker for experiments (no journal)."""
    def get_total_exposure(self) -> float: return 0.0
    def get_open_executor_count(self) -> int: return 0
    def get_drawdown_pct(self) -> float: return 0.0


def get_engine(agent_id: str) -> TickEngine | None:
    return _engines.get(agent_id)


def get_all_engines() -> dict[str, "TickEngine"]:
    return dict(_engines)


@dataclass
class TickEngine:
    strategy: Strategy
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
    _last_tick_at: float = field(default=0.0, init=False)
    _last_error: str = field(default="", init=False)
    _last_skill_data: dict[str, Any] = field(default_factory=dict, init=False)
    _pending_directives: list[str] = field(default_factory=list, init=False)
    _cached_routines_section: str | None = field(default=None, init=False, repr=False)
    _last_running_executor_ids: set[str] = field(default_factory=set, init=False)
    _notified_barrier_close_ids: set[str] = field(default_factory=set, init=False)
    _agent_closed_executor_ids: set[str] = field(default_factory=set, init=False)

    def __post_init__(self):
        agent_dir = self.strategy.agent_dir
        mode = self.config.get("execution_mode", "loop")
        self.is_experiment = mode in ("dry_run", "run_once")

        if self.is_experiment:
            self.session_num = next_experiment_number(agent_dir)
            self.agent_id = f"{self.strategy.slug}_e{self.session_num}"
            # Experiments: flat folder, no session dir or journal
            self.session_dir = None
            self.journal = None
        else:
            self.session_num = next_session_number(agent_dir)
            self.agent_id = f"{self.strategy.slug}_{self.session_num}"
            self.session_dir = agent_dir / "sessions" / f"session_{self.session_num}"
            self.session_dir.mkdir(parents=True, exist_ok=True)

            # Save config per session
            from .config import save_full_config
            save_full_config(self.session_dir, self.config)

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
        log.info("TickEngine %s started (freq=%ss)", self.agent_id, self.config.get("frequency_sec", 60))

    async def stop(self) -> None:
        """Stop gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.journal:
            self.journal.close()
        _engines.pop(self.agent_id, None)
        log.info("TickEngine %s stopped", self.agent_id)

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

                # Single-tick modes: stop after first tick
                if mode in ("dry_run", "run_once"):
                    label = "Dry run" if mode == "dry_run" else "Run-once"
                    log.info("TickEngine %s: %s complete, self-stopping", self.agent_id, label)
                    await self._notify(f"Agent {self.agent_id}: {label} complete.")
                    self._running = False
                    _engines.pop(self.agent_id, None)
                    return

                # max_ticks limit (loop mode only)
                max_ticks = self.config.get("max_ticks", 0)
                if max_ticks > 0 and self.journal.tick_count >= max_ticks:
                    log.info("TickEngine %s: reached max_ticks=%d, self-stopping", self.agent_id, max_ticks)
                    await self._notify(f"Agent {self.agent_id}: completed {max_ticks} ticks (max_ticks limit).")
                    self._running = False
                    self.journal.close()
                    _engines.pop(self.agent_id, None)
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

        # 2. Run core data providers (executors only -- agent uses MCP for market data)
        skill_results = await self.provider_registry.run_core_providers(
            client, self.config, agent_id=self.agent_id
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

        barrier_closes_section = ""
        if executors_result and not self.is_experiment:
            all_executors = executors_result.data.get("all_executors") or []
            barrier_closes = _detect_barrier_closes(
                all_executors,
                self._last_running_executor_ids,
                self._notified_barrier_close_ids,
                self._agent_closed_executor_ids,
            )
            barrier_closes_section = _format_barrier_closes_section(barrier_closes)
            for ex in barrier_closes:
                eid = ex.get("id")
                if eid:
                    self._notified_barrier_close_ids.add(eid)
            self._last_running_executor_ids = {
                e["id"]
                for e in all_executors
                if e.get("status") == "RUNNING" and e.get("id")
            }

        # 3. Read journal context (sessions only)
        learnings = self.journal.read_learnings() if self.journal else ""
        next_tick = self.journal.tick_count + 1 if self.journal else 1
        digest_interval = int(self.config.get("digest_interval_ticks", 0) or 0)
        is_digest_boundary = digest_interval > 0 and next_tick % digest_interval == 0
        recent_count = digest_interval if is_digest_boundary else 3
        recent_decisions = (
            self.journal.get_recent_decisions(count=recent_count) if self.journal else ""
        )
        summary = self.journal.read_summary() if self.journal else ""

        # 4. Get risk state (experiments pass None — returns clean state)
        risk_state = self.risk.get_state(self.journal or _NullTracker())

        if risk_state.is_blocked and not self.is_experiment:
            self.journal.append_action(
                self.journal.tick_count + 1,
                "tick_blocked",
                risk_state.block_reason,
            )
            self.journal.record_tick("blocked: " + risk_state.block_reason)
            await self._notify(f"Agent {self.agent_id} blocked: {risk_state.block_reason}")
            return

        # 5. Build prompt (server credentials are injected via env into MCP process)
        # Cache routine discovery on first tick — routines rarely change mid-session
        if self._cached_routines_section is None:
            from .prompts import _build_routines_section
            try:
                self._cached_routines_section = _build_routines_section(self.strategy)
            except Exception:
                self._cached_routines_section = ""

        prompt = build_tick_prompt(
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
            digest_boundary=is_digest_boundary,
            digest_interval=digest_interval,
            barrier_closes_section=barrier_closes_section,
        )

        # Inject pending user directives
        if self._pending_directives:
            directives = "\n".join(f"- {d}" for d in self._pending_directives)
            prompt += (
                f"\n\nUSER DIRECTIVES (apply these on this tick):\n{directives}"
            )
            self._pending_directives.clear()

        # 6. Create a fresh agent client per tick (clean context window)
        acp_client = await self._create_client()

        response_chunks: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_call_map: dict[str, dict[str, Any]] = {}

        await acp_client.start()
        try:
            async with asyncio.timeout(300):
                async for event in self._collect_stream(acp_client, prompt):
                    if isinstance(event, TextChunk):
                        response_chunks.append(event.text)
                    elif isinstance(event, ToolCallEvent):
                        if event.tool_call_id in tool_call_map:
                            tc = tool_call_map[event.tool_call_id]
                            tc["status"] = event.status
                            if event.title:
                                tc["name"] = event.title
                            if event.input:
                                tc["input"] = event.input
                        else:
                            tc = {
                                "id": event.tool_call_id,
                                "name": event.title,
                                "status": event.status,
                                "kind": event.kind,
                            }
                            if event.input:
                                tc["input"] = event.input
                            tool_calls.append(tc)
                            tool_call_map[event.tool_call_id] = tc
                    elif isinstance(event, ToolCallUpdate):
                        if event.tool_call_id in tool_call_map:
                            tc = tool_call_map[event.tool_call_id]
                            if event.status:
                                tc["status"] = event.status
                            if event.title:
                                tc["name"] = event.title
                            if event.output:
                                tc["output"] = event.output
        except asyncio.TimeoutError:
            log.warning("TickEngine %s: ACP prompt timed out", self.agent_id)
            response_chunks.append("(timed out)")
        finally:
            await acp_client.stop()

        response_text = "".join(response_chunks)
        tick_duration = time.time() - self._last_tick_at

        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        executors_summary = core_data_summaries.get("executors", "No executor data.")

        if self.is_experiment:
            # Experiments: save a single snapshot file, no journal
            from .journal import save_experiment_snapshot
            save_experiment_snapshot(
                agent_dir=self.strategy.agent_dir,
                experiment_num=self.session_num,
                execution_mode=mode,
                timestamp=timestamp,
                system_prompt=prompt,
                response_text=response_text,
                tool_calls=tool_calls,
                executors_data=executors_summary,
                risk_state=risk_state.to_dict(),
                duration=tick_duration,
                agent_key=self.config.get("agent_key") or self.strategy.agent_key,
            )
            log.info(
                "TickEngine %s experiment #%d complete (tools=%d, response=%d chars)",
                self.agent_id, self.session_num, len(tool_calls), len(response_text),
            )
        else:
            # Sessions: full journal tracking
            tick_num = self.journal.record_tick(
                response_summary=response_text,
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

            action_brief = response_text.replace("\n", " ") if response_text else "No response"
            self.journal.write_summary(
                tick=tick_num,
                status="Running",
                pnl=skill_pnl,
                open_count=skill_executors,
                last_action=action_brief,
            )

            log.info(
                "TickEngine %s tick #%d complete (tools=%d, response=%d chars)",
                self.agent_id, tick_num, len(tool_calls), len(response_text),
            )

            agent_closed = _extract_agent_closed_executor_ids(tool_calls)
            if agent_closed:
                self._agent_closed_executor_ids.update(agent_closed)
                self._notified_barrier_close_ids.update(agent_closed)
                self._last_running_executor_ids -= agent_closed

    async def _collect_stream(
        self,
        acp_client: ACPClient | PydanticAIClient | CursorSdkClient,
        prompt: str,
    ):
        """Wrapper to make prompt_stream compatible with wait_for."""
        async for event in acp_client.prompt_stream(prompt):
            yield event
            if isinstance(event, PromptDone):
                break

    # ------------------------------------------------------------------
    # Client factory
    # ------------------------------------------------------------------

    async def _create_client(self) -> "ACPClient | PydanticAIClient | CursorSdkClient":
        """Build an ACP or PydanticAI client (does NOT start it)."""
        from handlers.agents._shared import (
            build_mcp_servers_for_agent,
            build_mcp_servers_for_session,
            get_project_dir,
        )

        mode = self.config.get("execution_mode", "loop")
        risk_state = self.risk.get_state(self.journal or _NullTracker())

        server_name = self.config.get("server_name")
        if server_name:
            mcp_servers = build_mcp_servers_for_agent(
                server_name, self.user_id, self.chat_id,
                agent_slug=self.strategy.slug,
                execution_mode=mode,
            )
        else:
            mcp_servers = build_mcp_servers_for_session(
                self.user_id, self.chat_id,
                execution_mode=mode,
            )
        permission_cb = auto_approve_with_risk_check(self.risk, risk_state, execution_mode=mode)

        agent_key = self.config.get("agent_key") or self.strategy.agent_key

        if is_cursor_sdk_model(agent_key):
            log.info(
                "TickEngine agent_key=%s uses Cursor SDK — MCP stdio configs are forwarded; "
                "Composer does not use Condor Telegram permission_callback for MCP tools.",
                agent_key,
            )
            return CursorSdkClient(
                model=agent_key,
                mcp_servers=mcp_servers,
                permission_callback=permission_cb,
            )

        use_pydantic_ai = is_pydantic_ai_model(agent_key)

        if use_pydantic_ai:
            import os
            base_url = self.config.get("model_base_url") or None
            tool_filter_mode = (
                self.config.get("tool_filter_mode") or
                os.environ.get("PYDANTIC_AI_TOOL_FILTER") or
                None
            )
            return PydanticAIClient(
                model=agent_key,
                mcp_servers=mcp_servers,
                permission_callback=permission_cb,
                base_url=base_url,
                tool_filter_mode=tool_filter_mode,
            )
        else:
            agent_cmd = ACP_COMMANDS.get(agent_key, ACP_COMMANDS["claude-code"])
            return ACPClient(
                command=agent_cmd,
                working_dir=get_project_dir(),
                mcp_servers=mcp_servers,
                permission_callback=permission_cb,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        """Send a notification to the user via Telegram."""
        chat_id = self.chat_id
        text = (message or "")[:4096]
        bot = getattr(self, "_bot", None)
        if bot:
            try:
                await bot.send_message(chat_id=chat_id, text=text)
                return
            except Exception:
                log.exception("Failed to send notification to chat %s", chat_id)
        await _notify_via_telegram_bot_api(chat_id, text)

    def get_info(self) -> dict[str, Any]:
        """Return a summary dict for display."""
        sd = self._last_skill_data
        risk_limits = self.config.get("risk_limits", {})

        if self.journal:
            summary = self.journal.get_summary_dict()
        else:
            summary = {"total_ticks": 0, "daily_pnl": 0, "total_volume": 0,
                       "total_exposure": 0, "open_executors": 0}

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
            "risk_limits": risk_limits if isinstance(risk_limits, dict) else risk_limits.model_dump() if hasattr(risk_limits, "model_dump") else {},
            "agent_key": self.config.get("agent_key") or self.strategy.agent_key,
            "execution_mode": self.config.get("execution_mode", "loop"),
            "max_ticks": self.config.get("max_ticks", 0),
            "digest_interval_ticks": int(self.config.get("digest_interval_ticks", 0) or 0),
            "last_tick_at": self._last_tick_at,
            "last_error": self._last_error,
            "session_dir": str(self.session_dir) if self.session_dir else "",
            "is_experiment": self.is_experiment,
        }
