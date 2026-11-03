"""CORR-601: a stray cancellation must not permanently retire the SDS poller.

``_rate_limited_fetch`` guarded its fetch with ``except Exception``, which
cannot catch ``CancelledError`` (a ``BaseException`` since 3.8). So a shared
single-flight fetch cancelled by one of *its* dependencies raised straight
through ``_poll_tick``'s gather into ``_poll_loop``, which ``break``\\ -ed on
cancellation unconditionally — while ``_running`` stayed True. ``start()``
returns early on ``_running``, so the poll loop was gone for the lifetime of
the process, silently, and every surface (dashboard WS, REST, Telegram) served
the last cached snapshot until Condor was restarted.

The distinction the fix draws is the one CORR-332 drew: "I was cancelled"
(propagate — a shutdown must never be swallowed) versus "what I awaited was
cancelled" (log and carry on).
"""

import asyncio

from condor import server_data_service as sds_module
from condor.server_data_service import ServerDataService, ServerDataType


def _make_sds(fetch_func):
    """Fresh (non-singleton) SDS with a fake client and a registered fetcher."""
    sds = ServerDataService()

    async def _fake_get_client(server_name):
        return object()

    sds._get_client = _fake_get_client
    sds.register_fetch(ServerDataType.PORTFOLIO, fetch_func)
    return sds


async def _until(predicate, timeout=3.0):
    """Wait for ``predicate()`` to hold, yielding to the poll loop meanwhile."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def test_poll_loop_survives_a_fetch_cancelled_by_its_dependency(monkeypatch):
    """A cancelled shared fetch must not take the poll loop down with it."""
    monkeypatch.setattr(sds_module, "_POLL_TICK", 0.01)

    calls = {"count": 0}
    armed = {"on": False}

    async def _drive():
        # Something the fetch awaits and does not own: another task, cancelled
        # by whoever does own it while this fetch is parked on it.
        dependency = asyncio.ensure_future(asyncio.sleep(3600))

        async def fetcher(client, **params):
            calls["count"] += 1
            if armed["on"]:
                armed["on"] = False
                await dependency  # cancelled out from under us
            return {"tick": calls["count"]}

        sds = _make_sds(fetcher)
        key = await sds.subscribe("srv", ServerDataType.PORTFOLIO, "sub", interval=0.02)
        assert sds._cache[key].value == {"tick": 1}, "priming fetch cached a value"

        armed["on"] = True
        sds.start()
        try:
            # Let the poll loop reach the armed fetch and park on the dependency.
            assert await _until(lambda: calls["count"] >= 2), "poll loop never ticked"
            assert await _until(lambda: bool(sds._inflight)), "fetch never in flight"

            dependency.cancel()

            # The loop must still be alive and still refreshing the cache.
            refreshed = await _until(lambda: sds._cache[key].value != {"tick": 1})
            poll_task = sds._poll_task
            assert poll_task is not None
            assert (
                not poll_task.done()
            ), "the poll task died on a cancellation it did not request"
            assert sds._running is True
            assert refreshed, "the poll loop stopped refreshing the cache"
            assert sds._cache[key].value["tick"] >= 3
        finally:
            sds.stop()
            await asyncio.sleep(0.02)

    asyncio.run(_drive())


def test_stop_still_stops_the_poll_loop_mid_fetch(monkeypatch):
    """The absorbed cancellation must never absorb a real shutdown.

    ``stop()`` while a fetch is in flight cancels the poll task, which cancels
    the gather child parked on that fetch; that cancellation is *ours* and has
    to propagate all the way out, leaving the task cancelled and ``_running``
    False.
    """
    monkeypatch.setattr(sds_module, "_POLL_TICK", 0.01)

    calls = {"count": 0}
    armed = {"on": False}

    async def _drive():
        async def fetcher(client, **params):
            calls["count"] += 1
            if armed["on"]:
                await asyncio.sleep(3600)  # never settles
            return {"tick": calls["count"]}

        sds = _make_sds(fetcher)
        await sds.subscribe("srv", ServerDataType.PORTFOLIO, "sub", interval=0.02)

        armed["on"] = True
        sds.start()
        assert await _until(lambda: calls["count"] >= 2), "poll loop never ticked"
        assert await _until(lambda: bool(sds._inflight)), "fetch never in flight"

        sds.stop()
        poll_task = sds._poll_task
        assert await _until(lambda: poll_task.done()), "stop() did not stop the loop"
        assert poll_task.cancelled(), "the shutdown cancellation was swallowed"
        assert sds._running is False

    asyncio.run(_drive())
