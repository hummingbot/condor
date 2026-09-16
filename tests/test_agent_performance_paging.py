"""PERF-599: the agent performance walk pages at the fetchers layer's page size.

``fetch_agent_performance_batch`` used to declare its own ``PAGE_SIZE = 50``
against the same ``client.executors.search_executors`` that
``condor.fetchers.executors`` walks 500 rows at a time — ten times the
sequential round trips for identical rows, on every agent tick and every 30s
web rollup, fanned out over every active agent at once.

These tests pin the two halves of that change: the page size now comes from the
fetchers' own constant, and the safety cap stays where it was (10,000 rows per
agent) rather than moving with the page size.
"""

import asyncio

from condor.agents.performance import fetch_agent_performance_batch
from condor.fetchers.executors import EXECUTORS_PAGE_SIZE

MAX_ROWS = 10_000  # the per-agent row cap, pinned here so a drift fails loudly


class PagingClient:
    """Serves a fixed history in cursor-advancing pages of exactly ``limit``.

    The cursor always advances and is always present, so the walk ends only on
    a short/empty page — which is what makes the recorded call count a faithful
    measure of the page size actually asked for.
    """

    def __init__(self, total_rows: int):
        self._rows = [
            {"id": f"e{i}", "status": "TERMINATED"} for i in range(total_rows)
        ]
        self.calls: list[dict] = []
        self.executors = self._Executors(self)

    class _Executors:
        def __init__(self, outer):
            self._outer = outer

        async def search_executors(self, **kwargs):
            outer = self._outer
            outer.calls.append(kwargs)
            offset = int(kwargs.get("cursor") or 0)
            limit = kwargs["limit"]
            page = outer._rows[offset : offset + limit]
            return {"executors": page, "next_cursor": str(offset + len(page))}

    @property
    def limits(self) -> list[int]:
        return [call["limit"] for call in self.calls]


def test_a_thousand_row_history_costs_three_requests_not_twenty_one():
    """500 + 500 + a short final page, instead of 20 pages of 50 plus one."""
    client = PagingClient(1_000)

    out = asyncio.run(fetch_agent_performance_batch(client, ["agent-1"]))

    assert client.limits == [EXECUTORS_PAGE_SIZE] * 3
    assert len(client.calls) == 3, f"walked in {len(client.calls)} requests"
    assert out["agent-1"].trade_count == 1_000


def test_every_row_of_the_history_still_lands_exactly_once():
    """A bigger page must not drop, duplicate or reorder the rows it carries."""
    client = PagingClient(1_234)

    perf = asyncio.run(fetch_agent_performance_batch(client, ["agent-1"]))["agent-1"]

    ids = [row["id"] for row in perf.executors]
    assert ids == [f"e{i}" for i in range(1_234)]


def test_the_row_cap_stays_at_ten_thousand():
    """The cap is expressed in rows, so it does not scale with the page size."""
    client = PagingClient(MAX_ROWS + 2_500)

    perf = asyncio.run(fetch_agent_performance_batch(client, ["agent-1"]))["agent-1"]

    assert perf.trade_count == MAX_ROWS
    assert len(perf.executors) == MAX_ROWS
    # The walker clamps each page to what is left of the cap, so no request
    # fetches rows that would be discarded, and none overshoots it.
    assert sum(client.limits) == MAX_ROWS
    assert client.limits == [EXECUTORS_PAGE_SIZE] * (MAX_ROWS // EXECUTORS_PAGE_SIZE)


def test_the_walk_asks_for_no_hardcoded_page_size():
    """The limit on the wire is the fetchers' constant, not a local literal."""
    client = PagingClient(10)

    asyncio.run(fetch_agent_performance_batch(client, ["agent-1"]))

    assert client.limits == [EXECUTORS_PAGE_SIZE]
