"""Tests for FEAT-005 controller mode: bot-by-name perf aggregation + merge.

Covers the reusable bot-performance fetcher, the disjoint merge into
``AgentPerformance``, the wiring through the provider, and the prompt's
controller-mode block.
"""

import asyncio
from types import SimpleNamespace

from condor.agents.config import AgentConfig, load_full_config
from condor.agents.performance import (
    AgentPerformance,
    fetch_agent_performance,
    fetch_agent_performance_batch,
)
from condor.fetchers.bot_performance import (
    _aggregate_by_bot,
    bot_executor_rows,
    extract_snapshots,
    fetch_all_bot_performance,
    fetch_bot_performance,
    resolve_bot,
)

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


def test_fetch_all_and_one_bot():
    client = _FakeClient()
    allp = asyncio.run(fetch_all_bot_performance(client))
    assert set(allp) == {"river", "otherbot"}
    one = asyncio.run(fetch_bot_performance(client, "river"))
    assert one["realized_pnl_quote"] == 7.0
    assert asyncio.run(fetch_bot_performance(client, "ghost")) is None
    assert asyncio.run(fetch_bot_performance(client, "")) is None


def test_fetch_bot_performance_resilient_to_errors():
    class _Boom:
        async def get_latest_controller_performance(self, bot_name=None):
            raise RuntimeError("api down")

    client = SimpleNamespace(bot_orchestration=_Boom())
    assert asyncio.run(fetch_bot_performance(client, "river")) is None


# ── Suffix-tolerant resolution ──


def _snap_with_positions(
    bot_name, controller_id, timestamp, positions, status="running"
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
        },
    }


def test_resolve_bot_exact_and_suffix():
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
    assert resolve_bot(agg, "other")["bot_name"] == "other"
    # base name resolves to the freshest timestamped deploy
    assert resolve_bot(agg, "dn-mm")["bot_name"] == "dn-mm-20260724-182221"
    # the hyphen boundary keeps a sibling base (dn-mmx) from matching dn-mm
    assert resolve_bot(agg, "dn-mm")["bot_name"] != "dn-mmx-20260724-000000"
    # no match → None; empty inputs → None
    assert resolve_bot(agg, "ghost") is None
    assert resolve_bot(agg, "") is None
    assert resolve_bot({}, "dn-mm") is None


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
    merged = asyncio.run(fetch_agent_performance(client, agent_id, bot_name="river"))
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
    merged = asyncio.run(fetch_agent_performance(client, agent_id, bot_name="dn-mm"))
    # bot_name reflects the resolved deployed instance, not just the base
    assert merged.bot_name == "dn-mm-20260724-1"
    assert len(merged.executors) == 1
    assert merged.executors[0]["pair"] == "XYZ:CL-USD"
    assert merged.open_count == 1
    assert merged.fees == 0.3
    assert merged.unrealized_pnl == 2.0  # controller-level aggregate


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
    ghost = asyncio.run(fetch_agent_performance(client, agent_id, bot_name="ghost"))
    assert ghost.bot_name == "ghost"
    assert ghost.unrealized_pnl == no_bot.unrealized_pnl == 2.0
    assert ghost.total_pnl == no_bot.total_pnl
    assert ghost.controllers == []


def test_batch_merges_only_named_agents():
    a1, a2 = "river.scalp_1", "plain.scalp_1"
    client = _FakeClient(rows_by_id={})
    out = asyncio.run(fetch_agent_performance_batch(client, [a1, a2], {a1: "river"}))
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


def test_resolve_bot_instances_returns_all_matches_sorted():
    from condor.fetchers.bot_performance import resolve_bot_instances

    def _snap(name, ts):
        return {
            "bot_name": name,
            "controller_id": "c",
            "timestamp": ts,
            "performance": {},
        }

    agg = _aggregate_by_bot(
        [
            _snap("dn-mm-20260101-000000", "2026-01-01T00:00:00"),
            _snap("dn-mm-20260724-182221", "2026-07-24T18:22:21"),
            _snap("dn-mmx-20260724-000000", "2026-07-24T00:00:00"),
            _snap("other", "2026-07-24T00:00:00"),
        ]
    )
    got = resolve_bot_instances(agg, "dn-mm")
    assert got == ["dn-mm-20260101-000000", "dn-mm-20260724-182221"]  # oldest→newest
    assert resolve_bot_instances(agg, "nope") == []


def test_slice_history_tiles_exactly():
    from condor.fetchers.bot_performance import slice_history

    # one instance, cumulative realized/volume/trades at t=10,20,30
    hist = [
        (10.0, 1.0, 100.0, 1.0),
        (20.0, 3.0, 300.0, 4.0),
        (30.0, 5.0, 500.0, 9.0),
    ]
    # window fully before → 0; fully after start baseline → final delta
    assert slice_history([hist], 0, 5) == (0.0, 0.0, 0.0)
    # [0,20] captures rows at 10 and 20 → cum_at(20)-cum_at(0)=3
    assert slice_history([hist], 0, 20) == (3.0, 300.0, 4.0)
    # [20,40] → cum_at(40)=final(5) - cum_at(20)=3 → 2
    assert slice_history([hist], 20, 40) == (2.0, 200.0, 5.0)
    # tiling: [0,20)+[20,40) sums to the instance's full cumulative
    a = slice_history([hist], 0, 20)
    b = slice_history([hist], 20, 40)
    assert (a[0] + b[0], a[1] + b[1], a[2] + b[2]) == (5.0, 500.0, 9.0)


def test_slice_history_sums_multiple_instances():
    from condor.fetchers.bot_performance import slice_history

    h1 = [(10.0, 2.0, 20.0, 1.0)]  # instance 1: +2 at t=10
    h2 = [(15.0, 3.0, 30.0, 2.0)]  # instance 2: +3 at t=15
    # window covering both
    assert slice_history([h1, h2], 0, 100) == (5.0, 50.0, 3.0)
    # window covering only instance 1
    assert slice_history([h1, h2], 0, 12) == (2.0, 20.0, 1.0)


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
    ts, realized, volume, trades = hist[0]
    assert realized == 1.5  # 1.0 + 0.5
    assert volume == 140.0  # 100 + 40
    assert trades == 3.0  # TAKE_PROFIT 2 + STOP_LOSS 1; EARLY_STOP/FAILED excluded


# ── Provider wiring ──


def test_executors_provider_forwards_bot_name(monkeypatch):
    from condor.agents.providers.executors import ExecutorsProvider

    captured = {}

    async def _fake_fetch(client, agent_id, bot_name=""):
        captured["agent_id"] = agent_id
        captured["bot_name"] = bot_name
        return AgentPerformance(agent_id=agent_id, bot_name=bot_name)

    monkeypatch.setattr(
        "condor.agents.performance.fetch_agent_performance", _fake_fetch
    )

    provider = ExecutorsProvider()
    asyncio.run(
        provider.execute(
            client=object(),
            config={"bot_name": "river"},
            agent_id="river.scalp_1",
        )
    )
    assert captured == {"agent_id": "river.scalp_1", "bot_name": "river"}


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
