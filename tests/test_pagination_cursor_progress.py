"""CORR-125: the two hand-rolled executor walks stop on a non-advancing cursor.

``fetch_all_executors`` got this guard in CORR-112 (covered by
``tests/test_fetcher_executors.py``); its two copies did not. An API that echoes
back the cursor it was sent while still returning a *full* page made both loops
re-issue the identical request and re-append the identical rows until their
safety cap — 5000 raw rows broadcast and written to the shared SDS cache in
``ws_manager``, 200 pages summed into per-agent PnL in ``performance``. Neither
hung; both silently produced duplicates. These tests drive each loop with such a
client and pin the request count and the row set.
"""

import asyncio

from condor.agents.performance import fetch_agent_performance_batch
from condor.web.ws_manager import WebSocketManager

# The page sizes each loop asks for, mirrored here so the fake can answer with a
# *full* page (a short page ends the walk on its own and would not exercise the
# cursor guard).
WS_FIRST_PAGE = 50
WS_NEXT_PAGE = 500
PERF_PAGE_SIZE = 50


def _page(start, count):
    """A page of ``count`` distinct executor rows numbered from ``start``."""
    return [
        {"id": f"e{i}", "status": "TERMINATED"} for i in range(start, start + count)
    ]


class EchoingCursorClient:
    """Client that answers every cursored request with the same cursor back.

    Unbounded on purpose: a loop missing the guard keeps getting served until it
    hits its own cap, so the test fails on the recorded call count instead of on
    an exception raised from inside the loop's own ``except`` block.
    """

    def __init__(self, first_page, repeat_page):
        self.calls = []
        self._first_page = first_page
        self._repeat_page = repeat_page
        self.executors = self._Executors(self)

    class _Executors:
        def __init__(self, outer):
            self._outer = outer

        async def search_executors(self, **kwargs):
            outer = self._outer
            outer.calls.append(kwargs)
            if not kwargs.get("cursor"):
                return {"executors": outer._first_page, "next_cursor": "c1"}
            # The echo: the walk is handed back the cursor it just used.
            return {"executors": outer._repeat_page, "next_cursor": "c1"}


class _FakeSDS:
    """Stands in for the ServerDataService the executor stream reads and writes.

    ``get`` answers with an empty (but present) cache so the stream skips the
    3s wait for a concurrent REST fill without ever populating ``_last_data``,
    which is what gates the pre-fetch under test.
    """

    def __init__(self):
        self.puts = []

    def get(self, server_name, data_type):
        return []

    def put(self, server_name, data_type, value):
        self.puts.append(value)


def test_ws_executor_prefetch_stops_when_the_cursor_does_not_advance(monkeypatch):
    """The pre-fetch must not re-page, or duplicates land in the SDS cache."""
    import condor.server_data_service as sds_module
    import config_manager as cm_module

    client = EchoingCursorClient(
        first_page=_page(0, WS_FIRST_PAGE),
        repeat_page=_page(WS_FIRST_PAGE, WS_NEXT_PAGE),
    )
    sds = _FakeSDS()

    class _FakeCM:
        async def get_client(self, server_name):
            return client

    monkeypatch.setattr(sds_module, "get_server_data_service", lambda: sds)
    monkeypatch.setattr(cm_module, "get_config_manager", lambda: _FakeCM())

    manager = WebSocketManager()

    async def _no_ws_stream(*args, **kwargs):
        return None

    # The pre-fetch is what is under test; the live socket that follows is not.
    manager._run_ws_stream = _no_ws_stream

    asyncio.run(manager._executor_stream("executors:srv"))

    assert len(client.calls) == 2, "the echoed cursor was re-requested"
    assert [call.get("cursor") for call in client.calls] == [None, "c1"]

    assert len(sds.puts) == 1, "the pre-fetch did not reach its sds.put"
    cached = sds.puts[0]
    assert len(cached) == WS_FIRST_PAGE + WS_NEXT_PAGE
    ids = [row["id"] for row in cached]
    assert len(set(ids)) == len(ids), "duplicate executors were cached"


def test_agent_perf_fetch_stops_when_the_cursor_does_not_advance():
    """A non-advancing cursor must not inflate the agent's rollup."""
    client = EchoingCursorClient(
        first_page=_page(0, PERF_PAGE_SIZE),
        repeat_page=_page(PERF_PAGE_SIZE, PERF_PAGE_SIZE),
    )

    out = asyncio.run(fetch_agent_performance_batch(client, ["agent-1"]))

    assert len(client.calls) == 2, "the echoed cursor was re-requested"
    assert [call.get("cursor") for call in client.calls] == [None, "c1"]

    perf = out["agent-1"]
    # Two real pages, counted once each — not MAX_PAGES x PAGE_SIZE.
    assert perf.trade_count == 2 * PERF_PAGE_SIZE
    ids = [row["id"] for row in perf.executors]
    assert len(set(ids)) == len(ids), "duplicate executors were summed"
