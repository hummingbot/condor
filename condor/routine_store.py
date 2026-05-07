"""Shared in-memory store for routine instances and results.

Bridges Telegram handler and web API so both can see
the same instances, schedule runs, and read results.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from pathlib import Path

from routines.base import RoutineResult, discover_routines, discover_routines_from_path, get_routine, normalize_result

logger = logging.getLogger(__name__)


class WebRoutineContext:
    """Lightweight context so routines can run without Telegram."""

    def __init__(self, server_name: str, bot=None, chat_id: int = 0):
        self._chat_id = chat_id
        self.bot = bot
        self._user_data: dict[str, Any] = {
            "preferences": {"general": {"active_server": server_name}},
        }

    @property
    def user_data(self) -> dict:
        return self._user_data


class RoutineStore:
    """Singleton store for routine instances and results."""

    def __init__(self) -> None:
        self._instances: dict[str, dict] = {}
        self._results: dict[str, RoutineResult] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._bot = None  # Telegram bot instance, set via set_bot()

    def set_bot(self, bot) -> None:
        """Inject the Telegram bot so web-triggered routines can send messages."""
        self._bot = bot

    # ── Discovery ──

    def _discover_all(self) -> dict[str, "RoutineInfo"]:
        """Discover global routines + agent routines, merged into one dict."""
        all_routines = dict(discover_routines())

        # Scan trading_agents/*/routines/
        agents_dir = Path(__file__).resolve().parent.parent / "trading_agents"
        if agents_dir.exists():
            for agent_dir in sorted(agents_dir.iterdir()):
                routines_path = agent_dir / "routines"
                if not routines_path.is_dir():
                    continue
                slug = agent_dir.name
                agent_routines = discover_routines_from_path(routines_path, agent_slug=slug)
                for rname, rinfo in agent_routines.items():
                    prefixed = f"{slug}/{rname}"
                    rinfo.name = prefixed
                    all_routines[prefixed] = rinfo

        return all_routines

    def _get_report_counts(self) -> dict[str, int]:
        """Get report count per routine source_name."""
        try:
            from condor.reports import list_reports
            reports, _ = list_reports(source_type="routine", limit=1000)
            counts: dict[str, int] = {}
            for r in reports:
                sn = r.get("source_name", "")
                if sn:
                    counts[sn] = counts.get(sn, 0) + 1
            return counts
        except Exception:
            return {}

    def list_routines(self) -> list[dict]:
        all_routines = self._discover_all()
        report_counts = self._get_report_counts()
        out = []
        for name, info in all_routines.items():
            out.append({
                "name": name,
                "description": info.description,
                "is_continuous": info.is_continuous,
                "category": info.category,
                "source": info.source,
                "fields": info.get_fields(),
                "report_count": report_counts.get(name, 0),
            })
        return out

    # ── Instances ──

    def list_instances(self) -> list[dict]:
        out = []
        for iid, meta in self._instances.items():
            entry = {"instance_id": iid, **meta}
            if iid in self._results:
                entry["has_result"] = True
            out.append(entry)
        return out

    def get_instance(self, instance_id: str) -> dict | None:
        meta = self._instances.get(instance_id)
        if not meta:
            return None
        entry = {"instance_id": instance_id, **meta}
        result = self._results.get(instance_id)
        if result:
            entry["has_result"] = True
            entry["result_text"] = result.text[:2000]
            entry["has_chart"] = result.chart_image is not None
            entry["table_data"] = result.table_data
            entry["table_columns"] = result.table_columns
            entry["sections"] = result.sections
        return entry

    def add_instance(self, instance_id: str, metadata: dict) -> None:
        self._instances[instance_id] = metadata

    def remove_instance(self, instance_id: str) -> None:
        self._instances.pop(instance_id, None)
        self._tasks.pop(instance_id, None)

    # ── Results ──

    def store_result(self, instance_id: str, result: RoutineResult) -> None:
        self._results[instance_id] = result

    def get_result(self, instance_id: str) -> RoutineResult | None:
        return self._results.get(instance_id)

    # ── Execution ──

    def _gen_id(self) -> str:
        return hashlib.md5(f"{time.time()}{id(object())}".encode()).hexdigest()[:8]

    def _resolve_routine(self, routine_name: str):
        """Resolve a routine by name, supporting 'agent_slug/routine_name' format."""
        # Try global first
        routine = get_routine(routine_name)
        if routine:
            return routine
        # Try agent format: slug/name
        if "/" in routine_name:
            slug, rname = routine_name.split("/", 1)
            agents_dir = Path(__file__).resolve().parent.parent / "trading_agents" / slug / "routines"
            agent_routines = discover_routines_from_path(agents_dir, agent_slug=slug)
            return agent_routines.get(rname)
        return None

    async def execute(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
        user_id: int = 0,
    ) -> str:
        """Run a one-shot routine from the web. Returns instance_id."""
        routine = self._resolve_routine(routine_name)
        if not routine:
            raise ValueError(f"Routine '{routine_name}' not found")

        instance_id = self._gen_id()
        self._instances[instance_id] = {
            "routine_name": routine_name,
            "config": config,
            "status": "running",
            "source": "web",
            "server_name": server_name,
            "user_id": user_id,
            "created_at": time.time(),
            "last_run_at": None,
            "last_result": None,
            "last_duration": None,
            "run_count": 0,
        }

        task = asyncio.create_task(
            self._run_oneshot(instance_id, routine, config, server_name, user_id)
        )
        self._tasks[instance_id] = task
        return instance_id

    async def _run_oneshot(self, instance_id: str, routine, config: dict, server_name: str, user_id: int = 0) -> None:
        ctx = WebRoutineContext(server_name, bot=self._bot, chat_id=user_id)
        start = time.time()
        try:
            cfg = routine.config_class(**config)
            raw = await routine.run_fn(cfg, ctx)
            result = normalize_result(raw)
        except Exception as e:
            logger.error(f"Web routine {routine.name}[{instance_id}] failed: {e}")
            result = RoutineResult(text=f"Error: {e}")

        duration = time.time() - start
        self._results[instance_id] = result

        if instance_id in self._instances:
            self._instances[instance_id].update({
                "status": "completed",
                "last_run_at": time.time(),
                "last_result": result.text[:500],
                "last_duration": duration,
                "run_count": self._instances[instance_id].get("run_count", 0) + 1,
            })

    async def schedule(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
        interval_sec: int,
        user_id: int = 0,
    ) -> str:
        """Schedule a routine to repeat at interval_sec. Returns instance_id."""
        routine = self._resolve_routine(routine_name)
        if not routine:
            raise ValueError(f"Routine '{routine_name}' not found")

        instance_id = self._gen_id()
        self._instances[instance_id] = {
            "routine_name": routine_name,
            "config": config,
            "status": "scheduled",
            "source": "web",
            "server_name": server_name,
            "user_id": user_id,
            "schedule": {"type": "interval", "interval_sec": interval_sec},
            "created_at": time.time(),
            "last_run_at": None,
            "last_result": None,
            "last_duration": None,
            "run_count": 0,
        }

        task = asyncio.create_task(
            self._run_scheduled(instance_id, routine, config, server_name, interval_sec, user_id)
        )
        self._tasks[instance_id] = task
        return instance_id

    async def _run_scheduled(
        self, instance_id: str, routine, config: dict, server_name: str, interval_sec: int, user_id: int = 0
    ) -> None:
        try:
            while instance_id in self._instances:
                ctx = WebRoutineContext(server_name, bot=self._bot, chat_id=user_id)
                start = time.time()
                try:
                    cfg = routine.config_class(**config)
                    raw = await routine.run_fn(cfg, ctx)
                    result = normalize_result(raw)
                except Exception as e:
                    logger.error(f"Scheduled routine {routine.name}[{instance_id}] error: {e}")
                    result = RoutineResult(text=f"Error: {e}")

                duration = time.time() - start
                self._results[instance_id] = result

                if instance_id in self._instances:
                    self._instances[instance_id].update({
                        "status": "scheduled",
                        "last_run_at": time.time(),
                        "last_result": result.text[:500],
                        "last_duration": duration,
                        "run_count": self._instances[instance_id].get("run_count", 0) + 1,
                    })

                await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            logger.info(f"Scheduled routine {instance_id} cancelled")

    def stop(self, instance_id: str) -> bool:
        task = self._tasks.pop(instance_id, None)
        if task and not task.done():
            task.cancel()

        if instance_id in self._instances:
            self._instances[instance_id]["status"] = "stopped"
            del self._instances[instance_id]
            return True
        return False


# Singleton
_store: RoutineStore | None = None


def get_routine_store() -> RoutineStore:
    global _store
    if _store is None:
        _store = RoutineStore()
    return _store
