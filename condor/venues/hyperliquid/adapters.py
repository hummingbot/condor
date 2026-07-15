"""Hyperliquid instrument adapters — perp (HyperliquidClient) and pred
(HyperliquidOutcomeClient, HIP-4 outcome shares). Moved verbatim from
``condor/executors/adapters.py`` (§6.2b venue packages); the venue-agnostic
adapter interface (``InstrumentAdapter``) and the shared spot/pred bases stay
in core — this module implements that boundary for Hyperliquid.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from condor.executors.adapters import InstrumentAdapter, _dec, _PredAdapter
from condor.executors.orders import OrderRole, OrderStatus, find_order

logger = logging.getLogger(__name__)


def _order_is_open(status: Any) -> bool:
    """True if an order-status response indicates a still-resting order."""
    if not isinstance(status, dict):
        return False
    order = status.get("order") or {}
    inner = order.get("order") if isinstance(order.get("order"), dict) else order
    state = (order.get("status") or inner.get("status") or "").lower()
    return state in ("open", "resting", "triggered")


class PerpAdapter(InstrumentAdapter):
    """Leveraged perpetual on Hyperliquid — triple barrier + liquidation guard +
    optional native reduce-only TP/SL triggers. The connector is a HyperliquidClient."""

    def __init__(self, connector, cfg):
        super().__init__(connector, cfg)
        self.hl = connector
        self._pos = None          # cached each tick via vanished()
        self._entry_oid = None    # guards against re-placing on OPENING retry

    @property
    def is_long(self) -> bool:
        return self.cfg.side == "LONG"

    def pnl_pct(self, entry: Decimal, mark: Decimal) -> Decimal:
        return (mark - entry) / entry if self.is_long else (entry - mark) / entry

    async def enter(self):
        cfg, hl = self.cfg, self.hl
        entry_side = "buy" if self.is_long else "sell"
        if self._entry_oid is None:
            await hl.set_leverage(cfg.coin, cfg.leverage, cfg.cross_margin)
            entry_ref = cfg.limit_px if (cfg.entry == "limit" and cfg.limit_px) else await hl.mid_price(cfg.coin)
            size = cfg.notional_quote / entry_ref
            if cfg.entry == "limit":
                if cfg.limit_px is None:
                    raise ValueError("entry='limit' requires limit_px")
                ack = await hl.place_limit(cfg.coin, self.is_long, size, cfg.limit_px)
                if ack.status == "error":
                    raise RuntimeError(f"limit entry rejected: {ack.detail}")
                self._entry_oid = ack.oid
                # Acknowledged (resting or crossed): lands OPEN; fills are
                # stamped absolutely from the venue's per-order view below.
                self.record_landed_order(
                    self.state,
                    venue_order_id=ack.oid,
                    role=self._leg_role(),
                    side=entry_side,
                    requested_qty=size,
                    requested_unit="base",
                    status=OrderStatus.OPEN,
                )
            else:
                fill = await hl.market_open(cfg.coin, self.is_long, size, cfg.slippage_pct)
                self._entry_oid = fill.oid
                self.record_landed_order(
                    self.state,
                    venue_order_id=fill.oid,
                    role=self._leg_role(),
                    side=entry_side,
                    requested_qty=size,
                    requested_unit="base",
                    status=OrderStatus.FILLED,
                    filled_base=fill.size,
                    filled_quote=fill.size * fill.avg_px,
                )
        pos = await hl.position(cfg.coin)
        if pos is None:
            # limit still resting — check the order, keep waiting (stay OPENING).
            if self._entry_oid is not None:
                status = await hl.order_status(self._entry_oid)
                if _order_is_open(status):
                    self._stamp_perp_order(self.state, self._entry_oid, status)
                    self.pending = True
                    return Decimal("0"), None, cfg.notional_quote, self._entry_oid
            raise RuntimeError("entry order neither filled nor resting — verify on venue")
        self.pending = False
        self._pos = pos
        await self._stamp_live_entry_order()
        return pos.size, pos.entry_px, cfg.notional_quote, self._entry_oid

    async def _stamp_live_entry_order(self) -> None:
        """A limit entry became a live position: stamp the landed entry from
        the venue's per-order view (absolute cumulative — position size can't
        be used, the account may hold unrelated inventory in the same coin)."""
        if self._entry_oid is None:
            return
        entry = find_order(self.state.orders, str(self._entry_oid))
        if entry is None or not entry.status.is_live:
            return  # market entry already landed FILLED from its Fill
        try:
            status = await self.hl.order_status(self._entry_oid)
        except Exception:
            logger.warning("perp: could not read entry order %s to stamp fills", self._entry_oid)
            return
        self._stamp_perp_order(self.state, self._entry_oid, status)

    async def on_open(self, state) -> None:
        pos = self._pos
        if pos is not None:
            state.extra["leverage"] = pos.leverage or self.cfg.leverage
            if pos.liquidation_px is not None:
                state.extra["liquidation_px"] = str(pos.liquidation_px)
        if self.cfg.native_triggers:
            await self._place_native_triggers(state)

    async def _place_native_triggers(self, state) -> None:
        cfg = self.cfg
        entry = state.entry_price
        if entry is None:
            return
        close_is_buy = not self.is_long  # closing a LONG sells; a SHORT buys
        if cfg.take_profit_pct is not None and not state.extra.get("tp_oid"):
            tp_px = entry * (1 + cfg.take_profit_pct) if self.is_long else entry * (1 - cfg.take_profit_pct)
            ack = await self.hl.place_trigger(cfg.coin, close_is_buy, state.size, tp_px, "tp")
            state.extra["tp_oid"] = ack.oid
            if ack.status == "error":
                logger.warning("perp native TP trigger rejected: %s", ack.detail)
            elif ack.oid is not None:
                self._record_trigger(state, ack.oid, close_is_buy)
        if cfg.stop_loss_pct is not None and not state.extra.get("sl_oid"):
            sl_px = entry * (1 - cfg.stop_loss_pct) if self.is_long else entry * (1 + cfg.stop_loss_pct)
            ack = await self.hl.place_trigger(cfg.coin, close_is_buy, state.size, sl_px, "sl")
            state.extra["sl_oid"] = ack.oid
            if ack.status == "error":
                logger.warning("perp native SL trigger rejected: %s", ack.detail)
            elif ack.oid is not None:
                self._record_trigger(state, ack.oid, close_is_buy)

    def _record_trigger(self, state, oid: int, close_is_buy: bool) -> None:
        self.record_landed_order(
            state,
            venue_order_id=oid,
            role=OrderRole.PROTECTION,
            side="buy" if close_is_buy else "sell",
            requested_qty=state.size,
            requested_unit="base",
            status=OrderStatus.OPEN,
        )

    async def reconcile_live(self, state) -> None:
        """Refresh venue metadata and restore any missing daemon-down guards."""
        self._pos = await self.hl.position(self.cfg.coin)
        if self._pos is not None:
            state.extra["leverage"] = self._pos.leverage or self.cfg.leverage
            if self._pos.liquidation_px is not None:
                state.extra["liquidation_px"] = str(self._pos.liquidation_px)
        if self.cfg.native_triggers:
            await self._place_native_triggers(state)

    async def mark_price(self) -> Decimal:
        return await self.hl.mid_price(self.cfg.coin)

    async def vanished(self) -> bool:
        # Fetch the position once per tick; extra_barriers reuses the cache.
        self._pos = await self.hl.position(self.cfg.coin)
        return self._pos is None

    def extra_barriers(self, state) -> Optional[str]:
        pos = self._pos
        if pos is not None:
            state.extra["unrealized_pnl"] = str(pos.unrealized_pnl)
            if pos.liquidation_px is not None:
                state.extra["liquidation_px"] = str(pos.liquidation_px)
        liq = _dec(state.extra["liquidation_px"]) if state.extra.get("liquidation_px") else None
        mark = state.mark_price
        # Liquidation guard — highest priority.
        if liq is not None and mark and mark > 0:
            dist = abs(mark - liq) / mark
            if dist <= self.cfg.liquidation_guard_pct:
                return "liquidation_guard"
        return None

    async def close(self, size: Decimal):
        fill = await self.hl.market_close(self.cfg.coin, size)
        self.record_landed_order(
            self.state,
            venue_order_id=fill.oid,
            role=OrderRole.EXIT,
            side="sell" if self.is_long else "buy",
            requested_qty=size,
            requested_unit="base",
            status=OrderStatus.FILLED,
            filled_base=fill.size,
            filled_quote=fill.size * fill.avg_px,
        )
        return fill.avg_px, None, fill.oid

    async def on_close(self, state) -> None:
        for key in ("tp_oid", "sl_oid"):
            oid = state.extra.get(key)
            if oid is None:
                continue
            # CANCEL_PENDING lands BEFORE the venue call so a crash mid-cancel
            # is visible; a cancel that raises leaves it pending (settle() can
            # still attribute fills if the trigger actually fired).
            self.update_landed_order(state, oid, status=OrderStatus.CANCEL_PENDING)
            try:
                await self.hl.cancel(self.cfg.coin, oid)
            except Exception:
                logger.warning("perp cancel trigger oid=%s failed (may already be gone)", oid)
            else:
                self.update_landed_order(state, oid, status=OrderStatus.CANCELED)
        state.extra["tp_oid"] = state.extra["sl_oid"] = None

    async def settle(self, state) -> None:
        """Sum realized pnl + fees for this coin from the venue's fill history."""
        try:
            since = int((state.opened_at or 0) * 1000) or None
            fills = await self.hl.fills(since_ms=since)
        except Exception:
            logger.warning("perp: could not fetch fills for settlement", exc_info=True)
            return
        realized = Decimal("0")
        fee = Decimal("0")
        for f in fills:
            if f.get("coin") != self.cfg.coin:
                continue
            realized += _dec(f.get("closedPnl", "0"))
            fee += _dec(f.get("fee", "0"))
            if str(f.get("dir", "")).lower().startswith("close") and f.get("px"):
                state.exit_price = _dec(f["px"])
            self._accumulate_fill(state, f)
        state.extra["realized_pnl"] = str(realized - fee)
        state.extra["close_fee"] = str(fee)

    def _accumulate_fill(self, state, f: dict) -> None:
        """Fold one venue fill DELTA into its landed order when attributable by
        oid; skipped otherwise. Cursor-gated ("time:tid" persisted on the entry)
        so a replayed settle never double-counts. Quantities only accumulate
        into entries never absolutely stamped (still live — e.g. a fired
        trigger); fees (USDC on HL perp) always accumulate."""
        oid = f.get("oid")
        entry = find_order(state.orders, str(oid)) if oid is not None else None
        if entry is None:
            return  # not attributable to one of our landed orders — skip
        try:
            key = (int(f.get("time") or 0), int(f.get("tid") or 0))
        except (TypeError, ValueError):
            key = (0, 0)
        if entry.cursor:
            try:
                t, tid = entry.cursor.split(":")
                if key <= (int(t), int(tid)):
                    return  # already counted this fill
            except ValueError:
                pass
        kwargs: dict = {"cursor": f"{key[0]}:{key[1]}"}
        fill_fee = _dec(f.get("fee", "0"))
        if fill_fee:
            fees = dict(entry.fees_by_asset)
            fees["USDC"] = fees.get("USDC", Decimal("0")) + fill_fee
            kwargs["fees_by_asset"] = fees
        if entry.status.is_live:
            filled_base = entry.cumulative_filled_base_qty + _dec(f.get("sz", "0"))
            kwargs["filled_base"] = filled_base
            kwargs["filled_quote"] = (
                entry.cumulative_filled_quote_qty + _dec(f.get("sz", "0")) * _dec(f.get("px", "0"))
            )
            if entry.requested_qty > 0 and filled_base >= entry.requested_qty:
                kwargs["status"] = OrderStatus.FILLED  # e.g. the trigger fired
        entry.apply_absolute(**kwargs)

    async def held_size(self) -> Decimal:
        pos = await self.hl.position(self.cfg.coin)
        return pos.size if pos else Decimal("0")

    async def venue_entry_price(self) -> Optional[Decimal]:
        pos = await self.hl.position(self.cfg.coin)
        return pos.entry_px if pos else None

    def net_pnl(self, state) -> Decimal:
        r = state.extra.get("realized_pnl")
        if r is not None:
            return _dec(r)
        u = state.extra.get("unrealized_pnl")
        if u is not None:
            return _dec(u)
        return Decimal("0")

    def recovery_ids(self, state) -> tuple:
        return (str(state.extra.get("tp_oid")), str(state.extra.get("sl_oid")))

    def info(self, state) -> dict:
        e = state.extra
        return {
            "venue": self.cfg.venue,
            "coin": self.cfg.coin,
            "side": self.cfg.side,
            "leverage": e.get("leverage"),
            "unrealized_pnl": float(_dec(e["unrealized_pnl"])) if e.get("unrealized_pnl") is not None else None,
            "liquidation_px": float(_dec(e["liquidation_px"])) if e.get("liquidation_px") else None,
            "realized_pnl": float(_dec(e["realized_pnl"])) if e.get("realized_pnl") is not None else None,
            "entry_oid": state.open_ref,
            "close_oid": state.close_ref,
        }

    def open_note(self, state) -> str:
        cfg = self.cfg
        tp = f"+{cfg.take_profit_pct * 100:.1f}%" if cfg.take_profit_pct is not None else "—"
        sl = f"-{cfg.stop_loss_pct * 100:.1f}%" if cfg.stop_loss_pct is not None else "—"
        lev = state.extra.get("leverage")
        return (
            f"🟢 {cfg.side} {cfg.coin} {state.size} @ {state.entry_price:.6g} "
            f"({lev}x, TP {tp} / SL {sl})"
        )

    def close_note(self, state, pnl) -> str:
        cfg = self.cfg
        emoji = "🔴" if pnl < 0 else "🟢"
        return f"{emoji} Closed {cfg.side} {cfg.coin} ({state.close_type}) — {pnl:+.4g} USDC"

    # -- order kind (open a leveraged position and stop) ---------------------

    async def place(self, state) -> None:
        from condor.executors.order import OrderStates

        cfg, hl = self.cfg, self.hl
        side = "buy" if self.is_long else "sell"
        entry_ref = cfg.limit_px if (cfg.order_type == "limit" and cfg.limit_px) else await hl.mid_price(cfg.coin)
        size = cfg.notional_quote / entry_ref
        await hl.set_leverage(cfg.coin, cfg.leverage, cfg.cross_margin)
        if cfg.order_type == "limit":
            if cfg.limit_px is None:
                raise ValueError("order_type='limit' requires limit_px")
            ack = await hl.place_limit(cfg.coin, self.is_long, size, cfg.limit_px)
            if ack.status == "error":
                raise RuntimeError(f"limit order rejected: {ack.detail}")
            state.open_ref = str(ack.oid)
            state.size = size
            state.entry_price = cfg.limit_px
            state.state = OrderStates.RESTING if ack.status == "resting" else OrderStates.DONE
            crossed = ack.status == "filled"
            self.record_landed_order(
                state,
                venue_order_id=ack.oid,
                role=OrderRole.TRADE,
                side=side,
                requested_qty=size,
                requested_unit="base",
                status=OrderStatus.FILLED if crossed else OrderStatus.OPEN,
                filled_base=size if crossed else Decimal(0),
                filled_quote=size * cfg.limit_px if crossed else Decimal(0),
            )
        else:
            fill = await hl.market_open(cfg.coin, self.is_long, size, cfg.slippage_pct)
            state.open_ref = str(fill.oid)
            state.size = fill.size
            state.entry_price = fill.avg_px
            state.state = OrderStates.DONE
            self.record_landed_order(
                state,
                venue_order_id=fill.oid,
                role=OrderRole.TRADE,
                side=side,
                requested_qty=size,
                requested_unit="base",
                status=OrderStatus.FILLED,
                filled_base=fill.size,
                filled_quote=fill.size * fill.avg_px,
            )

    async def poll(self, state) -> None:
        from condor.executors.order import OrderStates

        status = await self.hl.order_status(int(state.open_ref))
        # Absolute cumulative stamp (partial fills while open; terminal state
        # + final fills once the order left the book).
        self._stamp_perp_order(state, state.open_ref, status)
        if _order_is_open(status):
            return
        # Terminal: distinguish a fill from a cancel so size and labels are honest.
        order = status.get("order") if isinstance(status, dict) else {}
        st = ((order or {}).get("status") or "").lower()
        if st in ("canceled", "cancelled", "rejected", "margincanceled"):
            state.state = OrderStates.FAILED
            state.extra["error"] = f"perp limit {st}"
        else:
            state.state = OrderStates.DONE

    async def cancel(self, state) -> None:
        if not state.open_ref:
            return
        # CANCEL_PENDING lands BEFORE the venue call so a crash mid-cancel is visible.
        self.update_landed_order(state, state.open_ref, status=OrderStatus.CANCEL_PENDING)
        await self.hl.cancel(self.cfg.coin, int(state.open_ref))
        try:
            status = await self.hl.order_status(int(state.open_ref))
        except Exception:
            logger.warning("perp: could not confirm cancel of oid=%s", state.open_ref)
            return
        self._stamp_perp_order(state, state.open_ref, status)

    def _stamp_perp_order(self, state, oid, status: Any) -> None:
        """Stamp the landed entry for ``oid`` from a venue order-status response:
        absolute cumulative fill (origSz − remaining sz) and, when the order left
        the book, the terminal status (idempotent — safe to apply twice)."""
        if not isinstance(status, dict):
            return
        order = status.get("order") or {}
        inner = order.get("order") if isinstance(order.get("order"), dict) else order
        st = (order.get("status") or inner.get("status") or "").lower()
        kwargs: dict = {}
        orig = _dec(inner.get("origSz", "0"))
        if orig > 0:
            filled = max(Decimal("0"), orig - _dec(inner.get("sz", "0")))
            kwargs["filled_base"] = filled
            kwargs["filled_quote"] = filled * _dec(inner.get("limitPx", "0"))
        if st in ("canceled", "cancelled", "rejected", "margincanceled"):
            kwargs["status"] = OrderStatus.CANCELED
        elif st == "filled":
            kwargs["status"] = OrderStatus.FILLED
        if kwargs:
            self.update_landed_order(state, str(oid), **kwargs)


class HyperliquidPredAdapter(_PredAdapter):
    """Buy/sell HIP-4 outcome Yes/No shares (spot-like, priced [0,1]).
    ``market`` is the outcome id or name; LONG=Yes, SHORT=No."""

    def __init__(self, connector, cfg):
        super().__init__(connector, cfg)
        self.client = connector
        self._outcome: Optional[int] = None
        self._side: Optional[int] = None

    async def _resolve(self) -> tuple[int, int]:
        if self._outcome is None:
            outcome = await self.client.find_outcome(self.cfg.market)
            self._outcome = outcome.id
            side_name = "Yes" if self.cfg.position == "LONG" else "No"
            self._side = self.client.side_index(outcome, side_name)
        return self._outcome, self._side

    async def mark_price(self) -> Decimal:
        oc, sd = await self._resolve()
        return await self.client.price(oc, sd, "mid")

    async def enter(self):
        cfg = self.cfg
        oc, sd = await self._resolve()
        ask = await self.client.price(oc, sd, "ask")
        size = Decimal(int(cfg.amount_quote / ask))  # whole shares
        if size <= 0:
            raise RuntimeError(f"amount {cfg.amount_quote} too small at ask {ask}")
        before = await self.client.shares(oc, sd)
        ack = await self.client.marketable_buy(oc, sd, size, cfg.slippage_pct)
        if ack.status == "error":
            raise RuntimeError(f"outcome buy rejected: {ack.detail}")
        got = await self.client.shares(oc, sd) - before
        if ack.oid is not None:
            # IOC crossed: the measured shares delta IS this order's fill.
            self.record_landed_order(
                self.state,
                venue_order_id=ack.oid,
                role=self._leg_role(),
                side="buy",
                requested_qty=size,
                requested_unit="base",
                status=OrderStatus.FILLED,
                filled_base=got if got > 0 else Decimal(0),
                filled_quote=cfg.amount_quote if got > 0 else Decimal(0),
            )
        entry_px = (cfg.amount_quote / got) if got > 0 else ask
        return got, entry_px, cfg.amount_quote, ack.oid

    async def close(self, size: Decimal):
        oc, sd = await self._resolve()
        before = await self.client.usdc_balance()
        ack = await self.client.marketable_sell(oc, sd, size, self.cfg.slippage_pct)
        proceeds = await self.client.usdc_balance() - before
        if ack.oid is not None:
            self.record_landed_order(
                self.state,
                venue_order_id=ack.oid,
                role=OrderRole.EXIT,
                side="sell",
                requested_qty=size,
                requested_unit="base",
                status=OrderStatus.FILLED,
                filled_base=size,
                filled_quote=proceeds,
            )
        exit_px = (proceeds / size) if size > 0 else await self.client.price(oc, sd, "bid")
        return exit_px, proceeds, ack.oid

    async def held_size(self) -> Decimal:
        oc, sd = await self._resolve()
        return await self.client.shares(oc, sd)
