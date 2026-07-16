"""Tests for the performance rollup: attribution columns + aggregation."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from condor.executors.base import ExecutorStatus
from condor.executors.performance import GROUP_KEYS, aggregate_performance
from condor.executors.position import PositionSpotConfig, PositionExecutor, PositionStates
from condor.executors.log import ExecutorLog

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
GW = SimpleNamespace()


def position(store, eid, *, agent_slug="", agent_id="", strategy="", connector=None,
             status=ExecutorStatus.CLOSED, spent="0.02", returned="0.021",
             close_type="take_profit"):
    # ``connector`` is retained as a caller convenience but position_spot has no
    # such field — venue grouping keys on config.venue ("solana").
    ex = PositionExecutor(eid, PositionSpotConfig(
        chain_network="solana-mainnet-beta", wallet_address=WALLET,
        base_token="Mint1", quote_token="SOL", amount_quote=Decimal(spent),
        agent_slug=agent_slug, agent_id=agent_id, strategy=strategy,
    ), GW, store)
    ex.status = status
    s = ex.state
    s.amount_spent = Decimal(spent)
    if status == ExecutorStatus.CLOSED:
        s.state = PositionStates.COMPLETE
        if returned is not None:
            s.proceeds = Decimal(returned)
        s.close_type = close_type
    else:
        s.state = PositionStates.ACTIVE
        s.size = Decimal("1")
    store.save(ex)
    return ex


def _perp_closed(store, eid, realized="0.5"):
    from condor.executors.position import PositionPerpConfig

    ex = PositionExecutor(eid, PositionPerpConfig(
        wallet_address=WALLET, coin="ETH", side="LONG", notional_quote=Decimal("40"),
        agent_slug="perp_market_maker", agent_id="perp_market_maker_1",
    ), GW, store)
    ex.status = ExecutorStatus.CLOSED
    s = ex.state
    s.state = PositionStates.COMPLETE
    s.close_type = "take_profit"
    s.extra["realized_pnl"] = realized  # net of fees, from settle()
    s.extra["close_fee"] = "0.01"
    store.save(ex)
    return ex


def _pred_closed(store, eid, spent="10", proceeds="12"):
    from condor.executors.position import PositionPredConfig

    ex = PositionExecutor(eid, PositionPredConfig(
        venue="polymarket", wallet_address=WALLET, market="tok", position="SHORT",
        amount_quote=Decimal(spent), agent_slug="world_cup", agent_id="world_cup_1",
    ), GW, store)
    ex.status = ExecutorStatus.CLOSED
    s = ex.state
    s.state = PositionStates.COMPLETE
    s.close_type = "resolved_win"
    s.amount_spent = Decimal(spent)
    s.proceeds = Decimal(proceeds)
    store.save(ex)
    return ex


def test_perp_and_pred_realized_pnl_in_rollup(tmp_path):
    """#6: perp realized (from settle) and pred proceeds-vs-stake must fold into
    the rollup as realized PnL and count as wins — not silently drop to zero."""
    store = ExecutorLog(tmp_path)
    _perp_closed(store, "perp_1", realized="0.5")
    _pred_closed(store, "pred_1", spent="10", proceeds="12")
    rows = {r["key"]: r for r in aggregate_performance(store, group_by="agent")}
    assert rows["perp_market_maker"]["realized_pnl_quote"] == pytest.approx(0.5)
    assert rows["perp_market_maker"]["win_rate"] == pytest.approx(1.0)
    assert rows["world_cup"]["realized_pnl_quote"] == pytest.approx(2.0)  # 12 - 10
    assert rows["world_cup"]["win_rate"] == pytest.approx(1.0)




def test_store_persists_attribution_trio(tmp_path):
    store = ExecutorLog(tmp_path)
    position(store, "pos_1", agent_slug="range_trader", agent_id="range_trader_2",
             strategy="range_rebalance")
    rec = store.load("pos_1")
    assert (rec.agent_slug, rec.agent_id, rec.strategy) == (
        "range_trader", "range_trader_2", "range_rebalance")


def test_store_rejects_run_id_without_slug(tmp_path):
    # Run ids are opaque ULIDs (§7.1): nothing derives a slug, so an
    # attributed executor must carry agent_slug explicitly.
    store = ExecutorLog(tmp_path)
    with pytest.raises(ValueError, match="without agent_slug"):
        position(store, "pos_1", agent_id="01JZX5B7Q2K4N8P1T3V5W7Y9ZB")


def _seeded(tmp_path) -> ExecutorLog:
    store = ExecutorLog(tmp_path)
    # range_trader: session 1 (strategy range_rebalance) win + loss; delegation flat
    position(store, "pos_a", agent_slug="range_trader", agent_id="range_trader_1",
             strategy="range_rebalance", connector="meteora", spent="1", returned="1.01")
    position(store, "pos_b", agent_slug="range_trader", agent_id="range_trader_1",
             strategy="range_rebalance", connector="meteora", spent="1", returned="0.996")
    position(store, "pos_c", agent_slug="range_trader", agent_id="range_trader-d1",
             connector="orca", spent="1", returned="1")
    # memecoin_trender: one TP win via session strategy trend_position
    position(store, "mc_a", agent_slug="memecoin_trender",
             agent_id="memecoin_trender_1", strategy="trend_position")
    # open position, unattributed
    position(store, "pos_open", connector="meteora", status=ExecutorStatus.ACTIVE)
    return store


def test_aggregate_by_agent(tmp_path):
    rows = aggregate_performance(_seeded(tmp_path), group_by="agent")
    by_key = {r["key"]: r for r in rows}
    lp_agent = by_key["range_trader"]
    assert lp_agent["closed_count"] == 3
    # +0.01 - 0.004 + 0 = +0.006
    assert lp_agent["realized_pnl_quote"] == pytest.approx(0.006)
    assert lp_agent["win_rate"] == pytest.approx(0.5)
    trender = by_key["memecoin_trender"]
    assert trender["realized_pnl_quote"] == pytest.approx(0.001)
    assert trender["close_types"] == {"take_profit": 1}
    assert by_key["(manual)"]["open_count"] == 1


def test_aggregate_by_strategy(tmp_path):
    rows = aggregate_performance(_seeded(tmp_path), group_by="strategy")
    by_key = {r["key"]: r for r in rows}
    assert by_key["range_rebalance"]["closed_count"] == 2
    assert by_key["trend_position"]["closed_count"] == 1
    # delegation + manual rows carry no playbook
    assert by_key["(none)"]["closed_count"] == 1
    assert by_key["(none)"]["open_count"] == 1


def test_aggregate_by_run_and_venue(tmp_path):
    store = _seeded(tmp_path)
    runs = {r["key"]: r for r in aggregate_performance(store, group_by="run")}
    assert runs["range_trader_1"]["closed_count"] == 2
    assert runs["range_trader-d1"]["closed_count"] == 1
    # position_spot venue is "solana" (config.venue); pos_open is still open.
    venues = {r["key"]: r for r in aggregate_performance(store, group_by="venue")}
    assert venues["solana"]["closed_count"] == 4  # pos_a + pos_b + pos_c + mc_a
    assert venues["solana"]["open_count"] == 1     # pos_open


def test_aggregate_filter_and_bad_key(tmp_path):
    store = _seeded(tmp_path)
    rows = aggregate_performance(store, group_by="agent", agent_slug="memecoin_trender")
    assert len(rows) == 1 and rows[0]["key"] == "memecoin_trender"
    with pytest.raises(ValueError, match="group_by"):
        aggregate_performance(store, group_by="nope")
    assert set(GROUP_KEYS) == {"agent", "run", "strategy", "venue", "type"}


def test_detached_position_not_counted_as_realized(tmp_path):
    store = ExecutorLog(tmp_path)
    position(store, "pos_det", agent_slug="memecoin_trender",
             returned="0.021", close_type="detached")
    # simulate detach: no quote_returned
    ex = PositionExecutor("pos_det2", PositionSpotConfig(
        chain_network="solana-mainnet-beta", wallet_address=WALLET,
        base_token="Mint1", quote_token="SOL", amount_quote=Decimal("0.02"),
        agent_slug="memecoin_trender",
    ), GW, store)
    ex.status = ExecutorStatus.CLOSED
    ex.state.state = PositionStates.COMPLETE
    ex.state.amount_spent = Decimal("0.02")
    ex.state.close_type = "detached"
    store.save(ex)

    rows = aggregate_performance(store, group_by="agent")
    g = rows[0]
    assert g["closed_count"] == 2
    # pos_det realized +0.001; pos_det2 detached -> 0 contribution
    assert g["realized_pnl_quote"] == pytest.approx(0.001)
