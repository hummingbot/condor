"""Order executors (kind=order) — single-leg place-and-track, terminal.

An OrderExecutor places ONE order (market by default; a resting limit on CLOB
venues when ``order_type='limit'``), tracks it to filled/cancelled, and ends.
No barriers, no round-trip. The kind picks this class; (instrument, venue) picks
the adapter that actually places the order:

    order_spot -> SpotAdapter  (a one-way swap; replaces the old SwapExecutor)
    order_perp -> PerpAdapter  (market_open / resting limit)
    order_pred -> Polymarket/Hyperliquid pred adapter (buy shares)

Lifecycle (persisted on every transition):

    NOT_ACTIVE -> SUBMITTING -> (RESTING) -> DONE | FAILED
"""

from __future__ import annotations

import logging
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from condor.executors.adapters import make_adapter
from condor.executors.base import ExecutorBase, ExecutorConfig, ExecutorStatus, RiskDeclaration

logger = logging.getLogger(__name__)


class OrderStates(str, Enum):
    NOT_ACTIVE = "NOT_ACTIVE"
    SUBMITTING = "SUBMITTING"
    RESTING = "RESTING"
    DONE = "DONE"
    FAILED = "FAILED"


# -- configs ------------------------------------------------------------------


class OrderSpotConfig(ExecutorConfig):
    type: Literal["order_spot"] = "order_spot"
    venue: str = "solana"
    base_token: str
    quote_token: str
    amount: Decimal  # amount of base token
    side: Literal["BUY", "SELL"]
    slippage_pct: Decimal = Decimal("1.0")
    order_type: Literal["market", "limit"] = "market"
    limit_px: Optional[Decimal] = None
    # Declared notional in quote units — required for risk gating (the gate
    # cannot price base units statically). The create route fills it from a
    # live quote when omitted.
    notional_quote: Optional[Decimal] = None
    update_interval: float = 2.0

    def risk_declaration(self) -> RiskDeclaration:
        if self.notional_quote is None:
            raise ValueError(
                "order_spot config must declare notional_quote (quote-unit value "
                "of the swap) so the risk gate can bound it"
            )
        # Worst case loss on a confirmed swap is slippage + tx fee.
        return RiskDeclaration(
            max_notional_quote=self.notional_quote,
            max_loss_quote=self.notional_quote * self.slippage_pct / Decimal("100"),
        )


class OrderPerpConfig(ExecutorConfig):
    type: Literal["order_perp"] = "order_perp"
    venue: str = "hyperliquid"
    chain_network: str = "hyperliquid"
    wallet_address: str = ""
    coin: str
    side: Literal["LONG", "SHORT"]
    notional_quote: Decimal  # USD notional; size = notional / entry
    leverage: int = 1
    cross_margin: bool = True
    slippage_pct: Decimal = Decimal("0.5")
    order_type: Literal["market", "limit"] = "market"
    limit_px: Optional[Decimal] = None
    update_interval: float = 3.0

    def risk_declaration(self) -> RiskDeclaration:
        return RiskDeclaration(
            max_notional_quote=self.notional_quote,
            max_loss_quote=self.notional_quote / Decimal(self.leverage or 1),
        )


class OrderPredConfig(ExecutorConfig):
    type: Literal["order_pred"] = "order_pred"
    venue: Literal["polymarket", "hyperliquid"] = "polymarket"
    chain_network: str = "polymarket"
    wallet_address: str = ""
    market: str
    position: Literal["LONG", "SHORT"] = "LONG"
    amount_quote: Decimal  # USDC to deploy
    slippage_pct: Decimal = Decimal("1.0")
    order_type: Literal["market", "limit"] = "market"
    limit_px: Optional[Decimal] = None
    update_interval: float = 5.0

    def risk_declaration(self) -> RiskDeclaration:
        return RiskDeclaration(
            max_notional_quote=self.amount_quote,
            max_loss_quote=self.amount_quote,
        )


# -- state --------------------------------------------------------------------


class OrderState(BaseModel):
    state: OrderStates = OrderStates.NOT_ACTIVE
    size: Decimal = Decimal("0")
    entry_price: Optional[Decimal] = None
    open_ref: Optional[str] = None
    close_type: Optional[str] = None
    extra: dict = Field(default_factory=dict)


# -- executor -----------------------------------------------------------------


class OrderExecutor(ExecutorBase):
    def __init__(self, executor_id, config: ExecutorConfig, connector, store):
        super().__init__(executor_id, config, connector, store)
        self.config = config
        self.adapter = make_adapter(config.instrument, config.venue, connector, config)
        self.state = OrderState()
        self.adapter.bind(self.state)
        # Set by early_stop() when a RESTING limit must be pulled from the venue;
        # the async control loop performs the cancel (early_stop is synchronous).
        self._cancel_requested = False

    def state_model(self) -> BaseModel:
        return self.state

    def restore_state(self, state: dict) -> None:
        self.state = OrderState(**state)
        self.adapter.bind(self.state)

    def _recovery_key(self) -> tuple:
        return (self.state.state.value, self.state.open_ref)

    async def control_task(self) -> None:
        s = self.state
        if self._cancel_requested and s.state in (OrderStates.RESTING, OrderStates.SUBMITTING) and s.open_ref:
            await self._cancel_resting()
            return
        match s.state:
            case OrderStates.NOT_ACTIVE:
                s.state = OrderStates.SUBMITTING
                self.persist()
                await self.adapter.place(s)
                self._settle_terminal()
            case OrderStates.SUBMITTING:
                # Re-entered SUBMITTING after a crash: never re-place (may double).
                if s.open_ref:
                    await self.adapter.poll(s)
                    self._settle_terminal()
                else:
                    self.fail("crashed during submission with no ref — check wallet activity manually")
                    s.state = OrderStates.FAILED
            case OrderStates.RESTING:
                await self.adapter.poll(s)
                self._settle_terminal()
            case OrderStates.DONE:
                self.status = ExecutorStatus.CLOSED
                if not self.close_reason:
                    self.close_reason = "order filled"
            case OrderStates.FAILED:
                self.fail(s.extra.get("error") or "order failed")

    def _settle_terminal(self) -> None:
        s = self.state
        if s.state == OrderStates.DONE:
            self.status = ExecutorStatus.CLOSED
            self.close_reason = self.close_reason or "order filled"
            logger.info("order %s filled (%s) ref=%s", self.id, self.config.type, s.open_ref)
        elif s.state == OrderStates.FAILED:
            self.fail(s.extra.get("error") or "order failed")

    async def _cancel_resting(self) -> None:
        """Pull a resting limit off the venue on stop, then confirm from the
        real fill whether any of it filled before the cancel landed. Leaving a
        stale order live is exactly the requote bug (#3)."""
        s = self.state
        try:
            await self.adapter.cancel(s)
        except Exception as e:
            logger.warning("order %s: cancel raised (%s) — confirming fill state", self.id, e)
        # Truth after cancel: poll reads the venue's terminal view of the order.
        await self.adapter.poll(s)
        if s.state in (OrderStates.RESTING, OrderStates.SUBMITTING):
            # The venue still reports the order live. Keep retrying the cancel on
            # later control ticks; declaring success here would orphan a live quote.
            logger.warning("order %s: venue still reports order live after cancel", self.id)
            return
        if s.state == OrderStates.DONE:
            # A fill landed before the cancel took effect — the fill is real, keep it.
            self.status = ExecutorStatus.CLOSED
            self.close_reason = self.close_reason or "order filled before cancel"
            s.close_type = "filled" if s.close_type in (None, "early_stop") else s.close_type
        else:
            # FAILED means poll confirmed the order left the book unfilled: a
            # pulled quote is terminal and clean, not an executor failure.
            s.extra.pop("error", None)
            s.state = OrderStates.DONE
            s.close_type = "early_stop"
            self.status = ExecutorStatus.CLOSED
            self.close_reason = "resting order cancelled (early stop)"

    def early_stop(self, keep_position: bool = True) -> None:
        s = self.state
        if s.state in (OrderStates.NOT_ACTIVE,) or (s.state == OrderStates.SUBMITTING and not s.open_ref):
            self.status = ExecutorStatus.CLOSED
            self.close_reason = "stopped before submission"
            s.state = OrderStates.DONE
            s.close_type = "early_stop"
        elif s.state in (OrderStates.RESTING, OrderStates.SUBMITTING) and s.open_ref:
            # A resting limit exists on the venue: request an async cancel; the
            # control loop pulls it and settles from the real fill.
            self._cancel_requested = True
            s.close_type = "early_stop"

    def custom_info(self) -> dict[str, Any]:
        s = self.state
        return {
            "type": self.config.type,
            "instrument": self.config.instrument,
            "venue": self.config.venue,
            "state": s.state.value,
            "size": float(s.size),
            "entry_price": float(s.entry_price) if s.entry_price is not None else None,
            "open_ref": s.open_ref,
            "close_type": s.close_type,
        }

    def net_pnl_quote(self) -> Decimal:
        return Decimal("0")
