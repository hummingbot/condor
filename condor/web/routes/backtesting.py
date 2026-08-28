from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.backtest_store import get_backtest_store
from condor.backtesting import coerce_controller_config, normalize_backtest_task
from condor.web.auth import get_current_user, require_server_access
from condor.web.models import WebUser
from config_manager import ServerPermission, get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["backtesting"])


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
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    client = await cm.get_client(name)

    # Resolve config
    config = await client.controllers.get_controller_config(body.config_id)
    if not config:
        raise HTTPException(
            status_code=404, detail=f"Config '{body.config_id}' not found"
        )

    result = await client.backtesting.submit_task(
        start_time=body.start_time,
        end_time=body.end_time,
        backtesting_resolution=body.backtesting_resolution,
        trade_cost=body.trade_cost,
        config=coerce_controller_config(config),
    )
    return result


@router.get("/servers/{name}/backtesting/tasks")
async def list_backtest_tasks(
    name: str,
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    store = get_backtest_store()

    # Try to get live tasks from hummingbot-api
    try:
        client = await cm.get_client(name)
        live_tasks = await client.backtesting.list_tasks()
    except Exception:
        live_tasks = []

    if not isinstance(live_tasks, list):
        live_tasks = []

    # Summaries, never payloads. This response used to carry every saved
    # envelope's ``result`` -- 1 GB for 22 runs on ``local`` -- to fill a list
    # that reads six fields per row, and the dashboard polls it every 5s.
    entries = [_live_entry(task, store) for task in live_tasks]
    live_ids = {t.get("task_id") for t in live_tasks}
    entries.extend(
        _summary_entry(summary)
        for summary in store.list_summaries(name)
        if summary["task_id"] not in live_ids
    )
    return entries


@router.get("/servers/{name}/backtesting/tasks/{task_id}")
async def get_backtest_task(
    name: str,
    task_id: str,
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    store = get_backtest_store()

    # Try live first
    try:
        client = await cm.get_client(name)
        result = await client.backtesting.get_task(task_id)

        # Auto-save completed results
        if isinstance(result, dict) and result.get("status") == "completed":
            store.save_result(name, task_id, result)
            result["saved"] = True

        return normalize_backtest_task(result)
    except Exception:
        pass

    # Fallback to saved. Ownership is checked against the *summary*: it is the
    # tier that survives retention, so a pruned run stays scoped to its server
    # instead of falling through the "no payload" hole into everyone's reach.
    summary = store.get_summary(task_id)
    if summary is None or summary.get("server") != name:
        raise HTTPException(status_code=404, detail="Task not found")

    saved = store.get_result(task_id)
    if saved is None:
        raise HTTPException(
            status_code=409,
            detail={"reason": "payload_expired", "summary": _summary_entry(summary)},
        )
    return normalize_backtest_task({**saved, "saved": True})


@router.delete("/servers/{name}/backtesting/tasks/{task_id}")
async def delete_backtest_task(
    name: str,
    task_id: str,
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    store = get_backtest_store()
    summary = store.get_summary(task_id)
    if summary is not None and summary.get("server") != name:
        # Result belongs to another server; don't let this server delete it
        raise HTTPException(status_code=404, detail="Task not found")
    store.delete_result(task_id)

    # Also try to delete from live
    try:
        client = await cm.get_client(name)
        return await client.backtesting.delete_task(task_id)
    except Exception:
        return {"deleted": True}


# ── Archive (server-agnostic) ──
#
# Not under ``/servers/{name}``: a backtest is a computation over candles, and
# the server is only the box that ran it. Once listing reads the index instead
# of every payload, scoping by server is a filter rather than a precondition --
# so the archive spans every server the caller can reach and authorization
# *filters* rather than gates. The per-server ``/saved`` pair these replace had
# zero consumers.


def _accessible_servers(user_id: int) -> set[str]:
    cm = get_config_manager()
    return {
        name
        for name in cm.list_servers()
        if cm.has_server_access(user_id, name, ServerPermission.TRADER)
    }


def _summary_entry(summary: dict) -> dict:
    """A summary in the shape the task list already speaks.

    The dashboard reads a run's parameters out of ``config.config``; rebuilding
    that nesting here is what lets one renderer handle a live task and an
    archived summary without branching, and it still costs ~1.6 KB because the
    payload never enters it.
    """
    return {
        "task_id": summary["task_id"],
        "status": summary.get("status") or "completed",
        "server": summary.get("server", ""),
        "config": {
            "config": {
                "id": summary.get("config_id", ""),
                "controller_name": summary.get("controller", ""),
                "trading_pair": summary.get("trading_pair", ""),
                "connector_name": summary.get("connector", ""),
            },
            "start_time": summary.get("start_time"),
            "end_time": summary.get("end_time"),
            "backtesting_resolution": summary.get("resolution", ""),
            "trade_cost": summary.get("trade_cost"),
        },
        "metrics": summary.get("metrics") or {},
        "has_payload": summary.get("has_payload", True),
        "created_at": summary.get("created_at"),
        "completed_at": summary.get("completed_at"),
        "error": summary.get("error"),
        "saved": True,
    }


def _live_entry(task: dict, store) -> dict:
    """A server-side task, stripped to the same listing tier.

    ``result`` is dropped here too: a completed live task carries the same
    hundred-megabyte payload the store does, and the list needs its metrics,
    not its candles.
    """
    if not isinstance(task, dict):
        return task
    entry = {k: v for k, v in task.items() if k != "result"}
    result = task.get("result")
    metrics = result.get("results") if isinstance(result, dict) else None
    entry["metrics"] = metrics if isinstance(metrics, dict) else {}
    summary = store.get_summary(task.get("task_id", ""))
    if summary is not None:
        entry["saved"] = True
        entry["server"] = summary.get("server", "")
        entry["has_payload"] = summary.get("has_payload", True)
    return entry


@router.get("/backtesting/archive")
async def list_backtest_archive(
    server: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    store = get_backtest_store()
    allowed = _accessible_servers(user.id)
    # A summary with no server provenance belongs to no server's access list and
    # so is reachable by nobody -- which is exactly how the per-server listing it
    # replaces behaved, since no server name ever equalled "".
    return {
        "migrated": store.migrated,
        "summaries": [
            _summary_entry(s)
            for s in store.list_summaries(server)
            if s.get("server") in allowed
        ],
    }


def _archived_or_404(task_id: str, user: WebUser) -> dict:
    """The summary for ``task_id``, or 404 if the caller cannot reach its server.

    404 rather than 403 on purpose: an id-addressed route that answers "you may
    not see this" tells an unauthorized caller the run exists. Same rule
    SEC-197 established for ``get_backtest_task``, applied to a route whose
    server arrives from the record instead of the path.
    """
    summary = get_backtest_store().get_summary(task_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    server = summary.get("server") or ""
    if not server or server not in _accessible_servers(user.id):
        raise HTTPException(status_code=404, detail="Backtest not found")
    return summary


@router.get("/backtesting/archive/{task_id}")
async def get_archived_backtest(
    task_id: str,
    user: WebUser = Depends(get_current_user),
):
    summary = _archived_or_404(task_id, user)
    store = get_backtest_store()

    payload = store.get_result(task_id)
    if payload is None:
        # Not "not found": the run is real and its metrics are right here. The
        # UI has to be able to say "chart expired" rather than deny the run.
        raise HTTPException(
            status_code=409,
            detail={"reason": "payload_expired", "summary": _summary_entry(summary)},
        )
    return normalize_backtest_task({**payload, "task_id": task_id, "saved": True})


@router.delete("/backtesting/archive/{task_id}")
async def delete_archived_backtest(
    task_id: str,
    user: WebUser = Depends(get_current_user),
):
    _archived_or_404(task_id, user)
    get_backtest_store().delete_result(task_id)
    return {"deleted": True}
