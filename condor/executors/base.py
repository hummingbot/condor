"""Executor contract: config schema, lifecycle, risk declaration, run loop.

Lifecycle (persisted on every transition):

    PENDING -> ACTIVE -> CLOSING -> CLOSED
                  \\----------------> FAILED

Per-type internal state machines (e.g. LP's OPENING/IN_RANGE/...) live in
the executor's state model and map onto this coarse lifecycle for the
store, the runtime, and — later — the risk gate and journal.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from condor.executors.gateway import GatewayClient
    from condor.executors.store import ExecutorStore

logger = logging.getLogger(__name__)


class ExecutorStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (ExecutorStatus.CLOSED, ExecutorStatus.FAILED)


@dataclass
class RiskDeclaration:
    """Platform-computable risk bounds derived from an executor config.

    Computed at creation time so a gate can approve the whole intent
    once; the runtime refuses to grow exposure past it.
    """

    max_notional_quote: Decimal
    max_loss_quote: Decimal


class ExecutorConfig(BaseModel):
    """Base config for every executor type — the declarative trade intent."""

    type: str
    chain_network: str  # gateway format, e.g. "solana-mainnet-beta"
    wallet_address: str
    # Attribution (tracking keys, NOT permission boundaries):
    #   agent_slug — WHO:      "lp_rebalancer";           "" for chat/manual
    #   agent_id   — WHICH RUN: session "{slug}_{N}" or delegation
    #                "{slug}-dN";                          "" for chat/manual
    #   strategy   — WHICH PLAYBOOK: session-created only; "" otherwise
    #                (delegations run no playbook — that is the truth)
    agent_slug: str = ""
    agent_id: str = ""
    strategy: str = ""
    update_interval: float = 5.0
    max_retries: int = 3

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def risk_declaration(self) -> RiskDeclaration:
        raise NotImplementedError

    @property
    def network(self) -> str:
        """Network portion of chain_network ("solana-mainnet-beta" -> "mainnet-beta")."""
        return self.chain_network.split("-", 1)[1]


def new_executor_id(type_: str) -> str:
    return f"{type_}_{int(time.time())}_{uuid.uuid4().hex[:6]}"


class ExecutorBase:
    """Async control loop + persistence + heartbeat for one executor.

    Subclasses implement control_task() (one iteration of the state
    machine), state_model() (the pydantic state persisted as JSON) and
    custom_info() (the reporting shape the journal consumes).
    """

    def __init__(
        self,
        executor_id: str,
        config: ExecutorConfig,
        gateway: "GatewayClient",
        store: "ExecutorStore",
    ):
        self.id = executor_id
        self.config = config
        self.gateway = gateway
        self.store = store
        self.status = ExecutorStatus.PENDING
        self.close_reason: Optional[str] = None
        self._retries = 0
        self._stop_requested = False

    # -- subclass surface -------------------------------------------------

    async def control_task(self) -> None:
        raise NotImplementedError

    def state_model(self) -> BaseModel:
        raise NotImplementedError

    def restore_state(self, state: dict) -> None:
        raise NotImplementedError

    def custom_info(self) -> dict[str, Any]:
        raise NotImplementedError

    def net_pnl_quote(self) -> Decimal:
        raise NotImplementedError

    def early_stop(self, keep_position: bool = True) -> None:
        """Request a graceful close; the loop drives it to a terminal state."""
        raise NotImplementedError

    # -- lifecycle ---------------------------------------------------------

    def persist(self) -> None:
        self.store.save(self)

    async def run(self) -> None:
        """Run the control loop until a terminal state.

        Every iteration persists state + heartbeat, so a killed process
        can be reconciled from the store on restart.
        """
        if self.status == ExecutorStatus.PENDING:
            self.status = ExecutorStatus.ACTIVE
        self.persist()
        logger.info("executor %s started (%s)", self.id, self.config.type)

        while not self.status.is_terminal:
            try:
                await self.control_task()
                self._retries = 0
            except asyncio.CancelledError:
                # Process shutdown, NOT a close: position (if any) stays
                # open on-chain and is re-adopted by reconcile() later.
                self.persist()
                logger.warning(
                    "executor %s cancelled mid-flight; state persisted for reconcile", self.id
                )
                raise
            except Exception as e:
                self._retries += 1
                logger.error(
                    "executor %s control_task error (%d/%d): %s",
                    self.id, self._retries, self.config.max_retries, e,
                )
                if self._retries >= self.config.max_retries:
                    self.fail(f"max retries reached: {e}")
            self.persist()
            if self.status.is_terminal:
                break
            await asyncio.sleep(self.config.update_interval)

        logger.info(
            "executor %s finished: %s (%s)", self.id, self.status.value, self.close_reason or "-"
        )
        self.persist()

    def fail(self, reason: str) -> None:
        self.status = ExecutorStatus.FAILED
        self.close_reason = reason
