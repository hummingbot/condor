"""Tests for CORR-112: fetch_all_executors' cursor walk terminates and progresses.

The pagination loop used to end only on an absent cursor (behind a dead double
``break``) or on the item cap. Because the cursor is read from ``cursor`` as
well as ``next_cursor`` — the field many APIs echo back — a final partial page
could be re-requested until the cap, duplicating rows, and an echoed cursor on
an empty page spun forever. These tests pin the terminal conditions the sibling
walks in ``condor/web/ws_manager.py`` and ``condor/agents/performance.py``
already use, plus the progress guards.
"""

import asyncio

from condor.fetchers.executors import EXECUTORS_PAGE_SIZE, fetch_all_executors


def _page(start, count):
    """A page of ``count`` distinct executor rows numbered from ``start``."""
    return [{"id": f"e{i}"} for i in range(start, start + count)]


class FakeClient:
    """Client replaying a scripted list of search responses, recording calls."""

    def __init__(self, responses):
        self.calls = []
        self.executors = self._Executors(self)
        self._responses = list(responses)

    class _Executors:
        def __init__(self, outer):
            self._outer = outer

        async def search_executors(self, **kwargs):
            self._outer.calls.append(kwargs)
            responses = self._outer._responses
            if not responses:
                raise AssertionError(
                    f"unexpected extra request #{len(self._outer.calls)}: {kwargs}"
                )
            return responses.pop(0)


def test_short_final_page_with_echoed_cursor_is_not_refetched():
    """A full page then a short page whose cursor is echoed back ends the walk."""
    client = FakeClient(
        [
            {"executors": _page(0, EXECUTORS_PAGE_SIZE), "next_cursor": "c1"},
            # Terminal partial page, echoing the cursor it was asked with.
            {"executors": _page(EXECUTORS_PAGE_SIZE, 3), "cursor": "c1"},
        ]
    )

    items = asyncio.run(fetch_all_executors(client))

    assert len(client.calls) == 2
    assert client.calls[0].get("cursor") is None
    assert client.calls[1]["cursor"] == "c1"
    assert len(items) == EXECUTORS_PAGE_SIZE + 3
    ids = [item["id"] for item in items]
    assert len(set(ids)) == len(ids)  # every executor exactly once


def test_empty_page_with_next_cursor_stops_after_one_request():
    """An empty page ends the walk even when the API offers a next cursor."""
    client = FakeClient([{"executors": [], "next_cursor": "c1"}])

    items = asyncio.run(fetch_all_executors(client))

    assert items == []
    assert len(client.calls) == 1


def test_cursor_that_does_not_advance_stops_the_walk():
    """A full page repeating the cursor already in use stops instead of looping."""
    client = FakeClient(
        [
            {"executors": _page(0, EXECUTORS_PAGE_SIZE), "next_cursor": "c1"},
            {"executors": _page(0, EXECUTORS_PAGE_SIZE), "next_cursor": "c1"},
        ]
    )

    items = asyncio.run(fetch_all_executors(client))

    assert len(client.calls) == 2
    assert [call.get("cursor") for call in client.calls] == [None, "c1"]
    assert len(items) == 2 * EXECUTORS_PAGE_SIZE


def test_max_items_caps_the_walk_and_stops_requesting():
    """The item cap is the single cap check and it ends the walk."""
    max_items = EXECUTORS_PAGE_SIZE + 200
    client = FakeClient(
        [
            {"executors": _page(0, EXECUTORS_PAGE_SIZE), "next_cursor": "c1"},
            {"executors": _page(EXECUTORS_PAGE_SIZE, 200), "next_cursor": "c2"},
        ]
    )

    items = asyncio.run(fetch_all_executors(client, max_items=max_items))

    assert len(items) == max_items
    assert len(client.calls) == 2
    # The last page is requested for exactly the remaining allowance.
    assert client.calls[1]["limit"] == 200


def test_absent_cursor_ends_the_walk():
    """The plain terminal case: a full page with no cursor at all."""
    client = FakeClient([{"executors": _page(0, EXECUTORS_PAGE_SIZE)}])

    items = asyncio.run(fetch_all_executors(client))

    assert len(items) == EXECUTORS_PAGE_SIZE
    assert len(client.calls) == 1
