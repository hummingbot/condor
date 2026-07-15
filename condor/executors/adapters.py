"""Instrument adapters — one per (instrument, venue), wrapping a venue connector.

The kind picks the executor class (OrderExecutor / PositionExecutor); the
(instrument, venue) pair picks the adapter here. Each adapter normalizes a
venue's connector to a single interface so the executors never branch on
instrument. All the venue-specific logic (moved verbatim from the retired
swap/perp/prediction executors) lives here.

Adapters are **bound** to the executor's live state object (``adapter.bind``),
so methods whose signature carries no ``state`` (``mark_price``, ``enter``,
``close``, ``held_size``, ``vanished``) can still read persisted fields
(e.g. spot's execution-aware mark uses the held ``size``). Instrument-specific
persisted fields live in ``state.extra``.

Interface (position kind):
    is_long                                          LONG vs SHORT (spot: True)
    pnl_pct(entry, mark) -> Decimal                  signed pnl fraction
    async enter() -> (size, entry_px, amount_spent, ref)   open the leg
    async mark_price() -> Decimal
    async close(size) -> (exit_px, proceeds|None, ref)
    async held_size() -> Decimal                     venue-held base (reconcile)
    async on_open(state)                             hook: perp native TP/SL
    async on_close(state)                            hook: perp cancel triggers
    extra_barriers(state) -> close_type|None         perp liq-guard; pred resolve
    async settle(state)                              perp funding/realized fills
    async vanished() -> bool                         venue shows flat
    net_pnl(state) -> Decimal
    info(state) -> dict                              instrument custom_info fields
    recovery_ids(state) -> tuple                     durable ids for the recovery key
    open_note(state) / close_note(state, pnl) -> str notification text
Interface (order kind):
    async place(state)                               place the entering order
    async poll(state)                                drive a resting/in-flight order
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


class InstrumentAdapter:
    is_long: bool = True

    def __init__(self, connector: Any, cfg: Any):
        self.c = connector
        self.cfg = cfg
        self.state: Any = None
        # Set by an order-kind adapter when the entering order is still in flight
        # (a resting CLOB limit): the executor stays OPENING and retries.
        self.pending: bool = False

    def bind(self, state: Any) -> None:
        """Give the adapter a handle to the executor's live state object."""
        self.state = state

    # -- position kind -------------------------------------------------------

    def pnl_pct(self, entry: Decimal, mark: Decimal) -> Decimal:
        raise NotImplementedError

    async def enter(self):
        raise NotImplementedError

    async def prepare_open(self, state) -> None:
        """Persist any venue baseline needed to attribute a later balance delta.
        Called before the executor records OPENING and submits financial intent."""
        return None

    async def mark_price(self) -> Decimal:
        raise NotImplementedError

    async def close(self, size: Decimal):
        raise NotImplementedError

    async def held_size(self) -> Decimal:
        raise NotImplementedError

    def attributable_held_size(self, state, venue_size: Decimal) -> Optional[Decimal]:
        """Executor-owned portion of an account-wide venue balance.

        ``None`` means the adapter has no persisted attribution baseline.
        Account-scoped products such as perps override nothing because their
        venue position is already the relevant unit of truth.
        """
        return venue_size

    async def venue_entry_price(self) -> Optional[Decimal]:
        """The venue's own entry price for the live position, when it keeps one
        (perp). None when the venue can't report it (spot/pred) — the caller
        then falls back to a mark. Used only on crash-recovery adoption (#1)."""
        return None

    async def on_open(self, state) -> None:
        return None

    async def reconcile_live(self, state) -> None:
        """Restore venue-side protection for an adopted live position."""
        return None

    async def on_close(self, state) -> None:
        return None

    def extra_barriers(self, state) -> Optional[str]:
        return None

    async def settle(self, state) -> None:
        return None

    async def vanished(self) -> bool:
        return False

    def net_pnl(self, state) -> Decimal:
        raise NotImplementedError

    def info(self, state) -> dict:
        return {}

    def recovery_ids(self, state) -> tuple:
        return ()

    def open_note(self, state) -> str:
        return ""

    def close_note(self, state, pnl) -> str:
        return ""

    # -- order kind ----------------------------------------------------------

    async def place(self, state) -> None:
        raise NotImplementedError

    async def poll(self, state) -> None:
        return None

    async def cancel(self, state) -> None:
        """Cancel a resting order on the venue (order kind). No-op for venues
        with no cancelable resting order (immediate swaps). Overridden by
        adapters that place resting limits so a stop actually pulls the order."""
        return None


# -- spot (Solana/Jupiter OR Hyperliquid spot) --------------------------------


class SpotAdapter(InstrumentAdapter):
    """Long-only spot via the swap interface (quote_swap / execute_swap /
    get_balances). Works for both the Jupiter connector and HyperliquidSpotClient.

    Position (round-trip): enter SELLS the quote token for the base (ExactIn —
    routable; spends exactly the budget), monitors an execution-aware sell quote
    for the held size, market-closes by selling the base back.
    Order (single leg): a one-way swap using cfg.side/cfg.amount (BUY or SELL).
    """

    is_long = True

    def pnl_pct(self, entry: Decimal, mark: Decimal) -> Decimal:
        return mark / entry - 1

    def _add_fee(self, data: dict) -> None:
        prior = _dec(self.state.extra.get("tx_fee"))
        self.state.extra["tx_fee"] = str(prior + _dec((data or {}).get("fee", 0)))

    async def prepare_open(self, state) -> None:
        state.extra["held_before"] = str(await self.held_size())

    def attributable_held_size(self, state, venue_size: Decimal) -> Optional[Decimal]:
        before = state.extra.get("held_before")
        if before is None:
            return None
        return max(Decimal("0"), venue_size - _dec(before))

    async def enter(self):
        cfg = self.cfg
        result = await self.c.execute_swap(
            chain_network=cfg.chain_network,
            wallet_address=cfg.wallet_address,
            base_token=cfg.quote_token,   # sell SOL/USDC...
            quote_token=cfg.base_token,   # ...for the memecoin
            amount=float(cfg.amount_quote),
            side="SELL",
            slippage_pct=float(cfg.slippage_pct),
        )
        data = result.get("data") or {}
        ref = result["signature"]
        size = _dec(data.get("amountOut", 0))
        if size <= 0:
            raise RuntimeError(f"buy returned no base: {result}")
        # Cost basis = the exact quote we routed INTO the ExactIn SELL — that is
        # cfg.amount_quote by construction. Do NOT use the connector's amountIn:
        # for a SOL-input swap it reports the gross wallet debit (swap + refundable
        # rent + network fee), inflating entry_price ~10-20%. Rent is a refundable
        # deposit; the network fee is tracked separately in extra.tx_fee.
        amount_spent = cfg.amount_quote
        self._add_fee(data)
        entry_px = amount_spent / size
        return size, entry_px, amount_spent, ref

    async def mark_price(self) -> Decimal:
        cfg = self.cfg
        # Execution-aware: what our actual held size would fetch right now.
        quote = await self.c.quote_swap(
            chain_network=cfg.chain_network,
            base_token=cfg.base_token,
            quote_token=cfg.quote_token,
            amount=float(self.state.size),
            side="SELL",
        )
        return _dec(quote["price"])

    async def close(self, size: Decimal):
        cfg = self.cfg
        result = await self.c.execute_swap(
            chain_network=cfg.chain_network,
            wallet_address=cfg.wallet_address,
            base_token=cfg.base_token,
            quote_token=cfg.quote_token,
            amount=float(size),
            side="SELL",
            slippage_pct=float(cfg.slippage_pct),
        )
        data = result.get("data") or {}
        ref = result["signature"]
        proceeds = _dec(data.get("amountOut", 0))
        self._add_fee(data)
        exit_px = (proceeds / size) if (size > 0 and proceeds) else None
        return exit_px, proceeds, ref

    async def held_size(self) -> Decimal:
        cfg = self.cfg
        chain = cfg.chain_network.split("-", 1)[0]
        network = cfg.chain_network.split("-", 1)[1] if "-" in cfg.chain_network else cfg.chain_network
        bals = await self.c.get_balances(chain, network, cfg.wallet_address, [cfg.base_token])
        return _dec(bals.get(cfg.base_token))

    def net_pnl(self, state) -> Decimal:
        if state.proceeds is not None:
            return state.proceeds - state.amount_spent
        if state.mark_price is not None and state.size > 0:
            return state.size * state.mark_price - state.amount_spent
        return Decimal("0")

    def info(self, state) -> dict:
        return {
            "pair": f"{self.cfg.base_token}-{self.cfg.quote_token}",
            "tx_fee": float(_dec(state.extra.get("tx_fee"))),
        }

    def open_note(self, state) -> str:
        cfg = self.cfg
        tp = f"+{cfg.take_profit_pct * 100:.1f}%" if cfg.take_profit_pct is not None else "—"
        sl = f"-{cfg.stop_loss_pct * 100:.1f}%" if cfg.stop_loss_pct is not None else "—"
        ttl = f"{cfg.time_limit_s}s" if cfg.time_limit_s is not None else "—"
        return (
            f"🟢 Entered {cfg.base_token[:8]} — {cfg.amount_quote} {cfg.quote_token} "
            f"@ {state.entry_price:.6g} (TP {tp} / SL {sl} / TTL {ttl})"
        )

    def close_note(self, state, pnl) -> str:
        cfg = self.cfg
        pnl_pct = (pnl / state.amount_spent * 100) if state.amount_spent else Decimal("0")
        emoji = "🔴" if pnl < 0 else "🟢"
        return (
            f"{emoji} Exited {cfg.base_token[:8]} ({state.close_type}) — "
            f"{pnl:+.6g} {cfg.quote_token} ({pnl_pct:+.2f}%)"
        )

    # -- order kind (one-way swap: quote -> execute -> confirm) --------------

    async def place(self, state) -> None:
        from condor.executors.order import OrderStates

        cfg = self.cfg
        quote = await self.c.quote_swap(
            chain_network=cfg.chain_network,
            base_token=cfg.base_token,
            quote_token=cfg.quote_token,
            amount=float(cfg.amount),
            side=cfg.side,
            slippage_pct=float(cfg.slippage_pct),
        )
        state.entry_price = _dec(quote["price"])
        # The executor already persisted SUBMITTING before calling place(), so a
        # crash inside execute_swap leaves an explicit orphan (SUBMITTING, no ref).
        result = await self.c.execute_swap(
            chain_network=cfg.chain_network,
            wallet_address=cfg.wallet_address,
            base_token=cfg.base_token,
            quote_token=cfg.quote_token,
            amount=float(cfg.amount),
            side=cfg.side,
            slippage_pct=float(cfg.slippage_pct),
        )
        data = result.get("data") or {}
        state.open_ref = result["signature"]
        state.size = _dec(data.get("amountOut", cfg.amount))
        state.extra["amount_in"] = float(_dec(data.get("amountIn", 0)))
        state.extra["amount_out"] = float(_dec(data.get("amountOut", 0)))
        state.extra["fee"] = float(_dec(data.get("fee", 0)))
        state.state = OrderStates.DONE if result.get("status") == 1 else OrderStates.RESTING

    async def poll(self, state) -> None:
        from condor.executors.order import OrderStates

        cfg = self.cfg
        chain = cfg.chain_network.split("-", 1)[0]
        network = cfg.chain_network.split("-", 1)[1] if "-" in cfg.chain_network else cfg.chain_network
        poll = await self.c.poll_tx(chain, network, state.open_ref)
        ts = poll.get("txStatus")
        if ts == 1:
            state.state = OrderStates.DONE
        elif ts == -1:
            state.state = OrderStates.FAILED
            state.extra["error"] = f"swap tx failed on-chain: {state.open_ref}"


# -- perp (Hyperliquid) -------------------------------------------------------


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
            else:
                fill = await hl.market_open(cfg.coin, self.is_long, size, cfg.slippage_pct)
                self._entry_oid = fill.oid
        pos = await hl.position(cfg.coin)
        if pos is None:
            # limit still resting — check the order, keep waiting (stay OPENING).
            if self._entry_oid is not None:
                status = await hl.order_status(self._entry_oid)
                if _order_is_open(status):
                    self.pending = True
                    return Decimal("0"), None, cfg.notional_quote, self._entry_oid
            raise RuntimeError("entry order neither filled nor resting — verify on venue")
        self.pending = False
        self._pos = pos
        return pos.size, pos.entry_px, cfg.notional_quote, self._entry_oid

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
        if cfg.stop_loss_pct is not None and not state.extra.get("sl_oid"):
            sl_px = entry * (1 - cfg.stop_loss_pct) if self.is_long else entry * (1 + cfg.stop_loss_pct)
            ack = await self.hl.place_trigger(cfg.coin, close_is_buy, state.size, sl_px, "sl")
            state.extra["sl_oid"] = ack.oid
            if ack.status == "error":
                logger.warning("perp native SL trigger rejected: %s", ack.detail)

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
        return fill.avg_px, None, fill.oid

    async def on_close(self, state) -> None:
        for key in ("tp_oid", "sl_oid"):
            oid = state.extra.get(key)
            if oid is None:
                continue
            try:
                await self.hl.cancel(self.cfg.coin, oid)
            except Exception:
                logger.warning("perp cancel trigger oid=%s failed (may already be gone)", oid)
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
        state.extra["realized_pnl"] = str(realized - fee)
        state.extra["close_fee"] = str(fee)

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
        else:
            fill = await hl.market_open(cfg.coin, self.is_long, size, cfg.slippage_pct)
            state.open_ref = str(fill.oid)
            state.size = fill.size
            state.entry_price = fill.avg_px
            state.state = OrderStates.DONE

    async def poll(self, state) -> None:
        from condor.executors.order import OrderStates

        status = await self.hl.order_status(int(state.open_ref))
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
        if state.open_ref:
            await self.hl.cancel(self.cfg.coin, int(state.open_ref))


# -- prediction (outcome markets) --------------------------------------------


def _resolve_barrier(cfg, state) -> Optional[str]:
    """Outcome-settlement exits (probability venues): close once the probability
    is ~1 (win) or ~0 (loss). Checked before the SL/TP ladder."""
    mark = state.mark_price
    if mark is None:
        return None
    if cfg.resolve_win_price is not None and mark >= cfg.resolve_win_price:
        return "resolved_win"
    if cfg.resolve_loss_price is not None and mark <= cfg.resolve_loss_price:
        return "resolved_loss"
    return None


def _pred_net_pnl(adapter, state) -> Decimal:
    if state.proceeds is not None:
        return state.proceeds - state.amount_spent
    if state.mark_price is not None and state.size > 0 and state.entry_price is not None:
        # Both LONG (Yes) and SHORT (No) BUY and HOLD their outcome token, and
        # mark_price/entry_price are quoted in that same held token. So PnL is
        # always long-in-the-held-token — a rising held token is a profit for
        # both. (Betting "No" means holding the No token, not short-selling Yes.)
        return state.size * state.mark_price - state.amount_spent
    return Decimal("0")


def _pred_open_note(cfg, state) -> str:
    return (
        f"🟢 {cfg.position} {cfg.venue}:{cfg.market[:10]} — {cfg.amount_quote} "
        f"@ {state.entry_price:.4g} ({state.size:.4g} units)"
    )


def _pred_close_note(cfg, state, pnl) -> str:
    emoji = "🔴" if pnl < 0 else "🟢"
    return f"{emoji} Closed {cfg.venue}:{cfg.market[:10]} ({state.close_type}) — {pnl:+.4g} USDC"


class _PredAdapter(InstrumentAdapter):
    """Shared prediction-market behavior (barriers, pnl, notes, single-leg place)."""

    @property
    def is_long(self) -> bool:
        return self.cfg.position == "LONG"

    async def prepare_open(self, state) -> None:
        state.extra["held_before"] = str(await self.held_size())

    def attributable_held_size(self, state, venue_size: Decimal) -> Optional[Decimal]:
        before = state.extra.get("held_before")
        if before is None:
            return None
        return max(Decimal("0"), venue_size - _dec(before))

    def pnl_pct(self, entry: Decimal, mark: Decimal) -> Decimal:
        # LONG (Yes) and SHORT (No) both hold their outcome token; mark and entry
        # are the price of that held token, so a rising mark is a gain for both.
        return mark / entry - 1

    def extra_barriers(self, state) -> Optional[str]:
        return _resolve_barrier(self.cfg, state)

    async def vanished(self) -> bool:
        return (await self.held_size()) <= 0

    def net_pnl(self, state) -> Decimal:
        return _pred_net_pnl(self, state)

    def info(self, state) -> dict:
        return {"venue": self.cfg.venue, "market": self.cfg.market, "position": self.cfg.position}

    def open_note(self, state) -> str:
        return _pred_open_note(self.cfg, state)

    def close_note(self, state, pnl) -> str:
        return _pred_close_note(self.cfg, state, pnl)

    async def place(self, state) -> None:
        from condor.executors.order import OrderStates

        if getattr(self.cfg, "order_type", "market") == "limit":
            raise NotImplementedError(
                f"order_pred order_type='limit' is not supported on venue "
                f"{self.cfg.venue!r} (only polymarket rests limits)"
            )
        size, entry_px, _spent, ref = await self.enter()
        if size <= 0:
            raise RuntimeError(f"enter returned no position: size={size}")
        state.size = size
        state.entry_price = entry_px
        state.open_ref = str(ref) if ref is not None else None
        state.state = OrderStates.DONE


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
        entry_px = (cfg.amount_quote / size) if size > 0 else Decimal("0")
        return size, entry_px, cfg.amount_quote, ack.order_id

    async def close(self, size: Decimal):
        before = await self.client.usdc_balance()
        ack = await self.client.place_market(self.cfg.market, "SELL", size)
        after = await self.client.usdc_balance()
        proceeds = after - before
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
        else:
            state.state = OrderStates.FAILED
            state.extra["error"] = "limit order left the book unfilled (cancelled/expired)"

    async def cancel(self, state) -> None:
        if state.open_ref:
            await self.client.cancel(state.open_ref)


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
        entry_px = (cfg.amount_quote / got) if got > 0 else ask
        return got, entry_px, cfg.amount_quote, ack.oid

    async def close(self, size: Decimal):
        oc, sd = await self._resolve()
        before = await self.client.usdc_balance()
        ack = await self.client.marketable_sell(oc, sd, size, self.cfg.slippage_pct)
        proceeds = await self.client.usdc_balance() - before
        exit_px = (proceeds / size) if size > 0 else await self.client.price(oc, sd, "bid")
        return exit_px, proceeds, ack.oid

    async def held_size(self) -> Decimal:
        oc, sd = await self._resolve()
        return await self.client.shares(oc, sd)


# -- factory ------------------------------------------------------------------


def make_adapter(instrument: str, venue: str, connector: Any, cfg: Any) -> InstrumentAdapter:
    """Build the adapter for an (instrument, venue) pair.

    spot -> SpotAdapter (Solana OR Hyperliquid connector); perp -> PerpAdapter;
    pred + polymarket -> PolymarketPredAdapter; pred + hyperliquid -> HyperliquidPredAdapter.
    """
    if instrument == "spot":
        return SpotAdapter(connector, cfg)
    if instrument == "perp":
        return PerpAdapter(connector, cfg)
    if instrument == "pred":
        if venue == "polymarket":
            return PolymarketPredAdapter(connector, cfg)
        if venue == "hyperliquid":
            return HyperliquidPredAdapter(connector, cfg)
        raise ValueError(f"unsupported prediction venue: {venue!r}")
    raise ValueError(f"unknown instrument: {instrument!r}")


def _order_is_open(status: Any) -> bool:
    """True if an order-status response indicates a still-resting order."""
    if not isinstance(status, dict):
        return False
    order = status.get("order") or {}
    inner = order.get("order") if isinstance(order.get("order"), dict) else order
    state = (order.get("status") or inner.get("status") or "").lower()
    return state in ("open", "resting", "triggered")


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
