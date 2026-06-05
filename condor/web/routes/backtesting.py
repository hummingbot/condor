from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config_manager import get_config_manager
from condor.backtest_store import get_backtest_store
from condor.web.auth import get_current_user
from condor.web.models import WebUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["backtesting"])


def _coerce_numeric_values(config: dict) -> dict:
    """Coerce string values that look numeric to int/float.

    Controller configs loaded from YAML sometimes store numbers as strings
    (e.g. "100" instead of 100). The backtesting engine does arithmetic on
    these values and will fail with 'int + str' errors if they aren't coerced.
    """
    out = {}
    for k, v in config.items():
        if isinstance(v, str):
            # Try int first, then float
            try:
                out[k] = int(v)
                continue
            except ValueError:
                pass
            try:
                out[k] = float(v)
                continue
            except ValueError:
                pass
        if isinstance(v, dict):
            v = _coerce_numeric_values(v)
        out[k] = v
    return out


class SubmitBacktestRequest(BaseModel):
    config_id: str
    start_time: int
    end_time: int
    backtesting_resolution: str = "1m"
    trade_cost: float = 0.0002


@router.post("/servers/{name}/backtesting/tasks")
async def submit_backtest_task(
    name: str,
    body: SubmitBacktestRequest,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)

    # Resolve config
    config = await client.controllers.get_controller_config(body.config_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Config '{body.config_id}' not found")

    result = await client.backtesting.submit_task(
        start_time=body.start_time,
        end_time=body.end_time,
        backtesting_resolution=body.backtesting_resolution,
        trade_cost=body.trade_cost,
        config=_coerce_numeric_values(config),
    )
    return result


@router.get("/servers/{name}/backtesting/tasks")
async def list_backtest_tasks(
    name: str,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    store = get_backtest_store()

    # Try to get live tasks from hummingbot-api
    try:
        client = await cm.get_client(name)
        live_tasks = await client.backtesting.list_tasks()
    except Exception:
        live_tasks = []

    # Merge with saved results
    live_ids = {t["task_id"] for t in live_tasks} if isinstance(live_tasks, list) else set()
    saved = store.list_results(name)

    # Add saved results that aren't in live tasks
    for entry in saved:
        if entry["task_id"] not in live_ids:
            live_tasks.append({
                "task_id": entry["task_id"],
                "status": "completed",
                "result": entry.get("result"),
                "config": entry.get("config"),
                "saved": True,
            })

    # Mark live completed tasks as saved if they are
    if isinstance(live_tasks, list):
        for task in live_tasks:
            tid = task.get("task_id", "")
            if store.get_result(tid):
                task["saved"] = True

    return live_tasks


@router.get("/servers/{name}/backtesting/tasks/{task_id}")
async def get_backtest_task(
    name: str,
    task_id: str,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    store = get_backtest_store()

    # Try live first
    try:
        client = await cm.get_client(name)
        result = await client.backtesting.get_task(task_id)

        # Auto-save completed results
        if isinstance(result, dict) and result.get("status") == "completed":
            store.save_result(name, task_id, result)
            result["saved"] = True

        return result
    except Exception:
        pass

    # Fallback to saved
    saved = store.get_result(task_id)
    if saved:
        return {**saved, "saved": True}

    raise HTTPException(status_code=404, detail="Task not found")


@router.delete("/servers/{name}/backtesting/tasks/{task_id}")
async def delete_backtest_task(
    name: str,
    task_id: str,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    store = get_backtest_store()
    store.delete_result(task_id)

    # Also try to delete from live
    try:
        client = await cm.get_client(name)
        return await client.backtesting.delete_task(task_id)
    except Exception:
        return {"deleted": True}


# ── Saved results endpoints ──


@router.get("/servers/{name}/backtesting/saved")
async def list_saved_results(
    name: str,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    store = get_backtest_store()
    return store.list_results(name)


@router.delete("/servers/{name}/backtesting/saved/{task_id}")
async def delete_saved_result(
    name: str,
    task_id: str,
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    store = get_backtest_store()
    deleted = store.delete_result(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved result not found")
    return {"deleted": True}
