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
from condor.executors.lp import LpConfig, LpExecutor, LpStates
from condor.executors.store import ExecutorStore
from condor.executors.swap import SwapConfig, SwapExecutor
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import native_executors

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
_OPTIONS = [{"kind": "allow_once", "optionId": "allow"}]


def _native_lp_create(quote_amount: float = 100.0, base_amount: float = 0.0) -> dict:
    return {
        "tool": "mcp__condor__manage_executors",
        "input": {
            "action": "create",
            "executor_type": "lp",
            "config": {
                "chain_network": "solana-mainnet-beta",
                "wallet_address": WALLET,
                "connector": "raydium",
                "pool_address": "Pool111",
                "trading_pair": "SOL-USDC",
                "lower_price": "98",
                "upper_price": "102",
                "base_amount": str(base_amount),
                "quote_amount": str(quote_amount),
            },
        },
    }


def lp_config(**over):
    kw = dict(chain_network="solana-mainnet-beta", wallet_address=WALLET,
              connector="raydium", pool_address="Pool111", trading_pair="SOL-USDC",
              lower_price=Decimal("98"), upper_price=Decimal("102"),
              quote_amount=Decimal("1"), update_interval=0.01)
    kw.update(over)
    return LpConfig(**kw)


# -- risk gate: native shape ------------------------------------------------------


def test_native_create_within_limit_allowed():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500))
    state = RiskState()
    allowed, reason = engine.check_executor_action(_native_lp_create(100), state)
    assert allowed, reason
    assert state.total_exposure == pytest.approx(100.0)
    assert state.executor_count == 1


def test_native_create_over_limit_blocked():
    engine = RiskEngine(RiskLimits(max_position_size_quote=50))
    allowed, reason = engine.check_executor_action(_native_lp_create(100), RiskState())
    assert not allowed
    assert "exceed position limit" in reason


def test_native_lp_base_amount_counts_at_mid():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500))
    state = RiskState()
    # base 1 @ mid 100 + quote 100 = 200 notional
    allowed, _ = engine.check_executor_action(
        _native_lp_create(quote_amount=100, base_amount=1), state
    )
    assert allowed
    assert state.total_exposure == pytest.approx(200.0)


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
            "executor_type": "swap",
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
        return await cb(_native_lp_create(100), _OPTIONS)

    result = asyncio.run(run())
    assert result["outcome"]["outcome"] == "selected"


def test_risk_gate_experiment_blocks_native_create():
    engine = RiskEngine(RiskLimits())

    async def run():
        cb = risk_gate(engine, RiskState(), experiment=True)
        return await cb(_native_lp_create(1), _OPTIONS)

    result = asyncio.run(run())
    assert result["outcome"]["outcome"] == "cancelled"


# -- store: agent attribution + migration ----------------------------------------


def test_store_agent_id_filter(tmp_path):
    store = ExecutorStore(tmp_path / "t.db")
    gw = SimpleNamespace()
    a = LpExecutor("lp_a", lp_config(agent_id="agent_x_1"), gw, store)
    b = LpExecutor("lp_b", lp_config(agent_id="agent_y_1"), gw, store)
    store.save(a)
    store.save(b)
    mine = store.load_by_agent("agent_x_1")
    assert [r.id for r in mine] == ["lp_a"]
    assert mine[0].agent_id == "agent_x_1"


def test_store_migrates_pre_m1_schema(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE executors (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, status TEXT NOT NULL,
            config TEXT NOT NULL, state TEXT NOT NULL, close_reason TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, heartbeat_at REAL NOT NULL
        )"""
    )
    conn.execute(
        "INSERT INTO executors VALUES ('old_1', 'swap', 'CLOSED', '{}', '{}', 'done', 1, 1, 1)"
    )
    conn.commit()
    conn.close()

    store = ExecutorStore(db)
    rec = store.load("old_1")
    assert rec.agent_id == ""
    assert rec.status == "CLOSED"


# -- provider ----------------------------------------------------------------------


def _seed_store(tmp_path) -> ExecutorStore:
    store = ExecutorStore(tmp_path / "p.db")
    gw = SimpleNamespace()

    open_ex = LpExecutor("lp_open", lp_config(agent_id="agent_x_1"), gw, store)
    open_ex.status = ExecutorStatus.ACTIVE
    open_ex.state.state = LpStates.IN_RANGE
    open_ex.state.position_address = "Pos1"
    open_ex.state.base_amount = Decimal("0.01")
    open_ex.state.quote_amount = Decimal("1")
    open_ex.state.add_mid_price = Decimal("100")
    store.save(open_ex)

    closed = LpExecutor("lp_closed", lp_config(agent_id="agent_x_1"), gw, store)
    closed.status = ExecutorStatus.CLOSED
    closed.state.state = LpStates.COMPLETE
    closed.state.add_mid_price = Decimal("100")
    closed.state.initial_base_amount = Decimal("0.01")
    closed.state.initial_quote_amount = Decimal("1")
    closed.state.base_amount = Decimal("0.011")
    closed.state.quote_amount = Decimal("0.95")
    closed.state.quote_fee = Decimal("0.05")
    store.save(closed)
    return store


def test_native_provider_reports_exposure_and_realized(tmp_path, monkeypatch):
    from condor.agents.providers.native_executors import NativeExecutorsProvider

    store = _seed_store(tmp_path)
    monkeypatch.setattr(service, "_runtime", SimpleNamespace(store=store))

    result = asyncio.run(NativeExecutorsProvider().execute(None, {}, agent_id="agent_x_1"))
    assert result.data["open_count"] == 1
    # open notional: 0.01 * 100 + 1 = 2
    assert result.data["total_exposure"] == pytest.approx(2.0)
    # realized: (0.011*100 + 0.95 + 0.05) - (0.01*100 + 1) = 0.1
    assert result.data["realized_pnl"] == pytest.approx(0.1)
    assert "lp_open" in result.summary


def test_native_provider_registered_as_core():
    from condor.agents.providers import list_core_providers

    names = {p.name for p in list_core_providers()}
    assert "native_executors" in names


# -- REST routes ---------------------------------------------------------------------


class _QuoteOnlyGateway:
    async def quote_swap(self, **kw):
        return {"price": 100.0}


def _make_client(tmp_path, monkeypatch) -> TestClient:
    store = ExecutorStore(tmp_path / "r.db")
    created = {}

    def fake_create(config):
        created["config"] = config
        return "fake_id_1"

    runtime = SimpleNamespace(
        store=store,
        gateway=_QuoteOnlyGateway(),
        create_executor=fake_create,
        list_running=lambda: [],
        stop_executor=lambda eid, keep_position=True: None,
    )
    monkeypatch.setattr(service, "_runtime", runtime)

    app = FastAPI()
    app.include_router(native_executors.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(id=1, role="admin")
    return TestClient(app)


def test_rest_create_lp(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post("/executors", json={
        "type": "lp",
        "agent_id": "agent_x_1",
        "config": {
            "chain_network": "solana-mainnet-beta", "wallet_address": WALLET,
            "connector": "raydium", "pool_address": "Pool111",
            "trading_pair": "SOL-USDC", "lower_price": "98", "upper_price": "102",
            "quote_amount": "1",
        },
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "fake_id_1"
    assert body["risk_declaration"]["max_notional_quote"] == pytest.approx(1.0)


def test_rest_create_swap_fills_notional_from_quote(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post("/executors", json={
        "type": "swap",
        "config": {
            "chain_network": "solana-mainnet-beta", "wallet_address": WALLET,
            "base_token": "SOL", "quote_token": "USDC",
            "amount": "0.5", "side": "SELL",
        },
    })
    assert resp.status_code == 200, resp.text
    # 0.5 SOL @ quoted 100 -> 50 quote notional
    assert resp.json()["risk_declaration"]["max_notional_quote"] == pytest.approx(50.0)


def test_rest_create_unknown_type_422(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post("/executors", json={"type": "nope", "config": {}})
    assert resp.status_code == 422


def test_rest_stop_unknown_404(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post("/executors/missing_1/stop", json={})
    assert resp.status_code == 404
