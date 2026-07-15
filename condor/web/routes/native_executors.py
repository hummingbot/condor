"""REST surface for Condor-native executors (docs/condor-simple.md M1).

The runtime lives in this (main) process; MCP subprocesses and the
dashboard drive it through these routes. Hummingbot-api executors keep
their own namespace under /servers/{name}/executors.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.executors import ops
from condor.executors.service import get_executor_runtime
from condor.web.auth import get_current_user
from condor.web.models import WebUser

router = APIRouter(tags=["native-executors"])


class CreateExecutorRequest(BaseModel):
    type: str
    config: dict
    # Attribution: WHO / WHICH RUN / WHICH PLAYBOOK (see ExecutorConfig)
    agent_slug: str = ""
    agent_id: str = ""
    strategy: str = ""
    # Trade-notification routing (0 = outbox-only, no Telegram mirror)
    user_id: int = 0
    chat_id: int = 0


class StopExecutorRequest(BaseModel):
    keep_position: bool = True


def _http(e: ops.ExecutorOpError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.message)


@router.get("/executors")
async def list_executors(
    agent_id: Optional[str] = None,
    limit: int = 50,
    user: WebUser = Depends(get_current_user),
):
    return ops.list_(get_executor_runtime(), agent_id=agent_id, limit=limit)


@router.get("/executors/performance")
async def executors_performance(
    group_by: str = "agent",
    agent_id: Optional[str] = None,
    agent_slug: Optional[str] = None,
    user: WebUser = Depends(get_current_user),
):
    try:
        return ops.performance(
            get_executor_runtime(), group_by=group_by,
            agent_id=agent_id, agent_slug=agent_slug,
        )
    except ops.ExecutorOpError as e:
        raise _http(e)


@router.get("/executors/{executor_id}")
async def get_executor(executor_id: str, user: WebUser = Depends(get_current_user)):
    try:
        return ops.get(get_executor_runtime(), executor_id)
    except ops.ExecutorOpError as e:
        raise _http(e)


@router.post("/executors")
async def create_executor(
    req: CreateExecutorRequest, user: WebUser = Depends(get_current_user)
):
    try:
        return await ops.create(
            get_executor_runtime(),
            type=req.type, config=req.config,
            agent_slug=req.agent_slug, agent_id=req.agent_id, strategy=req.strategy,
            user_id=req.user_id, chat_id=req.chat_id,
        )
    except ops.ExecutorOpError as e:
        raise _http(e)


@router.post("/executors/{executor_id}/stop")
async def stop_executor(
    executor_id: str,
    req: StopExecutorRequest | None = None,
    user: WebUser = Depends(get_current_user),
):
    keep = req.keep_position if req is not None else True
    try:
        return await ops.stop(
            get_executor_runtime(), executor_id=executor_id, keep_position=keep
        )
    except ops.ExecutorOpError as e:
        raise _http(e)
