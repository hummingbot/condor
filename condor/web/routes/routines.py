"""Routines API routes — discover, run, schedule, and view routine results."""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from condor import routine_hooks
from condor.reports import list_reports
from condor.routine_store import get_routine_store
from condor.web.auth import get_current_user
from condor.web.models import WebUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/routines", tags=["routines"])


# ── Request / Response Models ──


class RunRequest(BaseModel):
    config: dict = {}


class ScheduleRequest(BaseModel):
    config: dict = {}
    interval_sec: int = 300


class RunRequestV2(BaseModel):
    routine_name: str
    server_name: str
    config: dict = {}


class ScheduleRequestV2(BaseModel):
    routine_name: str
    server_name: str
    config: dict = {}
    interval_sec: int = 300


class HookEmail(BaseModel):
    enabled: bool = False
    recipients: list[str] = []


class HookTelegram(BaseModel):
    enabled: bool = False
    chat_ids: list[str] = []


class HooksRequest(BaseModel):
    email: HookEmail = HookEmail()
    telegram: HookTelegram = HookTelegram()
    trigger: str = "success"


# ── Routes ──


@router.get("")
async def list_routines(user: WebUser = Depends(get_current_user)):
    """List all discovered routines with their fields."""
    store = get_routine_store()
    return store.list_routines()


@router.get("/instances")
async def list_instances(user: WebUser = Depends(get_current_user)):
    """List all active routine instances."""
    store = get_routine_store()
    return store.list_instances()


@router.get("/instances/{instance_id}")
async def get_instance(instance_id: str, user: WebUser = Depends(get_current_user)):
    """Get instance detail including last result."""
    store = get_routine_store()
    inst = store.get_instance(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    return inst


@router.get("/instances/{instance_id}/image")
async def get_instance_image(
    instance_id: str, user: WebUser = Depends(get_current_user)
):
    """Serve the chart PNG for an instance result."""
    store = get_routine_store()
    result = store.get_result(instance_id)
    if not result or not result.chart_image:
        raise HTTPException(404, "No chart image available")
    return Response(content=result.chart_image, media_type="image/png")


@router.post("/servers/{server_name}/{routine_name}/run")
async def run_routine(
    server_name: str,
    routine_name: str,
    body: RunRequest,
    user: WebUser = Depends(get_current_user),
):
    """Execute a one-shot routine. Returns instance_id for polling."""
    store = get_routine_store()
    try:
        instance_id = await store.execute(
            routine_name=routine_name,
            config=body.config,
            server_name=server_name,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"instance_id": instance_id}


@router.post("/servers/{server_name}/{routine_name}/schedule")
async def schedule_routine(
    server_name: str,
    routine_name: str,
    body: ScheduleRequest,
    user: WebUser = Depends(get_current_user),
):
    """Schedule a routine at an interval. Returns instance_id."""
    store = get_routine_store()
    try:
        instance_id = await store.schedule(
            routine_name=routine_name,
            config=body.config,
            server_name=server_name,
            interval_sec=body.interval_sec,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"instance_id": instance_id}


@router.post("/run")
async def run_routine_v2(
    body: RunRequestV2,
    user: WebUser = Depends(get_current_user),
):
    """Execute a routine (supports names with slashes like agent/routine)."""
    store = get_routine_store()
    try:
        instance_id = await store.execute(
            routine_name=body.routine_name,
            config=body.config,
            server_name=body.server_name,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"instance_id": instance_id}


@router.post("/schedule")
async def schedule_routine_v2(
    body: ScheduleRequestV2,
    user: WebUser = Depends(get_current_user),
):
    """Schedule a routine (supports names with slashes like agent/routine)."""
    store = get_routine_store()
    try:
        instance_id = await store.schedule(
            routine_name=body.routine_name,
            config=body.config,
            server_name=body.server_name,
            interval_sec=body.interval_sec,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"instance_id": instance_id}


@router.post("/instances/{instance_id}/stop")
async def stop_instance(instance_id: str, user: WebUser = Depends(get_current_user)):
    """Stop a running or scheduled instance."""
    store = get_routine_store()
    if not store.stop(instance_id):
        raise HTTPException(404, "Instance not found")
    return {"stopped": True}


@router.get("/hooks/status")
async def hooks_status(user: WebUser = Depends(get_current_user)):
    """Report whether SMTP (email channel) is configured via env vars."""
    return {"smtp_configured": routine_hooks.smtp_configured()}


@router.get("/{routine_name:path}/hooks")
async def get_hooks(routine_name: str, user: WebUser = Depends(get_current_user)):
    """Get the post-execution hook config for a routine."""
    cfg = routine_hooks.load_hooks(routine_name)
    return cfg if cfg is not None else routine_hooks._default_config()


@router.put("/{routine_name:path}/hooks")
async def put_hooks(
    routine_name: str,
    body: HooksRequest,
    user: WebUser = Depends(get_current_user),
):
    """Save the post-execution hook config for a routine."""
    return routine_hooks.save_hooks(routine_name, body.model_dump())


@router.get("/options/{source}")
async def get_field_options(
    source: str,
    server: str = Query("local", alias="server"),
    user: WebUser = Depends(get_current_user),
):
    """Return dynamic options for routine config fields (e.g. controller_configs)."""
    if source == "controller_configs":
        try:
            from config_manager import get_config_manager

            cm = get_config_manager()
            client = await cm.get_client(server)
            if not client:
                return {"options": []}
            configs = await client.controllers.list_controller_configs()
            names = [c.get("id") or c.get("name", "") for c in (configs or [])]
            return {"options": sorted(n for n in names if n)}
        except Exception as e:
            log.warning(f"Failed to fetch controller configs: {e}")
            return {"options": []}
    return {"options": []}


@router.get("/{routine_name:path}/source")
async def get_routine_source(
    routine_name: str,
    user: WebUser = Depends(get_current_user),
):
    """Return the source code of a routine."""
    store = get_routine_store()
    all_routines = store._discover_all()
    routine = all_routines.get(routine_name)
    if not routine:
        raise HTTPException(404, "Routine not found")
    try:
        source_file = inspect.getfile(routine.run_fn)
        source_path = Path(source_file).resolve()
        routines_dir = Path("routines").resolve()
        if not str(source_path).startswith(str(routines_dir)):
            raise HTTPException(403, "Source not available")
        source = source_path.read_text()
        return {"filename": source_path.name, "source": source}
    except (TypeError, OSError) as e:
        raise HTTPException(404, f"Source not available: {e}")


@router.get("/{routine_name:path}/reports")
async def get_routine_reports(
    routine_name: str,
    limit: int = Query(50, ge=1, le=200),
    user: WebUser = Depends(get_current_user),
):
    """Get reports generated by a specific routine."""
    # Agent routines are prefixed (e.g. "agent_slug/routine_name") but reports
    # may be saved with just the base name. Match both.
    base_name = routine_name.split("/")[-1] if "/" in routine_name else routine_name
    reports, total = list_reports(search=base_name, limit=limit)
    # Filter to exact source_name match (full prefixed or base name)
    exact = [r for r in reports if r.get("source_name") in (routine_name, base_name)]
    return {"reports": exact, "total": len(exact)}
