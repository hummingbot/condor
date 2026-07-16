"""Main-process singleton for the native executor runtime.

Executors must live in the persistent Condor process (Tier 2) so they
survive MCP-subprocess and chat-session lifecycles — the same reason
TickEngines are created here. The web app starts this service on
startup; MCP tools reach it through the /executors REST routes.
"""

from __future__ import annotations

import logging
from typing import Optional

from condor.executors.runtime import ExecutorRuntime

logger = logging.getLogger(__name__)

_runtime: Optional[ExecutorRuntime] = None
_reconciling = False


def runtime_reconciling() -> bool:
    """True while startup reconciliation is running — mutating executor ops
    (create, session start) are 503-gated during that window so the control
    plane can come up first without racing venue-truth recovery."""
    return _reconciling


def get_executor_runtime() -> ExecutorRuntime:
    global _runtime
    if _runtime is None:
        _runtime = ExecutorRuntime()
    return _runtime


def peek_executor_runtime() -> Optional[ExecutorRuntime]:
    """Return the live runtime if one exists, without creating it."""
    return _runtime


async def start_executor_service() -> None:
    """Reconcile persisted executors and start the watchdog.

    Called once from the web app startup hook. Reconcile errors are
    per-record and logged CRITICAL inside reconcile(); a gateway that is
    down leaves rows untouched for the next start.
    """
    # Runs do not survive a restart (engines are memory-only, §4.2): any
    # run_started stream lacking run_ended gets a synthesized
    # run_ended{interrupted}, and its pending approvals are voided
    # (interrupted_void) — the dead run can never consume a grant. Their
    # executors are handled by reconcile() below (§4.2 survival rule).
    try:
        from condor.agents.runstore import get_run_store

        voided = get_run_store().sweep_interrupted()
        for perm in voided:
            try:
                from condor.notifications import notify

                await notify(
                    f"Pending approval {perm.get('approval_id', '')} voided — "
                    f"its run {perm.get('run_id', '')} was interrupted by "
                    "restart; a relaunched run must re-request approval.",
                    agent_id=perm.get("run_id", ""),
                    kind="approval",
                )
            except Exception:
                logger.exception("executor service: approval-void notify failed")
    except Exception:
        logger.exception("executor service: interrupted-run sweep failed")

    global _reconciling
    _reconciling = True
    try:
        runtime = get_executor_runtime()
        # §12 ordering: rebuild leases from durable records BEFORE venue
        # reconciliation (a create racing startup must see them), then refresh
        # after reconcile settles records so leases of settled executors drop.
        held = runtime.rebuild_leases()
        if held:
            logger.info("executor service: rebuilt %d lease holder(s)", held)
        try:
            resumed = await runtime.reconcile()
            if resumed:
                logger.info("executor service: resumed %s", resumed)
        except Exception:
            logger.exception("executor service: reconcile failed at startup")
        runtime.rebuild_leases()
        runtime.start_watchdog()
        logger.info("executor service: ready")
    finally:
        _reconciling = False


async def stop_executor_service() -> None:
    """Graceful process shutdown: persist and detach, never close positions."""
    global _runtime
    if _runtime is not None:
        await _runtime.shutdown()
        _runtime = None
