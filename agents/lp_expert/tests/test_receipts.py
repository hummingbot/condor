from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from conftest import POOL, SOL_MINT, WALLET, runtime_scope

from agents.lp_expert.core import receipts


def _identity(store, operation_id="swap-operation-1", **intent):
    return store.identity(
        operation_id=operation_id,
        operation_kind="swap",
        intent={
            "reason": "inventory_preparation",
            "pool_address": POOL,
            "base_mint": SOL_MINT,
            "amount": "0.01",
            **intent,
        },
    )


def test_receipt_round_trip_is_exact_and_idempotent(tmp_path):
    scope = runtime_scope(tmp_path)
    store = receipts.ReceiptStore(scope)
    identity = _identity(store)

    admitted = store.write(
        identity,
        phase="admitted",
        mutation_possible=False,
        create_only=True,
    )
    replay = store.write(
        identity,
        phase="admitted",
        mutation_possible=False,
    )
    store.write(
        identity,
        phase="submitting",
        mutation_possible=True,
    )
    confirmed = store.write(
        identity,
        phase="confirmed",
        mutation_possible=True,
        result={
            "receipt": {
                "transaction_hash": "tx-1",
                "input_amount": "2",
                "output_amount": "0.01",
            }
        },
    )

    assert admitted["operation_id"] == "swap-operation-1"
    assert replay["created_at"] == admitted["created_at"]
    assert confirmed["created_at"] == admitted["created_at"]
    assert store.read(identity) == confirmed
    assert store.read_confirmed_swap(identity.operation_id) == confirmed
    assert len(list((tmp_path / "lp_operations").glob("swap-operation-1.json"))) == 1


def test_operation_id_reuse_with_different_intent_fails_closed(tmp_path):
    store = receipts.ReceiptStore(runtime_scope(tmp_path))
    identity = _identity(store)
    store.write(
        identity,
        phase="admitted",
        mutation_possible=False,
        create_only=True,
    )
    conflict = _identity(store, amount="0.02")

    with pytest.raises(ValueError, match="identity or content"):
        store.read(conflict)
    with pytest.raises(ValueError, match="identity or content"):
        store.write(
            conflict,
            phase="submitting",
            mutation_possible=True,
        )


def test_uncertainty_is_durable_and_blocks_another_wallet_mutation(tmp_path):
    store = receipts.ReceiptStore(runtime_scope(tmp_path))
    uncertain = _identity(store, "uncertain-operation")
    store.write(
        uncertain,
        phase="uncertain",
        mutation_possible=True,
        reason="submission outcome unavailable",
        create_only=True,
    )

    blocker = store.unresolved_wallet_operation(exclude_operation_id="new-operation")

    assert blocker["operation_id"] == "uncertain-operation"
    assert blocker["phase"] == "uncertain"
    assert blocker["mutation_possible"] is True
    with pytest.raises(ValueError, match="transition"):
        store.write(
            uncertain,
            phase="rejected_before_submit",
            mutation_possible=False,
        )


def test_preparation_and_create_admission_are_one_per_tick(tmp_path):
    store = receipts.ReceiptStore(runtime_scope(tmp_path, tick=4))
    first = _identity(store, "preparation-one")
    second = _identity(store, "preparation-two")
    store.admit_preparation(first)
    store.admit_preparation(first)
    with pytest.raises(ValueError, match="already admitted this tick"):
        store.admit_preparation(second)

    create_one = store.identity(
        operation_id="create-operation-one",
        operation_kind="create",
        intent={"pool_address": "pool-one"},
    )
    create_two = store.identity(
        operation_id="create-operation-two",
        operation_kind="create",
        intent={"pool_address": "pool-two"},
    )
    assert store.admit_create(create_one)["phase"] == "admitted"
    assert store.admit_create(create_one)["phase"] == "admitted"
    with pytest.raises(ValueError, match="already admitted this tick"):
        store.admit_create(create_two)


def test_controller_mutation_lock_serializes_same_controller():
    first = receipts.controller_mutation_lock("lp_expert.orca_1")
    second = receipts.controller_mutation_lock("lp_expert.orca_1")
    foreign = receipts.controller_mutation_lock("lp_expert.orca_2")
    assert first is second
    assert first is not foreign

    order = []

    async def use(name):
        async with first:
            order.append(f"{name}-start")
            await asyncio.sleep(0)
            order.append(f"{name}-end")

    async def main():
        await asyncio.gather(use("one"), use("two"))

    asyncio.run(main())
    assert order in (
        ["one-start", "one-end", "two-start", "two-end"],
        ["two-start", "two-end", "one-start", "one-end"],
    )


def test_cleanup_requires_one_exact_prior_tick_native_stop(tmp_path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "snapshot_2.md").write_text("""### tool manage_executors

**Input:**
```json
{"action":"stop","controller_id":"lp_expert.orca_1","executor_id":"executor-1","keep_position":false}
```
""")
    scope = runtime_scope(tmp_path, tick=3)

    result = receipts.read_prior_tick_stop(scope, "executor-1", 2)

    assert result == {
        "tick": 2,
        "source": "snapshot_2",
        "controller_id": "lp_expert.orca_1",
        "executor_id": "executor-1",
        "keep_position": False,
    }


@pytest.mark.parametrize(
    "body",
    [
        "",
        """### tool manage_executors
**Input:**
```json
{"action":"stop","controller_id":"foreign.controller_1","executor_id":"executor-1","keep_position":false}
```""",
        """### tool manage_executors
**Input:**
```json
{"action":"stop","controller_id":"lp_expert.orca_1","executor_id":"executor-1","keep_position":true}
```""",
        """### tool manage_executors
**Input:**
```json
{"action":"stop","controller_id":"lp_expert.orca_1","executor_id":"executor-1","keep_position":false}
```
### second manage_executors
**Input:**
```json
{"action":"stop","controller_id":"lp_expert.orca_1","executor_id":"executor-1","keep_position":false}
```""",
    ],
)
def test_cleanup_prior_stop_ambiguity_and_foreign_evidence_fail_closed(tmp_path, body):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "snapshot_2.md").write_text(body)
    with pytest.raises(ValueError, match="one exact prior-tick"):
        receipts.read_prior_tick_stop(runtime_scope(tmp_path, tick=3), "executor-1", 2)


def test_cleanup_cannot_read_same_tick_or_experiment_evidence(tmp_path):
    with pytest.raises(ValueError, match="prior-tick"):
        receipts.read_prior_tick_stop(runtime_scope(tmp_path, tick=1), "executor-1", 1)
    with pytest.raises(ValueError, match="prior-tick"):
        receipts.read_prior_tick_stop(
            runtime_scope(tmp_path, mode="run_once", tick=1), "executor-1", 1
        )


def test_receipts_are_scoped_to_exact_controller_and_wallet(tmp_path):
    store = receipts.ReceiptStore(runtime_scope(tmp_path))
    identity = _identity(store)
    store.write(
        identity,
        phase="admitted",
        mutation_possible=False,
        create_only=True,
    )
    other_scope = replace(runtime_scope(tmp_path), controller_id="lp_expert.orca_2")
    other_store = receipts.ReceiptStore(other_scope)
    with pytest.raises(ValueError, match="scope or content"):
        other_store.read_by_id(identity.operation_id)

    wallet_scope = replace(runtime_scope(tmp_path), wallet_address=WALLET + "-other")
    wallet_store = receipts.ReceiptStore(wallet_scope)
    with pytest.raises(ValueError, match="scope or content"):
        wallet_store.read_by_id(identity.operation_id)


def test_read_only_receipt_store_has_no_directory_or_write_side_effect(
    tmp_path,
):
    scope = runtime_scope(tmp_path)
    operation_dir = tmp_path / "lp_operations"

    store = receipts.ReceiptStore(scope, read_only=True)

    assert operation_dir.exists() is False
    assert store.list_records() == []
    identity = _identity(store)
    with pytest.raises(PermissionError, match="read-only"):
        store.write(
            identity,
            phase="admitted",
            mutation_possible=False,
        )
    with pytest.raises(PermissionError, match="read-only"):
        store.admit_preparation(identity)
    assert operation_dir.exists() is False
