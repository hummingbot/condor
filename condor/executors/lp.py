"""LP position executor — port of hummingbot's LPExecutor onto gateway HTTP.

One executor = one position lifecycle: open on start, monitor
IN_RANGE/OUT_OF_RANGE, auto-close past limit prices, optional close-out
swap back to the entry asset mix (keep_position=False). Rebalancing
(close here, reopen there) stays a caller concern, planned with
condor.executors.ranges.

Source: hummingbot/strategy_v2/executors/lp_executor/ (state machine,
P&L math, close-out swap logic) with the connector/event plumbing
replaced by direct GatewayClient calls.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel

from condor.executors.base import ExecutorBase, ExecutorConfig, ExecutorStatus, RiskDeclaration
from condor.executors.gateway import GatewayError

logger = logging.getLogger(__name__)

SWAP_DUST_THRESHOLD = Decimal("0.000001")


class LpStates(str, Enum):
    NOT_ACTIVE = "NOT_ACTIVE"
    OPENING = "OPENING"
    IN_RANGE = "IN_RANGE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    CLOSING = "CLOSING"
    SWAPPING = "SWAPPING"  # close-out swap in progress (keep_position=False)
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class LpConfig(ExecutorConfig):
    type: Literal["lp"] = "lp"
    connector: str  # clmm dex: "raydium" | "meteora" | "orca" | ...
    pool_address: str
    trading_pair: str  # "SOL-USDC"
    lower_price: Decimal
    upper_price: Decimal
    base_amount: Decimal = Decimal("0")
    quote_amount: Decimal = Decimal("0")
    # Close when price crosses these (None = no limit on that side)
    upper_limit_price: Optional[Decimal] = None
    lower_limit_price: Optional[Decimal] = None
    slippage_pct: Decimal = Decimal("1.0")
    extra_params: Optional[dict] = None  # e.g. {"strategyType": 0} for meteora
    # True: track net token change when closed. False: swap back to entry mix.
    keep_position: bool = True
    swap_connector: Optional[str] = None  # close-out swap router (None -> gateway default)

    def risk_declaration(self) -> RiskDeclaration:
        mid = (self.lower_price + self.upper_price) / 2
        notional = self.base_amount * mid + self.quote_amount
        # Worst case bounded by the limit prices when both are set;
        # a memecoin pool with no lower limit can lose the full notional.
        return RiskDeclaration(max_notional_quote=notional, max_loss_quote=notional)

    @property
    def base_token(self) -> str:
        return self.trading_pair.split("-")[0]

    @property
    def quote_token(self) -> str:
        return self.trading_pair.split("-")[1]


class LpState(BaseModel):
    state: LpStates = LpStates.NOT_ACTIVE
    position_address: Optional[str] = None
    lower_price: Decimal = Decimal("0")
    upper_price: Decimal = Decimal("0")
    base_amount: Decimal = Decimal("0")
    quote_amount: Decimal = Decimal("0")
    base_fee: Decimal = Decimal("0")
    quote_fee: Decimal = Decimal("0")
    # Fixed at ADD time for P&L (base/quote_amount drift as price moves)
    initial_base_amount: Decimal = Decimal("0")
    initial_quote_amount: Decimal = Decimal("0")
    add_mid_price: Decimal = Decimal("0")
    position_rent: Decimal = Decimal("0")
    position_rent_refunded: Decimal = Decimal("0")
    tx_fee: Decimal = Decimal("0")  # cumulative, native currency
    open_tx_hash: Optional[str] = None
    close_tx_hash: Optional[str] = None
    swap_tx_hash: Optional[str] = None
    # Raw wallet balance changes from the open/close responses — the
    # ground truth for cost accounting. Includes ALL rents (gateway's
    # positionRent reports only one of several rent accounts; on raydium
    # ~0.0166 SOL of NFT metadata+mint rent is never refunded).
    open_wallet_base_change: Optional[Decimal] = None
    open_wallet_quote_change: Optional[Decimal] = None
    close_wallet_base_change: Optional[Decimal] = None
    close_wallet_quote_change: Optional[Decimal] = None
    out_of_range_since: Optional[float] = None
    # Net trade record built at close (the journal's accounting unit)
    net_trade: Optional[dict] = None

    def out_of_range_seconds(self, now: float) -> Optional[int]:
        if self.out_of_range_since is None:
            return None
        return int(now - self.out_of_range_since)


class LpExecutor(ExecutorBase):
    def __init__(self, executor_id, config: LpConfig, gateway, store):
        super().__init__(executor_id, config, gateway, store)
        self.config: LpConfig = config
        self.state = LpState()
        self._current_price: Optional[Decimal] = None
        self._native_to_quote_rate: Optional[Decimal] = None

    def state_model(self) -> BaseModel:
        return self.state

    def restore_state(self, state: dict) -> None:
        self.state = LpState(**state)

    # -- control loop --------------------------------------------------------

    async def control_task(self) -> None:
        now = time.time()

        if self.state.position_address:
            await self._update_position_info()
        elif self.state.state in (LpStates.NOT_ACTIVE, LpStates.OPENING):
            await self._update_pool_price()

        self._update_state(now)

        match self.state.state:
            case LpStates.NOT_ACTIVE:
                self.state.state = LpStates.OPENING
                self.persist()
                await self._create_position()

            case LpStates.OPENING:
                await self._create_position()

            case LpStates.CLOSING:
                self.status = ExecutorStatus.CLOSING
                await self._close_position()

            case LpStates.SWAPPING:
                self.status = ExecutorStatus.CLOSING
                await self._execute_closeout_swap()

            case LpStates.FAILED:
                self.fail(self.close_reason or "lp state machine failed")

            case LpStates.IN_RANGE:
                pass  # monitoring only

            case LpStates.OUT_OF_RANGE:
                self._check_limit_prices()

            case LpStates.COMPLETE:
                self.status = ExecutorStatus.CLOSED
                if not self.close_reason:
                    self.close_reason = "position closed"

    def _update_state(self, now: float) -> None:
        """Derive IN/OUT_OF_RANGE from price; preserve explicit states."""
        s = self.state
        if s.state in (
            LpStates.COMPLETE, LpStates.CLOSING, LpStates.SWAPPING,
            LpStates.FAILED, LpStates.OPENING, LpStates.NOT_ACTIVE,
        ):
            return
        if s.position_address and self._current_price is not None:
            if self._current_price < s.lower_price or self._current_price > s.upper_price:
                s.state = LpStates.OUT_OF_RANGE
                if s.out_of_range_since is None:
                    s.out_of_range_since = now
            else:
                s.state = LpStates.IN_RANGE
                s.out_of_range_since = None

    def _check_limit_prices(self) -> None:
        price = self._current_price
        if price is None:
            return
        cfg = self.config
        if cfg.upper_limit_price is not None and price >= cfg.upper_limit_price:
            direction = f"above upper limit {cfg.upper_limit_price}"
        elif cfg.lower_limit_price is not None and price <= cfg.lower_limit_price:
            direction = f"below lower limit {cfg.lower_limit_price}"
        else:
            return
        logger.info("lp %s: price %s %s — closing", self.id, price, direction)
        self.close_reason = f"limit price hit: {direction}"
        self.state.state = LpStates.CLOSING

    # -- gateway operations ----------------------------------------------------

    async def _update_pool_price(self) -> None:
        pool = await self.gateway.clmm_pool_info(
            self.config.connector, self.config.chain_network, self.config.pool_address
        )
        self._current_price = Decimal(str(pool["price"]))

    async def _update_position_info(self) -> None:
        try:
            info = await self.gateway.clmm_position_info(
                self.config.connector, self.config.chain_network, self.state.position_address
            )
        except GatewayError as e:
            msg = (e.body or "").lower()
            if "position closed" in msg or "not found" in msg:
                # Closed on-chain outside our flow (or timeout-but-succeeded close)
                logger.info("lp %s: position %s confirmed closed on-chain",
                            self.id, self.state.position_address)
                self._finalize_close_from_last_known()
                return
            raise
        self.state.base_amount = Decimal(str(info["baseTokenAmount"]))
        self.state.quote_amount = Decimal(str(info["quoteTokenAmount"]))
        self.state.base_fee = Decimal(str(info["baseFeeAmount"]))
        self.state.quote_fee = Decimal(str(info["quoteFeeAmount"]))
        self.state.lower_price = Decimal(str(info["lowerPrice"]))
        self.state.upper_price = Decimal(str(info["upperPrice"]))
        self._current_price = Decimal(str(info["price"]))

    async def _create_position(self) -> None:
        cfg = self.config
        if self._current_price is None:
            await self._update_pool_price()
        logger.info(
            "lp %s: opening position [%s - %s] base=%s quote=%s on %s",
            self.id, cfg.lower_price, cfg.upper_price, cfg.base_amount, cfg.quote_amount,
            cfg.connector,
        )
        result = await self.gateway.clmm_open(
            connector=cfg.connector,
            chain_network=cfg.chain_network,
            wallet_address=cfg.wallet_address,
            pool_address=cfg.pool_address,
            lower_price=float(cfg.lower_price),
            upper_price=float(cfg.upper_price),
            base_token_amount=float(cfg.base_amount) if cfg.base_amount else None,
            quote_token_amount=float(cfg.quote_amount) if cfg.quote_amount else None,
            slippage_pct=float(cfg.slippage_pct),
            extra_params=cfg.extra_params,
        )
        data = result.get("data") or {}
        position_address = data.get("positionAddress")
        if not position_address:
            raise RuntimeError(f"clmm_open returned no positionAddress: {result}")

        s = self.state
        s.position_address = position_address
        s.open_tx_hash = result["signature"]
        s.position_rent = Decimal(str(data.get("positionRent", 0)))
        s.tx_fee += Decimal(str(data.get("fee", 0)))
        if "baseTokenAmountAdded" in data:
            s.open_wallet_base_change = Decimal(str(data["baseTokenAmountAdded"]))
        if "quoteTokenAmountAdded" in data:
            s.open_wallet_quote_change = Decimal(str(data["quoteTokenAmountAdded"]))
        s.lower_price = cfg.lower_price
        s.upper_price = cfg.upper_price
        s.add_mid_price = self._current_price or (cfg.lower_price + cfg.upper_price) / 2

        # Refresh from chain for actual bounds/amounts, then freeze those as
        # the initial deposit for P&L. (Do NOT use the open response's
        # base/quoteTokenAmountAdded: connectors like raydium report wallet
        # balance CHANGES there — negative, and including rent/ATA costs.)
        await self._update_position_info()
        s.initial_base_amount = s.base_amount
        s.initial_quote_amount = s.quote_amount
        s.state = LpStates.IN_RANGE  # corrected on next _update_state if not
        logger.info(
            "lp %s: position created %s rent=%s base=%s quote=%s sig=%s",
            self.id, position_address, s.position_rent, s.base_amount, s.quote_amount,
            s.open_tx_hash,
        )

    async def _close_position(self) -> None:
        s = self.state
        # Verify the position still exists (handles timeout-but-succeeded)
        try:
            await self.gateway.clmm_position_info(
                self.config.connector, self.config.chain_network, s.position_address
            )
        except GatewayError as e:
            msg = (e.body or "").lower()
            if "position closed" in msg or "not found" in msg:
                logger.info("lp %s: position already closed — skipping close", self.id)
                self._finalize_close_from_last_known()
                return
            raise

        result = await self.gateway.clmm_close(
            connector=self.config.connector,
            chain_network=self.config.chain_network,
            wallet_address=self.config.wallet_address,
            position_address=s.position_address,
        )
        data = result.get("data") or {}
        s.close_tx_hash = result["signature"]
        s.position_rent_refunded = Decimal(str(data.get("positionRentRefunded", 0)))
        # NB: base/quoteTokenAmountRemoved are wallet balance changes (they
        # include SOL rent refunds), NOT position composition. Keep the last
        # position-info reading (refreshed at the top of this tick) as the
        # position amounts for net-trade accounting; record the raw wallet
        # changes alongside.
        s.base_fee = Decimal(str(data.get("baseFeeAmountCollected", 0)))
        s.quote_fee = Decimal(str(data.get("quoteFeeAmountCollected", 0)))
        s.tx_fee += Decimal(str(data.get("fee", 0)))
        if "baseTokenAmountRemoved" in data:
            s.close_wallet_base_change = Decimal(str(data["baseTokenAmountRemoved"]))
        if "quoteTokenAmountRemoved" in data:
            s.close_wallet_quote_change = Decimal(str(data["quoteTokenAmountRemoved"]))
        logger.info(
            "lp %s: position closed, returned base=%s quote=%s fees=%s/%s rent_refund=%s sig=%s",
            self.id, s.base_amount, s.quote_amount, s.base_fee, s.quote_fee,
            s.position_rent_refunded, s.close_tx_hash,
        )
        self._record_net_trade()
        s.position_address = None

        if not self.config.keep_position and abs(self._net_base_difference()) > SWAP_DUST_THRESHOLD:
            s.state = LpStates.SWAPPING
        else:
            s.state = LpStates.COMPLETE

    async def _execute_closeout_swap(self) -> None:
        """Swap back to the entry asset mix when keep_position=False."""
        base_diff = self._net_base_difference()
        if abs(base_diff) < SWAP_DUST_THRESHOLD:
            self.state.state = LpStates.COMPLETE
            return
        side = "BUY" if base_diff < 0 else "SELL"
        amount = abs(base_diff)
        logger.info("lp %s: close-out swap %s %s %s", self.id, side, amount, self.config.base_token)
        result = await self.gateway.execute_swap(
            chain_network=self.config.chain_network,
            wallet_address=self.config.wallet_address,
            base_token=self.config.base_token,
            quote_token=self.config.quote_token,
            amount=float(amount),
            side=side,
            connector=self.config.swap_connector,
            slippage_pct=float(self.config.slippage_pct),
        )
        self.state.swap_tx_hash = result["signature"]
        data = result.get("data") or {}
        self.state.tx_fee += Decimal(str(data.get("fee", 0)))
        logger.info("lp %s: close-out swap confirmed sig=%s", self.id, self.state.swap_tx_hash)
        self.state.state = LpStates.COMPLETE

    # -- close accounting -------------------------------------------------------

    def _finalize_close_from_last_known(self) -> None:
        """Position vanished on-chain: close out using last known amounts."""
        self._record_net_trade()
        self.state.position_address = None
        self.state.state = LpStates.COMPLETE
        if not self.close_reason:
            self.close_reason = "position closed on-chain (reconciled)"

    def _net_base_difference(self) -> Decimal:
        """received base (incl. fees) - deposited base. >0: sell excess, <0: buy back."""
        return (self.state.base_amount + self.state.base_fee) - self.state.initial_base_amount

    def _record_net_trade(self) -> None:
        """Collapse ADD/REMOVE into one net trade dict (journal accounting unit)."""
        s = self.state
        threshold = Decimal("0.0001")
        net_base = (s.base_amount + s.base_fee) - s.initial_base_amount
        net_quote = (s.quote_amount + s.quote_fee) - s.initial_quote_amount
        mid = float(self._current_price or s.add_mid_price or 0)

        if abs(net_base) < threshold and abs(net_quote) < threshold:
            trade = None
        elif net_base > threshold and net_quote < -threshold:
            trade = {"trade_type": "BUY", "amount_base": float(net_base),
                     "amount_quote": float(abs(net_quote)),
                     "price": float(abs(net_quote) / net_base)}
        elif net_base < -threshold and net_quote > threshold:
            trade = {"trade_type": "SELL", "amount_base": float(abs(net_base)),
                     "amount_quote": float(net_quote),
                     "price": float(net_quote / abs(net_base))}
        elif abs(net_base) > threshold:
            trade = {"trade_type": "BUY" if net_base > 0 else "SELL",
                     "amount_base": float(abs(net_base)),
                     "amount_quote": float(abs(net_base)) * mid, "price": mid}
        else:
            trade = {"trade_type": "BUY", "amount_base": 0.0,
                     "amount_quote": float(abs(net_quote)), "price": mid}

        if trade is not None:
            trade["lp_source"] = True
            trade["position_address"] = s.position_address
        s.net_trade = trade

    # -- stop / reporting ---------------------------------------------------------

    def early_stop(self, keep_position: bool = True) -> None:
        s = self.state
        if not keep_position:
            # Force close-out swap path regardless of config
            self.config = self.config.model_copy(update={"keep_position": False})
        if s.state in (LpStates.IN_RANGE, LpStates.OUT_OF_RANGE):
            self.close_reason = "early stop"
            s.state = LpStates.CLOSING
        elif s.state == LpStates.OPENING:
            self.fail("stopped while opening — verify on-chain state manually")
            s.state = LpStates.FAILED
        elif s.state == LpStates.NOT_ACTIVE:
            s.state = LpStates.COMPLETE
            self.close_reason = "stopped before opening"

    def custom_info(self) -> dict[str, Any]:
        s = self.state
        price = float(self._current_price) if self._current_price else None
        return {
            "type": "lp",
            "state": s.state.value,
            "position_address": s.position_address,
            "pool_address": self.config.pool_address,
            "trading_pair": self.config.trading_pair,
            "current_price": price,
            "lower_price": float(s.lower_price),
            "upper_price": float(s.upper_price),
            "base_amount": float(s.base_amount),
            "quote_amount": float(s.quote_amount),
            "base_fee": float(s.base_fee),
            "quote_fee": float(s.quote_fee),
            "unrealized_pnl_quote": float(self.net_pnl_quote()),
            "out_of_range_seconds": s.out_of_range_seconds(time.time()),
            "tx_fee": float(s.tx_fee),
            "open_tx_hash": s.open_tx_hash,
            "close_tx_hash": s.close_tx_hash,
            "net_trade": s.net_trade,
        }

    def net_pnl_quote(self) -> Decimal:
        """(current value + fees earned) - initial value, in pool quote.

        Native-currency tx fees are reported separately in custom_info
        (tx_fee); converting them to quote needs an FX rate and is the
        journal's concern, not the executor's.
        """
        s = self.state
        if self._current_price is None or self._current_price == 0:
            return Decimal("0")
        if s.state == LpStates.FAILED and not s.position_address:
            return Decimal("0")
        add_price = s.add_mid_price if s.add_mid_price > 0 else self._current_price
        initial_base = s.initial_base_amount
        initial_quote = s.initial_quote_amount
        if initial_base == 0 and initial_quote == 0:
            initial_base = self.config.base_amount
            initial_quote = self.config.quote_amount
        initial_value = initial_base * add_price + initial_quote
        current_value = s.base_amount * self._current_price + s.quote_amount
        fees_earned = s.base_fee * self._current_price + s.quote_fee
        return current_value + fees_earned - initial_value
