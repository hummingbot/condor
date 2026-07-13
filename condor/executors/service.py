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


def get_executor_runtime() -> ExecutorRuntime:
    global _runtime
    if _runtime is None:
        _runtime = ExecutorRuntime()
    return _runtime


async def start_executor_service() -> None:
    """Reconcile persisted executors and start the watchdog.

    Called once from the web app startup hook. Reconcile errors are
    per-record and logged CRITICAL inside reconcile(); a gateway that is
    down leaves rows untouched for the next start.
    """
    runtime = get_executor_runtime()
    try:
        resumed = await runtime.reconcile()
        if resumed:
            logger.info("executor service: resumed %s", resumed)
    except Exception:
        logger.exception("executor service: reconcile failed at startup")
    runtime.start_watchdog()


async def stop_executor_service() -> None:
    """Graceful process shutdown: persist and detach, never close positions."""
    global _runtime
    if _runtime is not None:
        await _runtime.shutdown()
        _runtime = None
