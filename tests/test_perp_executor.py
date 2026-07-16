"""Perp executor lifecycle + barriers, driven through the runtime with a fake
HyperliquidClient injected into the runtime's connector cache."""

import asyncio
from decimal import Decimal

import pytest

from condor.executors.base import ExecutorStatus
from condor.executors.hyperliquid import Fill, OrderAck, Position
from condor.executors.log import ExecutorLog
from condor.executors.order import OrderExecutor, OrderPerpConfig, OrderStates
from condor.executors.position import (
    PositionExecutor,
    PositionPerpConfig,
    PositionStates,
)
from condor.executors.runtime import ExecutorRuntime


class FakeHL:
    def __init__(self, entry, marks, liq=None, vanish=False):
        self.entry = Decimal(str(entry))
        self.marks = [Decimal(str(m)) for m in marks]
        self.liq = Decimal(str(liq)) if liq is not None else None
        self.vanish = vanish
        self.opened = False
        self.closed = False
        self.size = Decimal("0")
        self.triggers = []
        self.closes = []
        self.cancels = []
        self._oid = 100
        self._pos_calls = 0

    async def set_leverage(self, coin, lev, cross):
        self.leverage = lev

    async def mid_price(self, coin):
        if not self.opened:
            return self.entry
        return self.marks.pop(0) if len(self.marks) > 1 else self.marks[0]

    async def market_open(self, coin, is_buy, size, slippage_pct):
        self.opened = True
        self.size = size
        return Fill(oid=1, avg_px=self.entry, size=size)

    async def place_trigger(
        self, coin, is_buy, size, trigger_px, kind, reduce_only=True
    ):
        self._oid += 1
        self.triggers.append((kind, float(trigger_px)))
        return OrderAck(oid=self._oid, status="resting")

    async def order_status(self, oid):
        return {"order": {"status": "open"}}

    async def position(self, coin):
        if self.closed or not self.opened:
            return None
        self._pos_calls += 1
        # First call is the open-settle; vanish only afterwards (a trigger fired mid-flight).
        if self.vanish and self._pos_calls > 1:
            return None
        return Position(
            coin=coin,
            side="LONG",
            size=self.size,
            entry_px=self.entry,
            unrealized_pnl=Decimal("0"),
            liquidation_px=self.liq,
            margin_used=Decimal("0"),
            leverage=2,
            funding=Decimal("0"),
        )

    async def market_close(self, coin, size=None):
        self.closed = True
        self.closes.append(size)
        px = self.marks[0] if self.marks else self.entry
        return Fill(oid=9, avg_px=px, size=size or self.size)

    async def cancel(self, coin, oid):
        self.cancels.append(oid)

    async def fills(self, since_ms=None):
        return [
            {
                "coin": "ETH",
                "closedPnl": "1.5",
                "fee": "0.02",
                "dir": "Close Long",
                "px": "106",
            }
        ]


class CancelFailHL(FakeHL):
    """A venue that keeps a limit live after every cancel attempt."""

    async def order_status(self, oid):
        return {"order": {"status": "open"}}

    async def cancel(self, coin, oid):
        raise RuntimeError("venue cancel unavailable")


def _run(tmp_path, fake, cfg):
    store = ExecutorLog(tmp_path)
    runtime = ExecutorRuntime(store=store)
    runtime._connector_overrides[("hyperliquid", "perp")] = fake

    async def go():
        eid = runtime.create_executor(cfg)
        await asyncio.wait_for(runtime.wait_all(), timeout=5)
        return runtime.store.load(eid)

    return asyncio.run(go())


def _cfg(**over):
    from condor.accounts.model import AccountRef

    kw = dict(
        coin="ETH",
        side="LONG",
        notional_quote=Decimal("20"),
        leverage=2,
        account_ref=AccountRef("hyperliquid", "0x" + "a" * 40),
        update_interval=0.01,
        native_triggers=False,
        notify_trades=False,
    )
    kw.update(over)
    return PositionPerpConfig(**kw)


def test_take_profit_closes(tmp_path):
    fake = FakeHL(entry=100, marks=[106], liq=50)
    rec = _run(tmp_path, fake, _cfg(take_profit_pct=Decimal("0.05")))
    assert rec.status == ExecutorStatus.CLOSED.value
    assert rec.state["close_type"] == "take_profit"
    assert fake.closes  # a market_close was issued


def test_stop_loss_closes(tmp_path):
    fake = FakeHL(entry=100, marks=[96], liq=50)
    rec = _run(tmp_path, fake, _cfg(stop_loss_pct=Decimal("0.03")))
    assert rec.state["close_type"] == "stop_loss"


def test_liquidation_guard_fires_first(tmp_path):
    # mark 100, liq 95 -> 5% away, guard 15% -> close even with no TP/SL hit
    fake = FakeHL(entry=100, marks=[100], liq=95)
    rec = _run(tmp_path, fake, _cfg(liquidation_guard_pct=Decimal("0.15")))
    assert rec.state["close_type"] == "liquidation_guard"


def test_time_limit_closes(tmp_path):
    fake = FakeHL(entry=100, marks=[100], liq=50)
    rec = _run(tmp_path, fake, _cfg(time_limit_s=0))
    assert rec.state["close_type"] == "time_limit"


def test_native_triggers_placed(tmp_path):
    fake = FakeHL(entry=100, marks=[106], liq=50)
    _run(
        tmp_path,
        fake,
        _cfg(
            take_profit_pct=Decimal("0.05"),
            stop_loss_pct=Decimal("0.03"),
            native_triggers=True,
        ),
    )
    kinds = {k for k, _ in fake.triggers}
    assert kinds == {"tp", "sl"}


def test_position_vanished_settles(tmp_path):
    # native trigger fired between ticks: venue shows flat -> settle from fills
    fake = FakeHL(entry=100, marks=[100], liq=50, vanish=True)
    rec = _run(tmp_path, fake, _cfg(take_profit_pct=Decimal("0.05")))
    assert rec.status == ExecutorStatus.CLOSED.value


def test_realized_pnl_from_fills(tmp_path):
    fake = FakeHL(entry=100, marks=[106], liq=50)
    rec = _run(tmp_path, fake, _cfg(take_profit_pct=Decimal("0.05")))
    # 1.5 closedPnl - 0.02 fee (perp-specific fields live in state.extra)
    assert Decimal(str(rec.state["extra"]["realized_pnl"])) == Decimal("1.48")


def test_order_perp_market_fills(tmp_path):
    """order_perp: single-leg market open, terminal once filled (no barriers)."""
    fake = FakeHL(entry=100, marks=[100])
    store = ExecutorLog(tmp_path)
    rt = ExecutorRuntime(store=store)
    rt._connector_overrides[("hyperliquid", "perp")] = fake

    async def go():
        eid = rt.create_executor(
            OrderPerpConfig(
                coin="ETH",
                side="LONG",
                notional_quote=Decimal("20"),
                leverage=2,
                account_ref={
                    "venue_id": "hyperliquid",
                    "custody_address": "0x" + "a" * 40,
                },
                update_interval=0.01,
                notify_trades=False,
            )
        )
        await asyncio.wait_for(rt.wait_all(), timeout=5)
        return rt.store.load(eid)

    rec = asyncio.run(go())
    assert rec.type == "order_perp"
    assert rec.status == ExecutorStatus.CLOSED.value
    assert rec.state["state"] == "DONE"
    assert fake.opened  # a market_open was issued
    assert Decimal(str(rec.state["size"])) == Decimal("0.2")  # 20 / 100


def test_order_cancel_failure_keeps_resting_order_non_terminal(tmp_path):
    """A failed cancel followed by an OPEN venue status must not be reported as
    a cleanly cancelled order. The executor must remain live and retryable."""
    fake = CancelFailHL(entry=100, marks=[100])
    ex = OrderExecutor(
        "order_live",
        OrderPerpConfig(
            coin="ETH",
            side="LONG",
            notional_quote=Decimal("20"),
            leverage=2,
            order_type="limit",
            limit_px=Decimal("99"),
            notify_trades=False,
        ),
        fake,
        ExecutorLog(tmp_path),
    )
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = OrderStates.RESTING
    ex.state.open_ref = "101"
    ex._cancel_requested = True

    asyncio.run(ex.control_task())

    assert ex.status == ExecutorStatus.ACTIVE
    assert ex.state.state == OrderStates.RESTING


def test_position_entry_cancel_failure_stays_opening(tmp_path):
    """A failed limit-entry cancel cannot complete the executor while the venue
    may still fill the order later."""
    fake = CancelFailHL(entry=100, marks=[100])
    ex = PositionExecutor(
        "position_opening",
        _cfg(entry="limit", limit_px=Decimal("99")),
        fake,
        ExecutorLog(tmp_path),
    )
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = PositionStates.OPENING
    ex.state.open_ref = "101"

    asyncio.run(ex._cancel_opening("early_stop"))

    assert ex.status == ExecutorStatus.ACTIVE
    assert ex.state.state == PositionStates.OPENING


def test_early_stop_detach_keeps_position():
    cfg = _cfg(take_profit_pct=Decimal("0.05"))
    ex = PositionExecutor("perp_1", cfg, FakeHL(entry=100, marks=[100]), store=None)
    ex.state.state = PositionStates.ACTIVE
    ex.state.size = Decimal("0.2")
    ex.early_stop(keep_position=True)
    assert ex.state.state == PositionStates.DETACHING
    assert ex.state.close_type == "detaching"
