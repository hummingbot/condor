"""Per-session bot PnL attribution from the ownership ledger (FEAT-018).

The property under test is a conservation law: for any bot base, the sum of
every session's sliced realized PnL equals the bot's cumulative realized PnL —
no gap, no double count — while each slice lands on the session that actually
operated the bot during it, per the ledger's takeover instants.
"""

import asyncio
import json
from pathlib import Path

import yaml

from condor.agents.attribution import (
    OwnershipWindow,
    apply_bot_mode_pnl,
    current_owner_bases,
    ownership_windows,
    session_ownership,
    session_windows,
)
from condor.web.routes.agents import AgentPerformanceModel

# ── Fixtures: on-disk sessions, with and without a ledger ──


def _write_session(strategy_dir: Path, num: int, cfg: dict | None = None) -> Path:
    sd = strategy_dir / "sessions" / f"session_{num}"
    sd.mkdir(parents=True)
    (sd / "config.yml").write_text(yaml.safe_dump(cfg or {}))
    return sd


def _write_ledger(
    session_dir: Path, bots: dict[str, float], until: dict[str, float] | None = None
) -> None:
    """Ledger with ``{base: since}``, as BotLedger serializes it.

    ``until`` stamps a release instant on a base, i.e. the session stopped and
    stopped owning the bot from that moment.
    """
    (session_dir / "owned_bots.json").write_text(
        json.dumps(
            {
                "namespace": "ns",
                "declared": [],
                "bots": {
                    base: {
                        "base": base,
                        "origin": "deployed",
                        "since": since,
                        "last_seen": since,
                        "until": (until or {}).get(base, 0.0),
                    }
                    for base, since in bots.items()
                },
                "violations": [],
            }
        )
    )


def _session(num: int) -> AgentPerformanceModel:
    return AgentPerformanceModel(agent_id=f"a_{num}", session_num=num)


# ── Fake backend ──


class _FakeClient:
    """Serves controller snapshots + per-instance cumulative history."""

    def __init__(self, snapshots: list[dict], history: dict[str, list[dict]]):
        self._snapshots = snapshots
        self._history = history
        self.history_calls: list[str] = []
        self.bot_orchestration = self

    async def get_latest_controller_performance(self):
        return self._snapshots

    async def get_controller_performance_history(self, bot_name, interval, limit):
        self.history_calls.append(bot_name)
        return {"data": self._history.get(bot_name, [])}


def _snap(bot_name: str, ts: str, realized=0.0, unrealized=0.0, positions=None):
    return {
        "bot_name": bot_name,
        "controller_id": f"{bot_name}-c1",
        "timestamp": ts,
        "status": "RUNNING",
        "performance": {
            "realized_pnl_quote": realized,
            "unrealized_pnl_quote": unrealized,
            "volume_traded": 0.0,
            "positions_summary": positions or [],
        },
    }


def _hist_row(
    ts: str,
    cum_realized: float,
    cum_volume: float = 0.0,
    cum_fees: float = 0.0,
    closes: dict[str, int] | None = None,
):
    perf = {
        "realized_pnl_quote": cum_realized,
        "volume_traded": cum_volume,
        "close_type_counts": closes or {},
    }
    if cum_fees:
        perf["cum_fees_quote"] = cum_fees
    return {"timestamp": ts, "performance": perf}


# Fixed clock anchors: the history rows below are ISO strings whose epochs the
# ledger `since` values must interleave with, so compute them from the same source.
def _epoch(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso).timestamp()


T0 = "2026-07-01T00:00:00+00:00"
T1 = "2026-07-02T00:00:00+00:00"
T2 = "2026-07-03T00:00:00+00:00"
T3 = "2026-07-04T00:00:00+00:00"


# ── The conservation law ──


def test_handover_splits_pnl_at_the_takeover_instant(tmp_path):
    """Session A deploys, B adopts after a restart: A frozen, B post-takeover only."""
    sd1 = _write_session(tmp_path, 1)
    sd2 = _write_session(tmp_path, 2)
    _write_ledger(sd1, {"ns-bot": _epoch(T0)})
    _write_ledger(sd2, {"ns-bot": _epoch(T2)})  # B took over at T2

    inst = "ns-bot-20260701-000000"
    client = _FakeClient(
        snapshots=[_snap(inst, T3, realized=100.0)],
        history={
            inst: [
                _hist_row(T0, 0.0),
                _hist_row(T1, 30.0),
                _hist_row(T2, 40.0),  # ← handover: A made 40
                _hist_row(T3, 100.0),  # B made the remaining 60
            ]
        },
    )

    s1, s2 = _session(1), _session(2)
    asyncio.run(apply_bot_mode_pnl([s1, s2], tmp_path, None, client))

    assert s1.realized_pnl == 40.0
    assert s2.realized_pnl == 60.0
    # Conservation: the two slices reproduce the bot's cumulative exactly.
    assert s1.realized_pnl + s2.realized_pnl == 100.0


def test_live_unrealized_goes_to_the_last_owner_not_the_newest_session(tmp_path):
    """A newest session that never adopted the bot inherits nothing."""
    sd1 = _write_session(tmp_path, 1)
    _write_session(tmp_path, 2)  # session 2 exists but owns no bot
    _write_ledger(sd1, {"ns-bot": _epoch(T0)})

    inst = "ns-bot-20260701-000000"
    position = {
        "trading_pair": "BTC-USD",
        "connector_name": "hyperliquid",
        "side": "TradeType.BUY",
        "amount": 1.0,
        "breakeven_price": 100.0,
        "unrealized_pnl_quote": 7.0,
        "cum_fees_quote": 0.5,
    }
    client = _FakeClient(
        snapshots=[
            _snap(inst, T3, realized=10.0, unrealized=7.0, positions=[position])
        ],
        history={inst: [_hist_row(T0, 0.0), _hist_row(T3, 10.0)]},
    )

    s1, s2 = _session(1), _session(2)
    asyncio.run(apply_bot_mode_pnl([s1, s2], tmp_path, None, client))

    assert s1.unrealized_pnl == 7.0
    assert s1.open_count == 1
    assert len(s1.executors) == 1
    # The newer session never took the bot over — it holds none of the open book.
    assert s2.unrealized_pnl == 0.0
    assert s2.open_count == 0
    assert s2.executors == []


def test_session_owning_two_bots_sums_them_without_parent_folding(tmp_path):
    """`ns-bot-btc` and `ns-bot-eth` land on their own base, and sum for the owner."""
    sd1 = _write_session(tmp_path, 1)
    _write_ledger(sd1, {"ns-bot-btc": _epoch(T0), "ns-bot-eth": _epoch(T0)})

    btc = "ns-bot-btc-20260701-000000"
    eth = "ns-bot-eth-20260701-000000"
    client = _FakeClient(
        snapshots=[_snap(btc, T3, realized=10.0), _snap(eth, T3, realized=25.0)],
        history={
            btc: [_hist_row(T0, 0.0), _hist_row(T3, 10.0)],
            eth: [_hist_row(T0, 0.0), _hist_row(T3, 25.0)],
        },
    )

    s1 = _session(1)
    asyncio.run(apply_bot_mode_pnl([s1], tmp_path, None, client))
    assert s1.realized_pnl == 35.0


def test_parent_base_does_not_swallow_its_tagged_sibling(tmp_path):
    """Owning both `ns-bot` and `ns-bot-btc`, each instance counts exactly once."""
    sd1 = _write_session(tmp_path, 1)
    _write_ledger(sd1, {"ns-bot": _epoch(T0), "ns-bot-btc": _epoch(T0)})

    parent = "ns-bot-20260701-000000"
    child = "ns-bot-btc-20260701-101500"  # tag + deploy timestamp
    client = _FakeClient(
        snapshots=[_snap(parent, T3, realized=10.0), _snap(child, T3, realized=25.0)],
        history={
            parent: [_hist_row(T0, 0.0), _hist_row(T3, 10.0)],
            child: [_hist_row(T0, 0.0), _hist_row(T3, 25.0)],
        },
    )

    s1 = _session(1)
    asyncio.run(apply_bot_mode_pnl([s1], tmp_path, None, client))
    # 35, not 60: the child instance is attributed to `-btc` alone.
    assert s1.realized_pnl == 35.0


def test_multi_controller_bot_rolls_up_to_one_session_figure(tmp_path):
    """Three controllers on one bot instance sum into a single session number."""
    sd1 = _write_session(tmp_path, 1)
    _write_ledger(sd1, {"ns-bot": _epoch(T0)})

    inst = "ns-bot-20260701-000000"
    snaps = [
        dict(_snap(inst, T3, realized=5.0), controller_id=f"c{i}") for i in range(3)
    ]
    rows = [dict(_hist_row(T3, 5.0), controller_id=f"c{i}") for i in range(3)]
    client = _FakeClient(
        snapshots=snaps,
        history={inst: [_hist_row(T0, 0.0)] + rows},
    )

    s1 = _session(1)
    asyncio.run(apply_bot_mode_pnl([s1], tmp_path, None, client))
    assert s1.realized_pnl == 15.0  # 3 controllers × 5


def test_instance_history_is_fetched_once_per_instance(tmp_path):
    """Grouping by base must not multiply the (expensive) history fetches."""
    sd1 = _write_session(tmp_path, 1)
    sd2 = _write_session(tmp_path, 2)
    _write_ledger(sd1, {"ns-bot": _epoch(T0), "ns-bot-btc": _epoch(T0)})
    _write_ledger(sd2, {"ns-bot": _epoch(T2), "ns-bot-btc": _epoch(T2)})

    parent = "ns-bot-20260701-000000"
    child = "ns-bot-btc-20260701-101500"
    client = _FakeClient(
        snapshots=[_snap(parent, T3), _snap(child, T3)],
        history={parent: [_hist_row(T3, 1.0)], child: [_hist_row(T3, 1.0)]},
    )

    asyncio.run(apply_bot_mode_pnl([_session(1), _session(2)], tmp_path, None, client))
    # Two instances, four (base, owner) windows → still exactly two fetches.
    assert sorted(client.history_calls) == sorted([parent, child])


# ── End to end: the rollup the /agents views read ──


def _rollup(monkeypatch, tmp_path, client):
    """Run the real ``_compute_strategy_performance`` with the backend faked out.

    Only the two I/O seams are stubbed — the API client and the per-session
    executor fetch (empty: these sessions trade through the bot, not through
    agent_id-tagged executors) — so the attribution, the freezing and the totals
    are the production code path.
    """
    from condor.web.routes import agents as mod

    async def _fake_client(strategy_dir, default_config, principal):
        return client, "srv"

    async def _no_executors(client, ids, bot_names, failed_ids=None):
        from condor.agents.performance import AgentPerformance

        return {aid: AgentPerformance(agent_id=aid) for aid in ids}

    monkeypatch.setattr(mod, "_get_client_for_strategy", _fake_client)
    monkeypatch.setattr(
        "condor.agents.performance.fetch_agent_performance_batch", _no_executors
    )
    mod._PERF_CACHE.clear()
    mod._CLOSED_PERF_CACHE.clear()
    return asyncio.run(mod._compute_strategy_performance("run", tmp_path, None, 1))


def test_rollup_of_a_handover_sums_to_the_bots_cumulative(monkeypatch, tmp_path):
    sd1 = _write_session(tmp_path, 1)
    sd2 = _write_session(tmp_path, 2)
    _write_ledger(sd1, {"ns-bot": _epoch(T0)})
    _write_ledger(sd2, {"ns-bot": _epoch(T2)})

    inst = "ns-bot-20260701-000000"
    client = _FakeClient(
        snapshots=[_snap(inst, T3, realized=100.0, unrealized=8.0)],
        history={inst: [_hist_row(T0, 0.0), _hist_row(T2, 40.0), _hist_row(T3, 100.0)]},
    )

    sessions, totals = _rollup(monkeypatch, tmp_path, client)
    by_num = {s.session_num: s for s in sessions}
    assert by_num[1].realized_pnl == 40.0
    assert by_num[2].realized_pnl == 60.0
    assert by_num[1].unrealized_pnl == 0.0  # handed over: no live book
    assert by_num[2].unrealized_pnl == 8.0
    # The strategy total is the bot's cumulative — distributed, never duplicated.
    assert totals["realized_pnl"] == 100.0
    assert totals["total_pnl"] == 108.0


def test_session_detail_and_rollup_report_the_same_trade_count(monkeypatch, tmp_path):
    """One session, one bot, two surfaces, one number (CORR-114).

    The strategy rollup counted round-trip closes from the sliced history while
    the session detail counted rows built from the open book, so the same session
    read "50 trades" on the strategy list and "2 trades" on its own page — both
    with 0 closed positions.
    """
    from types import SimpleNamespace

    from condor.agents.performance import fetch_agent_performance

    sd1 = _write_session(tmp_path, 1)
    _write_ledger(sd1, {"ns-bot": _epoch(T0)})

    inst = "ns-bot-20260701-000000"
    positions = [
        {"trading_pair": pair, "amount": 1.0, "breakeven_price": 60.0}
        for pair in ("BTC-USD", "ETH-USD")
    ]
    closes = {
        "CloseType.TAKE_PROFIT": 40,
        "CloseType.STOP_LOSS": 10,
        "CloseType.EARLY_STOP": 900,  # pmm re-quoting churn, never a trade
    }
    client = _FakeClient(
        snapshots=[_snap(inst, T3, realized=100.0, positions=positions)],
        history={inst: [_hist_row(T0, 0.0), _hist_row(T3, 100.0, closes=closes)]},
    )

    # The session-detail path, before _rollup patches the executor fetch out.
    async def _no_executors(**_kw):
        return []

    client.executors = SimpleNamespace(search_executors=_no_executors)
    detail = asyncio.run(
        fetch_agent_performance(
            client, "a_1", windows={"ns-bot": OwnershipWindow(_epoch(T0))}
        )
    )

    rollup = next(s for s in _rollup(monkeypatch, tmp_path, client)[0])

    assert detail.trade_count == rollup.trade_count == 50
    assert detail.closed_count == rollup.closed_count == 50
    assert detail.open_count == 2  # the open book is not a trade count


def test_rollup_of_one_session_on_two_bots_sums_both(monkeypatch, tmp_path):
    sd1 = _write_session(tmp_path, 1)
    _write_ledger(sd1, {"ns-bot-btc": _epoch(T0), "ns-bot-eth": _epoch(T0)})

    btc, eth = "ns-bot-btc-20260701-000000", "ns-bot-eth-20260701-000000"
    client = _FakeClient(
        snapshots=[
            _snap(btc, T3, realized=10.0, unrealized=1.0),
            _snap(eth, T3, realized=25.0, unrealized=2.0),
        ],
        history={
            btc: [_hist_row(T0, 0.0), _hist_row(T3, 10.0)],
            eth: [_hist_row(T0, 0.0), _hist_row(T3, 25.0)],
        },
    )

    sessions, totals = _rollup(monkeypatch, tmp_path, client)
    assert len(sessions) == 1
    assert totals["realized_pnl"] == 35.0
    assert totals["unrealized_pnl"] == 3.0
    assert totals["total_pnl"] == 38.0


# ── Legacy path: sessions with no ledger ──


def test_ledgerless_sessions_keep_session_start_tiling(tmp_path):
    """No owned_bots.json → the pre-ledger windows, unchanged."""
    _write_session(tmp_path, 1, {"bot_name": "dn-mm"})
    _write_session(tmp_path, 2, {"bot_name": "dn-mm"})

    owned1 = session_ownership(tmp_path, {"bot_name": "dn-mm"}, 1)
    owned2 = session_ownership(tmp_path, {"bot_name": "dn-mm"}, 2)
    assert [o.base for o in owned1] == ["dn-mm"]
    assert [o.origin for o in owned1] == ["legacy"]
    # Windows come from session start times, and session 2 started after 1.
    assert owned1[0].since <= owned2[0].since

    inst = "dn-mm-20260701-000000"
    client = _FakeClient(
        snapshots=[_snap(inst, T3, realized=50.0)],
        history={inst: [_hist_row(T0, 0.0), _hist_row(T3, 50.0)]},
    )
    s1, s2 = _session(1), _session(2)
    asyncio.run(apply_bot_mode_pnl([s1, s2], tmp_path, {"bot_name": "dn-mm"}, client))
    # Both sessions started after T3 (they were written now), so the whole history
    # predates session 1's window and neither is credited — exactly what the
    # session-start tiling did before the ledger existed. What matters is the
    # conservation law still holding across the two windows.
    assert s1.realized_pnl + s2.realized_pnl == 0.0


def test_ledger_wins_over_the_legacy_config_name(tmp_path):
    sd = _write_session(tmp_path, 1, {"bot_name": "dn-mm"})
    _write_ledger(sd, {"ns-other": 123.0})
    owned = session_ownership(tmp_path, {"bot_name": "dn-mm"}, 1)
    assert [(o.base, o.since) for o in owned] == [("ns-other", 123.0)]


def test_direct_executor_strategy_owns_nothing(tmp_path):
    _write_session(tmp_path, 1, {"bot_name": ""})
    assert session_ownership(tmp_path, {}, 1) == []


# ── Current-owner lookup (per-session executors view) ──


def testcurrent_owner_bases_follows_the_ledger_not_the_session_number(tmp_path):
    sd1 = _write_session(tmp_path, 1)
    sd2 = _write_session(tmp_path, 2)
    _write_ledger(sd1, {"ns-a": 100.0, "ns-b": 100.0})
    _write_ledger(sd2, {"ns-a": 200.0})  # session 2 adopted only ns-a

    nums = [1, 2]
    assert current_owner_bases(tmp_path, None, nums, 1) == ["ns-b"]
    assert current_owner_bases(tmp_path, None, nums, 2) == ["ns-a"]


# ── Whole-server snapshot is fetched once per rollup, not once per strategy ──


def test_rollup_fans_out_one_snapshot_call_for_all_strategies(tmp_path):
    """N strategies on one server share a single whole-server snapshot fetch.

    ``list_agents`` gathers every strategy's ``apply_bot_mode_pnl`` at once, and
    ``get_latest_controller_performance()`` returns the same whole-server payload
    to each of them — so the fan-out must collapse to one round-trip.
    """
    from condor.fetchers.bot_performance import (
        clear_history_cache,
        clear_snapshot_cache,
    )

    class _CountingClient(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.base_url = "http://rollup-server:8000"
            self.snapshot_calls = 0

        async def get_latest_controller_performance(self):
            self.snapshot_calls += 1
            return self._snapshots

    inst = "ns-bot-20260701-000000"
    client = _CountingClient(
        snapshots=[_snap(inst, T3, realized=100.0)],
        history={inst: [_hist_row(T0, 0.0), _hist_row(T3, 100.0)]},
    )

    strategies = []
    for i in range(3):
        sdir = tmp_path / f"strategy_{i}"
        _write_ledger(_write_session(sdir, 1), {"ns-bot": _epoch(T0)})
        strategies.append((sdir, [_session(1)]))

    async def _go():
        await asyncio.gather(
            *[
                apply_bot_mode_pnl(sessions, sdir, None, client)
                for sdir, sessions in strategies
            ]
        )

    clear_snapshot_cache()
    clear_history_cache()
    try:
        asyncio.run(_go())
    finally:
        clear_snapshot_cache()
        clear_history_cache()

    assert client.snapshot_calls == 1
    # Every strategy still gets its attribution from that one snapshot.
    assert [sessions[0].realized_pnl for _, sessions in strategies] == [100.0] * 3


# ── Regressions: the four ways attribution used to be wrong ──


def test_long_lived_bot_does_not_dump_old_pnl_on_one_session(tmp_path):
    """A span past the finest interval's reach must still tile correctly.

    ``5m`` × the 500-row cap covers ~41h. Sessions whose windows closed before
    that read as $0 while the one straddling the boundary absorbed all of them —
    the strategy total stayed right, which is why it hid. The interval now widens
    to span the ownership timeline instead.
    """
    from datetime import datetime, timedelta

    day0 = datetime.fromisoformat("2026-06-01T00:00:00+00:00")
    marks = [(day0 + timedelta(days=n)).isoformat() for n in range(11)]

    # Three sessions, each owning the bot for ~4 days — far past a 41h window.
    for num, start in ((1, 0), (2, 4), (3, 8)):
        _write_ledger(_write_session(tmp_path, num), {"ns-bot": _epoch(marks[start])})

    inst = "ns-bot-20260601-000000"
    # $10/day, cumulative: day N ⇒ $10N.
    client = _FakeClient(
        snapshots=[_snap(inst, marks[10], realized=100.0)],
        history={inst: [_hist_row(m, 10.0 * n) for n, m in enumerate(marks)]},
    )

    s1, s2, s3 = _session(1), _session(2), _session(3)
    asyncio.run(apply_bot_mode_pnl([s1, s2, s3], tmp_path, None, client))

    # Each session keeps only what it earned; none is starved and none inherits.
    assert s1.realized_pnl == 40.0  # days 0–4
    assert s2.realized_pnl == 40.0  # days 4–8
    assert s3.realized_pnl == 20.0  # days 8–10 (history ends at day 10)
    assert s1.realized_pnl + s2.realized_pnl + s3.realized_pnl == 100.0


def test_interval_widens_to_cover_the_ownership_span(tmp_path):
    """The resolution is chosen from the span, not hardcoded to 5m."""
    from condor.fetchers.bot_performance import choose_interval

    assert choose_interval(3600) == "5m"  # an hour fits at the finest rung
    assert choose_interval(10 * 86400) == "1h"  # 10 days needs coarser buckets
    assert choose_interval(400 * 86400) == "1d"  # beyond the ladder → coarsest


def test_fees_are_sliced_per_session_not_dumped_on_the_last_owner(tmp_path):
    """Fees follow the same windows as realized PnL."""
    _write_ledger(_write_session(tmp_path, 1), {"ns-bot": _epoch(T0)})
    _write_ledger(_write_session(tmp_path, 2), {"ns-bot": _epoch(T2)})

    inst = "ns-bot-20260701-000000"
    client = _FakeClient(
        snapshots=[_snap(inst, T3, realized=100.0)],
        history={
            inst: [
                _hist_row(T0, 0.0, cum_fees=0.0),
                _hist_row(T2, 40.0, cum_fees=4.0),  # session 1 paid $4
                _hist_row(T3, 100.0, cum_fees=9.0),  # session 2 paid $5
            ]
        },
    )

    s1, s2 = _session(1), _session(2)
    asyncio.run(apply_bot_mode_pnl([s1, s2], tmp_path, None, client))

    assert s1.fees == 4.0
    assert s2.fees == 5.0


def test_fees_fall_back_to_live_figure_when_history_has_no_fee_column(tmp_path):
    """A backend without a cumulative fee column must not report $0 everywhere."""
    _write_ledger(_write_session(tmp_path, 1), {"ns-bot": _epoch(T0)})

    inst = "ns-bot-20260701-000000"
    position = {
        "trading_pair": "BTC-USD",
        "connector_name": "hyperliquid",
        "side": "TradeType.BUY",
        "amount": 1.0,
        "breakeven_price": 100.0,
        "unrealized_pnl_quote": 0.0,
        "cum_fees_quote": 3.5,
    }
    client = _FakeClient(
        snapshots=[_snap(inst, T3, realized=100.0, positions=[position])],
        history={inst: [_hist_row(T0, 0.0), _hist_row(T3, 100.0)]},  # no fees
    )

    s1 = _session(1)
    asyncio.run(apply_bot_mode_pnl([s1], tmp_path, None, client))

    assert s1.fees == 3.5


def test_released_session_stops_accruing_a_surviving_bot(tmp_path):
    """A stopped session keeps its slice but not what the bot earned afterwards."""
    sd1 = _write_session(tmp_path, 1)
    # Owned from T0, released at T2 — the bot kept running to T3 unattended.
    _write_ledger(sd1, {"ns-bot": _epoch(T0)}, until={"ns-bot": _epoch(T2)})

    inst = "ns-bot-20260701-000000"
    position = {
        "trading_pair": "BTC-USD",
        "connector_name": "hyperliquid",
        "side": "TradeType.BUY",
        "amount": 1.0,
        "breakeven_price": 100.0,
        "unrealized_pnl_quote": 7.0,
    }
    client = _FakeClient(
        snapshots=[
            _snap(inst, T3, realized=100.0, unrealized=7.0, positions=[position])
        ],
        history={inst: [_hist_row(T0, 0.0), _hist_row(T2, 40.0), _hist_row(T3, 100.0)]},
    )

    s1 = _session(1)
    asyncio.run(apply_bot_mode_pnl([s1], tmp_path, None, client))

    assert s1.realized_pnl == 40.0  # only up to the release
    assert s1.unrealized_pnl == 0.0  # an ended session holds no open book
    assert s1.open_count == 0


def test_released_session_is_not_the_current_owner(tmp_path):
    """current_owner_bases and the slicing agree that a released bot is unowned."""
    sd1 = _write_session(tmp_path, 1)
    _write_ledger(sd1, {"ns-bot": _epoch(T0)}, until={"ns-bot": _epoch(T2)})

    assert current_owner_bases(tmp_path, None, [1], 1) == []


def test_release_is_idempotent_and_reopens_on_further_use(tmp_path):
    """Stopping twice cannot extend a window; operating again re-opens it."""
    from condor.agents.ownership import BotLedger, read_owned

    sd = tmp_path / "session_1"
    sd.mkdir()
    ledger = BotLedger("ns", sd)
    ledger.note_deploy("ns-bot", now=100.0)

    ledger.release(now=200.0)
    ledger.release(now=300.0)  # a second stop must not move the instant
    assert read_owned(sd)[0].until == 200.0

    # A tick after the release means the session is operating it again.
    ledger.adopt("ns-bot", now=400.0)
    assert read_owned(sd)[0].until == 0.0


def test_release_never_closes_a_window_before_it_opened(tmp_path):
    """A stale instant is clamped to ``since`` — a negative slice is not a window."""
    from condor.agents.ownership import BotLedger, read_owned

    sd = tmp_path / "session_1"
    sd.mkdir()
    ledger = BotLedger("ns", sd)
    ledger.note_deploy("ns-bot", now=500.0)

    ledger.release(now=100.0)  # older than the deploy
    assert read_owned(sd)[0].until == 500.0


def test_release_preserves_namespace_for_a_ledger_opened_without_one(tmp_path):
    """Boot reconciliation closes windows with no strategy context to hand."""
    from condor.agents.ownership import BotLedger, read_owned

    sd = tmp_path / "session_1"
    sd.mkdir()
    BotLedger("ns", sd, declared=["legacy-bot"]).note_deploy("ns-bot", now=100.0)

    BotLedger("", sd).release(now=200.0)  # what loops._release_ownership does

    reopened = BotLedger("", sd)
    assert reopened.namespace == "ns"  # not blanked by the release write
    assert reopened.declared == ["legacy-bot"]
    assert read_owned(sd)[0].until == 200.0


# ── One engine, two surfaces (ARCH-191) ──


def test_rollup_and_agent_view_agree_on_an_adopted_bot():
    """The dashboard rollup and the agent's own fetch share one attribution engine.

    A session that adopts a long-running bot must be credited the same
    realized / volume / trades / fees on both surfaces: the web strategy rollup
    (``apply_bot_mode_pnl``) and the agent's own ``fetch_agent_performance``.
    Both now fold through ``condor.agents.attribution``, so agreement is
    structural — this test pins the contract over a synthetic history.
    """
    import tempfile
    from types import SimpleNamespace

    from condor.agents.performance import fetch_agent_performance

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        sd1 = _write_session(tmp_path, 1)
        # The bot ran since T0; this session only took it over at T2.
        _write_ledger(sd1, {"ns-bot": _epoch(T2)})

        inst = "ns-bot-20260701-000000"
        closes = {"CloseType.TAKE_PROFIT": 3}
        history = {
            inst: [
                _hist_row(T0, 0.0, cum_volume=0.0, cum_fees=0.0),
                _hist_row(T2, 40.0, cum_volume=4000.0, cum_fees=4.0),
                _hist_row(T3, 100.0, cum_volume=9000.0, cum_fees=9.0, closes=closes),
            ]
        }
        snapshots = [_snap(inst, T3, realized=100.0, unrealized=7.0)]

        # Web rollup surface.
        rollup_client = _FakeClient(snapshots=snapshots, history=history)
        s1 = _session(1)
        asyncio.run(apply_bot_mode_pnl([s1], tmp_path, None, rollup_client))

        # Agent's own surface, over the same ownership window.
        async def _no_executors(**_kw):
            return []

        agent_client = _FakeClient(snapshots=snapshots, history=history)
        agent_client.executors = SimpleNamespace(search_executors=_no_executors)
        owned = session_ownership(tmp_path, None, 1)
        detail = asyncio.run(
            fetch_agent_performance(
                agent_client, "a_1", windows=ownership_windows(owned)
            )
        )

        # Post-takeover only — the inherited 40/4000/4.0 belongs to nobody here.
        assert s1.realized_pnl == detail.realized_pnl == 60.0
        assert s1.volume == detail.volume == 5000.0
        assert s1.fees == detail.fees == 5.0
        assert s1.trade_count == detail.trade_count == 3
        assert s1.unrealized_pnl == detail.unrealized_pnl == 7.0
        assert s1.fees_known and detail.fees_known


def _both_surfaces(tmp_path: Path, history: dict, snapshots: list[dict]):
    """Run the same synthetic bot through both surfaces of the shared engine.

    Returns ``(rollup_session, agent_detail)`` for a single session that adopted
    ``ns-bot`` at T2 — the web strategy rollup (``apply_bot_mode_pnl``) and the
    agent's own ``fetch_agent_performance``, over the identical ownership window.
    """
    from types import SimpleNamespace

    from condor.agents.performance import fetch_agent_performance

    sd1 = _write_session(tmp_path, 1)
    _write_ledger(sd1, {"ns-bot": _epoch(T2)})

    rollup_client = _FakeClient(snapshots=snapshots, history=history)
    s1 = _session(1)
    asyncio.run(apply_bot_mode_pnl([s1], tmp_path, None, rollup_client))

    async def _no_executors(**_kw):
        return []

    agent_client = _FakeClient(snapshots=snapshots, history=history)
    agent_client.executors = SimpleNamespace(search_executors=_no_executors)
    owned = session_ownership(tmp_path, None, 1)
    detail = asyncio.run(
        fetch_agent_performance(agent_client, "a_1", windows=ownership_windows(owned))
    )
    return s1, detail


def test_live_fee_fallback_marks_the_figure_a_floor_on_both_surfaces(tmp_path):
    """A fee-less history plus a live cumulative: same number AND same caveat.

    The backend omitted the cumulative fee column, so every owner window slices to
    zero fees and the only figure there is comes from the live snapshot's open
    positions — a floor, not a total. Both surfaces must therefore report the same
    ``fees`` *and* ``fees_known=False`` ([[CORR-216]]): the rollup used to fold raw
    and top up after (flag False) while the agent's view substituted before folding
    (flag True), so the two disagreed on identical data.
    """
    inst = "ns-bot-20260701-000000"
    closes = {"CloseType.TAKE_PROFIT": 3}
    history = {
        inst: [
            _hist_row(T0, 0.0, cum_volume=0.0),
            _hist_row(T2, 40.0, cum_volume=4000.0),
            _hist_row(T3, 100.0, cum_volume=9000.0, closes=closes),
        ]
    }
    snapshots = [
        _snap(
            inst,
            T3,
            realized=100.0,
            unrealized=7.0,
            positions=[{"cum_fees_quote": 12.5}],
        )
    ]

    s1, detail = _both_surfaces(tmp_path, history, snapshots)

    assert s1.fees == detail.fees == 12.5
    assert s1.fees_known is detail.fees_known is False
    # The rest of the attribution is unchanged by the fee rule.
    assert s1.realized_pnl == detail.realized_pnl == 60.0
    assert s1.volume == detail.volume == 5000.0
    assert s1.trade_count == detail.trade_count == 3


def test_no_fees_anywhere_still_reads_as_unknown_on_both_surfaces(tmp_path):
    """Volume, no fee column, and no live figure either: unknown, not free.

    With nothing to fall back to the top-up is a no-op, so the flag is left where
    ``fold_sliced_window``'s heuristic put it — False, on both surfaces.
    """
    inst = "ns-bot-20260701-000000"
    history = {
        inst: [
            _hist_row(T0, 0.0, cum_volume=0.0),
            _hist_row(T2, 40.0, cum_volume=4000.0),
            _hist_row(T3, 100.0, cum_volume=9000.0),
        ]
    }
    snapshots = [_snap(inst, T3, realized=100.0, unrealized=7.0)]

    s1, detail = _both_surfaces(tmp_path, history, snapshots)

    assert s1.fees == detail.fees == 0.0
    assert s1.fees_known is detail.fees_known is False
    assert s1.volume == detail.volume == 5000.0


class _SnapshotDownClient(_FakeClient):
    """The whole-server snapshot errors while the archived listing still works."""

    def __init__(self, history: dict[str, list[dict]], archived: list[str]):
        super().__init__(snapshots=[], history=history)
        self.snapshot_calls = 0
        paths = [f"bots/archived/{n}/data/{n}.sqlite" for n in archived]

        async def _list_databases():
            return paths

        from types import SimpleNamespace

        self.archived_bots = SimpleNamespace(list_databases=_list_databases)

    async def get_latest_controller_performance(self):
        self.snapshot_calls += 1
        raise RuntimeError("controller-performance/latest timed out")


def test_rollup_still_attributes_archived_pnl_when_the_live_snapshot_fails():
    """A snapshot outage must not zero the rollup while the session detail survives.

    Both surfaces start from ``fetch_bot_universe``, whose policy is one: a
    failed snapshot degrades to an empty live set, and a stopped bot's realized
    PnL still resolves through the archived listing — on the strategy rollup
    (``apply_bot_mode_pnl``) exactly as on the agent's own view.
    """
    import tempfile
    from types import SimpleNamespace

    from condor.agents.performance import fetch_agent_performance
    from condor.fetchers.bot_performance import (
        clear_archived_cache,
        clear_history_cache,
        clear_snapshot_cache,
    )

    def _clear():
        clear_snapshot_cache()
        clear_archived_cache()
        clear_history_cache()

    _clear()
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            sd1 = _write_session(tmp_path, 1)
            _write_ledger(sd1, {"ns-bot": _epoch(T2)})

            inst = "ns-bot-20260701-000000"
            history = {
                inst: [
                    _hist_row(T0, 0.0, cum_volume=0.0, cum_fees=0.0),
                    _hist_row(T2, 40.0, cum_volume=4000.0, cum_fees=4.0),
                    _hist_row(T3, 100.0, cum_volume=9000.0, cum_fees=9.0),
                ]
            }

            rollup_client = _SnapshotDownClient(history, archived=[inst])
            s1 = _session(1)
            asyncio.run(apply_bot_mode_pnl([s1], tmp_path, None, rollup_client))
            assert rollup_client.snapshot_calls == 1

            async def _no_executors(**_kw):
                return []

            agent_client = _SnapshotDownClient(history, archived=[inst])
            agent_client.executors = SimpleNamespace(search_executors=_no_executors)
            owned = session_ownership(tmp_path, None, 1)
            detail = asyncio.run(
                fetch_agent_performance(
                    agent_client, "a_1", windows=ownership_windows(owned)
                )
            )

            # The stopped bot's post-takeover slice, not a silent $0.
            assert s1.realized_pnl == detail.realized_pnl == 60.0
            assert s1.volume == detail.volume == 5000.0
            assert s1.fees == detail.fees == 5.0
    finally:
        _clear()


def test_bot_universe_degrades_a_failed_snapshot_to_an_empty_live_set():
    """The shared prelude never raises on a snapshot outage and keeps the archive."""
    from condor.fetchers.bot_performance import (
        clear_archived_cache,
        clear_snapshot_cache,
        fetch_bot_universe,
    )

    clear_snapshot_cache()
    clear_archived_cache()
    try:
        client = _SnapshotDownClient({}, archived=["b-2", "a-1"])
        assert asyncio.run(fetch_bot_universe(client)) == ({}, ["a-1", "b-2"])

        healthy = _FakeClient(
            snapshots=[_snap("live-bot", T3, realized=5.0)], history={}
        )
        live, archived = asyncio.run(fetch_bot_universe(healthy))
        assert set(live) == {"live-bot"} and archived == []
    finally:
        clear_snapshot_cache()
        clear_archived_cache()


# ── ARCH-690: one AgentPerformance -> AgentPerformanceModel projection ──


def _every_field_perf():
    from condor.agents.performance import AgentPerformance

    return AgentPerformance(
        agent_id="demo.s_3",
        realized_pnl=1.5,
        unrealized_pnl=-0.25,
        total_pnl=1.25,
        volume=900.0,
        fees=0.75,
        trade_count=4,
        win_rate=0.5,
        open_count=1,
        closed_count=3,
        executors=[{"id": "ex1", "custom_info": {"k": 1}}],
        bot_names=["bot-a"],
        bot_instances=["bot-a-1", "bot-a-2"],
        controllers=[{"id": "ctrl-1"}],
        close_type_counts={"CloseType.TAKE_PROFIT": 2},
        fees_known=False,
        unresolved_bases=["gone-bot"],
    )


def test_from_perf_carries_every_dataclass_field():
    from dataclasses import fields

    from condor.agents.performance import AgentPerformance

    p = _every_field_perf()
    # Guard the fixture itself: every field must differ from its default, or a
    # dropped field would still compare equal.
    default = AgentPerformance(agent_id="other")
    for f in fields(p):
        assert getattr(p, f.name) != getattr(default, f.name), f.name

    dumped = AgentPerformanceModel.from_perf(p, agent_id=p.agent_id).model_dump()
    shared = [f.name for f in fields(p) if f.name in AgentPerformanceModel.model_fields]
    assert len(shared) == len(fields(p))  # the model covers the whole dataclass
    for name in shared:
        assert dumped[name] == getattr(p, name), name
    assert "bot_name" not in dumped

    assert AgentPerformanceModel.from_perf(p, session_num=7).session_num == 7
    model = AgentPerformanceModel.from_perf(
        p, agent_id="override", kind="experiment", error=True
    )
    assert (model.agent_id, model.kind, model.error) == ("override", "experiment", True)


def test_instance_from_engine_takes_money_fields_off_the_perf_row(monkeypatch):
    import condor.agents.fleet_map as fleet_map
    from condor.web.routes.agents import _instance_from_engine

    monkeypatch.setattr(fleet_map, "read_last_action", lambda journal: "")
    monkeypatch.setattr(fleet_map, "read_last_did", lambda engine: None)

    class _Engine:
        journal = None

        def get_info(self):
            return {
                "agent_id": "demo.s_3",
                "session_num": 3,
                "status": "running",
                "tick_count": 9,
                "daily_pnl": 42.0,
            }

    row = AgentPerformanceModel.from_perf(_every_field_perf(), session_num=3)
    inst = _instance_from_engine(_Engine(), {"demo.s_3": row})
    assert inst.daily_pnl == row.total_pnl
    for name in (
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
        "volume",
        "fees",
        "open_count",
        "closed_count",
        "win_rate",
    ):
        assert getattr(inst, name) == getattr(row, name), name

    bare = _instance_from_engine(_Engine(), {})
    assert bare.daily_pnl == 42.0
    assert (bare.realized_pnl, bare.total_pnl, bare.open_count) == (0.0, 0.0, 0)
    assert bare.win_rate is None


# ── ARCH-662: the ownership window is the unit both surfaces slice over ──


def _executorless(client: "_FakeClient") -> "_FakeClient":
    """The agent-side fetch also walks agent_id-tagged executors; these have none."""
    from types import SimpleNamespace

    async def _no_executors(**_kw):
        return []

    client.executors = SimpleNamespace(search_executors=_no_executors)
    return client


def _released_fixture(tmp_path: Path) -> tuple[list[dict], dict]:
    """test_released_session_stops_accruing_a_surviving_bot's session and bot."""
    sd1 = _write_session(tmp_path, 1)
    _write_ledger(sd1, {"ns-bot": _epoch(T0)}, until={"ns-bot": _epoch(T2)})
    inst = "ns-bot-20260701-000000"
    position = {
        "trading_pair": "BTC-USD",
        "connector_name": "hyperliquid",
        "side": "TradeType.BUY",
        "amount": 1.0,
        "breakeven_price": 100.0,
        "unrealized_pnl_quote": 7.0,
    }
    snapshots = [_snap(inst, T3, realized=100.0, unrealized=7.0, positions=[position])]
    history = {inst: [_hist_row(T0, 0.0), _hist_row(T2, 40.0), _hist_row(T3, 100.0)]}
    return snapshots, history


def test_session_detail_stops_accruing_after_release(tmp_path):
    """A finished session's detail reports its rollup row, not $0 and not $100."""
    from condor.agents.performance import fetch_agent_performance

    snapshots, history = _released_fixture(tmp_path)

    windows = session_windows(tmp_path, None, [1], 1)
    assert windows == {"ns-bot": OwnershipWindow(_epoch(T0), _epoch(T2))}
    detail = asyncio.run(
        fetch_agent_performance(
            _executorless(_FakeClient(snapshots, history)), "a_1", windows=windows
        )
    )

    s1 = _session(1)
    asyncio.run(
        apply_bot_mode_pnl([s1], tmp_path, None, _FakeClient(snapshots, history))
    )

    assert detail.realized_pnl == s1.realized_pnl == 40.0
    assert detail.unrealized_pnl == s1.unrealized_pnl == 0.0
    assert detail.open_count == s1.open_count == 0
    assert detail.executors == []  # the open rows belong to nobody now


def test_session_detail_route_prices_a_released_session(monkeypatch, tmp_path):
    """The route itself: KPI and curve both stop at the release instant."""
    from condor.web.routes import agents as mod

    snapshots, history = _released_fixture(tmp_path)
    client = _executorless(_FakeClient(snapshots, history))

    async def _fake_client(*_a, **_kw):
        return client, "srv"

    monkeypatch.setattr(
        mod,
        "_get_strategy",
        lambda slug, sslug: type("S", (), {"home": tmp_path, "default_config": {}})(),
    )
    monkeypatch.setattr(mod, "_get_client_for_strategy", _fake_client)
    monkeypatch.setattr(mod, "_strategy_principal", lambda *_a: None)
    monkeypatch.setattr(
        mod,
        "enumerate_agent_ids",
        lambda run_key, home: [(f"{run_key}_1", 1, "session")],
    )

    out = asyncio.run(mod.get_session_executors("ns", "st", 1, user=object()))

    perf = out["performance"]
    assert perf["realized_pnl"] == 40.0
    assert perf["unrealized_pnl"] == 0.0 and perf["open_count"] == 0
    assert out["pnl_series"][-1]["pnl"] == 40.0
    # The deployment row is still not live: the gate for display is unchanged.
    assert [r["live"] for r in out["deployments"] if r["kind"] == "bot"] == [False]


def test_two_bases_adopted_at_different_instants_are_sliced_per_base(tmp_path):
    """One scalar ``since`` credited ns-b with PnL from before its own takeover."""
    from condor.agents.performance import fetch_agent_performance

    sd1 = _write_session(tmp_path, 1)
    _write_ledger(sd1, {"ns-a": _epoch(T0), "ns-b": _epoch(T2)})
    a_inst, b_inst = "ns-a-20260701-000000", "ns-b-20260701-000000"
    snapshots = [_snap(a_inst, T3, realized=10.0), _snap(b_inst, T3, realized=100.0)]
    history = {
        a_inst: [_hist_row(T0, 0.0), _hist_row(T3, 10.0)],
        b_inst: [_hist_row(T0, 0.0), _hist_row(T2, 40.0), _hist_row(T3, 100.0)],
    }

    s1 = _session(1)
    asyncio.run(
        apply_bot_mode_pnl([s1], tmp_path, None, _FakeClient(snapshots, history))
    )
    detail = asyncio.run(
        fetch_agent_performance(
            _executorless(_FakeClient(snapshots, history)),
            "a_1",
            windows=ownership_windows(session_ownership(tmp_path, None, 1)),
        )
    )

    # ns-a over [T0, now) = 10, ns-b over [T2, now) = 60.
    assert detail.realized_pnl == s1.realized_pnl == 70.0


def test_session_detail_stops_at_the_next_owners_takeover(tmp_path):
    """Session 1's ledger never saw session 2 adopt; the tiling cuts it anyway."""
    from condor.agents.performance import fetch_agent_performance

    _write_ledger(_write_session(tmp_path, 1), {"ns-bot": _epoch(T0)})
    _write_ledger(_write_session(tmp_path, 2), {"ns-bot": _epoch(T2)})
    inst = "ns-bot-20260701-000000"
    snapshots = [_snap(inst, T3, realized=100.0, unrealized=7.0)]
    history = {inst: [_hist_row(T0, 0.0), _hist_row(T2, 40.0), _hist_row(T3, 100.0)]}

    w1 = session_windows(tmp_path, None, [1, 2], 1)
    w2 = session_windows(tmp_path, None, [1, 2], 2)
    assert w1 == {"ns-bot": OwnershipWindow(_epoch(T0), _epoch(T2))}
    assert w2 == {"ns-bot": OwnershipWindow(_epoch(T2))}
    # The open window and the current-owner gate are one rule.
    assert current_owner_bases(tmp_path, None, [1, 2], 1) == []
    assert current_owner_bases(tmp_path, None, [1, 2], 2) == ["ns-bot"]

    s1, s2 = _session(1), _session(2)
    asyncio.run(
        apply_bot_mode_pnl([s1, s2], tmp_path, None, _FakeClient(snapshots, history))
    )
    d1, d2 = (
        asyncio.run(
            fetch_agent_performance(
                _executorless(_FakeClient(snapshots, history)), f"a_{n}", windows=w
            )
        )
        for n, w in ((1, w1), (2, w2))
    )

    assert d1.realized_pnl == s1.realized_pnl == 40.0
    assert d1.unrealized_pnl == s1.unrealized_pnl == 0.0
    assert d2.realized_pnl == s2.realized_pnl == 60.0
    assert d2.unrealized_pnl == s2.unrealized_pnl == 7.0


def test_ownership_windows_close_at_release_or_handover_whichever_first():
    from condor.agents.ownership import OwnedBot

    owned = [
        OwnedBot(base="held", origin="deployed", since=10.0, last_seen=10.0),
        OwnedBot(
            base="released", origin="adopted", since=10.0, last_seen=10.0, until=50.0
        ),
        OwnedBot(
            base="taken", origin="deployed", since=20.0, last_seen=20.0, until=90.0
        ),
    ]
    windows = ownership_windows(owned, handovers={"taken": 40.0, "held": 0.0})

    assert windows == {
        "held": OwnershipWindow(10.0, 0.0),
        "released": OwnershipWindow(10.0, 50.0),
        "taken": OwnershipWindow(20.0, 40.0),
    }
    assert windows["held"].is_open and not windows["taken"].is_open
    assert windows["held"].bounds(99.0) == (10.0, 99.0)


def test_window_span_runs_to_now_while_any_window_is_open():
    from condor.agents.attribution import window_span

    closed = {"a": OwnershipWindow(30.0, 40.0), "b": OwnershipWindow(10.0, 60.0)}
    assert window_span(closed) == (10.0, 60.0)
    assert window_span({**closed, "c": OwnershipWindow(50.0)}) == (10.0, 0.0)
    assert window_span({}) == (0.0, 0.0)


def test_executors_provider_slices_each_owned_base_to_its_own_window(monkeypatch):
    """The tick carries the ledger unflattened, never ``min(since)``."""
    from condor.agents.ownership import OwnedBot
    from condor.agents.performance import AgentPerformance
    from condor.agents.providers.executors import ExecutorsProvider

    captured: dict = {}

    async def _fake(client, agent_id, bot_names=None, windows=None):
        captured.update(bot_names=bot_names, windows=windows)
        return AgentPerformance(agent_id=agent_id)

    monkeypatch.setattr("condor.agents.performance.fetch_agent_performance", _fake)
    owned = [
        OwnedBot(base="ns-a", origin="deployed", since=100.0, last_seen=1.0),
        OwnedBot(base="ns-b", origin="adopted", since=300.0, last_seen=1.0),
    ]
    asyncio.run(
        ExecutorsProvider().execute(
            object(), {}, agent_id="a_1", bot_names=["ns-a", "ns-b"], owned=owned
        )
    )

    assert captured["windows"] == {
        "ns-a": OwnershipWindow(100.0),
        "ns-b": OwnershipWindow(300.0),
    }


def test_merge_stopped_bot_perf_alias_is_gone():
    import condor.agents.performance as perf_mod

    assert not hasattr(perf_mod, "_merge_stopped_bot_perf")
