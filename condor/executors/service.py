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
    # Sessions do not survive a restart (engines are memory-only): mark any
    # meta.yml still claiming "running" as interrupted before anything reads
    # session statuses. Their executors are handled by reconcile() below.
    try:
        from condor.agents.agent import agents_data_root
        from condor.agents.journal import mark_interrupted_sessions

        marked = mark_interrupted_sessions(agents_data_root())
        if marked:
            logger.warning(
                "executor service: %d session(s) interrupted by restart: %s",
                len(marked),
                marked,
            )
    except Exception:
        logger.exception("executor service: interrupted-session sweep failed")

    global _reconciling
    _reconciling = True
    try:
        runtime = get_executor_runtime()
        try:
            resumed = await runtime.reconcile()
            if resumed:
                logger.info("executor service: resumed %s", resumed)
        except Exception:
            logger.exception("executor service: reconcile failed at startup")
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
