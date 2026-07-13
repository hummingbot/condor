"""Tests for the performance rollup: attribution columns + aggregation."""

import asyncio
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest

from condor.executors.base import ExecutorStatus
from condor.executors.lp import LpConfig, LpExecutor, LpStates
from condor.executors.performance import GROUP_KEYS, aggregate_performance
from condor.executors.position import PositionConfig, PositionExecutor, PositionStates
from condor.executors.store import ExecutorStore, _slug_from_run_id

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
GW = SimpleNamespace()


def lp(store, eid, *, agent_slug="", agent_id="", strategy="", connector="meteora",
       status=ExecutorStatus.CLOSED, pnl_bp=0):
    ex = LpExecutor(eid, LpConfig(
        chain_network="solana-mainnet-beta", wallet_address=WALLET,
        connector=connector, pool_address="Pool1", trading_pair="SOL-USDC",
        lower_price=Decimal("98"), upper_price=Decimal("102"),
        agent_slug=agent_slug, agent_id=agent_id, strategy=strategy,
    ), GW, store)
    ex.status = status
    s = ex.state
    s.add_mid_price = Decimal("100")
    s.initial_base_amount = Decimal("0.01")
    s.initial_quote_amount = Decimal("1")
    # final value differs by pnl_bp basis points on the 2.0 initial
    s.base_amount = Decimal("0.01")
    s.quote_amount = Decimal("1") + Decimal(pnl_bp) / 10000 * 2
    s.position_rent = Decimal("0.001")
    s.position_rent_refunded = Decimal("0.001")
    if status == ExecutorStatus.CLOSED:
        s.state = LpStates.COMPLETE
    store.save(ex)
    return ex


def position(store, eid, *, agent_slug="", agent_id="", strategy="",
             returned="0.021", close_type="take_profit"):
    ex = PositionExecutor(eid, PositionConfig(
        chain_network="solana-mainnet-beta", wallet_address=WALLET,
        base_token="Mint1", quote_token="SOL", amount_quote=Decimal("0.02"),
        agent_slug=agent_slug, agent_id=agent_id, strategy=strategy,
    ), GW, store)
    ex.status = ExecutorStatus.CLOSED
    s = ex.state
    s.state = PositionStates.COMPLETE
    s.quote_spent = Decimal("0.02")
    s.quote_returned = Decimal(returned)
    s.close_type = close_type
    store.save(ex)
    return ex


def test_slug_derivation():
    assert _slug_from_run_id("lp_rebalancer_2") == "lp_rebalancer"
    assert _slug_from_run_id("memecoin_trender-d2") == "memecoin_trender"
    assert _slug_from_run_id("mm_expert_e3") == "mm_expert"
    assert _slug_from_run_id("bare_slug") == "bare_slug"


def test_store_persists_attribution_trio(tmp_path):
    store = ExecutorStore(tmp_path / "t.db")
    lp(store, "lp_1", agent_slug="lp_rebalancer", agent_id="lp_rebalancer_2",
       strategy="lp_rebalance")
    rec = store.load("lp_1")
    assert (rec.agent_slug, rec.agent_id, rec.strategy) == (
        "lp_rebalancer", "lp_rebalancer_2", "lp_rebalance")


def test_store_derives_slug_when_missing(tmp_path):
    store = ExecutorStore(tmp_path / "t.db")
    lp(store, "lp_1", agent_id="lp_rebalancer-d1")  # no slug given
    assert store.load("lp_1").agent_slug == "lp_rebalancer"
    assert store.load_by_slug("lp_rebalancer")[0].id == "lp_1"


def test_migration_backfills_slug(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE executors (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, status TEXT NOT NULL,
            agent_id TEXT NOT NULL DEFAULT '', config TEXT NOT NULL,
            state TEXT NOT NULL, close_reason TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, heartbeat_at REAL NOT NULL
        )"""
    )
    conn.execute(
        "INSERT INTO executors VALUES ('old_1','lp','CLOSED','lp_rebalancer_2','{}','{}','x',1,1,1)"
    )
    conn.commit()
    conn.close()

    store = ExecutorStore(db)
    rec = store.load("old_1")
    assert rec.agent_slug == "lp_rebalancer"
    assert rec.strategy == ""  # unknowable after the fact


def _seeded(tmp_path) -> ExecutorStore:
    store = ExecutorStore(tmp_path / "p.db")
    # lp_rebalancer: session 1 (strategy lp_rebalance) win + loss; delegation flat
    lp(store, "lp_a", agent_slug="lp_rebalancer", agent_id="lp_rebalancer_1",
       strategy="lp_rebalance", pnl_bp=50)
    lp(store, "lp_b", agent_slug="lp_rebalancer", agent_id="lp_rebalancer_1",
       strategy="lp_rebalance", pnl_bp=-20)
    lp(store, "lp_c", agent_slug="lp_rebalancer", agent_id="lp_rebalancer-d1",
       connector="orca", pnl_bp=0)
    # memecoin_trender: one TP win via session strategy trend_position
    position(store, "pos_a", agent_slug="memecoin_trender",
             agent_id="memecoin_trender_1", strategy="trend_position")
    # open LP, unattributed
    lp(store, "lp_open", status=ExecutorStatus.ACTIVE)
    return store


def test_aggregate_by_agent(tmp_path):
    rows = aggregate_performance(_seeded(tmp_path), group_by="agent")
    by_key = {r["key"]: r for r in rows}
    lp_agent = by_key["lp_rebalancer"]
    assert lp_agent["closed_count"] == 3
    # +0.01 (50bp on 2.0) - 0.004 + 0 = +0.006
    assert lp_agent["realized_pnl_quote"] == pytest.approx(0.006)
    assert lp_agent["win_rate"] == pytest.approx(0.5)
    trender = by_key["memecoin_trender"]
    assert trender["realized_pnl_quote"] == pytest.approx(0.001)
    assert trender["close_types"] == {"take_profit": 1}
    assert by_key["(manual)"]["open_count"] == 1


def test_aggregate_by_strategy(tmp_path):
    rows = aggregate_performance(_seeded(tmp_path), group_by="strategy")
    by_key = {r["key"]: r for r in rows}
    assert by_key["lp_rebalance"]["closed_count"] == 2
    assert by_key["trend_position"]["closed_count"] == 1
    # delegation + manual rows carry no playbook
    assert by_key["(none)"]["closed_count"] == 1
    assert by_key["(none)"]["open_count"] == 1


def test_aggregate_by_run_and_venue(tmp_path):
    store = _seeded(tmp_path)
    runs = {r["key"]: r for r in aggregate_performance(store, group_by="run")}
    assert runs["lp_rebalancer_1"]["closed_count"] == 2
    assert runs["lp_rebalancer-d1"]["closed_count"] == 1
    venues = {r["key"]: r for r in aggregate_performance(store, group_by="venue")}
    assert venues["orca"]["closed_count"] == 1
    assert venues["meteora"]["closed_count"] == 2  # lp_a + lp_b (lp_open is open)
    assert venues["jupiter"]["closed_count"] == 1  # the position (no connector)


def test_aggregate_filter_and_bad_key(tmp_path):
    store = _seeded(tmp_path)
    rows = aggregate_performance(store, group_by="agent", agent_slug="memecoin_trender")
    assert len(rows) == 1 and rows[0]["key"] == "memecoin_trender"
    with pytest.raises(ValueError, match="group_by"):
        aggregate_performance(store, group_by="nope")
    assert set(GROUP_KEYS) == {"agent", "run", "strategy", "venue", "type"}


def test_detached_position_not_counted_as_realized(tmp_path):
    store = ExecutorStore(tmp_path / "t.db")
    position(store, "pos_det", agent_slug="memecoin_trender",
             returned="0.021", close_type="detached")
    # simulate detach: no quote_returned
    ex = PositionExecutor("pos_det2", PositionConfig(
        chain_network="solana-mainnet-beta", wallet_address=WALLET,
        base_token="Mint1", quote_token="SOL", amount_quote=Decimal("0.02"),
        agent_slug="memecoin_trender",
    ), GW, store)
    ex.status = ExecutorStatus.CLOSED
    ex.state.state = PositionStates.COMPLETE
    ex.state.quote_spent = Decimal("0.02")
    ex.state.close_type = "detached"
    store.save(ex)

    rows = aggregate_performance(store, group_by="agent")
    g = rows[0]
    assert g["closed_count"] == 2
    # pos_det realized +0.001; pos_det2 detached -> 0 contribution
    assert g["realized_pnl_quote"] == pytest.approx(0.001)
