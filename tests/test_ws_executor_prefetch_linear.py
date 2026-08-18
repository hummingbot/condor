"""PERF-135: the executor pre-fetch transforms each raw row exactly once.

The progressive pre-fetch in ``ws_manager._executor_stream`` broadcasts the
accumulated snapshot after every page. It used to build that snapshot by
re-running ``_transform_executors`` over the *whole* accumulated raw list, so a
row fetched on page 1 was pushed through ``ExecutorInfo.from_raw`` once per
remaining page: O(pages x rows) pydantic validations instead of O(rows), on the
event loop the candle and bots broadcasts share.

These tests pin the complexity rather than the wording of the fix: they count
``from_raw`` invocations across a multi-page walk and assert the count is the
number of rows fetched, for two different page counts, so a re-introduced
re-transform fails on the count and not on a snapshot comparison. The output is
pinned separately: every broadcast must still be the full accumulated prefix.
"""

import asyncio

import pytest

import condor.server_data_service as sds_module
import config_manager as cm_module
from condor.web.models import ExecutorInfo
from condor.web.ws_manager import WebSocketManager

# Mirrored from the pre-fetch so the fake serves *full* pages: a short page ends
# the walk, which is how each scenario below terminates on its last page.
WS_FIRST_PAGE = 50
WS_NEXT_PAGE = 500


def _rows(start, count):
    """``count`` distinct raw executor rows numbered from ``start``."""
    return [
        {
            "id": f"e{i}",
            "type": "position_executor",
            "config": {"connector_name": "binance", "trading_pair": "BTC-USDT"},
            "status": "TERMINATED",
        }
        for i in range(start, start + count)
    ]


class _PagingClient:
    """Serves a fixed list of pages, advancing the cursor on each one."""

    def __init__(self, pages):
        self._pages = pages
        self.calls = []
        self.executors = self._Executors(self)

    class _Executors:
        def __init__(self, outer):
            self._outer = outer

        async def search_executors(self, **kwargs):
            outer = self._outer
            index = len(outer.calls)
            outer.calls.append(kwargs)
            page = outer._pages[index] if index < len(outer._pages) else []
            cursor = f"c{index + 1}" if index + 1 < len(outer._pages) else None
            return {"executors": page, "next_cursor": cursor}


class _FakeSDS:
    """Empty-but-present cache, so the stream reaches the pre-fetch under test.

    ``get`` returning ``[]`` (not ``None``) skips the 3s wait for a concurrent
    REST fill without ever populating ``_last_data``, which is what gates the
    pre-fetch.
    """

    def __init__(self):
        self.puts = []

    def get(self, server_name, data_type):
        return []

    def put(self, server_name, data_type, value):
        self.puts.append(value)


def _run_prefetch(monkeypatch, pages):
    """Drive one full pre-fetch over ``pages``; return what it did.

    Returns ``(from_raw_calls, broadcasts, sds_puts)``.
    """
    client = _PagingClient(pages)
    sds = _FakeSDS()

    class _FakeCM:
        async def get_client(self, server_name):
            return client

    monkeypatch.setattr(sds_module, "get_server_data_service", lambda: sds)
    monkeypatch.setattr(cm_module, "get_config_manager", lambda: _FakeCM())

    # Count every raw row pushed through the pydantic transform. This is the
    # cost the item is about; the fix is only correct if it is O(total rows).
    calls = {"n": 0}
    real_from_raw = ExecutorInfo.from_raw.__func__

    @classmethod
    def _counting_from_raw(cls, ex):
        calls["n"] += 1
        return real_from_raw(cls, ex)

    monkeypatch.setattr(ExecutorInfo, "from_raw", _counting_from_raw)

    manager = WebSocketManager()
    broadcasts = []

    async def _record(channel, data):
        broadcasts.append(data)

    manager.broadcast = _record

    async def _no_ws_stream(*args, **kwargs):
        return None

    # The pre-fetch is under test; the live socket that follows it is not.
    manager._run_ws_stream = _no_ws_stream

    asyncio.run(manager._executor_stream("executors:srv"))
    return calls["n"], broadcasts, sds.puts


def _pages_of(sizes):
    """Consecutive pages of the given sizes, with globally unique row ids."""
    pages = []
    start = 0
    for size in sizes:
        pages.append(_rows(start, size))
        start += size
    return pages


@pytest.mark.parametrize(
    "sizes",
    [
        # Two pages: quadratic work would be 50 + 250 = 300 for 250 rows.
        [WS_FIRST_PAGE, 200],
        # Five pages: quadratic work would be 50 + 550 + 1050 + 1550 + 1750 =
        # 4950 for 1750 rows -- the gap grows with the page count, which is
        # exactly what makes the count a complexity assertion and not a
        # constant-factor one.
        [WS_FIRST_PAGE, WS_NEXT_PAGE, WS_NEXT_PAGE, WS_NEXT_PAGE, 200],
    ],
)
def test_prefetch_transforms_each_row_exactly_once(monkeypatch, sizes):
    """``from_raw`` runs once per fetched row, whatever the page count."""
    pages = _pages_of(sizes)
    total_rows = sum(sizes)

    from_raw_calls, broadcasts, _ = _run_prefetch(monkeypatch, pages)

    assert len(broadcasts) == len(pages), "one broadcast per page"
    assert from_raw_calls == total_rows, (
        "the accumulated list was re-transformed: "
        f"{from_raw_calls} validations for {total_rows} rows"
    )


def test_prefetch_broadcasts_the_full_accumulated_snapshot(monkeypatch):
    """Each page still broadcasts every row seen so far, in order."""
    sizes = [WS_FIRST_PAGE, WS_NEXT_PAGE, 200]
    pages = _pages_of(sizes)
    all_raw = [row for page in pages for row in page]

    _, broadcasts, puts = _run_prefetch(monkeypatch, pages)

    expected_full = WebSocketManager._transform_executors(all_raw)
    assert (
        broadcasts[-1] == expected_full
    ), "final snapshot differs from a whole-list transform"

    # Every intermediate broadcast is the prefix of that same list.
    seen = 0
    for page, payload in zip(pages, broadcasts):
        seen += len(page)
        assert payload == expected_full[:seen]

    # And the snapshots are independent: a later page must not mutate an
    # already-broadcast payload (``broadcast`` retains what it is handed).
    assert len(broadcasts[0]) == WS_FIRST_PAGE

    assert len(puts) == 1, "the pre-fetch did not reach its sds.put"
    assert puts[0] == all_raw, "SDS must still receive the complete raw list"
