"""TickEngine -- main orchestrator for autonomous trading agents.

One TickEngine instance per running agent.  Each tick:
1. Pre-compute core data providers (active executors)
2. Read journal
3. Build prompt with strategy + data + risk state
4. Spawn a fresh ACP session, send prompt, wait for completion
5. Update tracker and notify user if needed
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from condor.acp.client import ACP_COMMANDS, ACPClient, TextChunk, UsageUpdate

from .journal import JournalManager
from .prompts import build_tick_prompt
from .risk import RiskEngine, RiskLimits, auto_approve_with_risk_check
from .strategy import Strategy
from .tracker import ExecutorTracker
from .providers import ProviderRegistry

log = logging.getLogger(__name__)

# Module-level registry of running engines
_engines: dict[str, "TickEngine"] = {}


def get_engine(agent_id: str) -> TickEngine | None:
    return _engines.get(agent_id)


def get_all_engines() -> dict[str, "TickEngine"]:
    return dict(_engines)


@dataclass
class TickEngine:
    agent_id: str
    strategy: Strategy
    config: dict[str, Any]
    chat_id: int
    user_id: int

    # Components (created in __post_init__)
    journal: JournalManager = field(init=False)
    risk: RiskEngine = field(init=False)
    tracker: ExecutorTracker = field(init=False)
    provider_registry: ProviderRegistry = field(init=False)

    # Runtime state
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)
    _paused: bool = field(default=False, init=False)
    _last_tick_at: float = field(default=0.0, init=False)
    _last_error: str = field(default="", init=False)
    _last_skill_data: dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self):
        self.journal = JournalManager(
            self.agent_id,
            strategy_name=self.strategy.name,
            strategy_description=self.strategy.description,
        )
        risk_limits = RiskLimits.from_dict(self.config.get("risk_limits", {}))
        self.risk = RiskEngine(risk_limits)
        self.tracker = ExecutorTracker(self.agent_id)
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
        self.tracker.close()
        _engines.pop(self.agent_id, None)
        log.info("TickEngine %s stopped", self.agent_id)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

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
            try:
                await asyncio.sleep(freq)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        self._last_tick_at = time.time()

        # 1. Get API client
        client = await self._get_client()
        if not client:
            self.journal.append_error("No API client available")
            return

        # 2. Run core data providers (returns dict[str, ProviderResult])
        skill_results = await self.provider_registry.run_core_providers(
            client, self.config, agent_id=self.agent_id
        )

        # Extract structured data from skills for tracking
        executors_result = skill_results.get("executors")
        if executors_result:
            self._last_skill_data = executors_result.data
        portfolio_result = skill_results.get("portfolio")
        if portfolio_result:
            self._last_skill_data["portfolio"] = portfolio_result.data

        # Convert SkillResult objects to summary strings for prompt
        core_data_summaries: dict[str, str] = {
            name: result.summary for name, result in skill_results.items()
        }

        # 3. Read journal
        journal_content = self.journal.read_recent(max_entries=30)
        learnings = self.journal.read_learnings()

        # 4. Get risk state
        risk_state = self.risk.get_state(self.tracker)

        if risk_state.is_blocked:
            self.journal.append_action(
                self.tracker.tick_count + 1,
                "tick_blocked",
                risk_state.block_reason,
            )
            self.tracker.record_tick("blocked: " + risk_state.block_reason)
            await self._notify(f"Agent {self.agent_id} blocked: {risk_state.block_reason}")
            return

        # 5. Build prompt
        from .skill_loader import get_tick_skills

        server_creds = self._get_server_credentials()
        next_tick = self.tracker.tick_count + 1
        skill_prompts = get_tick_skills(self.strategy.skills, self.config)
        prompt = build_tick_prompt(
            strategy=self.strategy,
            config=self.config,
            core_data=core_data_summaries,
            journal=journal_content,
            learnings=learnings,
            risk_state=risk_state.to_dict(),
            server_credentials=server_creds,
            tick_number=next_tick,
            agent_id=self.agent_id,
            skill_prompts=skill_prompts,
        )

        # 6. Create ACP session
        from handlers.agents._shared import (
            build_mcp_servers_for_agent,
            build_mcp_servers_for_session,
            get_project_dir,
        )
        from condor.widget_bridge import get_widget_bridge

        widget_port = get_widget_bridge().port
        server_name = self.config.get("server_name")
        if server_name:
            mcp_servers = build_mcp_servers_for_agent(
                server_name, self.user_id, self.chat_id, widget_port
            )
        else:
            mcp_servers = build_mcp_servers_for_session(
                self.user_id, self.chat_id, widget_port
            )

        agent_cmd = ACP_COMMANDS.get(self.strategy.agent_key, ACP_COMMANDS["claude-code"])
        permission_cb = auto_approve_with_risk_check(self.risk, risk_state)

        acp_client = ACPClient(
            command=agent_cmd,
            working_dir=get_project_dir(),
            mcp_servers=mcp_servers,
            permission_callback=permission_cb,
        )

        cost = 0.0
        response_text = ""

        await acp_client.start()
        try:
            response_text = await asyncio.wait_for(
                acp_client.prompt(prompt),
                timeout=300,  # 5 min max per tick
            )
            if acp_client.last_usage:
                cost = acp_client.last_usage.cost_usd
        except asyncio.TimeoutError:
            log.warning("TickEngine %s: ACP prompt timed out", self.agent_id)
            response_text = "(timed out)"
        finally:
            await acp_client.stop()

        # 7. Record tick
        tick_num = self.tracker.record_tick(
            response_summary=response_text[:500],
            cost=cost,
        )

        # 8. Record snapshot from live skill data
        skill_pnl = self._last_skill_data.get("total_pnl", 0.0)
        skill_volume = self._last_skill_data.get("total_volume", 0.0)
        skill_executors = len(self._last_skill_data.get("executors", []))
        skill_exposure = self._last_skill_data.get("total_exposure", 0.0)
        self.tracker.record_snapshot(
            total_pnl=skill_pnl,
            total_volume=skill_volume,
            open_count=skill_executors,
            position_size=skill_exposure,
        )

        log.info(
            "TickEngine %s tick #%d complete (cost=$%.4f, response=%d chars)",
            self.agent_id, tick_num, cost, len(response_text),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_server(self) -> tuple[str | None, dict | None]:
        """Resolve the server for this agent.

        If config has server_name, use that directly.
        Otherwise fall back to chat-based resolution.
        """
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
                # Fall back to chat-based resolution
                from handlers.bots._shared import get_bots_client
                client, _ = await get_bots_client(self.chat_id)
                return client

            from config_manager import get_config_manager
            cm = get_config_manager()
            return cm.get_client(server_name)
        except Exception:
            log.exception("Failed to get API client for agent %s", self.agent_id)
            return None

    def _get_server_credentials(self) -> dict[str, str] | None:
        """Get server credentials for prompt injection."""
        _, server = self._resolve_server()
        if not server:
            return None
        return {
            "host": server["host"],
            "port": str(server["port"]),
            "username": server["username"],
            "password": server["password"],
        }

    async def _notify(self, message: str) -> None:
        """Send a notification to the user via Telegram."""
        if hasattr(self, "_bot") and self._bot:
            try:
                await self._bot.send_message(chat_id=self.chat_id, text=message)
            except Exception:
                log.exception("Failed to send notification to chat %s", self.chat_id)

    def get_info(self) -> dict[str, Any]:
        """Return a summary dict for display."""
        summary = self.tracker.get_summary()
        # Prefer live skill data over tracker-parsed data
        sd = self._last_skill_data
        return {
            "agent_id": self.agent_id,
            "strategy": self.strategy.name,
            "status": self.status,
            "tick_count": summary["total_ticks"],
            "daily_pnl": sd.get("total_pnl", summary["daily_pnl"]),
            "total_volume": sd.get("total_volume", summary.get("total_volume", 0)),
            "total_exposure": sd.get("total_exposure", summary["total_exposure"]),
            "daily_cost": summary["daily_cost"],
            "open_executors": len(sd.get("executors", [])) or summary["open_executors"],
            "frequency_sec": self.config.get("frequency_sec", 60),
            "last_tick_at": self._last_tick_at,
            "last_error": self._last_error,
            "connector": self.config.get("connector_name", ""),
            "pair": self.config.get("trading_pair", ""),
        }
