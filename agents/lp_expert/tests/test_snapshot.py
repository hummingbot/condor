from __future__ import annotations

import asyncio
import copy
import json
import os
from types import SimpleNamespace

import pytest
from conftest import (
    POOL,
    SOL_MINT,
    executor_row,
    prior_close,
    runtime_scope,
    strategy_config,
    terminal_row,
)
from pydantic import ValidationError

from agents.lp_expert.core import portfolio
from agents.lp_expert.routines import lp_snapshot


class Executors:
    def __init__(self, pages):
        self.pages = copy.deepcopy(pages)
        self.calls = []

    async def search_executors(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        cursor = kwargs["cursor"]
        return copy.deepcopy(self.pages[cursor])


class Gateway:
    async def get_network_config(self, network):
        assert network == "solana-mainnet-beta"
        return {"default_wallet": "wallet-1"}


def _install(
    monkeypatch,
    tmp_path,
    *,
    rows=None,
    pages=None,
    tick=2,
    stop_error=None,
    unresolved=None,
    unconsumed=None,
    creates=None,
    config=None,
):
    scope = runtime_scope(tmp_path, tick=tick, config=config)
    session_config = tmp_path / "config.yml"
    session_config.write_text("execution_mode: loop\n")
    os.utime(session_config, (1_900, 1_900))
    if pages is None:
        pages = {None: {"data": copy.deepcopy(rows or []), "next_cursor": None}}
    client = SimpleNamespace(executors=Executors(pages), gateway=Gateway())
    monkeypatch.setattr(lp_snapshot, "resolve_runtime", lambda _: scope)

    async def get_client(_):
        return client

    monkeypatch.setattr(lp_snapshot, "get_hummingbot_client", get_client)
    monkeypatch.setattr(portfolio.time, "time", lambda: 2_000)

    async def attach(payload, **_):
        client.report_payload = copy.deepcopy(payload)
        return {**payload, "report_id": "snapshot-report", "report_error": None}

    monkeypatch.setattr(lp_snapshot, "attach_report", attach)
    if unresolved is not None or unconsumed is not None:

        class ReadOnlyStore:
            def __init__(self, resolved_scope, *, read_only):
                self.scope = resolved_scope
                self.read_only = read_only

            def unresolved_operations(self):
                assert self.read_only is True
                return copy.deepcopy(unresolved or [])

            def unconsumed_preparations(self):
                assert self.read_only is True
                return copy.deepcopy(unconsumed or [])

            def list_records(self, operation_kind=None):
                assert self.read_only is True
                assert operation_kind == "create"
                return copy.deepcopy(creates or [])

        monkeypatch.setattr(lp_snapshot, "ReceiptStore", ReadOnlyStore)

    def read_stop(scope, executor_id, closed_tick):
        if stop_error:
            raise stop_error
        return {
            "tick": closed_tick,
            "controller_id": scope.controller_id,
            "executor_id": executor_id,
            "keep_position": False,
        }

    monkeypatch.setattr(lp_snapshot, "read_prior_tick_stop", read_stop)
    return client


def _config(*, tick=2, close=None, closes=None):
    return lp_snapshot.Config(
        controller_id="lp_expert.orca_1",
        tick=tick,
        prior_closes=(
            list(closes) if closes is not None else ([] if close is None else [close])
        ),
    )


def _snapshot_close(*args, **kwargs):
    return {
        key: value
        for key, value in prior_close(*args, **kwargs).items()
        if key != "stop_proof"
    }


def _run(config):
    raw = asyncio.run(lp_snapshot.run(config, None))
    assert len(raw) <= lp_snapshot._TRANSPORT_MAX_CHARS
    return json.loads(raw)


def _create_request(row):
    return {
        "action": "create",
        "executor_type": "lp_executor",
        "account_name": "master_account",
        "controller_id": "lp_expert.orca_1",
        "executor_config": copy.deepcopy(row["config"]),
    }


def test_snapshot_flat_portfolio_returns_scan_capacity_and_dynamic_constraints(
    monkeypatch, tmp_path
):
    _install(monkeypatch, tmp_path)

    result = _run(_config())

    assert result["status"] == "complete"
    assert result["mutation"] is False
    assert result["portfolio"]["active_count"] == 0
    assert result["portfolio"]["available_slots"] == 3
    assert result["scan_allowed"] is True
    assert result["selection_constraints"]["allocation_quote"] == ["3", "4", "12"]
    assert result["selection_constraints"]["deployments"] == [1, 1]
    assert result["report_id"] == "snapshot-report"


def test_snapshot_session_age_uses_stable_frozen_config_timestamp(
    monkeypatch, tmp_path
):
    _install(monkeypatch, tmp_path)
    (tmp_path / "lp_operations").mkdir()
    os.utime(tmp_path, (1_999, 1_999))

    result = _run(_config())

    assert float(result["portfolio"]["session"]["age_minutes"]) == pytest.approx(
        100 / 60
    )


def test_snapshot_healthy_executor_preserves_remaining_capacity(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, rows=[executor_row()])

    result = _run(_config())

    assert result["portfolio"]["active_count"] == 1
    assert result["portfolio"]["available_slots"] == 2
    assert result["portfolio"]["close_required_executor_ids"] == []
    assert result["scan_allowed"] is True


def test_snapshot_returns_multiple_deployments_from_frozen_config(
    monkeypatch, tmp_path
):
    configured = strategy_config(
        total_amount_quote=30,
        max_open_executors=5,
        max_slot_deployments_per_tick=2,
        candidate_scan_limit=6,
        risk_limits={
            "max_position_size_quote": 30,
            "max_open_executors": 5,
            "max_drawdown_pct": -1,
            "shutdown_drawdown_pct": -1,
        },
    )
    _install(monkeypatch, tmp_path, config=configured)

    result = _run(_config())

    assert result["portfolio"]["available_slots"] == 5
    assert result["selection_constraints"]["deployments"] == [2, 2]


def test_snapshot_triggered_executor_blocks_candidate_work(monkeypatch, tmp_path):
    _install(
        monkeypatch,
        tmp_path,
        rows=[executor_row(net_pnl_pct="0.06")],
    )

    result = _run(_config())

    assert result["portfolio"]["close_required_executor_ids"] == ["executor-1"]
    assert result["scan_allowed"] is False


def test_snapshot_reconciling_executor_blocks_deployment(monkeypatch, tmp_path):
    _install(
        monkeypatch,
        tmp_path,
        rows=[executor_row(state="CLOSING")],
    )

    result = _run(_config())

    assert result["portfolio"]["reconciling_executor_ids"] == ["executor-1"]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


def test_snapshot_foreign_active_executor_is_read_only_and_blocks_deploy(
    monkeypatch, tmp_path
):
    _install(
        monkeypatch,
        tmp_path,
        rows=[executor_row("foreign", controller_id="foreign.agent_1")],
    )

    result = _run(_config())

    assert result["portfolio"]["active_count"] == 0
    assert result["portfolio"]["foreign_active_executor_ids"] == ["foreign"]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


def test_snapshot_follows_pagination_and_supports_realistic_three_executors(
    monkeypatch, tmp_path
):
    executor_ids = [character * 44 for character in ("A", "B", "C")]
    pool_addresses = [character * 44 for character in ("D", "E", "F")]
    pages = {
        None: {
            "data": [
                executor_row(
                    executor_ids[0],
                    pool_address=pool_addresses[0],
                    net_pnl_pct="0.010123160378947211",
                    net_pnl_quote="0.03967283217344371",
                )
            ],
            "next_cursor": "second",
        },
        "second": {
            "data": [
                executor_row(
                    executor_ids[1],
                    pool_address=pool_addresses[1],
                    net_pnl_pct="0.0009146456020296545",
                    net_pnl_quote="0.0026971136627925874",
                ),
                executor_row(
                    executor_ids[2],
                    pool_address=pool_addresses[2],
                    net_pnl_pct="0.004190770639074312",
                    net_pnl_quote="0.011559348005873547",
                ),
            ],
            "next_cursor": None,
        },
    }
    client = _install(monkeypatch, tmp_path, pages=pages)

    result = _run(_config())

    assert result["status"] == "complete"
    assert result["portfolio"]["active_count"] == 3
    assert result["portfolio"]["available_slots"] == 0
    assert result["portfolio"]["occupied_pools"] == pool_addresses
    assert len(result["portfolio"]["executors"]) == 3
    assert all(
        set(executor)
        == {
            "executor_id",
            "status",
            "lifecycle_state",
            "trading_pair",
            "net_pnl_ratio",
        }
        for executor in result["portfolio"]["executors"]
    )
    assert all(
        "pool_address" in executor
        and "net_pnl_quote" in executor
        and "exposure_quote" in executor
        and "age_minutes" in executor
        for executor in client.report_payload["portfolio"]["executors"]
    )
    assert result["scan_allowed"] is False
    assert [call["cursor"] for call in client.executors.calls] == [None, "second"]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (terminal_row(residual="0", native_status="CONFIRMED"), "complete"),
        (terminal_row(residual="0.00001", native_status="FAILED"), "dust"),
        (terminal_row(residual="0.01", native_status="PENDING"), "pending"),
        (
            terminal_row(residual="0.01", native_status="CONFIRMED"),
            "cleanup_required",
        ),
        (terminal_row(residual="0.01", native_status="FAILED"), "cleanup_required"),
    ],
)
def test_snapshot_following_tick_cleanup_is_prioritized_and_fail_closed(
    monkeypatch, tmp_path, row, expected
):
    close = _snapshot_close()
    _install(
        monkeypatch,
        tmp_path,
        tick=2,
        rows=[row],
    )

    result = _run(_config(close=close))

    assert result["cleanup_tick"] is True
    assert result["portfolio"]["cleanups"][0]["status"] == expected
    assert result["scan_allowed"] is False


def test_snapshot_rejects_cleanup_on_the_close_tick(monkeypatch, tmp_path):
    close = _snapshot_close(closed_tick=2)
    _install(
        monkeypatch,
        tmp_path,
        tick=2,
        rows=[terminal_row()],
    )

    result = _run(_config(close=close))

    assert result["status"] == "rejected"
    assert "requires a later tick" in result["reason"]
    assert result["mutation"] is False
    assert result["scan_allowed"] is False


def test_snapshot_cleanup_ambiguity_is_manual_quarantine(monkeypatch, tmp_path):
    row = terminal_row()
    del row["custom_info"]["close_transaction_hash"]
    close = _snapshot_close()
    _install(
        monkeypatch,
        tmp_path,
        rows=[row],
    )

    result = _run(_config(close=close))

    assert result["portfolio"]["cleanups"][0]["status"] == "manual_review"
    assert result["portfolio"]["cleanups"][0]["capacity_quarantined"] is True
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


def test_snapshot_prior_closes_are_unique_and_capped_by_runtime(monkeypatch, tmp_path):
    close = _snapshot_close()
    with pytest.raises(ValidationError, match="must be unique"):
        _config(closes=[close, close])
    _install(monkeypatch, tmp_path)
    result = _run(
        _config(
            closes=[
                _snapshot_close(
                    f"executor-{index}",
                    pool_address=f"pool-{index}",
                )
                for index in range(4)
            ]
        )
    )
    assert result["status"] == "rejected"
    assert "configured executor capacity" in result["reason"]


def test_snapshot_classifies_multiple_prior_closes_independently(monkeypatch, tmp_path):
    rows = [
        terminal_row(
            "clean",
            pool_address="pool-clean",
            residual="0",
            native_status="CONFIRMED",
        ),
        terminal_row(
            "residual",
            pool_address="pool-residual",
            residual="0.01",
            native_status="FAILED",
        ),
    ]
    closes = [
        _snapshot_close("clean", pool_address="pool-clean"),
        _snapshot_close("residual", pool_address="pool-residual"),
    ]
    _install(monkeypatch, tmp_path, rows=rows)

    result = _run(_config(closes=closes))

    assert [item["executor_id"] for item in result["portfolio"]["cleanups"]] == [
        "clean",
        "residual",
    ]
    assert [item["status"] for item in result["portfolio"]["cleanups"]] == [
        "complete",
        "cleanup_required",
    ]
    assert result["portfolio"]["quarantined_cleanup_executor_ids"] == ["residual"]
    assert result["scan_allowed"] is False


def test_snapshot_missing_or_ambiguous_platform_stop_is_manual_quarantine(
    monkeypatch, tmp_path
):
    _install(
        monkeypatch,
        tmp_path,
        rows=[terminal_row()],
        stop_error=ValueError("one exact prior-tick native stop intent was not proven"),
    )

    result = _run(_config(close=_snapshot_close()))

    cleanup = result["portfolio"]["cleanups"][0]
    assert cleanup["status"] == "manual_review"
    assert cleanup["capacity_quarantined"] is True
    assert "not proven" in cleanup["reason"]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


def test_snapshot_persisted_unresolved_operation_blocks_scan_and_deploy(
    monkeypatch, tmp_path
):
    _install(
        monkeypatch,
        tmp_path,
        unresolved=[
            {
                "operation_id": "uncertain-swap-1",
                "operation_kind": "swap",
                "tick": 1,
                "phase": "uncertain",
                "mutation_possible": True,
                "reason": "submission outcome unavailable",
                "result": {"swap_executor_id": "native-order-1"},
            }
        ],
    )

    result = _run(_config())

    assert result["portfolio"]["unresolved_operations"] == [
        {
            "operation_id": "uncertain-swap-1",
            "operation_kind": "swap",
            "tick": 1,
            "phase": "uncertain",
            "reason": "submission outcome unavailable",
            "reconcile": {
                "routine": "lp_order_request",
                "config": {
                    "controller_id": "lp_expert.orca_1",
                    "operation_id": "uncertain-swap-1",
                    "swap_executor_id": "native-order-1",
                },
            },
        }
    ]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


def test_snapshot_continues_confirmed_preparation_before_restoring(
    monkeypatch, tmp_path
):
    preparation_id = f"lp_expert_orca_1-t1-{POOL}-prepare"
    client = _install(
        monkeypatch,
        tmp_path,
        rows=[
            executor_row("executor-one", pool_address="pool-one"),
            executor_row("executor-two", pool_address="pool-two"),
        ],
        unconsumed=[
            {
                "operation_id": preparation_id,
                "operation_kind": "swap",
                "tick": 1,
                "phase": "confirmed",
                "intent": {
                    "reason": "inventory_preparation",
                    "pool_address": POOL,
                    "base_symbol": "SOL",
                    "base_mint": SOL_MINT,
                    "base_decimals": 9,
                },
                "result": {
                    "confirmed_tick": 1,
                    "same_tick_lp_create_allowed": False,
                    "deployment_input": {
                        "candidate": {"pool_address": POOL},
                        "amount_quote": "3",
                        "range_half_width_pct": "10",
                        "preparation_operation_id": preparation_id,
                    },
                    "receipt": {
                        "transaction_hash": "tx-preparation",
                        "output_amount": "0.020228969",
                    },
                },
            }
        ],
    )

    result = _run(_config())

    unresolved = result["portfolio"]["unresolved_operations"][0]
    assert result["status"] == "complete"
    assert unresolved["phase"] == "confirmed_pending_create"
    assert unresolved["continue_create"] == {
        "routine": "lp_executor_request",
        "config": {
            "controller_id": "lp_expert.orca_1",
            "tick": 2,
            "operation_id": f"lp_expert_orca_1-t2-{POOL}-create",
            "preparation_operation_id": preparation_id,
        },
    }
    assert "restore" not in unresolved
    assert "executors" not in result["portfolio"]
    assert len(client.report_payload["portfolio"]["executors"]) == 2
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


def test_snapshot_blocks_scan_and_emits_exact_restoration_after_create_rejection(
    monkeypatch, tmp_path
):
    preparation_id = f"lp_expert_orca_1-t1-{POOL}-prepare"
    _install(
        monkeypatch,
        tmp_path,
        unconsumed=[
            {
                "operation_id": preparation_id,
                "operation_kind": "swap",
                "tick": 1,
                "phase": "confirmed",
                "intent": {
                    "reason": "inventory_preparation",
                    "pool_address": POOL,
                    "base_symbol": "SOL",
                    "base_mint": SOL_MINT,
                    "base_decimals": 9,
                },
                "result": {
                    "receipt": {
                        "transaction_hash": "tx-preparation",
                        "output_amount": "0.020228969",
                    }
                },
            }
        ],
        creates=[
            {
                "operation_id": f"lp_expert_orca_1-t1-{POOL}-create",
                "operation_kind": "create",
                "tick": 1,
                "phase": "rejected_before_submit",
                "mutation_possible": False,
                "intent": {
                    "preparation_operation_id": preparation_id,
                },
            }
        ],
    )

    result = _run(_config())

    unresolved = result["portfolio"]["unresolved_operations"][0]
    assert unresolved["phase"] == "confirmed_pending_restore"
    assert unresolved["prepared_inventory"]["amount"] == "0.020228969"
    assert unresolved["restore"] == {
        "routine": "lp_order_request",
        "config": {
            "controller_id": "lp_expert.orca_1",
            "operation_id": (f"lp_expert_orca_1-t2-{POOL}-restore"),
            "reason": "inventory_restoration",
            "pool_address": POOL,
            "base_symbol": "SOL",
            "base_mint": SOL_MINT,
            "base_decimals": 9,
            "amount": "0.020228969",
            "attributed_base_amount": "0.020228969",
            "attribution_operation_id": preparation_id,
        },
    }
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


def test_snapshot_recovers_legacy_admitted_create_from_one_exact_executor(
    monkeypatch, tmp_path
):
    row = executor_row("executor-created")
    _install(
        monkeypatch,
        tmp_path,
        rows=[row],
        unresolved=[
            {
                "operation_id": "admitted-create-1",
                "operation_kind": "create",
                "tick": 1,
                "phase": "admitted",
                "mutation_possible": False,
                "result": {"executor_request": _create_request(row)},
            }
        ],
    )

    result = _run(_config())

    assert result["portfolio"]["unresolved_operations"][0]["reconcile"] == {
        "routine": "lp_executor_request",
        "config": {
            "controller_id": "lp_expert.orca_1",
            "operation_id": "admitted-create-1",
            "lp_executor_id": "executor-created",
        },
    }
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


def test_snapshot_recovers_create_from_equivalent_native_lp_values(
    monkeypatch, tmp_path
):
    expected = executor_row("executor-created")
    expected["config"].update(
        {
            "side": 3,
            "lower_limit_price": "71.0821772893137114",
            "upper_limit_price": "76.9098811113271891",
            "keep_position": False,
        }
    )
    observed = copy.deepcopy(expected)
    observed["config"].update(
        {
            "side": "RANGE",
            "lower_limit_price": 71.0821772893137,
            "upper_limit_price": 76.9098811113272,
        }
    )
    _install(
        monkeypatch,
        tmp_path,
        rows=[observed],
        unresolved=[
            {
                "operation_id": "admitted-create-1",
                "operation_kind": "create",
                "tick": 1,
                "phase": "admitted",
                "mutation_possible": False,
                "result": {"executor_request": _create_request(expected)},
            }
        ],
    )

    result = _run(_config())

    assert result["portfolio"]["unresolved_operations"][0]["reconcile"] == {
        "routine": "lp_executor_request",
        "config": {
            "controller_id": "lp_expert.orca_1",
            "operation_id": "admitted-create-1",
            "lp_executor_id": "executor-created",
        },
    }
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


@pytest.mark.parametrize("match_count", [0, 2])
def test_snapshot_does_not_guess_create_recovery_without_one_exact_match(
    monkeypatch, tmp_path, match_count
):
    expected = executor_row("expected")
    rows = []
    if match_count == 0:
        rows = [executor_row("different", pool_address="different-pool")]
    else:
        rows = [
            executor_row("duplicate-one"),
            executor_row("duplicate-two"),
        ]
    _install(
        monkeypatch,
        tmp_path,
        rows=rows,
        unresolved=[
            {
                "operation_id": "admitted-create-1",
                "operation_kind": "create",
                "tick": 1,
                "phase": "admitted",
                "mutation_possible": False,
                "result": {"executor_request": _create_request(expected)},
            }
        ],
    )

    result = _run(_config())
    unresolved = result["portfolio"]["unresolved_operations"][0]

    assert "reconcile" not in unresolved
    if match_count == 2:
        assert "multiple live executors" in unresolved["reason"]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False


def test_snapshot_waits_for_incomplete_create_recovery_detail(monkeypatch, tmp_path):
    expected = executor_row("expected")
    expected["config"]["upper_limit_price"] = "210"
    incomplete = copy.deepcopy(expected)
    incomplete["config"].pop("upper_limit_price")
    _install(
        monkeypatch,
        tmp_path,
        rows=[incomplete],
        unresolved=[
            {
                "operation_id": "admitted-create-1",
                "operation_kind": "create",
                "tick": 1,
                "phase": "admitted",
                "mutation_possible": False,
                "result": {"executor_request": _create_request(expected)},
            }
        ],
    )

    result = _run(_config())
    unresolved = result["portfolio"]["unresolved_operations"][0]

    assert "reconcile" not in unresolved
    assert "detail is incomplete" in unresolved["reason"]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["scan_allowed"] is False
