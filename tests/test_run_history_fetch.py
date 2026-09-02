"""Condor walks a finished run's history once, per controller, and keeps it.

The three things these pin are the three the design turns on:

**Per controller, not per bot.** Upstream's downsampler buckets by *time only*,
so a request spanning several controllers keeps one row per bucket and silently
drops the rest — measured on a real 12-controller fleet, ``5m`` returns 12 of 12
and ``1h`` returns 11 of 12, and coarser is worse. Binding ``controller_id``
leaves only that controller's rows in each bucket, so nothing is lost at any
interval. Every test here that counts requests is really testing that.

**Projection and thinning.** A raw row carries ``custom_info`` and a whole
``positions_summary``; a point is six floats. That is 4.2 MB of upstream against
~30 KB on disk, and it is what makes caching every finished run affordable.

**Eligibility.** A run is written only once it can no longer change. A run
served live must leave nothing behind, because an immutable entry written a
moment early is wrong for ever.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from condor.fetchers import run_history as rh
from condor.run_history_store import RunHistoryStore, reset_run_history_store

NOW = datetime.now(timezone.utc)


def iso(offset_hours: float) -> str:
    return (NOW + timedelta(hours=offset_hours)).isoformat()


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """A store of this test's own, and no walk shared with the previous test."""
    monkeypatch.setenv("CONDOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONDOR_RUN_HISTORY_RETENTION_DAYS", "0")
    reset_run_history_store()
    rh._inflight.clear()
    yield
    rh._inflight.clear()
    reset_run_history_store()


def row(controller_id, at_hours, *, realized=1.0, pair="BTC-BRL", positions=True):
    return {
        "timestamp": iso(at_hours),
        "bot_name": "gan",
        "controller_id": controller_id,
        "performance": {
            "realized_pnl_quote": realized,
            "unrealized_pnl_quote": 0.5,
            "global_pnl_quote": realized + 0.5,
            "volume_traded": 100.0 * realized,
            "global_pnl_pct": 0.01,
            "positions_summary": (
                [{"connector_name": "binance", "trading_pair": pair}]
                if positions
                else []
            ),
        },
        "custom_info": {"a lot of": "bulk nobody reads"},
    }


class FakeClient:
    """A backend that answers per controller, and records how it was asked."""

    def __init__(self, rows_by_controller, page_size=1000):
        self._rows = rows_by_controller
        self._page_size = page_size
        self.calls = []
        self.bot_orchestration = self

    async def get_controller_performance_history(
        self,
        *,
        bot_name,
        controller_id=None,
        interval="5m",
        start_time=None,
        end_time=None,
        limit=1000,
        cursor=None,
    ):
        self.calls.append(
            {
                "controller_id": controller_id,
                "interval": interval,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
            }
        )
        rows = (
            self._rows.get(controller_id, [])
            if controller_id
            else [r for rs in self._rows.values() for r in rs]
        )
        offset = int(cursor) if cursor else 0
        page = rows[offset : offset + min(limit, self._page_size)]
        nxt = offset + len(page)
        return {
            "data": page,
            "pagination": {"next_cursor": str(nxt) if nxt < len(rows) else None},
        }


async def _fetch(client, *, stopped=-10.0, controller_ids=("c1", "c2")):
    return await rh.fetch_run_history(
        client,
        "brigado",
        bot_name="gan",
        deployed_at=iso(-100),
        stopped_at=iso(stopped) if stopped is not None else None,
        controller_ids=controller_ids,
    )


TWO = {
    "c1": [row("c1", -100 + i) for i in range(20)],
    "c2": [row("c2", -100 + i, realized=2.0) for i in range(20)],
}


# ── The walk is per controller ──


def test_each_controller_is_asked_for_separately():
    """The whole reason the collapse cannot bite: with an id bound, each bucket
    holds only that controller's rows."""
    client = FakeClient(TWO)
    history = asyncio.run(_fetch(client))

    assert sorted(history.controllers) == ["c1", "c2"]
    assert sorted(c["controller_id"] for c in client.calls) == ["c1", "c2"]


def test_a_run_that_declared_nothing_is_asked_at_the_finest_interval():
    """No id to bind means the collapse is unavoidable, so it is not invited:
    the finest rung is the only one that keeps every controller."""
    client = FakeClient(TWO)
    history = asyncio.run(_fetch(client, controller_ids=()))

    assert sorted(history.controllers) == ["c1", "c2"]
    assert {c["interval"] for c in client.calls} == {"5m"}


def test_a_run_that_declared_nothing_records_the_interval_it_actually_asked_for():
    """``interval`` is provenance, so it must be what was walked, not what the
    ladder would have chosen. This 90h run picks ``15m`` for its span but asks
    at ``5m`` because it has no id to bind, and the entry is immutable: a lie
    written here is never corrected."""
    client = FakeClient(TWO)
    history = asyncio.run(_fetch(client, controller_ids=()))

    assert rh.pick_interval(90 * 3_600_000) == "15m"  # what the span alone says
    assert {c["interval"] for c in client.calls} == {"5m"}
    assert history.interval == "5m"

    entry = RunHistoryStore().list_entries()[0]
    assert entry.interval == "5m"


def test_a_declared_run_records_the_coarse_interval_it_was_walked_at():
    """The other side of the same rule: when the walk does go coarse, that is
    what is recorded — the fix must not simply hard-code the finest rung."""
    client = FakeClient(TWO)
    history = asyncio.run(
        rh.fetch_run_history(
            client,
            "brigado",
            bot_name="gan",
            deployed_at=iso(-24 * 60),
            stopped_at=iso(-1),
            controller_ids=["c1"],
        )
    )

    asked = {c["interval"] for c in client.calls}
    assert asked == {history.interval}
    assert history.interval != "5m"


def test_the_window_is_the_run_s_own_life_widened_by_a_bucket():
    """A run's first dump lands after its deploy row is written and its last
    after the stop; a window clipped to the run's own timestamps drops both."""
    client = FakeClient(TWO)
    asyncio.run(_fetch(client))

    call = client.calls[0]
    assert call["start_time"] < iso(-100)
    assert call["end_time"] > iso(-10)


def test_a_long_run_is_asked_at_a_coarser_interval():
    """Per controller, a coarse interval is safe — which is what lets a
    month-long run be one small request per controller rather than 8,640."""
    client = FakeClient(TWO)
    asyncio.run(
        rh.fetch_run_history(
            client,
            "brigado",
            bot_name="gan",
            deployed_at=iso(-24 * 60),
            stopped_at=iso(-1),
            controller_ids=["c1"],
        )
    )
    assert client.calls[0]["interval"] != "5m"


def test_every_page_is_walked():
    many = {"c1": [row("c1", -100 + i * 0.01) for i in range(2500)]}
    client = FakeClient(many, page_size=1000)
    history = asyncio.run(_fetch(client, controller_ids=("c1",)))

    assert len(client.calls) == 3
    assert history.points == rh.HISTORY_POINT_BUDGET  # thinned, exactly


# ── Projection ──


def test_a_point_is_six_floats_and_nothing_else():
    history = asyncio.run(_fetch(FakeClient(TWO), controller_ids=("c1",)))
    point = history.controllers["c1"][0]
    assert len(point) == 6
    assert all(isinstance(v, float) for v in point)


def test_points_come_back_in_time_order_however_upstream_ordered_them():
    """Upstream answers newest-first; a chart drawn in that order is a
    scribble."""
    reversed_rows = {"c1": list(reversed([row("c1", -100 + i) for i in range(20)]))}
    history = asyncio.run(_fetch(FakeClient(reversed_rows), controller_ids=("c1",)))
    times = [p[0] for p in history.controllers["c1"]]
    assert times == sorted(times)


def test_the_pair_is_taken_from_the_first_row_that_held_a_position():
    """A controller that stopped flat has no position in its *last* snapshot —
    which is exactly the row the latest-snapshot route reads — but almost always
    had one earlier."""
    rows = {
        "c1": [row("c1", -100, positions=False)] * 3
        + [row("c1", -50, pair="ETH-BRL")]
        + [row("c1", -10, positions=False)]
    }
    history = asyncio.run(_fetch(FakeClient(rows), controller_ids=("c1",)))
    assert history.identities["c1"]["trading_pair"] == "ETH-BRL"


def test_a_controller_with_no_rows_is_left_out_rather_than_drawn_flat():
    client = FakeClient({"c1": [row("c1", -100)]})
    history = asyncio.run(_fetch(client, controller_ids=("c1", "gone")))
    assert list(history.controllers) == ["c1"]


def test_a_run_with_no_rows_at_all_is_an_answer_not_an_error():
    with pytest.raises(rh.RunHistoryUnavailable) as excinfo:
        asyncio.run(_fetch(FakeClient({}), controller_ids=("c1",)))
    assert excinfo.value.missing is True


def test_a_backend_failure_is_not_reported_as_a_missing_run():
    class Broken(FakeClient):
        async def get_controller_performance_history(self, **kwargs):
            raise ConnectionError("boom")

    with pytest.raises(rh.RunHistoryUnavailable) as excinfo:
        asyncio.run(_fetch(Broken({}), controller_ids=("c1",)))
    assert excinfo.value.missing is False


# ── Thinning ──


def test_a_curve_is_thinned_to_the_budget_and_keeps_its_last_point():
    points = [[float(i) * 1000, float(i), 0.0, float(i), 0.0, 0.0] for i in range(5000)]
    out = rh.downsample(points, budget=100)
    assert len(out) <= 100
    assert out[-1] == points[-1]


def test_a_curve_inside_the_budget_is_left_alone():
    points = [[float(i), 0.0, 0.0, 0.0, 0.0, 0.0] for i in range(50)]
    assert rh.downsample(points, budget=100) is points


def test_thinning_keeps_the_last_value_of_a_bucket_not_the_first():
    """The series is cumulative, so the last reading is what the bucket ended
    at — which is what a step on the chart means."""
    points = [[0.0, 1, 0, 1, 0, 0], [1.0, 9, 0, 9, 0, 0], [1000.0, 5, 0, 5, 0, 0]]
    out = rh.downsample(points, budget=2)
    assert out[0][1] == 9


# ── Caching ──


def test_the_second_open_issues_no_upstream_request():
    client = FakeClient(TWO)
    first = asyncio.run(_fetch(client))
    calls_after_first = len(client.calls)

    second = asyncio.run(_fetch(client))
    assert len(client.calls) == calls_after_first
    assert second.cached is True
    assert first.controllers == second.controllers


def test_the_cache_survives_a_restart(tmp_path):
    client = FakeClient(TWO)
    asyncio.run(_fetch(client))
    calls = len(client.calls)

    reset_run_history_store()  # what a fresh process sees
    again = asyncio.run(_fetch(client))
    assert len(client.calls) == calls
    assert again.cached is True


def test_a_run_that_only_just_stopped_is_served_live_and_not_stored():
    """Its final dump may not have landed. An immutable entry written a moment
    early is wrong for ever."""
    client = FakeClient(TWO)
    asyncio.run(_fetch(client, stopped=-0.01))
    assert RunHistoryStore().list_entries() == []


def test_a_live_run_is_served_live_and_not_stored():
    client = FakeClient(TWO)
    asyncio.run(_fetch(client, stopped=None))
    assert RunHistoryStore().list_entries() == []


def test_concurrent_cold_readers_share_one_walk():
    client = FakeClient(TWO)

    async def race():
        return await asyncio.gather(_fetch(client), _fetch(client), _fetch(client))

    results = asyncio.run(race())
    # Two controllers, one page each — three readers must not make it six.
    assert len(client.calls) == 2
    assert all(r.controllers == results[0].controllers for r in results)


def test_what_is_stored_is_what_the_fold_needs():
    client = FakeClient(TWO)
    asyncio.run(_fetch(client))

    entries = RunHistoryStore().list_entries()
    assert len(entries) == 1
    assert entries[0].source == "snapshots"
    assert entries[0].controllers["c1"]["trading_pair"] == "BTC-BRL"
    assert entries[0].points > 0


# ── The interval ladder ──


def test_the_interval_ladder_only_ever_offers_values_upstream_accepts():
    """Upstream validates against exactly this set and answers 422 otherwise, so
    a value outside it turns a chart into an error rather than a coarser chart."""
    accepted = {"5m", "15m", "30m", "1h", "4h", "12h", "1d"}
    for hours in (0, 1, 24, 24 * 30, 24 * 365, 24 * 3650):
        assert rh.pick_interval(hours * 3_600_000) in accepted


def test_an_unknown_span_falls_back_to_the_finest_interval():
    assert rh.pick_interval(0) == "5m"
    assert rh.pick_interval(-1) == "5m"


# ── The archive fallback, for a run older than the snapshot table ──


class ArchivePerf:
    """What ``ArchivedBotPerformance`` gives us: one curve for the whole run."""

    def __init__(self, points, *, converted=True, pair="BTC-BRL"):
        self.cumulative_pnl = [
            type("P", (), {"timestamp": t, "pnl": v})() for t, v in points
        ]
        self.converted = converted
        self.primary_connector = "binance"
        self.primary_trading_pair = pair


def _with_archive(monkeypatch, perf):
    import condor.fetchers.archived_run as ar

    async def fake(client, name, db_path):
        return perf

    monkeypatch.setattr(ar, "fetch_archived_run", fake)


def test_a_run_the_snapshot_table_forgot_falls_back_to_its_archive(monkeypatch):
    _with_archive(
        monkeypatch, ArchivePerf([(1_700_000_000, 5.0), (1_700_000_600, 9.0)])
    )
    history = asyncio.run(
        rh.fetch_run_history(
            FakeClient({}),
            "brigado",
            bot_name="ancient",
            deployed_at=iso(-1000),
            stopped_at=iso(-900),
            controller_ids=["c1"],
            db_path="/archived/ancient.sqlite",
        )
    )
    assert history.source == "archive"
    assert list(history.controllers) == [rh.ARCHIVE_SERIES_ID]
    assert history.controllers[rh.ARCHIVE_SERIES_ID][-1][1] == 9.0


def test_the_archive_curve_is_not_filed_under_an_empty_controller_id(monkeypatch):
    """``controllerKey`` reads an empty id as "drop this", so an empty id would
    mean the series was silently never drawn."""
    _with_archive(monkeypatch, ArchivePerf([(1_700_000_000, 5.0)]))
    history = asyncio.run(
        rh.fetch_run_history(
            FakeClient({}),
            "brigado",
            bot_name="ancient",
            deployed_at=iso(-1000),
            stopped_at=iso(-900),
            controller_ids=["c1"],
            db_path="/a.sqlite",
        )
    )
    assert all(cid for cid in history.controllers)


def test_an_already_converted_archive_carries_no_pair_to_convert_through(monkeypatch):
    """It is already restated in USD. Handing back the pair converts it twice."""
    _with_archive(monkeypatch, ArchivePerf([(1_700_000_000, 5.0)], converted=True))
    history = asyncio.run(
        rh.fetch_run_history(
            FakeClient({}),
            "brigado",
            bot_name="a",
            deployed_at=iso(-1000),
            stopped_at=iso(-900),
            controller_ids=["c1"],
            db_path="/a.sqlite",
        )
    )
    assert history.identities[rh.ARCHIVE_SERIES_ID]["trading_pair"] == ""


def test_an_unconverted_archive_keeps_its_pair_rather_than_passing_brl_as_dollars(
    monkeypatch,
):
    _with_archive(
        monkeypatch,
        ArchivePerf([(1_700_000_000, 5.0)], converted=False, pair="BTC-BRL"),
    )
    history = asyncio.run(
        rh.fetch_run_history(
            FakeClient({}),
            "brigado",
            bot_name="b",
            deployed_at=iso(-1000),
            stopped_at=iso(-900),
            controller_ids=["c1"],
            db_path="/a.sqlite",
        )
    )
    assert history.identities[rh.ARCHIVE_SERIES_ID]["trading_pair"] == "BTC-BRL"


def test_the_archive_is_not_consulted_when_snapshots_answered(monkeypatch):
    """It is an unbounded trade walk. Paying for it when there is a real series
    would make every open of every run expensive."""
    called = []

    import condor.fetchers.archived_run as ar

    async def fake(client, name, db_path):
        called.append(db_path)
        raise AssertionError("must not be reached")

    monkeypatch.setattr(ar, "fetch_archived_run", fake)
    asyncio.run(
        rh.fetch_run_history(
            FakeClient(TWO),
            "brigado",
            bot_name="gan",
            deployed_at=iso(-100),
            stopped_at=iso(-10),
            controller_ids=["c1", "c2"],
            db_path="/a.sqlite",
        )
    )
    assert called == []


def test_a_run_with_neither_snapshots_nor_an_archive_says_so(monkeypatch):
    with pytest.raises(rh.RunHistoryUnavailable) as excinfo:
        asyncio.run(
            rh.fetch_run_history(
                FakeClient({}),
                "brigado",
                bot_name="gone",
                deployed_at=iso(-1000),
                stopped_at=iso(-900),
                controller_ids=["c1"],
                db_path=None,
            )
        )
    assert excinfo.value.missing is True


def test_an_archive_that_cannot_be_read_is_a_missing_run_not_a_crash(monkeypatch):
    import condor.fetchers.archived_run as ar

    async def fake(client, name, db_path):
        raise OSError("database is locked")

    monkeypatch.setattr(ar, "fetch_archived_run", fake)
    with pytest.raises(rh.RunHistoryUnavailable) as excinfo:
        asyncio.run(
            rh.fetch_run_history(
                FakeClient({}),
                "brigado",
                bot_name="x",
                deployed_at=iso(-1000),
                stopped_at=iso(-900),
                controller_ids=["c1"],
                db_path="/a.sqlite",
            )
        )
    assert excinfo.value.missing is True


def test_an_archive_derived_run_is_cached_as_an_archive(monkeypatch):
    _with_archive(monkeypatch, ArchivePerf([(1_700_000_000, 5.0)]))
    asyncio.run(
        rh.fetch_run_history(
            FakeClient({}),
            "brigado",
            bot_name="ancient",
            deployed_at=iso(-1000),
            stopped_at=iso(-900),
            controller_ids=["c1"],
            db_path="/a.sqlite",
        )
    )
    entries = RunHistoryStore().list_entries()
    assert [e.source for e in entries] == ["archive"]
