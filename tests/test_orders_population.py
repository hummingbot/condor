"""orders[] population (§6.2b): every venue placement/poll/cancel site lands a
LandedOrder and updates it with the venue's ABSOLUTE cumulative state, purely
alongside the legacy scalar fields (fakes drive the adapters directly)."""

import asyncio
from decimal import Decimal

from condor.executors.adapters import PerpAdapter, PolymarketPredAdapter, SpotAdapter
from condor.executors.hyperliquid import Fill, OrderAck as HlAck, Position
from condor.executors.order import (
    OrderPerpConfig,
    OrderPredConfig,
    OrderState,
    OrderStates,
)
from condor.executors.orders import OrderRole, OrderStatus, find_order
from condor.executors.polymarket import OrderAck as PmAck
from condor.executors.position import PositionPerpConfig, PositionSpotConfig, PositionState


def _run(coro):
    return asyncio.run(coro)


# -- (a) perp market entry + native protection triggers -----------------------


class FakePerpMarket:
    """Market entry fills instantly; triggers rest with fresh oids."""

    def __init__(self):
        self.entry = Decimal("100")
        self.size = Decimal("0")
        self._oid = 10

    async def set_leverage(self, coin, lev, cross):
        pass

    async def mid_price(self, coin):
        return self.entry

    async def market_open(self, coin, is_buy, size, slippage_pct):
        self.size = size
        return Fill(oid=1, avg_px=self.entry, size=size)

    async def position(self, coin):
        return Position(
            coin=coin, side="LONG", size=self.size, entry_px=self.entry,
            unrealized_pnl=Decimal("0"), liquidation_px=None,
            margin_used=Decimal("0"), leverage=2, funding=Decimal("0"),
        )

    async def place_trigger(self, coin, is_buy, size, trigger_px, kind, reduce_only=True):
        self._oid += 1
        return HlAck(oid=self._oid, status="resting")


def test_perp_market_entry_records_entry_and_protection():
    cfg = PositionPerpConfig(
        coin="ETH", side="LONG", notional_quote=Decimal("20"), leverage=2,
        take_profit_pct=Decimal("0.05"), stop_loss_pct=Decimal("0.03"),
        native_triggers=True, notify_trades=False,
    )
    adapter = PerpAdapter(FakePerpMarket(), cfg)
    state = PositionState()
    adapter.bind(state)

    size, entry_px, _spent, _ref = _run(adapter.enter())
    state.size, state.entry_price = size, entry_px
    _run(adapter.on_open(state))

    entry = find_order(state.orders, "1")
    assert entry is not None
    assert entry.role == OrderRole.ENTRY
    assert entry.side == "buy"
    assert entry.status == OrderStatus.FILLED
    assert entry.requested_qty == Decimal("0.2")  # 20 / 100
    assert entry.requested_unit == "base"
    assert entry.cumulative_filled_base_qty == Decimal("0.2")
    assert entry.cumulative_filled_quote_qty == Decimal("20")

    prot = [o for o in state.orders if o.role == OrderRole.PROTECTION]
    assert len(prot) == 2  # tp + sl
    assert all(o.status == OrderStatus.OPEN for o in prot)
    assert all(o.side == "sell" for o in prot)  # closing a LONG sells
    assert all(o.cumulative_filled_base_qty == 0 for o in prot)


# -- (b) resting perp limit: absolute cumulative poll updates -----------------


class FakePerpLimit:
    """Order-kind resting limit whose order_status response is scripted."""

    def __init__(self):
        self.status_resp = {"order": {"order": {}, "status": "open"}}

    async def set_leverage(self, coin, lev, cross):
        pass

    async def place_limit(self, coin, is_buy, size, price, tif="Gtc", reduce_only=False):
        return HlAck(oid=77, status="resting")

    async def order_status(self, oid):
        return self.status_resp


def _hl_status(orig, remaining, st):
    return {"order": {"order": {"origSz": orig, "sz": remaining, "limitPx": "100"},
                      "status": st}}


def test_perp_resting_limit_polls_absolute_cumulative():
    cfg = OrderPerpConfig(
        coin="ETH", side="LONG", notional_quote=Decimal("20"),
        order_type="limit", limit_px=Decimal("100"), notify_trades=False,
    )
    fake = FakePerpLimit()
    adapter = PerpAdapter(fake, cfg)
    state = OrderState()
    adapter.bind(state)

    _run(adapter.place(state))
    entry = find_order(state.orders, "77")
    assert entry is not None
    assert entry.role == OrderRole.TRADE
    assert entry.status == OrderStatus.OPEN
    assert entry.requested_qty == Decimal("0.2")  # 20 / 100
    assert entry.cumulative_filled_base_qty == 0
    assert state.state == OrderStates.RESTING

    # Partial fill while resting: absolute (origSz - sz), still OPEN.
    fake.status_resp = _hl_status("0.2", "0.1", "open")
    _run(adapter.poll(state))
    assert entry.status == OrderStatus.OPEN
    assert entry.cumulative_filled_base_qty == Decimal("0.1")
    assert entry.cumulative_filled_quote_qty == Decimal("10")
    assert state.state == OrderStates.RESTING

    # The same venue snapshot applied twice must not double-count.
    _run(adapter.poll(state))
    assert entry.cumulative_filled_base_qty == Decimal("0.1")
    assert entry.cumulative_filled_quote_qty == Decimal("10")

    # Full fill: terminal FILLED with the venue's final totals.
    fake.status_resp = _hl_status("0.2", "0", "filled")
    _run(adapter.poll(state))
    assert entry.status == OrderStatus.FILLED
    assert entry.cumulative_filled_base_qty == Decimal("0.2")
    assert entry.cumulative_filled_quote_qty == Decimal("20")
    assert state.state == OrderStates.DONE


# -- (c) polymarket limit: OPEN -> CANCEL_PENDING -> CANCELED -----------------


class FakePmBook:
    def __init__(self):
        self.open: dict = {}
        self.shares = Decimal("0")
        self.cancelled: list = []

    async def shares_balance(self, token_id):
        return self.shares

    async def place_limit(self, token_id, side, price, size, order_type="GTC"):
        self.open["0xabc"] = Decimal(str(size))
        return PmAck("0xabc", "live", True)

    async def orders(self, market=None, asset_id=None):
        return [{"id": oid} for oid in self.open]

    async def cancel(self, order_id):
        self.cancelled.append(order_id)
        self.open.pop(order_id, None)


def test_polymarket_limit_place_cancel_lifecycle():
    cfg = OrderPredConfig(
        market="tok", amount_quote=Decimal("10"), order_type="limit",
        limit_px=Decimal("0.5"), notify_trades=False,
    )
    fake = FakePmBook()
    adapter = PolymarketPredAdapter(fake, cfg)
    state = OrderState()
    adapter.bind(state)

    _run(adapter.place(state))
    entry = find_order(state.orders, "0xabc")
    assert entry is not None
    assert entry.role == OrderRole.TRADE
    assert entry.side == "buy"
    assert entry.status == OrderStatus.OPEN
    assert entry.requested_qty == Decimal("20")  # 10 USDC / 0.5
    assert entry.requested_unit == "base"

    # CANCEL_PENDING lands before the venue cancel is issued.
    _run(adapter.cancel(state))
    assert entry.status == OrderStatus.CANCEL_PENDING
    assert fake.cancelled == ["0xabc"]

    # Poll confirms: left the book with no shares delta -> CANCELED, no fills.
    _run(adapter.poll(state))
    assert entry.status == OrderStatus.CANCELED
    assert entry.cumulative_filled_base_qty == 0
    assert entry.cumulative_filled_quote_qty == 0


# -- (d) jupiter swap: quote-unit request, FILLED with SOL fee ----------------


class FakeJupiterGateway:
    async def quote_swap(self, **kw):
        return {"price": 100.0}

    async def execute_swap(self, **kw):
        return {
            "signature": "sig-1",
            "status": 1,
            "data": {"amountIn": kw["amount"], "amountOut": kw["amount"] * 100,
                     "fee": 0.0001},
        }

    async def get_balances(self, chain, network, address, tokens=None):
        return {t: 0.0 for t in (tokens or [])}


def test_jupiter_entry_records_quote_request_and_sol_fee():
    cfg = PositionSpotConfig(
        chain_network="solana-mainnet-beta", wallet_address="wallet",
        base_token="MemeMint111", quote_token="SOL",
        amount_quote=Decimal("0.02"), notify_trades=False,
    )
    adapter = SpotAdapter(FakeJupiterGateway(), cfg)
    state = PositionState()
    adapter.bind(state)

    _run(adapter.enter())

    entry = find_order(state.orders, "sig-1")
    assert entry is not None
    assert entry.role == OrderRole.ENTRY
    assert entry.side == "buy"
    assert entry.status == OrderStatus.FILLED
    assert entry.requested_qty == Decimal("0.02")
    assert entry.requested_unit == "quote"
    assert entry.cumulative_filled_base_qty == Decimal("2")  # 0.02 * 100
    assert entry.cumulative_filled_quote_qty == Decimal("0.02")
    assert entry.fees_by_asset == {"SOL": Decimal("0.0001")}
