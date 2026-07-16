"""Triple-barrier tests for the spot position executor (mocked gateway)."""

import asyncio
from decimal import Decimal

import pytest

from condor.executors.base import ExecutorStatus
from condor.executors.log import ExecutorLog
from condor.executors.position import (
    PositionExecutor,
    PositionSpotConfig,
    PositionStates,
)
from condor.executors.runtime import ExecutorRuntime

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
MINT = "MemeCoinMint1111111111111111111111111111111"


class FakeGateway:
    """Entry/exit at prices[0]; each monitoring quote pops the next price.

    Prices are the memecoin's price in quote units. The executor enters
    by SELLING the quote token (ExactIn), so an entry execute has
    base_token == the QUOTE symbol ("SOL") and amountOut in memecoins.
    """

    def __init__(self, prices, held_base=0.0002):
        self.prices = list(prices)
        self.held_base = held_base
        self.calls = []

    def _next_price(self):
        return self.prices.pop(0) if len(self.prices) > 1 else self.prices[0]

    async def close(self):
        pass

    async def quote_swap(self, **kw):
        price = self._next_price()
        self.calls.append(("quote", kw["side"], price))
        return {"price": price}

    async def execute_swap(self, **kw):
        self.calls.append(("execute", kw["side"], kw["base_token"], kw["amount"]))
        price = self.prices[0]
        if kw["base_token"] == "SOL":  # entry: sell SOL for the memecoin
            return {
                "signature": "sig-buy",
                "status": 1,
                "data": {
                    "amountIn": kw["amount"],
                    "amountOut": kw["amount"] / price,
                    "fee": 0.0001,
                },
            }
        # exit: sell the memecoin for SOL
        return {
            "signature": "sig-sell",
            "status": 1,
            "data": {
                "amountIn": kw["amount"],
                "amountOut": kw["amount"] * price,
                "fee": 0.0001,
            },
        }

    async def get_balances(self, chain, network, address, tokens=None):
        # Reconcile / held-size: report the venue-held memecoin balance.
        return {t: self.held_base for t in (tokens or [])}


def config(**over):
    from condor.accounts.model import AccountRef

    kw = dict(
        chain_network="solana-mainnet-beta",
        wallet_address=WALLET,
        account_ref=AccountRef("solana", WALLET),
        base_token=MINT,
        quote_token="SOL",
        amount_quote=Decimal("0.02"),
        take_profit_pct=Decimal("0.10"),
        stop_loss_pct=Decimal("0.05"),
        time_limit_s=3600,
        update_interval=0.01,
    )
    kw.update(over)
    return PositionSpotConfig(**kw)


def run_executor(tmp_path, gateway, cfg):
    store = ExecutorLog(tmp_path)
    runtime = ExecutorRuntime(store=store)
    runtime._connector_overrides[("solana", "spot")] = gateway

    async def run():
        eid = runtime.create_executor(cfg)
        await runtime.wait_all()
        return eid

    eid = asyncio.run(run())
    return store.load(eid), gateway


def test_take_profit_closes(tmp_path):
    # unit quote 100 -> buy, then monitored sell-quotes: 100, 111 (TP at +10%)
    rec, gw = run_executor(tmp_path, FakeGateway([100.0, 100.0, 111.0]), config())
    assert rec.status == "CLOSED"
    assert rec.state["close_type"] == "take_profit"
    assert rec.state["close_ref"] == "sig-sell"
    assert ("execute", "SELL", MINT, pytest.approx(0.0002)) in gw.calls


def test_stop_loss_closes(tmp_path):
    rec, _ = run_executor(tmp_path, FakeGateway([100.0, 100.0, 94.0]), config())
    assert rec.status == "CLOSED"
    assert rec.state["close_type"] == "stop_loss"


def test_time_limit_closes_even_without_price(tmp_path):
    class NoPriceAfterOpen(FakeGateway):
        async def quote_swap(self, **kw):
            # first quote (unit price for the buy) works; monitoring quotes fail
            if not any(c[0] == "execute" for c in self.calls):
                return await super().quote_swap(**kw)
            raise RuntimeError("price feed down")

    rec, _ = run_executor(tmp_path, NoPriceAfterOpen([100.0]), config(time_limit_s=0))
    assert rec.status == "CLOSED"
    assert rec.state["close_type"] == "time_limit"


def test_trailing_stop_ratchets_and_closes(tmp_path):
    # activation 5%, delta 3%: 100 -> 108 (activates, trigger 5%) -> 115
    # (trigger 12%) -> 110 (pnl 10% < 12% trigger -> close)
    cfg = config(
        take_profit_pct=None,
        stop_loss_pct=None,
        trailing_activation_pct=Decimal("0.05"),
        trailing_delta_pct=Decimal("0.03"),
    )
    rec, _ = run_executor(
        tmp_path, FakeGateway([100.0, 100.0, 108.0, 115.0, 110.0]), cfg
    )
    assert rec.status == "CLOSED"
    assert rec.state["close_type"] == "trailing_stop"


def test_detach_on_stop_keeps_tokens(tmp_path):
    gateway = FakeGateway([100.0, 100.0])
    store = ExecutorLog(tmp_path)
    runtime = ExecutorRuntime(store=store)
    runtime._connector_overrides[("solana", "spot")] = gateway

    async def run():
        eid = runtime.create_executor(config(take_profit_pct=Decimal("99")))
        await asyncio.sleep(0.15)
        runtime._executors[eid].early_stop(keep_position=True)
        await runtime.wait_all()
        return eid

    eid = asyncio.run(run())
    rec = store.load(eid)
    assert rec.status == "CLOSED"
    assert rec.state["close_type"] == "detached"
    # tokens were never sold: no execute against the memecoin mint
    assert not any(c[0] == "execute" and c[2] == MINT for c in gateway.calls)


def test_reconcile_rejects_legacy_active_position_without_orders(tmp_path):
    gateway = FakeGateway([100.0, 100.0, 111.0])
    store = ExecutorLog(tmp_path)
    ex = PositionExecutor("pos_live", config(), gateway, store)
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = PositionStates.ACTIVE
    ex.state.size = Decimal("0.0002")
    ex.state.amount_spent = Decimal("0.02")
    import time as _time

    ex.state.entry_price = Decimal("100")
    ex.state.opened_at = _time.time()
    store.save(ex)

    runtime = ExecutorRuntime(store=store)
    runtime._connector_overrides[("solana", "spot")] = gateway

    async def run():
        resumed = await runtime.reconcile()
        assert resumed == ["pos_live"]
        await runtime.wait_all()

    asyncio.run(run())
    rec = store.load("pos_live")
    # The new schema never guesses a close size from a legacy aggregate.
    assert rec.status == "FAILED"
    assert "no landed orders" in (rec.close_reason or "")


def test_reconcile_fails_loud_mid_opening(tmp_path):
    gateway = FakeGateway([100.0], held_base=0.0)  # no position landed on-venue
    store = ExecutorLog(tmp_path)
    ex = PositionExecutor("pos_opening", config(), gateway, store)
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = PositionStates.OPENING
    store.save(ex)

    runtime = ExecutorRuntime(store=store)
    runtime._connector_overrides[("solana", "spot")] = gateway
    resumed = asyncio.run(runtime.reconcile())
    assert resumed == []
    rec = store.load("pos_opening")
    assert rec.status == "FAILED"
    assert "mid-OPENING" in rec.close_reason


def test_risk_declaration_full_loss():
    decl = config(amount_quote=Decimal("0.05")).risk_declaration()
    assert decl.max_notional_quote == Decimal("0.05")
    assert decl.max_loss_quote == Decimal("0.05")  # SL is intent, not a bound


def _outbox_texts():
    import condor.notifications as notifications

    if not notifications.OUTBOX_PATH.exists():
        return []
    import json

    return [
        json.loads(l)["text"]
        for l in notifications.OUTBOX_PATH.read_text().splitlines()
        if l.strip()
    ]


def test_trade_notifications_on_entry_and_exit(tmp_path):
    # take profit at +10%: expect an entry AND an exit notification
    run_executor(tmp_path, FakeGateway([100.0, 100.0, 111.0]), config())
    texts = _outbox_texts()
    assert any(t.startswith("🟢 Entered") for t in texts), texts
    assert any("Exited" in t and "take_profit" in t for t in texts), texts


def test_trade_notifications_off_by_flag(tmp_path):
    run_executor(
        tmp_path, FakeGateway([100.0, 100.0, 111.0]), config(notify_trades=False)
    )
    texts = _outbox_texts()
    assert not any("Entered" in t or "Exited" in t for t in texts), texts
