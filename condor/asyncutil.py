"""Shared asyncio helpers.

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
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterator


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
