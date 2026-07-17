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

from condor.accounts.model import AccountRef

if TYPE_CHECKING:
    from condor.executors.log import ExecutorLog

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


def validate_risk_declaration(declaration: RiskDeclaration) -> RiskDeclaration:
    """Reject malformed declarations before they can reach a venue or reduce a
    risk snapshot. Zero loss is valid for fee-free conversions; notional is not."""
    notional = declaration.max_notional_quote
    max_loss = declaration.max_loss_quote
    if not notional.is_finite() or notional <= 0:
        raise ValueError("executor notional must be a positive finite amount")
    if not max_loss.is_finite() or max_loss < 0:
        raise ValueError("executor max loss must be a non-negative finite amount")
    return declaration


class ExecutorConfig(BaseModel):
    """Base config for every executor type — the declarative trade intent.

    ``type`` is a composite ``{kind}_{instrument}``:
      kind       = order    (single leg, place-and-track) | position (round-trip barriers)
      instrument = spot     | perp | pred
    The **kind** selects the executor class; **(instrument, venue)** selects the
    connector/adapter. So there are six types — order_spot, order_perp,
    order_pred, position_spot, position_perp, position_pred.
    """

    type: str
    # Which exchange executes this intent. The (instrument, venue) pair selects
    # the connector: solana→Jupiter; hyperliquid→perp/spot/outcome by instrument;
    # polymarket→Polymarket CLOB. Per-type configs narrow the default.
    venue: str = "solana"
    # Resolved by ExecutionService from the server-side run capability (agent)
    # or structured account store (condor-direct). Callers may select an
    # account, but this canonical identity is injected and persisted by the
    # platform before any executor starts.
    account_ref: AccountRef | None = None
    chain_network: str  # e.g. "solana-mainnet-beta"; unused for non-chain venues
    wallet_address: str
    # Attribution (tracking keys, NOT permission boundaries):
    #   agent_slug — WHO:      "memecoin_trender";         "" for chat/manual
    #   agent_id   — WHICH RUN: session "{slug}_{N}" or delegation
    #                "{slug}-dN";                          "" for chat/manual
    #   strategy   — WHICH PLAYBOOK: session-created only; "" otherwise
    #                (delegations run no playbook — that is the truth)
    agent_slug: str = ""
    agent_id: str = ""
    strategy: str = ""
    # Creation context (§6.2): "agent" (run capability) or "condor" (direct).
    # Derived by ops.create from the server-side capability entry — NEVER from
    # caller-supplied fields. Empty only on legacy records.
    origin: str = ""
    # Immutable server-side authority owner used to scope idempotent replay:
    # agent run id or condor-direct control connection id.
    capability_owner: str = ""
    # Canonical create-request hash (§6.2): the executor_id is bound to this
    # at create; a replay with the same id + same hash returns the original
    # result, a different hash is rejected.
    request_hash: str = ""
    # Emit a notification on position open/close. Default on; set false to
    # silence a high-frequency strategy that would otherwise spam every fill.
    notify_trades: bool = True
    update_interval: float = 5.0
    max_retries: int = 3

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def risk_declaration(self) -> RiskDeclaration:
        raise NotImplementedError

    @property
    def kind(self) -> str:
        """'order' or 'position' — the first half of the composite type."""
        return self.type.split("_", 1)[0]

    @property
    def instrument(self) -> str:
        """'spot' | 'perp' | 'pred' — the second half of the composite type."""
        parts = self.type.split("_", 1)
        return parts[1] if len(parts) > 1 else ""

    @property
    def network(self) -> str:
        """Network portion of chain_network ("solana-mainnet-beta" -> "mainnet-beta")."""
        return self.chain_network.split("-", 1)[1]

    def instrument_id(self) -> str:
        """Canonical instrument identity for the lease key (§6.2b).

        Resolved through the venue package's ``normalize_instrument`` when
        the venue is registered (alias collapse: SOL/WSOL → one mint id, HL
        coins case-folded, polymarket token_id passthrough), so lease keys
        and attribution can never split across venue aliases. Falls back to
        the config's raw identity field (spot → "BASE-QUOTE"; perp → the
        coin; pred → the market/outcome id) when the venue has no spec or
        the spec has no opinion.
        """
        from condor.accounts.registry import UnknownVenueError
        from condor.venues.registry import venue_spec

        try:
            spec = venue_spec(self.venue)
        except UnknownVenueError:
            spec = None
        if spec is not None:
            normalized = spec.normalize_instrument(self)
            if normalized:
                return str(normalized)
        base = getattr(self, "base_token", None)
        quote = getattr(self, "quote_token", None)
        if base and quote:
            return f"{base}-{quote}"
        coin = getattr(self, "coin", None)
        if coin:
            return str(coin)
        market = getattr(self, "market", None)
        if market:
            return str(market)
        return self.type  # last resort — still lease-scoped per venue


class BarrierFields(BaseModel):
    """Shared triple-barrier + trailing knobs for every ``position``-kind config.

    Fractions of the entry price (hummingbot semantics: 0.03 = 3%). ``None``
    disables that barrier. Mixed into each PositionXxxConfig alongside
    ``ExecutorConfig`` so the generic PositionExecutor reads one field set.
    """

    take_profit_pct: Optional[Decimal] = None
    stop_loss_pct: Optional[Decimal] = None
    time_limit_s: Optional[int] = None
    # Trailing stop: activate when pnl_pct > activation_pct, then close when
    # pnl_pct falls trailing_delta_pct below its high-water mark.
    trailing_activation_pct: Optional[Decimal] = None
    trailing_delta_pct: Optional[Decimal] = None


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
        connector: Any,
        store: "ExecutorLog",
    ):
        self.id = executor_id
        self.config = config
        self.connector = connector
        self.store = store
        self.status = ExecutorStatus.PENDING
        self.close_reason: Optional[str] = None
        self._retries = 0
        self._stop_requested = False
        # Dedup guard for the append-only transaction log: persist() writes only
        # when the recovery-relevant snapshot changes, so volatile per-tick price
        # updates don't append a line every poll. None => nothing persisted yet.
        self._last_persist_key: Optional[tuple] = None
        # In-memory liveness signal for the watchdog — advanced every completed
        # loop iteration. Independent of the store so a healthy long-held
        # position (which no longer writes each tick) is never seen as stale.
        self._last_tick_at: float = time.time()

    # -- subclass surface -------------------------------------------------

    async def control_task(self) -> None:
        raise NotImplementedError

    def state_model(self) -> BaseModel:
        raise NotImplementedError

    def _recovery_key(self) -> tuple:
        """Recovery-relevant fields that, when changed, warrant a persist.

        Subclasses return their durable phase + tx identifiers (NOT volatile
        price/pnl). Combined with status + close_reason to dedup the log so a
        long-held position appends ~one line per phase transition, not per poll.
        """
        return ()

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
        """Snapshot to the store, but only when the recovery-relevant state
        changed since the last persist (append-only log stays small; SQLite
        just skips a redundant upsert). Terminal + every phase transition always
        change the key, so the store always holds the latest phase and the final
        state."""
        key = (self.status.value, self.close_reason, *self._recovery_key())
        if key == self._last_persist_key:
            return
        self._last_persist_key = key
        self.store.save(self)

    async def notify_trade(self, text: str) -> None:
        """Emit a trade event (position open/close) to the notifications
        outbox. No-op when notify_trades is off. Fail-soft:
        a notify error never touches the executor's lifecycle."""
        if not self.config.notify_trades:
            return
        try:
            from condor.notifications import notify

            await notify(
                text,
                agent_id=self.config.agent_id,
                kind="trade",
                origin=self.config.type,
            )
        except Exception:
            logger.warning("trade notify failed for %s", self.id, exc_info=True)

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
                    "executor %s cancelled mid-flight; state persisted for reconcile",
                    self.id,
                )
                raise
            except Exception as e:
                self._retries += 1
                logger.error(
                    "executor %s control_task error (%d/%d): %s",
                    self.id,
                    self._retries,
                    self.config.max_retries,
                    e,
                )
                if self._retries >= self.config.max_retries:
                    self.fail(f"max retries reached: {e}")
            self.persist()
            self._last_tick_at = (
                time.time()
            )  # completed an iteration (watchdog liveness)
            if self.status.is_terminal:
                break
            await asyncio.sleep(self.config.update_interval)

        logger.info(
            "executor %s finished: %s (%s)",
            self.id,
            self.status.value,
            self.close_reason or "-",
        )
        self.persist()
        if self.status == ExecutorStatus.FAILED:
            # An executor failure is an operator alert, not a trade ping — it
            # fires even with notify_trades off, and regardless of whether the
            # owning run is still alive (§9.5.3: the post-shutdown failures
            # landed after run_ended and were invisible everywhere but the
            # executor log). Fail-soft like notify_trade.
            try:
                from condor.notifications import notify

                await notify(
                    f"🚨 Executor {self.id} FAILED: "
                    f"{self.close_reason or 'unknown reason'} — verify the "
                    f"position on the venue manually",
                    agent_id=self.config.agent_id,
                    kind="error",
                    origin=self.config.type,
                )
            except Exception:
                logger.warning(
                    "failure notify failed for %s", self.id, exc_info=True
                )

    def fail(self, reason: str) -> None:
        self.status = ExecutorStatus.FAILED
        self.close_reason = reason
