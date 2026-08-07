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
    pool_record,
    prior_close,
    runtime_scope,
    strategy_config,
    terminal_row,
)
from pydantic import ValidationError

from agents.lp_expert.core import orca, portfolio
from agents.lp_expert.routines import lp_snapshot


def _candidate():
    normalized, error = orca.normalize_record(pool_record(), "all", "volume24h", 1)
    assert error is None
    unique, rejected = orca.deduplicate([normalized])
    assert not rejected
    return orca.rank_pools(unique)[0]


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

    async def get_network_tokens(self, network):
        assert network == "solana-mainnet-beta"
        return {
            "tokens": [
                {
                    "address": SOL_MINT,
                    "symbol": "SOL",
                    "decimals": 9,
                }
            ]
        }


def _install(
    monkeypatch,
    tmp_path,
    *,
    rows=None,
    pages=None,
    tick=2,
    scan=True,
    stop_error=None,
    unresolved=None,
    config=None,
    candidate_count=1,
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
        return {**payload, "report_id": "snapshot-report", "report_error": None}

    monkeypatch.setattr(lp_snapshot, "attach_report", attach)
    if unresolved is not None:

        class ReadOnlyStore:
            def __init__(self, resolved_scope, *, read_only):
                self.scope = resolved_scope
                self.read_only = read_only

            def unresolved_operations(self):
                assert self.read_only is True
                return copy.deepcopy(unresolved)

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
    scan_calls = []

    async def scan_pools(limit):
        scan_calls.append(limit)
        if not scan:
            raise AssertionError("candidate scan must be skipped")
        candidate = _candidate()
        candidates = [
            {
                **copy.deepcopy(candidate),
                "pool_address": (
                    candidate["pool_address"] if index == 0 else f"pool-{index + 1}"
                ),
            }
            for index in range(candidate_count)
        ]
        return {
            "status": "complete",
            "deployable": True,
            "source_coverage": {
                "required_requests": 4,
                "completed_requests": 4,
                "requests": [],
            },
            "universe": {
                "raw_records": 1,
                "normalized_records": 1,
                "valid_unique_pools": 1,
                "returned_candidates": len(candidates),
            },
            "technical_rejections": {},
            "candidates": candidates,
        }

    monkeypatch.setattr(lp_snapshot.orca, "scan_pools", scan_pools)
    return client, scan_calls


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
    return json.loads(asyncio.run(lp_snapshot.run(config, None)))


def test_snapshot_flat_portfolio_returns_candidates_and_dynamic_constraints(
    monkeypatch, tmp_path
):
    _, calls = _install(monkeypatch, tmp_path)

    result = _run(_config())

    assert result["status"] == "complete"
    assert result["mutation"] is False
    assert result["portfolio"]["active_count"] == 0
    assert result["portfolio"]["available_slots"] == 3
    assert result["deployable"] is True
    assert len(result["candidates"]) == 1
    assert "plan" not in result["candidates"][0]
    assert result["selection_constraints"]["allocation_quote"]["minimum"] == "3"
    assert result["selection_constraints"]["allocation_quote"]["maximum"] == "4"
    assert result["selection_constraints"]["available_deployments_this_tick"] == 1
    assert calls == [3]
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
    assert result["deployable"] is True


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
    _, calls = _install(
        monkeypatch,
        tmp_path,
        config=configured,
        candidate_count=2,
    )

    result = _run(_config())

    assert calls == [6]
    assert result["portfolio"]["available_slots"] == 5
    assert len(result["candidates"]) == 2
    assert result["selection_constraints"]["configured_deployments_per_tick"] == 2
    assert result["selection_constraints"]["available_deployments_this_tick"] == 2


def test_snapshot_triggered_executor_blocks_candidate_work(monkeypatch, tmp_path):
    _install(
        monkeypatch,
        tmp_path,
        rows=[executor_row(net_pnl_pct="0.06")],
        scan=False,
    )

    result = _run(_config())

    assert result["portfolio"]["close_required_executor_ids"] == ["executor-1"]
    assert result["deployable"] is False
    assert result["candidates"] == []


def test_snapshot_reconciling_executor_blocks_deployment(monkeypatch, tmp_path):
    _install(
        monkeypatch,
        tmp_path,
        rows=[executor_row(state="CLOSING")],
        scan=False,
    )

    result = _run(_config())

    assert result["portfolio"]["reconciling_executor_ids"] == ["executor-1"]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["deployable"] is False


def test_snapshot_foreign_active_executor_is_read_only_and_blocks_deploy(
    monkeypatch, tmp_path
):
    _install(
        monkeypatch,
        tmp_path,
        rows=[executor_row("foreign", controller_id="foreign.agent_1")],
        scan=False,
    )

    result = _run(_config())

    assert result["portfolio"]["active_count"] == 0
    assert result["portfolio"]["foreign_active_executor_ids"] == ["foreign"]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["deployable"] is False


def test_snapshot_follows_pagination_and_supports_three_executors(
    monkeypatch, tmp_path
):
    pages = {
        None: {
            "data": [executor_row("one", pool_address="pool-one")],
            "next_cursor": "second",
        },
        "second": {
            "data": [
                executor_row("two", pool_address="pool-two"),
                executor_row("three", pool_address="pool-three"),
            ],
            "next_cursor": None,
        },
    }
    client, _ = _install(
        monkeypatch,
        tmp_path,
        pages=pages,
        scan=False,
    )

    result = _run(_config())

    assert result["portfolio"]["active_count"] == 3
    assert result["portfolio"]["available_slots"] == 0
    assert result["portfolio"]["occupied_pools"] == [
        "pool-one",
        "pool-three",
        "pool-two",
    ]
    assert result["deployable"] is False
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
        scan=False,
    )

    result = _run(_config(close=close))

    assert result["cleanup_tick"] is True
    assert result["portfolio"]["cleanups"][0]["status"] == expected
    assert result["candidates"] == []
    assert result["deployable"] is False


def test_snapshot_rejects_cleanup_on_the_close_tick(monkeypatch, tmp_path):
    close = _snapshot_close(closed_tick=2)
    _install(
        monkeypatch,
        tmp_path,
        tick=2,
        rows=[terminal_row()],
        scan=False,
    )

    result = _run(_config(close=close))

    assert result["status"] == "rejected"
    assert "requires a later tick" in result["reason"]
    assert result["mutation"] is False
    assert result["deployable"] is False


def test_snapshot_cleanup_ambiguity_is_manual_quarantine(monkeypatch, tmp_path):
    row = terminal_row()
    del row["custom_info"]["close_transaction_hash"]
    close = _snapshot_close()
    _install(
        monkeypatch,
        tmp_path,
        rows=[row],
        scan=False,
    )

    result = _run(_config(close=close))

    assert result["portfolio"]["cleanups"][0]["status"] == "manual_review"
    assert result["portfolio"]["cleanups"][0]["capacity_quarantined"] is True
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["deployable"] is False


def test_snapshot_prior_closes_are_unique_and_capped_by_runtime(monkeypatch, tmp_path):
    close = _snapshot_close()
    with pytest.raises(ValidationError, match="must be unique"):
        _config(closes=[close, close])
    _install(monkeypatch, tmp_path, scan=False)
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
    _install(monkeypatch, tmp_path, rows=rows, scan=False)

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
    assert result["candidates"] == []
    assert result["deployable"] is False


def test_snapshot_missing_or_ambiguous_platform_stop_is_manual_quarantine(
    monkeypatch, tmp_path
):
    _install(
        monkeypatch,
        tmp_path,
        rows=[terminal_row()],
        scan=False,
        stop_error=ValueError("one exact prior-tick native stop intent was not proven"),
    )

    result = _run(_config(close=_snapshot_close()))

    cleanup = result["portfolio"]["cleanups"][0]
    assert cleanup["status"] == "manual_review"
    assert cleanup["capacity_quarantined"] is True
    assert "not proven" in cleanup["reason"]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["candidates"] == []
    assert result["deployable"] is False


def test_snapshot_persisted_unresolved_operation_blocks_scan_and_deploy(
    monkeypatch, tmp_path
):
    _install(
        monkeypatch,
        tmp_path,
        scan=False,
        unresolved=[
            {
                "operation_id": "uncertain-swap-1",
                "operation_kind": "swap",
                "tick": 1,
                "phase": "uncertain",
                "mutation_possible": True,
                "reason": "submission outcome unavailable",
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
        }
    ]
    assert result["portfolio"]["deployment_blocked"] is True
    assert result["candidates"] == []
    assert result["deployable"] is False
