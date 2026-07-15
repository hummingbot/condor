"""Polymarket instrument adapter — pred (CLOB outcome tokens). Moved verbatim
from ``condor/executors/adapters.py`` (§6.2b venue packages)."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from condor.executors.adapters import _dec, _PredAdapter
from condor.executors.orders import OrderRole, OrderStatus

logger = logging.getLogger(__name__)


def _pm_order_open(open_orders: Any, order_id: Optional[str]) -> bool:
    """True if ``order_id`` is still present in Polymarket's open-orders list.
    Tolerant of dict or object rows and the id-field naming the client returns."""
    if not order_id:
        return False
    for o in (open_orders or []):
        if isinstance(o, dict):
            oid = o.get("id") or o.get("orderID") or o.get("order_id")
        else:
            oid = getattr(o, "id", None) or getattr(o, "orderID", None)
        if oid == order_id:
            return True
    return False


class PolymarketPredAdapter(_PredAdapter):
    """Buy the outcome token to open, sell it to close. Price is the CLOB midpoint
    on [0, 1]; size is shares, measured from the balance delta (the real fill)."""

    def __init__(self, connector, cfg):
        super().__init__(connector, cfg)
        self.client = connector

    async def mark_price(self) -> Decimal:
        return await self.client.midpoint(self.cfg.market)

    async def enter(self):
        cfg = self.cfg
        before = await self.client.shares_balance(cfg.market)
        ack = await self.client.place_market(cfg.market, "BUY", cfg.amount_quote)
        if not ack.success and ack.order_id is None:
            raise RuntimeError(f"polymarket buy rejected: {ack.detail}")
        after = await self.client.shares_balance(cfg.market)
        size = after - before
        if ack.order_id:
            # FOK: the measured shares delta IS this order's cumulative fill.
            self.record_landed_order(
                self.state,
                venue_order_id=ack.order_id,
                role=self._leg_role(),
                side="buy",
                requested_qty=cfg.amount_quote,
                requested_unit="quote",
                status=OrderStatus.FILLED,
                filled_base=size,
                filled_quote=cfg.amount_quote if size > 0 else Decimal(0),
            )
        entry_px = (cfg.amount_quote / size) if size > 0 else Decimal("0")
        return size, entry_px, cfg.amount_quote, ack.order_id

    async def close(self, size: Decimal):
        before = await self.client.usdc_balance()
        ack = await self.client.place_market(self.cfg.market, "SELL", size)
        after = await self.client.usdc_balance()
        proceeds = after - before
        if ack.order_id:
            self.record_landed_order(
                self.state,
                venue_order_id=ack.order_id,
                role=OrderRole.EXIT,
                side="sell",
                requested_qty=size,
                requested_unit="base",
                status=OrderStatus.FILLED,
                filled_base=size,
                filled_quote=proceeds,
            )
        exit_px = (proceeds / size) if size > 0 else Decimal("0")
        return exit_px, proceeds, ack.order_id

    async def held_size(self) -> Decimal:
        return await self.client.shares_balance(self.cfg.market)

    # -- order kind: resting GTC limit buy to accumulate at an attractive price -

    async def place(self, state) -> None:
        from condor.executors.order import OrderStates

        cfg = self.cfg
        if getattr(cfg, "order_type", "market") != "limit":
            return await super().place(state)  # marketable buy (existing path)
        if cfg.limit_px is None:
            raise ValueError("order_pred order_type='limit' requires limit_px")
        size = cfg.amount_quote / cfg.limit_px  # shares this budget buys at the limit
        # Baseline the held shares so poll() can measure the real fill on the delta.
        state.extra["shares_before"] = str(await self.client.shares_balance(cfg.market))
        ack = await self.client.place_limit(cfg.market, "BUY", cfg.limit_px, size, "GTC")
        if not ack.success and ack.order_id is None:
            raise RuntimeError(f"polymarket limit rejected: {ack.detail}")
        state.open_ref = ack.order_id
        state.size = size
        state.entry_price = cfg.limit_px
        # "matched" = crossed on entry (terminal); anything else rests on the book.
        state.state = OrderStates.DONE if ack.status == "matched" else OrderStates.RESTING
        if ack.order_id:
            matched = ack.status == "matched"
            self.record_landed_order(
                state,
                venue_order_id=ack.order_id,
                role=OrderRole.TRADE,
                side="buy",
                requested_qty=size,
                requested_unit="base",
                status=OrderStatus.FILLED if matched else OrderStatus.OPEN,
                filled_base=size if matched else Decimal(0),
                filled_quote=size * cfg.limit_px if matched else Decimal(0),
            )

    async def poll(self, state) -> None:
        from condor.executors.order import OrderStates

        cfg = self.cfg
        open_orders = await self.client.orders(asset_id=cfg.market)
        if _pm_order_open(open_orders, state.open_ref):
            return  # still resting on the book
        # Left the book: filled (fully or partially) or cancelled/expired. The
        # held-shares delta is the truth for how much actually filled.
        after = await self.client.shares_balance(cfg.market)
        filled = after - _dec(state.extra.get("shares_before"))
        if filled > 0:
            state.size = filled
            state.entry_price = cfg.limit_px  # bought at the limit
            state.state = OrderStates.DONE
            self.update_landed_order(
                state, state.open_ref,
                status=OrderStatus.FILLED,
                filled_base=filled,
                filled_quote=filled * cfg.limit_px,
            )
        else:
            state.state = OrderStates.FAILED
            state.extra["error"] = "limit order left the book unfilled (cancelled/expired)"
            self.update_landed_order(state, state.open_ref, status=OrderStatus.CANCELED)

    async def cancel(self, state) -> None:
        if state.open_ref:
            # CANCEL_PENDING lands BEFORE the venue call; the poll site confirms
            # CANCELED (or FILLED, if a fill raced the cancel) from the book.
            self.update_landed_order(state, state.open_ref, status=OrderStatus.CANCEL_PENDING)
            await self.client.cancel(state.open_ref)
