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
from dataclasses import dataclass
from typing import Any

import aiohttp

from condor.fetchers.executors import normalize_executor_side

logger = logging.getLogger(__name__)

# Polling defaults for run_and_save. The timeout is deliberately generous: a caller
# that cannot afford to wait submits the *routine* asynchronously (manage_routines
# action='run_async') rather than shortening this.
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 600.0

# How many backtests a fan-out puts on the wire at once. hummingbot-api runs each
# backtest in its own worker process behind a semaphore of BACKTESTING_MAX_CONCURRENT
# (1 upstream; setup-environment.sh writes 4 into the .env it generates), so a caller
# that submits more than this is not going faster -- it is only queueing on the
# server, where a run's deadline is already ticking.
DEFAULT_MAX_CONCURRENT = 4

# The one response that carries the whole backtest -- every executor plus
# processed_data -- is the poll that finally succeeds, and aiohttp's ``total``
# covers the body read, so the shared client's 60s cap (config_manager.get_client)
# applied to it: a multi-month 1m window does not transfer and parse in a minute,
# the run completed on the server and Condor timed out reading the answer.
# ``sock_read`` is the knob that fits both shapes of slowness on this path -- a
# payload that is streaming keeps resetting it however long the transfer takes,
# while an API server stalled computing sends nothing and still trips it in a
# minute, which is the case _poll_task retries. ``total`` stays only as a backstop
# against a connection that dribbles forever.
TASK_READ_TIMEOUT = aiohttp.ClientTimeout(total=600, connect=15, sock_read=60)

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


def normalize_backtest_task(task: Any) -> Any:
    """Normalize the executor sides inside a backtest envelope, in place.

    The backtesting engine reports a side the way the raw API does -- ``1``,
    ``TradeType.BUY``, ``LONG`` -- and this payload is the one wire that never went
    through :func:`condor.fetchers.executors.normalize_executor_side`, so the
    dashboard carried a TypeScript reimplementation of the rule to render a backtest
    (ARCH-121). Applying the canonical normalizer here makes the backtest wire match
    the executor wire, leaving exactly one definition of the rule.

    Accepts either the task envelope (``{status, config, result, ...}``) or a bare
    result payload, and only rewrites a ``side`` that is actually present, so it adds
    nothing to the raw payload the dashboard also renders as JSON.
    """
    if not isinstance(task, dict):
        return task
    result = task.get("result")
    payload = result if isinstance(result, dict) else task
    executors = payload.get("executors")
    if isinstance(executors, list):
        for ex in executors:
            if isinstance(ex, dict) and "side" in ex:
                ex["side"] = normalize_executor_side(ex["side"])
    return task


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

    await _save(server, task_id, task)
    return task_id, task


@dataclass(frozen=True)
class BacktestOutcome:
    """One config's place in a fan-out: the task it produced, or why it has none.

    A batch of backtests is reported over, and a report that quietly covers five of
    the seven configs it was asked about is worse than one that says two failed. So
    the failure travels back with the config that caused it instead of being logged
    and turned into ``None``.
    """

    config: dict
    task_id: str | None = None
    task: dict | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.task is not None


async def run_many(
    client,
    server: str,
    configs: list[dict],
    start_time: int,
    end_time: int,
    resolution: str = "1m",
    trade_cost: float = 0.0002,
    max_concurrent: int | None = None,
    poll_interval: float | None = None,
    timeout: float | None = None,
) -> list[BacktestOutcome]:
    """Run a batch of backtests against one server, in input order, losing none.

    Two things make a fan-out different from N calls to :func:`run_and_save`, and
    both live here rather than in each caller:

    *Bounded.* The server serializes backtests behind ``BACKTESTING_MAX_CONCURRENT``
    worker slots, so submitting seven at once buys nothing and costs the six that
    wait. At most ``max_concurrent`` are on the wire, and the rest wait here, where
    waiting is free.

    *Patient.* A submitted run's deadline starts at submission, not at execution, so
    a run that shares a batch has to outlast the batch. Each therefore gets
    ``timeout`` multiplied by the number in flight beside it -- which covers the
    worst case, a server whose own cap is 1 running the whole batch end to end.

    Returns one :class:`BacktestOutcome` per config, in the order given.
    """
    configs = list(configs)
    if not configs:
        return []

    bound = DEFAULT_MAX_CONCURRENT if max_concurrent is None else int(max_concurrent)
    in_flight = max(1, min(bound, len(configs)))
    deadline = (DEFAULT_TIMEOUT if timeout is None else timeout) * in_flight
    slots = asyncio.Semaphore(in_flight)

    async def _one(config: dict) -> BacktestOutcome:
        async with slots:
            try:
                task_id, task = await run_and_save(
                    client,
                    server,
                    config,
                    start_time,
                    end_time,
                    resolution=resolution,
                    trade_cost=trade_cost,
                    poll_interval=poll_interval,
                    timeout=deadline,
                )
            except Exception as exc:
                logger.warning(
                    "Backtest failed for config %s: %s",
                    config.get("id", "?"),
                    exc,
                    exc_info=True,
                )
                return BacktestOutcome(
                    config=config, error=f"{type(exc).__name__}: {exc}"
                )
        task["task_id"] = task_id
        return BacktestOutcome(config=config, task_id=task_id, task=task)

    return list(await asyncio.gather(*[_one(cfg) for cfg in configs]))


async def _poll_task(
    client, task_id: str, poll_interval: float, timeout: float
) -> dict:
    """Poll a submitted task until it reaches a terminal state.

    A poll that times out is *not* a failed backtest. The API server computes a
    backtest on the same event loop it answers HTTP on, so a wide window over
    fine candles stalls every request to it — including this one, which then
    hits the client's own request timeout and raised a bare ``TimeoutError``
    that killed a run whose task was still healthy on the server. The deadline
    below is what bounds the wait; an individual poll failing to answer only
    means "ask again".
    """
    deadline = time.monotonic() + timeout
    last_status: str | None = None
    while True:
        stalled = False
        try:
            task = await get_task(client, task_id)
        except TimeoutError:
            # Covers asyncio.TimeoutError too — the same object since 3.11, and
            # what aiohttp raises when a request outlives its ClientTimeout.
            logger.debug(
                "Poll for backtest %s timed out; the server is busy, retrying",
                task_id,
            )
            stalled = True

        if not stalled:
            if not isinstance(task, dict):
                raise BacktestError(
                    f"Backtest {task_id} returned an unreadable task: {task}"
                )
            if task.get("status") in _TERMINAL:
                return normalize_backtest_task(task)
            last_status = task.get("status")

        if time.monotonic() >= deadline:
            raise BacktestError(
                f"Backtest {task_id} is still {last_status or 'running'} after "
                f"{int(timeout)}s. It keeps running on the server — render it later "
                f"with task_id='{task_id}'. For a window this long, submit the routine "
                "with action='run_async' instead of waiting."
            )
        await asyncio.sleep(poll_interval)


async def get_task(client, task_id: str) -> Any:
    """GET one backtest task with a read deadline sized for its result payload.

    ``BacktestingRouter.get_task`` takes no per-request timeout, and the
    session-wide one belongs to every other caller of the shared client -- 60s is
    right for a chat-latency call and wrong for this one. The request therefore
    goes to the same authenticated session directly, carrying
    :data:`TASK_READ_TIMEOUT`. A client that does not expose its session (the
    test doubles, a future client shape) falls back to the router.
    """
    router = getattr(client, "backtesting", None)
    session = getattr(router, "session", None)
    base_url = getattr(router, "base_url", None)
    if session is None or base_url is None:
        return await router.get_task(task_id)

    url = f"{base_url}/backtesting/tasks/{task_id}"
    async with session.get(url, timeout=TASK_READ_TIMEOUT) as response:
        if not response.ok:
            detail = (await response.text())[:300].strip()
            raise BacktestError(
                f"Backtest {task_id} could not be read: "
                f"HTTP {response.status} {detail}".strip()
            )
        return await response.json()


async def fetch_and_save(client, server: str, task_id: str) -> dict | None:
    """Fetch a finished task from the server and persist it; None if there is none.

    ``_save`` only ever runs at the end of a wait, so a run whose result read timed
    out completed on the server and never reached the store -- and "render it later
    with task_id=..." then found nothing to render. This is the fall-through that
    makes that advice true: fetched once, saved, and every later render is local.
    """
    try:
        task = await get_task(client, task_id)
    except Exception:
        logger.debug(
            "Could not fetch backtest %s from the server", task_id, exc_info=True
        )
        return None

    if (
        not isinstance(task, dict)
        or task.get("status") != "completed"
        or not task.get("result")
    ):
        return None

    task = normalize_backtest_task(task)
    await _save(server, task_id, task)
    return task


async def _save(server: str, task_id: str, task: dict) -> None:
    """Persist the envelope. A store failure must never lose the backtest itself.

    The write is ``json.dumps`` plus gzip level 6 over the whole envelope, which
    the store's own ``migrate`` docstring measures at seconds per file (payloads
    run to 137 MB). This coroutine shares its loop with the Telegram poller and
    every dashboard request, so the compression goes to a thread — the same
    treatment ``migrate_backtest_archive`` already gets in ``main.py``. The
    store is resolved on the loop first so only the bound method crosses over.
    """
    from condor.backtest_store import get_backtest_store

    try:
        store = get_backtest_store()
        await asyncio.to_thread(store.save_result, server or "", task_id, task)
    except Exception:
        logger.warning(
            "Failed to save backtest %s to the store", task_id, exc_info=True
        )
