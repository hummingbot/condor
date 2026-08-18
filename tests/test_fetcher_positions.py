"""Tests for CORR-113: fetch_positions filters server-side and walks the cursor.

The fetcher used to ask for a flat ``limit=100`` — every account, every
connector, no cursor — and only then filter on ``connector_name`` in Python.
Because the cap landed *before* the filter, an account holding more than 100
positions could return the requested connector partially or completely empty,
indistinguishable from "flat". These tests pin the server-side
``connector_names`` filter and the cursor walk, in the shape of the walk in
``condor/fetchers/executors.py``.
"""

import asyncio
import logging

from condor.fetchers.positions import POSITIONS_PAGE_SIZE, fetch_positions


def _positions(connector, start, count):
    """``count`` distinct position rows on ``connector``, numbered from ``start``."""
    return [
        {"trading_pair": f"P{i}-USDT", "connector_name": connector}
        for i in range(start, start + count)
    ]


class FakeClient:
    """Client replaying a scripted list of responses, recording every call."""

    def __init__(self, responses):
        self.calls = []
        self.trading = self._Trading(self)
        self._responses = list(responses)

    class _Trading:
        def __init__(self, outer):
            self._outer = outer

        async def get_positions(self, **kwargs):
            self._outer.calls.append(kwargs)
            responses = self._outer._responses
            if not responses:
                raise AssertionError(
                    f"unexpected extra request #{len(self._outer.calls)}: {kwargs}"
                )
            return responses.pop(0)


class FakeAccountClient:
    """Client holding an account's positions and honouring the API's filters.

    Pages ``limit`` rows at a time from the rows matching ``connector_names``,
    the way the real ``/trading/positions`` endpoint does.
    """

    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.trading = self._Trading(self)

    class _Trading:
        def __init__(self, outer):
            self._outer = outer

        async def get_positions(self, **kwargs):
            outer = self._outer
            outer.calls.append(kwargs)
            connectors = kwargs.get("connector_names")
            matching = [
                row
                for row in outer.rows
                if connectors is None or row["connector_name"] in connectors
            ]
            offset = int(kwargs.get("cursor") or 0)
            limit = kwargs["limit"]
            page = matching[offset : offset + limit]
            next_offset = offset + len(page)
            return {
                "data": page,
                "next_cursor": (
                    str(next_offset) if next_offset < len(matching) else None
                ),
            }


def test_connector_name_is_sent_as_a_server_side_filter():
    """The requested connector reaches the API, so the cap applies after it."""
    client = FakeClient([{"data": _positions("hyperliquid_perpetual", 0, 3)}])

    positions = asyncio.run(
        fetch_positions(client, connector_name="hyperliquid_perpetual")
    )

    assert client.calls[0]["connector_names"] == ["hyperliquid_perpetual"]
    assert len(positions) == 3


def test_no_connector_name_sends_no_filter_and_returns_everything():
    """Without a connector the walk is unfiltered and still paginates."""
    rows = _positions("binance_perpetual", 0, 150) + _positions(
        "hyperliquid_perpetual", 150, 150
    )
    client = FakeAccountClient(rows)

    positions = asyncio.run(fetch_positions(client))

    assert all("connector_names" not in call for call in client.calls)
    assert len(client.calls) > 1  # walked past the first page
    assert len(positions) == len(rows)


def test_two_cursor_pages_are_both_returned():
    """A full page followed by a short page yields the rows of both."""
    client = FakeClient(
        [
            {
                "data": _positions("binance_perpetual", 0, POSITIONS_PAGE_SIZE),
                "next_cursor": "c1",
            },
            {"data": _positions("binance_perpetual", POSITIONS_PAGE_SIZE, 5)},
        ]
    )

    positions = asyncio.run(fetch_positions(client))

    assert len(client.calls) == 2
    assert client.calls[0].get("cursor") is None
    assert client.calls[1]["cursor"] == "c1"
    assert len(positions) == POSITIONS_PAGE_SIZE + 5
    pairs = [p["trading_pair"] for p in positions]
    assert len(set(pairs)) == len(pairs)  # every position exactly once


def test_every_position_of_the_connector_survives_a_250_position_account():
    """Past the old flat 100-row cap, the connector's positions all come back."""
    rows = _positions("binance_perpetual", 0, 130) + _positions(
        "hyperliquid_perpetual", 130, 120
    )
    client = FakeAccountClient(rows)

    positions = asyncio.run(
        fetch_positions(client, connector_name="hyperliquid_perpetual")
    )

    assert len(positions) == 120
    assert {p["connector_name"] for p in positions} == {"hyperliquid_perpetual"}


def test_reaching_the_safety_cap_truncates_and_warns(caplog):
    """The cap ends the walk, and says so instead of looking like the whole set."""
    client = FakeAccountClient(_positions("binance_perpetual", 0, 500))

    with caplog.at_level(logging.WARNING, logger="condor.fetchers.positions"):
        positions = asyncio.run(fetch_positions(client, limit=POSITIONS_PAGE_SIZE * 2))

    assert len(positions) == POSITIONS_PAGE_SIZE * 2
    assert any("safety cap" in record.message for record in caplog.records)


def test_last_page_is_requested_for_only_the_remaining_allowance():
    """The cap is never overshot: the final request asks for what is left."""
    limit = POSITIONS_PAGE_SIZE + 20
    client = FakeAccountClient(_positions("binance_perpetual", 0, 500))

    positions = asyncio.run(fetch_positions(client, limit=limit))

    assert len(positions) == limit
    assert client.calls[-1]["limit"] == 20


def test_client_side_filter_still_guards_a_server_that_ignores_the_filter():
    """A server returning foreign connectors anyway does not leak them through."""
    client = FakeClient(
        [
            {
                "data": _positions("binance_perpetual", 0, 2)
                + _positions("hyperliquid_perpetual", 2, 3)
            }
        ]
    )

    positions = asyncio.run(
        fetch_positions(client, connector_name="hyperliquid_perpetual")
    )

    assert len(positions) == 3
    assert {p["connector_name"] for p in positions} == {"hyperliquid_perpetual"}


def test_extra_keyword_arguments_are_still_accepted():
    """SDS passes fetch kwargs positionally and by name; defaults must hold."""
    client = FakeClient([{"data": _positions("binance_perpetual", 0, 1)}])

    positions = asyncio.run(fetch_positions(client, None, server_name="prod"))

    assert len(positions) == 1
    assert client.calls[0]["limit"] == POSITIONS_PAGE_SIZE
