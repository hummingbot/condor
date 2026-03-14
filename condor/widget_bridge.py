"""Widget Bridge -- TCP server for agent ↔ Condor IPC.

Renders Telegram inline keyboards on behalf of the agent's MCP tools,
resolves button clicks back to the waiting tool call, and exposes
Condor internals (routines, servers, user context) to the agent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

WIDGET_TIMEOUT = 120  # seconds
TCP_READ_LIMIT = 1_048_576  # 1 MB


@dataclass
class _PendingWidget:
    chat_id: int
    message_id: int
    values: list[str]  # button values indexed by position
    future: asyncio.Future


class WidgetBridge:
    """Async TCP server that receives requests from MCP tools and
    renders them as Telegram inline keyboards or dispatches to
    internal Condor subsystems."""

    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._application = None  # telegram.ext.Application
        self._server: asyncio.Server | None = None
        self._port: int = 0
        self._pending: dict[str, _PendingWidget] = {}

    @property
    def port(self) -> int:
        return self._port

    async def start(self, bot: Bot, application=None) -> None:
        self._bot = bot
        self._application = application
        self._server = await asyncio.start_server(
            self._handle_connection, "127.0.0.1", 0
        )
        addr = self._server.sockets[0].getsockname()
        self._port = addr[1]
        log.info("Widget bridge listening on 127.0.0.1:%d", self._port)

    async def stop(self) -> None:
        # Cancel all pending futures
        for widget in list(self._pending.values()):
            if not widget.future.done():
                widget.future.cancel()
        self._pending.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        log.info("Widget bridge stopped")

    # --- Helpers ---

    def _get_user_data(self, chat_id: int) -> dict:
        """Get user_data dict for a chat, creating if needed."""
        if self._application is None:
            return {}
        if chat_id not in self._application.user_data:
            self._application.user_data[chat_id] = {}
        return self._application.user_data[chat_id]

    # --- TCP handler ---

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            data = await asyncio.wait_for(reader.read(TCP_READ_LIMIT), timeout=5)
            if not data:
                return

            request = json.loads(data.decode())
            method = request.get("method", "")

            if method == "send_buttons":
                result = await self._handle_send_buttons(
                    chat_id=request["chat_id"],
                    message=request["message"],
                    buttons=request["buttons"],
                    parse_mode=request.get("parse_mode"),
                )
            elif method == "send_notification":
                result = await self._handle_send_notification(
                    chat_id=request["chat_id"],
                    message=request["message"],
                    parse_mode=request.get("parse_mode"),
                )
            elif method == "manage_routines":
                result = await self._handle_manage_routines(
                    chat_id=request["chat_id"],
                    user_id=request.get("user_id"),
                    params=request.get("params", {}),
                )
            elif method == "manage_servers":
                result = await self._handle_manage_servers(
                    chat_id=request["chat_id"],
                    user_id=request.get("user_id"),
                    params=request.get("params", {}),
                )
            elif method == "get_user_context":
                result = await self._handle_get_user_context(
                    chat_id=request["chat_id"],
                    user_id=request.get("user_id"),
                )
            elif method == "get_session_usage":
                result = self._handle_get_session_usage(
                    chat_id=request["chat_id"],
                )
            elif method == "trading_agent_journal":
                result = self._handle_trading_agent_journal(
                    params=request.get("params", {}),
                )
            elif method == "manage_trading_agent":
                result = await self._handle_manage_trading_agent(
                    chat_id=request["chat_id"],
                    user_id=request.get("user_id"),
                    params=request.get("params", {}),
                )
            elif method == "manage_skills":
                result = await self._handle_manage_skills(
                    chat_id=request["chat_id"],
                    user_id=request.get("user_id"),
                    params=request.get("params", {}),
                )
            elif method == "manage_notes":
                result = self._handle_manage_notes(
                    chat_id=request["chat_id"],
                    params=request.get("params", {}),
                )
            else:
                result = {"error": f"Unknown method: {method}"}

            writer.write(json.dumps(result).encode())
            await writer.drain()
        except asyncio.TimeoutError:
            log.warning("Widget bridge: client read timeout")
        except Exception:
            log.exception("Widget bridge connection error")
            try:
                writer.write(json.dumps({"error": "internal error"}).encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # --- Widget Handlers ---

    async def _handle_send_buttons(
        self, chat_id: int, message: str, buttons: list[list[dict]],
        parse_mode: str | None = None,
    ) -> dict:
        assert self._bot is not None

        request_id = uuid.uuid4().hex[:8]

        # Build keyboard and collect values in order
        values: list[str] = []
        keyboard_rows: list[list[InlineKeyboardButton]] = []

        for row in buttons:
            kb_row: list[InlineKeyboardButton] = []
            for btn in row:
                idx = len(values)
                values.append(btn["value"])
                cb_data = f"agent:w:{request_id}:{idx}"
                kb_row.append(
                    InlineKeyboardButton(btn["label"], callback_data=cb_data)
                )
            keyboard_rows.append(kb_row)

        markup = InlineKeyboardMarkup(keyboard_rows)
        kwargs: dict = {"chat_id": chat_id, "text": message, "reply_markup": markup}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        sent = await self._bot.send_message(**kwargs)

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = _PendingWidget(
            chat_id=chat_id,
            message_id=sent.message_id,
            values=values,
            future=future,
        )

        try:
            selected = await asyncio.wait_for(future, timeout=WIDGET_TIMEOUT)
            return {"selected": selected}
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._pending.pop(request_id, None)
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=sent.message_id,
                    text=f"{message}\n\n(timed out)",
                )
            except Exception:
                pass
            return {"timeout": True}

    async def _handle_send_notification(
        self, chat_id: int, message: str, parse_mode: str | None = None
    ) -> dict:
        assert self._bot is not None
        kwargs: dict = {"chat_id": chat_id, "text": message}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        await self._bot.send_message(**kwargs)
        return {"sent": True}

    # --- Routines Handler ---

    async def _handle_manage_routines(
        self, chat_id: int, user_id: int | None, params: dict
    ) -> dict:
        from routines.base import discover_routines, get_routine

        action = params.get("action", "list")

        if action == "list":
            routines = discover_routines(force_reload=True)
            result = []
            for name, routine in sorted(routines.items()):
                result.append({
                    "name": name,
                    "description": routine.description,
                    "type": "continuous" if routine.is_continuous else "one-shot",
                })
            return {"routines": result}

        elif action == "describe":
            name = params.get("name")
            if not name:
                return {"error": "name is required"}
            routine = get_routine(name)
            if not routine:
                return {"error": f"Routine '{name}' not found"}
            fields = routine.get_fields()
            return {
                "name": name,
                "description": routine.description,
                "type": "continuous" if routine.is_continuous else "one-shot",
                "fields": fields,
            }

        elif action == "run":
            return await self._routine_run(chat_id, params)

        elif action == "schedule":
            return await self._routine_schedule(chat_id, params)

        elif action == "list_active":
            user_data = self._get_user_data(chat_id)
            instances = user_data.get("routine_instances", {})
            active = []
            for iid, inst in instances.items():
                if inst.get("status") == "running":
                    active.append({
                        "instance_id": iid,
                        "routine_name": inst.get("routine_name"),
                        "schedule": inst.get("schedule", {}),
                        "created_at": inst.get("created_at"),
                        "run_count": inst.get("run_count", 0),
                        "last_result": (inst.get("last_result") or "")[:200],
                        "config": inst.get("config", {}),
                    })
            return {"active_instances": active}

        elif action == "stop":
            return self._routine_stop(chat_id, params)

        else:
            return {"error": f"Unknown action: {action}"}

    async def _routine_run(self, chat_id: int, params: dict) -> dict:
        """Execute a routine once and return the result."""
        from routines.base import discover_routines

        name = params.get("name")
        if not name:
            return {"error": "name is required"}

        routines = discover_routines(force_reload=True)
        routine = routines.get(name)
        if not routine:
            return {"error": f"Routine '{name}' not found"}

        config_overrides = params.get("config") or {}

        # Build config: defaults + overrides
        try:
            default_config = routine.get_default_config().model_dump()
            default_config.update(config_overrides)
            config = routine.config_class(**default_config)
        except Exception as e:
            return {"error": f"Config error: {e}"}

        if routine.is_continuous:
            return {"error": "Use 'schedule' action with type='continuous' for continuous routines"}

        # Create mock context
        if self._application is None:
            return {"error": "Application not available"}

        class MockContext:
            def __init__(ctx):
                ctx._chat_id = chat_id
                ctx._instance_id = "mcp_run"
                if chat_id not in self._application.user_data:
                    self._application.user_data[chat_id] = {}
                ctx._user_data = self._application.user_data[chat_id]
                ctx.bot = self._application.bot
                ctx.application = self._application

            @property
            def user_data(ctx):
                return ctx._user_data

        ctx = MockContext()
        start = time.time()

        try:
            result = await asyncio.wait_for(
                routine.run_fn(config, ctx), timeout=120
            )
            result_text = str(result)[:2000] if result else "Completed (no output)"
        except asyncio.TimeoutError:
            result_text = "Error: routine timed out after 120s"
        except Exception as e:
            result_text = f"Error: {e}"

        duration = time.time() - start
        return {
            "result": result_text,
            "duration_sec": round(duration, 2),
            "routine": name,
        }

    async def _routine_schedule(self, chat_id: int, params: dict) -> dict:
        """Schedule a routine (interval, daily, once, or continuous)."""
        from handlers.routines import (
            _create_continuous_instance,
            _create_scheduled_instance,
            _generate_instance_id,
            _get_instances,
            _run_continuous_routine,
            _continuous_tasks,
        )
        from routines.base import get_routine

        name = params.get("name")
        if not name:
            return {"error": "name is required"}

        routine = get_routine(name)
        if not routine:
            return {"error": f"Routine '{name}' not found"}

        config_overrides = params.get("config") or {}
        schedule = params.get("schedule") or {}
        stype = schedule.get("type", "once")

        # Build config
        try:
            default_config = routine.get_default_config().model_dump()
            default_config.update(config_overrides)
            config_obj = routine.config_class(**default_config)
            config_dict = config_obj.model_dump()
        except Exception as e:
            return {"error": f"Config error: {e}"}

        if self._application is None:
            return {"error": "Application not available"}

        # For continuous routines
        if stype == "continuous" or (routine.is_continuous and stype in ("once", "continuous")):
            if not routine.is_continuous:
                return {"error": f"Routine '{name}' is not a continuous routine"}

            instance_id = _generate_instance_id()

            # Store instance in user_data
            user_data = self._get_user_data(chat_id)
            if "routine_instances" not in user_data:
                user_data["routine_instances"] = {}
            user_data["routine_instances"][instance_id] = {
                "routine_name": name,
                "config": config_dict.copy(),
                "schedule": {"type": "continuous"},
                "status": "running",
                "created_at": time.time(),
                "last_run_at": None,
                "last_result": None,
                "last_duration": None,
                "run_count": 0,
            }

            # Launch asyncio task
            task = asyncio.create_task(
                _run_continuous_routine(
                    self._application, instance_id, name, config_dict, chat_id
                )
            )
            _continuous_tasks[instance_id] = task

            return {
                "instance_id": instance_id,
                "routine": name,
                "schedule": {"type": "continuous"},
                "status": "started",
            }

        # For one-shot routines (once, interval, daily)
        if routine.is_continuous:
            return {"error": "Continuous routines can only be started with schedule type 'continuous'"}

        # Validate schedule
        if stype == "interval":
            interval_sec = schedule.get("interval_sec")
            if not interval_sec or not isinstance(interval_sec, (int, float)):
                return {"error": "interval_sec is required for interval schedule"}

        elif stype == "daily":
            daily_time = schedule.get("daily_time")
            if not daily_time:
                return {"error": "daily_time (HH:MM) is required for daily schedule"}
            try:
                hour, minute = map(int, daily_time.split(":"))
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError()
            except (ValueError, AttributeError):
                return {"error": f"Invalid daily_time: {daily_time}. Use HH:MM format."}

        # Build a mock context with job_queue for scheduling
        class ScheduleContext:
            def __init__(ctx):
                ctx.application = self._application
                ctx.job_queue = self._application.job_queue
                if chat_id not in self._application.user_data:
                    self._application.user_data[chat_id] = {}
                ctx.user_data = self._application.user_data[chat_id]
                ctx.bot = self._application.bot

        mock_ctx = ScheduleContext()
        instance_id = _create_scheduled_instance(
            mock_ctx, chat_id, name, config_dict, schedule
        )

        return {
            "instance_id": instance_id,
            "routine": name,
            "schedule": schedule,
            "status": "scheduled",
        }

    def _routine_stop(self, chat_id: int, params: dict) -> dict:
        """Stop a running routine instance."""
        from handlers.routines import _stop_instance

        instance_id = params.get("instance_id")
        if not instance_id:
            return {"error": "instance_id is required"}

        if self._application is None:
            return {"error": "Application not available"}

        # Build a mock context for _stop_instance
        class StopContext:
            def __init__(ctx):
                ctx.application = self._application
                ctx.job_queue = self._application.job_queue
                if chat_id not in self._application.user_data:
                    self._application.user_data[chat_id] = {}
                ctx.user_data = self._application.user_data[chat_id]

        mock_ctx = StopContext()
        stopped = _stop_instance(mock_ctx, chat_id, instance_id)
        if stopped:
            return {"stopped": True, "instance_id": instance_id}
        return {"error": f"Instance '{instance_id}' not found or already stopped"}

    # --- Servers Handler ---

    async def _handle_manage_servers(
        self, chat_id: int, user_id: int | None, params: dict
    ) -> dict:
        from config_manager import get_config_manager

        cm = get_config_manager()
        action = params.get("action", "list")

        if not user_id:
            return {"error": "user_id is required"}

        if action == "list":
            accessible = cm.get_accessible_servers(user_id)
            active_server = cm.get_chat_default_server(chat_id)
            servers = []
            for name in accessible:
                server = cm.get_server(name)
                if not server:
                    continue
                perm = cm.get_server_permission(user_id, name)
                servers.append({
                    "name": name,
                    "host": server["host"],
                    "port": server["port"],
                    "permission": perm.value if perm else "unknown",
                    "is_active": name == active_server,
                })
            return {"servers": servers, "active_server": active_server}

        elif action == "switch":
            name = params.get("name")
            if not name:
                return {"error": "name is required"}
            if not cm.has_server_access(user_id, name):
                return {"error": f"No access to server '{name}'"}
            cm.set_chat_default_server(chat_id, name)
            # Also update user_data preference
            user_data = self._get_user_data(chat_id)
            from condor.preferences import set_active_server
            set_active_server(user_data, name)
            return {"switched": True, "active_server": name}

        elif action == "status":
            name = params.get("name")
            if not name:
                # Check active server
                name = cm.get_chat_default_server(chat_id)
                if not name:
                    return {"error": "No active server"}
            if not cm.has_server_access(user_id, name):
                return {"error": f"No access to server '{name}'"}
            status = await cm.check_server_status(name)
            return {"server": name, **status}

        else:
            return {"error": f"Unknown action: {action}"}

    # --- User Context Handler ---

    async def _handle_get_user_context(
        self, chat_id: int, user_id: int | None
    ) -> dict:
        from config_manager import get_config_manager
        from condor.preferences import get_preferences

        cm = get_config_manager()
        user_data = self._get_user_data(chat_id)

        # Active server
        active_server = cm.get_chat_default_server(chat_id)

        # User role
        role = None
        is_admin = False
        if user_id:
            user_role = cm.get_user_role(user_id)
            role = user_role.value if user_role else None
            is_admin = cm.is_admin(user_id)

        # Active routine count
        instances = user_data.get("routine_instances", {})
        active_routine_count = sum(
            1 for inst in instances.values() if inst.get("status") == "running"
        )

        # Preferences (safe copy)
        try:
            prefs = get_preferences(user_data)
        except Exception:
            prefs = {}

        return {
            "active_server": active_server,
            "user_role": role,
            "is_admin": is_admin,
            "active_routine_count": active_routine_count,
            "preferences": prefs,
        }

    # --- Session Usage Handler ---

    def _handle_get_session_usage(self, chat_id: int) -> dict:
        from handlers.agents.session import get_session

        session = get_session(chat_id)
        if not session:
            return {"error": "No active session"}

        pct = round(session.tokens_used / session.context_window * 100) if session.context_window > 0 and session.tokens_used > 0 else 0
        return {
            "tokens_used": session.tokens_used,
            "input_tokens": session.input_tokens,
            "output_tokens": session.output_tokens,
            "context_window": session.context_window,
            "percent_used": pct,
            "cost_usd": session.cost_usd,
        }

    # --- Trading Agent Journal Handler ---

    def _handle_trading_agent_journal(self, params: dict) -> dict:
        from condor.trading_agent.engine import get_engine
        from condor.trading_agent.journal import JournalManager

        action = params.get("action", "read")
        agent_id = params.get("agent_id", "")
        if not agent_id:
            return {"error": "agent_id is required"}

        engine = get_engine(agent_id)
        session_dir = engine.session_dir if engine else None
        jm = JournalManager(agent_id, session_dir=session_dir)

        if action == "read":
            section = params.get("section", "recent")
            max_entries = params.get("max_entries", 30)
            if section == "full":
                return {"content": jm.read_full()}
            elif section == "learnings":
                return {"content": jm.read_learnings()}
            elif section in ("state", "summary"):
                return {"content": jm.read_state()}
            elif section == "runs":
                runs = jm.list_runs(limit=max_entries)
                return {"runs": runs}
            elif section.startswith("run:"):
                try:
                    tick_num = int(section.split(":", 1)[1])
                except (ValueError, IndexError):
                    return {"error": "Invalid run format. Use 'run:N' where N is the tick number."}
                content = jm.read_run_snapshot(tick_num)
                if not content:
                    return {"error": f"No run snapshot found for tick #{tick_num}"}
                return {"content": content}
            else:
                return {"content": jm.read_recent(max_entries=max_entries)}

        elif action == "write":
            entry_type = params.get("entry_type", "action")
            text = params.get("text", "")
            if not text:
                return {"error": "text is required"}

            if entry_type == "learning":
                jm.append_learning(text)
            elif entry_type == "state":
                jm.write_state(text)
            else:
                tick = params.get("tick", 0)
                reasoning = params.get("reasoning", "")
                risk_note = params.get("risk_note", "")
                jm.append_action(tick, text, reasoning, risk_note)
            return {"written": True}

        return {"error": f"Unknown action: {action}"}

    # --- Trading Agent Management Handler ---

    async def _handle_manage_trading_agent(
        self, chat_id: int, user_id: int | None, params: dict
    ) -> dict:
        from condor.trading_agent.strategy import StrategyStore
        from condor.trading_agent.engine import TickEngine, get_engine, get_all_engines

        action = params.get("action", "list_strategies")
        store = StrategyStore()

        if action == "list_strategies":
            strategies = store.list_all()
            return {
                "strategies": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "description": s.description,
                        "agent_key": s.agent_key,
                        "skills": s.skills,
                        "default_config": s.default_config,
                    }
                    for s in strategies
                ]
            }

        elif action == "get_strategy":
            strategy_id = params.get("strategy_id")
            if not strategy_id:
                return {"error": "strategy_id is required"}
            s = store.get(strategy_id)
            if not s:
                return {"error": f"Strategy '{strategy_id}' not found"}
            return {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "agent_key": s.agent_key,
                "instructions": s.instructions,
                "skills": s.skills,
                "default_config": s.default_config,
                "created_by": s.created_by,
                "created_at": s.created_at,
            }

        elif action == "update_strategy":
            strategy_id = params.get("strategy_id")
            if not strategy_id:
                return {"error": "strategy_id is required"}
            s = store.get(strategy_id)
            if not s:
                return {"error": f"Strategy '{strategy_id}' not found"}
            if params.get("name"):
                s.name = params["name"]
            if params.get("description"):
                s.description = params["description"]
            if params.get("instructions"):
                s.instructions = params["instructions"]
            if params.get("agent_key"):
                s.agent_key = params["agent_key"]
            if "skills" in params:
                s.skills = params["skills"]
            if "config" in params and params["config"]:
                s.default_config = params["config"]
            store.update(s)
            return {"updated": True, "strategy_id": s.id, "name": s.name}

        elif action == "create_strategy":
            name = params.get("name")
            description = params.get("description", "")
            instructions = params.get("instructions", "")
            if not name or not instructions:
                return {"error": "name and instructions are required"}
            strategy = store.create(
                name=name,
                description=description,
                agent_key=params.get("agent_key", "claude-code"),
                instructions=instructions,
                skills=params.get("skills"),
                default_config=params.get("config"),
                created_by=user_id or 0,
            )
            return {"created": True, "strategy_id": strategy.id, "name": strategy.name}

        elif action == "delete_strategy":
            strategy_id = params.get("strategy_id")
            if not strategy_id:
                return {"error": "strategy_id is required"}
            deleted = store.delete(strategy_id)
            return {"deleted": deleted}

        elif action == "list_agents":
            engines = get_all_engines()
            agents = []
            for eid, engine in engines.items():
                if engine.chat_id == chat_id:
                    agents.append(engine.get_info())
            return {"agents": agents}

        elif action == "start_agent":
            strategy_id = params.get("strategy_id")
            if not strategy_id:
                return {"error": "strategy_id is required"}
            strategy = store.get(strategy_id)
            if not strategy:
                return {"error": f"Strategy '{strategy_id}' not found"}

            config = dict(strategy.default_config)
            if params.get("config"):
                config.update(params["config"])

            import uuid
            agent_id = uuid.uuid4().hex[:8]

            engine = TickEngine(
                agent_id=agent_id,
                strategy=strategy,
                config=config,
                chat_id=chat_id,
                user_id=user_id or 0,
            )
            bot = self._bot
            await engine.start(bot=bot)

            # Store in user_data for persistence
            user_data = self._get_user_data(chat_id)
            if "ta_instances" not in user_data:
                user_data["ta_instances"] = {}
            user_data["ta_instances"][agent_id] = {
                "strategy_id": strategy_id,
                "config": config,
                "user_id": user_id,
                "status": "running",
            }

            return {"started": True, "agent_id": agent_id, "strategy": strategy.name}

        elif action == "stop_agent":
            agent_id = params.get("agent_id")
            if not agent_id:
                return {"error": "agent_id is required"}
            engine = get_engine(agent_id)
            if not engine:
                return {"error": f"Agent '{agent_id}' not found or not running"}
            await engine.stop()

            user_data = self._get_user_data(chat_id)
            instances = user_data.get("ta_instances", {})
            if agent_id in instances:
                instances[agent_id]["status"] = "stopped"

            return {"stopped": True, "agent_id": agent_id}

        elif action == "agent_status":
            agent_id = params.get("agent_id")
            if not agent_id:
                return {"error": "agent_id is required"}
            engine = get_engine(agent_id)
            if not engine:
                return {"error": f"Agent '{agent_id}' not found"}
            return engine.get_info()

        elif action == "pause_agent":
            agent_id = params.get("agent_id")
            if not agent_id:
                return {"error": "agent_id is required"}
            engine = get_engine(agent_id)
            if not engine:
                return {"error": f"Agent '{agent_id}' not found"}
            engine.pause()
            return {"paused": True, "agent_id": agent_id}

        elif action == "resume_agent":
            agent_id = params.get("agent_id")
            if not agent_id:
                return {"error": "agent_id is required"}
            engine = get_engine(agent_id)
            if not engine:
                return {"error": f"Agent '{agent_id}' not found"}
            engine.resume()
            return {"resumed": True, "agent_id": agent_id}

        elif action == "get_strategy":
            strategy_id = params.get("strategy_id")
            if not strategy_id:
                return {"error": "strategy_id is required"}
            strategy = store.get(strategy_id)
            if not strategy:
                return {"error": f"Strategy '{strategy_id}' not found"}
            from dataclasses import asdict
            return asdict(strategy)

        elif action == "update_strategy":
            strategy_id = params.get("strategy_id")
            if not strategy_id:
                return {"error": "strategy_id is required"}
            strategy = store.get(strategy_id)
            if not strategy:
                return {"error": f"Strategy '{strategy_id}' not found"}
            # Update only provided fields
            if params.get("name"):
                strategy.name = params["name"]
            if params.get("description") is not None:
                strategy.description = params["description"]
            if params.get("instructions") is not None:
                strategy.instructions = params["instructions"]
            if params.get("agent_key"):
                strategy.agent_key = params["agent_key"]
            if params.get("skills") is not None:
                strategy.skills = params["skills"]
            if params.get("config") is not None:
                strategy.default_config.update(params["config"])
            store.update(strategy)
            return {"updated": True, "strategy_id": strategy.id}

        elif action == "agent_tracker":
            agent_id = params.get("agent_id")
            if not agent_id:
                return {"error": "agent_id is required"}
            from condor.trading_agent.journal import JournalManager
            engine = get_engine(agent_id)
            session_dir = engine.session_dir if engine else None
            jm = JournalManager(agent_id, session_dir=session_dir)
            content = jm.read_full()
            summary = jm.get_summary_dict()
            return {"tracker_md": content, "summary": summary}

        elif action == "agent_journal":
            agent_id = params.get("agent_id")
            if not agent_id:
                return {"error": "agent_id is required"}
            from condor.trading_agent.journal import JournalManager
            engine = get_engine(agent_id)
            session_dir = engine.session_dir if engine else None
            jm = JournalManager(agent_id, session_dir=session_dir)
            return {
                "recent_actions": jm.read_recent(max_entries=30),
                "learnings": jm.read_learnings(),
                "entry_count": jm.entry_count(),
            }

        elif action == "agent_risk":
            agent_id = params.get("agent_id")
            if not agent_id:
                return {"error": "agent_id is required"}
            engine = get_engine(agent_id)
            if not engine:
                return {"error": f"Agent '{agent_id}' not found or not running"}
            risk_state = engine.risk.get_state(engine.journal)
            return {
                **risk_state.to_dict(),
                "limits": {
                    "max_position_size_quote": engine.risk.limits.max_position_size_quote,
                    "max_daily_loss_quote": engine.risk.limits.max_daily_loss_quote,
                    "max_drawdown_pct": engine.risk.limits.max_drawdown_pct,
                    "max_open_executors": engine.risk.limits.max_open_executors,
                    "max_single_order_quote": engine.risk.limits.max_single_order_quote,
                    "max_cost_per_day_usd": engine.risk.limits.max_cost_per_day_usd,
                    "cooldown_after_loss_sec": engine.risk.limits.cooldown_after_loss_sec,
                },
            }

        return {"error": f"Unknown action: {action}"}

    # --- Skills Management Handler ---

    async def _handle_manage_skills(
        self, chat_id: int, user_id: int | None, params: dict
    ) -> dict:
        from condor.trading_agent.providers import list_providers, get_provider
        from condor.trading_agent.skill_loader import list_skills as list_skill_files

        action = params.get("action", "list")

        if action == "list":
            # Combine data providers and SKILL.md files
            items = []
            for p in list_providers():
                items.append({
                    "name": p.name,
                    "is_core": p.is_core,
                    "type": "provider",
                })
            for s in list_skill_files():
                items.append({
                    "name": s.name,
                    "is_core": False,
                    "type": "skill",
                    "description": s.description,
                })
            return {"skills": items}

        elif action == "test":
            name = params.get("name")
            if not name:
                return {"error": "name is required"}

            # Try data provider first
            provider = get_provider(name)
            if provider:
                skill_params = params.get("params", {})
                try:
                    from handlers.bots._shared import get_bots_client
                    client, _ = await get_bots_client(chat_id)
                except Exception as e:
                    return {"error": f"Could not get API client: {e}"}
                try:
                    result = await provider.execute(client, skill_params)
                    return {
                        "name": result.name,
                        "summary": result.summary,
                        "data": result.data,
                    }
                except Exception as e:
                    return {"error": f"Provider execution failed: {e}"}

            # Try SKILL.md file
            from condor.trading_agent.skill_loader import load_skill, _render_placeholders
            skill_info = load_skill(name)
            if skill_info:
                config = params.get("params", {})
                rendered = _render_placeholders(skill_info.body, config)
                return {
                    "name": skill_info.name,
                    "description": skill_info.description,
                    "rendered_prompt": rendered,
                }

            return {"error": f"Skill or provider '{name}' not found"}

        return {"error": f"Unknown action: {action}"}

    # --- Notes Handler ---

    def _handle_manage_notes(self, chat_id: int, params: dict) -> dict:
        from condor.preferences import get_notes, get_note, set_note, delete_note

        action = params.get("action", "list")
        user_data = self._get_user_data(chat_id)

        if action == "list":
            return {"notes": get_notes(user_data)}

        elif action == "get":
            key = params.get("key")
            if not key:
                return {"error": "key is required"}
            value = get_note(user_data, key)
            if value is None:
                return {"error": f"Note '{key}' not found"}
            return {"key": key, "value": value}

        elif action == "set":
            key = params.get("key")
            value = params.get("value")
            if not key or value is None:
                return {"error": "key and value are required"}
            set_note(user_data, key, str(value))
            return {"saved": True, "key": key, "value": str(value)}

        elif action == "delete":
            key = params.get("key")
            if not key:
                return {"error": "key is required"}
            deleted = delete_note(user_data, key)
            if not deleted:
                return {"error": f"Note '{key}' not found"}
            return {"deleted": True, "key": key}

        return {"error": f"Unknown action: {action}"}

    # --- Resolution (called from Telegram callback handler) ---

    def resolve(self, request_id: str, button_index: int) -> bool:
        """Resolve a pending widget by button index. Returns True if resolved."""
        widget = self._pending.pop(request_id, None)
        if not widget or widget.future.done():
            return False

        if button_index < 0 or button_index >= len(widget.values):
            return False

        value = widget.values[button_index]
        widget.future.set_result(value)

        # Edit message to show selection (fire and forget)
        if self._bot:
            label = value
            asyncio.create_task(
                self._edit_selected(widget.chat_id, widget.message_id, label)
            )

        return True

    async def _edit_selected(
        self, chat_id: int, message_id: int, selected: str
    ) -> None:
        try:
            await self._bot.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )
        except Exception:
            pass

    def cancel_for_chat(self, chat_id: int) -> None:
        """Cancel all pending widgets for a chat."""
        to_remove = [
            rid for rid, w in self._pending.items() if w.chat_id == chat_id
        ]
        for rid in to_remove:
            widget = self._pending.pop(rid, None)
            if widget and not widget.future.done():
                widget.future.cancel()


# --- Singleton ---

_bridge: WidgetBridge | None = None


def get_widget_bridge() -> WidgetBridge:
    global _bridge
    if _bridge is None:
        _bridge = WidgetBridge()
    return _bridge
