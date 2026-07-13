"""Unit tests for condor.executors — runtime, store, swap, LP, ranges.

Gateway is faked; live-path validation happens in the M0 live tests
(docs/condor-simple.md §7).
"""

import asyncio
from decimal import Decimal

import pytest

from condor.executors.base import ExecutorStatus
from condor.executors.gateway import GatewayError
from condor.executors.lp import LpConfig, LpExecutor, LpStates
from condor.executors.ranges import RangePolicy, Side, calculate_amounts, calculate_price_bounds, plan_position
from condor.executors.runtime import ExecutorRuntime
from condor.executors.store import ExecutorStore
from condor.executors.swap import SwapConfig, SwapExecutor

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
POOL = "PoolAddr111"
POSITION = "PosAddr111"


class FakeGateway:
    """In-memory gateway double: one pool, price scripted per call."""

    def __init__(self, prices=None):
        self.prices = list(prices or [100.0])
        self.position_open = False
        self.calls = []

    def _price(self):
        return self.prices.pop(0) if len(self.prices) > 1 else self.prices[0]

    async def close(self):
        pass

    async def quote_swap(self, **kw):
        self.calls.append(("quote_swap", kw))
        return {"price": self._price(), "amountOut": kw["amount"] * 100}

    async def execute_swap(self, **kw):
        self.calls.append(("execute_swap", kw))
        return {"signature": "sig-swap", "status": 1,
                "data": {"amountIn": kw["amount"], "amountOut": kw["amount"] * 100, "fee": 0.0001}}

    async def poll_tx(self, chain, network, signature):
        self.calls.append(("poll_tx", signature))
        return {"txStatus": 1, "signature": signature}

    async def clmm_pool_info(self, connector, chain_network, pool_address):
        return {"price": self._price(), "address": pool_address}

    async def clmm_open(self, **kw):
        self.calls.append(("clmm_open", kw))
        self.position_open = True
        # NB: real connectors (raydium) report base/quoteTokenAmountAdded as
        # wallet balance CHANGES — negative and including rent. The executor
        # must ignore them and take initials from position-info instead.
        return {"signature": "sig-open", "status": 1,
                "data": {"positionAddress": POSITION, "positionRent": 0.002, "fee": 0.0001,
                         "baseTokenAmountAdded": -(kw.get("base_token_amount") or 0) - 0.02,
                         "quoteTokenAmountAdded": -(kw.get("quote_token_amount") or 0)}}

    async def clmm_position_info(self, connector, chain_network, position_address):
        if not self.position_open:
            raise GatewayError("/trading/clmm/position-info", 404,
                               f"Position closed: {position_address}")
        price = self._price()
        return {"baseTokenAmount": 0.005, "quoteTokenAmount": 0.5,
                "baseFeeAmount": 0.0001, "quoteFeeAmount": 0.01,
                "lowerPrice": 98.0, "upperPrice": 102.0, "price": price}

    async def clmm_close(self, **kw):
        self.calls.append(("clmm_close", kw))
        self.position_open = False
        return {"signature": "sig-close", "status": 1,
                "data": {"positionRentRefunded": 0.002, "fee": 0.0001,
                         "baseTokenAmountRemoved": 0.005, "quoteTokenAmountRemoved": 0.5,
                         "baseFeeAmountCollected": 0.0001, "quoteFeeAmountCollected": 0.01}}

    async def clmm_positions_owned(self, connector, chain_network, wallet_address=None):
        return [{"poolAddress": POOL, "address": POSITION}] if self.position_open else []


def make_runtime(tmp_path, gateway=None):
    store = ExecutorStore(tmp_path / "test.db")
    return ExecutorRuntime(gateway=gateway or FakeGateway(), store=store)


def swap_config(**over):
    kw = dict(chain_network="solana-mainnet-beta", wallet_address=WALLET,
              base_token="SOL", quote_token="USDC", amount=Decimal("0.01"),
              side="SELL", update_interval=0.01)
    kw.update(over)
    return SwapConfig(**kw)


def lp_config(**over):
    kw = dict(chain_network="solana-mainnet-beta", wallet_address=WALLET,
              connector="raydium", pool_address=POOL, trading_pair="SOL-USDC",
              lower_price=Decimal("98"), upper_price=Decimal("102"),
              quote_amount=Decimal("1"), lower_limit_price=Decimal("95"),
              upper_limit_price=Decimal("105"), update_interval=0.01)
    kw.update(over)
    return LpConfig(**kw)


# -- store ---------------------------------------------------------------------


def test_store_roundtrip(tmp_path):
    store = ExecutorStore(tmp_path / "t.db")
    ex = SwapExecutor("swap_1", swap_config(), FakeGateway(), store)
    store.save(ex)
    rec = store.load("swap_1")
    assert rec.status == "PENDING"
    assert rec.config["base_token"] == "SOL"
    assert store.load_non_terminal()[0].id == "swap_1"

    store.mark("swap_1", "CLOSED", "done")
    rec = store.load("swap_1")
    assert rec.status == "CLOSED"
    assert rec.close_reason == "done"
    assert store.load_non_terminal() == []


# -- swap ----------------------------------------------------------------------


def test_swap_happy_path(tmp_path):
    runtime = make_runtime(tmp_path)

    async def run():
        eid = runtime.create_executor(swap_config())
        await runtime.wait_all()
        return eid

    eid = asyncio.run(run())
    rec = runtime.store.load(eid)
    assert rec.status == "CLOSED"
    assert rec.state["signature"] == "sig-swap"
    assert rec.state["phase"] == "done"


def test_swap_reconcile_orphan_submitting(tmp_path):
    """Crash during submission with no signature -> FAILED with clear reason."""
    gateway = FakeGateway()
    store = ExecutorStore(tmp_path / "t.db")
    ex = SwapExecutor("swap_orphan", swap_config(), gateway, store)
    ex.status = ExecutorStatus.ACTIVE
    ex.state.phase = "submitting"
    store.save(ex)

    runtime = ExecutorRuntime(gateway=gateway, store=store)
    resumed = asyncio.run(runtime.reconcile())
    assert resumed == []
    rec = store.load("swap_orphan")
    assert rec.status == "FAILED"
    assert "no signature" in rec.close_reason


def test_swap_reconcile_confirms_by_signature(tmp_path):
    gateway = FakeGateway()
    store = ExecutorStore(tmp_path / "t.db")
    ex = SwapExecutor("swap_sig", swap_config(), gateway, store)
    ex.status = ExecutorStatus.ACTIVE
    ex.state.phase = "confirming"
    ex.state.signature = "sig-pending"
    store.save(ex)

    runtime = ExecutorRuntime(gateway=gateway, store=store)
    asyncio.run(runtime.reconcile())
    assert store.load("swap_sig").status == "CLOSED"


# -- lp ------------------------------------------------------------------------


def test_lp_opens_and_stop_detaches_leaving_position_open(tmp_path):
    gateway = FakeGateway(prices=[100.0])
    runtime = make_runtime(tmp_path, gateway)

    async def run():
        eid = runtime.create_executor(lp_config())
        await asyncio.sleep(0.2)
        ex = runtime._executors[eid]
        state = ex.state.state
        ex.early_stop(keep_position=True)  # detach: position stays open on-chain
        await runtime.wait_all()
        return eid, state

    eid, mid_state = asyncio.run(run())
    assert mid_state == LpStates.IN_RANGE
    rec = runtime.store.load(eid)
    assert rec.status == "CLOSED"
    assert "left open on-chain" in rec.close_reason
    assert not any(c[0] == "clmm_close" for c in gateway.calls)
    assert gateway.position_open  # still live on-chain
    # Initials must come from position-info, never the (negative,
    # balance-change) amounts in the open response
    assert Decimal(rec.state["initial_base_amount"]) == Decimal("0.005")
    assert Decimal(rec.state["initial_quote_amount"]) == Decimal("0.5")


def test_lp_stop_without_keep_position_closes_onchain(tmp_path):
    gateway = FakeGateway(prices=[100.0])
    runtime = make_runtime(tmp_path, gateway)

    async def run():
        eid = runtime.create_executor(lp_config())
        await asyncio.sleep(0.2)
        runtime._executors[eid].early_stop(keep_position=False)
        await runtime.wait_all()
        return eid

    eid = asyncio.run(run())
    rec = runtime.store.load(eid)
    assert rec.status == "CLOSED"
    assert rec.state["close_tx_hash"] == "sig-close"
    assert any(c[0] == "clmm_close" for c in gateway.calls)
    assert not gateway.position_open


def test_lp_closes_on_upper_limit(tmp_path):
    # price: open at 100, then above the 105 limit
    gateway = FakeGateway(prices=[100.0, 100.0, 106.0])
    runtime = make_runtime(tmp_path, gateway)

    async def run():
        eid = runtime.create_executor(lp_config())
        await runtime.wait_all()
        return eid

    eid = asyncio.run(run())
    rec = runtime.store.load(eid)
    assert rec.status == "CLOSED"
    assert "limit price hit" in rec.close_reason
    assert "above upper limit" in rec.close_reason


def test_lp_closeout_swap_when_not_keeping_position(tmp_path):
    gateway = FakeGateway(prices=[100.0, 100.0, 106.0])
    runtime = make_runtime(tmp_path, gateway)

    async def run():
        # initials freeze at 0.005 (position-info); close returns 0.005 +
        # 0.0001 fee -> 0.0001 excess base -> close-out SELL
        eid = runtime.create_executor(lp_config(keep_position=False))
        await runtime.wait_all()
        return eid

    eid = asyncio.run(run())
    rec = runtime.store.load(eid)
    assert rec.status == "CLOSED"
    assert rec.state["swap_tx_hash"] == "sig-swap"
    swap_call = next(c for c in gateway.calls if c[0] == "execute_swap")
    assert swap_call[1]["side"] == "SELL"
    assert abs(swap_call[1]["amount"] - 0.0001) < 1e-9


def test_lp_reconcile_readopts_live_position(tmp_path):
    gateway = FakeGateway(prices=[100.0])
    gateway.position_open = True
    store = ExecutorStore(tmp_path / "t.db")
    ex = LpExecutor("lp_live", lp_config(), gateway, store)
    ex.status = ExecutorStatus.ACTIVE
    ex.state.position_address = POSITION
    ex.state.state = LpStates.IN_RANGE
    ex.state.initial_base_amount = Decimal("0.005")
    ex.state.initial_quote_amount = Decimal("0.5")
    store.save(ex)

    runtime = ExecutorRuntime(gateway=gateway, store=store)

    async def run():
        resumed = await runtime.reconcile()
        assert resumed == ["lp_live"]
        await asyncio.sleep(0.05)
        runtime._executors["lp_live"].early_stop(keep_position=False)
        await runtime.wait_all()

    asyncio.run(run())
    rec = store.load("lp_live")
    assert rec.status == "CLOSED"
    # P&L survives the restart: initial amounts came back from the store
    assert rec.state["initial_base_amount"] == "0.005"


def test_lp_reconcile_settles_closed_position(tmp_path):
    gateway = FakeGateway()
    gateway.position_open = False
    store = ExecutorStore(tmp_path / "t.db")
    ex = LpExecutor("lp_gone", lp_config(), gateway, store)
    ex.status = ExecutorStatus.ACTIVE
    ex.state.position_address = POSITION
    ex.state.state = LpStates.IN_RANGE
    store.save(ex)

    runtime = ExecutorRuntime(gateway=gateway, store=store)
    resumed = asyncio.run(runtime.reconcile())
    assert resumed == []
    rec = store.load("lp_gone")
    assert rec.status == "CLOSED"
    assert "closed on-chain" in rec.close_reason


def test_lp_reconcile_mid_opening_fails_loudly(tmp_path):
    gateway = FakeGateway()
    gateway.position_open = True  # untracked position exists in the pool
    store = ExecutorStore(tmp_path / "t.db")
    ex = LpExecutor("lp_opening", lp_config(), gateway, store)
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = LpStates.OPENING
    store.save(ex)

    runtime = ExecutorRuntime(gateway=gateway, store=store)
    asyncio.run(runtime.reconcile())
    rec = store.load("lp_opening")
    assert rec.status == "FAILED"
    assert "mid-OPENING" in rec.close_reason


def test_lp_max_retries_fails(tmp_path):
    class BrokenGateway(FakeGateway):
        async def clmm_pool_info(self, *a, **kw):
            raise GatewayError("/trading/clmm/pool-info", 500, "boom")

    runtime = make_runtime(tmp_path, BrokenGateway())

    async def run():
        eid = runtime.create_executor(lp_config(max_retries=2))
        await runtime.wait_all()
        return eid

    eid = asyncio.run(run())
    rec = runtime.store.load(eid)
    assert rec.status == "FAILED"
    assert "max retries" in rec.close_reason


# -- risk declaration -------------------------------------------------------------


def test_lp_risk_declaration():
    cfg = lp_config(base_amount=Decimal("0.01"), quote_amount=Decimal("1"))
    decl = cfg.risk_declaration()
    assert decl.max_notional_quote == Decimal("0.01") * Decimal("100") + Decimal("1")
    assert decl.max_loss_quote == decl.max_notional_quote


# -- ranges (ported math) ----------------------------------------------------------


def test_range_bounds_centered():
    policy = RangePolicy(position_width_pct=Decimal("4"))
    lower, upper = calculate_price_bounds(Side.RANGE, Decimal("100"), policy)
    assert lower == Decimal("98")
    assert upper == Decimal("102")


def test_buy_bounds_below_price_with_offset():
    policy = RangePolicy(position_width_pct=Decimal("10"), position_offset_pct=Decimal("1"))
    lower, upper = calculate_price_bounds(Side.BUY, Decimal("100"), policy)
    assert upper == Decimal("99")               # 100 * (1 - 0.01)
    assert lower == Decimal("99") * Decimal("0.9")


def test_amounts_range_split():
    policy = RangePolicy(position_width_pct=Decimal("4"))
    base, quote = calculate_amounts(Side.RANGE, Decimal("100"), Decimal("10"), policy)
    assert quote == Decimal("5")
    assert base == Decimal("5") / Decimal("100")


def test_amounts_single_sided():
    policy = RangePolicy(position_width_pct=Decimal("4"))
    base, quote = calculate_amounts(Side.BUY, Decimal("100"), Decimal("10"), policy)
    assert (base, quote) == (Decimal("0"), Decimal("10"))
    base, quote = calculate_amounts(Side.SELL, Decimal("100"), Decimal("10"), policy)
    assert quote == Decimal("0")
    assert base == Decimal("10") / Decimal("100")


def test_plan_position_limits_and_clamping():
    policy = RangePolicy(
        position_width_pct=Decimal("10"),
        rebalance_threshold_pct=Decimal("2"),
        buy_price_min=Decimal("95"),
    )
    plan = plan_position(Side.BUY, Decimal("100"), Decimal("10"), policy)
    assert plan.lower_price == Decimal("95")  # clamped from 90
    assert plan.upper_price == Decimal("100")
    assert plan.lower_limit_price == Decimal("95") * Decimal("0.98")
    assert plan.upper_limit_price == Decimal("100") * Decimal("1.02")


def test_plan_position_impossible_returns_none():
    policy = RangePolicy(
        position_width_pct=Decimal("10"),
        buy_price_min=Decimal("200"), buy_price_max=Decimal("210"),
        sell_price_min=Decimal("300"), sell_price_max=Decimal("310"),
    )
    # BUY bounds land below both buy limits; SELL bounds below sell limits too
    assert plan_position(Side.BUY, Decimal("100"), Decimal("10"), policy) is None


# -- watchdog ---------------------------------------------------------------------


def test_watchdog_flattens_dead_task(tmp_path):
    gateway = FakeGateway(prices=[100.0])
    runtime = make_runtime(tmp_path, gateway)

    async def run():
        eid = runtime.create_executor(lp_config(update_interval=60))
        await asyncio.sleep(0.2)  # let it open the position
        # Simulate a crashed loop: cancel the task without any cleanup
        runtime._tasks[eid].cancel()
        try:
            await runtime._tasks[eid]
        except asyncio.CancelledError:
            pass
        await runtime._watchdog_pass()
        return eid

    eid = asyncio.run(run())
    rec = runtime.store.load(eid)
    assert rec.status == "FAILED"
    assert "watchdog" in rec.close_reason
    assert any(c[0] == "clmm_close" for c in gateway.calls)
