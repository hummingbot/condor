import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mcp_servers.hummingbot_api.formatters.executors import format_executor_detail
from mcp_servers.hummingbot_api.tools.executors import get_executor

EVIDENCE = (
    Path(__file__).parents[1]
    / "docs/aomi-demo-evidence/universal-v2/recording-20260912"
)


@pytest.mark.parametrize("venue", ["jupiter", "kamino", "pumpswap"])
@pytest.mark.parametrize("action", ["deposit", "withdraw"])
@pytest.mark.asyncio
async def test_chat_receives_review_evidence_from_recorded_executors(venue, action):
    executor = json.loads((EVIDENCE / f"ui-{venue}-{action}-executor.json").read_text())
    executor["type"] = "onchain_executor"
    client = SimpleNamespace(
        executors=SimpleNamespace(get_executor=AsyncMock(return_value=executor))
    )
    result = await get_executor(client, executor_id=executor["executor_id"])
    text = result["formatted_output"]
    evidence = json.loads(text[text.index("{") :])
    assert evidence["balance_changes"] == [
        {
            key: row[key]
            for key in (
                "account",
                "asset",
                "amount",
                "direction",
                "symbol",
                "decimals",
                "chain_id",
                "cluster",
                "standard",
                "step",
            )
            if key in row
        }
        for row in executor["custom_info"]["balance_changes"]
    ]
    assert evidence["tx_hashes"] == executor["custom_info"]["tx_hashes"]
    assert evidence["execution"]["committed"] is True
    assert (
        evidence["execution"]["svm_plan_hash"]
        == executor["config"]["reviewed_svm_plan_hash"]
    )
    assert evidence["svm_spending_policy"] == executor["config"]["svm_spending_policy"]
    assert "not receipt-derived" in text


def test_passed_simulation_does_not_hide_policy_refusal():
    executor = json.loads((EVIDENCE / "ui-asset-budget-proof.json").read_text())
    executor["type"] = "onchain_executor"
    text = format_executor_detail(executor)
    evidence = json.loads(text[text.index("{") :])
    assert evidence["execution"]["simulation_passed"] is True
    assert evidence["execution"]["committed"] is False
    assert evidence["execution"]["commit_attempted"] is False
    assert evidence["execution"]["reason"] == "spending_policy_refused"
    assert evidence["tx_hashes"] == []
    assert evidence["svm_spending_policy"]["max_debits_raw"]["native"] == "30000000"


def test_onchain_projection_excludes_raw_nested_and_scalar_sensitive_fields():
    executor = {
        "type": "onchain_executor",
        "config": {"token": "config-secret", "arguments": {"rpc": "rpc-secret"}},
        "custom_info": {
            "attestation": "attestation-secret",
            "commit_requests": [{"credential": "request-secret"}],
            "error": {"reason": "simulation_failed", "message": "error-secret"},
            "simulation_guards": [
                {"name": "simulation", "status": "failed", "message": "guard-secret"}
            ],
            "balance_changes": [
                {"account": "wallet", "amount": "1", "raw": "balance-secret"}
            ],
        },
    }
    text = format_executor_detail(executor)
    assert "-secret" not in text
    assert "simulation_failed" in text
    assert "missing values are unknown" in text
    assert '"committed": true' not in text


def test_other_executor_format_is_unchanged():
    text = format_executor_detail(
        {"type": "position_executor", "custom_info": {"side": "BUY"}}
    )
    assert text.startswith("Executor Details:")
    assert "Side: BUY" in text


def test_native_wallet_movement_is_not_presented_as_rent_plus_extra_fee():
    executor = json.loads((EVIDENCE / "ui-jupiter-deposit-executor.json").read_text())
    executor["type"] = "onchain_executor"
    text = format_executor_detail(executor)
    evidence = json.loads(text[text.index("{") :])
    wallet = evidence["execution"]["wallet_address"]
    native = [
        row
        for row in evidence["balance_changes"]
        if row["account"] == wallet and row["asset"] == "native"
    ]
    assert native[0]["amount"] == "2044280"
    assert evidence["execution"]["estimated_svm_network_fee_lamports"] == "5000"
    assert (
        "total net wallet movement, already including network fees and account funding"
        in text
    )
    assert "components of that movement, not extra debits" in text
    assert "Do not label the entire native outflow as account rent or funding" in text


def test_unconfirmed_attempt_is_not_reported_as_an_unsubmitted_preview():
    text = format_executor_detail(
        {
            "type": "onchain_executor",
            "custom_info": {
                "commit_attempted": True,
                "committed": False,
                "outcome_kind": "submitted",
                "tx_hashes": ["pending-signature"],
            },
        }
    )
    evidence = json.loads(text[text.index("{") :])
    assert evidence["execution"] == {
        "commit_attempted": True,
        "committed": False,
        "outcome_kind": "submitted",
    }
    assert evidence["tx_hashes"] == ["pending-signature"]
    assert "committed=false alone does not prove no transaction was submitted" in text
    assert "committed=true means the backend reports a confirmed outcome" in text
    assert "do not retry it as though it were an unsubmitted preview" in text
