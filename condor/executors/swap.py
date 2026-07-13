"""One-shot swap executor: quote -> execute -> confirmed -> CLOSED.

Exists so one-off swaps get the same lifecycle, persistence and
accounting as every other executor (see docs/condor-simple.md §3).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel

from condor.executors.base import ExecutorBase, ExecutorConfig, ExecutorStatus, RiskDeclaration

logger = logging.getLogger(__name__)


class SwapConfig(ExecutorConfig):
    type: Literal["swap"] = "swap"
    base_token: str
    quote_token: str
    amount: Decimal  # amount of base token
    side: Literal["BUY", "SELL"]
    connector: Optional[str] = None  # None -> gateway's default swap router
    slippage_pct: Decimal = Decimal("1.0")
    # Declared notional in quote units — required for risk gating (the
    # gate cannot price base units statically). The REST create route
    # fills it from a live gateway quote when omitted.
    notional_quote: Optional[Decimal] = None
    update_interval: float = 2.0

    def risk_declaration(self) -> RiskDeclaration:
        if self.notional_quote is None:
            raise ValueError(
                "swap config must declare notional_quote (quote-unit value of "
                "the swap) so the risk gate can bound it"
            )
        # Worst case loss on a confirmed swap is slippage + tx fee.
        return RiskDeclaration(
            max_notional_quote=self.notional_quote,
            max_loss_quote=self.notional_quote * self.slippage_pct / Decimal("100"),
        )


class SwapState(BaseModel):
    phase: Literal["created", "submitting", "confirming", "done", "failed"] = "created"
    quoted_price: Optional[Decimal] = None
    signature: Optional[str] = None
    amount_in: Optional[Decimal] = None
    amount_out: Optional[Decimal] = None
    fee: Optional[Decimal] = None


class SwapExecutor(ExecutorBase):
    def __init__(self, executor_id, config: SwapConfig, gateway, store):
        super().__init__(executor_id, config, gateway, store)
        self.config: SwapConfig = config
        self.state = SwapState()

    def state_model(self) -> BaseModel:
        return self.state

    def restore_state(self, state: dict) -> None:
        self.state = SwapState(**state)

    async def control_task(self) -> None:
        if self.state.phase == "created":
            quote = await self.gateway.quote_swap(
                chain_network=self.config.chain_network,
                base_token=self.config.base_token,
                quote_token=self.config.quote_token,
                amount=float(self.config.amount),
                side=self.config.side,
                connector=self.config.connector,
                slippage_pct=float(self.config.slippage_pct),
            )
            self.state.quoted_price = Decimal(str(quote["price"]))
            # Persist intent BEFORE the irreversible call so a crash inside
            # execute_swap leaves an explicit "submitting" orphan, never a
            # silently unknown one.
            self.state.phase = "submitting"
            self.persist()

            result = await self.gateway.execute_swap(
                chain_network=self.config.chain_network,
                wallet_address=self.config.wallet_address,
                base_token=self.config.base_token,
                quote_token=self.config.quote_token,
                amount=float(self.config.amount),
                side=self.config.side,
                connector=self.config.connector,
                slippage_pct=float(self.config.slippage_pct),
            )
            self.state.signature = result["signature"]
            data = result.get("data") or {}
            self.state.amount_in = Decimal(str(data.get("amountIn", 0)))
            self.state.amount_out = Decimal(str(data.get("amountOut", 0)))
            self.state.fee = Decimal(str(data.get("fee", 0)))
            if result.get("status") == 1:
                self.state.phase = "done"
                self.status = ExecutorStatus.CLOSED
                self.close_reason = "swap confirmed"
                logger.info(
                    "swap %s confirmed: %s %s %s->%s sig=%s",
                    self.id, self.config.side, self.config.amount,
                    self.config.base_token, self.config.quote_token, self.state.signature,
                )
            else:
                self.state.phase = "confirming"

        elif self.state.phase in ("submitting", "confirming"):
            # submitting with a signature or unconfirmed execute: poll the chain
            if not self.state.signature:
                self.fail("crashed during submission with no signature — check wallet activity manually")
                self.state.phase = "failed"
                return
            chain = self.config.chain_network.split("-", 1)[0]
            poll = await self.gateway.poll_tx(chain, self.config.network, self.state.signature)
            tx_status = poll.get("txStatus")
            if tx_status == 1:
                self.state.phase = "done"
                self.status = ExecutorStatus.CLOSED
                self.close_reason = "swap confirmed (polled)"
            elif tx_status == -1:
                self.state.phase = "failed"
                self.fail(f"swap tx failed on-chain: {self.state.signature}")
            # else still pending — next tick polls again

    def early_stop(self, keep_position: bool = True) -> None:
        # A swap in flight cannot be cancelled; only an unsubmitted one can.
        if self.state.phase == "created":
            self.status = ExecutorStatus.CLOSED
            self.close_reason = "stopped before submission"

    def custom_info(self) -> dict[str, Any]:
        return {
            "type": "swap",
            "phase": self.state.phase,
            "side": self.config.side,
            "pair": f"{self.config.base_token}-{self.config.quote_token}",
            "amount": float(self.config.amount),
            "quoted_price": float(self.state.quoted_price) if self.state.quoted_price else None,
            "amount_in": float(self.state.amount_in) if self.state.amount_in else None,
            "amount_out": float(self.state.amount_out) if self.state.amount_out else None,
            "fee": float(self.state.fee) if self.state.fee else None,
            "signature": self.state.signature,
        }

    def net_pnl_quote(self) -> Decimal:
        return Decimal("0")
