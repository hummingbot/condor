"""ARCH-128: one cursor walk, shared by every paginated endpoint Condor reads.

The same loop was hand-written at five call sites and the copies drifted apart.
CORR-112 and CORR-113 fixed the same terminal conditions twice on the same day in
two different fetchers; CORR-125 then had to add the non-advancing-cursor guard
to the two copies that had never received it, and CORR-110 wrote a fifth walk for
a different endpoint. These tests pin the single walker all five now delegate to:
the four terminal conditions, the row cap and its warning, the ``cursor`` kwarg
being omitted rather than nulled on the first request, and the per-page yielding
that ``ws_manager``'s progressive broadcast depends on.
"""

import asyncio

import pytest

from condor.fetchers._pagination import collect_pages, next_cursor, walk_pages


def _rows(start, count):
    """A page of ``count`` distinct rows numbered from ``start``."""
    return [{"id": f"r{i}"} for i in range(start, start + count)]


def _extract(result):
    return result.get("data", []) if isinstance(result, dict) else []


class ScriptedEndpoint:
    """Replays a scripted list of responses, recording the kwargs it was called with."""

    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError(
                f"unexpected extra request #{len(self.calls)}: {kwargs}"
            )
        return self._responses.pop(0)


class EchoingEndpoint:
    """Always answers with a full page and the same cursor back, without limit.

    Unbounded on purpose: a walker missing the progress guard keeps being served
    until its own cap, so the test fails on the recorded call count rather than
    on an exception raised from inside the walk.
    """

    def __init__(self, page_rows):
        self.calls = []
        self._page_rows = page_rows

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"data": _rows(0, self._page_rows), "next_cursor": "c1"}


def _collect(endpoint, **kw):
    kw.setdefault("page_size", 10)
    kw.setdefault("max_items", 1000)
    return asyncio.run(collect_pages(endpoint, _extract, **kw))


# -- cursor extraction --------------------------------------------------------


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"next_cursor": "a"}, "a"),
        ({"cursor": "b"}, "b"),
        ({"pagination": {"next_cursor": "c"}}, "c"),
        ({"pagination": {"cursor": "d"}}, "d"),
        # Top level wins over the nested spelling.
        ({"next_cursor": "a", "pagination": {"cursor": "d"}}, "a"),
        ({}, None),
        ({"next_cursor": ""}, None),
        ({"pagination": "not-a-dict"}, None),
        ([{"id": "r0"}], None),
        (None, None),
    ],
)
def test_next_cursor_reads_every_spelling_the_backend_uses(result, expected):
    """All four cursor spellings resolve; anything else is "no next page"."""
    assert next_cursor(result) == expected


# -- terminal conditions ------------------------------------------------------


def test_absent_cursor_ends_the_walk():
    """A full page with no cursor at all is the last page."""
    endpoint = ScriptedEndpoint([{"data": _rows(0, 10)}])

    rows = _collect(endpoint)

    assert len(rows) == 10
    assert len(endpoint.calls) == 1


def test_short_page_ends_the_walk_even_with_a_cursor():
    """Fewer rows than requested is the last page, whatever cursor comes with it."""
    endpoint = ScriptedEndpoint([{"data": _rows(0, 3), "next_cursor": "c1"}])

    rows = _collect(endpoint)

    assert len(rows) == 3
    assert len(endpoint.calls) == 1


def test_empty_page_ends_the_walk_even_with_a_cursor():
    """An empty page ends the walk even when the API offers a next cursor."""
    endpoint = ScriptedEndpoint([{"data": [], "next_cursor": "c1"}])

    rows = _collect(endpoint)

    assert rows == []
    assert len(endpoint.calls) == 1


def test_echoed_cursor_stops_the_walk_instead_of_duplicating_rows():
    """The API handing back the cursor it was sent ends the walk (CORR-125)."""
    endpoint = EchoingEndpoint(page_rows=10)

    rows = _collect(endpoint, page_size=10, max_items=1000)

    # Second request goes out with c1, comes back with c1: stop there.
    assert [call.get("cursor") for call in endpoint.calls] == [None, "c1"]
    assert len(rows) == 20  # not the 1000 the cap would otherwise allow


def test_the_walk_continues_while_the_cursor_advances():
    """Distinct cursors and full pages keep the walk going."""
    endpoint = ScriptedEndpoint(
        [
            {"data": _rows(0, 10), "next_cursor": "c1"},
            {"data": _rows(10, 10), "pagination": {"next_cursor": "c2"}},
            {"data": _rows(20, 4), "next_cursor": "c3"},
        ]
    )

    rows = _collect(endpoint)

    assert [call.get("cursor") for call in endpoint.calls] == [None, "c1", "c2"]
    ids = [r["id"] for r in rows]
    assert len(ids) == 24
    assert len(set(ids)) == 24  # every row exactly once


def test_the_first_request_omits_the_cursor_rather_than_nulling_it():
    """Endpoints that reject an explicit null must not see a ``cursor`` key."""
    endpoint = ScriptedEndpoint([{"data": _rows(0, 3)}])

    _collect(endpoint)

    assert "cursor" not in endpoint.calls[0]
    assert endpoint.calls[0]["limit"] == 10


# -- the cap ------------------------------------------------------------------


def test_max_items_caps_the_walk_and_clamps_the_last_request():
    """The cap bounds the rows kept and the last page is asked for only the rest."""
    endpoint = ScriptedEndpoint(
        [
            {"data": _rows(0, 10), "next_cursor": "c1"},
            {"data": _rows(10, 4), "next_cursor": "c2"},
        ]
    )

    rows = _collect(endpoint, page_size=10, max_items=14)

    assert len(rows) == 14
    assert len(endpoint.calls) == 2
    assert endpoint.calls[1]["limit"] == 4  # never fetch rows we would discard


def test_on_truncated_fires_only_when_the_cap_ended_the_walk():
    """Hitting the cap is reported; ending on exhaustion is not."""
    fired = []
    # Distinct cursors and always-full pages, so only the cap can stop this walk.
    cursors = iter(range(100))

    async def advancing(**kwargs):
        return {"data": _rows(0, kwargs["limit"]), "next_cursor": f"c{next(cursors)}"}

    rows = _collect(
        advancing, page_size=10, max_items=25, on_truncated=lambda: fired.append(1)
    )

    assert len(rows) == 25
    assert fired == [1]


def test_a_walk_that_exhausts_on_the_cap_boundary_is_not_reported_as_truncated():
    """Exactly ``max_items`` rows, then the history ends: nothing was dropped."""
    fired = []
    endpoint = ScriptedEndpoint([{"data": _rows(0, 10)}])

    rows = _collect(
        endpoint, page_size=10, max_items=10, on_truncated=lambda: fired.append(1)
    )

    assert len(rows) == 10
    assert fired == []


# -- per-page delivery --------------------------------------------------------


def test_walk_pages_yields_each_page_as_it_lands():
    """``ws_manager`` broadcasts per page, so pages arrive one at a time."""
    endpoint = ScriptedEndpoint(
        [
            {"data": _rows(0, 10), "next_cursor": "c1"},
            {"data": _rows(10, 2), "next_cursor": "c2"},
        ]
    )
    seen = []

    async def drive():
        async for page in walk_pages(endpoint, _extract, page_size=10, max_items=1000):
            seen.append(len(page))

    asyncio.run(drive())

    # The final (short) page is yielded like any other, before the walk ends.
    assert seen == [10, 2]


def test_first_page_size_applies_to_the_first_request_only():
    """A small first page paints the UI early; the rest use the full page size."""
    endpoint = ScriptedEndpoint(
        [
            {"data": _rows(0, 5), "next_cursor": "c1"},
            {"data": _rows(5, 10), "next_cursor": "c2"},
            {"data": _rows(15, 1)},
        ]
    )

    rows = _collect(endpoint, page_size=10, first_page_size=5, max_items=1000)

    assert [call["limit"] for call in endpoint.calls] == [5, 10, 10]
    assert len(rows) == 16
