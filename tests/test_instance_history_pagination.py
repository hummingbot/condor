"""CORR-110: fetch_instance_history walks the cursor instead of keeping one page.

The endpoint's ``limit`` is a *row* budget and rows are per controller, so one
500-row page holds ~41h of 5m samples for a single-controller bot but only ~14h
for a three-controller one. Everything older than the first retained row reads as
zero through ``_cum_at``, so a truncated history hands one session the instance's
whole lifetime cumulative — including previous owners' share.

These tests drive the walk with fakes that page, that stall, and that fail
part-way, and pin the attribution invariant the walk exists to protect: the sum
of every owner's window equals the instance's final cumulative.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from condor.fetchers.bot_performance import (
    HISTORY_PAGE_SIZE,
    fetch_instance_history,
    slice_history,
)

BASE_TS = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _row(bucket: int, controller: int = 0, interval_sec: int = 300) -> dict:
    """One controller's cumulative snapshot for bucket ``bucket``.

    Cumulative by construction: realized/volume/trades only ever grow, which is
    what ``_cum_at`` assumes when it differences two instants.
    """
    ts = BASE_TS + timedelta(seconds=bucket * interval_sec)
    return {
        "timestamp": ts.isoformat(),
        "controller_id": f"ctrl-{controller}",
        "performance": {
            "realized_pnl_quote": float(bucket + 1),
            "volume_traded": float((bucket + 1) * 10),
            "cum_fees_quote": float(bucket + 1) / 10,
            "close_type_counts": {"CloseType.TAKE_PROFIT": bucket + 1},
        },
    }


def _epoch(bucket: int, interval_sec: int = 300) -> float:
    return (BASE_TS + timedelta(seconds=bucket * interval_sec)).timestamp()


class _PagingHistory:
    """Serves ``rows`` in cursor-addressed pages, honouring the requested limit."""

    def __init__(self, rows: list[dict], *, echo_cursor: bool = False):
        self.rows = rows
        self.echo_cursor = echo_cursor
        self.calls: list[dict] = []

    async def get_controller_performance_history(self, **kw):
        self.calls.append(kw)
        offset = int(kw.get("cursor") or 0)
        limit = int(kw.get("limit") or HISTORY_PAGE_SIZE)
        page = self.rows[offset : offset + limit]
        nxt = offset + len(page)
        cursor = str(nxt) if nxt < len(self.rows) else None
        if self.echo_cursor:
            # A stalling API hands back the cursor it was sent while still
            # returning a full page — the CORR-125 case, guarded here too.
            cursor = kw.get("cursor") or cursor
        return {"data": page, "pagination": {"next_cursor": cursor}}


def _client(handler) -> SimpleNamespace:
    return SimpleNamespace(bot_orchestration=handler)


def test_walks_every_cursor_page():
    """Three full pages of 500 rows yield all 1500 timestamps, not just the first."""
    rows = [_row(b) for b in range(3 * HISTORY_PAGE_SIZE)]
    handler = _PagingHistory(rows)

    hist = asyncio.run(fetch_instance_history(_client(handler), "bot-1"))

    assert len(hist) == 3 * HISTORY_PAGE_SIZE
    assert len(handler.calls) == 3
    # First request carries no cursor; every later one does.
    assert "cursor" not in handler.calls[0]
    assert [c["cursor"] for c in handler.calls[1:]] == ["500", "1000"]
    # Sorted, and the final row is the last bucket's cumulative.
    assert [r[0] for r in hist] == sorted(r[0] for r in hist)
    assert hist[-1][1] == float(3 * HISTORY_PAGE_SIZE)


def test_three_controller_bot_over_three_days_keeps_every_bucket():
    """864 5m buckets x 3 controllers = 2592 rows: all 864 timestamps survive."""
    buckets = 3 * 24 * 12  # 3 days of 5m samples
    rows = [_row(b, controller=c) for b in range(buckets) for c in range(3)]
    handler = _PagingHistory(rows)

    hist = asyncio.run(fetch_instance_history(_client(handler), "bot-3ctrl"))

    assert len(hist) == buckets  # not the ~166 a single page would have held
    assert len(handler.calls) == 6  # 2592 rows / 500 per page
    # Controllers at the same timestamp are summed, not collapsed.
    assert hist[-1][1] == pytest.approx(3 * float(buckets))
    assert hist[-1][3] == pytest.approx(3 * float(buckets))  # cum trades


def test_owner_windows_tile_to_the_instance_lifetime_cumulative():
    """A window opening before the first snapshot still yields the true delta.

    This is the failure CORR-110 describes: with a truncated history the first
    owner's window has no rows, so ``cum_at(since) == 0`` and the window that
    straddles the boundary is credited with everything the instance ever earned.
    """
    buckets = 3 * 24 * 12
    rows = [_row(b, controller=c) for b in range(buckets) for c in range(3)]
    hist = asyncio.run(fetch_instance_history(_client(_PagingHistory(rows)), "bot-x"))

    final_realized = 3 * float(buckets)
    # Three owners tiling [before first snapshot, after last).
    edges = [_epoch(0) - 3600, _epoch(300), _epoch(700), _epoch(buckets) + 3600]
    windows = [
        slice_history([hist], edges[i], edges[i + 1]) for i in range(len(edges) - 1)
    ]

    # The first owner really did hold the bot from bucket 0 to 300.
    assert windows[0][0] == pytest.approx(3 * 301.0)
    assert all(w[0] > 0 for w in windows)  # nobody is credited with zero
    assert sum(w[0] for w in windows) == pytest.approx(final_realized)
    assert sum(w[1] for w in windows) == pytest.approx(final_realized * 10)
    assert sum(w[3] for w in windows) == pytest.approx(final_realized / 10)


def test_row_cap_logs_a_warning_naming_the_instance(caplog):
    rows = [_row(b) for b in range(5 * HISTORY_PAGE_SIZE)]
    handler = _PagingHistory(rows)

    with caplog.at_level(logging.WARNING, logger="condor.fetchers.bot_performance"):
        hist = asyncio.run(
            fetch_instance_history(_client(handler), "bot-huge", max_rows=1000)
        )

    assert len(hist) == 1000  # stopped at the cap, not at the data
    assert len(handler.calls) == 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("bot-huge" in m and "1000-row cap" in m for m in warnings)


def test_no_warning_when_the_walk_completes(caplog):
    handler = _PagingHistory([_row(b) for b in range(10)])

    with caplog.at_level(logging.WARNING, logger="condor.fetchers.bot_performance"):
        hist = asyncio.run(fetch_instance_history(_client(handler), "bot-small"))

    assert len(hist) == 10
    assert len(handler.calls) == 1  # short page ends the walk
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_stops_when_the_api_echoes_the_cursor_back():
    """A non-advancing cursor must not re-append the same page into every bucket."""
    rows = [_row(b) for b in range(3 * HISTORY_PAGE_SIZE)]
    handler = _PagingHistory(rows, echo_cursor=True)

    hist = asyncio.run(fetch_instance_history(_client(handler), "bot-stall"))

    # Page 1 returns cursor=None (nothing was sent), page 2 echoes "500".
    assert len(handler.calls) == 2
    assert len(hist) == 2 * HISTORY_PAGE_SIZE
    # Summed per timestamp, so a duplicated page would double a bucket's PnL.
    assert hist[0][1] == 1.0


def test_a_failure_part_way_through_the_walk_returns_no_history():
    """Partial history is the same silent misattribution as truncated history."""

    class _FailsOnSecondPage:
        def __init__(self):
            self.calls = 0

        async def get_controller_performance_history(self, **kw):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("upstream blew up")
            page = [_row(b) for b in range(HISTORY_PAGE_SIZE)]
            return {"data": page, "next_cursor": "500"}

    handler = _FailsOnSecondPage()
    assert asyncio.run(fetch_instance_history(_client(handler), "bot-flaky")) == []
    assert handler.calls == 2


def test_empty_first_page_ends_the_walk():
    handler = _PagingHistory([])
    assert asyncio.run(fetch_instance_history(_client(handler), "bot-none")) == []
    assert len(handler.calls) == 1
