"""Shared in-memory store for routine instances and results.

The single registry the web API and the MCP routines tool both read:
instances, scheduled runs, and results.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import time
import traceback
from pathlib import Path
from typing import Any

import condor.reports as reports
from routines.base import (
    RoutineResult,
    discover_routines,
    discover_routines_from_path,
    get_routine,
    normalize_result,
)

logger = logging.getLogger(__name__)


def _agent_of(routine) -> str:
    """Which assistant produced this routine's reports.

    A routine's ``source`` is ``"agent:<slug>"`` for an agent/expert-local routine
    or ``"global"`` for the shared/condor library — so reports are attributed to
    the owning expert, else to the chat ``condor``.
    """
    src = getattr(routine, "source", "global") or "global"
    return src.split(":", 1)[1] if src.startswith("agent:") else "condor"


class WebRoutineContext:
    """Lightweight run context handed to routines: preferences only.

    Chat delivery died with the retired bot surface (§9.1) — routines report
    via condor.reports / notify(), never a transport handle.
    """

    def __init__(self, server_name: str):
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

    # ── Discovery ──

    def _discover_all(self) -> dict[str, "RoutineInfo"]:
        """Discover routines: the general library (root ``routines/``) + each agent's.

        The chat ``condor`` sees the general library plus every agent's routines
        (prefixed). Each agent's own routines stay isolated under its slug.

        Discovery is mtime-cached in routines.base: only new/changed files are
        (re)imported, so this stays cheap on every list call while edits are
        still picked up without a restart.
        """
        all_routines = dict(discover_routines())

        # Scan agents/*/routines/
        agents_dir = Path(__file__).resolve().parent.parent / "agents"
        if agents_dir.exists():
            for agent_dir in sorted(agents_dir.iterdir()):
                routines_path = agent_dir / "routines"
                if not routines_path.is_dir():
                    continue
                slug = agent_dir.name
                agent_routines = discover_routines_from_path(
                    routines_path, agent_slug=slug
                )
                for rname, rinfo in agent_routines.items():
                    # Shallow-copy before prefixing: the RoutineInfo is shared
                    # with the discovery cache and must keep its bare name.
                    prefixed_info = copy.copy(rinfo)
                    prefixed_info.name = f"{slug}/{rname}"
                    all_routines[prefixed_info.name] = prefixed_info

        return all_routines

    def _get_report_counts(self) -> dict[str, int]:
        """Get report count per routine source_name."""
        try:
            from condor.reports import list_reports

            reports, _ = list_reports(limit=1000)
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
            out.append(
                {
                    "name": name,
                    "description": info.description,
                    "is_continuous": info.is_continuous,
                    "category": info.category,
                    "source": info.source,
                    "fields": info.get_fields(),
                    "last_modified": info.last_modified,
                    "report_count": report_counts.get(name, 0)
                    or report_counts.get(name.split("/")[-1], 0),
                }
            )
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
        # Ensure error is always present in response
        entry.setdefault("error", None)
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

    def _new_instance_meta(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
        source: str,
        **extra,
    ) -> dict:
        """Fresh instance-metadata dict shared by execute/start_continuous/schedule."""
        return {
            "routine_name": routine_name,
            "config": config,
            "status": "running",
            "source": source,
            "server_name": server_name,
            "created_at": time.time(),
            "last_run_at": None,
            "last_result": None,
            "last_duration": None,
            "run_count": 0,
            **extra,
        }

    async def _execute_and_record(
        self,
        instance_id: str,
        routine,
        config: dict,
        server_name: str,
        *,
        status_after: str,
        failed_status: str | None = None,
    ) -> None:
        """Run a routine once, store the result, update instance metadata.

        Shared by one-shot, continuous and scheduled runs — the entry points
        only differ in loop/cancellation semantics. ``status_after`` is the
        instance status recorded after the run; ``failed_status`` (defaults to
        ``status_after``) is used when the run raises. ``CancelledError`` is
        recorded as a clean "Stopped by user" run so a stopped continuous
        routine still stores its final result.
        """
        ctx = WebRoutineContext(server_name)
        start = time.time()
        reports.reset_last_report_id()
        error_msg = None
        failed = False
        try:
            cfg = routine.config_class(**config)
            with reports.attribute_to(_agent_of(routine)):
                raw = await routine.run_fn(cfg, ctx)
            result = normalize_result(raw)
        except asyncio.CancelledError:
            result = RoutineResult(text="Stopped by user")
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(
                f"Routine {routine.name}[{instance_id}] failed: {type(e).__name__}: {e}\n{tb}"
            )
            error_msg = f"{type(e).__name__}: {e}"
            result = RoutineResult(text=f"Error: {error_msg}\n\n{tb}")
            failed = True

        duration = time.time() - start
        self._results[instance_id] = result

        if instance_id in self._instances:
            self._instances[instance_id].update(
                {
                    "status": (
                        (failed_status or status_after) if failed else status_after
                    ),
                    "last_run_at": time.time(),
                    "last_result": result.text[:500],
                    "last_duration": duration,
                    "run_count": self._instances[instance_id].get("run_count", 0) + 1,
                    "error": error_msg,
                }
            )

    def _resolve_routine(self, routine_name: str):
        """Resolve a routine by name, supporting 'agent_slug/routine_name' format."""
        # Try global first
        routine = get_routine(routine_name)
        if routine:
            return routine
        # Try agent format: slug/name
        if "/" in routine_name:
            slug, rname = routine_name.split("/", 1)
            agents_dir = (
                Path(__file__).resolve().parent.parent / "agents" / slug / "routines"
            )
            agent_routines = discover_routines_from_path(agents_dir, agent_slug=slug)
            return agent_routines.get(rname)
        return None

    async def execute(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
    ) -> str:
        """Run a one-shot routine from the web. Returns instance_id."""
        routine = self._resolve_routine(routine_name)
        if not routine:
            raise ValueError(f"Routine '{routine_name}' not found")

        instance_id = self._gen_id()
        self._instances[instance_id] = self._new_instance_meta(
            routine_name, config, server_name, source="web"
        )

        task = asyncio.create_task(
            self._run_oneshot(instance_id, routine, config, server_name)
        )
        self._tasks[instance_id] = task
        return instance_id

    async def _run_oneshot(
        self,
        instance_id: str,
        routine,
        config: dict,
        server_name: str,
    ) -> None:
        await self._execute_and_record(
            instance_id,
            routine,
            config,
            server_name,
            status_after="completed",
            failed_status="failed",
        )

    async def start_continuous(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
    ) -> str:
        """Start a continuous routine as a background task. Returns instance_id."""
        routine = self._resolve_routine(routine_name)
        if not routine:
            raise ValueError(f"Routine '{routine_name}' not found")
        if not routine.is_continuous:
            raise ValueError(
                f"Routine '{routine_name}' is not continuous — use execute() instead"
            )

        instance_id = self._gen_id()
        self._instances[instance_id] = self._new_instance_meta(
            routine_name, config, server_name, source="mcp"
        )

        task = asyncio.create_task(
            self._run_continuous(instance_id, routine, config, server_name)
        )
        self._tasks[instance_id] = task
        return instance_id

    async def _run_continuous(
        self,
        instance_id: str,
        routine,
        config: dict,
        server_name: str,
    ) -> None:
        await self._execute_and_record(
            instance_id,
            routine,
            config,
            server_name,
            status_after="stopped",
        )

    async def schedule(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
        interval_sec: int,
    ) -> str:
        """Schedule a routine to repeat at interval_sec. Returns instance_id."""
        routine = self._resolve_routine(routine_name)
        if not routine:
            raise ValueError(f"Routine '{routine_name}' not found")

        instance_id = self._gen_id()
        self._instances[instance_id] = self._new_instance_meta(
            routine_name,
            config,
            server_name,
            source="web",
            status="scheduled",
            schedule={"type": "interval", "interval_sec": interval_sec},
        )

        task = asyncio.create_task(
            self._run_scheduled(
                instance_id, routine, config, server_name, interval_sec
            )
        )
        self._tasks[instance_id] = task
        return instance_id

    async def _run_scheduled(
        self,
        instance_id: str,
        routine,
        config: dict,
        server_name: str,
        interval_sec: int,
    ) -> None:
        try:
            while instance_id in self._instances:
                await self._execute_and_record(
                    instance_id,
                    routine,
                    config,
                    server_name,
                    status_after="scheduled",
                )
                # A cancel that lands mid-run is swallowed (and recorded) by
                # _execute_and_record; stop() removed the instance, so bail out
                # instead of sleeping through one more interval.
                if instance_id not in self._instances:
                    break
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
