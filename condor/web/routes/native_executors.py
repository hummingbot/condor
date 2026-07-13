"""REST surface for Condor-native executors (docs/condor-simple.md M1).

The runtime lives in this (main) process; MCP subprocesses and the
dashboard drive it through these routes. Hummingbot-api executors keep
their own namespace under /servers/{name}/executors.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from condor.executors.runtime import _EXECUTOR_TYPES
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


def _record_to_dict(record) -> dict:
    return {
        "id": record.id,
        "type": record.type,
        "status": record.status,
        "agent_slug": record.agent_slug,
        "agent_id": record.agent_id,
        "strategy": record.strategy,
        "config": record.config,
        "state": record.state,
        "close_reason": record.close_reason,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@router.get("/executors")
async def list_executors(
    agent_id: Optional[str] = None,
    limit: int = 50,
    user: WebUser = Depends(get_current_user),
):
    store = get_executor_runtime().store
    records = (
        store.load_by_agent(agent_id, limit=limit) if agent_id else store.list_all(limit=limit)
    )
    return {"executors": [_record_to_dict(r) for r in records],
            "running": get_executor_runtime().list_running()}


@router.get("/executors/performance")
async def executors_performance(
    group_by: str = "agent",
    agent_id: Optional[str] = None,
    agent_slug: Optional[str] = None,
    user: WebUser = Depends(get_current_user),
):
    from condor.executors.performance import GROUP_KEYS, aggregate_performance

    if group_by not in GROUP_KEYS:
        raise HTTPException(status_code=422, detail=f"group_by must be one of {GROUP_KEYS}")
    return {
        "group_by": group_by,
        "groups": aggregate_performance(
            get_executor_runtime().store, group_by=group_by,
            agent_id=agent_id, agent_slug=agent_slug,
        ),
    }


@router.get("/executors/{executor_id}")
async def get_executor(executor_id: str, user: WebUser = Depends(get_current_user)):
    record = get_executor_runtime().store.load(executor_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Executor not found")
    return _record_to_dict(record)


@router.post("/executors")
async def create_executor(
    req: CreateExecutorRequest, user: WebUser = Depends(get_current_user)
):
    if req.type not in _EXECUTOR_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown executor type '{req.type}' (known: {sorted(_EXECUTOR_TYPES)})",
        )
    config_cls, _ = _EXECUTOR_TYPES[req.type]
    runtime = get_executor_runtime()

    config_data = dict(req.config)
    config_data["agent_slug"] = req.agent_slug
    config_data["agent_id"] = req.agent_id
    config_data["strategy"] = req.strategy
    config_data["user_id"] = req.user_id
    config_data["chat_id"] = req.chat_id

    # Swaps: fill the declared notional from a live quote when omitted, so
    # the risk declaration below is always computable.
    if req.type == "swap" and not config_data.get("notional_quote"):
        try:
            quote = await runtime.gateway.quote_swap(
                chain_network=config_data["chain_network"],
                base_token=config_data["base_token"],
                quote_token=config_data["quote_token"],
                amount=float(config_data["amount"]),
                side=config_data["side"],
                connector=config_data.get("connector"),
            )
        except KeyError as e:
            raise HTTPException(status_code=422, detail=f"Missing swap config field: {e}")
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"Cannot price swap via gateway: {e}"
            )
        config_data["notional_quote"] = str(
            Decimal(str(quote["price"])) * Decimal(str(config_data["amount"]))
        )

    try:
        config = config_cls(**config_data)
        declaration = config.risk_declaration()
    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))

    executor_id = runtime.create_executor(config)
    return {
        "id": executor_id,
        "status": "PENDING",
        "risk_declaration": {
            "max_notional_quote": float(declaration.max_notional_quote),
            "max_loss_quote": float(declaration.max_loss_quote),
        },
    }


@router.post("/executors/{executor_id}/stop")
async def stop_executor(
    executor_id: str,
    req: StopExecutorRequest | None = None,
    user: WebUser = Depends(get_current_user),
):
    runtime = get_executor_runtime()
    record = runtime.store.load(executor_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Executor not found")
    keep = req.keep_position if req is not None else True
    try:
        runtime.stop_executor(executor_id, keep_position=keep)
    except KeyError:
        # Known but not running in this process (e.g. pre-restart row that
        # reconcile settled) — nothing to stop.
        raise HTTPException(
            status_code=409,
            detail=f"Executor {executor_id} is not running (status: {record.status})",
        )
    return {"id": executor_id, "stopping": True, "keep_position": keep}
