from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from conftest import (
    POOL,
    SOL_MINT,
    WALLET,
    pool_record,
    runtime_scope,
    strategy_config,
)

from agents.lp_expert.core import orca
from agents.lp_expert.core.receipts import OperationIdentity
from agents.lp_expert.routines import lp_swap


def _candidate():
    value, error = orca.normalize_record(pool_record(), "all", "volume24h", 1)
    assert error is None
    unique, rejected = orca.deduplicate([value])
    assert not rejected
    return orca.rank_pools(unique)[0]


class Store:
    next_existing = None
    next_conflict = None
    next_identity_conflict = False
    next_preparation = None
    next_create_records = None
    next_swap_records = None
    latest = None

    def __init__(self, scope):
        self.scope = scope
        self.existing = copy.deepcopy(type(self).next_existing)
        self.conflict = copy.deepcopy(type(self).next_conflict)
        self.identity_conflict = type(self).next_identity_conflict
        self.preparation = copy.deepcopy(type(self).next_preparation)
        self.create_records = copy.deepcopy(type(self).next_create_records or [])
        self.swap_records = copy.deepcopy(type(self).next_swap_records or [])
        self.writes = []
        self.preparation_admissions = []
        type(self).latest = self

    def identity(self, *, operation_id, operation_kind, intent):
        return OperationIdentity(
            controller_id=self.scope.controller_id,
            strategy_slug=self.scope.strategy_slug,
            operation_id=operation_id,
            operation_kind=operation_kind,
            tick=self.scope.current_tick,
            account_name=self.scope.account_name,
            network=self.scope.network,
            wallet_address=self.scope.wallet_address,
            intent=intent,
        )

    def read(self, _identity):
        if self.identity_conflict:
            raise ValueError("operation receipt identity or content is invalid")
        return copy.deepcopy(self.existing)

    def read_by_id(self, operation_id):
        assert operation_id
        return copy.deepcopy(self.existing)

    def unresolved_operations(self):
        return [copy.deepcopy(self.conflict)] if self.conflict else []

    def write(
        self,
        identity,
        *,
        phase,
        mutation_possible,
        result=None,
        reason=None,
        **_,
    ):
        now = datetime.now(timezone.utc).isoformat()
        value = {
            "version": 1,
            "created_at": (
                self.existing.get("created_at")
                if isinstance(self.existing, dict)
                else now
            ),
            "updated_at": now,
            **identity.as_dict(),
            "phase": phase,
            "mutation_possible": mutation_possible,
            "result": copy.deepcopy(result),
            "reason": reason,
        }
        self.existing = value
        self.writes.append(copy.deepcopy(value))
        return value

    def admit_preparation(self, identity):
        self.preparation_admissions.append(identity.operation_id)

    def read_confirmed_swap(self, operation_id):
        if self.preparation is None:
            raise ValueError("confirmed preparation swap receipt is unavailable")
        assert self.preparation["operation_id"] == operation_id
        return copy.deepcopy(self.preparation)

    def list_records(self, operation_kind=None):
        if operation_kind == "create":
            return copy.deepcopy(self.create_records)
        if operation_kind == "swap":
            return copy.deepcopy(self.swap_records)
        return copy.deepcopy(self.create_records + self.swap_records)


class Lock:
    entries = 0

    async def __aenter__(self):
        type(self).entries += 1
        return self

    async def __aexit__(self, *_):
        return False


class GatewaySwap:
    def __init__(
        self,
        *,
        quote_in="2.054723",
        quote_out="0.011415132",
        execute_response=None,
        execute_error=None,
        status_response=None,
        history=None,
    ):
        self.quote_in = quote_in
        self.quote_out = quote_out
        self.execute_response = execute_response or {
            "transaction_hash": "tx-swap",
            "status": "CONFIRMED",
            "input_amount": quote_in,
            "output_amount": quote_out,
        }
        self.execute_error = execute_error
        self.status_response = status_response
        self.history = copy.deepcopy(history or [])
        self.quote_calls = []
        self.execute_calls = []
        self.status_calls = []
        self.history_calls = []

    async def get_swap_quote(self, **kwargs):
        self.quote_calls.append(copy.deepcopy(kwargs))
        return {"input_amount": self.quote_in, "output_amount": self.quote_out}

    async def execute_swap(self, **kwargs):
        self.execute_calls.append(copy.deepcopy(kwargs))
        if self.execute_error:
            raise self.execute_error
        return copy.deepcopy(self.execute_response)

    async def get_swap_status(self, transaction_hash):
        self.status_calls.append(transaction_hash)
        if isinstance(self.status_response, Exception):
            raise self.status_response
        return copy.deepcopy(
            self.status_response
            or {
                "transaction_hash": transaction_hash,
                "status": "CONFIRMED",
                "input_amount": self.quote_in,
                "output_amount": self.quote_out,
            }
        )

    async def search_swaps(self, **kwargs):
        self.history_calls.append(copy.deepcopy(kwargs))
        return {"data": copy.deepcopy(self.history)}


class Gateway:
    async def get_network_tokens(self, network):
        assert network == "solana-mainnet-beta"
        return {
            "tokens": [
                {"address": SOL_MINT, "symbol": "SOL", "decimals": 9},
                {
                    "address": orca.USDC_MINT,
                    "symbol": "USDC",
                    "decimals": 6,
                },
            ]
        }


class Executors:
    def __init__(self, *, rows=None, detail=None):
        self.rows = copy.deepcopy(rows or [])
        self.detail = copy.deepcopy(detail)
        self.search_calls = []
        self.detail_calls = []

    async def search_executors(self, **kwargs):
        self.search_calls.append(copy.deepcopy(kwargs))
        return {"data": copy.deepcopy(self.rows), "next_cursor": None}

    async def get_executor(self, executor_id):
        self.detail_calls.append(executor_id)
        return copy.deepcopy(self.detail)


def _preparation_record(amount="0.01"):
    return {
        "operation_id": "prepare-operation-1",
        "phase": "confirmed",
        "intent": {
            "reason": "inventory_preparation",
            "pool_address": POOL,
            "trading_pair": "SOL-USDC",
            "base_mint": SOL_MINT,
            "base_decimals": 9,
        },
        "result": {
            "receipt": {
                "transaction_hash": "tx-preparation",
                "input_amount": "1.8",
                "output_amount": amount,
            }
        },
    }


def _cleanup_detail(
    *,
    amount="0.01",
    native_status="FAILED",
    executor_id="executor-closed",
    controller_id="lp_expert.orca_1",
    position_id="position-closed",
):
    return {
        "executor_id": executor_id,
        "controller_id": controller_id,
        "status": "CLOSED",
        "is_active": False,
        "config": {
            "type": "lp_executor",
            "controller_id": controller_id,
            "pool_address": POOL,
            "trading_pair": "SOL-USDC",
            "base_symbol": "SOL",
            "base_mint": SOL_MINT,
            "base_decimals": 9,
        },
        "custom_info": {
            "position_id": position_id,
            "residual_base_amount": amount,
            "native_swap_status": native_status,
            "close_transaction_hash": "tx-close",
            "close_timestamp": "2026-08-06T00:00:00Z",
        },
    }


def _prep_config(**overrides):
    values = {
        "controller_id": "lp_expert.orca_1",
        "operation_id": "prepare-operation-1",
        "reason": "inventory_preparation",
        "candidate": _candidate(),
        "amount_quote": "4",
        "range_half_width_pct": "10",
    }
    values.update(overrides)
    return lp_swap.Config(**values)


def _restoration_config(**overrides):
    values = {
        "controller_id": "lp_expert.orca_1",
        "operation_id": "restore-operation-1",
        "reason": "inventory_restoration",
        "pool_address": POOL,
        "base_symbol": "SOL",
        "base_mint": SOL_MINT,
        "base_decimals": 9,
        "amount": "0.01",
        "attributed_base_amount": "0.01",
        "attribution_operation_id": "prepare-operation-1",
    }
    values.update(overrides)
    return lp_swap.Config(**values)


def _cleanup_config(**overrides):
    values = {
        "controller_id": "lp_expert.orca_1",
        "operation_id": "cleanup-operation-1",
        "reason": "post_close_residual_cleanup",
        "pool_address": POOL,
        "base_symbol": "SOL",
        "base_mint": SOL_MINT,
        "base_decimals": 9,
        "amount": "0.01",
        "attributed_base_amount": "0.01",
        "executor_id": "executor-closed",
        "position_id": "position-closed",
        "closed_tick": 1,
    }
    values.update(overrides)
    return lp_swap.Config(**values)


def _existing(config, *, phase, result=None, mutation_possible=True):
    scope = SimpleNamespace(
        controller_id=config.controller_id,
        strategy_slug="orca",
        account_name="master_account",
        network="solana-mainnet-beta",
        wallet_address="wallet-1",
        current_tick=2,
    )
    identity = OperationIdentity(
        controller_id=scope.controller_id,
        strategy_slug=scope.strategy_slug,
        operation_id=config.operation_id,
        operation_kind="swap",
        tick=scope.current_tick,
        account_name=scope.account_name,
        network=scope.network,
        wallet_address=scope.wallet_address,
        intent=lp_swap._intent(
            lp_swap._resolve_request(
                config,
                SimpleNamespace(config=strategy_config()),
            )
        ),
    )
    now = "2026-08-06T00:00:00+00:00"
    return {
        "version": 1,
        "created_at": now,
        "updated_at": now,
        **identity.as_dict(),
        "phase": phase,
        "mutation_possible": mutation_possible,
        "result": copy.deepcopy(result),
        "reason": None,
    }


def _install(
    monkeypatch,
    tmp_path,
    *,
    config,
    gateway_swap=None,
    balances=None,
    executors=None,
    existing=None,
    conflict=None,
    identity_conflict=False,
    preparation=None,
    create_records=None,
    swap_records=None,
    mode="loop",
    stop_error=None,
):
    scope = runtime_scope(tmp_path, mode=mode, tick=2)
    client = SimpleNamespace(
        gateway=Gateway(),
        gateway_swap=gateway_swap or GatewaySwap(),
        executors=executors or Executors(),
    )
    Store.next_existing = copy.deepcopy(existing)
    Store.next_conflict = copy.deepcopy(conflict)
    Store.next_identity_conflict = identity_conflict
    Store.next_preparation = copy.deepcopy(preparation)
    Store.next_create_records = copy.deepcopy(create_records or [])
    Store.next_swap_records = copy.deepcopy(swap_records or [])
    Store.latest = None
    Lock.entries = 0
    monkeypatch.setattr(lp_swap, "resolve_runtime", lambda _: scope)

    async def get_client(_):
        return client

    async def bind(value, _):
        return value

    async def refresh(_scope, _client):
        return copy.deepcopy(
            balances
            or [
                {"symbol": "SOL", "mint": SOL_MINT, "available": "2"},
                {
                    "symbol": "USDC",
                    "mint": orca.USDC_MINT,
                    "available": "10",
                },
            ]
        )

    async def refresh_candidate(candidate):
        return copy.deepcopy(candidate)

    async def attach(payload, **_):
        return {**payload, "report_id": "swap-report", "report_error": None}

    def stop(scope, executor_id, closed_tick):
        if stop_error:
            raise stop_error
        return {
            "tick": closed_tick,
            "controller_id": scope.controller_id,
            "executor_id": executor_id,
            "keep_position": False,
        }

    monkeypatch.setattr(lp_swap, "get_hummingbot_client", get_client)
    monkeypatch.setattr(lp_swap, "bind_wallet", bind)
    monkeypatch.setattr(lp_swap, "refresh_balances", refresh)
    monkeypatch.setattr(lp_swap.orca, "refresh_candidate", refresh_candidate)
    monkeypatch.setattr(lp_swap, "ReceiptStore", Store)
    monkeypatch.setattr(lp_swap, "controller_mutation_lock", lambda _: Lock())
    monkeypatch.setattr(lp_swap, "read_prior_tick_stop", stop)
    monkeypatch.setattr(lp_swap.reporting, "attach_report", attach)
    return client


def _run(config):
    return json.loads(asyncio.run(lp_swap.run(config, None)))


def test_preparation_swap_enforces_cap_and_serializes_one_mutation(
    monkeypatch, tmp_path
):
    config = _prep_config()
    client = _install(monkeypatch, tmp_path, config=config)

    result = _run(config)

    assert result["status"] == "confirmed"
    assert result["receipt"]["input_amount"] == "2.054723"
    assert result["receipt"]["output_amount"] == "0.011415132"
    assert result["retry_allowed"] is False
    assert len(client.gateway_swap.execute_calls) == 1
    assert (
        str(client.gateway_swap.execute_calls[0]["amount"])
        == result["selection_plan"]["inventory"]["base_shortfall"]
    )
    assert Store.latest.preparation_admissions == ["prepare-operation-1"]
    assert Lock.entries == 1
    assert result["report_id"] == "swap-report"


def test_preparation_quote_over_cap_is_rejected_before_submit(monkeypatch, tmp_path):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="2.1", quote_out="0.01"),
    )

    result = _run(config)

    assert result["status"] == "rejected"
    assert result["mutation"] is False
    assert result["retry_allowed"] is True
    assert "cap" in result["reason"]
    assert client.gateway_swap.execute_calls == []


def test_preparation_cap_is_derived_from_frozen_selection_plan(monkeypatch, tmp_path):
    config = _prep_config()
    client = _install(monkeypatch, tmp_path, config=config)

    result = _run(config)

    assert result["status"] == "confirmed"
    assert (
        result["selection_plan"]["inventory"]["max_usdc_for_preparation_swap"]
        == "2.075272"
    )
    assert client.gateway_swap.quote_calls[0]["slippage_pct"] == 1


def test_sell_reserve_guard_blocks_exact_sol_attribution(monkeypatch, tmp_path):
    config = _restoration_config(amount="0.06", attributed_base_amount="0.06")
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="0.06", quote_out="10"),
        balances=[
            {"symbol": "SOL", "mint": SOL_MINT, "available": "0.1"},
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "10",
            },
        ],
        preparation=_preparation_record("0.06"),
    )

    result = _run(config)

    assert result["status"] == "rejected"
    assert "reserve" in result["reason"]
    assert result["mutation"] is False
    assert client.gateway_swap.execute_calls == []


def test_confirmed_operation_id_is_idempotent_and_never_resubmits(
    monkeypatch, tmp_path
):
    config = _prep_config()
    receipt = {
        "transaction_hash": "tx-existing",
        "status": "CONFIRMED",
        "input_amount": "2.054723",
        "output_amount": "0.011415132",
    }
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        existing=_existing(
            config,
            phase="confirmed",
            result={"receipt": receipt},
        ),
    )

    result = _run(config)

    assert result["status"] == "confirmed"
    assert result["receipt"]["transaction_hash"] == "tx-existing"
    assert client.gateway_swap.quote_calls == []
    assert client.gateway_swap.execute_calls == []


def test_swap_operation_id_intent_conflict_is_manual_review_without_retry(
    monkeypatch, tmp_path
):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        existing=_existing(config, phase="uncertain"),
        identity_conflict=True,
    )

    result = _run(config)

    assert result["status"] == "manual_review"
    assert result["retry_allowed"] is False
    assert "identity conflicts" in result["reason"]
    assert client.gateway_swap.quote_calls == []
    assert client.gateway_swap.execute_calls == []


def test_uncertain_operation_recovers_one_exact_gateway_history_match(
    monkeypatch, tmp_path
):
    config = _prep_config()
    history = [
        {
            "transaction_hash": "tx-recovered",
            "status": "CONFIRMED",
            "input_amount": "2.054723",
            "output_amount": "0.011415132",
            "timestamp": "2026-08-06T00:00:01+00:00",
            "connector": "jupiter",
            "network": "solana-mainnet-beta",
            "wallet_address": WALLET,
            "trading_pair": "SOL-USDC",
            "side": "BUY",
        }
    ]
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        existing=_existing(config, phase="uncertain"),
        gateway_swap=GatewaySwap(history=history),
    )

    result = _run(config)

    assert result["status"] == "confirmed"
    assert result["receipt"]["transaction_hash"] == "tx-recovered"
    assert result["recovery_source"] == "gateway_history"
    assert result["retry_allowed"] is False
    assert client.gateway_swap.execute_calls == []
    assert len(client.gateway_swap.history_calls) == 1


def test_multiple_recovery_matches_are_manual_quarantine(monkeypatch, tmp_path):
    config = _prep_config()
    common = {
        "status": "CONFIRMED",
        "input_amount": "2.054723",
        "output_amount": "0.011415132",
        "timestamp": "2026-08-06T00:00:01+00:00",
        "connector": "jupiter",
        "network": "solana-mainnet-beta",
        "wallet_address": WALLET,
        "trading_pair": "SOL-USDC",
        "side": "BUY",
    }
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        existing=_existing(config, phase="uncertain"),
        gateway_swap=GatewaySwap(
            history=[
                {"transaction_hash": "tx-one", **common},
                {"transaction_hash": "tx-two", **common},
            ]
        ),
    )

    result = _run(config)

    assert result["status"] == "manual_review"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert client.gateway_swap.execute_calls == []


def test_submission_timeout_is_uncertain_and_not_retried(monkeypatch, tmp_path):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(execute_error=TimeoutError("outcome unavailable")),
    )

    result = _run(config)

    assert result["status"] == "uncertain"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert len(client.gateway_swap.execute_calls) == 1
    assert Store.latest.writes[-1]["phase"] == "uncertain"


def test_other_uncertain_wallet_operation_blocks_swap(monkeypatch, tmp_path):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        conflict={
            "operation_id": "other-operation",
            "phase": "uncertain",
            "mutation_possible": True,
        },
    )

    result = _run(config)

    assert result["status"] == "manual_review"
    assert result["retry_allowed"] is False
    assert "remains unresolved" in result["reason"]
    assert client.gateway_swap.quote_calls == []
    assert client.gateway_swap.execute_calls == []


def test_second_preparation_waits_for_prior_confirmed_create(monkeypatch, tmp_path):
    config = _prep_config(operation_id="prepare-operation-2")
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        swap_records=[
            {
                "operation_id": "prepare-operation-1",
                "phase": "confirmed",
                "intent": {"reason": "inventory_preparation"},
            }
        ],
    )

    result = _run(config)

    assert result["status"] == "manual_review"
    assert "has not been consumed" in result["reason"]
    assert client.gateway_swap.quote_calls == []
    assert client.gateway_swap.execute_calls == []


def test_second_preparation_can_follow_prior_confirmed_create(monkeypatch, tmp_path):
    config = _prep_config(operation_id="prepare-operation-2")
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        swap_records=[
            {
                "operation_id": "prepare-operation-1",
                "phase": "confirmed",
                "intent": {"reason": "inventory_preparation"},
            }
        ],
        create_records=[
            {
                "operation_id": "create-operation-1",
                "phase": "confirmed",
                "intent": {"preparation_operation_id": "prepare-operation-1"},
            }
        ],
    )

    result = _run(config)

    assert result["status"] == "confirmed"
    assert len(client.gateway_swap.execute_calls) == 1


def test_cleanup_sells_exact_residual_and_releases_capacity_only_on_confirmation(
    monkeypatch, tmp_path
):
    config = _cleanup_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="0.01", quote_out="1.8"),
        executors=Executors(detail=_cleanup_detail()),
    )

    result = _run(config)

    assert result["status"] == "confirmed"
    assert result["capacity_release_allowed"] is True
    assert result["attribution"]["executor_id"] == "executor-closed"
    assert result["attribution"]["position_id"] == "position-closed"
    assert result["attribution"]["residual_base_amount"] == "0.01"
    assert client.gateway_swap.execute_calls[0]["amount"] == config.amount
    assert client.executors.detail_calls == ["executor-closed"]


def test_cleanup_sells_exact_material_residual_after_native_confirmed_glitch(
    monkeypatch, tmp_path
):
    config = _cleanup_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="0.01", quote_out="1.8"),
        executors=Executors(detail=_cleanup_detail(native_status="CONFIRMED")),
    )

    result = _run(config)

    assert result["status"] == "confirmed"
    assert result["attribution"]["native_close_status"] == "CONFIRMED"
    assert result["attribution"]["residual_base_amount"] == "0.01"
    assert result["capacity_release_allowed"] is True
    assert len(client.gateway_swap.execute_calls) == 1
    assert client.gateway_swap.execute_calls[0]["side"] == "SELL"
    assert client.gateway_swap.execute_calls[0]["amount"] == config.amount


def test_cleanup_report_links_stop_attribution_trace_and_release_evidence(
    monkeypatch, tmp_path
):
    config = _cleanup_config()
    _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="0.01", quote_out="1.8"),
        executors=Executors(detail=_cleanup_detail()),
    )
    captured = {}

    async def attach(payload, **kwargs):
        captured.update(kwargs)
        return {**payload, "report_id": "cleanup-report", "report_error": None}

    monkeypatch.setattr(lp_swap.reporting, "attach_report", attach)

    result = _run(config)

    assert result["report_id"] == "cleanup-report"
    assert result["capacity_release_allowed"] is True
    assert result["post_transaction_refresh"]["transaction_hash"] == "tx-swap"
    assert captured["routine_input"]["closed_tick"] == 1
    assert captured["links"]["executor_id"] == "executor-closed"
    assert captured["links"]["transaction_hash"] == "tx-swap"
    stages = [event["stage"] for event in captured["trace"].events]
    assert "attribution" in stages
    assert "cleanup_post_transaction_refresh" in stages


def test_cleanup_dust_does_not_submit_and_releases_capacity(monkeypatch, tmp_path):
    config = _cleanup_config(amount="0.00001", attributed_base_amount="0.00001")
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="0.00001", quote_out="0.001"),
        executors=Executors(detail=_cleanup_detail(amount="0.00001")),
    )

    result = _run(config)

    assert result["status"] == "not_submitted"
    assert result["mutation"] is False
    assert result["retry_allowed"] is False
    assert result["capacity_release_allowed"] is True
    assert client.gateway_swap.execute_calls == []


@pytest.mark.parametrize(
    ("detail", "stop_error", "reason"),
    [
        (
            _cleanup_detail(amount="0.02"),
            None,
            "residual attribution conflicts",
        ),
        (
            _cleanup_detail(controller_id="foreign.agent_1"),
            None,
            "residual attribution conflicts",
        ),
        (
            _cleanup_detail(),
            ValueError("one exact prior-tick stop was not proven"),
            "not proven",
        ),
    ],
)
def test_cleanup_ambiguous_foreign_or_unproven_identity_is_manual_quarantine(
    monkeypatch, tmp_path, detail, stop_error, reason
):
    config = _cleanup_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="0.01", quote_out="1.8"),
        executors=Executors(detail=detail),
        stop_error=stop_error,
    )

    result = _run(config)

    assert result["status"] == "manual_review"
    assert result["retry_allowed"] is False
    assert result["capacity_release_allowed"] is False
    assert reason in result["reason"]
    assert client.gateway_swap.execute_calls == []


def test_cleanup_unknown_submission_never_releases_capacity(monkeypatch, tmp_path):
    config = _cleanup_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(
            quote_in="0.01",
            quote_out="1.8",
            execute_error=TimeoutError("outcome unavailable"),
        ),
        executors=Executors(detail=_cleanup_detail()),
    )

    result = _run(config)

    assert result["status"] == "uncertain"
    assert result["retry_allowed"] is False
    assert result["capacity_release_allowed"] is False
    assert len(client.gateway_swap.execute_calls) == 1


def test_restoration_refuses_inventory_consumed_by_create(monkeypatch, tmp_path):
    config = _restoration_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="0.01", quote_out="1.8"),
        preparation=_preparation_record(),
        create_records=[
            {
                "phase": "confirmed",
                "intent": {"preparation_operation_id": "prepare-operation-1"},
            }
        ],
    )

    result = _run(config)

    assert result["status"] == "manual_review"
    assert "consumed by an executor create" in result["reason"]
    assert client.gateway_swap.execute_calls == []
