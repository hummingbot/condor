"""Spot position executor — triple barrier (TP / SL / time limit / trailing).

Port of hummingbot's position_executor barrier semantics onto gateway
swaps: BUY base with quote at open, monitor an execution-aware price
(the sell-side quote for the actual held amount), market-close on the
first barrier hit. Long-only spot — built for memecoin entries where
the exit MUST be machine-speed and unattended.

Honesty note: a stop loss here is an intent, not a guarantee — a gap or
liquidity rug can exit worse than the SL, so the risk declaration bounds
loss at the full entry amount.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel

from condor.executors.base import ExecutorBase, ExecutorConfig, ExecutorStatus, RiskDeclaration

logger = logging.getLogger(__name__)


class PositionStates(str, Enum):
    NOT_ACTIVE = "NOT_ACTIVE"
    OPENING = "OPENING"
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class PositionConfig(ExecutorConfig):
    type: Literal["position"] = "position"
    base_token: str  # symbol or mint address (memecoins: use the address)
    quote_token: str  # "SOL" | "USDC" — the side being spent and returned
    amount_quote: Decimal  # entry size, quote units
    # Triple barrier (fractions of entry price, hummingbot semantics:
    # 0.03 = 3%). None disables that barrier.
    take_profit_pct: Optional[Decimal] = None
    stop_loss_pct: Optional[Decimal] = None
    time_limit_s: Optional[int] = None
    # Trailing stop: activate when pnl_pct > activation_pct, then close when
    # pnl_pct falls trailing_delta_pct below its high-water mark.
    trailing_activation_pct: Optional[Decimal] = None
    trailing_delta_pct: Optional[Decimal] = None
    connector: Optional[str] = None  # None -> gateway default router
    slippage_pct: Decimal = Decimal("1.0")
    update_interval: float = 3.0

    def risk_declaration(self) -> RiskDeclaration:
        # Max loss is the FULL entry: SL is best-effort on thin memecoin
        # liquidity, never a bound.
        return RiskDeclaration(
            max_notional_quote=self.amount_quote,
            max_loss_quote=self.amount_quote,
        )


class PositionState(BaseModel):
    state: PositionStates = PositionStates.NOT_ACTIVE
    opened_at: Optional[float] = None
    entry_price: Optional[Decimal] = None
    base_bought: Decimal = Decimal("0")
    quote_spent: Decimal = Decimal("0")
    quote_returned: Optional[Decimal] = None
    exit_price: Optional[Decimal] = None
    last_price: Optional[Decimal] = None
    pnl_pct: Optional[Decimal] = None
    trailing_trigger_pct: Optional[Decimal] = None
    open_tx_hash: Optional[str] = None
    close_tx_hash: Optional[str] = None
    tx_fee: Decimal = Decimal("0")  # native, cumulative
    close_type: Optional[str] = None  # take_profit|stop_loss|time_limit|trailing_stop|early_stop


class PositionExecutor(ExecutorBase):
    def __init__(self, executor_id, config: PositionConfig, gateway, store):
        super().__init__(executor_id, config, gateway, store)
        self.config: PositionConfig = config
        self.state = PositionState()

    def state_model(self) -> BaseModel:
        return self.state

    def restore_state(self, state: dict) -> None:
        self.state = PositionState(**state)

    # -- control loop --------------------------------------------------------

    async def control_task(self) -> None:
        s = self.state
        match s.state:
            case PositionStates.NOT_ACTIVE:
                s.state = PositionStates.OPENING
                self.persist()
                await self._open()

            case PositionStates.OPENING:
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
        """Enter by SELLING amount_quote of the quote token for the base.

        Inverted on purpose: an exact-base BUY quotes as ExactOut, which
        Jupiter frequently cannot route for memecoins ("No route found").
        Selling an exact quote amount is ExactIn — routable whenever
        liquidity exists — and it spends exactly what we budgeted.

        Fresh mints are auto-registered in gateway's token list on the
        first "Token not found" and retried once.
        """
        from condor.executors.gateway import GatewayError

        cfg = self.config
        s = self.state

        async def _swap_in():
            return await self.gateway.execute_swap(
                chain_network=cfg.chain_network,
                wallet_address=cfg.wallet_address,
                base_token=cfg.quote_token,   # sell SOL/USDC...
                quote_token=cfg.base_token,   # ...for the memecoin
                amount=float(cfg.amount_quote),
                side="SELL",
                connector=cfg.connector,
                slippage_pct=float(cfg.slippage_pct),
            )

        try:
            result = await _swap_in()
        except GatewayError as e:
            if "token not found" not in (e.body or "").lower():
                raise
            logger.info("position %s: registering unknown token %s in gateway",
                        self.id, cfg.base_token)
            await self.gateway.save_token(cfg.chain_network, cfg.base_token)
            result = await _swap_in()

        data = result.get("data") or {}
        s.open_tx_hash = result["signature"]
        # Cost basis = the exact amount we routed INTO the swap. This is an
        # ExactIn SELL of cfg.amount_quote, so that is the swap input by
        # construction. Do NOT use gateway's amountIn here: for a SOL-input
        # swap it reports the gross wallet debit (swap + refundable token-
        # account rent + network fee), which inflates entry_price ~10-20% and
        # makes a fresh position read as an instant stop-loss against its own
        # sell quote. Rent is a refundable deposit, not part of the price; the
        # network fee is tracked separately in tx_fee.
        s.quote_spent = cfg.amount_quote
        s.base_bought = Decimal(str(data.get("amountOut", 0)))
        s.tx_fee += Decimal(str(data.get("fee", 0)))
        if s.base_bought <= 0:
            raise RuntimeError(f"buy returned no base: {result}")
        s.entry_price = s.quote_spent / s.base_bought
        s.opened_at = time.time()
        s.state = PositionStates.ACTIVE
        logger.info(
            "position %s: opened %.10f %s @ %.10f (%s %s) sig=%s",
            self.id, s.base_bought, cfg.base_token, s.entry_price,
            s.quote_spent, cfg.quote_token, s.open_tx_hash,
        )
        tp = f"+{cfg.take_profit_pct * 100:.1f}%" if cfg.take_profit_pct is not None else "—"
        sl = f"-{cfg.stop_loss_pct * 100:.1f}%" if cfg.stop_loss_pct is not None else "—"
        ttl = f"{cfg.time_limit_s}s" if cfg.time_limit_s is not None else "—"
        await self.notify_trade(
            f"🟢 Entered {cfg.base_token[:8]} — {cfg.amount_quote} {cfg.quote_token} "
            f"@ {s.entry_price:.6g} (TP {tp} / SL {sl} / TTL {ttl})"
        )

    async def _control_barriers(self) -> None:
        """Hummingbot barrier order: SL -> trailing -> TP, then time limit.

        Time limit is enforced even when the price fetch fails — a
        position must never outlive its TTL because a feed hiccuped.
        """
        cfg = self.config
        s = self.state
        price_ok = True
        try:
            quote = await self.gateway.quote_swap(
                chain_network=cfg.chain_network,
                base_token=cfg.base_token,
                quote_token=cfg.quote_token,
                amount=float(s.base_bought),
                side="SELL",
            )
            # Execution-aware: what our actual size would fetch right now
            s.last_price = Decimal(str(quote["price"]))
            s.pnl_pct = s.last_price / s.entry_price - 1
        except Exception as e:
            price_ok = False
            logger.warning("position %s: price fetch failed: %s", self.id, e)

        if price_ok:
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
        from condor.executors.gateway import GatewayError

        cfg = self.config
        s = self.state

        async def _swap_out():
            return await self.gateway.execute_swap(
                chain_network=cfg.chain_network,
                wallet_address=cfg.wallet_address,
                base_token=cfg.base_token,
                quote_token=cfg.quote_token,
                amount=float(s.base_bought),
                side="SELL",
                connector=cfg.connector,
                slippage_pct=float(cfg.slippage_pct),
            )

        try:
            result = await _swap_out()
        except GatewayError as e:
            if "token not found" not in (e.body or "").lower():
                raise
            await self.gateway.save_token(cfg.chain_network, cfg.base_token)
            result = await _swap_out()
        data = result.get("data") or {}
        s.close_tx_hash = result["signature"]
        s.quote_returned = Decimal(str(data.get("amountOut", 0)))
        s.tx_fee += Decimal(str(data.get("fee", 0)))
        if s.base_bought > 0 and s.quote_returned:
            s.exit_price = s.quote_returned / s.base_bought
        logger.info(
            "position %s: closed (%s) returned %s %s sig=%s",
            self.id, s.close_type, s.quote_returned, cfg.quote_token, s.close_tx_hash,
        )
        pnl = self.net_pnl_quote()
        pnl_pct = (pnl / s.quote_spent * 100) if s.quote_spent else Decimal("0")
        emoji = "🔴" if pnl < 0 else "🟢"
        await self.notify_trade(
            f"{emoji} Exited {cfg.base_token[:8]} ({s.close_type}) — "
            f"{pnl:+.6g} {cfg.quote_token} ({pnl_pct:+.2f}%)"
        )
        s.state = PositionStates.COMPLETE

    # -- stop / reporting ------------------------------------------------------

    def early_stop(self, keep_position: bool = True) -> None:
        """keep_position=True: DETACH (tokens stay in the wallet, executor
        ends). keep_position=False: market-sell now."""
        s = self.state
        if s.state == PositionStates.ACTIVE:
            if keep_position:
                self.close_reason = (
                    f"stopped — holding {s.base_bought} {self.config.base_token} (detached)"
                )
                s.close_type = "detached"
                s.state = PositionStates.COMPLETE
            else:
                s.close_type = "early_stop"
                self.close_reason = "early stop (selling position)"
                s.state = PositionStates.CLOSING
        elif s.state == PositionStates.OPENING:
            self.fail("stopped while opening — verify wallet balance manually")
            s.state = PositionStates.FAILED
        elif s.state == PositionStates.NOT_ACTIVE:
            s.state = PositionStates.COMPLETE
            s.close_type = "early_stop"
            self.close_reason = "stopped before opening"

    def custom_info(self) -> dict[str, Any]:
        s = self.state
        return {
            "type": "position",
            "state": s.state.value,
            "pair": f"{self.config.base_token}-{self.config.quote_token}",
            "entry_price": float(s.entry_price) if s.entry_price else None,
            "last_price": float(s.last_price) if s.last_price else None,
            "pnl_pct": float(s.pnl_pct) if s.pnl_pct is not None else None,
            "base_bought": float(s.base_bought),
            "quote_spent": float(s.quote_spent),
            "quote_returned": float(s.quote_returned) if s.quote_returned is not None else None,
            "trailing_trigger_pct": float(s.trailing_trigger_pct)
            if s.trailing_trigger_pct is not None else None,
            "close_type": s.close_type,
            "open_tx_hash": s.open_tx_hash,
            "close_tx_hash": s.close_tx_hash,
            "unrealized_pnl_quote": float(self.net_pnl_quote()),
        }

    def net_pnl_quote(self) -> Decimal:
        s = self.state
        if s.quote_returned is not None:
            return s.quote_returned - s.quote_spent
        if s.last_price is not None and s.base_bought > 0:
            return s.base_bought * s.last_price - s.quote_spent
        return Decimal("0")
