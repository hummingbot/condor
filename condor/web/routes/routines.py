"""Routines API routes — discover, run, schedule, and view routine results."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

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
async def get_instance_image(instance_id: str, user: WebUser = Depends(get_current_user)):
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


@router.get("/{routine_name:path}/reports")
async def get_routine_reports(
    routine_name: str,
    limit: int = Query(50, ge=1, le=200),
    user: WebUser = Depends(get_current_user),
):
    """Get reports generated by a specific routine."""
    reports, total = list_reports(source_type="routine", search=routine_name, limit=limit)
    # Filter to exact source_name match
    exact = [r for r in reports if r.get("source_name") == routine_name]
    return {"reports": exact, "total": len(exact)}
