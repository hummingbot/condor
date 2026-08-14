"""Tests for CORR-141: SDS subscriber callbacks run as tracked tasks.

``_fire_callbacks`` used to dispatch every subscriber callback with a bare
``asyncio.ensure_future`` and drop the task: the event loop keeps only a weak
reference, so the task could be garbage-collected mid-await, and a crash
inside the callback surfaced only as asyncio's "Task exception was never
retrieved" at GC time. Callbacks are now tracked with a strong reference and
their exceptions are retrieved and logged.
"""

import asyncio
import gc
import logging

from condor.server_data_service import ServerDataService, ServerDataType


def _make_sds() -> ServerDataService:
    """Fresh (non-singleton) SDS with no fetchers registered."""
    sds = ServerDataService()

    async def _fake_get_client(server_name):
        return object()

    sds._get_client = _fake_get_client
    return sds


def test_fired_callback_survives_gc_and_task_set_drains():
    """A fired callback keeps a strong ref until it completes, then drains."""
    ran = {"done": False}
    started = asyncio.Event()

    async def slow_callback(key, old_value, new_value):
        started.set()
        await asyncio.sleep(0.05)
        ran["done"] = True

    async def _drive():
        sds = _make_sds()
        await sds.subscribe(
            "srv", ServerDataType.PORTFOLIO, "sub", callback=slow_callback
        )

        sds.put("srv", ServerDataType.PORTFOLIO, {"v": 1})
        await started.wait()

        # The task is strongly referenced while in flight, so a GC pass in the
        # middle of the await cannot cancel it.
        assert len(sds._callback_tasks) == 1, "callback task was not tracked"
        gc.collect()

        await asyncio.gather(*list(sds._callback_tasks), return_exceptions=True)
        await asyncio.sleep(0)  # let done-callbacks run
        return sds

    sds = asyncio.run(_drive())

    assert ran["done"] is True, "callback was lost mid-flight"
    assert sds._callback_tasks == set(), "done-callback must discard the task"


def test_callback_exception_is_logged_and_isolated(caplog):
    """A raising subscriber is logged on the SDS logger; others still run."""
    good_ran = {"count": 0}

    async def boom_callback(key, old_value, new_value):
        raise RuntimeError("subscriber exploded")

    async def good_callback(key, old_value, new_value):
        good_ran["count"] += 1

    async def _drive():
        sds = _make_sds()
        await sds.subscribe(
            "srv", ServerDataType.PORTFOLIO, "bad_sub", callback=boom_callback
        )
        await sds.subscribe(
            "srv", ServerDataType.PORTFOLIO, "good_sub", callback=good_callback
        )

        sds.put("srv", ServerDataType.PORTFOLIO, {"v": 1})
        await asyncio.gather(*list(sds._callback_tasks), return_exceptions=True)
        await asyncio.sleep(0)  # let done-callbacks run
        return sds

    with caplog.at_level(logging.ERROR, logger="condor.server_data_service"):
        sds = asyncio.run(_drive())

    messages = [
        r.getMessage() for r in caplog.records if r.name == "condor.server_data_service"
    ]
    assert any(
        "bad_sub" in m and "subscriber exploded" in m for m in messages
    ), f"callback failure was not logged: {messages}"

    assert good_ran["count"] == 1, "a failing subscriber must not block the others"
    assert sds._callback_tasks == set(), "failed task must still be discarded"


def test_publisher_survives_failing_callback():
    """``put`` never raises because a subscriber callback blew up."""

    async def boom_callback(key, old_value, new_value):
        raise RuntimeError("subscriber exploded")

    async def _drive():
        sds = _make_sds()
        await sds.subscribe(
            "srv", ServerDataType.PORTFOLIO, "bad_sub", callback=boom_callback
        )

        sds.put("srv", ServerDataType.PORTFOLIO, {"v": 1})
        sds.put("srv", ServerDataType.PORTFOLIO, {"v": 2})
        await asyncio.gather(*list(sds._callback_tasks), return_exceptions=True)
        await asyncio.sleep(0)
        return sds

    sds = asyncio.run(_drive())

    # Publisher kept writing through the failures.
    assert sds.get("srv", ServerDataType.PORTFOLIO) == {"v": 2}


def test_stop_cancels_pending_callback_tasks():
    """``stop()`` cancels in-flight callback tasks and clears the set."""
    finished = {"done": False}

    async def never_ending_callback(key, old_value, new_value):
        await asyncio.sleep(30)
        finished["done"] = True

    async def _drive():
        sds = _make_sds()
        await sds.subscribe(
            "srv", ServerDataType.PORTFOLIO, "sub", callback=never_ending_callback
        )

        sds.put("srv", ServerDataType.PORTFOLIO, {"v": 1})
        await asyncio.sleep(0)  # let the task start
        pending = list(sds._callback_tasks)
        assert len(pending) == 1

        sds.stop()
        await asyncio.gather(*pending, return_exceptions=True)
        return sds, pending

    sds, pending = asyncio.run(_drive())

    assert pending[0].cancelled(), "pending callback task must be cancelled by stop()"
    assert finished["done"] is False
    assert sds._callback_tasks == set()
