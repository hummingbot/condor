"""The finished set reports its controllers, not only the executors left over.

``controller-performance-latest`` looks like a live-fleet route and is not: it
is the final snapshot of every controller of every bot the API has ever
orchestrated, and the rows outlive the bot. Reading it as live-only is why the
Terminated population had no controller to name and no history to draw
(FEAT-089).

These pin what the route must get right, all of which is a way to report a
number nobody earned:

* the pair, which is **not** at the top level of the payload but inside
  ``positions_summary`` — a leaf with an empty pair is folded as if its quote
  were dollars, which on a BRL fleet overstates every figure by the whole rate;
* the close types, which ``ControllerPerformanceSnapshot`` deliberately drops
  (PERF-261) and the scope header leads with;
* the live runs, which must appear in this listing not at all;
* a run with no snapshots at all, which must still be a row.
"""

import asyncio

import pytest

from condor.fetchers.run_history import declared_controllers, terminated_controllers
from condor.web.models import BotRunInfo
from condor.web.routes import controller_performance as cp


@pytest.fixture(autouse=True)
def _no_cache_bleed():
    cp.clear_terminated_cache()
    yield
    cp.clear_terminated_cache()


def run(
    bot_name, *, is_live=False, controller_ids=(), deployed="2026-08-21T18:05:02+00:00"
):
    return BotRunInfo(
        bot_name=bot_name,
        created_at=deployed,
        stopped_at=None if is_live else "2026-08-25T05:46:25+00:00",
        deployment_status="DEPLOYED" if is_live else "ARCHIVED",
        run_status="CREATED" if is_live else "STOPPED",
        is_live=is_live,
        controller_ids=list(controller_ids),
    )


def snap(bot_name, controller_id, **perf):
    """One row shaped the way the real payload is shaped."""
    body = {
        "realized_pnl_quote": 294.7,
        "unrealized_pnl_quote": 15.9,
        "global_pnl_quote": 310.6,
        "global_pnl_pct": 0.108,
        "volume_traded": 285975.8,
        "positions_summary": [
            {"connector_name": "binance", "trading_pair": "BTC-BRL", "amount": 0.00898}
        ],
        "close_type_counts": {
            "CloseType.TAKE_PROFIT": 1286,
            "CloseType.EARLY_STOP": 9108,
        },
    }
    body.update(perf)
    return {
        "timestamp": "2026-08-25T05:45:11.417445+00:00",
        "bot_name": bot_name,
        "controller_id": controller_id,
        # Hardcoded upstream, and false for a bot that stopped a week ago.
        "status": "running",
        "performance": body,
        "custom_info": {"reference_price": 417064.5},
    }


# ── The transform ──


def test_a_finished_controller_carries_the_pair_it_actually_traded():
    """The row has no top-level ``trading_pair``; it is one level down, per
    position. Folding an empty pair converts BRL as if it were dollars."""
    got, _ = terminated_controllers([snap("gan", "c1")], [run("gan")])
    assert [c.trading_pair for c in got] == ["BTC-BRL"]
    assert [c.connector for c in got] == ["binance"]


def test_a_controller_that_stopped_flat_reports_no_pair_rather_than_a_guess():
    got, _ = terminated_controllers(
        [snap("gan", "c1", positions_summary=[])], [run("gan")]
    )
    assert got[0].trading_pair == ""


def test_the_close_types_survive_the_mapping():
    """``ControllerPerformanceSnapshot`` drops these on purpose (PERF-261), so
    the terminated route must not go through it."""
    got, _ = terminated_controllers([snap("gan", "c1")], [run("gan")])
    assert got[0].close_type_counts["CloseType.TAKE_PROFIT"] == 1286


def test_a_finished_controller_is_stopped_whatever_the_row_claims():
    got, _ = terminated_controllers([snap("gan", "c1")], [run("gan")])
    assert got[0].status == "stopped"


def test_a_live_run_contributes_nothing_to_the_finished_set():
    """It is already reported by the Running population; listed in both, any
    fold that spans them counts it twice."""
    got, seen = terminated_controllers(
        [snap("live", "c1"), snap("gan", "c1")],
        [run("live", is_live=True), run("gan")],
    )
    assert [c.bot_name for c in got] == ["gan"]
    assert seen == 1


def test_a_snapshot_whose_bot_has_no_run_is_dropped():
    """Without the run there is no deploy time, no stop time and no archive —
    the node would be a name with nothing behind it."""
    got, seen = terminated_controllers([snap("orphan", "c1")], [])
    assert got == []
    assert seen == 0


def test_each_controller_learns_its_run_s_deploy_time():
    got, _ = terminated_controllers([snap("gan", "c1")], [run("gan")])
    assert got[0].deployed_at == "2026-08-21T18:05:02+00:00"


def test_runs_seen_counts_runs_not_controllers():
    got, seen = terminated_controllers(
        [snap("gan", "c1"), snap("gan", "c2"), snap("gan", "c3")], [run("gan")]
    )
    assert len(got) == 3
    assert seen == 1


# ── A run older than the snapshot table ──


def test_a_run_with_no_snapshots_still_has_the_shape_its_deployment_declared():
    got = declared_controllers(run("old", controller_ids=["a", "b"]))
    assert [c.controller_id for c in got] == ["a", "b"]
    assert all(c.global_pnl_quote == 0 for c in got)
    assert all(c.status == "stopped" for c in got)


# ── The route ──


class FakeClient:
    def __init__(self, latest, runs):
        self._latest = latest
        self._runs = runs
        self.latest_calls = 0
        self.bot_orchestration = self

    async def get_latest_controller_performance(self, **kwargs):
        self.latest_calls += 1
        return self._latest

    async def get_bot_runs(self, **kwargs):
        return self._runs


def _route(client, monkeypatch, name="srv"):
    class FakeCM:
        async def get_client(self, _name):
            return client

    monkeypatch.setattr(cp, "get_config_manager", lambda: FakeCM())
    return asyncio.run(cp.get_terminated_controllers(name=name, user=object()))


RAW_RUNS = [
    {
        "bot_name": "gan",
        "deployed_at": "2026-08-21T18:05:02+00:00",
        "stopped_at": "2026-08-25T05:46:25+00:00",
        "deployment_status": "ARCHIVED",
        "run_status": "STOPPED",
        "deployment_config": '{"controllers_config": ["c1", "c2"]}',
    },
    {
        "bot_name": "live",
        "deployed_at": "2026-08-31T21:55:25+00:00",
        "stopped_at": None,
        "deployment_status": "DEPLOYED",
        "run_status": "CREATED",
        "deployment_config": '{"controllers_config": ["c1"]}',
    },
    {
        # Older than the snapshot table: declared controllers, no rows.
        "bot_name": "ancient",
        "deployed_at": "2026-03-11T10:00:00+00:00",
        "stopped_at": "2026-03-12T10:00:00+00:00",
        "deployment_status": "ARCHIVED",
        "run_status": "STOPPED",
        "deployment_config": '{"controllers_config": ["old_1"]}',
    },
]


def test_the_route_reports_finished_controllers_and_never_live_ones(monkeypatch):
    client = FakeClient(
        [snap("gan", "c1"), snap("gan", "c2"), snap("live", "c1")], RAW_RUNS
    )
    out = _route(client, monkeypatch)

    assert {c.bot_name for c in out.controllers} == {"gan", "ancient"}
    assert out.runs_seen == 2


def test_the_route_fills_in_a_run_the_snapshot_table_forgot(monkeypatch):
    client = FakeClient([snap("gan", "c1")], RAW_RUNS)
    out = _route(client, monkeypatch)

    ancient = [c for c in out.controllers if c.bot_name == "ancient"]
    assert [c.controller_id for c in ancient] == ["old_1"]
    assert ancient[0].global_pnl_quote == 0.0


def test_a_covered_run_is_not_topped_up_with_zero_controllers(monkeypatch):
    """``gan`` declared c1 and c2 but only reported c1. Adding c2 at zero would
    count in the scope's leaf count and drag its win rate down over trading
    there is no record of."""
    client = FakeClient([snap("gan", "c1")], RAW_RUNS)
    out = _route(client, monkeypatch)

    assert [c.controller_id for c in out.controllers if c.bot_name == "gan"] == ["c1"]


def test_the_listing_is_served_warm_rather_than_refetched(monkeypatch):
    client = FakeClient([snap("gan", "c1")], RAW_RUNS)
    _route(client, monkeypatch)
    _route(client, monkeypatch)
    assert client.latest_calls == 1


def test_two_servers_do_not_share_one_warm_listing(monkeypatch):
    client = FakeClient([snap("gan", "c1")], RAW_RUNS)
    _route(client, monkeypatch, name="a")
    _route(client, monkeypatch, name="b")
    assert client.latest_calls == 2


def test_an_unreachable_server_is_said_to_be_offline_rather_than_empty(monkeypatch):
    class Broken(FakeClient):
        async def get_latest_controller_performance(self, **kwargs):
            raise ConnectionError("boom")

    out = _route(Broken([], RAW_RUNS), monkeypatch)
    assert out.server_online is False
    assert "boom" in (out.error_hint or "")
    assert out.controllers == []


# ── Filling in a pair the latest snapshot could not name ──


def test_a_pairless_controller_takes_the_pair_its_cached_history_knows(
    tmp_path, monkeypatch
):
    """A controller that stopped flat reports no pair, and a leaf with no pair is
    folded as though its quote were dollars — which on a BRL fleet overstates it
    by the whole rate. The run's own history walked forward to a row that did
    hold a position, and that answer is already on disk."""
    from condor.fetchers.run_history import fill_pairs_from_cache
    from condor.run_history_store import (
        RunHistoryEntry,
        get_run_history_store,
        reset_run_history_store,
        run_key,
    )

    monkeypatch.setenv("CONDOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONDOR_RUN_HISTORY_RETENTION_DAYS", "0")
    reset_run_history_store()
    try:
        get_run_history_store().put(
            run_key("srv", "gan", "2026-08-21T18:05:02+00:00"),
            RunHistoryEntry(
                server="srv",
                bot_name="gan",
                deployed_at="2026-08-21T18:05:02+00:00",
                stopped_at="2026-08-25T05:46:25+00:00",
                controllers={"c1": {"connector": "binance", "trading_pair": "BTC-BRL"}},
            ),
            {"c1": [[1.0, 0, 0, 0, 0, 0]]},
        )

        got, _ = terminated_controllers(
            [snap("gan", "c1", positions_summary=[])], [run("gan")]
        )
        assert got[0].trading_pair == ""

        assert fill_pairs_from_cache(got, [run("gan")], "srv") == 1
        assert got[0].trading_pair == "BTC-BRL"
        assert got[0].connector == "binance"
    finally:
        reset_run_history_store()


def test_a_pair_that_is_not_cached_stays_empty_rather_than_guessed(
    tmp_path, monkeypatch
):
    """Never inferred from a bot's name. Empty is what the browser labels; a
    guess is what it would report as fact."""
    from condor.fetchers.run_history import fill_pairs_from_cache
    from condor.run_history_store import reset_run_history_store

    monkeypatch.setenv("CONDOR_DATA_DIR", str(tmp_path))
    reset_run_history_store()
    try:
        got, _ = terminated_controllers(
            [snap("chessboard-btc-brl-1", "c1", positions_summary=[])],
            [run("chessboard-btc-brl-1")],
        )
        assert fill_pairs_from_cache(got, [run("chessboard-btc-brl-1")], "srv") == 0
        assert got[0].trading_pair == ""
    finally:
        reset_run_history_store()


def test_a_controller_that_named_its_own_pair_is_left_alone(tmp_path, monkeypatch):
    from condor.fetchers.run_history import fill_pairs_from_cache
    from condor.run_history_store import reset_run_history_store

    monkeypatch.setenv("CONDOR_DATA_DIR", str(tmp_path))
    reset_run_history_store()
    try:
        got, _ = terminated_controllers([snap("gan", "c1")], [run("gan")])
        assert fill_pairs_from_cache(got, [run("gan")], "srv") == 0
        assert got[0].trading_pair == "BTC-BRL"
    finally:
        reset_run_history_store()
