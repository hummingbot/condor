from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from conftest import (
    POOL,
    SOL_MINT,
    executor_row,
    pool_record,
    runtime_scope,
    strategy_config,
)

from agents.lp_expert.core import orca, planner
from agents.lp_expert.routines import lp_create


def _candidate():
    value, error = orca.normalize_record(pool_record(), "all", "volume24h", 1)
    assert error is None
    unique, rejected = orca.deduplicate([value])
    assert not rejected
    return orca.rank_pools(unique)[0]


def _selection_plan(candidate):
    return planner.build_candidate_plan(
        candidate,
        amount_quote="4",
        range_half_width_pct="10",
        strategy_config={
            "min_quote_per_executor": "3",
            "max_quote_per_executor": "4",
            "minimum_range_half_width_pct": "0.5",
            "maximum_range_half_width_pct": "20",
            "rebalance_threshold_pct": "1",
            "max_slippage_pct": "1",
        },
    )


class Store:
    next_existing = None
    next_conflict = None

    def __init__(self, scope, *, receipt_intent=None):
        self.scope = scope
        self.records = {}
        if type(self).next_existing is not None:
            self.records["create-operation-1"] = _complete_existing_record(
                type(self).next_existing
            )
        self.conflict = copy.deepcopy(type(self).next_conflict)
        self.unresolved = []
        self.writes = []
        self.admissions = []
        self.receipt_intent = receipt_intent

    def read_confirmed_swap(self, operation_id):
        plan = _selection_plan(_candidate())
        return {
            "operation_id": operation_id,
            "tick": self.scope.current_tick,
            "phase": "confirmed",
            "intent": self.receipt_intent
            or {
                "reason": "inventory_preparation",
                "side": "BUY",
                "trading_pair": "SOL-USDC",
                "pool_address": POOL,
                "base_mint": SOL_MINT,
                "plan_digest": plan["plan_digest"],
                "amount_quote": "4",
                "range_half_width_pct": "10",
            },
            "result": {
                "confirmed_tick": self.scope.current_tick,
                "same_tick_lp_create_allowed": True,
                "receipt": {
                    "transaction_hash": "tx-preparation",
                    "input_amount": plan["inventory"][
                        "estimated_usdc_for_preparation_swap"
                    ],
                    "output_amount": plan["inventory"]["base_amount"],
                },
            },
        }

    def read_confirmed_preparation(self, operation_id):
        return self.read_confirmed_swap(operation_id)

    def identity(self, *, operation_id, operation_kind, intent, tick=None):
        return SimpleNamespace(
            operation_id=operation_id,
            operation_kind=operation_kind,
            intent=intent,
            tick=self.scope.current_tick if tick is None else tick,
        )

    def read(self, identity):
        if self.conflict is not None:
            raise ValueError("operation receipt identity or content is invalid")
        return self.records.get(identity.operation_id)

    def read_by_id(self, operation_id):
        if self.conflict is not None:
            assert operation_id == "create-operation-1"
            return copy.deepcopy(self.conflict)
        return copy.deepcopy(self.records.get(operation_id))

    def unresolved_operations(self):
        return copy.deepcopy(self.unresolved)

    def list_records(self, operation_kind=None):
        values = list(self.records.values())
        if operation_kind is None:
            return copy.deepcopy(values)
        return [
            copy.deepcopy(value)
            for value in values
            if value.get("operation_kind", "create") == operation_kind
        ]

    def admit_create(self, identity):
        self.admissions.append(identity.operation_id)
        return {}

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
        value = {
            "operation_id": identity.operation_id,
            "operation_kind": identity.operation_kind,
            "tick": identity.tick,
            "intent": copy.deepcopy(identity.intent),
            "phase": phase,
            "mutation_possible": mutation_possible,
            "result": result,
            "reason": reason,
        }
        self.records[identity.operation_id] = value
        self.writes.append(copy.deepcopy(value))
        return value


class Executors:
    def __init__(
        self,
        *,
        rows=None,
        schema_missing=None,
        create_error=None,
        create_response=None,
        recovery_rows=None,
        detail_error=None,
    ):
        self.rows = copy.deepcopy(rows or [])
        self.schema_missing = schema_missing
        self.create_error = create_error
        self.create_response = create_response or {"executor_id": "executor-new"}
        self.recovery_rows = copy.deepcopy(recovery_rows)
        self.detail_error = detail_error
        self.search_count = 0
        self.create_calls = []
        self.created_config = None

    async def get_executor_config_schema(self, executor_type):
        assert executor_type == "lp_executor"
        fields = {
            "controller_id",
            "connector_name",
            "lp_provider",
            "trading_pair",
            "pool_address",
            "lower_price",
            "upper_price",
            "lower_limit_price",
            "upper_limit_price",
            "side",
            "base_amount",
            "quote_amount",
            "swap_provider",
            "keep_position",
        }
        if self.schema_missing:
            fields.remove(self.schema_missing)
        return {"properties": {field: {} for field in fields}}

    async def search_executors(self, **_):
        self.search_count += 1
        if self.search_count > 1 and self.recovery_rows is not None:
            return {"data": copy.deepcopy(self.recovery_rows)}
        return {"data": copy.deepcopy(self.rows)}

    async def create_executor(self, **kwargs):
        self.create_calls.append(copy.deepcopy(kwargs))
        self.created_config = copy.deepcopy(kwargs["executor_config"])
        if self.create_error:
            raise self.create_error
        return copy.deepcopy(self.create_response)

    async def get_executor(self, executor_id):
        assert executor_id == "executor-new"
        if self.detail_error:
            raise self.detail_error
        config = copy.deepcopy(self.created_config)
        return {
            "executor_id": executor_id,
            "account_name": "master_account",
            "controller_id": config["controller_id"],
            "status": "RUNNING",
            "is_active": True,
            "timestamp": 2_000,
            "net_pnl_pct": "0",
            "net_pnl_quote": "0",
            "config": {"type": "lp_executor", **config},
            "custom_info": {"state": "IN_RANGE"},
        }


class Gateway:
    async def get_network_tokens(self, network):
        assert network == "solana-mainnet-beta"
        return {"tokens": [{"address": SOL_MINT, "symbol": "SOL", "decimals": 9}]}


class Portfolio:
    def __init__(self, balances=None):
        self.balances = copy.deepcopy(
            balances
            or [
                {
                    "symbol": "SOL",
                    "mint": SOL_MINT,
                    "available": "2",
                },
                {
                    "symbol": "USDC",
                    "mint": orca.USDC_MINT,
                    "available": "20",
                },
            ]
        )

    async def get_state(self, **_):
        return {"master_account": {"solana-mainnet-beta": copy.deepcopy(self.balances)}}


def _config(candidate):
    return lp_create.Config(
        controller_id="lp_expert.orca_1",
        tick=2,
        operation_id="create-operation-1",
        candidate=candidate,
        amount_quote="4",
        range_half_width_pct="10",
        preparation_operation_id="prepare-operation-1",
    )


@pytest.mark.parametrize("field", ["operation_id", "preparation_operation_id"])
def test_create_rejects_dotted_controller_operation_ids_at_input(field):
    values = _config(_candidate()).model_dump()
    values[field] = "lp_expert.orca_17-t1-pool-create"

    with pytest.raises(ValueError, match="String should match pattern"):
        lp_create.Config(**values)


def test_create_accepts_dynamic_receipt_safe_operation_ids():
    values = _config(_candidate()).model_dump()
    values.update(
        {
            "operation_id": f"lp_expert_orca_17-t1-{POOL}-create",
            "preparation_operation_id": (f"lp_expert_orca_17-t1-{POOL}-prepare"),
        }
    )

    config = lp_create.Config(**values)

    assert config.operation_id.endswith("-create")
    assert config.preparation_operation_id.endswith("-prepare")


def _expected_executor_config(candidate):
    snapshot = _selection_plan(candidate)
    final = planner.build_candidate_plan(
        candidate,
        amount_quote="4",
        range_half_width_pct="10",
        strategy_config={
            "min_quote_per_executor": "3",
            "max_quote_per_executor": "4",
            "minimum_range_half_width_pct": "0.5",
            "maximum_range_half_width_pct": "20",
            "rebalance_threshold_pct": "1",
            "max_slippage_pct": "1",
        },
        attributed_base_amount=snapshot["inventory"]["base_amount"],
    )
    return {**final["executor_config"], "controller_id": "lp_expert.orca_1"}


def _complete_existing_record(record):
    candidate = _candidate()
    selection = _selection_plan(candidate)
    executor_config = _expected_executor_config(candidate)
    final = planner.build_candidate_plan(
        candidate,
        amount_quote="4",
        range_half_width_pct="10",
        strategy_config={
            "min_quote_per_executor": "3",
            "max_quote_per_executor": "4",
            "minimum_range_half_width_pct": "0.5",
            "maximum_range_half_width_pct": "20",
            "rebalance_threshold_pct": "1",
            "max_slippage_pct": "1",
        },
        attributed_base_amount=selection["inventory"]["base_amount"],
    )
    supplied_result = record.get("result")
    result = {
        "executor_request": {
            "action": "create",
            "executor_type": "lp_executor",
            "account_name": "master_account",
            "controller_id": "lp_expert.orca_1",
            "executor_config": executor_config,
        },
        "selection_plan": selection,
        "final_plan": final,
        **(copy.deepcopy(supplied_result) if isinstance(supplied_result, dict) else {}),
    }
    return {
        "operation_id": "create-operation-1",
        "operation_kind": "create",
        "tick": 2,
        "intent": {
            "pool_address": executor_config["pool_address"],
            "trading_pair": executor_config["trading_pair"],
            "selection_plan_digest": selection["plan_digest"],
            "final_plan_digest": final["plan_digest"],
            "preparation_operation_id": "prepare-operation-1",
            "amount_quote": "4",
            "range_half_width_pct": "10",
        },
        **copy.deepcopy(record),
        "result": result,
    }


def _install(
    monkeypatch,
    tmp_path,
    *,
    executors=None,
    receipt_intent=None,
    refresh_error=None,
    existing=None,
    conflict=None,
    config=None,
    balances=None,
):
    scope = runtime_scope(tmp_path, tick=2, config=config)
    client = SimpleNamespace(
        executors=executors or Executors(),
        gateway=Gateway(),
        portfolio=Portfolio(balances),
    )
    Store.next_existing = copy.deepcopy(existing)
    Store.next_conflict = copy.deepcopy(conflict)
    store = Store(scope, receipt_intent=receipt_intent)
    monkeypatch.setattr(lp_create, "resolve_runtime", lambda _: scope)

    async def get_client(_):
        return client

    async def bind(value, _):
        return value

    async def refresh(candidate):
        if refresh_error:
            raise refresh_error
        return copy.deepcopy(candidate)

    async def attach(payload, **_):
        return {**payload, "report_id": "create-report", "report_error": None}

    monkeypatch.setattr(lp_create, "get_hummingbot_client", get_client)
    monkeypatch.setattr(lp_create, "bind_wallet", bind)
    monkeypatch.setattr(lp_create, "ReceiptStore", lambda _: store)
    monkeypatch.setattr(lp_create.orca, "refresh_candidate", refresh)
    monkeypatch.setattr(lp_create, "attach_report", attach)
    return client, store


def _run(config):
    return json.loads(asyncio.run(lp_create.run(config, None)))


def test_create_refreshes_candidate_validates_receipt_and_confirms(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    client, store = _install(monkeypatch, tmp_path)

    ready = _run(_config(candidate))

    assert ready["status"] == "ready"
    assert ready["mutation"] is False
    assert ready["executor_request"]["executor_type"] == "lp_executor"
    assert ready["executor_request"]["controller_id"] == "lp_expert.orca_1"
    assert (
        ready["executor_request"]["executor_config"]["controller_id"]
        == "lp_expert.orca_1"
    )
    assert client.executors.create_calls == []
    assert store.admissions == ["create-operation-1"]
    assert [row["phase"] for row in store.writes] == ["admitted"]
    client.executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )

    result = _run(
        _config(candidate).model_copy(update={"lp_executor_id": "executor-new"})
    )

    assert result["status"] == "confirmed"
    assert result["executor_id"] == "executor-new"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert client.executors.create_calls == []
    assert [row["phase"] for row in store.writes] == [
        "admitted",
        "submitting",
        "submitted",
        "confirmed",
    ]
    assert result["final_plan"]["inventory"]["inventory_ready"] is True
    assert result["report_id"] == "create-report"


def test_create_id_only_recovery_uses_frozen_request_without_market_refresh(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    executors = Executors()
    client, store = _install(monkeypatch, tmp_path, executors=executors)
    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )

    async def changed_market_must_not_be_read(_):
        raise AssertionError("existing create recovery refreshed the market")

    monkeypatch.setattr(
        lp_create.orca,
        "refresh_candidate",
        changed_market_must_not_be_read,
    )
    recovery = lp_create.Config(
        controller_id="lp_expert.orca_1",
        operation_id="create-operation-1",
        lp_executor_id="executor-new",
    )

    result = _run(recovery)

    assert result["status"] == "confirmed"
    assert result["executor_id"] == "executor-new"
    assert result["recovery_source"] == "exact_frozen_executor_request"
    assert result["final_plan"] == ready["final_plan"]
    assert [row["phase"] for row in store.writes] == [
        "admitted",
        "submitting",
        "submitted",
        "confirmed",
    ]


def test_create_id_only_recovery_without_receipt_is_manual_review(
    monkeypatch, tmp_path
):
    client, store = _install(monkeypatch, tmp_path)

    result = _run(
        lp_create.Config(
            controller_id="lp_expert.orca_1",
            operation_id="missing-create-operation",
            lp_executor_id="executor-new",
        )
    )

    assert result["status"] == "manual_review"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert client.executors.create_calls == []
    assert store.writes == []


def test_admitted_lp_create_is_not_reemitted_without_exact_executor_id(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    client, store = _install(monkeypatch, tmp_path)
    ready = _run(_config(candidate))

    replay = _run(_config(candidate))

    assert replay["status"] == "admitted"
    assert replay["retry_allowed"] is False
    assert replay["executor_request"] == ready["executor_request"]
    assert client.executors.create_calls == []
    assert [row["phase"] for row in store.writes] == ["admitted"]


def test_create_waits_for_native_risk_count_refresh_when_required(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    client, store = _install(monkeypatch, tmp_path)
    original = store.read_confirmed_preparation

    def preparation(operation_id):
        record = original(operation_id)
        record["result"]["same_tick_lp_create_allowed"] = False
        return record

    store.read_confirmed_preparation = preparation

    result = _run(_config(candidate))

    assert result["status"] == "rejected_before_submit"
    assert "requires LP create on a later tick" in result["reason"]
    assert result["mutation"] is False
    assert client.executors.create_calls == []
    assert store.admissions == []


def test_create_can_continue_after_following_tick_swap_confirmation(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    client, store = _install(monkeypatch, tmp_path)
    original = store.read_confirmed_preparation

    def preparation(operation_id):
        record = original(operation_id)
        record["tick"] = 1
        record["result"]["confirmed_tick"] = 2
        record["result"]["same_tick_lp_create_allowed"] = False
        return record

    store.read_confirmed_preparation = preparation

    result = _run(_config(candidate))

    assert result["status"] == "ready"
    assert result["mutation"] is False
    assert result["executor_request"]["executor_type"] == "lp_executor"
    assert store.admissions == ["create-operation-1"]
    assert client.executors.create_calls == []


def test_create_accepts_confirmed_existing_wallet_inventory_allocation(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    client, store = _install(monkeypatch, tmp_path)
    base_plan = _selection_plan(candidate)
    existing_base = base_plan["inventory"]["base_amount"]
    selection = planner.apply_existing_base_inventory(
        base_plan,
        existing_base,
    )

    def preparation(operation_id):
        assert operation_id == "prepare-operation-1"
        return {
            "operation_id": operation_id,
            "tick": 2,
            "phase": "confirmed",
            "intent": {
                "reason": "inventory_preparation",
                "side": "BUY",
                "trading_pair": "SOL-USDC",
                "pool_address": POOL,
                "base_mint": SOL_MINT,
                "amount": "0",
                "attributed_base_amount": str(existing_base),
                "plan_digest": selection["plan_digest"],
                "amount_quote": "4",
                "range_half_width_pct": "10",
            },
            "result": {
                "confirmed_tick": 2,
                "same_tick_lp_create_allowed": True,
                "inventory_allocation": {
                    "source": "existing_wallet_balance",
                    "attributed_base_amount": str(existing_base),
                },
            },
        }

    store.read_confirmed_preparation = preparation

    result = _run(_config(candidate))

    assert result["status"] == "ready"
    assert result["mutation"] is False
    assert result["executor_request"]["executor_type"] == "lp_executor"
    assert result["final_plan"]["inventory"]["inventory_ready"] is True
    assert store.admissions == ["create-operation-1"]
    assert client.executors.create_calls == []


def test_create_returns_field_specific_required_and_available_balances(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    client, store = _install(
        monkeypatch,
        tmp_path,
        balances=[
            {"symbol": "SOL", "mint": SOL_MINT, "available": "0.1"},
            {
                "symbol": "USDC",
                "mint": orca.USDC_MINT,
                "available": "0.5",
            },
        ],
    )

    result = _run(_config(candidate))

    assert result["status"] == "rejected_before_submit"
    assert "SOL balance for LP base and reserve" in result["reason"]
    assert "USDC balance for LP quote leg" in result["reason"]
    assert "required=" in result["reason"]
    assert "available=0.5" in result["reason"]
    assert store.admissions == []
    assert client.executors.create_calls == []


def test_create_rejects_receipt_attributed_to_another_pool(monkeypatch, tmp_path):
    candidate = _candidate()
    client, _ = _install(
        monkeypatch,
        tmp_path,
        receipt_intent={
            "reason": "inventory_preparation",
            "side": "BUY",
            "trading_pair": "OTHER-USDC",
            "base_mint": "other-mint",
        },
    )

    result = _run(_config(candidate))

    assert result["status"] == "rejected_before_submit"
    assert "not attributed to this selection" in result["reason"]
    assert result["mutation"] is False
    assert client.executors.create_calls == []


def test_create_refresh_failure_is_pre_submit_rejection(monkeypatch, tmp_path):
    candidate = _candidate()
    client, _ = _install(
        monkeypatch,
        tmp_path,
        refresh_error=ValueError("pool identity changed"),
    )

    result = _run(_config(candidate))

    assert result["status"] == "rejected_before_submit"
    assert "pool identity changed" in result["reason"]
    assert client.executors.create_calls == []


def test_create_schema_failure_is_fail_closed(monkeypatch, tmp_path):
    candidate = _candidate()
    client, _ = _install(
        monkeypatch,
        tmp_path,
        executors=Executors(schema_missing="pool_address"),
    )

    result = _run(_config(candidate))

    assert result["status"] == "rejected_before_submit"
    assert "schema lacks fields" in result["reason"]
    assert client.executors.create_calls == []


def test_create_rejects_full_three_executor_capacity(monkeypatch, tmp_path):
    candidate = _candidate()
    rows = [
        executor_row(f"executor-{index}", pool_address=f"pool-{index}")
        for index in range(1, 4)
    ]
    client, _ = _install(
        monkeypatch,
        tmp_path,
        executors=Executors(rows=rows),
    )

    result = _run(_config(candidate))

    assert result["status"] == "rejected_before_submit"
    assert "capacity is full" in result["reason"]
    assert client.executors.create_calls == []


def test_create_capacity_is_not_hardcoded_to_three(monkeypatch, tmp_path):
    rows = [
        executor_row(f"executor-{index}", pool_address=f"pool-{index}")
        for index in range(1, 4)
    ]
    configured = strategy_config(
        total_amount_quote=30,
        max_open_executors=5,
        max_slot_deployments_per_tick=2,
        candidate_scan_limit=5,
        risk_limits={
            "max_position_size_quote": 30,
            "max_open_executors": 5,
            "max_drawdown_pct": -1,
            "shutdown_drawdown_pct": -1,
        },
    )
    client, _ = _install(
        monkeypatch,
        tmp_path,
        executors=Executors(rows=rows),
        config=configured,
    )

    result = _run(_config(_candidate()))

    assert result["status"] == "ready"
    assert client.executors.create_calls == []


def test_create_rejects_aggregate_capital_before_capacity_is_full(
    monkeypatch, tmp_path
):
    rows = [
        executor_row("executor-one", pool_address="pool-one"),
        executor_row("executor-two", pool_address="pool-two"),
    ]
    for row in rows:
        row["config"]["base_amount"] = "0.02"
        row["config"]["quote_amount"] = "3"
    client, _ = _install(
        monkeypatch,
        tmp_path,
        executors=Executors(rows=rows),
    )

    result = _run(_config(_candidate()))

    assert result["status"] == "rejected_before_submit"
    assert "aggregate LP capital" in result["reason"]
    assert client.executors.create_calls == []


def test_create_rejects_foreign_live_executor_ownership(monkeypatch, tmp_path):
    client, _ = _install(
        monkeypatch,
        tmp_path,
        executors=Executors(
            rows=[
                executor_row(
                    "foreign-executor",
                    controller_id="foreign.agent_1",
                    pool_address="foreign-pool",
                )
            ]
        ),
    )

    result = _run(_config(_candidate()))

    assert result["status"] == "rejected_before_submit"
    assert "foreign live executor" in result["reason"]
    assert client.executors.create_calls == []


def test_create_rejects_same_pool_even_with_capacity(monkeypatch, tmp_path):
    candidate = _candidate()
    client, _ = _install(
        monkeypatch,
        tmp_path,
        executors=Executors(rows=[executor_row(pool_address=POOL)]),
    )

    result = _run(_config(candidate))

    assert result["status"] == "rejected_before_submit"
    assert "pool already has an active executor" in result["reason"]
    assert client.executors.create_calls == []


def test_create_detail_unavailable_is_submitted_and_never_retried(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    executors = Executors(detail_error=TimeoutError("outcome unavailable"))
    client, store = _install(
        monkeypatch,
        tmp_path,
        executors=executors,
    )

    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )
    result = _run(
        _config(candidate).model_copy(update={"lp_executor_id": "executor-new"})
    )

    assert result["status"] == "submitted"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert client.executors.create_calls == []
    assert store.writes[-1]["phase"] == "submitted"


def test_create_initializing_detail_is_submitted_then_confirms_same_id(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    executors = Executors()
    client, store = _install(monkeypatch, tmp_path, executors=executors)
    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )
    complete_detail = executors.get_executor

    async def initializing_detail(executor_id):
        detail = await complete_detail(executor_id)
        detail["config"].pop("upper_price")
        return detail

    executors.get_executor = initializing_detail
    recovery = lp_create.Config(
        controller_id="lp_expert.orca_1",
        operation_id="create-operation-1",
        lp_executor_id="executor-new",
    )

    pending = _run(recovery)

    assert pending["status"] == "submitted"
    assert pending["retry_allowed"] is False
    assert pending["executor_match"]["outcome"] == "pending"
    assert pending["executor_match"]["missing_fields"] == ["config.upper_price"]
    assert store.writes[-1]["phase"] == "submitted"
    assert client.executors.create_calls == []

    executors.get_executor = complete_detail
    confirmed = _run(recovery)

    assert confirmed["status"] == "confirmed"
    assert confirmed["executor_id"] == "executor-new"
    assert confirmed["executor_match"]["outcome"] == "match"
    assert store.writes[-1]["phase"] == "confirmed"
    assert client.executors.create_calls == []


def test_create_does_not_require_initialized_lifecycle_telemetry(monkeypatch, tmp_path):
    candidate = _candidate()
    executors = Executors()
    client, store = _install(monkeypatch, tmp_path, executors=executors)
    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )
    complete_detail = executors.get_executor

    async def detail_without_telemetry(executor_id):
        detail = await complete_detail(executor_id)
        detail.pop("timestamp")
        detail["custom_info"] = {}
        return detail

    executors.get_executor = detail_without_telemetry

    result = _run(
        _config(candidate).model_copy(update={"lp_executor_id": "executor-new"})
    )

    assert result["status"] == "confirmed"
    assert result["executor_match"]["outcome"] == "match"
    assert result["executor_match"]["observed_lifecycle"] is None
    assert store.writes[-1]["phase"] == "confirmed"
    assert client.executors.create_calls == []


@pytest.mark.parametrize("native_side", ["RANGE", "TradeType.RANGE", "3.0"])
def test_create_matches_equivalent_native_numeric_representations(
    monkeypatch, tmp_path, native_side
):
    candidate = _candidate()
    executors = Executors()
    client, store = _install(monkeypatch, tmp_path, executors=executors)
    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )
    complete_detail = executors.get_executor

    async def normalized_detail(executor_id):
        detail = await complete_detail(executor_id)
        for field in (
            "base_amount",
            "lower_limit_price",
            "lower_price",
            "quote_amount",
            "upper_limit_price",
            "upper_price",
        ):
            detail["config"][field] = float(detail["config"][field])
        detail["config"]["side"] = native_side
        detail["config"]["keep_position"] = "false"
        return detail

    executors.get_executor = normalized_detail

    result = _run(
        _config(candidate).model_copy(update={"lp_executor_id": "executor-new"})
    )

    assert result["status"] == "confirmed"
    assert result["executor_match"]["outcome"] == "match"
    assert result["executor_match"]["mismatches"] == {}
    assert store.writes[-1]["phase"] == "confirmed"
    assert client.executors.create_calls == []


def test_create_different_lp_side_remains_manual_review(monkeypatch, tmp_path):
    candidate = _candidate()
    executors = Executors()
    client, store = _install(monkeypatch, tmp_path, executors=executors)
    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )
    complete_detail = executors.get_executor

    async def conflicting_detail(executor_id):
        detail = await complete_detail(executor_id)
        detail["config"]["side"] = "BUY"
        return detail

    executors.get_executor = conflicting_detail

    result = _run(
        _config(candidate).model_copy(update={"lp_executor_id": "executor-new"})
    )

    assert result["status"] == "manual_review"
    assert result["executor_match"]["outcome"] == "conflict"
    assert result["executor_match"]["mismatches"]["config.side"] == {
        "expected": 3,
        "observed": "BUY",
    }
    assert store.writes[-1]["phase"] == "manual_review"
    assert client.executors.create_calls == []


def test_create_numeric_difference_outside_tolerance_remains_manual_review(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    executors = Executors()
    client, store = _install(monkeypatch, tmp_path, executors=executors)
    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )
    complete_detail = executors.get_executor

    async def conflicting_detail(executor_id):
        detail = await complete_detail(executor_id)
        detail["config"]["lower_limit_price"] = (
            float(detail["config"]["lower_limit_price"]) + 0.000001
        )
        return detail

    executors.get_executor = conflicting_detail

    result = _run(
        _config(candidate).model_copy(update={"lp_executor_id": "executor-new"})
    )

    assert result["status"] == "manual_review"
    assert result["executor_match"]["outcome"] == "conflict"
    assert "config.lower_limit_price" in result["executor_match"]["mismatches"]
    assert store.writes[-1]["phase"] == "manual_review"
    assert client.executors.create_calls == []


def test_create_immutable_detail_conflict_remains_manual_review(monkeypatch, tmp_path):
    candidate = _candidate()
    executors = Executors()
    client, store = _install(monkeypatch, tmp_path, executors=executors)
    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )
    complete_detail = executors.get_executor

    async def conflicting_detail(executor_id):
        detail = await complete_detail(executor_id)
        detail["config"]["pool_address"] = "different-pool"
        return detail

    executors.get_executor = conflicting_detail

    result = _run(
        _config(candidate).model_copy(update={"lp_executor_id": "executor-new"})
    )

    assert result["status"] == "manual_review"
    assert result["retry_allowed"] is False
    assert result["executor_match"]["outcome"] == "conflict"
    assert result["executor_match"]["mismatches"]["config.pool_address"] == {
        "expected": POOL,
        "observed": "different-pool",
    }
    assert store.writes[-1]["phase"] == "manual_review"
    assert client.executors.create_calls == []


@pytest.mark.parametrize(
    ("field", "value", "mismatch"),
    [
        ("executor_id", "executor-other", "executor_id"),
        ("controller_id", "other.agent_1", "controller_id"),
        ("account_name", "other-account", "account_name"),
        ("executor_type", "order_executor", "executor_type"),
    ],
)
def test_create_identity_conflicts_remain_manual_review(
    monkeypatch, tmp_path, field, value, mismatch
):
    candidate = _candidate()
    executors = Executors()
    client, store = _install(monkeypatch, tmp_path, executors=executors)
    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )
    complete_detail = executors.get_executor

    async def conflicting_detail(executor_id):
        detail = await complete_detail(executor_id)
        detail[field] = value
        return detail

    executors.get_executor = conflicting_detail

    result = _run(
        _config(candidate).model_copy(update={"lp_executor_id": "executor-new"})
    )

    assert result["status"] == "manual_review"
    assert result["executor_match"]["outcome"] == "conflict"
    assert mismatch in result["executor_match"]["mismatches"]
    assert store.writes[-1]["phase"] == "manual_review"
    assert client.executors.create_calls == []


def test_create_recovery_cancellation_preserves_frozen_request_and_exact_id(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    executors = Executors(detail_error=asyncio.CancelledError())
    _, store = _install(monkeypatch, tmp_path, executors=executors)
    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )

    result = _run(
        lp_create.Config(
            controller_id="lp_expert.orca_1",
            operation_id="create-operation-1",
            lp_executor_id="executor-new",
        )
    )

    assert result["status"] == "uncertain"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert store.writes[-1]["phase"] == "uncertain"
    assert store.writes[-1]["result"]["executor_id"] == "executor-new"
    assert store.writes[-1]["result"]["executor_request"] == ready["executor_request"]


def test_create_is_blocked_by_another_unresolved_current_session_operation(
    monkeypatch, tmp_path
):
    client, store = _install(monkeypatch, tmp_path)
    store.unresolved = [
        {
            "operation_id": "older-create-operation",
            "operation_kind": "create",
            "phase": "submitted",
            "mutation_possible": True,
        }
    ]

    result = _run(_config(_candidate()))

    assert result["status"] == "rejected_before_submit"
    assert "unresolved current-session operation" in result["reason"]
    assert client.executors.create_calls == []


def test_create_reconciles_one_exact_native_executor_id(monkeypatch, tmp_path):
    candidate = _candidate()
    baseline = executor_row("baseline", pool_address="other-pool")
    executors = Executors(rows=[baseline])
    client, store = _install(
        monkeypatch,
        tmp_path,
        executors=executors,
    )

    ready = _run(_config(candidate))
    executors.created_config = copy.deepcopy(
        ready["executor_request"]["executor_config"]
    )
    result = _run(
        _config(candidate).model_copy(update={"lp_executor_id": "executor-new"})
    )

    assert result["status"] == "confirmed"
    assert result["executor_id"] == "executor-new"
    assert result["recovery_source"] == "exact_frozen_executor_request"
    assert client.executors.create_calls == []
    assert store.writes[-1]["phase"] == "confirmed"


def test_submitted_create_replays_exact_executor_detail_without_second_create(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    executors = Executors()
    executors.created_config = _expected_executor_config(candidate)
    client, store = _install(
        monkeypatch,
        tmp_path,
        executors=executors,
        existing={
            "phase": "submitted",
            "mutation_possible": True,
            "result": {"executor_id": "executor-new"},
            "reason": None,
        },
    )

    result = _run(_config(candidate))

    assert result["status"] == "confirmed"
    assert result["executor_id"] == "executor-new"
    assert result["recovery_source"] == "exact_frozen_executor_request"
    assert result["retry_allowed"] is False
    assert client.executors.create_calls == []
    assert store.writes[-1]["phase"] == "confirmed"


def test_existing_create_intent_conflict_is_manual_review_without_resubmit(
    monkeypatch, tmp_path
):
    candidate = _candidate()
    client, store = _install(
        monkeypatch,
        tmp_path,
        conflict={
            "operation_id": "create-operation-1",
            "operation_kind": "create",
            "tick": 2,
            "phase": "submitted",
            "mutation_possible": True,
            "result": {"executor_id": "executor-other"},
            "intent": {"pool_address": "different-pool"},
        },
    )

    result = _run(_config(candidate))

    assert result["status"] == "manual_review"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert result["executor_id"] == "executor-other"
    assert "identity conflicts" in result["reason"]
    assert client.executors.create_calls == []
    assert store.writes == []
