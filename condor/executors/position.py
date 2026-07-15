"""Position executors (kind=position) — round-trip with a barrier ladder.

A PositionExecutor opens a position, monitors it each tick, and closes on the
first barrier hit (SL -> trailing -> TP -> time_limit). It is generic: every
instrument specific — how to enter, mark, close, settle, and what extra barriers
apply — comes from an :class:`InstrumentAdapter`. The kind picks this class;
(instrument, venue) picks the adapter:

    position_spot -> SpotAdapter      (long-only spot, execution-aware exit)
    position_perp -> PerpAdapter      (leverage, liquidation guard, native triggers)
    position_pred -> pred adapter     (outcome markets, resolve_win/loss exits)

Honesty note: a stop loss is an intent, not a guarantee — a gap or liquidity rug
can exit worse than the SL, so each risk declaration bounds loss accordingly.

Lifecycle (persisted on every transition):

    NOT_ACTIVE -> OPENING -> ACTIVE -> CLOSING -> COMPLETE
                     \\-----------------------------> FAILED
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from condor.executors.adapters import make_adapter
from condor.executors.orders import LandedOrder, orders_digest
from condor.executors.base import (
    BarrierFields,
    ExecutorBase,
    ExecutorConfig,
    ExecutorStatus,
    RiskDeclaration,
)

logger = logging.getLogger(__name__)


class PositionStates(str, Enum):
    NOT_ACTIVE = "NOT_ACTIVE"
    OPENING = "OPENING"
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


# -- configs ------------------------------------------------------------------


class PositionSpotConfig(ExecutorConfig, BarrierFields):
    type: Literal["position_spot"] = "position_spot"
    venue: str = "solana"
    base_token: str  # symbol or mint address (memecoins: use the address)
    quote_token: str  # "SOL" | "USDC" — the side being spent and returned
    amount_quote: Decimal  # entry size, quote units
    slippage_pct: Decimal = Decimal("1.0")
    update_interval: float = 3.0

    def risk_declaration(self) -> RiskDeclaration:
        # Max loss is the FULL entry: SL is best-effort on thin memecoin
        # liquidity, never a bound.
        return RiskDeclaration(
            max_notional_quote=self.amount_quote,
            max_loss_quote=self.amount_quote,
        )


class PositionPerpConfig(ExecutorConfig, BarrierFields):
    type: Literal["position_perp"] = "position_perp"
    venue: str = "hyperliquid"
    chain_network: str = "hyperliquid"
    wallet_address: str = ""
    coin: str  # "ETH", "BTC", "SOL", ...
    side: Literal["LONG", "SHORT"]
    notional_quote: Decimal  # USD notional; size = notional / entry
    leverage: int = 1
    cross_margin: bool = True
    entry: Literal["market", "limit"] = "market"
    limit_px: Optional[Decimal] = None
    slippage_pct: Decimal = Decimal("0.5")
    # Perp-only: close if mark within this fraction of the liquidation price.
    liquidation_guard_pct: Decimal = Decimal("0.15")
    # Also place native reduce-only TP/SL triggers as a daemon-down backstop.
    native_triggers: bool = True
    update_interval: float = 3.0

    def risk_declaration(self) -> RiskDeclaration:
        return RiskDeclaration(
            max_notional_quote=self.notional_quote,
            max_loss_quote=self.notional_quote / Decimal(self.leverage or 1),
        )


class PositionPredConfig(ExecutorConfig, BarrierFields):
    type: Literal["position_pred"] = "position_pred"
    venue: Literal["polymarket", "hyperliquid"] = "polymarket"
    chain_network: str = "polymarket"
    wallet_address: str = ""
    # Polymarket token_id, or (hyperliquid) the HIP-4 outcome id or name;
    # position LONG=Yes / SHORT=No.
    market: str
    position: Literal["LONG", "SHORT"] = "LONG"
    amount_quote: Decimal  # USDC to deploy
    slippage_pct: Decimal = Decimal("1.0")
    # Outcome-settlement exits (probability venues only), absolute price [0,1].
    resolve_win_price: Optional[Decimal] = None
    resolve_loss_price: Optional[Decimal] = None
    update_interval: float = 5.0

    def risk_declaration(self) -> RiskDeclaration:
        # An outcome can settle to 0 — bound loss at the full stake.
        return RiskDeclaration(
            max_notional_quote=self.amount_quote,
            max_loss_quote=self.amount_quote,
        )


# -- state --------------------------------------------------------------------


class PositionState(BaseModel):
    """Generic round-trip state. Instrument-specific persisted fields live in
    ``extra`` (perp: tp_oid/sl_oid/liquidation_px/realized_pnl/funding; spot:
    tx_fee)."""

    state: PositionStates = PositionStates.NOT_ACTIVE
    opened_at: Optional[float] = None
    entry_price: Optional[Decimal] = None
    size: Decimal = Decimal("0")
    amount_spent: Decimal = Decimal("0")
    mark_price: Optional[Decimal] = None
    pnl_pct: Optional[Decimal] = None
    trailing_trigger_pct: Optional[Decimal] = None
    exit_price: Optional[Decimal] = None
    proceeds: Optional[Decimal] = None
    open_ref: Optional[str] = None
    close_ref: Optional[str] = None
    close_type: Optional[str] = None
    extra: dict = Field(default_factory=dict)
    # The sole durable financial authority (§6.2b): every venue-acknowledged
    # order (entry legs, exit legs, native TP/SL protection), keyed by
    # venue_order_id. Legacy aggregates (size/amount_spent/proceeds) become
    # transitional projections of this collection.
    orders: list[LandedOrder] = Field(default_factory=list)


# -- executor -----------------------------------------------------------------


class PositionExecutor(ExecutorBase):
    def __init__(self, executor_id, config: ExecutorConfig, connector, store):
        super().__init__(executor_id, config, connector, store)
        self.config = config
        self.adapter = make_adapter(config.instrument, config.venue, connector, config)
        self.state = PositionState()
        self.adapter.bind(self.state)
        # Set by early_stop() when a still-resting limit ENTRY must be pulled;
        # the async loop performs the cancel (early_stop is synchronous).
        self._cancel_requested = False

    def state_model(self) -> BaseModel:
        return self.state

    def restore_state(self, state: dict) -> None:
        self.state = PositionState(**state)
        self.adapter.bind(self.state)

    def _recovery_key(self) -> tuple:
        s = self.state
        # orders[] digest: identifier-only transitions and cumulative-fill
        # changes are recovery-relevant (§6.2b) and must persist.
        return (
            s.state.value,
            s.open_ref,
            s.close_ref,
            orders_digest(s.orders),
            *self.adapter.recovery_ids(s),
        )

    # -- control loop --------------------------------------------------------

    async def control_task(self) -> None:
        s = self.state
        match s.state:
            case PositionStates.NOT_ACTIVE:
                # Account-wide spot/outcome balances need a pre-trade baseline so
                # restart reconciliation can distinguish our fill from inventory
                # that was already in the wallet. Do this before recording OPENING.
                await self.adapter.prepare_open(s)
                s.state = PositionStates.OPENING
                self.persist()
                await self._open()
            case PositionStates.OPENING:
                # A resting limit entry (perp) can be pulled by a stop or by its
                # own time_limit before it ever fills — otherwise it lingers live
                # and unmanaged while the strategy re-quotes (#3).
                if self._cancel_requested:
                    return await self._cancel_opening("early_stop")
                if self._opening_expired():
                    return await self._cancel_opening("time_limit")
                await self._open()
            case PositionStates.ACTIVE:
                await self._control_barriers()
            case PositionStates.CLOSING:
                self.status = ExecutorStatus.CLOSING
                await self._close()
            case PositionStates.COMPLETE:
                self.status = ExecutorStatus.CLOSED
                if not self.close_reason:
                    self.close_reason = f"position closed ({s.close_type})"
            case PositionStates.FAILED:
                self.fail(self.close_reason or "position state machine failed")

    async def _open(self) -> None:
        s = self.state
        size, entry_px, amount_spent, ref = await self.adapter.enter()
        if size <= 0:
            if self.adapter.pending:
                # e.g. a perp limit still resting — stay OPENING, poll next tick.
                s.open_ref = str(ref) if ref is not None else None
                # Stamp when the quote was PLACED (not when it fills) so an unfilled
                # resting entry can still be expired by time_limit_s (#3).
                if s.opened_at is None:
                    s.opened_at = time.time()
                return
            raise RuntimeError(f"enter returned no position: size={size}")
        s.size = size
        s.entry_price = entry_px
        s.amount_spent = amount_spent
        s.open_ref = str(ref) if ref is not None else None
        s.opened_at = time.time()
        s.state = PositionStates.ACTIVE
        await self.adapter.on_open(s)
        logger.info("position %s: opened %s @ %s (%s) ref=%s (%s)",
                    self.id, s.size, s.entry_price, amount_spent, s.open_ref, self.config.type)
        await self.notify_trade(self.adapter.open_note(s))

    def _opening_expired(self) -> bool:
        cfg, s = self.config, self.state
        return (
            cfg.time_limit_s is not None
            and s.opened_at is not None
            and time.time() >= s.opened_at + cfg.time_limit_s
        )

    async def _cancel_opening(self, reason: str) -> None:
        """Pull a still-resting limit entry, then reconcile against the venue:
        if it raced a fill, adopt and manage it; otherwise end cleanly. Never
        leave the order live after the executor stops."""
        s = self.state
        try:
            await self.adapter.cancel(s)
        except Exception as e:
            logger.warning("position %s: cancel resting entry failed: %s", self.id, e)
            # The entry may still be resting and fill later. Leave OPENING and
            # retain _cancel_requested so the control loop retries next tick.
            return
        if await self.adapter.held_size() > 0:
            # A fill landed before the cancel — enter() now reads the filled
            # position and transitions us to ACTIVE to manage it.
            await self._open()
            return
        s.close_type = reason
        self.close_reason = f"opening cancelled ({reason})"
        s.state = PositionStates.COMPLETE

    async def _control_barriers(self) -> None:
        """Barrier order: extra_barriers (perp liq-guard / pred resolve) ->
        SL -> trailing -> TP, then time limit. Time limit is enforced even when
        the price fetch fails — a position must never outlive its TTL."""
        cfg, s = self.config, self.state

        # The venue is the truth: a native trigger fired / liquidation / resolve.
        if await self.adapter.vanished():
            s.close_type = s.close_type or "detached"
            await self.adapter.settle(s)
            if not self.close_reason:
                self.close_reason = f"position closed on venue ({s.close_type})"
            s.state = PositionStates.COMPLETE
            return

        price_ok = True
        try:
            s.mark_price = await self.adapter.mark_price()
            s.pnl_pct = self.adapter.pnl_pct(s.entry_price, s.mark_price)
        except Exception as e:
            price_ok = False
            logger.warning("position %s: mark fetch failed: %s", self.id, e)

        if price_ok:
            extra = self.adapter.extra_barriers(s)
            if extra is not None:
                return self._trigger_close(extra, s.pnl_pct)
            pnl = s.pnl_pct
            if cfg.stop_loss_pct is not None and pnl <= -cfg.stop_loss_pct:
                return self._trigger_close("stop_loss", pnl)
            if cfg.trailing_activation_pct is not None and cfg.trailing_delta_pct is not None:
                if s.trailing_trigger_pct is None:
                    if pnl > cfg.trailing_activation_pct:
                        s.trailing_trigger_pct = pnl - cfg.trailing_delta_pct
                else:
                    if pnl < s.trailing_trigger_pct:
                        return self._trigger_close("trailing_stop", pnl)
                    if pnl - cfg.trailing_delta_pct > s.trailing_trigger_pct:
                        s.trailing_trigger_pct = pnl - cfg.trailing_delta_pct
            if cfg.take_profit_pct is not None and pnl >= cfg.take_profit_pct:
                return self._trigger_close("take_profit", pnl)

        if cfg.time_limit_s is not None and s.opened_at is not None:
            if time.time() >= s.opened_at + cfg.time_limit_s:
                return self._trigger_close("time_limit", s.pnl_pct)

    def _trigger_close(self, close_type: str, pnl) -> None:
        s = self.state
        s.close_type = close_type
        self.close_reason = f"{close_type} hit (pnl {float(pnl) * 100:+.2f}%)" if pnl is not None \
            else f"{close_type} hit"
        logger.info("position %s: %s", self.id, self.close_reason)
        s.state = PositionStates.CLOSING

    async def _close(self) -> None:
        s = self.state
        # on_close cancels owned protection triggers FIRST (they reach
        # CANCELED/CANCEL_PENDING in orders[]); only then is the close sized.
        await self.adapter.on_close(s)
        close_size = self._close_size()
        if close_size <= 0:
            # Nothing owned remains (fully exited by triggers/partials) —
            # settle without placing a close order.
            logger.info(
                "position %s: owned_net_base is 0 after cancellations — "
                "nothing to close", self.id,
            )
            await self.adapter.settle(s)
            s.state = PositionStates.COMPLETE
            return
        exit_px, proceeds, ref = await self.adapter.close(close_size)
        s.exit_price = exit_px
        s.proceeds = proceeds
        s.close_ref = str(ref) if ref is not None else None
        await self.adapter.settle(s)
        pnl = self.net_pnl_quote()
        logger.info("position %s: closed (%s) exit=%s proceeds=%s ref=%s",
                    self.id, s.close_type, exit_px, proceeds, s.close_ref)
        await self.notify_trade(self.adapter.close_note(s, pnl))
        s.state = PositionStates.COMPLETE

    def _close_size(self) -> Decimal:
        """Close sizing (§6.2): the scope's REMAINING SIGNED inventory folded
        from orders[] — never raw gross entry fills, which over-close after a
        partial exit, a cancel/fill race, base-asset fees, or offsetting
        two-sided fills. Falls back to the legacy scalar while orders[] is
        empty (pre-§6.2b records); divergence is logged, the fold wins."""
        from condor.executors.orders import owned_net_base

        s = self.state
        if not s.orders:
            return s.size
        net = owned_net_base(
            s.orders,
            product=self.config.instrument or "spot",
            base_asset=str(getattr(self.config, "base_token", "") or ""),
        )
        size = abs(net)
        if s.size and abs(size - s.size) > abs(s.size) * Decimal("0.001"):
            logger.warning(
                "position %s: close sizing from orders[] fold %s diverges from "
                "legacy scalar %s — using the fold (§6.2)",
                self.id, size, s.size,
            )
        return size

    # -- stop / reporting ----------------------------------------------------

    def early_stop(self, keep_position: bool = True) -> None:
        """keep_position=True: DETACH (position stays on venue, executor ends).
        keep_position=False: close now."""
        s = self.state
        if s.state == PositionStates.ACTIVE:
            if keep_position:
                s.close_type = "detached"
                self.close_reason = f"stopped — holding {s.size} units (detached)"
                s.state = PositionStates.COMPLETE
            else:
                s.close_type = "early_stop"
                self.close_reason = "early stop (closing position)"
                s.state = PositionStates.CLOSING
        elif s.state == PositionStates.OPENING:
            if s.open_ref:
                # A resting limit entry exists on the venue — request an async
                # cancel; the loop pulls it (adopting any raced fill) (#3).
                self._cancel_requested = True
            else:
                # No order placed yet (mid-submission): cannot safely continue.
                self.fail("stopped while opening — verify position on venue manually")
                s.state = PositionStates.FAILED
        elif s.state == PositionStates.NOT_ACTIVE:
            s.close_type = "early_stop"
            self.close_reason = "stopped before opening"
            s.state = PositionStates.COMPLETE

    def custom_info(self) -> dict[str, Any]:
        s = self.state
        info = {
            "type": self.config.type,
            "instrument": self.config.instrument,
            "venue": self.config.venue,
            "state": s.state.value,
            "entry_price": float(s.entry_price) if s.entry_price else None,
            "mark_price": float(s.mark_price) if s.mark_price else None,
            "pnl_pct": float(s.pnl_pct) if s.pnl_pct is not None else None,
            "size": float(s.size),
            "amount_spent": float(s.amount_spent),
            "proceeds": float(s.proceeds) if s.proceeds is not None else None,
            "exit_price": float(s.exit_price) if s.exit_price is not None else None,
            "trailing_trigger_pct": float(s.trailing_trigger_pct)
            if s.trailing_trigger_pct is not None else None,
            "close_type": s.close_type,
            "open_ref": s.open_ref,
            "close_ref": s.close_ref,
            "unrealized_pnl_quote": float(self.net_pnl_quote()),
        }
        info.update(self.adapter.info(s))
        return info

    def net_pnl_quote(self) -> Decimal:
        return self.adapter.net_pnl(self.state)
