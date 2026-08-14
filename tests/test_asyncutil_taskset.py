"""`TaskSet` is the one tracker behind ws_manager's one-shots and SDS's
subscriber callbacks (ARCH-162). Both hazards it exists for are invisible in
normal operation, so they are pinned here: the strong reference that keeps a
fire-and-forget task alive when nothing else refers to it, and the exception
retrieval that turns a silent "Task exception was never retrieved" at GC time
into an ERROR on the *caller's* logger.
"""

import asyncio
import gc
import logging

import pytest

from condor.asyncutil import TaskSet

LOGGER_NAME = "tests.asyncutil_taskset"
logger = logging.getLogger(LOGGER_NAME)


def _tracker(message: str = "Tracked task %s failed: %s") -> TaskSet:
    return TaskSet(logger, message)


def test_strong_reference_keeps_the_task_alive_to_completion():
    """Nothing but the TaskSet refers to the task: the event loop holds only a
    weak reference, so without tracking a GC pass mid-await can drop it."""
    tracker = _tracker()
    finished = []

    async def work():
        await asyncio.sleep(0)
        finished.append("done")

    async def scenario():
        tracker.track(asyncio.create_task(work(), name="worker"))
        # Drop every local handle and force a collection while it is suspended.
        gc.collect()
        assert len(tracker) == 1, "the task was not held"
        await asyncio.gather(*list(tracker), return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert finished == ["done"], "the tracked task did not run to completion"
    assert len(tracker) == 0, "the strong reference was not released"


def test_failure_is_retrieved_and_logged_on_the_callers_logger(caplog):
    """A crash must leave a traceback on the logger the site passed in, and the
    exception must be consumed so asyncio never warns about it."""
    tracker = _tracker()

    async def boom():
        raise RuntimeError("downstream exploded")

    async def scenario():
        task = tracker.track(asyncio.create_task(boom(), name="worker"))
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        return task

    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        task = asyncio.run(scenario())

    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert records, "the failure produced no ERROR on the caller's logger"
    assert "worker" in records[0].getMessage()
    assert "downstream exploded" in records[0].getMessage()
    assert records[0].exc_info is not None, "traceback was not attached"
    # This is the property that matters: the exception was already retrieved,
    # so the task is not left holding an unretrieved one at GC time.
    assert isinstance(task.exception(), RuntimeError)
    assert len(tracker) == 0


def test_explicit_label_names_the_task_instead_of_its_name(caplog):
    """SDS identifies the guilty callback by subscriber_id, which is not
    derivable from the task; ws_manager falls back to the task name."""
    tracker = _tracker("SDS callback error for %s: %s")

    async def boom():
        raise ValueError("subscriber blew up")

    async def scenario():
        task = tracker.track(
            asyncio.create_task(boom(), name="unhelpful"), "ws_manager"
        )
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        asyncio.run(scenario())

    message = caplog.records[0].getMessage()
    assert message == "SDS callback error for ws_manager: subscriber blew up"
    assert "unhelpful" not in message


def test_cancellation_is_not_a_failure(caplog):
    """Shutdown cancels whatever is pending; that must not log an error."""
    tracker = _tracker()

    async def scenario():
        task = tracker.track(asyncio.create_task(asyncio.Event().wait()))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        asyncio.run(scenario())

    assert [r for r in caplog.records if r.name == LOGGER_NAME] == []
    assert len(tracker) == 0


def test_cancel_all_drains_pending_tasks_without_mutating_mid_iteration():
    """`cancel()` re-enters the done-callback that discards from the set, so
    cancel_all must iterate a snapshot."""
    tracker = _tracker()

    async def scenario():
        for i in range(5):
            tracker.track(asyncio.create_task(asyncio.Event().wait(), name=f"t{i}"))
        await asyncio.sleep(0)
        tasks = list(tracker)
        tracker.cancel_all()
        await asyncio.gather(*tasks, return_exceptions=True)
        return tasks

    tasks = asyncio.run(scenario())

    assert all(t.cancelled() for t in tasks), "cancel_all left a task running"
    assert len(tracker) == 0
    assert not tracker


@pytest.mark.parametrize(
    "module, attr, message",
    [
        ("condor.web.ws_manager", "_oneshot_tasks", "One-shot task %s failed: %s"),
        (
            "condor.server_data_service",
            "_callback_tasks",
            "SDS callback error for %s: %s",
        ),
    ],
)
def test_both_call_sites_share_the_helper_and_keep_their_own_wording(
    module, attr, message
):
    """The point of ARCH-162: one implementation, but each site still logs its
    own sentence on its own logger."""
    import importlib
    import inspect

    mod = importlib.import_module(module)
    source = inspect.getsource(mod)
    assert "TaskSet" in source, f"{module} does not use the shared tracker"
    assert f"self.{attr} = TaskSet(" in source, f"{module} does not hold one"
    assert message in source, f"{module} lost its own error wording"
    assert "def _track_" not in source, f"{module} still hand-rolls a tracker"
