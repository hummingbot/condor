from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from conftest import (
    POOL,
    SOL_MINT,
    WALLET,
    executor_row,
    pool_record,
    runtime_scope,
    strategy_config,
)

from agents.lp_expert.core import orca, planner
from agents.lp_expert.core.receipts import OperationIdentity
from agents.lp_expert.routines import lp_order_request


def _candidate():
    value, error = orca.normalize_record(
        pool_record(),
        "all",
        "volume24h",
        1,
        10_000,
    )
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

    def identity(self, *, operation_id, operation_kind, intent, tick=None):
        return OperationIdentity(
            controller_id=self.scope.controller_id,
            strategy_slug=self.scope.strategy_slug,
            operation_id=operation_id,
            operation_kind=operation_kind,
            tick=self.scope.current_tick if tick is None else tick,
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

    def unconsumed_preparations(self):
        create_records = self.list_records("create")
        result = []
        for record in self.list_records("swap"):
            intent = record.get("intent")
            outcome = record.get("result")
            if (
                record.get("phase") != "confirmed"
                or not isinstance(intent, dict)
                or intent.get("reason") != "inventory_preparation"
                or not isinstance(outcome, dict)
                or not isinstance(outcome.get("receipt"), dict)
            ):
                continue
            consumers = [
                create
                for create in create_records
                if isinstance(create.get("intent"), dict)
                and create["intent"].get("preparation_operation_id")
                == record.get("operation_id")
                and create.get("phase") == "confirmed"
            ]
            if not consumers:
                result.append(copy.deepcopy(record))
        return result

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
    def __init__(
        self,
        slippage="1",
        swap_provider="jupiter/router",
        tokens=None,
        add_error=None,
    ):
        self.slippage = slippage
        self.swap_provider = swap_provider
        self.tokens = (
            [
                {"address": SOL_MINT, "symbol": "SOL", "decimals": 9},
                {
                    "address": orca.USDC_MINT,
                    "symbol": "USDC",
                    "decimals": 6,
                },
            ]
            if tokens is None
            else copy.deepcopy(tokens)
        )
        self.add_error = add_error
        self.add_calls = []

    async def get_network_config(self, network):
        assert network == "solana-mainnet-beta"
        return {
            "swapProvider": self.swap_provider,
            "node_url": "https://solana-rpc.example",
        }

    async def get_connector_config(self, connector_name):
        assert connector_name == "jupiter"
        return {"config": {"slippagePct": self.slippage}}

    async def get_network_tokens(self, network):
        assert network == "solana-mainnet-beta"
        return {"tokens": copy.deepcopy(self.tokens)}

    async def add_token(self, **kwargs):
        self.add_calls.append(copy.deepcopy(kwargs))
        if self.add_error:
            raise self.add_error
        self.tokens.append(
            {
                "address": kwargs["address"],
                "symbol": kwargs["symbol"],
                "decimals": kwargs["decimals"],
            }
        )


class Executors:
    def __init__(
        self,
        *,
        rows=None,
        detail=None,
        details=None,
        detail_error=None,
        schema_missing=None,
    ):
        self.rows = copy.deepcopy(rows or [])
        self.detail = copy.deepcopy(detail)
        self.details = copy.deepcopy(details or {})
        self.detail_error = detail_error
        self.schema_missing = schema_missing
        self.search_calls = []
        self.detail_calls = []

    async def get_executor_config_schema(self, executor_type):
        assert executor_type == "order_executor"
        fields = {
            "controller_id",
            "connector_name",
            "trading_pair",
            "side",
            "amount",
            "execution_strategy",
            "level_id",
        }
        if self.schema_missing:
            fields.remove(self.schema_missing)
        return {"properties": {field: {} for field in fields}}

    async def search_executors(self, **kwargs):
        self.search_calls.append(copy.deepcopy(kwargs))
        return {"data": copy.deepcopy(self.rows), "next_cursor": None}

    async def get_executor(self, executor_id):
        self.detail_calls.append(executor_id)
        if self.detail_error:
            raise self.detail_error
        if executor_id in self.details:
            return copy.deepcopy(self.details[executor_id])
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


def _order_detail(executor_request, executor_id="swap-order", tx_hash="tx-swap"):
    config = copy.deepcopy(executor_request["executor_config"])
    return {
        "executor_id": executor_id,
        "controller_id": executor_request["controller_id"],
        "status": "TERMINATED",
        "is_active": False,
        "close_type": "POSITION_HOLD",
        "config": config,
        "custom_info": {
            "level_id": config["level_id"],
            "held_position_orders": [{"exchange_order_id": tx_hash}],
        },
    }


def _solana_transaction(
    *,
    native_delta_lamports,
    quote_delta_raw,
    fee_lamports=5_000,
    transaction_hash="transaction-signature",
):
    native_before = 2_000_000_000
    quote_before = 10_000_000
    return {
        "slot": 123,
        "blockTime": 1_754_435_200,
        "transaction": {
            "signatures": [transaction_hash],
            "message": {
                "accountKeys": [
                    {"pubkey": WALLET, "signer": True, "writable": True},
                    {
                        "pubkey": "usdc-token-account",
                        "signer": False,
                        "writable": True,
                    },
                ]
            },
        },
        "meta": {
            "err": None,
            "fee": fee_lamports,
            "preBalances": [native_before, 2_039_280],
            "postBalances": [
                native_before + native_delta_lamports,
                2_039_280,
            ],
            "preTokenBalances": [
                {
                    "accountIndex": 1,
                    "mint": orca.USDC_MINT,
                    "owner": WALLET,
                    "uiTokenAmount": {"amount": str(quote_before), "decimals": 6},
                }
            ],
            "postTokenBalances": [
                {
                    "accountIndex": 1,
                    "mint": orca.USDC_MINT,
                    "owner": WALLET,
                    "uiTokenAmount": {
                        "amount": str(quote_before + quote_delta_raw),
                        "decimals": 6,
                    },
                }
            ],
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
    return lp_order_request.Config(**values)


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
    return lp_order_request.Config(**values)


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
    return lp_order_request.Config(**values)


@pytest.mark.parametrize(
    ("factory", "operation_id"),
    [
        pytest.param(
            _prep_config,
            "lp_expert.orca_17-t1-pool-prepare",
            id="preparation",
        ),
        pytest.param(
            _restoration_config,
            "lp_expert.orca_17-t2-pool-restore",
            id="restoration",
        ),
        pytest.param(
            _cleanup_config,
            "lp_expert.orca_17-t2-executor-cleanup",
            id="cleanup",
        ),
    ],
)
def test_swap_rejects_dotted_controller_operation_ids_at_input(factory, operation_id):
    with pytest.raises(ValueError, match="String should match pattern"):
        factory(operation_id=operation_id)


def test_swap_accepts_dynamic_receipt_safe_operation_ids():
    operation_id = f"lp_expert_orca_17-t1-{POOL}-prepare"

    config = _prep_config(operation_id=operation_id)

    assert config.operation_id == operation_id


def test_swap_accepts_compact_id_only_recovery_input():
    config = lp_order_request.Config(
        controller_id="lp_expert.orca_1",
        operation_id="prepare-operation-1",
        swap_executor_id="native-order-1",
    )

    assert config.reason is None


def test_finalized_solana_receipt_uses_exact_net_wallet_deltas(tmp_path):
    scope = runtime_scope(tmp_path)
    preparation = lp_order_request._resolve_request(
        _prep_config(),
        SimpleNamespace(config=strategy_config()),
    )
    buy = lp_order_request._solana_receipt_from_transaction(
        _solana_transaction(
            native_delta_lamports=11_415_132,
            quote_delta_raw=-2_054_723,
            transaction_hash="buy-transaction",
        ),
        "buy-transaction",
        preparation,
        scope,
    )
    cleanup = lp_order_request._resolve_request(
        _cleanup_config(),
        SimpleNamespace(config=strategy_config()),
    )
    sell = lp_order_request._solana_receipt_from_transaction(
        _solana_transaction(
            native_delta_lamports=-10_005_000,
            quote_delta_raw=1_800_000,
            transaction_hash="sell-transaction",
        ),
        "sell-transaction",
        cleanup,
        scope,
    )

    assert buy["status"] == "CONFIRMED"
    assert buy["input_amount"] == "2.054723"
    assert buy["output_amount"] == "0.011415132"
    assert buy["gross_output_amount"] == "0.011420132"
    assert buy["native_fee_amount"] == "0.000005"
    assert buy["evidence_source"] == "solana_rpc_finalized_balance_deltas"
    assert sell["input_amount"] == "0.010000"
    assert sell["output_amount"] == "1.8"


def test_transaction_receipt_uses_configured_rpc_and_exact_hash(monkeypatch, tmp_path):
    scope = runtime_scope(tmp_path)
    request = lp_order_request._resolve_request(
        _prep_config(),
        SimpleNamespace(config=strategy_config()),
    )
    observed = {}

    async def fetch(node_url, transaction_hash, *, timeout):
        observed.update(
            {
                "node_url": node_url,
                "transaction_hash": transaction_hash,
                "timeout": timeout,
            }
        )
        value = _solana_transaction(
            native_delta_lamports=11_415_132,
            quote_delta_raw=-2_054_723,
            transaction_hash=transaction_hash,
        )
        return value

    monkeypatch.setattr(lp_order_request, "_fetch_solana_transaction", fetch)
    receipt = asyncio.run(
        lp_order_request._solana_transaction_receipt(
            SimpleNamespace(gateway=Gateway(), timeout=None),
            scope,
            request,
            "exact-transaction",
        )
    )

    assert observed == {
        "node_url": "https://solana-rpc.example",
        "transaction_hash": "exact-transaction",
        "timeout": None,
    }
    assert receipt["transaction_hash"] == "exact-transaction"
    assert receipt["status"] == "CONFIRMED"


def test_restoration_rejects_unsafe_preparation_attribution_id():
    with pytest.raises(ValueError, match="String should match pattern"):
        _restoration_config(
            attribution_operation_id="lp_expert.orca_17-t1-pool-prepare"
        )


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
        intent=lp_order_request._intent(
            lp_order_request._resolve_request(
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
    gateway=None,
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
    strategy=None,
):
    scope = runtime_scope(
        tmp_path,
        mode=mode,
        tick=2,
        config=(
            strategy
            or strategy_config(
                execution_mode=mode,
                use_existing_base_inventory=False,
            )
        ),
    )
    client = SimpleNamespace(
        gateway=gateway or Gateway(),
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
    monkeypatch.setattr(lp_order_request, "resolve_runtime", lambda _: scope)

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

    async def refresh_candidate(candidate, minimum_tvl_usd):
        assert minimum_tvl_usd == 10_000
        return copy.deepcopy(candidate)

    async def attach(payload, **_):
        Store.latest.report_payload = copy.deepcopy(payload)
        return {**payload, "report_id": "swap-report", "report_error": None}

    async def solana_receipt(_client, resolved_scope, request, transaction_hash):
        raw = await _client.gateway_swap.get_swap_status(transaction_hash)
        if not isinstance(raw, dict):
            raise ValueError("test transaction receipt is invalid")
        return {
            "transaction_hash": transaction_hash,
            "status": str(raw.get("status") or "UNKNOWN").upper(),
            "input_amount": raw.get("input_amount"),
            "output_amount": raw.get("output_amount"),
            "network": resolved_scope.network,
            "wallet_address": resolved_scope.wallet_address,
            "trading_pair": request.trading_pair,
            "side": request.side,
            "evidence_source": "solana_rpc_finalized_balance_deltas",
        }

    def stop(scope, executor_id, closed_tick):
        if stop_error:
            raise stop_error
        return {
            "tick": closed_tick,
            "controller_id": scope.controller_id,
            "executor_id": executor_id,
            "keep_position": False,
        }

    monkeypatch.setattr(lp_order_request, "get_hummingbot_client", get_client)
    monkeypatch.setattr(lp_order_request, "bind_wallet", bind)
    monkeypatch.setattr(lp_order_request, "refresh_balances", refresh)
    monkeypatch.setattr(lp_order_request.orca, "refresh_candidate", refresh_candidate)
    monkeypatch.setattr(lp_order_request, "ReceiptStore", Store)
    monkeypatch.setattr(lp_order_request, "controller_mutation_lock", lambda _: Lock())
    monkeypatch.setattr(lp_order_request, "read_prior_tick_stop", stop)
    monkeypatch.setattr(lp_order_request, "_solana_transaction_receipt", solana_receipt)
    monkeypatch.setattr(lp_order_request.reporting, "attach_report", attach)
    return client


def _run(config):
    raw = asyncio.run(lp_order_request.run(config, None))
    assert len(raw) <= lp_order_request._TRANSPORT_MAX_CHARS
    result = json.loads(raw)
    assert result["transport_complete"] is True
    return result


def test_oversized_order_result_returns_valid_hold_capsule():
    config = _prep_config()
    raw = lp_order_request._model_result(
        {
            "status": "ready",
            "mutation": False,
            "mutation_classification": "admitted",
            "retry_allowed": False,
            "executor_request": {"oversized": "x" * 2_000},
            "report_id": "swap-report",
            "report_error": None,
        },
        config,
    )

    result = json.loads(raw)

    assert len(raw) <= lp_order_request._TRANSPORT_MAX_CHARS
    assert result["status"] == "ready"
    assert result["transport_complete"] is False
    assert result["retry_allowed"] is False
    assert "executor_request" not in result


def _reconcile_ready(client, config, ready, executor_id="swap-order"):
    Store.next_existing = copy.deepcopy(Store.latest.existing)
    if config.executor_id and client.executors.detail is not None:
        client.executors.details[config.executor_id] = copy.deepcopy(
            client.executors.detail
        )
    client.executors.details[executor_id] = _order_detail(
        ready["executor_request"],
        executor_id=executor_id,
    )
    return _run(config.model_copy(update={"swap_executor_id": executor_id}))


def test_preparation_swap_enforces_cap_and_serializes_one_mutation(
    monkeypatch, tmp_path
):
    config = _prep_config()
    client = _install(monkeypatch, tmp_path, config=config)

    ready = _run(config)

    assert ready["status"] == "ready"
    assert ready["mutation"] is False
    assert ready["executor_request"]["executor_type"] == "order_executor"
    assert ready["executor_request"]["controller_id"] == config.controller_id
    executor_config = ready["executor_request"]["executor_config"]
    assert executor_config["controller_id"] == config.controller_id
    assert executor_config["side"] == 1
    assert (
        executor_config["amount"]
        == Store.latest.report_payload["selection_plan"]["inventory"]["base_shortfall"]
    )
    assert "selection_plan" not in ready
    assert client.gateway_swap.execute_calls == []
    assert Store.latest.preparation_admissions == ["prepare-operation-1"]
    assert Lock.entries == 1

    result = _reconcile_ready(client, config, ready)

    assert result["status"] == "confirmed"
    assert result["receipt"]["input_amount"] == "2.054723"
    assert result["receipt"]["output_amount"] == "0.011415132"
    assert result["swap_executor_id"] == "swap-order"
    assert result["same_tick_lp_create_allowed"] is True
    assert result["retry_allowed"] is False
    assert client.gateway_swap.execute_calls == []
    assert result["report_id"] == "swap-report"


def test_preparation_accepts_compact_scan_candidate(monkeypatch, tmp_path):
    candidate = orca.compact_candidate(_candidate())
    config = _prep_config(candidate=candidate)
    _install(monkeypatch, tmp_path, config=config)

    async def refresh_candidate(selected, minimum_tvl_usd):
        assert selected == candidate
        assert minimum_tvl_usd == 10_000
        return _candidate()

    monkeypatch.setattr(lp_order_request.orca, "refresh_candidate", refresh_candidate)

    result = _run(config)

    assert result["status"] == "ready"
    assert result["executor_request"]["executor_config"]["trading_pair"] == "SOL-USDC"


def test_existing_sol_above_reserve_skips_preparation_swap(monkeypatch, tmp_path):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        strategy=strategy_config(use_existing_base_inventory=True),
        balances=[
            {
                "symbol": "SOL",
                "mint": SOL_MINT,
                "available": "0.562109782",
            },
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "2.917004",
            },
        ],
    )

    result = _run(config)

    assert result["status"] == "confirmed"
    assert result["mutation"] is False
    assert result["inventory_allocation"]["source"] == ("existing_wallet_balance")
    assert result["inventory_allocation"]["minimum_sol_reserve"] == "0.1"
    assert result["inventory_allocation"]["attributed_base_amount"] == (
        Store.latest.report_payload["selection_plan"]["inventory"]["base_amount"]
    )
    assert result["deployment_input"]["preparation_operation_id"] == (
        config.operation_id
    )
    assert client.gateway_swap.quote_calls == []
    assert Store.latest.preparation_admissions == [config.operation_id]


def test_existing_inventory_can_be_disabled_by_frozen_config(monkeypatch, tmp_path):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        strategy=strategy_config(use_existing_base_inventory=False),
        balances=[
            {"symbol": "SOL", "mint": SOL_MINT, "available": "2"},
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "10",
            },
        ],
    )

    result = _run(config)

    assert result["status"] == "ready"
    assert (
        Store.latest.report_payload["selection_plan"]["inputs"][
            "attributed_base_amount"
        ]
        == "0"
    )
    assert client.gateway_swap.quote_calls


def test_selected_unregistered_token_is_registered_before_preparation(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    candidate["token_a"] = {
        "symbol": "NEW",
        "mint": "new-token-mint",
        "decimals": 9,
    }
    candidate["trading_pair"] = "NEW-USDC"
    config = _prep_config(candidate=candidate)
    gateway = Gateway(
        tokens=[
            {
                "address": orca.USDC_MINT,
                "symbol": "USDC",
                "decimals": 6,
            }
        ]
    )
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway=gateway,
        balances=[
            {"symbol": "SOL", "mint": SOL_MINT, "available": "2"},
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "10",
            },
        ],
    )

    result = _run(config)

    assert result["status"] == "ready"
    assert result["token_registration"]["status"] == "confirmed"
    assert result["configuration_mutation"] is True
    assert gateway.add_calls == [
        {
            "network_id": "solana-mainnet-beta",
            "address": "new-token-mint",
            "symbol": "NEW",
            "decimals": 9,
            "name": "NEW",
        }
    ]
    assert client.gateway_swap.quote_calls


def test_tvl_deterioration_blocks_registration_and_preparation(monkeypatch, tmp_path):
    candidate = _candidate()
    candidate["token_a"] = {
        "symbol": "NEW",
        "mint": "new-token-mint",
        "decimals": 9,
    }
    candidate["trading_pair"] = "NEW-USDC"
    config = _prep_config(candidate=candidate)
    gateway = Gateway(
        tokens=[
            {
                "address": orca.USDC_MINT,
                "symbol": "USDC",
                "decimals": 6,
            }
        ]
    )
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway=gateway,
    )

    async def below_floor(_, minimum_tvl_usd):
        assert minimum_tvl_usd == 10_000
        raise ValueError("selected Orca pool is below minimum TVL 10000 USD")

    monkeypatch.setattr(lp_order_request.orca, "refresh_candidate", below_floor)

    result = _run(config)

    assert result["status"] == "rejected"
    assert "below minimum TVL 10000 USD" in result["reason"]
    assert result["mutation"] is False
    assert gateway.add_calls == []
    assert client.gateway_swap.quote_calls == []


def test_dry_run_only_proposes_selected_token_registration(monkeypatch, tmp_path):
    candidate = _candidate()
    candidate["token_a"] = {
        "symbol": "NEW",
        "mint": "new-token-mint",
        "decimals": 9,
    }
    candidate["trading_pair"] = "NEW-USDC"
    config = _prep_config(candidate=candidate)
    gateway = Gateway(
        tokens=[
            {
                "address": orca.USDC_MINT,
                "symbol": "USDC",
                "decimals": 6,
            }
        ]
    )
    _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway=gateway,
        mode="dry_run",
        balances=[
            {"symbol": "SOL", "mint": SOL_MINT, "available": "2"},
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "10",
            },
        ],
    )

    result = _run(config)

    assert result["status"] == "not_submitted"
    assert result["mutation"] is False
    assert result["proposed_token_registration"]["status"] == "proposed"
    assert gateway.add_calls == []


def test_selected_token_symbol_collision_is_rejected_without_registration(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    candidate["token_a"] = {
        "symbol": "NEW",
        "mint": "new-token-mint",
        "decimals": 9,
    }
    candidate["trading_pair"] = "NEW-USDC"
    config = _prep_config(candidate=candidate)
    gateway = Gateway(
        tokens=[
            {
                "address": "different-token-mint",
                "symbol": "NEW",
                "decimals": 9,
            },
            {
                "address": orca.USDC_MINT,
                "symbol": "USDC",
                "decimals": 6,
            },
        ]
    )
    _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway=gateway,
        balances=[
            {"symbol": "SOL", "mint": SOL_MINT, "available": "2"},
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "10",
            },
        ],
    )

    result = _run(config)

    assert result["status"] == "rejected"
    assert result["mutation"] is False
    assert "symbol NEW is ambiguous" in result["reason"]
    assert gateway.add_calls == []


def test_unverified_selected_token_registration_requires_manual_review(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    candidate["token_a"] = {
        "symbol": "NEW",
        "mint": "new-token-mint",
        "decimals": 9,
    }
    candidate["trading_pair"] = "NEW-USDC"
    config = _prep_config(candidate=candidate)
    gateway = Gateway(
        tokens=[
            {
                "address": orca.USDC_MINT,
                "symbol": "USDC",
                "decimals": 6,
            }
        ],
        add_error=TimeoutError("registration timed out"),
    )
    _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway=gateway,
        balances=[
            {"symbol": "SOL", "mint": SOL_MINT, "available": "2"},
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "10",
            },
        ],
    )

    result = _run(config)

    assert result["status"] == "manual_review"
    assert result["mutation"] is True
    assert result["token_registration"]["status"] == "uncertain"
    assert result["retry_allowed"] is False
    assert Store.latest.writes[-1]["phase"] == "manual_review"


def test_partial_existing_sol_reduces_only_the_preparation_shortfall(
    monkeypatch, tmp_path
):
    config = _prep_config()
    policy = strategy_config(use_existing_base_inventory=True)
    initial = planner.build_candidate_plan(
        config.candidate,
        amount_quote=config.amount_quote,
        range_half_width_pct=config.range_half_width_pct,
        strategy_config=policy,
    )
    adjusted = planner.apply_existing_base_inventory(
        initial,
        Decimal("0.005"),
    )
    shortfall = adjusted["inventory"]["base_shortfall"]
    quote_input = adjusted["inventory"]["estimated_usdc_for_preparation_swap"]
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        strategy=policy,
        gateway_swap=GatewaySwap(
            quote_in=str(quote_input),
            quote_out=str(shortfall),
        ),
        balances=[
            {"symbol": "SOL", "mint": SOL_MINT, "available": "0.105"},
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "10",
            },
        ],
    )

    result = _run(config)

    assert result["status"] == "ready"
    assert (
        str(
            Store.latest.report_payload["selection_plan"]["inputs"][
                "attributed_base_amount"
            ]
        )
        == "0.005"
    )
    assert result["executor_request"]["executor_config"]["amount"] == str(shortfall)
    assert Decimal(str(shortfall)) < Decimal(
        str(initial["inventory"]["base_shortfall"])
    )
    assert client.gateway_swap.quote_calls


def test_missing_selected_token_balance_is_zero_and_preparation_can_continue(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    candidate["token_a"] = {
        "symbol": "ZEC",
        "mint": "zec-token-mint",
        "decimals": 8,
    }
    candidate["trading_pair"] = "ZEC-USDC"
    config = _prep_config(candidate=candidate)
    policy = strategy_config(use_existing_base_inventory=True)
    plan = planner.build_candidate_plan(
        candidate,
        amount_quote=config.amount_quote,
        range_half_width_pct=config.range_half_width_pct,
        strategy_config=policy,
    )
    gateway = Gateway(
        tokens=[
            {
                "address": "zec-token-mint",
                "symbol": "ZEC",
                "decimals": 8,
            },
            {
                "address": orca.USDC_MINT,
                "symbol": "USDC",
                "decimals": 6,
            },
        ]
    )
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway=gateway,
        gateway_swap=GatewaySwap(
            quote_in=plan["inventory"]["estimated_usdc_for_preparation_swap"],
            quote_out=plan["inventory"]["base_shortfall"],
        ),
        strategy=policy,
        balances=[
            {"token": "SOL", "available_units": "0.58"},
            {"token": "USDC", "available_units": "10"},
        ],
    )

    result = _run(config)

    assert result["status"] == "ready", result
    assert result["mutation"] is False
    assert result["executor_request"]["executor_config"]["trading_pair"] == ("ZEC-USDC")
    assert (
        Store.latest.report_payload["selection_plan"]["inputs"][
            "attributed_base_amount"
        ]
        == "0"
    )
    assert client.gateway_swap.quote_calls


def test_selected_token_balance_with_wrong_mint_remains_rejected():
    with pytest.raises(ValueError, match="conflicts with the selected mint"):
        lp_order_request._balance(
            [
                {
                    "symbol": "ZEC",
                    "mint": "different-zec-mint",
                    "available": "1",
                }
            ],
            symbol="ZEC",
            mint="selected-zec-mint",
            missing_exact_is_zero=True,
        )


def test_preparation_requires_swap_input_plus_future_lp_quote_balance(
    monkeypatch, tmp_path
):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        strategy=strategy_config(use_existing_base_inventory=False),
        balances=[
            {"symbol": "SOL", "mint": SOL_MINT, "available": "0.562109782"},
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "2.917004",
            },
        ],
    )

    result = _run(config)

    assert result["status"] == "rejected"
    assert result["mutation"] is False
    assert "preparation input and LP quote leg" in result["reason"]
    assert "required=" in result["reason"]
    assert "available=2.917004" in result["reason"]
    assert Store.latest.preparation_admissions == []
    assert client.gateway_swap.execute_calls == []


def test_following_tick_reconciles_from_compact_ids_and_persisted_intent(
    monkeypatch, tmp_path
):
    config = _prep_config()
    client = _install(monkeypatch, tmp_path, config=config)
    ready = _run(config)
    Store.next_existing = copy.deepcopy(Store.latest.existing)
    client.executors.details["native-order-1"] = _order_detail(
        ready["executor_request"],
        executor_id="native-order-1",
    )

    result = _run(
        lp_order_request.Config(
            controller_id=config.controller_id,
            operation_id=config.operation_id,
            swap_executor_id="native-order-1",
        )
    )

    assert result["status"] == "confirmed"
    assert result["swap_executor_id"] == "native-order-1"
    assert result["deployment_input"] == {
        "candidate": lp_order_request._candidate_capsule(config.candidate),
        "amount_quote": "4",
        "range_half_width_pct": "10",
        "preparation_operation_id": config.operation_id,
    }
    assert result["same_tick_lp_create_allowed"] is True


def test_legacy_compact_recovery_resolves_block_without_report_lookup(
    monkeypatch, tmp_path
):
    config = _prep_config()
    client = _install(monkeypatch, tmp_path, config=config)
    ready = _run(config)
    legacy = copy.deepcopy(Store.latest.existing)
    legacy["intent"].pop("candidate")
    Store.next_existing = legacy
    client.executors.details["legacy-native-order"] = _order_detail(
        ready["executor_request"],
        executor_id="legacy-native-order",
    )

    result = _run(
        lp_order_request.Config(
            controller_id=config.controller_id,
            operation_id=config.operation_id,
            swap_executor_id="legacy-native-order",
        )
    )

    assert result["status"] == "confirmed"
    assert result["swap_executor_id"] == "legacy-native-order"
    assert "deployment_input" not in result


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

    assert result["status"] == "ready"
    assert (
        Store.latest.report_payload["selection_plan"]["inventory"][
            "max_usdc_for_preparation_swap"
        ]
        == "2.075272"
    )
    assert client.gateway_swap.quote_calls[0]["slippage_pct"] == 1


def test_swap_accepts_executor_slippage_below_strategy_maximum(monkeypatch, tmp_path):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        strategy=strategy_config(
            max_slippage_pct=3,
            use_existing_base_inventory=False,
        ),
        gateway=Gateway(slippage="1"),
    )
    captured = {}

    async def attach(payload, **kwargs):
        captured["trace"] = kwargs["trace"]
        return {**payload, "report_id": "swap-report", "report_error": None}

    monkeypatch.setattr(lp_order_request.reporting, "attach_report", attach)

    result = _run(config)

    assert result["status"] == "ready"
    preflight = next(
        event
        for event in captured["trace"].events
        if event["stage"] == "native_order_executor_preflight"
    )
    assert preflight["facts"]["executor_slippage_pct"] == Decimal("1")
    assert preflight["facts"]["max_slippage_pct"] == Decimal("3")
    assert client.gateway_swap.execute_calls == []


def test_swap_rejects_when_executor_slippage_exceeds_strategy_maximum(
    monkeypatch, tmp_path
):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway=Gateway(slippage="2"),
    )

    result = _run(config)

    assert result["status"] == "rejected"
    assert "slippage exceeds" in result["reason"]
    assert result["mutation"] is False
    assert client.gateway_swap.execute_calls == []


def test_swap_rejects_when_network_default_is_not_jupiter(monkeypatch, tmp_path):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway=Gateway(swap_provider="dflow/router"),
    )

    result = _run(config)

    assert result["status"] == "rejected"
    assert "default swap provider conflicts" in result["reason"]
    assert result["mutation"] is False
    assert client.gateway_swap.execute_calls == []


def test_swap_accepts_large_base_amount_when_quote_exposure_is_bounded(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    candidate["price"] = "0.001"
    config = _prep_config(candidate=candidate)
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="2.04", quote_out="2030"),
    )

    result = _run(config)

    assert result["status"] == "ready"
    assert result["mutation"] is False
    assert Decimal(result["executor_request"]["executor_config"]["amount"]) > 10
    assert Store.latest.report_payload["quote"]["input_amount"] == "2.04"
    assert client.gateway_swap.execute_calls == []


def test_swap_reports_when_same_tick_lp_create_needs_another_tick(
    monkeypatch, tmp_path
):
    rows = []
    for index in (1, 2):
        row = executor_row(f"existing-{index}", pool_address=f"another-pool-{index}")
        row["config"].update(
            {
                "trading_pair": f"OTHER{index}-USDC",
                "base_mint": f"other-mint-{index}",
                "base_symbol": f"OTHER{index}",
            }
        )
        rows.append(row)
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        executors=Executors(rows=rows),
    )

    result = _run(config)

    assert result["status"] == "ready"
    assert result["same_tick_lp_create_allowed"] is False
    assert result["executor_request"]["executor_type"] == "order_executor"
    assert client.gateway_swap.execute_calls == []


def test_admitted_swap_request_is_not_reemitted_without_exact_executor_id(
    monkeypatch, tmp_path
):
    config = _prep_config()
    client = _install(monkeypatch, tmp_path, config=config)
    ready = _run(config)
    Store.next_existing = copy.deepcopy(Store.latest.existing)

    replay = _run(config)

    assert replay["status"] == "admitted"
    assert replay["retry_allowed"] is False
    assert replay["executor_request"] == ready["executor_request"]
    assert len(client.gateway_swap.quote_calls) == 1
    assert client.gateway_swap.execute_calls == []


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


def test_uncertain_operation_without_native_executor_id_never_guesses_from_history(
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

    assert result["status"] == "manual_review"
    assert "Gateway-history guessing is prohibited" in result["reason"]
    assert result["retry_allowed"] is False
    assert client.gateway_swap.execute_calls == []
    assert client.gateway_swap.history_calls == []


def test_multiple_legacy_history_matches_are_not_used_for_recovery(
    monkeypatch, tmp_path
):
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
    assert "Gateway-history guessing is prohibited" in result["reason"]
    assert client.gateway_swap.history_calls == []
    assert client.gateway_swap.execute_calls == []


def test_order_executor_detail_unavailable_is_submitted_and_not_retried(
    monkeypatch, tmp_path
):
    config = _prep_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
    )

    ready = _run(config)
    Store.next_existing = copy.deepcopy(Store.latest.existing)
    client.executors.detail_error = TimeoutError("outcome unavailable")
    result = _run(
        config.model_copy(update={"swap_executor_id": "swap-order-unavailable"})
    )

    assert result["status"] == "submitted"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert client.gateway_swap.execute_calls == []
    assert Store.latest.writes[-1]["phase"] == "submitted"


def test_late_order_confirmation_allows_second_tick_lp_create(monkeypatch, tmp_path):
    config = _prep_config()
    client = _install(monkeypatch, tmp_path, config=config)
    ready = _run(config)
    Store.latest.existing["tick"] = 1

    result = _reconcile_ready(client, config, ready)

    assert result["status"] == "confirmed"
    assert result["same_tick_lp_create_allowed"] is True
    assert Store.latest.existing["result"]["confirmed_tick"] == 2


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
                "result": {
                    "receipt": {
                        "transaction_hash": "tx-preparation-1",
                        "output_amount": "0.01",
                    }
                },
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
                "result": {
                    "receipt": {
                        "transaction_hash": "tx-preparation-1",
                        "output_amount": "0.01",
                    }
                },
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

    assert result["status"] == "ready"
    assert client.gateway_swap.execute_calls == []


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

    ready = _run(config)
    result = _reconcile_ready(client, config, ready)

    assert result["status"] == "confirmed"
    assert result["capacity_release_allowed"] is True
    assert result["attribution"]["executor_id"] == "executor-closed"
    assert result["attribution"]["position_id"] == "position-closed"
    assert result["attribution"]["residual_base_amount"] == "0.01"
    assert client.gateway_swap.execute_calls == []
    assert client.executors.detail_calls == [
        "executor-closed",
        "swap-order",
    ]


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

    ready = _run(config)
    result = _reconcile_ready(client, config, ready)

    assert result["status"] == "confirmed"
    assert result["attribution"]["native_close_status"] == "CONFIRMED"
    assert result["attribution"]["residual_base_amount"] == "0.01"
    assert result["capacity_release_allowed"] is True
    assert client.gateway_swap.execute_calls == []
    assert ready["executor_request"]["executor_config"]["side"] == 2
    assert ready["executor_request"]["executor_config"]["amount"] == str(config.amount)


def test_cleanup_report_links_stop_attribution_trace_and_release_evidence(
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
    captured = {}

    async def attach(payload, **kwargs):
        captured.update(kwargs)
        return {**payload, "report_id": "cleanup-report", "report_error": None}

    monkeypatch.setattr(lp_order_request.reporting, "attach_report", attach)

    ready = _run(config)
    result = _reconcile_ready(client, config, ready)

    assert result["report_id"] == "cleanup-report"
    assert result["capacity_release_allowed"] is True
    assert result["post_transaction_refresh"]["transaction_hash"] == "tx-swap"
    assert captured["routine_input"]["closed_tick"] == 1
    assert captured["links"]["executor_id"] == "executor-closed"
    assert captured["links"]["transaction_hash"] == "tx-swap"
    stages = [event["stage"] for event in captured["trace"].events]
    assert "native_order_executor_reconciliation" in stages
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


def test_cleanup_pending_order_executor_never_releases_capacity(monkeypatch, tmp_path):
    config = _cleanup_config()
    client = _install(
        monkeypatch,
        tmp_path,
        config=config,
        gateway_swap=GatewaySwap(quote_in="0.01", quote_out="1.8"),
        executors=Executors(detail=_cleanup_detail()),
    )

    ready = _run(config)
    Store.next_existing = copy.deepcopy(Store.latest.existing)
    client.executors.details["executor-closed"] = _cleanup_detail()
    pending = _order_detail(ready["executor_request"], executor_id="swap-pending")
    pending["status"] = "RUNNING"
    pending["is_active"] = True
    pending["custom_info"]["held_position_orders"] = []
    client.executors.details["swap-pending"] = pending
    result = _run(config.model_copy(update={"swap_executor_id": "swap-pending"}))

    assert result["status"] == "submitted"
    assert result["retry_allowed"] is False
    assert result["capacity_release_allowed"] is False
    assert client.gateway_swap.execute_calls == []


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
