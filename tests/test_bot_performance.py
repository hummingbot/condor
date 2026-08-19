"""Tests for FEAT-005 controller mode: bot-by-name perf aggregation + merge.

Covers the reusable bot-performance fetcher, the disjoint merge into
``AgentPerformance``, the wiring through the provider, and the prompt's
controller-mode block.
"""

import asyncio
from types import SimpleNamespace

import pytest

from condor.agents.config import AgentConfig, load_full_config
from condor.agents.performance import (
    AgentPerformance,
    fetch_agent_performance,
    fetch_agent_performance_batch,
)
from condor.fetchers.bot_performance import (
    _aggregate_by_bot,
    bot_executor_rows,
    clear_snapshot_cache,
    extract_snapshots,
    fetch_all_bot_performance,
)


@pytest.fixture(autouse=True)
def _no_snapshot_cache_bleed():
    """Isolate the per-server snapshot cache between tests."""
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


# ── Sample payloads ──


def _snapshot(bot_name, controller_id, realized, unrealized, volume):
    return {
        "bot_name": bot_name,
        "controller_id": controller_id,
        "trading_pair": "BTC-USDT",
        "performance": {
            "realized_pnl_quote": realized,
            "unrealized_pnl_quote": unrealized,
            "volume_traded": volume,
        },
    }


SNAPSHOTS = [
    _snapshot("river", "grid_strike_1", 10.0, 5.0, 1000.0),
    _snapshot("river", "grid_strike_2", -3.0, 2.0, 500.0),
    _snapshot("otherbot", "dca_1", 100.0, 0.0, 9999.0),
    _snapshot("", "orphan", 7.0, 7.0, 7.0),  # no bot_name → dropped
]


class _FakeBotOrchestration:
    def __init__(self, snapshots):
        self._snapshots = snapshots

    async def get_latest_controller_performance(self, bot_name=None):
        return {"data": self._snapshots}


class _FakeExecutors:
    def __init__(self, rows_by_id):
        self._rows_by_id = rows_by_id

    async def search_executors(self, controller_ids, limit, cursor=None):
        aid = controller_ids[0]
        return {"executors": self._rows_by_id.get(aid, [])}


class _FakeClient:
    def __init__(self, rows_by_id=None, snapshots=None):
        self.executors = _FakeExecutors(rows_by_id or {})
        self.bot_orchestration = _FakeBotOrchestration(
            SNAPSHOTS if snapshots is None else snapshots
        )


# ── Aggregation ──


def test_aggregate_by_bot_groups_and_drops_empty():
    agg = _aggregate_by_bot(SNAPSHOTS)
    assert set(agg) == {"river", "otherbot"}  # empty bot_name dropped
    river = agg["river"]
    assert river["realized_pnl_quote"] == 7.0  # 10 + (-3)
    assert river["unrealized_pnl_quote"] == 7.0  # 5 + 2
    assert river["global_pnl_quote"] == 14.0
    assert river["volume_traded"] == 1500.0
    assert river["num_controllers"] == 2
    assert len(river["controllers"]) == 2


def test_extract_snapshots_shapes():
    assert extract_snapshots(SNAPSHOTS) == SNAPSHOTS
    assert extract_snapshots({"data": SNAPSHOTS}) == SNAPSHOTS
    assert extract_snapshots(None) == []


def test_fetch_all_bot_performance_keys_by_bot():
    client = _FakeClient()
    allp = asyncio.run(fetch_all_bot_performance(client))
    assert set(allp) == {"river", "otherbot"}
    assert allp["river"]["realized_pnl_quote"] == 7.0


# ── Whole-server snapshot coalescing ──
#
# get_latest_controller_performance() returns every bot on the server, so the
# agents rollup — which fans one call per strategy into a single gather — used to
# fire N byte-identical whole-server requests at once.


class _CountingOrchestration:
    def __init__(self, snapshots, delay=0.0):
        self._snapshots = snapshots
        self._delay = delay
        self.calls = 0

    async def get_latest_controller_performance(self, bot_name=None):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return {"data": self._snapshots}


class _ServerClient:
    """Fake client that identifies its server via ``base_url``, like the real one."""

    def __init__(self, base_url, snapshots=None, delay=0.0):
        self.base_url = base_url
        self.bot_orchestration = _CountingOrchestration(
            SNAPSHOTS if snapshots is None else snapshots, delay
        )

    @property
    def calls(self):
        return self.bot_orchestration.calls


def test_concurrent_callers_share_one_round_trip():
    # In-flight coalescing, not just TTL: the fetch is still running when the
    # other callers arrive, so they must join it rather than start their own.
    client = _ServerClient("http://server-a:8000", delay=0.02)

    async def _go():
        return await asyncio.gather(
            *[fetch_all_bot_performance(client) for _ in range(5)]
        )

    results = asyncio.run(_go())
    assert client.calls == 1
    assert all(set(r) == {"river", "otherbot"} for r in results)


def test_staggered_callers_within_ttl_reuse_the_snapshot():
    # The rollup's per-strategy calls do not always overlap exactly; the TTL keeps
    # a staggered fan-out at one round-trip too.
    client = _ServerClient("http://server-a:8000")

    async def _go():
        for _ in range(4):
            await fetch_all_bot_performance(client)

    asyncio.run(_go())
    assert client.calls == 1


def test_two_servers_never_share_a_snapshot():
    a = _ServerClient(
        "http://a:8000", snapshots=[_snapshot("river", "c1", 1.0, 0.0, 10.0)]
    )
    b = _ServerClient(
        "http://b:8000", snapshots=[_snapshot("otherbot", "c2", 2.0, 0.0, 20.0)]
    )

    async def _go():
        return await asyncio.gather(
            fetch_all_bot_performance(a), fetch_all_bot_performance(b)
        )

    ra, rb = asyncio.run(_go())
    assert set(ra) == {"river"}
    assert set(rb) == {"otherbot"}
    assert (a.calls, b.calls) == (1, 1)


def test_failed_fetch_is_not_cached_and_still_propagates():
    class _Flaky:
        def __init__(self):
            self.calls = 0

        async def get_latest_controller_performance(self, bot_name=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("api down")
            return {"data": SNAPSHOTS}

    client = SimpleNamespace(base_url="http://flaky:8000", bot_orchestration=_Flaky())

    async def _go():
        with pytest.raises(RuntimeError):
            await fetch_all_bot_performance(client)
        # The failure left nothing cached, so the next call really re-fetches.
        return await fetch_all_bot_performance(client)

    out = asyncio.run(_go())
    assert client.bot_orchestration.calls == 2
    assert set(out) == {"river", "otherbot"}


def test_concurrent_waiters_all_see_the_failure():
    class _Boom:
        def __init__(self):
            self.calls = 0

        async def get_latest_controller_performance(self, bot_name=None):
            self.calls += 1
            await asyncio.sleep(0.02)
            raise RuntimeError("api down")

    client = SimpleNamespace(base_url="http://boom:8000", bot_orchestration=_Boom())

    async def _go():
        return await asyncio.gather(
            *[fetch_all_bot_performance(client) for _ in range(3)],
            return_exceptions=True,
        )

    out = asyncio.run(_go())
    assert client.bot_orchestration.calls == 1
    assert all(isinstance(r, RuntimeError) for r in out)


def test_client_without_base_url_is_never_cached():
    # An unidentifiable client (test doubles) must not share a blank key with
    # unrelated servers — it bypasses the cache entirely.
    a = _ServerClient("", snapshots=[_snapshot("river", "c1", 1.0, 0.0, 10.0)])
    b = _ServerClient("", snapshots=[_snapshot("otherbot", "c2", 2.0, 0.0, 20.0)])
    assert set(asyncio.run(fetch_all_bot_performance(a))) == {"river"}
    assert set(asyncio.run(fetch_all_bot_performance(b))) == {"otherbot"}
    assert set(asyncio.run(fetch_all_bot_performance(a))) == {"river"}
    assert a.calls == 2


# ── Suffix-tolerant resolution ──


def _snap_with_positions(
    bot_name, controller_id, timestamp, positions, status="running", closes=None
):
    return {
        "bot_name": bot_name,
        "controller_id": controller_id,
        "status": status,
        "timestamp": timestamp,
        "performance": {
            "realized_pnl_quote": 1.0,
            "unrealized_pnl_quote": 2.0,
            "volume_traded": 100.0,
            "positions_summary": positions,
            "close_type_counts": closes or {},
        },
    }


def test_resolve_bots_exact_and_suffix():
    from condor.fetchers.bot_performance import resolve_bots

    agg = _aggregate_by_bot(
        [
            _snap_with_positions(
                "dn-mm-20260101-000000", "c", "2026-01-01T00:00:00+00:00", []
            ),
            _snap_with_positions(
                "dn-mm-20260724-182221", "c", "2026-07-24T18:22:21+00:00", []
            ),
            _snap_with_positions(
                "dn-mmx-20260724-000000", "c", "2026-07-24T00:00:00+00:00", []
            ),
            _snap_with_positions("other", "c", "2026-07-24T00:00:00+00:00", []),
        ]
    )
    # exact match wins outright
    assert resolve_bots(agg, ["other"])["other"]["bot_name"] == "other"
    # base name resolves to the freshest timestamped deploy
    assert resolve_bots(agg, ["dn-mm"])["dn-mm"]["bot_name"] == "dn-mm-20260724-182221"
    # the hyphen boundary keeps a sibling base (dn-mmx) from matching dn-mm
    assert resolve_bots(agg, ["dn-mm"])["dn-mm"]["bot_name"] != "dn-mmx-20260724-000000"
    # never deployed → absent from the result; empty inputs → nothing to resolve
    assert resolve_bots(agg, ["ghost"]) == {}
    assert resolve_bots(agg, [""]) == {}
    assert resolve_bots({}, ["dn-mm"]) == {}


# ── Executor rows from positions_summary ──


def test_bot_executor_rows_from_positions():
    pos = {
        "connector_name": "hyperliquid",
        "trading_pair": "XYZ:CL-USD",
        "volume_traded_quote": 250.0,
        "side": "TradeType.SELL",
        "amount": 2.0,
        "breakeven_price": 60.0,
        "unrealized_pnl_quote": -1.5,
        "realized_pnl_quote": 0.4,
        "cum_fees_quote": 0.3,
    }
    agg = _aggregate_by_bot(
        [_snap_with_positions("bot", "dn_CL_mm", "2026-07-24T18:00:00+00:00", [pos])]
    )
    rows = bot_executor_rows(agg["bot"])
    assert len(rows) == 1
    r = rows[0]
    assert r["controller_id"] == "dn_CL_mm"
    assert r["pair"] == "XYZ:CL-USD"
    assert r["side"] == "SELL"  # TradeType. prefix stripped
    assert r["status"] == "RUNNING"
    assert r["pnl"] == -1.5  # unrealized mark
    assert r["volume"] == 250.0
    assert r["fees"] == 0.3
    assert r["entry_price"] == 60.0
    assert r["amount"] == 120.0  # abs(2.0) * 60.0 quote notional
    assert r["timestamp"] > 0  # ISO parsed to epoch


def test_flat_controller_yields_no_rows_but_counts_pnl():
    agg = _aggregate_by_bot(
        [_snap_with_positions("bot", "c", "2026-07-24T18:00:00+00:00", [])]
    )
    assert bot_executor_rows(agg["bot"]) == []
    # realized/unrealized still aggregate even with no open positions
    assert agg["bot"]["realized_pnl_quote"] == 1.0
    assert agg["bot"]["unrealized_pnl_quote"] == 2.0


# ── Merge into AgentPerformance ──


def test_merge_is_disjoint_addition():
    # Agent has its own executors tagged with agent_id (NOT the bot's controllers).
    agent_id = "river.scalp_1"
    rows_by_id = {
        agent_id: [
            {
                "id": "e1",
                "status": "CLOSED",
                "net_pnl_quote": 4.0,
                "filled_amount_quote": 200.0,
                "config": {"controller_id": agent_id},
            },
        ]
    }
    client = _FakeClient(rows_by_id=rows_by_id)

    # Without bot_name: executor-only behavior.
    base = asyncio.run(fetch_agent_performance(client, agent_id))
    assert base.realized_pnl == 4.0
    assert base.bot_name == ""

    # With bot_name "river": adds river's 7/7/1500 on top, no double count.
    merged = asyncio.run(fetch_agent_performance(client, agent_id, bot_names=["river"]))
    assert merged.bot_name == "river"
    assert merged.realized_pnl == 4.0 + 7.0
    assert merged.unrealized_pnl == 7.0
    assert merged.total_pnl == merged.realized_pnl + merged.unrealized_pnl
    assert len(merged.controllers) == 2
    # The bot's controller executors never appear in the executor list.
    assert all(r["id"] == "e1" for r in merged.executors)


def test_merge_appends_bot_positions_as_rows():
    # Bot-mode agent: its own agent_id table is empty; the bot holds one open
    # position that must surface as an executor row with open_count bumped.
    agent_id = "dn.sess_1"
    pos = {
        "connector_name": "hyperliquid",
        "trading_pair": "XYZ:CL-USD",
        "volume_traded_quote": 250.0,
        "side": "TradeType.BUY",
        "amount": 1.0,
        "breakeven_price": 60.0,
        "unrealized_pnl_quote": -0.5,
        "realized_pnl_quote": 0.4,
        "cum_fees_quote": 0.3,
    }
    snaps = [
        _snap_with_positions(
            "dn-mm-20260724-1", "dn_CL_mm", "2026-07-24T18:00:00+00:00", [pos]
        ),
    ]
    client = _FakeClient(rows_by_id={}, snapshots=snaps)
    # base name resolves suffix-tolerantly to the deployed instance
    merged = asyncio.run(fetch_agent_performance(client, agent_id, bot_names=["dn-mm"]))
    # bot_name reflects the resolved deployed instance, not just the base
    assert merged.bot_name == "dn-mm-20260724-1"
    assert len(merged.executors) == 1
    assert merged.executors[0]["pair"] == "XYZ:CL-USD"
    assert merged.open_count == 1
    assert merged.fees == 0.3
    assert merged.unrealized_pnl == 2.0  # controller-level aggregate


def test_aggregate_counts_only_round_trip_closes():
    """EARLY_STOP is pmm re-quoting churn, not a trade — per controller and per bot."""
    agg = _aggregate_by_bot(
        [
            _snap_with_positions(
                "bot",
                "c1",
                "2026-07-24T18:00:00+00:00",
                [],
                closes={"CloseType.TAKE_PROFIT": 40, "CloseType.EARLY_STOP": 900},
            ),
            _snap_with_positions(
                "bot",
                "c2",
                "2026-07-24T18:00:00+00:00",
                [],
                closes={"CloseType.STOP_LOSS": 10, "CloseType.POSITION_HOLD": 3},
            ),
        ]
    )
    assert agg["bot"]["closed_trades"] == 50
    assert [c["closed_trades"] for c in agg["bot"]["controllers"]] == [40, 10]


def test_bot_mode_trades_are_closes_not_open_positions():
    """A bot with 50 real closes and 2 open positions reports 50 trades, not 2.

    ``positions_summary`` only ever describes the currently-open book, so counting
    its rows made a session that had closed hundreds of round trips report as many
    trades as it happened to hold positions — while closed_count stayed 0.
    """
    agent_id = "dn.sess_1"
    positions = [
        {
            "connector_name": "hyperliquid",
            "trading_pair": pair,
            "volume_traded_quote": 250.0,
            "side": "TradeType.BUY",
            "amount": 1.0,
            "breakeven_price": 60.0,
            "unrealized_pnl_quote": -0.5,
            "cum_fees_quote": 0.3,
        }
        for pair in ("BTC-USD", "ETH-USD")
    ]
    snaps = [
        _snap_with_positions(
            "dn-mm-20260724-1",
            "dn_CL_mm",
            "2026-07-24T18:00:00+00:00",
            positions,
            closes={
                "CloseType.TAKE_PROFIT": 40,
                "CloseType.STOP_LOSS": 10,
                "CloseType.EARLY_STOP": 900,
            },
        )
    ]
    client = _FakeClient(rows_by_id={}, snapshots=snaps)
    perf = asyncio.run(fetch_agent_performance(client, agent_id, bot_names=["dn-mm"]))

    assert perf.trade_count == 50
    assert perf.closed_count == 50
    assert perf.open_count == 2  # the live book, unchanged
    # The snapshot says how many positions closed, not how they ended: unknown,
    # which is not the same as a 0% win rate.
    assert perf.win_rate is None


def test_no_snapshot_leaves_executor_totals_unchanged():
    agent_id = "river.scalp_1"
    rows_by_id = {
        agent_id: [
            {
                "id": "e1",
                "status": "RUNNING",
                "net_pnl_quote": 2.0,
                "config": {"controller_id": agent_id},
            },
        ]
    }
    client = _FakeClient(rows_by_id=rows_by_id)
    no_bot = asyncio.run(fetch_agent_performance(client, agent_id))
    # bot_name set but no matching snapshot → totals identical to executor-only.
    ghost = asyncio.run(fetch_agent_performance(client, agent_id, bot_names=["ghost"]))
    assert ghost.bot_name == "ghost"
    assert ghost.unrealized_pnl == no_bot.unrealized_pnl == 2.0
    assert ghost.total_pnl == no_bot.total_pnl
    assert ghost.controllers == []


def test_batch_merges_only_named_agents():
    a1, a2 = "river.scalp_1", "plain.scalp_1"
    client = _FakeClient(rows_by_id={})
    out = asyncio.run(fetch_agent_performance_batch(client, [a1, a2], {a1: ["river"]}))
    assert out[a1].bot_name == "river"
    assert out[a1].realized_pnl == 7.0
    assert out[a2].bot_name == ""  # not named → untouched, executor-only
    assert out[a2].realized_pnl == 0.0


# ── Config field ──


def test_bot_name_config_field_defaults_empty():
    assert AgentConfig().bot_name == ""
    assert AgentConfig(bot_name="river").bot_name == "river"


def test_bot_name_round_trips_through_full_config(tmp_path):
    cfg = load_full_config(tmp_path, {"bot_name": "river", "frequency_sec": 30})
    assert cfg["bot_name"] == "river"
    # absent default → empty string from AgentConfig defaults
    cfg2 = load_full_config(tmp_path, {})
    assert cfg2["bot_name"] == ""


# ── Per-session history slicing ──


def _instances_agg(*names_ts):
    return _aggregate_by_bot(
        [
            {
                "bot_name": name,
                "controller_id": "c",
                "timestamp": ts,
                "performance": {},
            }
            for name, ts in names_ts
        ]
    )


def test_partition_assigns_a_tagged_instance_to_its_own_base():
    from condor.fetchers.bot_performance import partition_instances

    agg = _instances_agg(
        ("brigado-ema_trend-20260731-100000", "2026-07-31T10:00:00"),
        ("brigado-ema_trend-btc-20260731-101500", "2026-07-31T10:15:00"),
    )
    bases = ["brigado-ema_trend", "brigado-ema_trend-btc"]

    # Prefix-matching one base at a time, the parent would swallow its tagged
    # sibling; deciding over all owned bases at once, each instance lands on one.
    assert partition_instances(agg, bases) == {
        "brigado-ema_trend": ["brigado-ema_trend-20260731-100000"],
        "brigado-ema_trend-btc": ["brigado-ema_trend-btc-20260731-101500"],
    }


def test_partition_keeps_unowned_instances_out_and_orders_oldest_first():
    from condor.fetchers.bot_performance import partition_instances

    agg = _instances_agg(
        ("dn-mm-20260724-182221", "2026-07-24T18:22:21"),
        ("dn-mm-20260101-000000", "2026-01-01T00:00:00"),
        ("someone-else", "2026-07-24T00:00:00"),
    )
    assert partition_instances(agg, ["dn-mm", "ghost"]) == {
        "dn-mm": ["dn-mm-20260101-000000", "dn-mm-20260724-182221"],
        "ghost": [],  # owned but never deployed
    }
    assert partition_instances(agg, []) == {}


def test_resolve_bots_never_hands_the_same_aggregate_to_two_bases():
    from condor.fetchers.bot_performance import resolve_bots

    agg = _instances_agg(
        ("ns-bot-20260731-100000", "2026-07-31T10:00:00"),
        ("ns-bot-btc-20260731-101500", "2026-07-31T10:15:00"),
    )
    # Freshest-prefix-match alone would give BOTH bases the …-btc instance;
    # partition-aware resolution gives each base its own live instance.
    live = resolve_bots(agg, ["ns-bot", "ns-bot-btc"])
    assert live["ns-bot"]["bot_name"] == "ns-bot-20260731-100000"
    assert live["ns-bot-btc"]["bot_name"] == "ns-bot-btc-20260731-101500"


def test_resolve_bots_prefers_an_exact_deploy_over_a_newer_suffixed_one():
    from condor.fetchers.bot_performance import resolve_bots

    agg = _instances_agg(
        ("dn-mm", "2026-01-01T00:00:00"),
        ("dn-mm-20260724-182221", "2026-07-24T18:22:21"),
    )
    # Exact beats freshest: a bot deployed under the bare base wins.
    assert resolve_bots(agg, ["dn-mm"])["dn-mm"]["bot_name"] == "dn-mm"


def test_slice_history_tiles_exactly():
    from condor.fetchers.bot_performance import slice_history

    # one instance, cumulative realized/volume/trades/fees at t=10,20,30
    hist = [
        (10.0, 1.0, 100.0, 1.0, 0.1),
        (20.0, 3.0, 300.0, 4.0, 0.3),
        (30.0, 5.0, 500.0, 9.0, 0.5),
    ]
    # window fully before → 0; fully after start baseline → final delta
    assert slice_history([hist], 0, 5) == (0.0, 0.0, 0.0, 0.0)
    # [0,20] captures rows at 10 and 20 → cum_at(20)-cum_at(0)=3
    assert slice_history([hist], 0, 20) == (3.0, 300.0, 4.0, 0.3)
    # [20,40] → cum_at(40)=final(5) - cum_at(20)=3 → 2
    assert slice_history([hist], 20, 40) == (2.0, 200.0, 5.0, pytest.approx(0.2))
    # tiling: [0,20)+[20,40) sums to the instance's full cumulative
    a = slice_history([hist], 0, 20)
    b = slice_history([hist], 20, 40)
    assert (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3]) == (
        5.0,
        500.0,
        9.0,
        pytest.approx(0.5),
    )


def test_slice_history_sums_multiple_instances():
    from condor.fetchers.bot_performance import slice_history

    h1 = [(10.0, 2.0, 20.0, 1.0, 0.2)]  # instance 1: +2 at t=10
    h2 = [(15.0, 3.0, 30.0, 2.0, 0.3)]  # instance 2: +3 at t=15
    # window covering both
    assert slice_history([h1, h2], 0, 100) == (5.0, 50.0, 3.0, pytest.approx(0.5))
    # window covering only instance 1
    assert slice_history([h1, h2], 0, 12) == (2.0, 20.0, 1.0, 0.2)


def test_slice_history_series_matches_per_stamp_slices():
    """The merge-pass series is byte-identical to per-stamp slice_history."""
    from condor.fetchers.bot_performance import slice_history, slice_history_series

    # Single instance.
    h1 = [
        (10.0, 1.0, 100.0, 1.0, 0.1),
        (20.0, 3.0, 300.0, 4.0, 0.3),
        (30.0, 5.0, 500.0, 9.0, 0.5),
    ]
    # Second instance with disjoint sample epochs, including rows earlier
    # than the window's `since` (they fold into the baseline, not the curve).
    h2 = [
        (5.0, 0.5, 50.0, 1.0, 0.05),
        (12.0, 2.5, 250.0, 2.0, 0.25),
        (27.0, 4.5, 450.0, 3.0, 0.45),
        (44.0, 6.5, 650.0, 4.0, 0.65),
    ]

    for histories, since, until in (
        ([h1], 0.0, 100.0),  # single instance
        ([h1, h2], 0.0, 100.0),  # disjoint epochs merged
        ([h1, h2], 11.0, 100.0),  # rows earlier than since → nonzero baseline
        ([h1, h2], 0.0, 28.0),  # until cutoff drops later stamps
    ):
        stamps = sorted({t for h in histories for t, *_ in h if since <= t <= until})
        expected = [(t, *slice_history(histories, since, t)) for t in stamps]
        assert slice_history_series(histories, since, stamps) == expected

    # Empty stamps and empty histories degrade cleanly.
    assert slice_history_series([h1], 0.0, []) == []
    assert slice_history_series([], 0.0, [10.0]) == [(10.0, 0.0, 0.0, 0.0, 0.0)]


def test_slice_history_series_is_linear_not_quadratic():
    """3 instances × 5000 rows: one merge pass stays well under 100ms."""
    import time

    from condor.fetchers.bot_performance import slice_history_series

    histories = []
    for k in range(3):
        histories.append(
            [
                (float(k + i * 3), float(i), float(i * 10), float(i), float(i) / 10)
                for i in range(5000)
            ]
        )
    stamps = sorted({t for h in histories for t, *_ in h})

    t0 = time.perf_counter()
    out = slice_history_series(histories, 0.0, stamps)
    elapsed = time.perf_counter() - t0

    assert len(out) == len(stamps)
    assert elapsed < 0.1, f"series build took {elapsed:.3f}s"


def test_fetch_instance_history_sums_controllers_and_filters_trades():
    from condor.fetchers.bot_performance import fetch_instance_history

    class _Client:
        async def get_controller_performance_history(self, **_kw):
            return {
                "data": [
                    {
                        "timestamp": "2026-07-24T18:00:00+00:00",
                        "performance": {
                            "realized_pnl_quote": 1.0,
                            "volume_traded": 100.0,
                            # 2 real trades; EARLY_STOP churn + FAILED excluded
                            "close_type_counts": {
                                "CloseType.TAKE_PROFIT": 2,
                                "CloseType.EARLY_STOP": 500,
                                "CloseType.FAILED": 9,
                            },
                        },
                    },
                    {
                        "timestamp": "2026-07-24T18:00:00+00:00",  # 2nd controller, same ts
                        "performance": {
                            "realized_pnl_quote": 0.5,
                            "volume_traded": 40.0,
                            "close_type_counts": {"CloseType.STOP_LOSS": 1},
                        },
                    },
                ]
            }

    client = SimpleNamespace(bot_orchestration=_Client())
    hist = asyncio.run(fetch_instance_history(client, "bot-1"))
    assert len(hist) == 1  # both controllers summed into one timestamp
    ts, realized, volume, trades, fees = hist[0]
    assert realized == 1.5  # 1.0 + 0.5
    assert volume == 140.0  # 100 + 40
    assert trades == 3.0  # TAKE_PROFIT 2 + STOP_LOSS 1; EARLY_STOP/FAILED excluded
    # No cumulative fee column in this payload → 0, which is the signal callers
    # use to fall back to the live open-position figure.
    assert fees == 0.0


# ── Provider wiring ──


def _capture_provider_fetch(monkeypatch) -> dict:
    from condor.agents.performance import AgentPerformance as _AP

    captured: dict = {}

    async def _fake_fetch(client, agent_id, bot_names=None, since=0.0):
        captured["agent_id"] = agent_id
        captured["bot_names"] = bot_names
        captured["since"] = since
        return _AP(agent_id=agent_id, bot_names=list(bot_names or []))

    monkeypatch.setattr(
        "condor.agents.performance.fetch_agent_performance", _fake_fetch
    )
    return captured


def test_executors_provider_falls_back_to_config_bot_name(monkeypatch):
    # No ledger (executor-mode caller / older engine): the configured name is
    # still what the session would deploy under, so it must reach the fetch.
    from condor.agents.providers.executors import ExecutorsProvider

    captured = _capture_provider_fetch(monkeypatch)
    asyncio.run(
        ExecutorsProvider().execute(
            client=object(),
            config={"bot_name": "river"},
            agent_id="river.scalp_1",
        )
    )
    assert captured == {
        "agent_id": "river.scalp_1",
        "bot_names": ["river"],
        "since": 0.0,  # no ledger → no takeover instant → unsliced, as before
    }


def test_executors_provider_prefers_ledger_bases(monkeypatch):
    # With a ledger, the session's OWNED bases win over the configured single
    # name — a session that deployed two bots must see both.
    from condor.agents.providers.executors import ExecutorsProvider

    captured = _capture_provider_fetch(monkeypatch)
    asyncio.run(
        ExecutorsProvider().execute(
            client=object(),
            config={"bot_name": "river"},
            agent_id="river.scalp_1",
            bot_names=["river-btc", "river-eth"],
        )
    )
    assert captured["bot_names"] == ["river-btc", "river-eth"]


# ── Prompt controller-mode block ──


def _minimal_prompt(config):
    from condor.agents.prompts import build_tick_prompt

    agent = SimpleNamespace(instructions="", agent_key="claude-code", slug="river")
    strategy = SimpleNamespace(
        instructions="Do the thing.",
        agent_key="claude-code",
        slug="scalp",
        agent_slug="river",
        dir=None,
    )
    return build_tick_prompt(
        agent=agent,
        strategy=strategy,
        config=config,
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={},
        tick_number=1,
        agent_id="river.scalp_1",
        cached_routines_section="",
    )


def test_prompt_controller_block_present_iff_bot_name():
    with_bot = _minimal_prompt({"bot_name": "river", "execution_mode": "loop"})
    assert "[CONTROLLER MODE]" in with_bot
    assert "river" in with_bot
    assert "manage_controllers" in with_bot

    without = _minimal_prompt({"execution_mode": "loop"})
    assert "[CONTROLLER MODE]" not in without


def test_controller_mode_base_rules_do_not_contradict_the_controller_block():
    """The top-of-prompt rule must name the surface the session actually trades on.

    A controller-mode tick used to open with "Trade ONLY via
    manage_executors(action='create')" and then close with "Do NOT create
    standalone executors" — two opposite orders in one prompt.
    """
    with_bot = _minimal_prompt({"bot_name": "river", "execution_mode": "loop"})
    assert 'Trade ONLY via manage_executors(action="create")' not in with_bot
    assert "steering the controllers" in with_bot
    # The tools it trades with are preloaded, not discovered mid-tick.
    assert "mcp__mcp-hummingbot__manage_controllers" in with_bot
    assert "mcp__mcp-hummingbot__manage_bots" in with_bot
    # Executor mode keeps the strict executor-only rule.
    without = _minimal_prompt({"execution_mode": "loop"})
    assert 'Trade ONLY via manage_executors(action="create")' in without
    assert "mcp__mcp-hummingbot__manage_controllers" not in without


def test_controller_id_hint_is_executor_mode_only():
    """`controller_id` is an executor arg; in controller mode it is noise."""
    with_bot = _minimal_prompt({"bot_name": "river", "execution_mode": "loop"})
    assert "controller_id" not in with_bot

    without = _minimal_prompt({"execution_mode": "loop"})
    assert 'Pass controller_id="river.scalp_1"' in without


# ── The live agent view and the dashboard must agree ──


def _epoch_utc(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso).timestamp()


def test_adopted_bot_pnl_is_sliced_to_the_sessions_window():
    """`since` scopes an inherited bot to what THIS session produced.

    Without it the live tick merged the bot's whole lifetime aggregate, so a
    session that adopted a long-running bot was told it had earned everything
    the bot ever made — while the dashboard sliced the same bot per session.
    """
    from condor.agents.performance import fetch_agent_performance

    inst = "ns-bot-20260701-000000"
    snapshot = [
        {
            "bot_name": inst,
            "controller_id": "c1",
            "timestamp": "2026-07-04T00:00:00+00:00",
            "status": "RUNNING",
            "performance": {
                "realized_pnl_quote": 100.0,  # lifetime
                "unrealized_pnl_quote": 7.0,
                "volume_traded": 5000.0,
                "positions_summary": [],
            },
        }
    ]
    # The session took over at T2, by which point the bot had made $40.
    history = [
        {
            "timestamp": ts,
            "performance": {
                "realized_pnl_quote": cum,
                "volume_traded": vol,
                "close_type_counts": {},
            },
        }
        for ts, cum, vol in (
            ("2026-07-01T00:00:00+00:00", 0.0, 0.0),
            ("2026-07-03T00:00:00+00:00", 40.0, 2000.0),
            ("2026-07-04T00:00:00+00:00", 100.0, 5000.0),
        )
    ]

    class _Client:
        base_url = "http://slice-test"

        async def get_latest_controller_performance(self):
            return snapshot

        async def get_controller_performance_history(self, bot_name, interval, limit):
            return {"data": history}

    class _Executors:
        async def search_executors(self, **_kw):
            return []  # controller mode: nothing tagged with the agent_id

    client = SimpleNamespace(
        bot_orchestration=_Client(),
        executors=_Executors(),
        base_url="http://slice-test",
    )
    took_over = _epoch_utc("2026-07-03T00:00:00+00:00")

    perf = asyncio.run(
        fetch_agent_performance(
            client, "ns.strat_2", bot_names=["ns-bot"], since=took_over
        )
    )

    # $60 earned since the takeover, not the bot's lifetime $100.
    assert perf.realized_pnl == 60.0
    assert perf.volume == 3000.0
    # The open book belongs to whoever runs the bot now — this session.
    assert perf.unrealized_pnl == 7.0
    assert perf.total_pnl == 67.0


def test_without_since_the_lifetime_aggregate_is_still_used():
    """A session that deployed the bot itself has no inherited PnL to strip."""
    from condor.agents.performance import AgentPerformance, _merge_bot_perf

    bot = {
        "bot_name": "ns-bot",
        "realized_pnl_quote": 100.0,
        "unrealized_pnl_quote": 7.0,
        "volume_traded": 5000.0,
        "cum_fees_quote": 2.0,
        "controllers": [],
    }
    perf = AgentPerformance(agent_id="ns.strat_1")
    _merge_bot_perf(perf, bot)

    assert perf.realized_pnl == 100.0
    assert perf.volume == 5000.0
    assert perf.fees == 2.0
    assert perf.total_pnl == 107.0


# ── PnL unification regressions ──
# All three defects below were found together on one live session
# (directional_trader.ema_trend_loop_1): it deployed three bots, lost $1.46, and
# every structured surface reported $0.00 while the model's own MCP reads
# reported the truth to Telegram. Each test pins one link of that chain.


def _history_client(rows):
    class _C:
        async def get_controller_performance_history(self, **kwargs):
            return {"data": rows}

    return SimpleNamespace(bot_orchestration=_C())


def _hist_row(ts, controller_id, realized, volume=0.0):
    return {
        "timestamp": ts,
        "controller_id": controller_id,
        "performance": {
            "realized_pnl_quote": realized,
            "volume_traded": volume,
        },
    }


def test_history_carries_each_controller_forward_across_sparse_buckets():
    """A bucket holding one controller is not the bot's total.

    Above its native resolution the endpoint returns roughly one row per bucket
    *in total*, so a multi-controller bot's controllers arrive round-robin.
    Summing per timestamp read each bucket as the whole bot and produced a series
    that lurched between one controller's cumulative and another's — which
    slice_history then differenced into noise.
    """
    from condor.fetchers.bot_performance import fetch_instance_history

    # BTC settles at -0.30, ETH at -0.70; never both in the same bucket.
    rows = [
        _hist_row("2026-08-06T22:00:00+00:00", "btc", 0.0),
        _hist_row("2026-08-06T22:15:00+00:00", "eth", 0.0),
        _hist_row("2026-08-06T22:30:00+00:00", "btc", -0.30),
        _hist_row("2026-08-06T22:45:00+00:00", "eth", -0.70),
        _hist_row("2026-08-06T23:00:00+00:00", "btc", -0.30),
    ]
    hist = asyncio.run(fetch_instance_history(_history_client(rows), "bot-1"))

    # The bot total is the sum of the carried per-controller cumulatives, so it
    # never regresses when the next bucket happens to carry the other controller.
    assert [round(r[1], 4) for r in hist] == [0.0, 0.0, -0.30, -1.00, -1.00]


def test_history_forward_carry_survives_a_controller_that_stops_reporting():
    """A controller absent from later buckets keeps its last cumulative."""
    from condor.fetchers.bot_performance import fetch_instance_history

    rows = [
        _hist_row("2026-08-06T22:00:00+00:00", "btc", -0.50, 100.0),
        _hist_row("2026-08-06T22:15:00+00:00", "sol", 0.0, 0.0),
        _hist_row("2026-08-06T22:30:00+00:00", "sol", 0.0, 0.0),
    ]
    hist = asyncio.run(fetch_instance_history(_history_client(rows), "bot-1"))

    assert [round(r[1], 4) for r in hist] == [-0.50, -0.50, -0.50]
    assert [round(r[2], 4) for r in hist] == [100.0, 100.0, 100.0]


def test_partition_includes_archived_instances():
    """A stopped bot's realized PnL is exactly what its session is judged on."""
    from condor.fetchers.bot_performance import partition_instances

    agg = _instances_agg(("ema_trend_loop-20260807-045821", "2026-08-07T04:58:21"))
    archived = [
        "ema_trend_loop-20260806-213931",
        "ema_trend_loop-20260807-022130",
        "some_other_bot-20260101-000000",
    ]

    assert partition_instances(agg, ["ema_trend_loop"], archived) == {
        "ema_trend_loop": [
            # Archived first: no snapshot timestamp, and the deploy suffix orders
            # them chronologically among themselves.
            "ema_trend_loop-20260806-213931",
            "ema_trend_loop-20260807-022130",
            "ema_trend_loop-20260807-045821",
        ]
    }


def test_resolve_bots_sums_every_live_instance_of_a_base():
    """Redeploying under a stable base name must not discard the earlier ones."""
    from condor.fetchers.bot_performance import resolve_bots

    agg = _aggregate_by_bot(
        [
            {
                "bot_name": "dn-mm-20260724-100000",
                "controller_id": "c",
                "timestamp": "2026-07-24T10:00:00",
                "performance": {"realized_pnl_quote": -3.0, "volume_traded": 100.0},
            },
            {
                "bot_name": "dn-mm-20260724-182221",
                "controller_id": "c",
                "timestamp": "2026-07-24T18:22:21",
                "performance": {"realized_pnl_quote": -1.0, "volume_traded": 50.0},
            },
        ]
    )
    merged = resolve_bots(agg, ["dn-mm"])["dn-mm"]

    assert merged["realized_pnl_quote"] == -4.0
    assert merged["volume_traded"] == 150.0
    # Freshest still supplies the display name; nothing is dropped for it.
    assert merged["bot_name"] == "dn-mm-20260724-182221"


def test_archived_instance_names_come_from_database_paths():
    from condor.fetchers.bot_performance import (
        clear_archived_cache,
        fetch_archived_instances,
    )

    clear_archived_cache()

    class _C:
        async def list_databases(self):
            return [
                "bots/archived/ema_trend_loop-20260806-213931/data/"
                "ema_trend_loop-20260806-213931.sqlite",
                {"db_path": "bots/archived/x/data/other-20260101-000000.db"},
                {"db_path": "bots/archived/y/data/broken.sqlite", "status": "error"},
            ]

    client = SimpleNamespace(archived_bots=_C(), base_url="")

    assert asyncio.run(fetch_archived_instances(client)) == [
        "ema_trend_loop-20260806-213931",
        "other-20260101-000000",
    ]


def test_archived_instance_discovery_degrades_to_empty():
    """A backend without the endpoint costs stopped bots, never running ones."""
    from condor.fetchers.bot_performance import (
        clear_archived_cache,
        fetch_archived_instances,
    )

    clear_archived_cache()

    class _C:
        async def list_databases(self):
            raise RuntimeError("no such endpoint")

    client = SimpleNamespace(archived_bots=_C(), base_url="")

    assert asyncio.run(fetch_archived_instances(client)) == []


def test_a_stopped_bots_realized_pnl_reaches_the_agent():
    """End-to-end: the exact shape that reported $0.00 for twenty ticks.

    The session deployed a bot, stopped it, and deployed another. Nothing is
    running, no executor carries the agent_id, and the whole loss lives in the
    archived instances' history. Reporting $0 here is what sent the dashboard and
    the live agent's core data into disagreement with the model's own MCP reads.
    """
    from condor.fetchers.bot_performance import clear_archived_cache

    clear_archived_cache()

    history = {
        "ema_trend_loop-20260806-213931": [
            _hist_row("2026-08-06T22:00:00+00:00", "btc", 0.0, 193.0),
            _hist_row("2026-08-06T22:45:00+00:00", "btc", -0.3053, 386.19),
            _hist_row("2026-08-07T00:02:00+00:00", "eth", -0.6953, 399.35),
        ],
        "ema_trend_loop-20260807-022130": [
            _hist_row("2026-08-07T02:22:00+00:00", "btc", 0.0, 193.05),
            _hist_row("2026-08-07T03:27:00+00:00", "btc", -0.4635, 385.84),
        ],
    }

    class _Archived:
        async def list_databases(self):
            return [f"bots/archived/{n}/data/{n}.sqlite" for n in history]

    class _Orch:
        async def get_latest_controller_performance(self, bot_name=None):
            return {"data": []}  # nothing running

        async def get_controller_performance_history(
            self, bot_name, interval, limit=None, cursor=None
        ):
            return {"data": history.get(bot_name, [])}

    client = SimpleNamespace(
        base_url="",
        executors=_FakeExecutors({}),  # no executor carries the agent_id
        bot_orchestration=_Orch(),
        archived_bots=_Archived(),
    )

    perf = asyncio.run(
        fetch_agent_performance(
            client,
            "directional_trader.ema_trend_loop_1",
            bot_names=["ema_trend_loop"],
            since=1786052340.0,  # 2026-08-06 21:39 UTC, the session's first tick
        )
    )

    # -0.3053 + -0.6953 (first instance) + -0.4635 (second) — the figure the
    # agent reported to Telegram, which no structured surface could see.
    assert perf.realized_pnl == pytest.approx(-1.4641, abs=1e-4)
    assert perf.total_pnl == pytest.approx(-1.4641, abs=1e-4)
    assert perf.volume == pytest.approx(785.54 + 385.84, abs=1e-2)
    # Resolved through archived history, so the base is not flagged as missing.
    assert perf.unresolved_bases == []


def test_a_base_with_no_instance_anywhere_is_flagged_not_zeroed():
    """'No data' and 'traded flat' must not render alike."""
    from condor.fetchers.bot_performance import clear_archived_cache

    clear_archived_cache()

    class _Archived:
        async def list_databases(self):
            return []

    class _Orch:
        async def get_latest_controller_performance(self, bot_name=None):
            return {"data": []}

        async def get_controller_performance_history(self, bot_name, **kw):
            return {"data": []}

    client = SimpleNamespace(
        base_url="",
        executors=_FakeExecutors({}),
        bot_orchestration=_Orch(),
        archived_bots=_Archived(),
    )

    perf = asyncio.run(
        fetch_agent_performance(
            client, "agent.strat_1", bot_names=["vanished_bot"], since=1786052340.0
        )
    )

    assert perf.unresolved_bases == ["vanished_bot"]
    assert perf.total_pnl == 0.0  # zero, but the caller now knows it is unknown


def test_the_provider_says_when_a_bots_pnl_is_missing():
    """The agent is told a floor, not a flat result — a floor it can go check."""
    from condor.agents.providers.executors import ExecutorsProvider

    perf = AgentPerformance(
        agent_id="agent.strat_1",
        bot_names=["vanished_bot"],
        unresolved_bases=["vanished_bot"],
    )

    async def _fake(*a, **kw):
        return perf

    import condor.agents.performance as perf_mod

    original = perf_mod.fetch_agent_performance
    perf_mod.fetch_agent_performance = _fake
    try:
        result = asyncio.run(
            ExecutorsProvider().execute(
                object(), {"bot_name": "vanished_bot"}, agent_id="agent.strat_1"
            )
        )
    finally:
        perf_mod.fetch_agent_performance = original

    assert "vanished_bot" in result.summary
    assert "incomplete, not flat" in result.summary


def test_pnl_series_is_continuous_across_a_redeploy():
    """The curve is derived, so it is right for sessions the journal got wrong.

    Per-tick journal snapshots record what the aggregator believed at the time —
    permanently flat for a session that ran while it could not see its bots. This
    series comes from the bots' own history, so it self-corrects, and it steps
    rather than restarting at zero when the session redeploys under one base.
    """
    from condor.agents.performance import fetch_agent_pnl_series
    from condor.fetchers.bot_performance import clear_archived_cache

    clear_archived_cache()

    history = {
        "loop-20260806-213931": [
            _hist_row("2026-08-06T22:00:00+00:00", "btc", 0.0, 193.0),
            _hist_row("2026-08-06T22:45:00+00:00", "btc", -0.3053, 386.19),
        ],
        "loop-20260807-022130": [
            _hist_row("2026-08-07T02:22:00+00:00", "btc", 0.0, 193.05),
            _hist_row("2026-08-07T03:27:00+00:00", "btc", -0.4635, 385.84),
        ],
    }

    class _Archived:
        async def list_databases(self):
            return [f"{n}.sqlite" for n in history]

    class _Orch:
        async def get_latest_controller_performance(self, bot_name=None):
            return {"data": []}

        async def get_controller_performance_history(self, bot_name, **kw):
            return {"data": history.get(bot_name, [])}

    client = SimpleNamespace(
        base_url="", bot_orchestration=_Orch(), archived_bots=_Archived()
    )

    series = asyncio.run(fetch_agent_pnl_series(client, ["loop"], since=1786052340.0))

    pnls = [round(p["pnl"], 4) for p in series]
    # The second instance picks up where the first left off — it does not reset.
    assert pnls == [0.0, -0.3053, -0.3053, -0.7688]
    # And it ends where the KPI does.
    assert pnls[-1] == pytest.approx(-0.3053 + -0.4635, abs=1e-4)


def test_pnl_series_is_empty_without_an_owned_bot():
    """An executor-only session has no bot history; the journal stays the source."""
    from condor.agents.performance import fetch_agent_pnl_series

    assert asyncio.run(fetch_agent_pnl_series(object(), [], since=1.0)) == []
    assert asyncio.run(fetch_agent_pnl_series(object(), ["b"], since=0.0)) == []
