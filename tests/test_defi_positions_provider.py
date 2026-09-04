"""The ``defi_positions`` core provider: on-chain executors plus the Aomi wallet.

Two clients meet here — the Hummingbot API (where the executor records live)
and Aomi (where the wallet is read) — and either may be down, unconfigured or
unhappy. Every failure mode must become a line the agent reads, never a raised
provider, and Aomi must not be touched at all when there is nothing to show.
"""

from __future__ import annotations

import asyncio

import condor.aomi_client as aomi_client
from condor.agents.providers import (
    ProviderRegistry,
    get_provider,
    list_core_providers,
    list_providers,
)
from condor.agents.providers.defi_positions import DefiPositionsProvider

TX = "0x" + "ab" * 32
WALLET = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"


def _executor(**over):
    ex = {
        "executor_id": "onchain-1234567890abcdef",
        "status": "TERMINATED",
        "close_type": "COMPLETED",
        "config": {
            "chain_id": 8453,
            "mode": "calls",
            "type": "onchain_executor",
            "controller_id": "agent-a",
        },
        "custom_info": {
            "tx_hashes": [TX],
            "wallet_address": WALLET,
            "chain_id": 8453,
            "digest": "sha256:deadbeef",
            "error": None,
        },
    }
    ex.update(over)
    return ex


class _Executors:
    def __init__(self, result=None, fail=None):
        self.result = result if result is not None else {"data": []}
        self.fail = fail
        self.kwargs = None

    async def search_executors(self, **kw):
        self.kwargs = kw
        if self.fail:
            raise self.fail
        return self.result


class _Client:
    def __init__(self, result=None, fail=None):
        self.executors = _Executors(result, fail)


class _Pipeline:
    def __init__(self, account=None, context=None, fail=None):
        self.account = account if account is not None else {}
        self.context = context if context is not None else {}
        self.fail = fail
        self.calls: list[tuple] = []
        self.closed = False

    async def evm_account(self, address, chain_id):
        self.calls.append(("evm_account", address, chain_id))
        if self.fail:
            raise self.fail
        return self.account

    async def evm_context(self, chain_id=None):
        self.calls.append(("evm_context", chain_id))
        if self.fail:
            raise self.fail
        return self.context

    async def close(self):
        self.closed = True


def _aomi(monkeypatch, pipeline):
    """Route the provider's Aomi reads to ``pipeline`` (``None`` = unconfigured)."""
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return pipeline

    monkeypatch.setattr(aomi_client, "get_pipeline_client", factory)
    return calls


def _run(client, config=None, agent_id="agent-a"):
    return asyncio.run(
        DefiPositionsProvider().execute(client, config or {}, agent_id=agent_id)
    )


# ── Registration ─────────────────────────────────────────────────────────────


def test_registered_and_core():
    provider = get_provider("defi_positions")

    assert isinstance(provider, DefiPositionsProvider)
    assert provider.is_core is True
    assert provider in list_core_providers()


# ── The search ───────────────────────────────────────────────────────────────


def test_searches_onchain_executors_for_this_agent(monkeypatch):
    _aomi(monkeypatch, None)
    client = _Client()

    _run(client, agent_id="agent-a")

    assert client.executors.kwargs == {
        "executor_types": ["onchain_executor"],
        "controller_ids": ["agent-a"],
        "limit": 50,
    }


def test_no_agent_id_searches_every_controller(monkeypatch):
    _aomi(monkeypatch, None)
    client = _Client()

    _run(client, agent_id="")

    assert client.executors.kwargs["controller_ids"] is None


def test_summary_lists_chain_close_type_and_tx_hash(monkeypatch):
    _aomi(monkeypatch, None)
    failed = _executor(
        executor_id="onchain-failed-0001",
        close_type="FAILED",
        custom_info={
            "tx_hashes": [],
            "wallet_address": WALLET,
            "error": {"reason": "awaiting_wallet", "message": "nobody signed"},
        },
    )
    client = _Client({"data": [_executor(), failed]})

    result = _run(client)

    assert result.name == "defi_positions"
    lines = result.summary.splitlines()
    assert lines[0] == "DeFi Positions (2 on-chain executors) [agent: agent-a]:"
    assert lines[1] == (
        "  onchain-1234 chain=8453 mode=calls status=TERMINATED close=COMPLETED "
        f"tx={TX[:10]}…{TX[-4:]}"
    )
    assert lines[2] == (
        "  onchain-fail chain=8453 mode=calls status=TERMINATED close=FAILED "
        "err=awaiting_wallet"
    )
    assert result.data["executors"] == [_executor(), failed]


def test_a_running_executor_prints_a_dash_for_its_close_type(monkeypatch):
    _aomi(monkeypatch, None)
    running = _executor(
        status="RUNNING", close_type=None, custom_info={"tx_hashes": []}
    )
    client = _Client({"data": [running]})

    result = _run(client)

    assert "status=RUNNING close=-" in result.summary
    assert "tx=" not in result.summary


def test_none_and_no_flag_is_one_line_and_never_touches_aomi(monkeypatch):
    calls = _aomi(monkeypatch, _Pipeline())
    client = _Client({"data": []})

    result = _run(client)

    assert result.summary == "DeFi Positions [agent: agent-a]: none"
    assert result.data == {"executors": [], "wallet": None}
    assert calls["n"] == 0


def test_the_flag_asks_for_the_wallet_even_with_no_executors(monkeypatch):
    pipeline = _Pipeline(account={"balance_native": "0.25", "nonce": 3})
    calls = _aomi(monkeypatch, pipeline)
    client = _Client({"data": []})

    result = _run(client, {"defi_positions": True, "wallet_address": WALLET})

    assert calls["n"] == 1
    assert pipeline.calls == [("evm_account", WALLET, 8453)]
    assert result.summary.splitlines()[0] == (
        "DeFi Positions (0 on-chain executors) [agent: agent-a]:"
    )
    assert f"Wallet {WALLET[:10]}… on chain 8453: 0.25 ETH (nonce 3)" in result.summary


# ── The wallet line ──────────────────────────────────────────────────────────


def test_wallet_line_uses_the_executors_wallet_address(monkeypatch):
    pipeline = _Pipeline(account={"balance_native": "1.5", "nonce": 12})
    _aomi(monkeypatch, pipeline)
    client = _Client({"data": [_executor()]})

    result = _run(client)

    assert pipeline.calls == [("evm_account", WALLET, 8453)]
    assert result.summary.splitlines()[-1] == (
        f"Wallet {WALLET[:10]}… on chain 8453: 1.5 ETH (nonce 12)"
    )
    assert result.data["wallet"]["address"] == WALLET
    assert result.data["wallet"]["account"] == {"balance_native": "1.5", "nonce": 12}
    assert pipeline.closed


def test_configured_wallet_and_chain_win_over_the_executor(monkeypatch):
    pipeline = _Pipeline(account={"balance_native": "9", "nonce": 0})
    _aomi(monkeypatch, pipeline)
    client = _Client({"data": [_executor()]})

    result = _run(client, {"wallet_address": "0xconfigured", "chain_id": 137})

    assert pipeline.calls == [("evm_account", "0xconfigured", 137)]
    assert "Wallet 0xconfigur… on chain 137: 9 POL (nonce 0)" in result.summary


def test_no_known_wallet_falls_back_to_the_chain_context(monkeypatch):
    pipeline = _Pipeline(context={"block_number": 123456, "gas_price": "0.01 gwei"})
    _aomi(monkeypatch, pipeline)
    client = _Client({"data": [_executor(custom_info={"tx_hashes": [TX]})]})

    result = _run(client)

    assert pipeline.calls == [("evm_context", 8453)]
    assert result.summary.splitlines()[-1] == (
        "Wallet: none known; chain 8453 at block 123456, gas 0.01 gwei"
    )
    assert result.data["wallet"]["address"] is None


def test_aomi_unconfigured_is_a_line_not_a_failure(monkeypatch):
    _aomi(monkeypatch, None)
    client = _Client({"data": [_executor()]})

    result = _run(client)

    assert result.summary.splitlines()[-1] == (
        "Wallet: Aomi not configured (AOMI_TOKEN unset)"
    )
    assert "onchain-1234" in result.summary
    assert "error" not in result.data


def test_aomi_read_failure_is_a_line_not_a_failure(monkeypatch):
    pipeline = _Pipeline(fail=RuntimeError("502 upstream"))
    _aomi(monkeypatch, pipeline)
    client = _Client({"data": [_executor()]})

    result = _run(client)

    assert result.summary.splitlines()[-1] == "Wallet: Aomi read failed (502 upstream)"
    assert "onchain-1234" in result.summary
    assert pipeline.closed


def test_search_failure_is_the_error_summary(monkeypatch):
    calls = _aomi(monkeypatch, _Pipeline())
    client = _Client(fail=RuntimeError("API unreachable"))

    result = _run(client)

    assert result.summary == (
        "DeFi Positions [agent: agent-a]: failed to fetch (API unreachable)"
    )
    assert result.data == {"error": "API unreachable"}
    assert calls["n"] == 0


def test_more_than_ten_executors_are_capped_in_the_summary(monkeypatch):
    _aomi(monkeypatch, None)
    rows = [_executor(id=f"onchain-{i:02d}") for i in range(12)]
    client = _Client({"data": rows})

    result = _run(client)

    assert "  … 2 more not shown" in result.summary
    assert len(result.data["executors"]) == 12


# ── Through the registry ─────────────────────────────────────────────────────


def test_run_core_providers_includes_defi_positions(monkeypatch):
    import condor.agents.providers as registry
    from condor.agents.providers.base import ProviderResult

    _aomi(monkeypatch, None)
    # Importing this module registered defi_positions first, which is enough to
    # make the registry think it is populated; load the rest explicitly.
    registry._auto_register()

    async def _stub(self, client, config, agent_id="", bot_names=None, since=0.0):
        return ProviderResult(name=self.name, data={}, summary=f"{self.name}: stub")

    for provider in list_providers():
        if provider.name != "defi_positions":
            monkeypatch.setattr(type(provider), "execute", _stub, raising=True)

    client = _Client({"data": [_executor()]})
    results = asyncio.run(
        ProviderRegistry().run_core_providers(client, {}, agent_id="agent-a")
    )

    assert "defi_positions" in results
    assert results["defi_positions"].summary.startswith(
        "DeFi Positions (1 on-chain executors) [agent: agent-a]:"
    )
    assert results["executors"].summary == "executors: stub"
