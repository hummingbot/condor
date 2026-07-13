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

from condor.executors.base import ExecutorBase, ExecutorConfig, ExecutorStatus, new_executor_id
from condor.executors.gateway import GatewayClient, GatewayError
from condor.executors.lp import LpConfig, LpExecutor, LpStates
from condor.executors.store import ExecutorRecord, ExecutorStore
from condor.executors.swap import SwapConfig, SwapExecutor

logger = logging.getLogger(__name__)

_EXECUTOR_TYPES: dict[str, tuple[type[ExecutorConfig], type[ExecutorBase]]] = {
    "swap": (SwapConfig, SwapExecutor),
    "lp": (LpConfig, LpExecutor),
}

# Heartbeat staler than this many update_intervals trips the watchdog
STALE_INTERVALS = 10
WATCHDOG_INTERVAL = 30.0


class ExecutorRuntime:
    def __init__(self, gateway: Optional[GatewayClient] = None, store: Optional[ExecutorStore] = None):
        self.gateway = gateway or GatewayClient()
        self.store = store or ExecutorStore()
        self._executors: dict[str, ExecutorBase] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._watchdog_task: Optional[asyncio.Task] = None

    # -- create / stop -------------------------------------------------------

    def create_executor(self, config: ExecutorConfig) -> str:
        """Create, persist, and start an executor. Returns its id."""
        if config.type not in _EXECUTOR_TYPES:
            raise ValueError(f"unknown executor type: {config.type}")
        _, executor_cls = _EXECUTOR_TYPES[config.type]
        executor_id = new_executor_id(config.type)
        executor = executor_cls(executor_id, config, self.gateway, self.store)
        executor.persist()
        self._start_task(executor)
        logger.info("created executor %s: %s", executor_id, config.model_dump_json())
        return executor_id

    def stop_executor(self, executor_id: str, keep_position: bool = True) -> None:
        executor = self._executors.get(executor_id)
        if executor is None:
            raise KeyError(f"executor {executor_id} not running (see store for history)")
        executor.early_stop(keep_position=keep_position)
        executor.persist()

    def get(self, executor_id: str) -> Optional[ExecutorRecord]:
        return self.store.load(executor_id)

    def list_running(self) -> list[str]:
        return [eid for eid, t in self._tasks.items() if not t.done()]

    def _start_task(self, executor: ExecutorBase) -> None:
        self._executors[executor.id] = executor
        self._tasks[executor.id] = asyncio.create_task(executor.run(), name=f"executor-{executor.id}")

    # -- reconciliation ---------------------------------------------------------

    async def reconcile(self) -> list[str]:
        """Re-adopt or settle every non-terminal executor from the store.

        Called on startup, before accepting new work. Returns the ids of
        executors resumed live.
        """
        resumed: list[str] = []
        for record in self.store.load_non_terminal():
            try:
                action = await self._reconcile_one(record)
            except Exception as e:
                logger.critical(
                    "reconcile failed for %s: %s — leaving row untouched, manual check required",
                    record.id, e,
                )
                continue
            if action == "resumed":
                resumed.append(record.id)
        return resumed

    async def _reconcile_one(self, record: ExecutorRecord) -> str:
        config_cls, executor_cls = _EXECUTOR_TYPES[record.type]
        config = config_cls(**record.config)

        if record.type == "lp":
            position_address = record.state.get("position_address")
            if position_address:
                try:
                    await self.gateway.clmm_position_info(
                        config.connector, config.chain_network, position_address
                    )
                except GatewayError as e:
                    if "position closed" in (e.body or "").lower() or "not found" in (e.body or "").lower():
                        self.store.mark(record.id, ExecutorStatus.CLOSED.value,
                                        "reconciled: position closed on-chain")
                        logger.info("reconcile %s: position %s closed on-chain -> CLOSED",
                                    record.id, position_address)
                        return "settled"
                    raise
                # Position live: re-adopt it
                executor = executor_cls(record.id, config, self.gateway, self.store)
                executor.restore_state(record.state)
                executor.status = ExecutorStatus.ACTIVE
                self._start_task(executor)
                logger.info("reconcile %s: re-adopted live position %s", record.id, position_address)
                return "resumed"
            # No position recorded. If it died mid-OPENING the position may
            # still exist on-chain without us knowing its address.
            if record.state.get("state") == LpStates.OPENING.value:
                owned = await self.gateway.clmm_positions_owned(
                    config.connector, config.chain_network, config.wallet_address
                )
                pool_matches = [p for p in owned if p.get("poolAddress") == config.pool_address]
                if pool_matches:
                    logger.critical(
                        "reconcile %s: died mid-OPENING and wallet owns %d position(s) in pool %s "
                        "— NOT auto-adopting (cannot prove ownership of the intent). "
                        "Inspect and adopt/close manually.",
                        record.id, len(pool_matches), config.pool_address,
                    )
                self.store.mark(record.id, ExecutorStatus.FAILED.value,
                                "reconciled: died mid-OPENING; verify on-chain state")
                return "settled"
            self.store.mark(record.id, ExecutorStatus.CLOSED.value,
                            "reconciled: no position was opened")
            return "settled"

        if record.type == "swap":
            signature = record.state.get("signature")
            if signature:
                chain = config.chain_network.split("-", 1)[0]
                poll = await self.gateway.poll_tx(chain, config.network, signature)
                if poll.get("txStatus") == 1:
                    self.store.mark(record.id, ExecutorStatus.CLOSED.value,
                                    "reconciled: swap confirmed on-chain")
                else:
                    self.store.mark(record.id, ExecutorStatus.FAILED.value,
                                    f"reconciled: swap tx status={poll.get('txStatus')}")
                return "settled"
            if record.state.get("phase") == "submitting":
                self.store.mark(record.id, ExecutorStatus.FAILED.value,
                                "reconciled: died during submission with no signature — "
                                "check wallet activity manually")
                logger.critical("reconcile %s: swap died mid-submission, manual check required",
                                record.id)
                return "settled"
            self.store.mark(record.id, ExecutorStatus.CLOSED.value,
                            "reconciled: never submitted")
            return "settled"

        raise ValueError(f"unknown executor type in store: {record.type}")

    # -- watchdog ------------------------------------------------------------------

    def start_watchdog(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop(), name="executor-watchdog")

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
            record = self.store.load(eid)
            stale_after = executor.config.update_interval * STALE_INTERVALS
            died = task.done()
            stalled = record is not None and (now - record.heartbeat_at) > stale_after
            if not died and not stalled:
                continue
            reason = "task died" if died else f"heartbeat stale >{int(stale_after)}s"
            if died and not task.cancelled() and task.exception() is not None:
                reason = f"task died: {task.exception()}"
            logger.critical("watchdog: executor %s %s — flattening", eid, reason)
            await self._flatten(executor, reason)

    async def _flatten(self, executor: ExecutorBase, reason: str) -> None:
        """Last-resort close of whatever the executor left open."""
        if isinstance(executor, LpExecutor) and executor.state.position_address:
            try:
                await self.gateway.clmm_close(
                    connector=executor.config.connector,
                    chain_network=executor.config.chain_network,
                    wallet_address=executor.config.wallet_address,
                    position_address=executor.state.position_address,
                )
                logger.warning("watchdog: flattened position %s of %s",
                               executor.state.position_address, executor.id)
            except GatewayError as e:
                logger.critical("watchdog: FLATTEN FAILED for %s: %s — manual intervention required",
                                executor.id, e)
                self.store.mark(executor.id, ExecutorStatus.FAILED.value,
                                f"watchdog flatten FAILED ({reason}): {e}")
                return
        self.store.mark(executor.id, ExecutorStatus.FAILED.value, f"watchdog: {reason}")
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
        await self.gateway.close()
        self.store.close()

    async def wait_all(self) -> None:
        """Wait until every running executor reaches a terminal state."""
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
