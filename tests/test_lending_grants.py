import asyncio
from types import SimpleNamespace

import pytest

from condor.agents.lending import lending_exposure
from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)

WALLET = "0x" + "3" * 40
POOL = "0xa238dd80c259a72e81d7e4664a9801593f98d1c5"
ASSET = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
OPTIONS = [{"kind": "allow_once", "optionId": "allow"}]


class Client:
    def __init__(self, price="1.01"):
        self.policy = {
            "enabled": True,
            "automatic_admission": "database_serialized",
            "chain_id": 8453,
            "wallet": WALLET,
            "pool": POOL,
            "asset": ASSET,
            "decimals": 6,
            "account_name": "master_account",
            "controller_limits_raw": {"agent-a": "100000000"},
            "max_total_supply_raw": "150000000",
            "max_action_raw": "100000000",
            "max_gas_quote": "1",
        }
        self.positions = []
        self.price = price
        self.executors = SimpleNamespace(_get=self.read)
        self.market_data = SimpleNamespace(get_prices=self.prices)
        self.price_calls = []

    async def read(self, path):
        return (
            self.policy
            if path == "/executors/lending/policy"
            else {"positions": self.positions}
        )

    async def prices(self, **kwargs):
        self.price_calls.append(kwargs)
        return {"prices": {"USDC-USDT": self.price}}


def call(amount="60000000", action="supply"):
    return {
        "tool": "create_lending_executor",
        "input": {
            "controller_id": "agent-a",
            "chain_id": 8453,
            "commit": True,
            "require_lending_policy": True,
            "max_gas_quote": "1",
            "wallet": WALLET,
            "pool": POOL,
            "asset": ASSET,
            "amount": amount,
            "action": action,
            "notional_quote": "0.000001",
            "trading_pair": "ETH-ETH",
        },
    }


def position(net="60000000", pending="0", controller="agent-a"):
    return {
        "chain_id": 8453,
        "wallet": WALLET,
        "pool": POOL,
        "asset": ASSET,
        "account_name": "master_account",
        "controller_id": controller,
        "net_contributed_raw": net,
        "pending_supply_raw": pending,
        "pending_withdraw_raw": "0",
    }


def gate(client, state=None, limit=500):
    state = state or RiskState()
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_position_size_quote=limit)),
        state,
        agent_id="agent-a",
        price_client=client,
    )
    return callback, state


def allowed(result):
    return result["outcome"]["outcome"] == "selected"


def test_exact_grant_uses_trusted_usdc_price_not_notional_or_display_pair():
    client = Client()
    callback, state = gate(client)
    assert allowed(asyncio.run(callback(call(), OPTIONS)))
    assert state.total_exposure == pytest.approx(60.6)
    assert client.price_calls == [
        {"connector_name": "binance", "trading_pairs": "USDC-USDT"}
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "disabled",
        "require",
        "wallet",
        "pool",
        "asset",
        "controller",
        "account",
        "amount",
        "gas",
    ],
)
def test_authority_and_policy_changes_refuse_automatic_commit(mutation):
    client = Client()
    request = call()
    cfg = request["input"]
    if mutation == "disabled":
        client.policy["enabled"] = False
    elif mutation == "require":
        cfg["require_lending_policy"] = False
    elif mutation in {"wallet", "pool", "asset"}:
        cfg[mutation] = "0x" + "4" * 40
    elif mutation == "controller":
        request["input"]["controller_id"] = "agent-b"
    elif mutation == "account":
        request["input"]["account_name"] = "other"
    elif mutation == "amount":
        cfg["amount"] = "100000001"
    elif mutation == "gas":
        cfg["max_gas_quote"] = "2"
    callback, state = gate(client)
    assert not allowed(asyncio.run(callback(request, OPTIONS)))
    assert state.executor_count == 0


@pytest.mark.parametrize("price", [None, "NaN", "Infinity", "0", "-1"])
def test_unpriced_lending_never_auto_approves(price):
    callback, state = gate(Client(price))
    assert not allowed(asyncio.run(callback(call(), OPTIONS)))
    assert state.executor_count == 0


def test_old_positions_count_toward_quote_limit_and_pending_withdrawal_does_not_release_capacity():
    client = Client()
    client.positions = [position()]
    callback, state = gate(client, limit=90)
    assert not allowed(asyncio.run(callback(call("30000000"), OPTIONS)))
    assert state.total_exposure == pytest.approx(60.6)
    client.positions[0]["pending_withdraw_raw"] = "60000000"
    callback, _ = gate(client)
    assert not allowed(asyncio.run(callback(call("1", "withdraw"), OPTIONS)))


def test_approved_supplies_are_not_double_counted_when_they_appear_in_history():
    client = Client("1")
    callback, state = gate(client, limit=100)

    async def run():
        assert allowed(await callback(call("60000000"), OPTIONS))
        client.positions = [position()]
        assert allowed(await callback(call("40000000"), OPTIONS))

    asyncio.run(run())
    assert state.total_exposure == 100
    assert state.lending_exposure_quote == 100


def test_withdrawal_can_reduce_lending_above_quote_limit_without_releasing_unconfirmed_capacity():
    client = Client()
    client.positions = [position()]
    callback, state = gate(client, limit=10)
    assert allowed(asyncio.run(callback(call("60000000", "withdraw"), OPTIONS)))
    assert state.total_exposure == pytest.approx(60.6)


def test_each_new_tick_includes_completed_lending_and_blocks_other_excess_allocations():
    engine = RiskEngine(RiskLimits(max_position_size_quote=100))
    state = RiskState(total_exposure=30)
    engine.include_lending(state, {"lending": {"exposure_quote": 60}})
    assert state.total_exposure == 90
    other = {"tool": "create_grid_executor", "input": {"leverage": 1}}
    assert not engine.check_executor_action(other, state, 11)[0]
    engine.include_lending(state, {"lending": {"exposure_quote": None}})
    assert state.is_blocked


def test_provider_valuation_counts_pending_supplies_without_claiming_other_wallet_capital():
    client = Client("1.02")
    row = position(pending="40000000")
    row["wallet_receipt_balance_raw"] = "9000000000"
    assert asyncio.run(lending_exposure(client, [row])) == pytest.approx(102)
