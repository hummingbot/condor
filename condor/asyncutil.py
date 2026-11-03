"""Shared asyncio helpers.

Each class here is the one implementation of an idiom ``condor`` had hand-rolled
in several places, with the copies' guards folded together rather than left to
drift apart: ``TaskSet`` for fire-and-forget tasks, ``SingleFlight`` for
per-key request coalescing.

``TaskSet`` is the one implementation of the fire-and-forget task tracker that
``condor`` had hand-rolled twice: once in ``condor/web/ws_manager.py``
(CORR-107, one-shot broadcasts and backfills) and once in
``condor/server_data_service.py`` (CORR-141, subscriber callbacks). Both copies
existed because ``asyncio.create_task`` / ``ensure_future`` alone is unsafe for
work nobody awaits:

* **The event loop only holds a weak reference**, so a task with no other
  referent can be garbage-collected mid-await and silently disappear.
* **The exception is never retrieved**, so a crash surfaces only as asyncio's
  "Task exception was never retrieved" at GC time — on no module's logger, with
  nothing naming what stalled.

``TaskSet`` holds the strong reference until the task completes, drops it
automatically, and reports failures on the *caller's* logger. Both call sites
word their error differently and identify the guilty task differently — the
ws manager by task name, SDS by ``subscriber_id`` — so the message template and
the label are the two things this helper parameterises rather than flattens.
Everything else (discard-then-check, cancellation is not a failure, snapshot
before cancelling because ``cancel()`` re-enters the done-callback) was
identical in both copies and is fixed here.

``SingleFlight`` (ARCH-606) is the same story for "one in-flight task per key,
every concurrent caller awaits it", which had been written out seven times: in
``condor/pool_data.py`` (gecko listings), ``condor/fetchers/archived_run.py``,
``condor/fetchers/bot_performance.py`` twice (whole-server snapshot, instance
history), ``condor/fetchers/run_history.py``, ``condor/server_data_service.py``
and ``condor/web/routes/market.py`` (candles). No two copies agreed: three of
the guards below were present in some and missing from others, so the same
idiom silently behaved differently depending on which module you were in.
``SingleFlight`` carries all three.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Hashable, Iterator, TypeVar

T = TypeVar("T")


class TaskSet:
    """Strong references to fire-and-forget tasks, with their failures logged.

    ``error_message`` is a printf-style template taking exactly two ``%s``: the
    label of the task and the exception. It is logged on ``logger``, so failures
    land on the calling module's logger and not on this one.
    """

    def __init__(self, logger: logging.Logger, error_message: str) -> None:
        self._logger = logger
        self._error_message = error_message
        self._tasks: set[asyncio.Task] = set()

    def track(self, task: asyncio.Task, label: str | None = None) -> asyncio.Task:
        """Keep a strong reference to ``task`` until it finishes.

        ``label`` names the task in the failure log; it defaults to the task's
        own name, which is what a caller that names its tasks wants. The
        reference is dropped automatically on completion. Returns the task so a
        caller can keep dispatching in one expression.
        """
        self._tasks.add(task)
        task.add_done_callback(lambda t, lbl=label: self._on_done(t, lbl))
        return task

    def cancel_all(self) -> None:
        """Cancel every still-pending task and forget all of them.

        Iterates a snapshot: ``cancel()`` fires the done-callback, which mutates
        the underlying set.
        """
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        self._tasks.clear()

    def _on_done(self, task: asyncio.Task, label: str | None) -> None:
        """Release the strong reference and surface the failure, if any."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.error(
                self._error_message, label or task.get_name(), exc, exc_info=exc
            )

    # -- Introspection (tests and log lines; not part of the hot path) --

    def __len__(self) -> int:
        return len(self._tasks)

    def __iter__(self) -> Iterator[asyncio.Task]:
        return iter(self._tasks)

    def __contains__(self, task: object) -> bool:
        return task in self._tasks

    def __bool__(self) -> bool:
        return bool(self._tasks)


class SingleFlight:
    """One in-flight task per key, shared by every concurrent caller of that key.

    ``run(key, factory)`` calls ``factory()`` only when no usable task is already
    running for ``key``; everyone else joins the one that is. A TTL cache only
    helps once an answer has *arrived*, so this is what collapses the burst of
    identical requests that all miss a cold cache at the same instant.

    Three guards, each of which one of the hand-rolled copies had and the others
    lacked:

    * **Shielded.** The work runs as a detached task and awaiters ``shield`` it,
      so the very thing that causes the stampede — a viewer navigating away and
      cancelling their request — cannot also cancel the fetch the remaining
      viewers are waiting on. Awaiting a ``Task`` bare propagates the awaiter's
      cancellation into it, handing every other waiter a ``CancelledError``.
    * **Same loop.** Reuse an in-flight fetch only from the loop that created
      it: a task is bound to its loop and awaiting it from another one raises.
    * **Not done.** A finished task lingers in the map until its done-callback
      runs on the next loop iteration; joining it would hand a fresh caller the
      previous run's outcome, a failure included. So a settled task is never
      reused and the next caller genuinely retries — a failure is shared by the
      waiters that were already on it, never remembered for the ones after.

    The done-callback is identity-checked, so a callback that fires late can
    only ever evict its own entry and never a newer task registered under the
    same key.
    """

    def __init__(self) -> None:
        self._inflight: dict[
            Hashable, tuple[asyncio.AbstractEventLoop, asyncio.Task]
        ] = {}

    async def run(self, key: Hashable, factory: Callable[[], Awaitable[T]]) -> T:
        """Run ``factory()`` once per key, sharing its outcome with every waiter."""
        loop = asyncio.get_running_loop()
        entry = self._inflight.get(key)
        task = (
            entry[1]
            if entry is not None and entry[0] is loop and not entry[1].done()
            else None
        )
        if task is None:
            task = asyncio.ensure_future(factory())
            self._inflight[key] = (loop, task)

            def _clear(finished: asyncio.Task, _key: Hashable = key) -> None:
                current = self._inflight.get(_key)
                if current is not None and current[1] is finished:
                    self._inflight.pop(_key, None)

            task.add_done_callback(_clear)
        return await asyncio.shield(task)

    def clear(self) -> None:
        """Forget every in-flight entry (tests, reconfiguration).

        The tasks themselves are left to finish: their waiters are still
        shielded on them, and only the sharing is dropped.
        """
        self._inflight.clear()

    # -- Introspection (tests and log lines; not part of the hot path) --

    def __len__(self) -> int:
        return len(self._inflight)

    def __bool__(self) -> bool:
        return bool(self._inflight)

    def __contains__(self, key: object) -> bool:
        return key in self._inflight

    def __iter__(self) -> Iterator[Any]:
        return iter(self._inflight)
