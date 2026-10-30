"""Tests for ARCH-606: ``condor.asyncutil.SingleFlight``.

The idiom was hand-rolled seven times across ``condor/`` and no two copies
carried the same guards. These cover the four the class folds together —
shield, same-loop, not-done, no borrowed scope — plus the identity-checked
done-callback, on the class itself rather than through one of its call sites.
"""

import asyncio
import os

import pytest

from condor.asyncutil import SingleFlight
from condor.runtime import code_guard


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


def test_the_shared_fetch_does_not_inherit_a_guarded_callers_scope():
    """A run_code snippet first on a key must not hand its deadline to every
    other waiter joining the same fetch: the shared task runs detached from
    whichever caller's context happened to start it.
    """
    seen = {}

    async def _work():
        seen["scope"] = code_guard.current_scope()
        await asyncio.sleep(0.01)
        return "value"

    async def _go():
        sf = SingleFlight()
        # Already past its deadline, as a timed-out snippet's scope would be.
        scope = code_guard.GuardScope(label="snippet", deadline=0.0)
        with code_guard.guarded(scope):
            return await sf.run("k", _work)

    assert asyncio.run(_go()) == "value"
    assert seen["scope"] is None


def test_an_unrelated_waiter_is_not_cut_by_the_first_callers_expired_scope():
    """The symptom this guards against: an ordinary caller joining a fetch a
    timed-out snippet happened to start first must get the real result, not
    the snippet's CodeRunDeadline. ``os.listdir`` is an audited call, so it is
    where the leaked scope's expired deadline would actually fire.
    """

    async def _work():
        await asyncio.sleep(0.01)
        os.listdir(".")
        return "value"

    async def _go():
        sf = SingleFlight()
        scope = code_guard.GuardScope(label="snippet", deadline=0.0)
        with code_guard.guarded(scope):
            snippet_call = asyncio.ensure_future(sf.run("k", _work))
        # No scope here: an ordinary dashboard request joining the same key.
        plain_call = asyncio.ensure_future(sf.run("k", _work))
        return await asyncio.gather(snippet_call, plain_call, return_exceptions=True)

    assert asyncio.run(_go()) == ["value", "value"]


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
