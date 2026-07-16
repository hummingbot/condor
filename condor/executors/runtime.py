"""Executor runtime: task lifecycle, watchdog, restart reconciliation.

The runtime is what makes a killed Condor process safe: every executor
persists state each loop, and reconcile() re-adopts open positions from
the store + chain on startup. The watchdog flattens positions whose
executor task died or stalled.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from condor.executors.base import (
    ExecutorBase,
    ExecutorConfig,
    ExecutorStatus,
    new_executor_id,
    validate_risk_declaration,
)
from condor.executors.log import ExecutorLog
from condor.executors.order import (
    OrderExecutor,
    OrderPerpConfig,
    OrderPredConfig,
    OrderSpotConfig,
    OrderStates,
)
from condor.executors.position import (
    PositionExecutor,
    PositionPerpConfig,
    PositionPredConfig,
    PositionSpotConfig,
    PositionStates,
)
from condor.executors.records import ExecutorRecord

logger = logging.getLogger(__name__)

# type = {kind}_{instrument}: kind picks the executor class, (instrument, venue)
# picks the connector/adapter.
_EXECUTOR_TYPES: dict[str, tuple[type[ExecutorConfig], type[ExecutorBase]]] = {
    "order_spot": (OrderSpotConfig, OrderExecutor),
    "order_perp": (OrderPerpConfig, OrderExecutor),
    "order_pred": (OrderPredConfig, OrderExecutor),
    "position_spot": (PositionSpotConfig, PositionExecutor),
    "position_perp": (PositionPerpConfig, PositionExecutor),
    "position_pred": (PositionPredConfig, PositionExecutor),
}

# Heartbeat staler than this many update_intervals trips the watchdog
STALE_INTERVALS = 10
WATCHDOG_INTERVAL = 30.0


class ExecutorRuntime:
    def __init__(self, store: Optional[ExecutorLog] = None):
        from condor.executors.leases import LeaseManager

        self.store = store or ExecutorLog()
        self.leases = LeaseManager()
        self._executors: dict[str, ExecutorBase] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._watchdog_task: Optional[asyncio.Task] = None
        # Live connectors are bound per executor/account and never cached by
        # venue. Tests may inject deterministic connectors through the explicit
        # override map; production connector construction never populates it.
        self._connector_overrides: dict[tuple[str, str], object] = {}
        self._owned_connectors: dict[str, object] = {}

    # -- connector resolution ------------------------------------------------

    def connector_for(self, config: ExecutorConfig):
        if config.account_ref is None:
            raise ValueError("executor config has no resolved account_ref")
        return self.connector_for_spec(config.type, config.venue, config.account_ref)

    def connector_for_spec(self, type_: str, venue: Optional[str], account_ref=None):
        """Resolve the connector from (type, venue) through the loaded venue
        packages (§6.2b): the venue's spec builds the client for the
        instrument from the resolved account credentials. Unknown venue ids
        error (UnknownVenueError); a venue that does not claim the instrument
        errors too."""
        instrument = type_.split("_", 1)[1]
        venue = venue or "solana"
        key = (venue, instrument)
        override = self._connector_overrides.get(key)
        if override is not None:
            return override
        if account_ref is None:
            raise ValueError("connector construction requires a resolved account_ref")
        if account_ref.venue_id != venue:
            raise ValueError(
                f"account_ref venue {account_ref.venue_id!r} does not match "
                f"executor venue {venue!r}"
            )
        from condor.venues.registry import venue_spec

        spec = venue_spec(venue)  # raises UnknownVenueError for unregistered ids
        if instrument not in spec.adapter_factories:
            raise ValueError(
                f"no connector for type={type_!r} venue={venue!r} "
                f"(supported instruments: {sorted(spec.adapter_factories)})"
            )
        from condor.executors.wallets import account_credentials

        return spec.make_connector(
            instrument,
            account_credentials(venue, account_ref.custody_address),
        )

    # -- create / stop -------------------------------------------------------

    def create_executor(
        self,
        config: ExecutorConfig,
        executor_id: Optional[str] = None,
        *,
        connector=None,
    ) -> str:
        """Create, persist, and start an executor. Returns its id.

        ``executor_id``: the client-generated create identity (§6.2) — when
        given, it IS the id (ops.create has already checked replay/rebind);
        otherwise one is generated.
        """
        validate_risk_declaration(config.risk_declaration())
        type_ = config.type
        if type_ not in _EXECUTOR_TYPES:
            raise ValueError(f"unknown executor type: {config.type}")
        if config.account_ref is None:
            raise ValueError("executor config has no resolved account_ref")
        _, executor_cls = _EXECUTOR_TYPES[type_]
        executor_id = executor_id or new_executor_id(type_)
        connector = connector or self.connector_for(config)
        executor = executor_cls(executor_id, config, connector, self.store)
        executor.persist()
        self._start_task(executor)
        logger.info("created executor %s: %s", executor_id, config.model_dump_json())
        return executor_id

    def stop_executor(self, executor_id: str, keep_position: bool = True) -> None:
        executor = self._executors.get(executor_id)
        if executor is None:
            record = self.store.load(executor_id)
            if record is None:
                raise KeyError(f"executor {executor_id} not found")
            config_cls, executor_cls = _EXECUTOR_TYPES[record.type]
            config = config_cls(**record.config)
            connector = self.connector_for(config)
            executor = executor_cls(record.id, config, connector, self.store)
            executor.restore_state(record.state)
            try:
                executor.status = ExecutorStatus(record.status)
            except ValueError:
                executor.status = ExecutorStatus.ACTIVE

            if executor.status.is_terminal and not (
                isinstance(executor, PositionExecutor)
                and executor.state.close_type == "detached"
                and not keep_position
            ):
                raise KeyError(
                    f"executor {executor_id} is terminal and has no resumable lifecycle"
                )
            self._executors[executor_id] = executor
        # A detached position is terminal as a polling lifecycle but its
        # inventory remains financially live. Explicit close reactivates that
        # durable scope and drives the normal close state machine.
        if (
            not keep_position
            and isinstance(executor, PositionExecutor)
            and executor.state.state == PositionStates.COMPLETE
            and executor.state.close_type == "detached"
        ):
            executor.state.state = PositionStates.ACTIVE
            executor.status = ExecutorStatus.ACTIVE
        if executor.status.is_terminal:
            raise KeyError(
                f"executor {executor_id} is terminal and has no resumable lifecycle"
            )
        executor.early_stop(keep_position=keep_position)
        executor.persist()
        if executor.status.is_terminal:
            self._maybe_release_lease(executor_id)
        elif executor_id not in self._tasks or self._tasks[executor_id].done():
            self._start_task(executor)

    def stop_agent_executors(
        self, agent_id: str, keep_position: bool = True
    ) -> list[str]:
        """Stop every running executor owned by ``agent_id``. Returns their ids.

        Called when an agent session is stopped so it does not leave executors
        polling Gateway. ``keep_position=True`` detaches (positions remain in the
        wallet, unmanaged); ``False`` swaps them back to the quote token.
        """
        stopped: list[str] = []
        for record in self.store.load_by_agent(agent_id, limit=10_000):
            try:
                self.stop_executor(record.id, keep_position=keep_position)
            except KeyError:
                continue
            stopped.append(record.id)
        return stopped

    def stop_slug_executors(
        self, agent_slug: str, keep_position: bool = True
    ) -> list[str]:
        """AGENT-scoped stop (§6.2 hierarchy): every running executor
        attributed to the slug, across ALL of its runs. ``keep_position=False``
        closes each executor's remaining signed inventory (owned_net_base fold
        — §6.2 close sizing)."""
        stopped: list[str] = []
        for record in self.store.load_by_slug(agent_slug):
            try:
                self.stop_executor(record.id, keep_position=keep_position)
            except KeyError:
                continue
            stopped.append(record.id)
        return stopped

    def get(self, executor_id: str) -> Optional[ExecutorRecord]:
        return self.store.load(executor_id)

    def list_running(self) -> list[str]:
        return [eid for eid, t in self._tasks.items() if not t.done()]

    def _start_task(self, executor: ExecutorBase) -> None:
        self._executors[executor.id] = executor
        self._owned_connectors[executor.id] = executor.connector
        task = asyncio.create_task(executor.run(), name=f"executor-{executor.id}")
        task.add_done_callback(lambda _t, eid=executor.id: self._on_task_done(eid))
        self._tasks[executor.id] = task

    def _on_task_done(self, executor_id: str) -> None:
        self._maybe_release_lease(executor_id)
        connector = self._owned_connectors.pop(executor_id, None)
        if (
            connector is not None
            and connector not in self._connector_overrides.values()
        ):
            try:
                asyncio.create_task(self._close_connector(connector))
            except RuntimeError:
                # Event loop is already closing; shutdown() performs the final
                # best-effort close for any connector still registered.
                self._owned_connectors[executor_id] = connector

    @staticmethod
    async def _close_connector(connector) -> None:
        close = getattr(connector, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result):
            await result

    def _maybe_release_lease(self, executor_id: str) -> None:
        """Release the instrument lease once the executor is terminal AND no
        owned order is still live (§6.2: the lease is held until every
        cancellation reaches a confirmed terminal state)."""
        try:
            from condor.executors.orders import live_orders

            record = self.store.load(executor_id)
            if record is None:
                return
            if record.status not in ("CLOSED", "FAILED"):
                return  # cancelled task but nonterminal (process shutdown) — keep
            raw_orders = (record.state or {}).get("orders") or []
            from condor.executors.orders import LandedOrder

            landed = [LandedOrder(**o) for o in raw_orders]
            if live_orders(landed):
                logger.warning(
                    "executor %s terminal but %d order(s) still live — lease held "
                    "(reconcile will retry cancels)",
                    executor_id,
                    len(live_orders(landed)),
                )
                return
            self._release_lease_for_record(record)
        except Exception:
            logger.exception("lease release check failed for %s", executor_id)

    def _release_lease_for_record(self, record) -> None:
        cfg = record.config or {}
        try:
            account_ref = self._account_ref_from_config(cfg)
            instrument = self._instrument_id_from_config(cfg)
            self.leases.release(account_ref, instrument, executor_id=record.id)
        except Exception:
            logger.exception("lease release failed for %s", record.id)

    @staticmethod
    def _account_ref_from_config(cfg: dict):
        from condor.accounts.model import AccountRef

        raw = cfg.get("account_ref")
        if not isinstance(raw, dict):
            raise ValueError(
                "executor record has no resolved account_ref; pre-binding "
                "records require explicit operator reconciliation"
            )
        ref = AccountRef.from_dict(raw)
        venue = cfg.get("venue") or "solana"
        if ref.venue_id != venue:
            raise ValueError(
                f"executor account_ref venue {ref.venue_id!r} != {venue!r}"
            )
        return ref

    @staticmethod
    def _instrument_id_from_config(cfg: dict) -> str:
        """Canonical instrument id from a RAW config dict (lease release /
        rebuild) — same venue-package normalization as
        ``ExecutorConfig.instrument_id`` so lease keys never diverge across a
        restart; generic identity-field fallback when the venue has no spec."""
        from condor.accounts.registry import UnknownVenueError
        from condor.venues.registry import venue_spec

        try:
            spec = venue_spec(cfg.get("venue") or "solana")
        except UnknownVenueError:
            spec = None
        if spec is not None:
            normalized = spec.normalize_instrument(cfg)
            if normalized:
                return str(normalized)
        base, quote = cfg.get("base_token"), cfg.get("quote_token")
        if base and quote:
            return f"{base}-{quote}"
        if cfg.get("coin"):
            return str(cfg["coin"])
        if cfg.get("market"):
            return str(cfg["market"])
        return cfg.get("type", "")

    def rebuild_leases(self) -> int:
        """Rebuild the lease table from nonterminal executor records at
        startup (§12 ordering: before readiness opens). Returns count."""
        count = 0
        self.leases.clear()
        for record in self.store.load_non_terminal():
            cfg = record.config or {}
            owner = record.agent_id or (
                "condor"
                if cfg.get("origin") == "condor"
                else record.agent_slug or "condor"
            )
            try:
                self.leases.acquire(
                    self._account_ref_from_config(cfg),
                    self._instrument_id_from_config(cfg),
                    owner=owner,
                    executor_id=record.id,
                )
                count += 1
            except Exception:
                logger.exception("lease rebuild failed for %s", record.id)
        return count

    # -- reconciliation ---------------------------------------------------------

    async def reconcile(self) -> list[str]:
        """Re-adopt or settle every non-terminal executor from the store.

        Called on startup, before accepting new work. Returns the ids of
        executors resumed live.
        """
        resumed: list[str] = []
        for record in self.store.load_non_terminal():
            try:
                # Bounded per record: one hung venue call must not stall the
                # whole startup sequence (this once held readiness for ~20 min).
                action = await asyncio.wait_for(self._reconcile_one(record), timeout=60)
            except asyncio.TimeoutError:
                logger.critical(
                    "reconcile timed out for %s (60s) — leaving row untouched, "
                    "manual check required",
                    record.id,
                )
                continue
            except Exception as e:
                logger.critical(
                    "reconcile failed for %s: %s — leaving row untouched, manual check required",
                    record.id,
                    e,
                )
                continue
            if action == "resumed":
                resumed.append(record.id)
        return resumed

    async def _reconcile_one(self, record: ExecutorRecord) -> str:
        config_cls, executor_cls = _EXECUTOR_TYPES[record.type]
        config = config_cls(**record.config)
        connector = self.connector_for(config)

        if config.kind == "order":
            return await self._reconcile_order(record, config, executor_cls, connector)
        return await self._reconcile_position(record, config, executor_cls, connector)

    async def _reconcile_position(self, record, config, executor_cls, connector) -> str:
        """Re-adopt or settle a position from the venue's held size (the truth).

        The venue — not the persisted phase — decides what happened across the
        crash window, so a financial action is never repeated (#1):
          * mid-CLOSING but already flat  -> the close landed; do NOT re-sell.
          * held inventory but phase still OPENING/NOT_ACTIVE -> the entry
            landed; adopt it as ACTIVE and do NOT re-open (no duplicate entry).
        """
        executor = executor_cls(record.id, config, connector, self.store)
        executor.restore_state(record.state)
        s = executor.state
        state = record.state.get("state")

        venue_held = await executor.adapter.held_size()
        attributed = executor.adapter.attributable_held_size(s, venue_held)
        held = venue_held if attributed is None else attributed

        if state == PositionStates.CLOSING.value:
            if held > 0:
                executor.status = ExecutorStatus.CLOSING
                self._start_task(executor)
                logger.warning(
                    "reconcile %s: re-adopted mid-CLOSING position (%s held) — close will retry",
                    record.id,
                    held,
                )
                return "resumed"
            # Already flat: the close completed before the crash. Re-closing here
            # would sell inventory we no longer hold.
            await self._settle_reconciled_position(
                executor,
                "reconciled: close already completed on venue",
                s.close_type or "reconciled_close",
            )
            logger.warning(
                "reconcile %s: mid-CLOSING but venue is flat — close already done",
                record.id,
            )
            return "settled"

        if held > 0:
            if (
                s.state in (PositionStates.OPENING, PositionStates.NOT_ACTIVE)
                and not s.open_ref
                and attributed is None
            ):
                # Wallet/account balances are not executor-scoped. Without a
                # transaction/order reference, existing inventory cannot prove
                # this executor's entry landed, so automatic adoption is unsafe.
                s.state = PositionStates.FAILED
                executor.status = ExecutorStatus.FAILED
                executor.close_reason = (
                    "reconciled: inventory exists but opening has no venue reference; "
                    "manual attribution required"
                )
                executor.persist()
                logger.critical(
                    "reconcile %s: unattributed inventory during OPENING", record.id
                )
                return "settled"
            # Adopt the live position. If the internal machine is still OPENING/
            # NOT_ACTIVE (the fill landed but ACTIVE was never persisted),
            # advance it to ACTIVE HERE so control_task does not call _open()
            # again and duplicate the entry.
            if s.state in (PositionStates.OPENING, PositionStates.NOT_ACTIVE):
                s.size = held
                if s.entry_price is None:
                    s.entry_price = (
                        await executor.adapter.venue_entry_price()
                        or await executor.adapter.mark_price()
                    )
                if s.amount_spent == 0 and s.entry_price is not None:
                    s.amount_spent = held * s.entry_price
                if s.opened_at is None:
                    s.opened_at = time.time()
                s.state = PositionStates.ACTIVE
                logger.warning(
                    "reconcile %s: adopted mid-OPENING fill as ACTIVE (%s @ %s) — not re-opening",
                    record.id,
                    held,
                    s.entry_price,
                )
            await executor.adapter.reconcile_live(s)
            executor.status = ExecutorStatus.ACTIVE
            self._start_task(executor)
            logger.info(
                "reconcile %s: re-adopted open position (%s units on %s)",
                record.id,
                held,
                config.venue,
            )
            return "resumed"

        if state == PositionStates.OPENING.value:
            s.state = PositionStates.FAILED
            executor.status = ExecutorStatus.FAILED
            executor.close_reason = (
                "reconciled: died mid-OPENING with no venue position — verify manually"
            )
            executor.persist()
            logger.critical(
                "reconcile %s: position died mid-OPENING, manual check required",
                record.id,
            )
            return "settled"
        await self._settle_reconciled_position(
            executor,
            "reconciled: position closed/resolved on venue",
            s.close_type or "closed_on_venue",
        )
        return "settled"

    async def _settle_reconciled_position(
        self, executor: PositionExecutor, reason: str, close_type: str
    ) -> None:
        """Persist terminal venue accounting instead of only flipping status."""
        await executor.adapter.settle(executor.state)
        executor.state.close_type = close_type
        executor.state.state = PositionStates.COMPLETE
        executor.status = ExecutorStatus.CLOSED
        executor.close_reason = reason
        executor.persist()

    async def _reconcile_order(self, record, config, executor_cls, connector) -> str:
        """Settle a single-leg order from its in-flight state.

        DONE/RESTING with a ref -> re-adopt and let poll() drive it terminal;
        SUBMITTING with no ref -> orphan (died before/inside submission)."""
        state = record.state.get("state")
        open_ref = record.state.get("open_ref")

        if (
            state in (OrderStates.RESTING.value, OrderStates.SUBMITTING.value)
            and open_ref
        ):
            executor = executor_cls(record.id, config, connector, self.store)
            executor.restore_state(record.state)
            executor.status = ExecutorStatus.ACTIVE
            self._start_task(executor)
            logger.warning(
                "reconcile %s: re-adopted in-flight order — poll will settle it",
                record.id,
            )
            return "resumed"
        if state == OrderStates.SUBMITTING.value:
            self.store.mark(
                record.id,
                ExecutorStatus.FAILED.value,
                "reconciled: died during submission with no signature — "
                "check wallet activity manually",
            )
            logger.critical(
                "reconcile %s: order died mid-submission, manual check required",
                record.id,
            )
            return "settled"
        if state == OrderStates.DONE.value:
            self.store.mark(
                record.id, ExecutorStatus.CLOSED.value, "reconciled: order filled"
            )
            return "settled"
        self.store.mark(
            record.id, ExecutorStatus.CLOSED.value, "reconciled: never submitted"
        )
        return "settled"

    # -- watchdog ------------------------------------------------------------------

    def start_watchdog(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(
                self._watchdog_loop(), name="executor-watchdog"
            )

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            try:
                await self._watchdog_pass()
            except Exception as e:
                logger.error("watchdog pass failed: %s", e)

    async def _watchdog_pass(self) -> None:
        now = time.time()
        for eid, task in list(self._tasks.items()):
            executor = self._executors.get(eid)
            if executor is None or executor.status.is_terminal:
                continue
            stale_after = executor.config.update_interval * STALE_INTERVALS
            died = task.done()
            # In-memory liveness (advanced each completed loop iteration), not the
            # store heartbeat — the dedup'd log doesn't write on volatile ticks.
            stalled = (now - executor._last_tick_at) > stale_after
            if not died and not stalled:
                continue
            reason = "task died" if died else f"heartbeat stale >{int(stale_after)}s"
            if died and not task.cancelled() and task.exception() is not None:
                reason = f"task died: {task.exception()}"
            logger.critical("watchdog: executor %s %s — flattening", eid, reason)
            await self._flatten(executor, reason)

    async def _flatten(self, executor: ExecutorBase, reason: str) -> None:
        """Last-resort close of whatever the executor left open.

        Uses the same protection-cancel + signed ``orders[]`` close path as a
        normal explicit stop; never submits against the legacy gross scalar."""
        if isinstance(executor, PositionExecutor):
            try:
                executor.state.close_type = "watchdog"
                executor.close_reason = f"watchdog: {reason}"
                executor.state.state = PositionStates.CLOSING
                executor.status = ExecutorStatus.CLOSING
                executor.persist()
                await executor._close()
                executor.status = ExecutorStatus.FAILED
                executor.persist()
                logger.warning(
                    "watchdog: cancelled protections and closed signed scope of %s",
                    executor.id,
                )
            except Exception as e:
                logger.critical(
                    "watchdog: FLATTEN FAILED for %s: %s — manual intervention required",
                    executor.id,
                    e,
                )
                executor.status = ExecutorStatus.FAILED
                executor.close_reason = f"watchdog flatten FAILED ({reason}): {e}"
                executor.persist()
                return
        else:
            executor.status = ExecutorStatus.FAILED
            executor.close_reason = f"watchdog: {reason}"
            executor.persist()
        task = self._tasks.get(executor.id)
        if task and not task.done():
            task.cancel()

    # -- shutdown ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Stop the process WITHOUT closing positions: cancel loops, persist,
        leave re-adoption to the next start's reconcile()."""
        if self._watchdog_task:
            self._watchdog_task.cancel()
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        connectors = {
            id(c): c
            for c in (
                list(self._owned_connectors.values())
                + list(self._connector_overrides.values())
            )
        }
        for connector in connectors.values():
            await self._close_connector(connector)
        self._owned_connectors.clear()
        self.store.close()

    async def wait_all(self) -> None:
        """Wait until every running executor reaches a terminal state."""
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
