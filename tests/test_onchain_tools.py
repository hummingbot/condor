from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mcp_servers.hummingbot_api.tools import executor_create, onchain


@pytest.fixture
def client(monkeypatch):
    # A stale saved raw operation cannot change an explicitly reviewed lending plan.
    monkeypatch.setattr(
        executor_create.executor_preferences,
        "merge_with_defaults",
        lambda kind, config: {
            "operation": "old",
            "calls": [{"to": "old"}],
            "controller_id": "someone-else",
            **config,
        },
    )
    return SimpleNamespace(
        executors=SimpleNamespace(
            create_executor=AsyncMock(return_value={"executor_id": "id"})
        )
    )


@pytest.mark.asyncio
async def test_typed_lending_preserves_exact_plan_authority_and_policy(client):
    result = await onchain.create_lending_executor(
        client,
        chain_id=8453,
        wallet="wallet",
        pool="pool",
        asset="asset",
        amount="10000000",
        action="supply",
        commit=True,
        require_lending_policy=True,
        controller_id="agent-a",
        max_gas_quote="1",
    )
    assert result["executor_id"] == "id"
    sent = client.executors.create_executor.call_args.kwargs
    config = sent["executor_config"]
    assert (
        sent["controller_id"] == "agent-a" and sent["account_name"] == "master_account"
    )
    assert "controller_id" not in config
    assert config["lending"] == dict(
        chain_id=8453,
        wallet="wallet",
        pool="pool",
        asset="asset",
        amount="10000000",
        action="supply",
    )
    assert config["commit"] is True and config["require_lending_policy"] is True
    assert (
        config["calls"] is None
        and config["operation"] is None
        and config["arguments"] is None
    )


@pytest.mark.asyncio
async def test_typed_raw_call_keeps_calldata_and_explicit_dry_run(client):
    call = onchain.EvmCall(
        to="wallet", value="0", data=onchain.EvmCalldata(raw="0x1234")
    )
    await onchain.create_onchain_executor(
        client,
        chain_id=8453,
        mode="calls",
        calls=[call],
        commit=False,
        controller_id="agent-a",
    )
    config = client.executors.create_executor.call_args.kwargs["executor_config"]
    assert config["calls"][0]["data"]["raw"] == "0x1234"
    assert (
        config["commit"] is False
        and config["operation"] is None
        and config["lending"] is None
    )


@pytest.mark.asyncio
async def test_mixed_raw_and_catalog_modes_refuse_before_api(client):
    with pytest.raises(ValueError):
        await onchain.create_onchain_executor(
            client,
            chain_id=8453,
            mode="operation",
            operation="swap",
            calls=[onchain.EvmCall(to="wallet", value="0", data=onchain.EvmCalldata())],
            commit=True,
        )
    client.executors.create_executor.assert_not_awaited()


def test_unknown_raw_transaction_fields_are_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="extra_forbidden"):
        onchain.EvmCall(
            to="wallet", value="0", data=onchain.EvmCalldata(), spender="other"
        )
