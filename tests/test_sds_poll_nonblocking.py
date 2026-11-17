"""DL-03: one slow fetch must not stall the SDS poller for every other key.

``_poll_tick`` gathered every due key across all servers, and the poll loop
awaited that gather before sleeping into the next tick. So the slowest due
fetch anywhere — a hung server riding out the 60s client timeout, a routinely
slow 3M history window — set the refresh cadence for every key on every
server, with nothing logged.

The loop now only *starts* a tracked task per due key and moves on; a key
whose previous fetch is still running is skipped rather than fetched twice.
"""

import asyncio

from condor import server_data_service as sds_module
from condor.server_data_service import ServerDataService, ServerDataType


def _make_sds(fetchers):
    """Fresh (non-singleton) SDS with a fake client and the given fetchers."""
    sds = ServerDataService()

    async def _fake_get_client(server_name):
        return object()

    sds._get_client = _fake_get_client
    for data_type, fetch_func in fetchers.items():
        sds.register_fetch(data_type, fetch_func)
    return sds


async def _until(predicate, timeout=3.0):
    """Wait for ``predicate()`` to hold, yielding to the poll loop meanwhile."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def test_a_hung_fetch_does_not_stop_other_keys_from_refreshing(monkeypatch):
    monkeypatch.setattr(sds_module, "_POLL_TICK", 0.01)

    fast = {"count": 0}
    slow = {"count": 0}
    armed = {"on": False}

    async def _drive():
        release = asyncio.Event()

        async def fast_fetcher(client, **params):
            fast["count"] += 1
            return {"tick": fast["count"]}

        async def slow_fetcher(client, **params):
            slow["count"] += 1
            if armed["on"]:
                await release.wait()  # a server that hangs instead of refusing
            return {"tick": slow["count"]}

        sds = _make_sds(
            {
                ServerDataType.PORTFOLIO: fast_fetcher,
                ServerDataType.POSITIONS: slow_fetcher,
            }
        )
        await sds.subscribe("a", ServerDataType.PORTFOLIO, "sub", interval=0.02)
        await sds.subscribe("b", ServerDataType.POSITIONS, "sub", interval=0.02)

        armed["on"] = True
        sds.start()
        try:
            assert await _until(lambda: slow["count"] >= 2), "slow key never polled"
            before = fast["count"]

            # The slow fetch is parked; the fast key must keep refreshing.
            assert await _until(
                lambda: fast["count"] >= before + 5
            ), "a hung fetch on one server stalled another server's key"

            # ...and the hung key is not fetched again while it is in flight.
            assert slow["count"] == 2, "an in-flight key was fetched a second time"

            # Once it settles, the slow key resumes polling on its own.
            armed["on"] = False
            release.set()
            assert await _until(lambda: slow["count"] >= 4), "slow key never resumed"
        finally:
            sds.stop()
            await asyncio.sleep(0.02)

    asyncio.run(_drive())


def test_stop_cancels_the_poll_fetch_tasks(monkeypatch):
    """``stop()`` must not leave per-key poll tasks behind."""
    monkeypatch.setattr(sds_module, "_POLL_TICK", 0.01)

    calls = {"count": 0}
    armed = {"on": False}

    async def _drive():
        async def fetcher(client, **params):
            calls["count"] += 1
            if armed["on"]:
                await asyncio.sleep(3600)  # never settles
            return {"tick": calls["count"]}

        sds = _make_sds({ServerDataType.PORTFOLIO: fetcher})
        await sds.subscribe("srv", ServerDataType.PORTFOLIO, "sub", interval=0.02)

        armed["on"] = True
        sds.start()
        assert await _until(lambda: bool(sds._poll_fetches)), "no poll fetch started"
        tasks = list(sds._poll_fetch_tasks)

        sds.stop()
        assert await _until(lambda: all(t.done() for t in tasks))
        assert all(t.cancelled() for t in tasks), "a poll fetch outlived stop()"
        assert not sds._poll_fetches
        assert not sds._poll_fetch_tasks

    asyncio.run(_drive())
