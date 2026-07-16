"""Unit tests for condor.executors — runtime, store, swap.

Gateway is faked; live-path validation happens in the M0 live tests
(docs/condor-simple.md §7).
"""

import asyncio
from decimal import Decimal

from condor.executors.base import ExecutorStatus
from condor.executors.log import ExecutorLog
from condor.executors.order import OrderExecutor, OrderSpotConfig, OrderStates
from condor.executors.position import PositionSpotConfig
from condor.executors.runtime import ExecutorRuntime

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
MINT = "MemeCoinMint1111111111111111111111111111111"


class FakeGateway:
    """In-memory gateway double: price scripted per call."""

    def __init__(self, prices=None):
        self.prices = list(prices or [100.0])
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
        return {
            "signature": "sig-swap",
            "status": 1,
            "data": {
                "amountIn": kw["amount"],
                "amountOut": kw["amount"] * 100,
                "fee": 0.0001,
            },
        }

    async def poll_tx(self, chain, network, signature):
        self.calls.append(("poll_tx", signature))
        return {"txStatus": 1, "signature": signature}

    async def get_balances(self, chain, network, address, tokens=None):
        self.calls.append(("get_balances", tokens))
        return {t: 999.0 for t in (tokens or [])}

    async def save_token(self, chain_network, address):
        self.calls.append(("save_token", address))
        return {}


def make_runtime(tmp_path, gateway=None):
    store = ExecutorLog(tmp_path)
    runtime = ExecutorRuntime(store=store)
    runtime._connector_overrides[("solana", "spot")] = gateway or FakeGateway()
    return runtime


def swap_config(**over):
    from condor.accounts.model import AccountRef

    kw = dict(
        chain_network="solana-mainnet-beta",
        wallet_address=WALLET,
        account_ref=AccountRef("solana", WALLET),
        base_token="SOL",
        quote_token="USDC",
        amount=Decimal("0.01"),
        side="SELL",
        notional_quote=Decimal("1"),
        update_interval=0.01,
    )
    kw.update(over)
    return OrderSpotConfig(**kw)


def position_config(**over):
    from condor.accounts.model import AccountRef

    kw = dict(
        chain_network="solana-mainnet-beta",
        wallet_address=WALLET,
        account_ref=AccountRef("solana", WALLET),
        base_token=MINT,
        quote_token="SOL",
        amount_quote=Decimal("0.02"),
        update_interval=0.01,
    )
    kw.update(over)
    return PositionSpotConfig(**kw)


# -- store ---------------------------------------------------------------------


def test_store_roundtrip(tmp_path):
    store = ExecutorLog(tmp_path)
    ex = OrderExecutor("order_1", swap_config(), FakeGateway(), store)
    store.save(ex)
    rec = store.load("order_1")
    assert rec.status == "PENDING"
    assert rec.type == "order_spot"
    assert rec.config["base_token"] == "SOL"
    assert store.load_non_terminal()[0].id == "order_1"

    store.mark("order_1", "CLOSED", "done")
    rec = store.load("order_1")
    assert rec.status == "CLOSED"
    assert rec.close_reason == "done"
    assert store.load_non_terminal() == []


# -- order_spot (single-leg swap) ---------------------------------------------


def test_swap_happy_path(tmp_path):
    runtime = make_runtime(tmp_path)

    async def run():
        eid = runtime.create_executor(swap_config())
        await runtime.wait_all()
        return eid

    eid = asyncio.run(run())
    rec = runtime.store.load(eid)
    assert rec.status == "CLOSED"
    assert rec.state["open_ref"] == "sig-swap"
    assert rec.state["state"] == "DONE"


def test_swap_reconcile_orphan_submitting(tmp_path):
    """Crash during submission with no signature -> FAILED with clear reason."""
    gateway = FakeGateway()
    store = ExecutorLog(tmp_path)
    ex = OrderExecutor("order_orphan", swap_config(), gateway, store)
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = OrderStates.SUBMITTING
    store.save(ex)

    runtime = ExecutorRuntime(store=store)
    runtime._connector_overrides[("solana", "spot")] = gateway
    resumed = asyncio.run(runtime.reconcile())
    assert resumed == []
    rec = store.load("order_orphan")
    assert rec.status == "FAILED"
    assert "no signature" in rec.close_reason


def test_swap_reconcile_confirms_by_signature(tmp_path):
    gateway = FakeGateway()
    store = ExecutorLog(tmp_path)
    ex = OrderExecutor("order_sig", swap_config(), gateway, store)
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = OrderStates.RESTING
    ex.state.open_ref = "sig-pending"
    store.save(ex)

    runtime = ExecutorRuntime(store=store)
    runtime._connector_overrides[("solana", "spot")] = gateway

    async def run():
        resumed = await runtime.reconcile()
        assert resumed == ["order_sig"]
        await runtime.wait_all()

    asyncio.run(run())
    assert store.load("order_sig").status == "CLOSED"


# -- watchdog ---------------------------------------------------------------------


def test_watchdog_flattens_dead_task(tmp_path):
    gateway = FakeGateway(prices=[100.0])
    runtime = make_runtime(tmp_path, gateway)

    async def run():
        # No barriers -> the position opens and then just sits.
        eid = runtime.create_executor(
            position_config(
                take_profit_pct=None,
                stop_loss_pct=None,
                time_limit_s=None,
                update_interval=60,
            )
        )
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
    # the flatten sold the held base back to quote
    assert any(
        c[0] == "execute_swap"
        and c[1].get("base_token") == MINT
        and c[1].get("side") == "SELL"
        for c in gateway.calls
    )
