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
from condor.acp.pydantic_ai_client import PydanticAIClient, is_pydantic_ai_model

from .journal import JournalManager, next_experiment_number, next_session_number
from .prompts import build_tick_prompt
from .risk import RiskEngine, RiskLimits, auto_approve_with_risk_check
from .strategy import Strategy
from .providers import ProviderRegistry

log = logging.getLogger(__name__)

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
            from .config import AgentConfig, save_agent_config
            agent_config = AgentConfig.from_dict(self.config)
            save_agent_config(self.session_dir, agent_config)

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

        # 3. Read journal context (sessions only)
        learnings = self.journal.read_learnings() if self.journal else ""
        recent_decisions = self.journal.get_recent_decisions(count=3) if self.journal else ""
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
        next_tick = self.journal.tick_count + 1 if self.journal else 1
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
        )

        # Inject pending user directives
        if self._pending_directives:
            directives = "\n".join(f"- {d}" for d in self._pending_directives)
            prompt += (
                f"\n\nUSER DIRECTIVES (apply these on this tick):\n{directives}"
            )
            self._pending_directives.clear()

        # 6. Create agent client (ACP for Claude/Gemini, PydanticAI for open-source models)
        from handlers.agents._shared import (
            build_mcp_servers_for_agent,
            build_mcp_servers_for_session,
            get_project_dir,
        )

        mode = self.config.get("execution_mode", "loop")

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

        # Session config overrides strategy default for agent_key
        agent_key = self.config.get("agent_key") or self.strategy.agent_key
        use_pydantic_ai = is_pydantic_ai_model(agent_key)

        if use_pydantic_ai:
            import os
            base_url = self.config.get("model_base_url") or None
            # Auto-detect filter mode based on model, or use explicit config
            tool_filter_mode = (
                self.config.get("tool_filter_mode") or
                os.environ.get("PYDANTIC_AI_TOOL_FILTER") or
                None  # None triggers auto-detection
            )
            acp_client = PydanticAIClient(
                model=agent_key,
                mcp_servers=mcp_servers,
                permission_callback=permission_cb,
                base_url=base_url,
                tool_filter_mode=tool_filter_mode,  # Auto-detects if None
            )
        else:
            agent_cmd = ACP_COMMANDS.get(agent_key, ACP_COMMANDS["claude-code"])
            acp_client = ACPClient(
                command=agent_cmd,
                working_dir=get_project_dir(),
                mcp_servers=mcp_servers,
                permission_callback=permission_cb,
            )

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
                agent_key=agent_key,
            )
            log.info(
                "TickEngine %s experiment #%d complete (tools=%d, response=%d chars)",
                self.agent_id, self.session_num, len(tool_calls), len(response_text),
            )
        else:
            # Sessions: full journal tracking
            tick_num = self.journal.record_tick(
                response_summary=response_text[:500],
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

            action_brief = response_text[:100].replace("\n", " ") if response_text else "No response"
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

    async def _collect_stream(self, acp_client: ACPClient, prompt: str):
        """Wrapper to make prompt_stream compatible with wait_for."""
        async for event in acp_client.prompt_stream(prompt):
            yield event
            if isinstance(event, PromptDone):
                break

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
            "last_tick_at": self._last_tick_at,
            "last_error": self._last_error,
            "session_dir": str(self.session_dir) if self.session_dir else "",
            "is_experiment": self.is_experiment,
        }
