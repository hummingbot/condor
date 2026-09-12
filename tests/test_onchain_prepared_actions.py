from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mcp_servers.hummingbot_api.tools import onchain


@pytest.fixture
def prepared(monkeypatch):
    onchain._prepared_actions.clear()
    clock = [1000.0]
    monkeypatch.setattr(onchain.time, "time", lambda: clock[0])
    batches = [
        {
            "version": "v0",
            "address_lookup_tables": ["lookup"],
            "instructions": [
                {
                    "program_id": "program",
                    "data_base64": "AA==",
                    "accounts": [{"pubkey": "wallet", "is_signer": True}],
                }
            ],
        }
    ]
    payload = {
        "wallet": "wallet",
        "cluster": "mainnet-beta",
        "result": {
            "wallet": "wallet",
            "cluster": "mainnet-beta",
            "instructions": batches,
            "expires_at": 1120,
            "market": {"address": "market"},
        },
    }
    client = SimpleNamespace(
        executors=SimpleNamespace(_post=AsyncMock(return_value=payload))
    )
    create = AsyncMock(return_value={"executor_id": "executor"})
    monkeypatch.setattr(onchain, "create_executor", create)
    return client, payload, clock, create


@pytest.mark.asyncio
async def test_preview_and_commit_preserve_exact_batches_without_exposing_binary(
    prepared,
):
    client, payload, clock, create = prepared
    expected = deepcopy(payload["result"]["instructions"])
    public = await onchain.prepare_onchain_action(client, {})
    assert "instructions" not in public["result"]
    assert public["result"]["allowed_programs"] == ["program"]
    payload["result"]["instructions"][0]["version"] = "legacy"
    for commit in (False, True):
        await onchain.create_onchain_executor(
            client,
            chain_id=1,
            mode="instructions",
            chain="svm",
            cluster="mainnet-beta",
            commit=commit,
            prepared_action_id=public["prepared_action_id"],
            reviewed_svm_plan_hash="a" * 64,
        )
        config = create.call_args.args[2]
        assert config["instructions"] == expected
        assert config["reviewed_svm_plan_hash"] == "a" * 64
        config["instructions"][0]["version"] = "tampered"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["expiry", "wrongclient", "raw", "operation", "cluster"]
)
async def test_invalid_reference_refuses_before_create(prepared, case):
    client, payload, clock, create = prepared
    public = await onchain.prepare_onchain_action(client, {})
    kwargs = dict(
        chain_id=1,
        mode="instructions",
        chain="svm",
        cluster="mainnet-beta",
        commit=True,
        prepared_action_id=public["prepared_action_id"],
    )
    if case == "expiry":
        clock[0] = 1120
    if case == "wrongclient":
        client = SimpleNamespace()
    if case == "raw":
        kwargs["instructions"] = []
    if case == "operation":
        kwargs["operation"] = "deposit"
    if case == "cluster":
        kwargs["cluster"] = "devnet"
    with pytest.raises(ValueError):
        await onchain.create_onchain_executor(client, **kwargs)
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_bounded_cache_evicts_oldest_and_expiry_boundary(prepared):
    client, payload, clock, create = prepared
    first = await onchain.prepare_onchain_action(client, {})
    for _ in range(64):
        await onchain.prepare_onchain_action(client, {})
    assert len(onchain._prepared_actions) == 64
    assert first["prepared_action_id"] not in onchain._prepared_actions
    clock[0] = 1119.999
    entry = next(iter(onchain._prepared_actions.values()))
    assert entry.resolve(client, "mainnet-beta", None)[0]["version"] == "v0"
    clock[0] = 1120
    with pytest.raises(ValueError, match="prepare again and review"):
        entry.resolve(client, "mainnet-beta", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [1000, 999, None, float("nan"), float("inf"), True])
async def test_invalid_expiry_does_not_cache(prepared, expiry):
    client, payload, clock, create = prepared
    payload["result"]["expires_at"] = expiry
    with pytest.raises(ValueError):
        await onchain.prepare_onchain_action(client, {})
    assert not onchain._prepared_actions


@pytest.mark.asyncio
async def test_policy_is_preserved_and_cannot_change_wallet(prepared):
    client, payload, clock, create = prepared
    public = await onchain.prepare_onchain_action(client, {})
    policy = onchain.SvmSpendingPolicy(
        wallet="wallet",
        market="market",
        protocol_program="program",
        allowed_programs=["program"],
        max_debits_raw={"native": "30000000"},
    )
    kwargs = dict(
        chain_id=1,
        mode="instructions",
        chain="svm",
        cluster="mainnet-beta",
        commit=False,
        prepared_action_id=public["prepared_action_id"],
        svm_spending_policy=policy,
    )
    await onchain.create_onchain_executor(client, **kwargs)
    assert create.call_args.args[2]["svm_spending_policy"] == policy.model_dump()
    create.reset_mock()
    policy.wallet = "other-wallet"
    with pytest.raises(ValueError, match="wallet or cluster"):
        await onchain.create_onchain_executor(client, **kwargs)
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_recipe_cannot_extend_cache_lifetime(prepared):
    client, payload, clock, create = prepared
    payload["result"]["expires_at"] = 2000
    public = await onchain.prepare_onchain_action(client, {})
    assert public["result"]["expires_at"] == 1120
