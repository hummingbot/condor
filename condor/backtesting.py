"""The one place Condor talks to the backtesting API.

Backtesting used to have three entry points — an MCP tool, the ``backtest_chart``
routine and the web dashboard — each with its own copy of the controller-config
coercion and its own idea of whether a result was worth keeping. FEAT-039 made the
routine the single surface; this module is the mechanism underneath it, and the web
routes share it so a run from Telegram, from an agent and from the dashboard are one
record in one store.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Polling defaults for run_and_save. The timeout is deliberately generous: a caller
# that cannot afford to wait submits the *routine* asynchronously (manage_routines
# action='run_async') rather than shortening this.
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 600.0

_TERMINAL = ("completed", "failed", "error", "cancelled")


class BacktestError(RuntimeError):
    """A backtest that did not produce a result — engine failure or timeout."""


def coerce_controller_config(config: dict) -> dict:
    """Coerce string values that look numeric to int/float.

    Controller configs loaded from YAML sometimes store numbers as strings
    (e.g. "100" instead of 100). The backtesting engine does arithmetic on
    these values and fails with 'int + str' errors if they aren't coerced.
    """
    out: dict[str, Any] = {}
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
            v = coerce_controller_config(v)
        out[k] = v
    return out


async def run_and_save(
    client,
    server: str,
    config: dict,
    start_time: int,
    end_time: int,
    resolution: str = "1m",
    trade_cost: float = 0.0002,
    poll_interval: float | None = None,
    timeout: float | None = None,
) -> tuple[str, dict]:
    """Submit a backtest task, poll to completion, persist it, return (task_id, task).

    Uses the task API rather than the sync ``/backtesting/run`` endpoint because the
    store is keyed by the server-side ``task_id`` and readers such as
    ``backtest_chart._load_saved_task`` read back the whole task envelope —
    ``{task_id, status, config, result, ...}`` — to reconstruct the run's parameters.
    A sync run produces neither an id nor that envelope, so "save the result" and "use
    the task API" are the same decision.

    Returns the SAME envelope the store holds and the web route serves.

    Raises:
        BacktestError: the engine failed, or the task did not finish within ``timeout``.
    """
    # Read off the module rather than the signature so the defaults stay one knob.
    poll_interval = DEFAULT_POLL_INTERVAL if poll_interval is None else poll_interval
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout

    submitted = await client.backtesting.submit_task(
        start_time=start_time,
        end_time=end_time,
        backtesting_resolution=resolution,
        trade_cost=trade_cost,
        config=coerce_controller_config(config),
    )

    task_id = (submitted or {}).get("task_id") if isinstance(submitted, dict) else None
    if not task_id:
        raise BacktestError(f"Backtest was not accepted by the server: {submitted}")

    task = await _poll_task(client, task_id, poll_interval, timeout)

    status = task.get("status")
    if status != "completed":
        raise BacktestError(
            f"Backtest {task_id} {status or 'unknown'}: "
            f"{task.get('error') or 'no error reported'}"
        )

    result = task.get("result")
    if not result:
        raise BacktestError(f"Backtest {task_id} completed with no result payload")
    if isinstance(result, dict) and result.get("error"):
        raise BacktestError(f"Backtest {task_id} failed: {result['error']}")

    _save(server, task_id, task)
    return task_id, task


async def _poll_task(
    client, task_id: str, poll_interval: float, timeout: float
) -> dict:
    """Poll a submitted task until it reaches a terminal state."""
    deadline = time.monotonic() + timeout
    task: dict = {}
    while True:
        task = await client.backtesting.get_task(task_id)
        if not isinstance(task, dict):
            raise BacktestError(
                f"Backtest {task_id} returned an unreadable task: {task}"
            )
        if task.get("status") in _TERMINAL:
            return task
        if time.monotonic() >= deadline:
            raise BacktestError(
                f"Backtest {task_id} is still {task.get('status') or 'running'} after "
                f"{int(timeout)}s. It keeps running on the server — render it later "
                f"with task_id='{task_id}'. For a window this long, submit the routine "
                "with action='run_async' instead of waiting."
            )
        await asyncio.sleep(poll_interval)


def _save(server: str, task_id: str, task: dict) -> None:
    """Persist the envelope. A store failure must never lose the backtest itself."""
    from condor.backtest_store import get_backtest_store

    try:
        get_backtest_store().save_result(server or "", task_id, task)
    except Exception:
        logger.warning(
            "Failed to save backtest %s to the store", task_id, exc_info=True
        )
