from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

from conftest import (
    POOL,
    SOL_MINT,
    executor_row,
    pool_record,
    runtime_scope,
)

from agents.lp_expert.core import orca, planner
from agents.lp_expert.routines import lp_create


def _candidate():
    value, error = orca.normalize_record(pool_record(), "all", "volume24h", 1)
    assert error is None
    unique, rejected = orca.deduplicate([value])
    assert not rejected
    candidate = orca.rank_pools(unique)[0]
    plan = planner.build_plan(
        planner.PlanRequest(
            pool_address=POOL,
            base_symbol="SOL",
            base_mint=SOL_MINT,
            base_decimals=9,
            current_price="180",
            tick_spacing=64,
            amount_quote="4",
            range_half_width_pct="10",
            minimum_range_half_width_pct="0.5",
            maximum_range_half_width_pct="20",
            rebalance_threshold_pct="1",
            max_slippage_pct="1",
        )
    )
    return {**candidate, "plan": plan}


class Store:
    next_existing = None
    next_conflict = None

    def __init__(self, scope, *, receipt_intent=None):
        self.scope = scope
        self.records = {}
        if type(self).next_existing is not None:
            self.records["create-operation-1"] = copy.deepcopy(type(self).next_existing)
        self.conflict = copy.deepcopy(type(self).next_conflict)
        self.unresolved = []
        self.writes = []
        self.admissions = []
        self.receipt_intent = receipt_intent

    def read_confirmed_swap(self, operation_id):
        plan = _candidate()["plan"]
        return {
            "operation_id": operation_id,
            "phase": "confirmed",
            "intent": self.receipt_intent
            or {
                "reason": "inventory_preparation",
                "side": "BUY",
                "trading_pair": "SOL-USDC",
                "base_mint": SOL_MINT,
            },
            "result": {
                "receipt": {
                    "transaction_hash": "tx-preparation",
                    "input_amount": plan["inventory"][
                        "estimated_usdc_for_preparation_swap"
                    ],
                    "output_amount": plan["inventory"]["base_amount"],
                }
            },
        }

    def identity(self, *, operation_id, operation_kind, intent):
        return SimpleNamespace(
            operation_id=operation_id,
            operation_kind=operation_kind,
            intent=intent,
            tick=self.scope.current_tick,
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
    ):
        self.rows = copy.deepcopy(rows or [])
        self.schema_missing = schema_missing
        self.create_error = create_error
        self.create_response = create_response or {"executor_id": "executor-new"}
        self.recovery_rows = copy.deepcopy(recovery_rows)
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
        config = copy.deepcopy(self.created_config)
        return {
            "executor_id": executor_id,
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
    async def get_state(self, **_):
        return {
            "master_account": {
                "solana-mainnet-beta": [
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
            }
        }


def _config(candidate):
    return lp_create.Config(
        controller_id="lp_expert.orca_1",
        tick=2,
        operation_id="create-operation-1",
        candidate=candidate,
        snapshot_plan_digest=candidate["plan"]["plan_digest"],
        preparation_operation_id="prepare-operation-1",
    )


def _expected_executor_config(candidate):
    snapshot = candidate["plan"]
    final = planner.build_plan(
        planner.PlanRequest(
            pool_address=candidate["pool_address"],
            base_symbol=candidate["token_a"]["symbol"],
            base_mint=candidate["token_a"]["mint"],
            base_decimals=candidate["token_a"]["decimals"],
            current_price=candidate["price"],
            tick_spacing=candidate["tick_spacing"],
            amount_quote=snapshot["inputs"]["amount_quote"],
            range_half_width_pct=snapshot["inputs"]["range_half_width_pct"],
            minimum_range_half_width_pct="0.5",
            maximum_range_half_width_pct="20",
            rebalance_threshold_pct="1",
            max_slippage_pct="1",
            attributed_base_amount=snapshot["inventory"]["base_amount"],
        )
    )
    return {**final["executor_config"], "controller_id": "lp_expert.orca_1"}


def _install(
    monkeypatch,
    tmp_path,
    *,
    executors=None,
    receipt_intent=None,
    refresh_error=None,
    existing=None,
    conflict=None,
):
    scope = runtime_scope(tmp_path, tick=2)
    client = SimpleNamespace(
        executors=executors or Executors(),
        gateway=Gateway(),
        portfolio=Portfolio(),
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
        return {key: value for key, value in candidate.items() if key != "plan"}

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

    result = _run(_config(candidate))

    assert result["status"] == "confirmed"
    assert result["executor_id"] == "executor-new"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert len(client.executors.create_calls) == 1
    assert store.admissions == ["create-operation-1"]
    assert [row["phase"] for row in store.writes] == [
        "submitting",
        "submitted",
        "confirmed",
    ]
    assert result["final_plan"]["inventory"]["inventory_ready"] is True
    assert result["report_id"] == "create-report"


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
    assert "not attributed to this candidate" in result["reason"]
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


def test_create_timeout_is_uncertain_and_never_retried(monkeypatch, tmp_path):
    candidate = _candidate()
    executors = Executors(create_error=TimeoutError("outcome unavailable"))
    client, store = _install(
        monkeypatch,
        tmp_path,
        executors=executors,
    )

    result = _run(_config(candidate))

    assert result["status"] == "uncertain"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert len(client.executors.create_calls) == 1
    assert store.writes[-1]["phase"] == "uncertain"


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


def test_create_recovers_one_exact_executor_after_lost_response(monkeypatch, tmp_path):
    candidate = _candidate()
    baseline = executor_row("baseline", pool_address="other-pool")
    executors = Executors(
        rows=[baseline],
        create_error=TimeoutError("response lost"),
        recovery_rows=[baseline],
    )
    client, store = _install(
        monkeypatch,
        tmp_path,
        executors=executors,
    )

    original_create = executors.create_executor

    async def create_and_prepare_match(**kwargs):
        executors.created_config = copy.deepcopy(kwargs["executor_config"])
        matching = {
            "executor_id": "executor-recovered",
            "controller_id": "lp_expert.orca_1",
            "status": "RUNNING",
            "is_active": True,
            "timestamp": 2_000,
            "net_pnl_pct": "0",
            "net_pnl_quote": "0",
            "config": {
                "type": "lp_executor",
                **copy.deepcopy(kwargs["executor_config"]),
            },
            "custom_info": {"state": "IN_RANGE"},
        }
        executors.recovery_rows = [baseline, matching]
        return await original_create(**kwargs)

    executors.create_executor = create_and_prepare_match

    result = _run(_config(candidate))

    assert result["status"] == "confirmed"
    assert result["executor_id"] == "executor-recovered"
    assert result["recovery_source"] == "exact_executor_search"
    assert len(client.executors.create_calls) == 1
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
    assert result["recovery_source"] == "exact_executor_detail"
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
