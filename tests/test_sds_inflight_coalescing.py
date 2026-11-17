"""Tests for PERF-059: ServerDataService in-flight fetch coalescing.

Concurrent ``get_or_fetch`` calls for the same cold key must trigger exactly
one backend fetch (single-flight); the outcome — success or failure — is
shared by all coalesced waiters, and a failed fetch never poisons subsequent
fetches for the key.
"""

import asyncio

from condor.server_data_service import ServerDataService, ServerDataType


def _make_sds(fetch_func):
    """Fresh (non-singleton) SDS with a fake client and a registered fetcher."""
    sds = ServerDataService()

    async def _fake_get_client(server_name):
        return object()

    sds._get_client = _fake_get_client
    sds.register_fetch(ServerDataType.PORTFOLIO, fetch_func)
    return sds


def test_concurrent_get_or_fetch_coalesces_to_single_fetch():
    """Two concurrent get_or_fetch calls on a cold key fetch exactly once."""
    calls = {"count": 0}

    async def counting_fetcher(client, **params):
        calls["count"] += 1
        await asyncio.sleep(0.05)  # keep the fetch in flight while others join
        return {"value": calls["count"]}

    async def _drive():
        sds = _make_sds(counting_fetcher)
        results = await asyncio.gather(
            sds.get_or_fetch("srv", ServerDataType.PORTFOLIO),
            sds.get_or_fetch("srv", ServerDataType.PORTFOLIO),
        )
        return sds, results

    sds, results = asyncio.run(_drive())

    assert calls["count"] == 1, "concurrent cold reads must coalesce to one fetch"
    assert results[0] == results[1] == {"value": 1}
    assert not sds._inflight, "in-flight map must be cleared once settled"


def test_fetch_failure_shared_by_waiters_and_does_not_poison_next_fetch():
    """All coalesced waiters see the failure; the next fetch starts clean."""
    calls = {"count": 0}
    fail = {"on": True}

    async def flaky_fetcher(client, **params):
        calls["count"] += 1
        await asyncio.sleep(0.05)
        if fail["on"]:
            raise RuntimeError("backend down")
        return {"ok": True}

    async def _drive():
        sds = _make_sds(flaky_fetcher)

        # Both waiters coalesce onto the single failing fetch (no value cached)
        failed = await asyncio.gather(
            sds.get_or_fetch("srv", ServerDataType.PORTFOLIO),
            sds.get_or_fetch("srv", ServerDataType.PORTFOLIO),
        )
        assert failed == [None, None]
        assert calls["count"] == 1
        assert not sds._inflight

        # Failure must not poison the key: a later fetch runs and succeeds
        fail["on"] = False
        recovered = await sds.get_or_fetch("srv", ServerDataType.PORTFOLIO)
        assert recovered == {"ok": True}
        assert calls["count"] == 2
        assert not sds._inflight

    asyncio.run(_drive())


def test_cancelled_waiter_does_not_kill_the_shared_fetch():
    """ARCH-606: SDS joins the shared fetch through a shield, like its siblings.

    Two callers coalesce onto one cold-key fetch and the first is cancelled — a
    ``wait_for`` timeout, a cancelled ``gather`` leg, a WS teardown. Before the
    extraction SDS joined with a bare ``await task``, so that cancellation
    propagated into the shared task and the survivor got a ``CancelledError``
    instead of the value.
    """
    calls = {"count": 0}

    async def counting_fetcher(client, **params):
        calls["count"] += 1
        await asyncio.sleep(0.05)
        return {"value": calls["count"]}

    async def _drive():
        sds = _make_sds(counting_fetcher)
        leaver = asyncio.ensure_future(
            sds.get_or_fetch("srv", ServerDataType.PORTFOLIO)
        )
        stayer = asyncio.ensure_future(
            sds.get_or_fetch("srv", ServerDataType.PORTFOLIO)
        )
        # Both are parked on the single in-flight fetch before it settles.
        await asyncio.sleep(0.01)
        leaver.cancel()
        try:
            await leaver
        except asyncio.CancelledError:
            pass
        return sds, await stayer

    sds, survived = asyncio.run(_drive())

    assert calls["count"] == 1, "the cancellation must not have started a second fetch"
    assert survived == {"value": 1}, "the surviving waiter still gets the value"
    assert not sds._inflight


def test_invalidate_wins_over_a_fetch_already_in_flight():
    """DL-07: a request that left before the mutation must not be cached.

    ``invalidate`` only deleted entries, so the handler's very next read joined
    the poller's in-flight request — sent before the order/swap/key change
    landed — and that request then wrote the pre-mutation snapshot back with a
    fresh timestamp.
    """
    calls = {"count": 0}
    state = {"value": "before"}

    async def _drive():
        release = asyncio.Event()

        async def fetcher(client, **params):
            calls["count"] += 1
            snapshot = state["value"]  # what the server answered at send time
            if calls["count"] == 1:
                await release.wait()
            return {"value": snapshot}

        sds = _make_sds(fetcher)
        early = asyncio.ensure_future(sds.get_or_fetch("srv", ServerDataType.PORTFOLIO))
        await asyncio.sleep(0.01)
        assert calls["count"] == 1 and sds._inflight

        # The mutation lands, and the handler invalidates and re-reads.
        state["value"] = "after"
        sds.invalidate("srv", ServerDataType.PORTFOLIO)
        fresh = await sds.get_or_fetch("srv", ServerDataType.PORTFOLIO)
        assert calls["count"] == 2, "the re-read joined the pre-mutation request"
        assert fresh == {"value": "after"}

        # The old request settles late: its waiter is answered, the cache is not.
        release.set()
        assert await early == {"value": "before"}
        await asyncio.sleep(0)
        assert sds.get("srv", ServerDataType.PORTFOLIO) == {"value": "after"}
        assert calls["count"] == 2
        return sds

    sds = asyncio.run(_drive())
    assert not sds._inflight


def test_invalidated_fetch_is_not_cached_when_nothing_re_reads():
    """DL-07: with no re-read racing it, the stale answer still is not cached."""
    calls = {"count": 0}

    async def _drive():
        release = asyncio.Event()

        async def fetcher(client, **params):
            calls["count"] += 1
            if calls["count"] == 1:
                await release.wait()
            return {"value": calls["count"]}

        sds = _make_sds(fetcher)
        early = asyncio.ensure_future(sds.get_or_fetch("srv", ServerDataType.PORTFOLIO))
        await asyncio.sleep(0.01)

        sds.invalidate_server("srv")
        release.set()
        await early

        assert sds.get("srv", ServerDataType.PORTFOLIO) is None
        assert await sds.get_or_fetch("srv", ServerDataType.PORTFOLIO) == {"value": 2}

    asyncio.run(_drive())
