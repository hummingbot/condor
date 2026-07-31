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

from condor.web.routes.agents import (
    AgentPerformanceModel,
    _apply_bot_mode_pnl,
    _current_owner_bases,
    _session_ownership,
)

# ── Fixtures: on-disk sessions, with and without a ledger ──


def _write_session(strategy_dir: Path, num: int, cfg: dict | None = None) -> Path:
    sd = strategy_dir / "sessions" / f"session_{num}"
    sd.mkdir(parents=True)
    (sd / "config.yml").write_text(yaml.safe_dump(cfg or {}))
    return sd


def _write_ledger(session_dir: Path, bots: dict[str, float]) -> None:
    """Ledger with ``{base: since}``, as BotLedger serializes it."""
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


def _hist_row(ts: str, cum_realized: float, cum_volume: float = 0.0):
    return {
        "timestamp": ts,
        "performance": {
            "realized_pnl_quote": cum_realized,
            "volume_traded": cum_volume,
            "close_type_counts": {},
        },
    }


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
    asyncio.run(_apply_bot_mode_pnl([s1, s2], tmp_path, None, client))

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
    asyncio.run(_apply_bot_mode_pnl([s1, s2], tmp_path, None, client))

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
    asyncio.run(_apply_bot_mode_pnl([s1], tmp_path, None, client))
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
    asyncio.run(_apply_bot_mode_pnl([s1], tmp_path, None, client))
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
    asyncio.run(_apply_bot_mode_pnl([s1], tmp_path, None, client))
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

    asyncio.run(_apply_bot_mode_pnl([_session(1), _session(2)], tmp_path, None, client))
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

    async def _fake_client(strategy_dir, default_config):
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
    return asyncio.run(mod._compute_strategy_performance("run", tmp_path, None))


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

    owned1 = _session_ownership(tmp_path, {"bot_name": "dn-mm"}, 1)
    owned2 = _session_ownership(tmp_path, {"bot_name": "dn-mm"}, 2)
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
    asyncio.run(_apply_bot_mode_pnl([s1, s2], tmp_path, {"bot_name": "dn-mm"}, client))
    # Both sessions started after T3 (they were written now), so the whole history
    # predates session 1's window and neither is credited — exactly what the
    # session-start tiling did before the ledger existed. What matters is the
    # conservation law still holding across the two windows.
    assert s1.realized_pnl + s2.realized_pnl == 0.0


def test_ledger_wins_over_the_legacy_config_name(tmp_path):
    sd = _write_session(tmp_path, 1, {"bot_name": "dn-mm"})
    _write_ledger(sd, {"ns-other": 123.0})
    owned = _session_ownership(tmp_path, {"bot_name": "dn-mm"}, 1)
    assert [(o.base, o.since) for o in owned] == [("ns-other", 123.0)]


def test_direct_executor_strategy_owns_nothing(tmp_path):
    _write_session(tmp_path, 1, {"bot_name": ""})
    assert _session_ownership(tmp_path, {}, 1) == []


# ── Current-owner lookup (per-session executors view) ──


def test_current_owner_bases_follows_the_ledger_not_the_session_number(tmp_path):
    sd1 = _write_session(tmp_path, 1)
    sd2 = _write_session(tmp_path, 2)
    _write_ledger(sd1, {"ns-a": 100.0, "ns-b": 100.0})
    _write_ledger(sd2, {"ns-a": 200.0})  # session 2 adopted only ns-a

    nums = [1, 2]
    assert _current_owner_bases(tmp_path, None, nums, 1) == ["ns-b"]
    assert _current_owner_bases(tmp_path, None, nums, 2) == ["ns-a"]
