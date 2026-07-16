"""REST surface for Condor-native executors (docs/condor-simple.md M1).

The runtime lives in this (main) process; MCP subprocesses and the
dashboard drive it through these routes. Hummingbot-api executors keep
their own namespace under /servers/{name}/executors.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from condor.executors import ops
from condor.executors.service import get_executor_runtime

router = APIRouter(tags=["native-executors"])


class StopExecutorRequest(BaseModel):
    keep_position: bool = True


def _http(e: ops.ExecutorOpError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.message)


@router.get("/executors")
async def list_executors(
    agent_id: Optional[str] = None,
    limit: int = 50,
):
    return ops.list_(get_executor_runtime(), agent_id=agent_id, limit=limit)


@router.get("/executors/performance")
async def executors_performance(
    group_by: str = "agent",
    agent_id: Optional[str] = None,
    agent_slug: Optional[str] = None,
):
    try:
        return ops.performance(
            get_executor_runtime(),
            group_by=group_by,
            agent_id=agent_id,
            agent_slug=agent_slug,
        )
    except ops.ExecutorOpError as e:
        raise _http(e)


@router.get("/executors/snapshot")
async def executors_snapshot(
    agent_slug: str | None = None,
    agent_id: str | None = None,
    venue_id: str | None = None,
    account: str | None = None,
):
    """One portfolio snapshot (§6.3): account view + attribution filters."""
    from condor.executors.snapshot import snapshot
    from condor.executors.wallets import account_store

    ref = account_store().resolve(venue_id, account) if venue_id else None
    return snapshot(
        get_executor_runtime(),
        account_ref=ref,
        agent_slug=agent_slug,
        agent_id=agent_id,
    )


@router.get("/executors/{executor_id}")
async def get_executor(executor_id: str):
    try:
        return ops.get(get_executor_runtime(), executor_id)
    except ops.ExecutorOpError as e:
        raise _http(e)


# Dashboard raw create is deliberately NOT exposed (§6.4): the route is
# reserved for the later direct-creation UI. Browser-side creation happens
# only through agent runs; direct creation ships via MCP (condor-direct
# capability). List/performance/get/stop remain the browser transport.


@router.post("/executors/{executor_id}/stop")
async def stop_executor(
    executor_id: str,
    req: StopExecutorRequest | None = None,
):
    keep = req.keep_position if req is not None else True
    try:
        return await ops.stop(
            get_executor_runtime(), executor_id=executor_id, keep_position=keep
        )
    except ops.ExecutorOpError as e:
        raise _http(e)
