"""Tests for ARCH-606: ``condor.asyncutil.SingleFlight``.

The idiom was hand-rolled seven times across ``condor/`` and no two copies
carried the same guards. These cover the three the class folds together —
shield, same-loop, not-done — plus the identity-checked done-callback, on the
class itself rather than through one of its call sites.
"""

import asyncio

import pytest

from condor.asyncutil import SingleFlight


def test_concurrent_callers_of_one_key_share_a_single_run():
    calls = {"n": 0}

    async def _work():
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return calls["n"]

    async def _go():
        sf = SingleFlight()
        results = await asyncio.gather(*[sf.run("k", _work) for _ in range(5)])
        return sf, results

    sf, results = asyncio.run(_go())
    assert calls["n"] == 1
    assert results == [1, 1, 1, 1, 1]
    assert not sf, "the entry is dropped once the task settles"


def test_distinct_keys_never_share_a_run():
    async def _work():
        await asyncio.sleep(0.02)
        return "done"

    async def _go():
        sf = SingleFlight()
        pending = [asyncio.ensure_future(sf.run(k, _work)) for k in ("a", "b", "c")]
        await asyncio.sleep(0.01)
        assert len(sf) == 3
        assert "a" in sf and "z" not in sf
        await asyncio.gather(*pending)
        return sf

    assert not asyncio.run(_go())


def test_a_cancelled_waiter_leaves_the_others_with_the_result():
    """The shield: the stampede's cause must not also be its kill switch."""
    calls = {"n": 0}

    async def _work():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return "value"

    async def _go():
        sf = SingleFlight()
        leaver = asyncio.ensure_future(sf.run("k", _work))
        stayer = asyncio.ensure_future(sf.run("k", _work))
        await asyncio.sleep(0.01)
        leaver.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaver
        return await stayer

    assert asyncio.run(_go()) == "value"
    assert calls["n"] == 1


def test_a_failure_is_shared_by_its_waiters_and_never_remembered():
    calls = {"n": 0}

    async def _flaky():
        calls["n"] += 1
        await asyncio.sleep(0.02)
        if calls["n"] == 1:
            raise RuntimeError("upstream down")
        return "recovered"

    async def _go():
        sf = SingleFlight()
        failed = await asyncio.gather(
            *[sf.run("k", _flaky) for _ in range(2)], return_exceptions=True
        )
        # The settled task is not reused: the next caller genuinely retries.
        return failed, await sf.run("k", _flaky)

    failed, recovered = asyncio.run(_go())
    assert all(isinstance(e, RuntimeError) for e in failed)
    assert recovered == "recovered"
    assert calls["n"] == 2


def test_an_entry_from_another_loop_is_never_awaited():
    """A task is bound to its loop; awaiting it from another one raises."""

    async def _work():
        return "fresh"

    sf = SingleFlight()
    # A stale entry left behind by a loop that is gone. Its "task" is a plain
    # object, so reusing it instead of the guard firing would blow up loudly.
    sf._inflight["k"] = (object(), object())

    assert asyncio.run(sf.run("k", _work)) == "fresh"


def test_a_late_done_callback_cannot_evict_a_newer_task():
    """The callback is identity-checked, so it only ever drops its own entry."""

    async def _work():
        return "done"

    async def _go():
        sf = SingleFlight()
        first = await sf.run("k", _work)
        # The first task is settled but its callback has already popped the key;
        # re-running registers a fresh entry that the stale callback must not
        # touch. Drive a full loop iteration so any late callback would fire.
        pending = asyncio.ensure_future(sf.run("k", _work))
        await asyncio.sleep(0)
        assert "k" in sf, "the newer task must still own the key"
        return first, await pending

    assert asyncio.run(_go()) == ("done", "done")
