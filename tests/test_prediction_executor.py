"""PredictionExecutor lifecycle + barriers on both venues (fakes injected)."""

import asyncio
from decimal import Decimal

import pytest

from condor.executors.base import ExecutorStatus
from condor.executors.hyperliquid import OrderAck as HlOrderAck
from condor.executors.hyperliquid_outcome import Outcome
from condor.executors.log import ExecutorLog
from condor.executors.polymarket import OrderAck
from condor.executors.position import PositionExecutor, PositionPredConfig, PositionStates
from condor.executors.runtime import ExecutorRuntime


class FakePM:
    """Polymarket outcome token: BUY sets shares at entry price, SELL returns
    shares*mark as USDC; midpoint drives the barrier."""

    def __init__(self, entry, marks):
        self.entry = Decimal(str(entry))
        self.marks = [Decimal(str(m)) for m in marks]
        self.shares = Decimal("0")
        self.usdc = Decimal("0")
        self.opened = False
        self.orders = []

    async def midpoint(self, token_id):
        if not self.opened:
            return self.entry
        return self.marks.pop(0) if len(self.marks) > 1 else self.marks[0]

    async def shares_balance(self, token_id):
        return self.shares

    async def usdc_balance(self):
        return self.usdc

    async def place_market(self, token_id, side, amount):
        self.orders.append((side, float(amount)))
        if side == "BUY":
            self.shares = Decimal(str(amount)) / self.entry
            self.opened = True
            return OrderAck("0xbuy", "matched", True)
        price = self.marks[0] if self.marks else self.entry
        self.usdc += Decimal(str(amount)) * price
        self.shares -= Decimal(str(amount))
        return OrderAck("0xsell", "matched", True)


class FakeOutcome:
    """Fake HIP-4 outcome client: buy sets Yes-share holdings at entry price,
    sell returns shares*mark as USDC; price() drives the barrier."""

    def __init__(self, entry, marks):
        self.entry = Decimal(str(entry))
        self.marks = [Decimal(str(m)) for m in marks]
        self.shares_held = Decimal("0")
        self.usdc = Decimal("0")
        self.opened = False

    async def find_outcome(self, market):
        return Outcome(id=173, name="Argentina", description="", sides=["Yes", "No"], quote_token="USDC")

    def side_index(self, outcome, side_name):
        return outcome.sides.index(side_name)

    async def price(self, outcome, side, want="mid"):
        if not self.opened:
            return self.entry
        return self.marks.pop(0) if len(self.marks) > 1 else self.marks[0]

    async def shares(self, outcome, side):
        return self.shares_held

    async def usdc_balance(self):
        return self.usdc

    async def marketable_buy(self, outcome, side, size, slippage_pct):
        self.opened = True
        self.shares_held = Decimal(str(size))
        self.usdc -= Decimal(str(size)) * self.entry
        return HlOrderAck(oid=1, status="filled")

    async def marketable_sell(self, outcome, side, size, slippage_pct):
        px = self.marks[0] if self.marks else self.entry
        self.usdc += Decimal(str(size)) * px
        self.shares_held -= Decimal(str(size))
        return HlOrderAck(oid=2, status="filled")


def _run_pm(tmp_path, fake, cfg):
    rt = ExecutorRuntime(store=ExecutorLog(tmp_path))
    rt._polymarket = fake

    async def go():
        eid = rt.create_executor(cfg)
        await asyncio.wait_for(rt.wait_all(), timeout=5)
        return rt.store.load(eid)

    return asyncio.run(go())


def _run_outcome(tmp_path, fake, cfg):
    rt = ExecutorRuntime(store=ExecutorLog(tmp_path))
    rt._hl_clients["outcome"] = fake

    async def go():
        eid = rt.create_executor(cfg)
        await asyncio.wait_for(rt.wait_all(), timeout=5)
        return rt.store.load(eid)

    return asyncio.run(go())


def _pm_cfg(**over):
    kw = dict(venue="polymarket", market="tok123", amount_quote=Decimal("10"),
              update_interval=0.01, notify_trades=False)
    kw.update(over)
    return PositionPredConfig(**kw)


def test_polymarket_take_profit(tmp_path):
    fake = FakePM(entry="0.5", marks=["0.6"])
    rec = _run_pm(tmp_path, fake, _pm_cfg(take_profit_pct=Decimal("0.2")))
    assert rec.status == ExecutorStatus.CLOSED.value
    assert rec.state["close_type"] == "take_profit"
    assert ("SELL", pytest.approx(20.0)) in fake.orders  # 10 USDC / 0.5 = 20 shares sold


def test_polymarket_resolve_win(tmp_path):
    fake = FakePM(entry="0.5", marks=["0.99"])
    rec = _run_pm(tmp_path, fake, _pm_cfg(resolve_win_price=Decimal("0.98")))
    assert rec.state["close_type"] == "resolved_win"


def test_polymarket_resolve_loss(tmp_path):
    fake = FakePM(entry="0.5", marks=["0.01"])
    rec = _run_pm(tmp_path, fake, _pm_cfg(resolve_loss_price=Decimal("0.02")))
    assert rec.state["close_type"] == "resolved_loss"


def test_polymarket_stop_loss(tmp_path):
    fake = FakePM(entry="0.5", marks=["0.34"])
    rec = _run_pm(tmp_path, fake, _pm_cfg(stop_loss_pct=Decimal("0.3")))
    assert rec.state["close_type"] == "stop_loss"


def test_polymarket_time_limit(tmp_path):
    fake = FakePM(entry="0.5", marks=["0.5"])
    rec = _run_pm(tmp_path, fake, _pm_cfg(time_limit_s=0))
    assert rec.state["close_type"] == "time_limit"


def test_polymarket_entry_and_pnl(tmp_path):
    fake = FakePM(entry="0.5", marks=["0.6"])
    rec = _run_pm(tmp_path, fake, _pm_cfg(take_profit_pct=Decimal("0.2")))
    s = rec.state
    assert Decimal(str(s["entry_price"])) == Decimal("0.5")
    assert Decimal(str(s["size"])) == Decimal("20")  # 10 / 0.5
    # proceeds 20 * 0.6 = 12, spent 10 -> +2
    assert Decimal(str(s["proceeds"])) == Decimal("12.0")


def test_polymarket_short_no_token_profits_on_rise(tmp_path):
    """SHORT = holding the No token; a rising held token is a PROFIT, so this
    must hit take_profit, NOT stop_loss. Regression for the reversed-sign bug
    (#5): with both barriers at 20%, a +20% move on the held token is a win."""
    fake = FakePM(entry="0.5", marks=["0.6"])
    rec = _run_pm(tmp_path, fake, _pm_cfg(position="SHORT",
                                          take_profit_pct=Decimal("0.2"),
                                          stop_loss_pct=Decimal("0.2")))
    assert rec.status == ExecutorStatus.CLOSED.value
    assert rec.state["close_type"] == "take_profit"


def test_hyperliquid_outcome_take_profit(tmp_path):
    # Yes at 0.5 -> mark 0.6 = +20% -> take_profit
    fake = FakeOutcome(entry="0.5", marks=["0.6"])
    cfg = PositionPredConfig(venue="hyperliquid", market="Argentina", position="LONG",
                           amount_quote=Decimal("10"), take_profit_pct=Decimal("0.2"),
                           update_interval=0.01, notify_trades=False)
    rec = _run_outcome(tmp_path, fake, cfg)
    assert rec.status == ExecutorStatus.CLOSED.value
    assert rec.state["close_type"] == "take_profit"
    assert Decimal(str(rec.state["size"])) == Decimal("20")  # 10 USDC / 0.5


def test_order_pred_buys_shares(tmp_path):
    """order_pred: single-leg buy of outcome shares, terminal once filled."""
    from condor.executors.order import OrderPredConfig

    fake = FakePM(entry="0.5", marks=["0.5"])
    rt = ExecutorRuntime(store=ExecutorLog(tmp_path))
    rt._polymarket = fake

    async def go():
        eid = rt.create_executor(OrderPredConfig(
            market="tok123", amount_quote=Decimal("10"),
            update_interval=0.01, notify_trades=False))
        await asyncio.wait_for(rt.wait_all(), timeout=5)
        return rt.store.load(eid)

    rec = asyncio.run(go())
    assert rec.type == "order_pred"
    assert rec.status == ExecutorStatus.CLOSED.value
    assert rec.state["state"] == "DONE"
    assert Decimal(str(rec.state["size"])) == Decimal("20")  # 10 USDC / 0.5
    assert ("BUY", pytest.approx(10.0)) in fake.orders


class FakePMLimit:
    """Polymarket fake for the order_pred RESTING GTC-limit path: place_limit
    rests on a book, orders() lists it, cancel() pulls it, and (when fill=True)
    the resting order fills after the first poll by crediting shares."""

    def __init__(self, fill=True, fill_after=1):
        self.shares = Decimal("0")
        self._open: dict = {}
        self._polls = 0
        self.fill = fill
        self.fill_after = fill_after
        self.cancelled: list = []
        self._next = 1

    async def shares_balance(self, token_id):
        return self.shares

    async def place_limit(self, token_id, side, price, size, order_type="GTC"):
        oid = f"0xlim{self._next}"
        self._next += 1
        self._open[oid] = Decimal(str(size))
        return OrderAck(oid, "live", True)

    async def orders(self, market=None, asset_id=None):
        self._polls += 1
        if self.fill and self._polls >= self.fill_after:
            for sz in self._open.values():
                self.shares += sz  # resting order fills
            self._open.clear()
        return [{"id": oid} for oid in self._open]

    async def cancel(self, order_id):
        self.cancelled.append(order_id)
        self._open.pop(order_id, None)


def test_order_pred_limit_rests_then_fills(tmp_path):
    """A GTC limit rests, then fills; size comes from the real shares delta."""
    from condor.executors.order import OrderPredConfig

    fake = FakePMLimit(fill=True, fill_after=1)
    rt = ExecutorRuntime(store=ExecutorLog(tmp_path))
    rt._polymarket = fake

    async def go():
        eid = rt.create_executor(OrderPredConfig(
            market="tok", amount_quote=Decimal("10"), order_type="limit",
            limit_px=Decimal("0.5"), update_interval=0.01, notify_trades=False))
        await asyncio.wait_for(rt.wait_all(), timeout=5)
        return rt.store.load(eid)

    rec = asyncio.run(go())
    assert rec.status == ExecutorStatus.CLOSED.value
    assert rec.state["state"] == "DONE"
    assert Decimal(str(rec.state["size"])) == Decimal("20")  # 10 / 0.5
    assert Decimal(str(rec.state["entry_price"])) == Decimal("0.5")


def test_order_pred_limit_cancel_on_stop(tmp_path):
    """Stopping a resting limit pulls it from the venue (requote safety, #3)."""
    from condor.executors.order import OrderPredConfig

    fake = FakePMLimit(fill=False)  # never fills — rests until cancelled
    rt = ExecutorRuntime(store=ExecutorLog(tmp_path))
    rt._polymarket = fake

    async def go():
        eid = rt.create_executor(OrderPredConfig(
            market="tok", amount_quote=Decimal("10"), order_type="limit",
            limit_px=Decimal("0.5"), update_interval=0.02, notify_trades=False))
        await asyncio.sleep(0.1)  # let it reach RESTING
        rt.stop_executor(eid, keep_position=False)
        await asyncio.wait_for(rt.wait_all(), timeout=5)
        return rt.store.load(eid)

    rec = asyncio.run(go())
    assert rec.status == ExecutorStatus.CLOSED.value
    assert rec.state["state"] == "DONE"
    assert rec.state["close_type"] == "early_stop"
    assert len(fake.cancelled) == 1  # the resting order was actually pulled


def test_early_stop_detach():
    cfg = _pm_cfg(take_profit_pct=Decimal("0.2"))
    ex = PositionExecutor("prediction_1", cfg, FakePM("0.5", ["0.5"]), store=None)
    ex.state.state = PositionStates.ACTIVE
    ex.state.size = Decimal("20")
    ex.early_stop(keep_position=True)
    assert ex.state.state == PositionStates.COMPLETE
    assert ex.state.close_type == "detached"
