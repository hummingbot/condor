"""P2: the executor runtime driven by the append-only ExecutorLog.

Validates that the runtime opens/closes and reconciles from the transaction log
(not SQLite), and that persist() dedups on the recovery key so a live executor
does not append a line every poll. Gateway is faked (live-path validation is the
`python -m condor.executors` harness against a real Gateway).
"""

import asyncio
from decimal import Decimal

from condor.executors.base import ExecutorStatus
from condor.executors.log import ExecutorLog
from condor.executors.order import OrderExecutor, OrderStates
from condor.executors.position import PositionExecutor, PositionStates
from condor.executors.runtime import ExecutorRuntime
from tests.test_executors import FakeGateway, position_config, swap_config


def _log(tmp_path):
    return ExecutorLog(tmp_path / "agents")


def _lines(log, slug):
    path = log._path_for_slug(slug)
    return path.read_text().strip().splitlines() if path.exists() else []


# -- runtime open -> close through the log -------------------------------------


def test_swap_runs_to_close_via_log(tmp_path):
    log = _log(tmp_path)
    runtime = ExecutorRuntime(store=log)
    runtime._connector_overrides[("solana", "spot")] = FakeGateway()

    async def run():
        eid = runtime.create_executor(swap_config())
        await runtime.wait_all()
        return eid

    eid = asyncio.run(run())
    rec = log.load(eid)
    assert rec.status == "CLOSED"
    assert rec.state["state"] == "DONE"
    # dedup: a few lifecycle lines (created/submitting/done), never one per poll
    assert 0 < len(_lines(log, "")) <= 4  # "" -> the _manual slug


# -- reconcile from the log ----------------------------------------------------


def test_swap_reconcile_settles_from_log(tmp_path):
    log = _log(tmp_path)
    gateway = FakeGateway()

    # An order that died mid-submission with no signature -> reconcile FAILs it.
    ex = OrderExecutor(
        "swap_orphan", swap_config(agent_slug="mm", agent_id="mm_1"), gateway, log
    )
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = OrderStates.SUBMITTING
    log.save(ex)

    runtime = ExecutorRuntime(store=log)
    runtime._connector_overrides[("solana", "spot")] = gateway
    resumed = asyncio.run(runtime.reconcile())
    assert resumed == []
    rec = log.load("swap_orphan")
    assert rec.status == "FAILED"
    assert "no signature" in rec.close_reason


class _FlatGateway(FakeGateway):
    """Venue that reports zero balance — used to simulate a position already
    closed on-chain across a crash window."""

    async def get_balances(self, chain, network, address, tokens=None):
        self.calls.append(("get_balances", tokens))
        return {t: 0.0 for t in (tokens or [])}


class _InventoryGateway(FakeGateway):
    """Wallet inventory that predates the executor under reconciliation."""

    async def get_balances(self, chain, network, address, tokens=None):
        self.calls.append(("get_balances", tokens))
        return {t: 7.0 for t in (tokens or [])}


def test_position_reconcile_adopts_opening_fill_not_reopen(tmp_path):
    """Crash after the entry filled but before ACTIVE was persisted: reconcile
    must ADOPT the live position, never call _open() again (no duplicate entry, #1)."""
    log = _log(tmp_path)
    gw = FakeGateway(prices=[100.0])  # get_balances -> 999 held
    ex = PositionExecutor(
        "pos_crash", position_config(agent_slug="mm", agent_id="mm_1"), gw, log
    )
    ex.status = ExecutorStatus.ACTIVE  # coarse status persisted...
    ex.state.state = PositionStates.OPENING  # ...but internal machine never advanced
    ex.state.extra["held_before"] = "0"  # persisted before the entry submission
    log.save(ex)

    runtime = ExecutorRuntime(store=log)
    runtime._connector_overrides[("solana", "spot")] = gw

    async def go():
        resumed = await runtime.reconcile()
        await asyncio.sleep(0.05)  # let a couple of barrier ticks run
        await runtime.shutdown()
        return resumed

    resumed = asyncio.run(go())
    assert "pos_crash" in resumed
    # A re-entry would be a SELL execute_swap. Adoption must not place one.
    assert not any(c[0] == "execute_swap" for c in gw.calls)


def test_position_reconcile_closing_but_flat_settles_no_resell(tmp_path):
    """Crash after the close landed but before COMPLETE was persisted: reconcile
    sees a flat venue and settles CLOSED — it must NOT sell again (#1)."""
    log = _log(tmp_path)
    gw = _FlatGateway()
    ex = PositionExecutor(
        "pos_closing", position_config(agent_slug="mm", agent_id="mm_1"), gw, log
    )
    ex.status = ExecutorStatus.CLOSING
    ex.state.state = PositionStates.CLOSING
    ex.state.size = Decimal("5")
    log.save(ex)

    runtime = ExecutorRuntime(store=log)
    runtime._connector_overrides[("solana", "spot")] = gw
    resumed = asyncio.run(runtime.reconcile())
    assert resumed == []  # settled, not resumed
    rec = log.load("pos_closing")
    assert rec.status == "CLOSED"
    assert "already completed" in rec.close_reason
    assert not any(c[0] == "execute_swap" for c in gw.calls)  # did NOT re-sell


def test_position_reconcile_does_not_adopt_unattributed_wallet_inventory(tmp_path):
    """An OPENING record with no transaction reference cannot claim the wallet's
    pre-existing token balance as its own fill. The ambiguity must fail closed."""
    log = _log(tmp_path)
    gw = _InventoryGateway()
    ex = PositionExecutor(
        "pos_ambiguous",
        position_config(agent_slug="mm", agent_id="mm_1"),
        gw,
        log,
    )
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = PositionStates.OPENING
    ex.state.open_ref = None
    ex.state.extra["held_before"] = "7"
    log.save(ex)

    runtime = ExecutorRuntime(store=log)
    runtime._connector_overrides[("solana", "spot")] = gw

    async def go():
        resumed = await runtime.reconcile()
        await runtime.shutdown()
        return resumed

    resumed = asyncio.run(go())
    rec = log.load("pos_ambiguous")
    assert resumed == []
    assert rec.status == ExecutorStatus.FAILED.value
    assert not any(c[0] == "execute_swap" for c in gw.calls)


def test_perp_reconcile_restores_missing_native_triggers(tmp_path):
    """A recovered live perp must restore absent TP/SL orders before relying on
    local polling again, preserving the documented daemon-down backstop."""
    from tests.test_perp_executor import FakeHL, _cfg

    log = _log(tmp_path)
    fake = FakeHL(entry=100, marks=[100], liq=50)
    fake.opened = True
    fake.size = Decimal("0.2")
    ex = PositionExecutor(
        "perp_missing_triggers",
        _cfg(
            agent_slug="mm",
            agent_id="mm_1",
            native_triggers=True,
            take_profit_pct=Decimal("0.05"),
            stop_loss_pct=Decimal("0.03"),
        ),
        fake,
        log,
    )
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = PositionStates.ACTIVE
    ex.state.size = Decimal("0.2")
    ex.state.entry_price = Decimal("100")
    ex.state.amount_spent = Decimal("20")
    ex.state.open_ref = "1"
    log.save(ex)

    runtime = ExecutorRuntime(store=log)
    runtime._connector_overrides[("hyperliquid", "perp")] = fake

    async def go():
        resumed = await runtime.reconcile()
        await asyncio.sleep(0.03)
        await runtime.shutdown()
        return resumed

    assert "perp_missing_triggers" in asyncio.run(go())
    assert {kind for kind, _ in fake.triggers} == {"tp", "sl"}


def test_perp_reconcile_flat_position_settles_fill_history(tmp_path):
    """If a native trigger closed the perp while Condor was down, reconciliation
    must persist realized PnL from venue fills instead of only flipping status."""
    from tests.test_perp_executor import FakeHL, _cfg

    log = _log(tmp_path)
    fake = FakeHL(entry=100, marks=[100], liq=50)
    fake.opened = True
    fake.closed = True
    fake.size = Decimal("0.2")
    ex = PositionExecutor(
        "perp_closed_offline",
        _cfg(agent_slug="mm", agent_id="mm_1"),
        fake,
        log,
    )
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = PositionStates.ACTIVE
    ex.state.size = Decimal("0.2")
    ex.state.entry_price = Decimal("100")
    ex.state.amount_spent = Decimal("20")
    ex.state.open_ref = "1"
    log.save(ex)

    runtime = ExecutorRuntime(store=log)
    runtime._connector_overrides[("hyperliquid", "perp")] = fake
    assert asyncio.run(runtime.reconcile()) == []

    rec = log.load("perp_closed_offline")
    assert rec.status == ExecutorStatus.CLOSED.value
    assert Decimal(rec.state["extra"]["realized_pnl"]) == Decimal("1.48")


# -- dedup on the recovery key -------------------------------------------------


def test_persist_dedups_on_recovery_key(tmp_path):
    log = _log(tmp_path)
    ex = OrderExecutor(
        "swap_d", swap_config(agent_slug="mm", agent_id="mm_1"), FakeGateway(), log
    )
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = OrderStates.RESTING
    ex.state.open_ref = "sig-1"

    ex.persist()
    ex.persist()
    ex.persist()
    assert len(_lines(log, "mm")) == 1  # identical recovery key -> one line

    ex.state.entry_price = Decimal("999")  # volatile, NOT in the recovery key
    ex.persist()
    assert len(_lines(log, "mm")) == 1  # still deduped

    ex.status = ExecutorStatus.CLOSED  # status change -> new key
    ex.close_reason = "done"
    ex.persist()
    assert len(_lines(log, "mm")) == 2
    assert log.load("swap_d").status == "CLOSED"
