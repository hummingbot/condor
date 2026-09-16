"""``sweep_fees`` and ``vault_status`` against a stubbed hummingbot-api client.

The impl in ``mcp_servers/hummingbot_api/tools/vault.py`` is MCP-free so it can
be driven here with a client that records what would have been signed. Every
refusal path is pinned — each is a message and never a swap — and the happy
path is pinned down to the leg order, the amounts and the ledger it leaves.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from mcp_servers.hummingbot_api.tools import vault as vault_tools
from mcp_servers.hummingbot_api.tools.vault import (
    DBC_CONNECTOR,
    SWEEP_LEDGER_FILE,
    SweepRefused,
    realised_fees,
    sweep_fees,
    swept_executors,
    vault_status,
)

MINT = "Mint111111111111111111111111111111111111111"
SWIG = "Swig11111111111111111111111111111111111111"


def _vault(**overrides) -> dict:
    block = {
        "slug": "lp-referencer-1",
        "mint": MINT,
        "pool": "Pool11111111111111111111111111111111111111",
        "run_id": "run-42",
        "swig_wallet": SWIG,
        "buyback_bps": 2500,
    }
    block.update(overrides)
    return block


def _executor(**overrides) -> dict:
    """The shape hummingbot-api returns for a closed LP executor (cbBTC-USDC on Orca)."""
    row = {
        "executor_id": "9ivvisksAZhwFA5fftJT7Y5EPT5oy6YkeN7oFjs7vfZA",
        "executor_type": "lp_executor",
        "trading_pair": "cbBTC-USDC",
        "status": "TERMINATED",
        "close_type": "EARLY_STOP",
        "cum_fees_quote": 3.1846e-05,  # gas paid, NOT income
        "net_pnl_quote": 0.0091,
        "custom_info": {
            "base_fee": 0.0,
            "quote_fee": 0.4,
            "fees_earned_quote": 0.4,
            "state": "COMPLETE",
        },
        "config": {"type": "lp_executor", "trading_pair": "cbBTC-USDC"},
    }
    row.update(overrides)
    return row


class _Swaps:
    """Records quotes and executes; answers with fixed prices."""

    def __init__(self):
        self.quotes: list[dict] = []
        self.executes: list[dict] = []
        self.fail_on: str | None = None

    async def get_swap_quote(self, **kw):
        self.quotes.append(kw)
        if kw["connector"] == DBC_CONNECTOR:
            # 1 SOL buys 1000 vault tokens on the curve.
            return {
                "amount_in": kw["amount"],
                "amount_out": Decimal(kw["amount"]) * 1000,
            }
        # 200 USDC per SOL on the aggregator.
        return {"amount_in": kw["amount"], "amount_out": Decimal(kw["amount"]) / 200}

    async def execute_swap(self, **kw):
        self.executes.append(kw)
        if self.fail_on and self.fail_on in ("*", kw["connector"]):
            raise RuntimeError("Gateway: simulation failed")
        return {"transaction_hash": f"sig-{len(self.executes)}", "status": "submitted"}


class _Executors:
    def __init__(self, executor):
        self.executor = executor

    async def get_executor(self, executor_id):
        return self.executor


class _Gateway:
    def __init__(self, pool=None, balances=None):
        self.pool = pool or {
            "poolAddress": "Pool1",
            "isMigrated": False,
            "migrationProgress": 0.12,
            "price": 0.000001,
            "quoteReserve": 10.2,
            "migrationQuoteThreshold": 85,
        }
        self.balances = balances or {"balances": {MINT: 1234.5}}
        self.calls: list[tuple[str, dict]] = []

    async def _get(self, path, params=None):
        self.calls.append((path, params))
        if path.endswith("pool-info"):
            return self.pool
        return self.balances


class _Client:
    def __init__(self, executor=None, gateway=None):
        self.executors = _Executors(executor or _executor())
        self.gateway_swap = _Swaps()
        self.gateway = gateway or _Gateway()


@pytest.fixture(autouse=True)
def _fresh_ledger():
    vault_tools.reset_ledger_for_tests()
    yield
    vault_tools.reset_ledger_for_tests()


def _run(coro):
    return asyncio.run(coro)


# ── realised fees: the field, and only the field ──


def test_realised_fees_read_fees_earned_quote_not_cum_fees_quote():
    fees, asset = realised_fees(_executor())
    assert fees == 0.4
    assert asset == "USDC"


def test_realised_fees_refuse_a_non_lp_executor():
    with pytest.raises(SweepRefused, match="not an LP executor"):
        realised_fees(_executor(executor_type="grid_executor"))


def test_realised_fees_refuse_when_the_field_is_absent():
    with pytest.raises(SweepRefused, match="fees_earned_quote"):
        realised_fees(_executor(custom_info={"state": "COMPLETE"}))


# ── refusals ──


def test_no_vault_block_refuses():
    client = _Client()
    with pytest.raises(SweepRefused, match="no vault block"):
        _run(sweep_fees(client, "abc", None))
    assert client.gateway_swap.executes == []


def test_zero_buyback_refuses():
    client = _Client()
    with pytest.raises(SweepRefused, match="buyback_bps = 0"):
        _run(sweep_fees(client, "abc", _vault(buyback_bps=0)))
    assert client.gateway_swap.quotes == []


def test_a_running_executor_refuses():
    client = _Client(_executor(status="RUNNING"))
    with pytest.raises(SweepRefused, match="not TERMINATED"):
        _run(sweep_fees(client, "abc", _vault()))
    assert client.gateway_swap.quotes == []


def test_zero_realised_fees_refuses():
    client = _Client(_executor(custom_info={"fees_earned_quote": 0.0}))
    with pytest.raises(SweepRefused, match="realised no fees"):
        _run(sweep_fees(client, "abc", _vault()))


def test_an_empty_executor_id_refuses():
    with pytest.raises(SweepRefused, match="executor_id"):
        _run(sweep_fees(_Client(), "", _vault()))


# ── the sweep ──


def test_a_sweep_converts_then_buys_from_the_swig_wallet(tmp_path):
    vault = _vault(session_dir=str(tmp_path))
    client = _Client()
    result = _run(sweep_fees(client, "exec-1", vault))

    # 0.4 USDC × 2500 bps = 0.1 USDC → 0.0005 SOL → 0.5 tokens.
    assert result["fee_amount"] == 0.4
    assert result["sweep_amount"] == pytest.approx(0.1)
    assert result["sol_amount"] == pytest.approx(0.0005)
    assert result["tokens_expected"] == pytest.approx(0.5)
    assert result["signature"] == "sig-2"
    assert result["stage"] == "done"

    convert, buy = client.gateway_swap.executes
    assert convert["connector"] == "jupiter/router"
    assert convert["trading_pair"] == "USDC-SOL"
    assert convert["side"] == "SELL"
    assert convert["amount"] == Decimal("0.1")
    assert convert["wallet_address"] == SWIG
    assert buy["connector"] == DBC_CONNECTOR
    assert buy["trading_pair"] == f"{MINT}-SOL"
    assert buy["side"] == "BUY"
    assert buy["amount"] == Decimal("0.0005")
    assert buy["wallet_address"] == SWIG

    text = result["formatted_output"]
    assert "0.4 USDC" in text and "2500 bps" in text and "sig-2" in text
    assert "fees_earned_quote" in text

    ledger = (tmp_path / SWEEP_LEDGER_FILE).read_text().splitlines()
    stages = [json.loads(line)["stage"] for line in ledger]
    assert stages == ["convert_submitted", "converted", "buy_submitted", "done"]


def test_sol_fees_skip_the_conversion():
    client = _Client(
        _executor(trading_pair="JUP-SOL", custom_info={"fees_earned_quote": 0.02})
    )
    result = _run(sweep_fees(client, "exec-sol", _vault(buyback_bps=10000)))
    assert result["sol_amount"] == pytest.approx(0.02)
    assert len(client.gateway_swap.executes) == 1
    assert client.gateway_swap.executes[0]["connector"] == DBC_CONNECTOR
    assert "already SOL" in result["formatted_output"]


def test_a_second_sweep_for_the_same_executor_refuses():
    client = _Client()
    _run(sweep_fees(client, "exec-1", _vault()))
    with pytest.raises(SweepRefused, match="already has a sweep record"):
        _run(sweep_fees(client, "exec-1", _vault()))
    assert len(client.gateway_swap.executes) == 2  # nothing more was signed


def test_the_ledger_on_disk_survives_a_new_process(tmp_path):
    vault = _vault(session_dir=str(tmp_path))
    _run(sweep_fees(_Client(), "exec-1", vault))
    vault_tools.reset_ledger_for_tests()  # a fresh server process

    client = _Client()
    with pytest.raises(SweepRefused, match="stage=done"):
        _run(sweep_fees(client, "exec-1", vault))
    assert client.gateway_swap.executes == []


def test_a_failed_buy_is_recorded_and_never_retried(tmp_path):
    vault = _vault(session_dir=str(tmp_path))
    client = _Client()
    client.gateway_swap.fail_on = DBC_CONNECTOR
    with pytest.raises(RuntimeError, match="simulation failed"):
        _run(sweep_fees(client, "exec-1", vault))

    stages = [
        json.loads(l)["stage"]
        for l in (tmp_path / SWEEP_LEDGER_FILE).read_text().splitlines()
    ]
    assert stages[-1] == "buy_failed"
    with pytest.raises(SweepRefused, match="stage=buy_failed"):
        _run(sweep_fees(_Client(), "exec-1", vault))


# ── vault_status ──


def test_vault_status_reads_the_pool_the_balance_and_the_ledger():
    vault = _vault()
    client = _Client()
    _run(sweep_fees(client, "exec-1", vault))

    status = _run(vault_status(client, vault))
    assert status["vault"] == vault
    assert status["token_balance"] == 1234.5
    assert [r["executor_id"] for r in status["swept"]] == ["exec-1"]
    text = status["formatted_output"]
    assert "lp-referencer-1" in text and "0.12" in text and "1234.5" in text
    assert "exec-1" in text and "sig-2" in text

    pool_call, balance_call = client.gateway.calls
    assert pool_call[0] == "/gateway/launch/pool-info"
    assert pool_call[1] == {
        "connector": "meteora",
        "network": "solana-mainnet-beta",
        "pool_address": vault["pool"],
    }
    assert balance_call[0] == "/gateway/balances"
    assert balance_call[1]["address"] == SWIG and balance_call[1]["tokens"] == MINT


def test_vault_status_refuses_without_a_block():
    with pytest.raises(SweepRefused, match="no vault block"):
        _run(vault_status(_Client(), None))


def test_vault_status_reports_an_unreadable_balance_and_keeps_the_block():
    client = _Client(gateway=_Gateway(balances={"balances": {"A": 1, "B": 2}}))
    status = _run(vault_status(client, _vault()))
    assert status["token_balance"] is None
    assert "did not answer" in status["balance_error"]
    assert status["vault"]["slug"] == "lp-referencer-1"
    assert "UNAVAILABLE" in status["formatted_output"]


def test_vault_status_reports_a_gateway_that_is_down_per_read():
    class _Down:
        async def _get(self, path, params=None):
            raise RuntimeError("Gateway service is not available")

    status = _run(vault_status(_Client(gateway=_Down()), _vault()))
    assert status["pool"] is None and "not available" in status["pool_error"]
    assert (
        status["token_balance"] is None and "not available" in status["balance_error"]
    )
    text = status["formatted_output"]
    assert "lp-referencer-1" in text and text.count("UNAVAILABLE") == 2


def test_swept_executors_are_scoped_to_the_run():
    client = _Client()
    _run(sweep_fees(client, "exec-1", _vault(run_id="run-1")))
    _run(sweep_fees(client, "exec-2", _vault(run_id="run-2")))
    assert [r["executor_id"] for r in swept_executors(_vault(run_id="run-1"))] == [
        "exec-1"
    ]


def test_the_conversion_goes_through_the_aggregator():
    client = _Client()
    result = _run(sweep_fees(client, "exec-1", _vault()))
    assert client.gateway_swap.executes[0]["connector"] == vault_tools.CONVERT_CONNECTOR
    assert f"via {vault_tools.CONVERT_CONNECTOR}" in result["formatted_output"]


def test_a_failed_aggregator_leg_retries_through_the_named_pool():
    client = _Client()
    client.gateway_swap.fail_on = vault_tools.CONVERT_CONNECTOR
    result = _run(sweep_fees(client, "exec-1", _vault()))

    tried = [e["connector"] for e in client.gateway_swap.executes]
    assert tried[:2] == [vault_tools.CONVERT_CONNECTOR, vault_tools.CONVERT_RETRY_CONNECTOR]
    retry = client.gateway_swap.executes[1]
    assert retry["extra_params"] == {"pool_address": vault_tools.CONVERT_RETRY_POOL}
    assert f"via {vault_tools.CONVERT_RETRY_CONNECTOR}" in result["formatted_output"]


def test_a_conversion_that_fails_on_every_route_is_refused():
    client = _Client()
    client.gateway_swap.fail_on = "*"
    with pytest.raises(Exception, match="failed on every route"):
        _run(sweep_fees(client, "exec-1", _vault()))
