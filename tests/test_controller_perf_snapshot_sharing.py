"""PERF-579: every whole-server controller-performance caller shares one fetch.

``get_latest_controller_performance()`` is a whole-server call — the latest row
for every controller of every bot the API ever orchestrated — so its payload is
identical no matter who asks. Four call sites used to issue it independently:
the bots-page enrichment (every 30s poll, per open tab), the
``/controller-performance/latest`` route, the terminated-controllers route and
the 30s WS poller. This file pins that they now go through the one cached,
coalesced fetcher, and that the one caller whose request is *not* whole-server —
``?bot_name=X`` — still makes its own filtered round-trip.
"""

from __future__ import annotations

import asyncio

import pytest

import condor.web.routes.controller_performance as cp
from condor.fetchers.bot_performance import (
    clear_snapshot_cache,
    fetch_all_bot_performance,
    fetch_latest_snapshots,
)
from condor.fetchers.bots import _fetch_latest_perf
from condor.web.streams.hummingbot_ws import HummingbotStreamsMixin

SNAPSHOTS = [
    {
        "bot_name": "gan",
        "controller_id": "c1",
        "performance": {
            "realized_pnl_quote": 10.0,
            "unrealized_pnl_quote": 1.0,
            "global_pnl_quote": 11.0,
            "volume_traded": 500.0,
            "close_type_counts": {},
        },
    },
]

RUNS = [
    {
        "bot_name": "gan",
        "deployed_at": "2026-08-21T18:05:02+00:00",
        "stopped_at": "2026-08-25T05:46:25+00:00",
        "deployment_status": "ARCHIVED",
        "run_status": "STOPPED",
        "deployment_config": '{"controllers_config": ["c1"]}',
    },
]


class CountingClient:
    """A client that records every whole-server and every filtered request."""

    base_url = "http://perf-579"

    def __init__(self):
        self.whole_server_calls = 0
        self.filtered_calls = 0
        self.bot_orchestration = self

    async def get_latest_controller_performance(self, bot_name=None, **_kw):
        if bot_name is None:
            self.whole_server_calls += 1
        else:
            self.filtered_calls += 1
        return SNAPSHOTS

    async def get_bot_runs(self, **_kw):
        return RUNS


@pytest.fixture(autouse=True)
def _clean_caches():
    """Both the shared snapshot cache and the route's own 60s cache."""
    clear_snapshot_cache()
    cp._terminated_cache.clear()
    yield
    clear_snapshot_cache()
    cp._terminated_cache.clear()


def _with_fake_cm(monkeypatch, client):
    class FakeCM:
        async def get_client(self, _name):
            return client

    monkeypatch.setattr(cp, "get_config_manager", lambda: FakeCM())


class _Streams(HummingbotStreamsMixin):
    """The WS poller with its surroundings stubbed to one tick."""

    def __init__(self, client):
        self._client = client
        self._controller_perf_tasks = {}
        self.broadcast = None

    def _has_subscribers(self, _channel):
        return True

    async def _broadcast_update(self, _channel, data):
        self.broadcast = data
        # One tick is all this test needs; the stream's own handler exits on it.
        raise asyncio.CancelledError


async def _poll_once(client, monkeypatch):
    import config_manager

    class FakeCM:
        async def get_client(self, _name):
            return client

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: FakeCM())
    streams = _Streams(client)
    await streams._controller_perf_stream("controller_perf:srv")
    return streams.broadcast


def test_the_four_whole_server_callers_share_one_upstream_call(monkeypatch):
    """The observable claim: four callers within the TTL, one round-trip.

    Against the unfixed code this is 4 — the bots enrichment, both routes and
    the WS poller each issued their own byte-identical whole-server request.
    """
    client = CountingClient()
    _with_fake_cm(monkeypatch, client)

    async def _all_four():
        perf_map = await _fetch_latest_perf(client)
        latest = await cp.get_latest_controller_performance(
            name="srv", bot_name=None, user=object()
        )
        terminated = await cp.get_terminated_controllers(
            name="srv", limit=200, user=object()
        )
        broadcast = await _poll_once(client, monkeypatch)
        return perf_map, latest, terminated, broadcast

    perf_map, latest, terminated, broadcast = asyncio.run(_all_four())

    assert client.whole_server_calls == 1

    # …and every caller still got its own answer out of that one payload.
    assert perf_map["c1"]["bot_name"] == "gan"
    assert [s.controller_id for s in latest.snapshots] == ["c1"]
    assert terminated.server_online is True
    assert [c.controller_id for c in terminated.controllers] == ["c1"]
    assert [s["controller_id"] for s in broadcast["snapshots"]] == ["c1"]


def test_the_agents_rollup_shares_that_same_call(monkeypatch):
    """``fetch_all_bot_performance`` is the fifth caller of the same payload."""
    client = CountingClient()
    _with_fake_cm(monkeypatch, client)

    async def _both():
        agg = await fetch_all_bot_performance(client)
        await cp.get_latest_controller_performance(
            name="srv", bot_name=None, user=object()
        )
        return agg

    agg = asyncio.run(_both())

    assert client.whole_server_calls == 1
    assert agg["gan"]["global_pnl_quote"] == 11.0


def test_a_bot_filter_is_not_served_from_the_whole_server_cache(monkeypatch):
    """``?bot_name=X`` is a narrower request, so it keeps its own round-trip."""
    client = CountingClient()
    _with_fake_cm(monkeypatch, client)

    async def _warm_then_filter():
        await cp.get_latest_controller_performance(
            name="srv", bot_name=None, user=object()
        )
        return await cp.get_latest_controller_performance(
            name="srv", bot_name="gan", user=object()
        )

    out = asyncio.run(_warm_then_filter())

    assert client.whole_server_calls == 1
    assert client.filtered_calls == 1
    assert [s.controller_id for s in out.snapshots] == ["c1"]


def test_clear_snapshot_cache_empties_the_raw_layer_too(monkeypatch):
    """Both layers, or a cleared aggregate would be rebuilt from stale rows."""
    client = CountingClient()

    async def _twice_around_a_clear():
        await fetch_latest_snapshots(client)
        await fetch_all_bot_performance(client)
        clear_snapshot_cache()
        await fetch_latest_snapshots(client)
        await fetch_all_bot_performance(client)

    asyncio.run(_twice_around_a_clear())

    assert client.whole_server_calls == 2


def test_a_failed_fetch_is_never_cached(monkeypatch):
    """No previous value papers over a failure, and the next caller retries."""

    class Flaky(CountingClient):
        def __init__(self):
            super().__init__()
            self.fail = False

        async def get_latest_controller_performance(self, bot_name=None, **_kw):
            if self.fail:
                self.whole_server_calls += 1
                raise ConnectionError("boom")
            return await super().get_latest_controller_performance(bot_name, **_kw)

    client = Flaky()

    async def _fetch_fail_fetch():
        await fetch_latest_snapshots(client)
        client.fail = True
        clear_snapshot_cache()
        with pytest.raises(ConnectionError):
            await fetch_latest_snapshots(client)
        # The failure left nothing behind: the next call goes upstream again.
        client.fail = False
        return await fetch_latest_snapshots(client)

    got = asyncio.run(_fetch_fail_fetch())

    assert client.whole_server_calls == 3
    assert [s["controller_id"] for s in got] == ["c1"]


def test_an_unidentifiable_client_is_not_cached_at_all():
    """A client with no ``base_url`` cannot be told apart from any other."""

    class Anonymous(CountingClient):
        base_url = ""

    client = Anonymous()

    async def _twice():
        await fetch_latest_snapshots(client)
        await fetch_latest_snapshots(client)

    asyncio.run(_twice())

    assert client.whole_server_calls == 2
