"""Tests for FEAT-005 controller mode: bot-by-name perf aggregation + merge.

Covers the reusable bot-performance fetcher, the disjoint merge into
``AgentPerformance``, the wiring through the provider, and the prompt's
controller-mode block.
"""

import asyncio
from types import SimpleNamespace

from condor.agents.performance import (
    AgentPerformance,
    fetch_agent_performance,
    fetch_agent_performance_batch,
)
from condor.fetchers.bot_performance import (
    _aggregate_by_bot,
    extract_snapshots,
    fetch_all_bot_performance,
    fetch_bot_performance,
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
