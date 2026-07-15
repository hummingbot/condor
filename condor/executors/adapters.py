"""Instrument adapters — one per (instrument, venue), wrapping a venue connector.

The kind picks the executor class (OrderExecutor / PositionExecutor); the
(instrument, venue) pair picks the adapter via ``make_adapter``, which
dispatches through the loaded venue packages (§6.2b). This module keeps only
the venue-agnostic pieces: the ``InstrumentAdapter`` interface, the shared
``SpotAdapter`` (Jupiter and HL spot speak the same swap interface) and the
shared prediction base — per-venue adapters live in
``condor/venues/{hyperliquid,polymarket}/adapters.py``.

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

from condor.executors.orders import (
    LandedOrder,
    OrderRole,
    OrderStatus,
    find_order,
    upsert_landed,
)

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

    # -- uniform orders[] recording (§6.2b) -----------------------------------

    def record_landed_order(
        self,
        state,
        *,
        venue_order_id,
        role,
        side: str,
        requested_qty,
        requested_unit: str = "base",
        status=OrderStatus.OPEN,
        filled_base: Decimal = Decimal(0),
        filled_quote: Decimal = Decimal(0),
        fees_by_asset: Optional[dict] = None,
        client_order_id: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> LandedOrder:
        """Land a venue-ACKNOWLEDGED order into ``state.orders``, idempotent by
        venue id (an MCP retry re-observing the same id never duplicates it).
        Rejections/errors record nothing — they are transitions, not orders."""
        entry = LandedOrder(
            venue_order_id=str(venue_order_id),
            client_order_id=client_order_id,
            role=OrderRole(role),
            side=side,
            requested_qty=Decimal(str(requested_qty)),
            requested_unit=requested_unit,
            cumulative_filled_base_qty=Decimal(str(filled_base)),
            cumulative_filled_quote_qty=Decimal(str(filled_quote)),
            fees_by_asset={k: Decimal(str(v)) for k, v in (fees_by_asset or {}).items()},
            status=OrderStatus(status),
            cursor=cursor,
        )
        return upsert_landed(state.orders, entry)

    def update_landed_order(self, state, venue_order_id, **absolute) -> None:
        """Overwrite an existing entry with the venue's ABSOLUTE cumulative view
        (idempotent). A missing id is logged, never fatal — recording must not
        break trading."""
        entry = find_order(state.orders, str(venue_order_id))
        if entry is None:
            logger.warning(
                "orders[]: no landed entry for venue_order_id=%s (update %s ignored)",
                venue_order_id, sorted(absolute),
            )
            return
        entry.apply_absolute(**absolute)

    def _leg_role(self) -> OrderRole:
        """TRADE for an order-kind single leg; ENTRY for a position opening leg."""
        return OrderRole.TRADE if str(getattr(self.cfg, "type", "")).startswith("order") else OrderRole.ENTRY

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

    @staticmethod
    def _swap_fees(data: dict) -> Optional[dict]:
        """Per-swap network fee as fees_by_asset — mirrors extra.tx_fee/extra.fee.
        Jupiter reports it in SOL (lamports/1e9); HL spot reports 0."""
        fee = _dec((data or {}).get("fee", 0))
        return {"SOL": fee} if fee > 0 else None

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
        # A swap is one immediately-executed venue order: the signature is its
        # id, the measured out-delta its cumulative fill; a failed tx (status
        # -1) landed but executed nothing economically.
        confirmed = result.get("status", 1) == 1
        self.record_landed_order(
            self.state,
            venue_order_id=ref,
            role=self._leg_role(),
            side="buy",  # entering acquires the base token
            requested_qty=cfg.amount_quote,
            requested_unit="quote",
            status=OrderStatus.FILLED if confirmed else OrderStatus.CANCELED,
            filled_base=size if confirmed else Decimal(0),
            filled_quote=cfg.amount_quote if confirmed else Decimal(0),
            fees_by_asset=self._swap_fees(data),
        )
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
        confirmed = result.get("status", 1) == 1
        self.record_landed_order(
            self.state,
            venue_order_id=ref,
            role=OrderRole.EXIT,
            side="sell",
            requested_qty=size,
            requested_unit="base",
            status=OrderStatus.FILLED if confirmed else OrderStatus.CANCELED,
            filled_base=(_dec(data.get("amountIn", 0)) or size) if confirmed else Decimal(0),
            filled_quote=proceeds if confirmed else Decimal(0),
            fees_by_asset=self._swap_fees(data),
        )
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
        # The signature is the landed order id. Confirmed (HL spot always; a
        # Jupiter tx polled to success inside execute_swap) -> FILLED with the
        # measured deltas; still in flight -> OPEN, the poll site settles it.
        confirmed = result.get("status") == 1
        filled_base, filled_quote = self._swap_leg_fills(state)
        self.record_landed_order(
            state,
            venue_order_id=result["signature"],
            role=OrderRole.TRADE,
            side="buy" if cfg.side.upper() == "BUY" else "sell",
            requested_qty=cfg.amount,
            requested_unit="base",  # order_spot cfg.amount is base units both ways
            status=OrderStatus.FILLED if confirmed else OrderStatus.OPEN,
            filled_base=filled_base if confirmed else Decimal(0),
            filled_quote=filled_quote if confirmed else Decimal(0),
            fees_by_asset=self._swap_fees(data),
        )

    def _swap_leg_fills(self, state) -> tuple[Decimal, Decimal]:
        """(filled_base, filled_quote) for the one-way swap from the measured
        amountIn/amountOut persisted at placement (BUY: base out / quote in;
        SELL: base in / quote out)."""
        amount_in = _dec(state.extra.get("amount_in", 0))
        amount_out = _dec(state.extra.get("amount_out", 0))
        if self.cfg.side.upper() == "BUY":
            return amount_out, amount_in
        return (amount_in or _dec(self.cfg.amount)), amount_out

    async def poll(self, state) -> None:
        from condor.executors.order import OrderStates

        cfg = self.cfg
        chain = cfg.chain_network.split("-", 1)[0]
        network = cfg.chain_network.split("-", 1)[1] if "-" in cfg.chain_network else cfg.chain_network
        poll = await self.c.poll_tx(chain, network, state.open_ref)
        ts = poll.get("txStatus")
        if ts == 1:
            state.state = OrderStates.DONE
            filled_base, filled_quote = self._swap_leg_fills(state)
            self.update_landed_order(
                state, state.open_ref,
                status=OrderStatus.FILLED,
                filled_base=filled_base,
                filled_quote=filled_quote,
            )
        elif ts == -1:
            state.state = OrderStates.FAILED
            state.extra["error"] = f"swap tx failed on-chain: {state.open_ref}"
            # The signature landed but executed nothing economically.
            self.update_landed_order(
                state, state.open_ref,
                status=OrderStatus.CANCELED,
                filled_base=Decimal(0),
                filled_quote=Decimal(0),
            )


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


# -- factory (registry-dispatched, §6.2b) --------------------------------------


def make_adapter(instrument: str, venue: str, connector: Any, cfg: Any) -> InstrumentAdapter:
    """Build the adapter for an (instrument, venue) pair by dispatching through
    the loaded venue packages — core carries no per-venue branches. Unknown
    venue ids error (UnknownVenueError); a venue that does not claim the
    instrument errors too."""
    from condor.venues.registry import venue_spec

    venue = venue or "solana"
    spec = venue_spec(venue)  # raises UnknownVenueError for unregistered ids
    factory = spec.adapter_factories.get(instrument)
    if factory is None:
        raise ValueError(
            f"venue {venue!r} does not support instrument {instrument!r} "
            f"(supported: {sorted(spec.adapter_factories)})"
        )
    return factory(connector, cfg)


# -- moved per-venue adapters (compat re-exports, §6.2b) ------------------------

# PerpAdapter / HyperliquidPredAdapter / _order_is_open live in
# condor.venues.hyperliquid.adapters; PolymarketPredAdapter / _pm_order_open in
# condor.venues.polymarket.adapters. Resolved lazily (PEP 562) so importing a
# venue package never re-enters this module mid-initialization.
_MOVED = {
    "PerpAdapter": "condor.venues.hyperliquid.adapters",
    "HyperliquidPredAdapter": "condor.venues.hyperliquid.adapters",
    "_order_is_open": "condor.venues.hyperliquid.adapters",
    "PolymarketPredAdapter": "condor.venues.polymarket.adapters",
    "_pm_order_open": "condor.venues.polymarket.adapters",
}


def __getattr__(name: str):
    module = _MOVED.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module), name)
