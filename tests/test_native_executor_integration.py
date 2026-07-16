"""M1 integration tests: native executors through risk gate, provider, and REST.

Covers the seams added in docs/condor-simple.md M1: risk_gate's
native-shape branch (RiskDeclaration, fail-closed), agent attribution in
the store, the native_executors provider, and the /executors routes.
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import condor.executors.service as service
from condor.agents.risk import RiskEngine, RiskLimits, RiskState, risk_gate
from condor.executors.base import ExecutorStatus
from condor.executors.hyperliquid import Position
from condor.executors.order import OrderExecutor, OrderPerpConfig, OrderStates
from condor.executors.position import PositionSpotConfig, PositionExecutor, PositionStates
from condor.executors.log import ExecutorLog
from condor.web.routes import native_executors

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
_OPTIONS = [{"kind": "allow_once", "optionId": "allow"}]


def _native_pos_create(amount_quote: float = 100.0) -> dict:
    return {
        "tool": "mcp__condor__manage_executors",
        "input": {
            "action": "create",
            "executor_type": "position_spot",
            "config": {
                "chain_network": "solana-mainnet-beta",
                "wallet_address": WALLET,
                "base_token": "Mint111",
                "quote_token": "USDC",
                "amount_quote": str(amount_quote),
            },
        },
    }


def pos_config(**over):
    kw = dict(chain_network="solana-mainnet-beta", wallet_address=WALLET,
              base_token="Mint111", quote_token="USDC", amount_quote=Decimal("1"),
              update_interval=0.01)
    kw.update(over)
    return PositionSpotConfig(**kw)


# -- risk gate: native shape ------------------------------------------------------


def test_native_create_within_limit_allowed():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500))
    state = RiskState()
    allowed, reason = engine.check_executor_action(_native_pos_create(100), state)
    assert allowed, reason
    assert state.total_exposure == pytest.approx(100.0)
    assert state.executor_count == 1


def test_native_create_over_limit_blocked():
    engine = RiskEngine(RiskLimits(max_position_size_quote=50))
    allowed, reason = engine.check_executor_action(_native_pos_create(100), RiskState())
    assert not allowed
    assert "exceed position limit" in reason


def test_native_negative_notional_fails_closed():
    """Malformed negative notionals must not lower the accumulated exposure and
    create additional headroom under the risk cap."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=50))
    state = RiskState(total_exposure=40)

    allowed, reason = engine.check_executor_action(_native_pos_create(-25), state)

    assert not allowed
    assert "positive" in reason.lower()
    assert state.total_exposure == pytest.approx(40)
    assert state.executor_count == 0


def test_native_unknown_type_fails_closed():
    engine = RiskEngine(RiskLimits())
    call = {"tool": "manage_executors",
            "input": {"action": "create", "executor_type": "nope", "config": {}}}
    allowed, reason = engine.check_executor_action(call, RiskState())
    assert not allowed
    assert "Cannot compute risk" in reason


def test_native_swap_without_notional_fails_closed():
    engine = RiskEngine(RiskLimits())
    call = {
        "tool": "manage_executors",
        "input": {
            "action": "create",
            "executor_type": "order_spot",
            "config": {
                "chain_network": "solana-mainnet-beta", "wallet_address": WALLET,
                "base_token": "SOL", "quote_token": "USDC",
                "amount": "0.5", "side": "SELL",
            },
        },
    }
    allowed, reason = engine.check_executor_action(call, RiskState())
    assert not allowed
    assert "notional_quote" in reason


def test_risk_gate_native_create_not_blocked_by_controller_id():
    """The hummingbot-only controller_id requirement must not block native creates."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=500))

    async def run():
        cb = risk_gate(engine, RiskState())
        return await cb(_native_pos_create(100), _OPTIONS)

    result = asyncio.run(run())
    assert result["outcome"]["outcome"] == "selected"


def test_risk_gate_experiment_blocks_native_create():
    engine = RiskEngine(RiskLimits())

    async def run():
        cb = risk_gate(engine, RiskState(), experiment=True)
        return await cb(_native_pos_create(1), _OPTIONS)

    result = asyncio.run(run())
    assert result["outcome"]["outcome"] == "cancelled"


# -- store: agent attribution + migration ----------------------------------------


def test_store_agent_id_filter(tmp_path):
    store = ExecutorLog(tmp_path)
    gw = SimpleNamespace()
    a = PositionExecutor("pos_a", pos_config(agent_slug="agent_x", agent_id="agent_x_1"), gw, store)
    b = PositionExecutor("pos_b", pos_config(agent_slug="agent_y", agent_id="agent_y_1"), gw, store)
    store.save(a)
    store.save(b)
    mine = store.load_by_agent("agent_x_1")
    assert [r.id for r in mine] == ["pos_a"]
    assert mine[0].agent_id == "agent_x_1"


def _seed_store(tmp_path) -> ExecutorLog:
    store = ExecutorLog(tmp_path)
    gw = SimpleNamespace()

    open_ex = PositionExecutor("pos_open", pos_config(agent_slug="agent_x", agent_id="agent_x_1"), gw, store)
    open_ex.status = ExecutorStatus.ACTIVE
    open_ex.state.state = PositionStates.ACTIVE
    open_ex.state.size = Decimal("0.01")
    open_ex.state.amount_spent = Decimal("1")
    open_ex.state.entry_price = Decimal("100")
    open_ex.state.mark_price = Decimal("110")
    store.save(open_ex)

    closed = PositionExecutor("pos_closed", pos_config(agent_slug="agent_x", agent_id="agent_x_1"), gw, store)
    closed.status = ExecutorStatus.CLOSED
    closed.state.state = PositionStates.COMPLETE
    closed.state.amount_spent = Decimal("1")
    closed.state.proceeds = Decimal("1.1")
    store.save(closed)
    return store


def test_native_provider_reports_exposure_and_unrealized(tmp_path, monkeypatch):
    from condor.agents.providers.native_executors import NativeExecutorsProvider

    store = _seed_store(tmp_path)
    monkeypatch.setattr(
        service, "_runtime", SimpleNamespace(store=store, gateway=SimpleNamespace())
    )

    result = asyncio.run(NativeExecutorsProvider().execute({}, agent_id="agent_x_1", agent_slug="agent_x"))
    assert result.data["open_count"] == 1
    # open notional at cost basis: quote_spent = 1
    assert result.data["total_exposure"] == pytest.approx(1.0)
    # unrealized: base_bought * last_price - quote_spent = 0.01*110 - 1 = 0.1
    assert result.data["unrealized_pnl"] == pytest.approx(0.1)
    assert result.data["closed_count"] == 1
    assert "pos_open" in result.summary
    assert "uPnL +0.1000" in result.summary


def test_native_provider_reads_live_pnl_after_deduped_poll(tmp_path, monkeypatch):
    """Volatile marks are intentionally excluded from the append-only recovery
    key, but the risk provider must still report the live executor's latest PnL."""
    from condor.agents.providers.native_executors import NativeExecutorsProvider

    store = ExecutorLog(tmp_path)
    ex = PositionExecutor(
        "pos_live_mark",
        pos_config(agent_slug="agent_x", agent_id="agent_x_1"),
        SimpleNamespace(),
        store,
    )
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = PositionStates.ACTIVE
    ex.state.size = Decimal("0.01")
    ex.state.amount_spent = Decimal("1")
    ex.state.entry_price = Decimal("100")
    ex.persist()

    # A later barrier poll updates volatile state. persist() deliberately dedups
    # this snapshot, so a provider that only reloads the log sees a stale mark.
    ex.state.mark_price = Decimal("110")
    ex.persist()
    monkeypatch.setattr(
        service,
        "_runtime",
        SimpleNamespace(store=store, _executors={ex.id: ex}),
    )

    result = asyncio.run(
        NativeExecutorsProvider().execute({}, agent_id="agent_x_1", agent_slug="agent_x")
    )
    assert result.data["unrealized_pnl"] == pytest.approx(0.1)


def test_filled_order_perp_inventory_remains_risk_exposure(tmp_path, monkeypatch):
    """A terminal single-leg perp order can leave a live net venue position.
    That inventory must remain in aggregate exposure after the executor closes."""
    from condor.agents.providers.native_executors import NativeExecutorsProvider

    class _LivePerp:
        async def position(self, coin):
            return Position(
                coin=coin,
                side="LONG",
                size=Decimal("0.2"),
                entry_px=Decimal("100"),
                unrealized_pnl=Decimal("0"),
                liquidation_px=Decimal("50"),
                margin_used=Decimal("10"),
                leverage=2,
                funding=Decimal("0"),
            )

    store = ExecutorLog(tmp_path)
    ex = OrderExecutor(
        "filled_bid",
        OrderPerpConfig(
            coin="ETH", side="LONG", notional_quote=Decimal("20"), leverage=2,
            agent_slug="perp_market_maker", agent_id="perp_market_maker_1",
        ),
        _LivePerp(),
        store,
    )
    ex.status = ExecutorStatus.CLOSED
    ex.state.state = OrderStates.DONE
    ex.state.size = Decimal("0.2")
    ex.state.entry_price = Decimal("100")
    store.save(ex)
    monkeypatch.setattr(
        service,
        "_runtime",
        SimpleNamespace(
            store=store,
            _executors={ex.id: ex},
            _hl_clients={"perp": ex.connector},
        ),
    )

    result = asyncio.run(
        NativeExecutorsProvider().execute(
            {}, agent_id="perp_market_maker_1", agent_slug="perp_market_maker"
        )
    )
    assert result.data["total_exposure"] == pytest.approx(20.0)


def test_native_provider_registered_as_core():
    from condor.agents.providers import list_core_providers

    names = {p.name for p in list_core_providers()}
    assert "native_executors" in names


# -- REST routes ---------------------------------------------------------------------


class _QuoteOnlyGateway:
    async def quote_swap(self, **kw):
        return {"price": 100.0}


def _make_runtime(tmp_path, monkeypatch):
    from condor.executors.leases import LeaseManager

    store = ExecutorLog(tmp_path)
    gateway = _QuoteOnlyGateway()

    def fake_create(config, executor_id=None):
        return executor_id or "fake_id_1"

    return SimpleNamespace(
        store=store,
        gateway=gateway,
        leases=LeaseManager(),
        connector_for_spec=lambda type_, venue: gateway,
        create_executor=fake_create,
        list_running=lambda: [],
        stop_executor=lambda eid, keep_position=True: None,
    )


def _make_client(tmp_path, monkeypatch) -> TestClient:
    store = ExecutorLog(tmp_path)
    created = {}

    def fake_create(config):
        created["config"] = config
        return "fake_id_1"

    gateway = _QuoteOnlyGateway()
    runtime = SimpleNamespace(
        store=store,
        gateway=gateway,
        connector_for_spec=lambda type_, venue: gateway,
        create_executor=fake_create,
        list_running=lambda: [],
        stop_executor=lambda eid, keep_position=True: None,
    )
    monkeypatch.setattr(service, "_runtime", runtime)

    app = FastAPI()
    app.include_router(native_executors.router)
    return TestClient(app)


def test_ops_create_swap_fills_notional_from_quote(tmp_path, monkeypatch):
    """ops.create (the only create path — the dashboard raw-create route is
    reserved, §6.4) fills the declared notional from a live quote."""
    import asyncio

    from condor.executors import ops, service
    from condor.executors.capabilities import get_capability_registry

    runtime = _make_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_runtime", runtime, raising=False)
    cap = get_capability_registry().mint_run_capability("mm", "mm_1")

    result = asyncio.run(ops.create(
        runtime,
        type="order_spot",
        config={
            "chain_network": "solana-mainnet-beta", "wallet_address": WALLET,
            "base_token": "SOL", "quote_token": "USDC",
            "amount": "0.5", "side": "SELL",
        },
        capability=cap.id,
    ))
    # 0.5 SOL @ quoted 100 -> 50 quote notional
    assert result["risk_declaration"]["max_notional_quote"] == pytest.approx(50.0)


def test_ops_create_unknown_type_422(tmp_path, monkeypatch):
    import asyncio

    from condor.executors import ops, service

    runtime = _make_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_runtime", runtime, raising=False)
    with pytest.raises(ops.ExecutorOpError) as ei:
        asyncio.run(ops.create(runtime, type="nope", config={}, capability="x"))
    assert ei.value.status == 422


def test_rest_stop_unknown_404(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post("/executors/missing_1/stop", json={})
    assert resp.status_code == 404
