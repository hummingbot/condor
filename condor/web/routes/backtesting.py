"""The backtest archive, and nothing else.

Three routes over local data. Launching a backtest is the ``backtest_chart``
routine's job from every seat, the dashboard included (FEAT-076), so this module
makes no call to the Hummingbot API at all: a run is submitted through
``POST /api/v1/routines/run`` and read back from here once it has completed.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from condor.backtest_store import get_backtest_store
from condor.backtesting import normalize_backtest_task
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import ServerPermission, get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["backtesting"])


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
    that nesting here is what lets one renderer handle an archived run and a
    run still in flight without branching, and it still costs ~1.6 KB because
    the payload never enters it.
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
    not see this" tells an unauthorized caller the run exists. That is the rule
    SEC-197 established for the per-server reads these replaced, and this is now
    the only place it lives: the server arrives from the record rather than from
    the path, so the check has to read the summary to know whose run it is.
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

    # gunzip + parse of a payload that runs to 137 MB: off the loop, or this
    # one chart open stalls the Telegram poller and every other request with it.
    payload = await asyncio.to_thread(store.get_result, task_id)
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
