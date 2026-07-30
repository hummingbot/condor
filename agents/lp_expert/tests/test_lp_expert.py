from __future__ import annotations

import asyncio
import copy
import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

import config_manager
from agents.lp_expert.routines import _gateway_operation_receipt as operation_receipts
from agents.lp_expert.routines import _orca_pool_metrics as metrics
from agents.lp_expert.routines import _routine_report as routine_reports
from agents.lp_expert.routines import clmm_position_plan as planner
from agents.lp_expert.routines import gateway_swap
from agents.lp_expert.routines import lp_create_guard as guard
from agents.lp_expert.routines import lp_portfolio_limits as portfolio_limits
from agents.lp_expert.routines import orca_pool_scan as scanner
from agents.lp_expert.routines import solana_transaction_reconcile as chain_reconcile
from condor.agents.agent import AgentStore
from condor.agents.config import load_full_config
from condor.agents.prompts import build_tick_prompt
from condor.agents.strategy import StrategyStore
from condor.memory.skills import SkillStore
from routines.base import discover_routines_from_path

ROOT = Path(__file__).resolve().parents[1]
USDC = metrics.USDC_MINT
SOL = "So11111111111111111111111111111111111111112"
WALLET = "Cs8TbwfyEN1paECV8aarY4oi9FMevWDBdEdb2afxpWgP"
GMT = "7i5KKsX2weiTkry7jA4ZwSuXGhs5eJBEjY8vVxR4pfRx"
ORCA_POOL = "7PNQ9rfSGCbCC3XTeL6CwwAzevqQGvKXeXMxP2TjS7rM"
JUPITER_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
ORCA_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
CHAIN_SIGNATURE = "hi7WNzYPf8wJLYWmE9qga54mY6DQB9iiCw8cH9YNGKeTebcj2kPJQQjTfSEKwPJe2F2jdcEyiaaYEuyLrZkYCd6"
SECOND_CHAIN_SIGNATURE = "4rUka19P9xsrr1uiy4RMCKD2Xrbv5K54TJAdswN2KdzJ7eVMsmthgGkfg5WJCc7FWMTJ8P9qSHhokC8YSF1qAeVQ"
REAL_SAVE_TRACE = routine_reports.save_trace


@pytest.fixture(autouse=True)
def stub_routine_reports(monkeypatch):
    async def save_trace(**values):
        return f"{values['source']}-report"

    monkeypatch.setattr(routine_reports, "save_trace", save_trace)


@pytest.fixture(autouse=True)
def isolate_gateway_operation_receipts(tmp_path, monkeypatch):
    strategies = tmp_path / "gateway_strategies"
    for strategy in ("orca", "meteora"):
        for session in (1, 2):
            (strategies / strategy / "sessions" / f"session_{session}").mkdir(
                parents=True
            )
    monkeypatch.setattr(gateway_swap, "STRATEGIES_DIR", strategies)
    return strategies


def pool_record(
    address: str = "pool-1",
    *,
    fees: tuple[float, float, float, float] = (10, 35, 180, 1000),
    volume: tuple[float, float, float, float] = (1000, 5000, 30000, 150000),
    tvl: float = 100_000,
    change: tuple[float, float, float, float] = (0.001, 0.004, 0.01, 0.03),
) -> dict:
    return {
        "address": address,
        "tokenA": {"symbol": "SOL", "mint": SOL, "decimals": 9},
        "tokenB": {"symbol": "USDC", "mint": USDC, "decimals": 6},
        "price": 180,
        "tvlUsdc": tvl,
        "tickSpacing": 64,
        "feeRate": 3000,
        "adaptiveFeeEnabled": False,
        "hasWarning": False,
        "updatedAt": "2026-07-29T00:00:00Z",
        "stats": {
            window: {"fees": fee, "volume": amount, "priceDelta": delta}
            for window, fee, amount, delta in zip(
                metrics.WINDOWS, fees, volume, change, strict=True
            )
        },
    }


def normalized(raw: dict, category: str = "utility", lens: str = "volume24h") -> dict:
    value, error = metrics.normalize_record(raw, category, lens, 1)
    assert error is None
    assert value is not None
    return value


def run(coro):
    return asyncio.run(coro)


def initial_plan(**overrides) -> dict:
    values = {
        "pool_address": "pool-1",
        "base_symbol": "SOL",
        "base_mint": SOL,
        "base_decimals": 9,
        "current_price": "180",
        "tick_spacing": 64,
        "amount_quote": "5",
        "range_half_width_pct": "10",
        "minimum_range_half_width_pct": "0.5",
        "maximum_range_half_width_pct": "20",
        "rebalance_threshold_pct": "1",
        "max_slippage_pct": "1",
    }
    values.update(overrides)
    return json.loads(run(planner.run(planner.Config(**values), None)))


def ready_plan(pool: str = "pool-1", **overrides) -> dict:
    first = initial_plan(pool_address=pool, **overrides)
    return initial_plan(
        pool_address=pool,
        attributed_base_amount=first["inventory"]["base_amount"],
        **overrides,
    )


def session_config() -> dict:
    return {
        "server_name": "local",
        "agent_key": "",
        "model_base_url": "",
        "frequency_sec": 60,
        "trading_context": "",
        "execution_mode": "loop",
        "max_ticks": 0,
        "bot_name": "",
        "account_name": "master_account",
        "default_risk_posture": "balanced",
        "total_amount_quote": 10,
        "max_open_executors": 2,
        "max_slot_deployments_per_tick": 2,
        "min_quote_per_executor": 5,
        "max_quote_per_executor": 5,
        "max_slippage_pct": 1,
        "min_sol_reserve": 0.05,
        "residual_base_dust_quote": 0.01,
        "minimum_range_half_width_pct": 0.5,
        "maximum_range_half_width_pct": 20,
        "rebalance_threshold_pct": 1,
        "executor_max_age_minutes": 1440,
        "executor_take_profit_net_pnl_ratio": 0.05,
        "executor_stop_loss_net_pnl_ratio": 0.05,
        "session_max_age_minutes": 1440,
        "session_take_profit_net_pnl_ratio": 0.05,
        "session_stop_loss_net_pnl_ratio": 0.05,
        "onchain_reconcile_before_seconds": 15,
        "onchain_reconcile_after_seconds": 180,
        "onchain_reconcile_signature_limit": 100,
        "onchain_reconcile_rpc_timeout_seconds": 15,
        "risk_limits": {
            "max_position_size_quote": 10,
            "max_open_executors": 2,
            "max_drawdown_pct": -1,
            "shutdown_drawdown_pct": -1,
        },
    }


def chain_config(**overrides) -> chain_reconcile.Config:
    values = {
        "controller_id": "lp_expert.orca_1",
        "operation_id": "orca1-gmt-buy",
        "account_name": "master_account",
        "network": planner.NETWORK,
        "wallet_address": WALLET,
        "operation_kind": "swap",
        "window_start": "2026-07-30T15:03:33Z",
        "window_end": "2026-07-30T15:06:48Z",
        "required_accounts": [GMT, USDC],
        "required_program_ids": [JUPITER_PROGRAM],
        "asset_changes": [
            {
                "mint": USDC,
                "decimals": 6,
                "direction": "decrease",
                "minimum_amount": "1",
                "maximum_amount": "2",
            },
            {
                "mint": GMT,
                "decimals": 9,
                "direction": "increase",
                "minimum_amount": "278",
                "maximum_amount": "279",
            },
        ],
    }
    values.update(overrides)
    return chain_reconcile.Config(**values)


def finalized_swap_transaction(
    *, block_time: int = 1785423829, gmt_raw: str = "278273559729"
) -> dict:
    return {
        "blockTime": block_time,
        "slot": 436173918,
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": WALLET, "signer": True},
                    {"pubkey": GMT, "signer": False},
                    {"pubkey": USDC, "signer": False},
                    {"pubkey": ORCA_POOL, "signer": False},
                    {"pubkey": JUPITER_PROGRAM, "signer": False},
                    {"pubkey": ORCA_PROGRAM, "signer": False},
                ],
                "instructions": [{"programId": JUPITER_PROGRAM}],
            },
            "signatures": [CHAIN_SIGNATURE],
        },
        "meta": {
            "err": None,
            "preBalances": [1_000_000_000, 0, 0, 0, 0, 0],
            "postBalances": [999_987_540, 0, 0, 0, 0, 0],
            "preTokenBalances": [
                {
                    "mint": USDC,
                    "owner": WALLET,
                    "uiTokenAmount": {"amount": "8808958", "decimals": 6},
                }
            ],
            "postTokenBalances": [
                {
                    "mint": USDC,
                    "owner": WALLET,
                    "uiTokenAmount": {"amount": "6989206", "decimals": 6},
                },
                {
                    "mint": GMT,
                    "owner": WALLET,
                    "uiTokenAmount": {"amount": gmt_raw, "decimals": 9},
                },
            ],
            "innerInstructions": [{"instructions": [{"programId": ORCA_PROGRAM}]}],
        },
    }


class MockExecutors:
    def __init__(
        self,
        plan: dict,
        rows: list[dict] | None = None,
        create_response: object = None,
        create_error: Exception | None = None,
        missing_schema_field: str | None = None,
    ):
        self.plan = plan
        self.rows = rows or []
        self.create_response = (
            {"executor_id": "executor-new"}
            if create_response is None
            else create_response
        )
        self.create_error = create_error
        self.missing_schema_field = missing_schema_field
        self.create_calls: list[dict] = []
        self.search_calls: list[dict] = []

    async def get_executor_config_schema(self, executor_type):
        assert executor_type == "lp_executor"
        fields = set(self.plan["executor_config"]) | {"controller_id"}
        if self.missing_schema_field:
            fields.remove(self.missing_schema_field)
        return {"properties": {field: {} for field in fields}}

    async def search_executors(self, **kwargs):
        assert kwargs["limit"] == 1000
        self.search_calls.append(copy.deepcopy(kwargs))
        return {"data": copy.deepcopy(self.rows)}

    async def create_executor(self, **kwargs):
        self.create_calls.append(kwargs)
        if self.create_error:
            raise self.create_error
        return self.create_response


class MockGatewaySwap:
    def __init__(
        self,
        *,
        quote_input: str = "2",
        quote_output: str = "0.0112",
        execute_input: str | None = None,
        execute_output: str | None = None,
        execute_error: Exception | None = None,
        execute_response: dict | None = None,
        status_response: dict | None = None,
        status_error: Exception | None = None,
        history_rows: list[dict] | None = None,
        history_error: Exception | None = None,
    ):
        self.quote_input = quote_input
        self.quote_output = quote_output
        self.execute_input = execute_input or quote_input
        self.execute_output = execute_output or quote_output
        self.execute_error = execute_error
        self.execute_response = execute_response
        self.status_response = status_response
        self.status_error = status_error
        self.history_rows = history_rows or []
        self.history_error = history_error
        self.quote_calls: list[dict] = []
        self.execute_calls: list[dict] = []
        self.status_calls: list[str] = []
        self.search_calls: list[dict] = []

    async def get_swap_quote(self, **kwargs):
        self.quote_calls.append(kwargs)
        return {
            "input_amount": self.quote_input,
            "output_amount": self.quote_output,
        }

    async def execute_swap(self, **kwargs):
        self.execute_calls.append(kwargs)
        if self.execute_error:
            raise self.execute_error
        return (
            copy.deepcopy(self.execute_response)
            if self.execute_response
            else {
                "transaction_hash": "tx-1",
                "status": "CONFIRMED",
                "input_amount": self.execute_input,
                "output_amount": self.execute_output,
            }
        )

    async def get_swap_status(self, transaction_hash):
        self.status_calls.append(transaction_hash)
        if self.status_error:
            raise self.status_error
        return (
            copy.deepcopy(self.status_response)
            if self.status_response
            else {
                "transaction_hash": transaction_hash,
                "status": "CONFIRMED",
                "input_amount": self.execute_input,
                "output_amount": self.execute_output,
            }
        )

    async def _post(self, path, params):
        assert path == "/gateway/swaps/search"
        self.search_calls.append(copy.deepcopy(params))
        if self.history_error:
            raise self.history_error
        return {"data": copy.deepcopy(self.history_rows), "pagination": {}}


class MockClient:
    def __init__(
        self,
        executors: MockExecutors,
        sol: str = "1",
        usdc: str = "10",
        gateway_swap: MockGatewaySwap | None = None,
        extra_balances: list[dict] | None = None,
        gateway_tokens: list[dict] | None = None,
        token_add_error: Exception | None = None,
    ):
        self.executors = executors
        self.gateway = SimpleNamespace(
            get_network_config=self.get_network_config,
            get_network_tokens=self.get_network_tokens,
            add_token=self.add_token,
        )
        self.gateway_swap = gateway_swap or MockGatewaySwap()
        self.portfolio = SimpleNamespace(get_state=self.get_state)
        self.sol = sol
        self.usdc = usdc
        self.extra_balances = extra_balances or []
        self.gateway_tokens = copy.deepcopy(gateway_tokens or [])
        self.token_add_error = token_add_error
        self.token_add_calls: list[dict] = []

    async def get_network_config(self, network):
        assert network == planner.NETWORK
        return {"default_wallet": "wallet-1"}

    async def get_network_tokens(self, network):
        assert network == planner.NETWORK
        return {"tokens": copy.deepcopy(self.gateway_tokens)}

    async def add_token(self, **kwargs):
        self.token_add_calls.append(copy.deepcopy(kwargs))
        if self.token_add_error:
            raise self.token_add_error
        self.gateway_tokens.append(
            {
                "address": kwargs["address"],
                "symbol": kwargs["symbol"],
                "decimals": kwargs["decimals"],
                "name": kwargs["name"],
            }
        )
        return {"status": "success"}

    async def get_state(self, **kwargs):
        return {
            "master_account": {
                planner.NETWORK: [
                    {"symbol": "SOL", "mint": SOL, "available": self.sol},
                    {"symbol": "USDC", "mint": USDC, "available": self.usdc},
                    *self.extra_balances,
                ]
            }
        }


def write_session(
    tmp_path: Path,
    monkeypatch,
    values: dict | None = None,
    *,
    started_at: float | None = None,
) -> Path:
    strategies = tmp_path / "strategies"
    path = strategies / "orca" / "sessions" / "session_1"
    path.mkdir(parents=True)
    config_path = path / "config.yml"
    config_path.write_text(yaml.safe_dump(values or session_config()))
    if started_at is not None:
        os.utime(config_path, (started_at, started_at))
    monkeypatch.setattr(guard, "STRATEGIES_DIR", strategies)
    monkeypatch.setattr(portfolio_limits, "STRATEGIES_DIR", strategies)
    return config_path


def guard_config(plan: dict, **overrides) -> guard.Config:
    values = {
        "controller_id": "lp_expert.orca_1",
        "operation_id": "operation-123",
        "wallet_address": "wallet-1",
        "attributed_base_amount": plan["inventory"]["base_amount"],
        "preparation_quote_spent": str(
            Decimal(plan["inventory"]["base_amount"])
            * Decimal(plan["inputs"]["current_price"])
        ),
        "plan": plan,
        "plan_digest": plan["plan_digest"],
    }
    values.update(overrides)
    return guard.Config(**values)


def active_row(
    pool: str,
    executor_id: str = "executor-old",
    plan: dict | None = None,
    *,
    timestamp: float = 1_000,
    net_pnl_pct: str = "0",
    net_pnl_quote: str = "0",
) -> dict:
    executor_config = (
        copy.deepcopy(plan["executor_config"])
        if plan is not None
        else {
            "type": "lp_executor",
            "connector_name": planner.NETWORK,
            "lp_provider": planner.LP_PROVIDER,
            "trading_pair": "TOKEN-USDC",
            "pool_address": pool,
            "lower_price": "160",
            "upper_price": "200",
            "base_amount": "0.013",
            "quote_amount": "2.674489303400218715734459384",
            "keep_position": False,
        }
    )
    return {
        "executor_id": executor_id,
        "timestamp": timestamp,
        "controller_id": "lp_expert.orca_1",
        "status": "RUNNING",
        "is_active": True,
        "net_pnl_pct": net_pnl_pct,
        "net_pnl_quote": net_pnl_quote,
        "custom_info": {"state": "IN_RANGE", "position_address": f"position-{pool}"},
        "config": executor_config,
    }


def install_client(monkeypatch, client):
    class Manager:
        async def get_client(self, server_name):
            assert server_name == "local"
            return client

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: Manager())


def gateway_config(
    action: str = "scope", controller_id: str = "lp_expert.orca_1", **overrides
) -> gateway_swap.Config:
    values = {
        "action": action,
        "controller_id": controller_id,
        "account_name": "master_account",
        "network": planner.NETWORK,
    }
    if action in {"quote", "recover", "execute", "status"}:
        values.update(
            connector="jupiter",
            trading_pair="SOL-USDC",
            side="BUY",
            amount="0.011",
            slippage_pct="1",
        )
    if action == "ensure_token":
        values.update(
            token_address="TokenMint111111111111111111111111111111111",
            token_symbol="TOKEN",
            token_decimals=9,
            token_name="Token",
        )
    if action == "execute":
        values.update(
            operation_id="operation-123",
            wallet_address="wallet-1",
            max_quote_input="2.5",
            reserve_token="SOL",
            minimum_reserve_amount="0.05",
        )
    if action == "recover":
        values.update(
            operation_id="operation-123",
            wallet_address="wallet-1",
            max_quote_input="2.5",
        )
    if action == "status":
        values.update(
            operation_id="operation-123",
            wallet_address="wallet-1",
            max_quote_input="2.5",
            transaction_hash="tx-1",
        )
    values.update(overrides)
    return gateway_swap.Config(**values)


def install_runtime(
    monkeypatch, execution_mode: str = "loop", include_strategy_config: bool = True
):
    async def runtime(controller_id):
        values = session_config() if include_strategy_config else {}
        values.update(
            agent_id=controller_id,
            status="running",
            execution_mode=execution_mode,
            server_name="local",
            _strategy_slug=controller_id.split(".", 1)[1].rsplit("_", 1)[0],
        )
        return values

    monkeypatch.setattr(gateway_swap, "_runtime", runtime)


def test_declared_public_routines_are_discovered():
    found = discover_routines_from_path(
        ROOT / "routines", agent_slug="lp_expert", force_reload=True
    )
    assert set(found) == {
        "orca_pool_scan",
        "clmm_position_plan",
        "gateway_swap",
        "lp_portfolio_limits",
        "solana_transaction_reconcile",
        "lp_create_guard",
    }


def test_dynamic_loader_configs_run_and_generate_real_reports(tmp_path, monkeypatch):
    from condor import reports as condor_reports

    ready = ready_plan()
    report_dir = tmp_path / "reports"
    monkeypatch.setattr(routine_reports, "save_trace", REAL_SAVE_TRACE)
    monkeypatch.setattr(condor_reports, "CHARTS_DIR", report_dir)
    monkeypatch.setattr(condor_reports, "INDEX_FILE", report_dir / "reports_index.json")
    found = discover_routines_from_path(
        ROOT / "routines", agent_slug="lp_expert", force_reload=True
    )
    assert set(found) == {
        "orca_pool_scan",
        "clmm_position_plan",
        "gateway_swap",
        "lp_portfolio_limits",
        "solana_transaction_reconcile",
        "lp_create_guard",
    }
    assert all(info.config_class.model_json_schema() for info in found.values())

    scan = found["orca_pool_scan"]
    monkeypatch.setitem(
        scan.run_fn.__globals__, "_fetch_json", lambda url: {"data": [pool_record()]}
    )
    scan_result = json.loads(run(scan.run_fn(scan.config_class(limit=5), None)))
    assert scan_result["status"] == "complete"
    assert scan_result["report_id"]

    plan_routine = found["clmm_position_plan"]
    plan_config = plan_routine.config_class(
        pool_address="pool-1",
        base_symbol="SOL",
        base_mint=SOL,
        base_decimals=9,
        current_price="180",
        tick_spacing=64,
        amount_quote="5",
        range_half_width_pct="10",
        minimum_range_half_width_pct="0.5",
        maximum_range_half_width_pct="20",
        rebalance_threshold_pct="1",
        max_slippage_pct="1",
    )
    plan_result = json.loads(run(plan_routine.run_fn(plan_config, None)))
    assert plan_result["status"] == "planned"
    assert plan_result["report_id"]

    executors = MockExecutors(ready)
    install_client(monkeypatch, MockClient(executors))
    install_runtime(monkeypatch)
    gateway = found["gateway_swap"]
    monkeypatch.setitem(gateway.run_fn.__globals__, "_runtime", gateway_swap._runtime)
    monkeypatch.setitem(
        gateway.run_fn.__globals__, "STRATEGIES_DIR", gateway_swap.STRATEGIES_DIR
    )
    gateway_result = json.loads(
        run(
            gateway.run_fn(
                gateway.config_class(**gateway_config().model_dump()),
                None,
            )
        )
    )
    assert gateway_result["status"] == "ready"
    assert gateway_result["report_id"]
    recovery_result = json.loads(
        run(
            gateway.run_fn(
                gateway.config_class(**gateway_config("recover").model_dump()),
                None,
            )
        )
    )
    assert recovery_result["status"] == "not_submitted"
    assert recovery_result["report_id"]

    chain = found["solana_transaction_reconcile"]

    async def chain_scope(config):
        return None, "https://rpc.invalid/private"

    async def chain_rpc(session, rpc_url, method, params):
        if method == "getSignaturesForAddress":
            return [
                {
                    "signature": CHAIN_SIGNATURE,
                    "blockTime": 1785423829,
                    "err": None,
                }
            ]
        return finalized_swap_transaction()

    monkeypatch.setitem(chain.run_fn.__globals__, "_scope", chain_scope)
    monkeypatch.setitem(chain.run_fn.__globals__, "_rpc", chain_rpc)
    chain_result = json.loads(
        run(
            chain.run_fn(
                chain.config_class(**chain_config().model_dump()),
                None,
            )
        )
    )
    assert chain_result["status"] == "confirmed"
    assert chain_result["report_id"]

    write_session(tmp_path, monkeypatch)
    limits_routine = found["lp_portfolio_limits"]
    monkeypatch.setitem(
        limits_routine.run_fn.__globals__,
        "STRATEGIES_DIR",
        portfolio_limits.STRATEGIES_DIR,
    )
    limits_result = json.loads(
        run(
            limits_routine.run_fn(
                limits_routine.config_class(controller_id="lp_expert.orca_1"),
                None,
            )
        )
    )
    assert limits_result["status"] == "complete"
    assert limits_result["report_id"]

    create_guard = found["lp_create_guard"]
    monkeypatch.setitem(
        create_guard.run_fn.__globals__, "STRATEGIES_DIR", guard.STRATEGIES_DIR
    )
    guard_result = json.loads(
        run(
            create_guard.run_fn(
                create_guard.config_class(**guard_config(ready).model_dump()),
                None,
            )
        )
    )
    assert guard_result["status"] == "submitted"
    assert guard_result["report_id"]

    report_ids = {
        scan_result["report_id"],
        plan_result["report_id"],
        gateway_result["report_id"],
        recovery_result["report_id"],
        chain_result["report_id"],
        limits_result["report_id"],
        guard_result["report_id"],
    }
    entries = json.loads((report_dir / "reports_index.json").read_text())
    indexed = {entry["id"]: entry for entry in entries}
    assert report_ids <= set(indexed)
    assert all(
        (report_dir / indexed[report_id]["filename"]).is_file()
        for report_id in report_ids
    )


def test_agent_tool_allowlist_and_thin_files():
    agent = AgentStore().get("lp_expert")
    assert agent is not None
    assert set(agent.tools) == {
        "manage_routines",
        "manage_skill",
        "explore_dex_pools",
        "explore_geckoterminal",
        "get_portfolio_overview",
        "manage_executors",
        "trading_agent_journal_write",
    }
    names = {path.name for path in (ROOT / "routines").glob("*.py")}
    assert not names & {"state.py", "open.py", "close.py", "recover.py"}
    assert (ROOT / "strategies" / "orca" / "learnings.md").read_text().strip() == ""


def test_tool_availability_only_forbids_consult_and_scopes_missing_evidence():
    agent = AgentStore().get("lp_expert")
    strategy = StrategyStore().get("lp_expert", "orca")
    assert agent and strategy
    prompt = build_tick_prompt(
        agent,
        strategy,
        {**strategy.default_config, "execution_mode": "loop"},
        {},
        "",
        "",
        "",
        {},
        agent_id="lp_expert.orca_1",
    )
    compact_prompt = " ".join(prompt.split())
    assert "Never call `consult`" in prompt
    assert "best-effort cache warm-up" in prompt
    assert "Attempt that group once and silently" in prompt
    assert "Never retry, repair, or report the grouped preload" in compact_prompt
    assert (
        "a partial or empty group result does not prove that any individual tool"
        in compact_prompt
    )
    assert "discover only that exact surface" in prompt
    assert (
        "Classify it as unavailable only when that targeted discovery or direct "
        "call fails" in compact_prompt
    )
    assert "Never mention preload or discovery housekeeping" in compact_prompt
    assert "Do not retry or discuss the group result" in prompt
    assert "follow runtime tool-discovery and retry guidance" not in prompt
    assert "equivalent current-session evidence" in prompt
    assert "Missing evidence blocks only the dependent action or slot" in prompt
    assert "Never substitute a mutation or journal surface" in prompt
    assert "a missing required tool is a fail-closed" not in prompt
    assert "never search for substitute tools" not in prompt
    assert "Never call `consult`, delegate, memory, history" not in prompt
    assert "Do not use memory, history search, consultation, delegation" not in prompt
    assert "`trading_agent_journal_write` exactly once" in prompt
    assert "with the injected current tick" in prompt
    assert "consult" not in agent.tools


def test_skill_architecture_is_shared_except_for_orca_intelligence():
    store = SkillStore("lp_expert")
    expected = {
        "hummingbot_mcp_operations",
        "hummingbot_api_contracts",
        "gateway_dex_operations",
        "lp_pool_review",
        "lp_range_and_inventory",
        "lp_portfolio_supervision",
        "lp_close_and_recovery",
        "orca_venue_intelligence",
    }
    skills = {item["name"]: item for item in store.search("", limit=20)}
    assert set(skills) == expected
    assert skills["orca_venue_intelligence"]["references_routine"] == "orca_pool_scan"
    assert skills["orca_venue_intelligence"]["routine_ok"] is True
    for name in {
        "lp_pool_review",
        "lp_range_and_inventory",
        "lp_portfolio_supervision",
        "lp_close_and_recovery",
    }:
        assert "Orca" not in skills[name]["body"]

    companions = {
        "hummingbot_mcp_operations": "tool-map.md",
        "hummingbot_api_contracts": "api-contract.md",
        "gateway_dex_operations": "gateway-contract.md",
        "orca_venue_intelligence": "orca-contract.md",
    }
    for name, filename in companions.items():
        skill = store.read(name)
        assert skill is not None
        assert filename in skill["files"]
        result = store.read_file(name, filename)
        assert result["content"].strip()
    strategy = (ROOT / "strategies" / "orca" / "strategy.md").read_text()
    assert all(f"`{name}`" in strategy for name in expected)


def test_skill_source_references_are_portable_github_links():
    reference_files = (
        ROOT / "skills" / "hummingbot_api_contracts" / "api-contract.md",
        ROOT / "skills" / "gateway_dex_operations" / "gateway-contract.md",
        ROOT / "skills" / "orca_venue_intelligence" / "orca-contract.md",
    )
    local_markers = (
        "/Users/",
        "/home/",
        "/workspace/",
        "file://",
        "`agents/",
        "`condor/",
        "`gateway/",
        "`hummingbot/",
        "`hummingbot-api/",
        "`mcp_servers/",
        "`routines/",
    )
    for path in reference_files:
        content = path.read_text()
        assert "https://github.com/" in content
        assert not any(marker in content for marker in local_markers)


def test_example_and_engine_config_match_guard_safety_contract():
    strategy = StrategyStore().get("lp_expert", "orca")
    assert strategy is not None
    engine_config = load_full_config(strategy.dir, strategy.default_config)
    example = yaml.safe_load(
        (ROOT / "strategies" / "orca" / "config.example.yml").read_text()
    )
    assert set(engine_config) == guard.SESSION_KEYS
    safety_keys = {
        "account_name",
        "default_risk_posture",
        "total_amount_quote",
        "max_open_executors",
        "max_slot_deployments_per_tick",
        "min_quote_per_executor",
        "max_quote_per_executor",
        "max_slippage_pct",
        "min_sol_reserve",
        "residual_base_dust_quote",
        "minimum_range_half_width_pct",
        "maximum_range_half_width_pct",
        "rebalance_threshold_pct",
        "executor_max_age_minutes",
        "executor_take_profit_net_pnl_ratio",
        "executor_stop_loss_net_pnl_ratio",
        "session_take_profit_net_pnl_ratio",
        "session_stop_loss_net_pnl_ratio",
        "onchain_reconcile_before_seconds",
        "onchain_reconcile_after_seconds",
        "onchain_reconcile_signature_limit",
        "onchain_reconcile_rpc_timeout_seconds",
        "risk_limits",
    }
    for key in safety_keys:
        value = example[key]
        assert engine_config[key] == value
    assert engine_config["frequency_sec"] > 0
    assert engine_config["executor_max_age_minutes"] > 0
    assert engine_config["session_max_age_minutes"] > 0


def test_prompt_contract_balanced_dynamic_risk_and_no_direct_create():
    agent = AgentStore().get("lp_expert")
    strategy = StrategyStore().get("lp_expert", "orca")
    assert agent and strategy
    config = {
        **strategy.default_config,
        "execution_mode": "loop",
        "trading_context": "be more risky",
    }
    prompt = build_tick_prompt(
        agent,
        strategy,
        config,
        {},
        "",
        "",
        "",
        {"max_position_size": 10, "max_open_executors": 2},
        agent_id="lp_expert.orca_1",
    )
    assert "configured `default_risk_posture`" in prompt
    assert "shipped Orca default is `balanced`" in prompt
    assert "more risky/aggressive/higher risk means `opportunistic`" in prompt
    assert "create LP executors only through `lp_create_guard`" in prompt
    assert (
        'manage_executors(action="stop", executor_id=..., keep_position=false)'
        in prompt
    )
    assert "same tick" in prompt
    assert "One candidate rejection must not become" in prompt
    assert "portfolio HOLD." in prompt
    assert "`min(clean_slot_count, max_slot_deployments_per_tick)`" in prompt
    assert "fill at most `max_slot_deployments_per_tick` clean slots" in prompt
    assert 'gateway_swap(action="recover")' in prompt
    assert "solana_transaction_reconcile" in prompt
    assert "finalized-chain fallback" in prompt
    assert "zero, multiple, truncated, unavailable" in prompt
    assert 'gateway_swap(action="ensure_token")' in prompt
    assert "token_address=<selected token_a mint>" in prompt
    assert "before the first token registration or wallet mutation" in prompt
    assert "register an unselected scan token" in prompt
    assert "bulk-register tokens, or restart Gateway" in prompt
    assert "intent is not proof of submission" in prompt
    assert "Resolve Gateway scope once" in prompt
    assert "do not duplicate a fresh scan" in prompt
    assert (
        "A `RUNNING` LP executor with its expected on-chain position plus an empty"
        in prompt
    )
    assert "`positions_summary` is healthy and not contradictory" in prompt
    assert "It must not block the" in prompt
    assert "remaining configured executor capacity" in prompt
    assert "Never use `result_limit`" in prompt
    assert "Do not independently retrieve the" in prompt
    assert "`lp_executor` schema: `lp_create_guard` retrieves" in prompt
    assert "generic position-hold lookup" in prompt
    assert (
        "scan -> scope -> plan -> journal (loop only) -> ensure selected token -> quote -> execute"
        in prompt
    )
    assert "max_usdc_for_preparation_swap" in prompt
    assert "All selected close chains remain in the same tick" in prompt
    assert "close at most one" not in prompt
    assert "scan `report_id`" in prompt
    assert "ordered routine report trace" in prompt
    assert all(
        routine in prompt
        for routine in (
            "clmm_position_plan",
            "lp_portfolio_limits",
            "gateway_swap",
            "solana_transaction_reconcile",
            "lp_create_guard",
        )
    )
    assert "never blindly retry" in prompt
    assert "exact executor evidence" in prompt
    assert "proves the native close-out did not complete" in prompt
    assert "`session_max_age_minutes`" in prompt
    assert "`executor_max_age_minutes`" in prompt
    assert "hard Agent-supervised close triggers" in prompt
    assert "exactly once even" in prompt
    assert "when none is active" in prompt
    assert "`session.stop_latched=true` is an immediate portfolio-wide" in prompt
    assert "Use only the routine's exact session-age" in prompt
    assert "loop-session age from the frozen current-session config" in prompt
    assert "never estimate," in prompt
    assert "clear, or override it in prose" in prompt
    assert "must be included" in prompt
    assert "Never add the `executor_*` settings to" in prompt
    assert "[SESSION CONTEXT]" in prompt and "be more risky" in prompt


def test_prompt_has_exact_canonical_deployment_routine_keys():
    agent = AgentStore().get("lp_expert")
    strategy = StrategyStore().get("lp_expert", "orca")
    assert agent and strategy
    prompt = build_tick_prompt(
        agent,
        strategy,
        {**strategy.default_config, "execution_mode": "loop"},
        {},
        "",
        "",
        "",
        {},
        agent_id="lp_expert.orca_1",
    )
    required = {
        "base_symbol",
        "base_mint",
        "base_decimals",
        "amount_quote",
        "range_half_width_pct",
        "minimum_range_half_width_pct",
        "maximum_range_half_width_pct",
        "rebalance_threshold_pct",
        "max_slippage_pct",
        "minimum_reserve_amount",
        "attributed_base_amount",
        "preparation_quote_spent",
        "plan_digest",
    }
    assert required <= set(planner.Config.model_fields) | set(
        gateway_swap.Config.model_fields
    ) | set(guard.Config.model_fields)
    assert all(f"`{field}`" in prompt or field in prompt for field in required)
    assert "minimum_reserve_amount=<configured min_sol_reserve>" in prompt
    assert "`min_reserve`" in prompt
    assert "`minimum_reserve`" in prompt
    assert "`reserve_amount`" in prompt
    assert "never by" in prompt and "probing aliases" in prompt
    assert "finish its reconciliation and defer the next clean slot" in prompt


@pytest.mark.parametrize(
    "alias",
    ["min_reserve", "minimum_reserve", "reserve_amount"],
)
def test_gateway_rejects_legacy_reserve_aliases(alias):
    values = gateway_config("execute").model_dump(exclude_none=True)
    values[alias] = "0.05"
    with pytest.raises(ValidationError):
        gateway_swap.Config(**values)


def test_prompt_explicitly_authorizes_the_guarded_orca_mutation_exception():
    strategy = StrategyStore().get("lp_expert", "orca")
    agent = AgentStore().get("lp_expert")
    assert agent and strategy

    live = build_tick_prompt(
        agent,
        strategy,
        {**strategy.default_config, "execution_mode": "loop"},
        {},
        "",
        "",
        "",
        {},
        agent_id="lp_expert.orca_1",
    )
    assert 'Trade ONLY via manage_executors(action="create")' in live
    assert "The owner explicitly authorizes a narrow exception" in live
    assert 'gateway_swap(action="ensure_token")' in live
    assert 'gateway_swap(action="execute")' in live
    assert "`lp_create_guard` for one Strategy-planned LP executor create" in live
    assert "do not treat that generic line as" in live
    assert "blocking these exact Strategy-declared calls" in live
    assert 'direct `manage_executors(action="create")`' in live
    assert "It grants no" in live
    assert "mutation authority in dry-run mode" in live

    dry = build_tick_prompt(
        agent,
        strategy,
        {**strategy.default_config, "execution_mode": "dry_run"},
        {},
        "",
        "",
        "",
        {},
        agent_id="lp_expert.orca_e1",
    )
    assert "This is OBSERVATION ONLY" in dry
    assert "It grants no" in dry
    assert "mutation authority in dry-run mode" in dry


def test_agent_local_fast_path_does_not_confuse_active_lp_with_position_holds():
    agent = (ROOT / "AGENT.md").read_text()
    strategy = (ROOT / "strategies" / "orca" / "strategy.md").read_text()
    portfolio_skill = (
        ROOT / "skills" / "lp_portfolio_supervision" / "SKILL.md"
    ).read_text()
    api_skill = (ROOT / "skills" / "hummingbot_api_contracts" / "SKILL.md").read_text()

    assert "`positions_summary` reports executor-held" in agent
    assert "residual inventory; use it only during close/recovery" in agent
    assert "never as proof that an" in agent
    assert "active LP exists or as a deployment-capacity gate" in agent
    assert "A `RUNNING` LP executor plus an empty" in agent
    assert "`positions_summary` is expected, not contradictory" in agent

    assert "`positions_summary` is not an active-LP inventory view" in strategy
    assert "must not block the" in strategy
    assert "remaining configured executor capacity" in strategy
    assert "Do not query" in strategy
    assert "`positions_summary` while supervising a healthy active LP" in strategy
    assert "Only after stop/close may `positions_summary`" in strategy
    assert (
        "Never HOLD merely because an active LP executor has no corresponding"
        in strategy
    )

    assert "a running LP plus an empty held summary is expected" in portfolio_skill
    assert "`positions_summary` contains executor-held residual positions" in api_skill


def test_agent_local_known_paths_are_direct_and_skills_are_exception_driven():
    strategy = (ROOT / "strategies" / "orca" / "strategy.md").read_text()
    skill_entrypoints = {
        path.parent.name: path.read_text()
        for path in (ROOT / "skills").glob("*/SKILL.md")
    }

    assert "Run `orca_pool_scan` directly once with config key `limit`" in strategy
    assert "Never use `result_limit`" in strategy
    assert "Read `orca_venue_intelligence` and `lp_pool_review`" not in strategy
    assert "Read `lp_range_and_inventory`" not in strategy
    assert "Read `lp_portfolio_supervision` when any executor" not in strategy
    assert "Do not independently retrieve the" in strategy
    assert "Do not follow it with" in strategy
    assert "The ordinary one-slot fast path is:" in strategy

    for name in {
        "gateway_dex_operations",
        "hummingbot_api_contracts",
        "lp_pool_review",
        "lp_portfolio_supervision",
        "lp_range_and_inventory",
        "orca_venue_intelligence",
    }:
        assert "when_to_use: Read only" in skill_entrypoints[name]


def test_dry_run_contract_is_observation_only():
    strategy = StrategyStore().get("lp_expert", "orca")
    agent = AgentStore().get("lp_expert")
    prompt = build_tick_prompt(
        agent,
        strategy,
        {**strategy.default_config, "execution_mode": "dry_run"},
        {},
        "",
        "",
        "",
        {},
        agent_id="lp_expert.orca_e1",
    )
    assert "running inside Condor in 🧪 DRY RUN mode." in prompt
    assert "never mutate or journal" in prompt
    assert "Do NOT call trading_agent_journal_write" in prompt


def test_run_once_is_live_without_a_journal_and_loop_is_inferred():
    strategy = StrategyStore().get("lp_expert", "orca")
    agent = AgentStore().get("lp_expert")
    once = build_tick_prompt(
        agent,
        strategy,
        {**strategy.default_config, "execution_mode": "run_once"},
        {},
        "",
        "",
        "",
        {},
        agent_id="lp_expert.orca_e1",
    )
    assert "[EXECUTION MODE — RUN ONCE]" in once
    assert "LIVE execution" in once
    assert "The `_eN` suffix does not mean read-only" in once
    assert "Do NOT call trading_agent_journal_write" in once
    loop = build_tick_prompt(
        agent,
        strategy,
        {**strategy.default_config, "execution_mode": "loop"},
        {},
        "",
        "",
        "",
        {},
        agent_id="lp_expert.orca_1",
    )
    assert "Neither marker: live loop `_N`" in loop
    assert "Single-tick session with LIVE execution." not in loop
    assert "running inside Condor in 🧪 DRY RUN mode." not in loop
    assert "Write ONE action entry per tick" in loop


def test_normalization_technical_gates_only():
    value = normalized(pool_record(tvl=1, fees=(0, 0, 0, 0), volume=(0, 0, 0, 0)))
    assert value["tvl_usd"] == 1
    assert value["fees_usd"]["1h"] == 0
    invalid = pool_record()
    invalid["tokenB"]["mint"] = "not-usdc"
    assert (
        metrics.normalize_record(invalid, "utility", "volume24h", 1)[1]
        == "noncanonical_usdc_quote"
    )


def test_malformed_duplicate_cannot_hide_valid_record():
    good = normalized(pool_record())
    bad = pool_record()
    del bad["stats"]["4h"]["fees"]
    rejected, reason = metrics.normalize_record(bad, "utility", "volume7d", 2)
    assert rejected is None and reason == "missing_or_invalid_fees_4h"
    unique, duplicate_rejections = metrics.deduplicate([good])
    assert [item["pool_address"] for item in unique] == ["pool-1"]
    assert not duplicate_rejections


def test_contradictory_valid_duplicate_identity_is_rejected():
    first = normalized(pool_record())
    changed = pool_record()
    changed["tokenA"]["mint"] = "different-mint"
    second = normalized(changed, "memecoin", "volume7d")
    unique, rejected = metrics.deduplicate([first, second])
    assert unique == []
    assert rejected["contradictory_duplicate_identity"] == 2


def test_hourly_metrics_acceleration_and_zero_baseline():
    rows = metrics.rank_pools([normalized(pool_record(fees=(10, 40, 240, 1680)))])
    row = rows[0]
    assert row["hourly_fee_yield"] == {
        "1h": pytest.approx(0.0001),
        "4h": pytest.approx(0.0001),
        "24h": pytest.approx(0.0001),
        "7d": pytest.approx(0.0001),
    }
    assert row["sustainable_fee_yield_per_hour"] == pytest.approx(0.0001)
    zero = metrics.rank_pools([normalized(pool_record(fees=(1, 1, 0, 1)))])[0]
    assert zero["fee_yield_acceleration"]["1h"]["ratio_vs_24h"] is None


def test_neutral_rank_is_deterministic_without_rigid_posture_scores():
    source = [
        normalized(pool_record("a", fees=(2, 8, 60, 350), tvl=50_000)),
        normalized(pool_record("b", fees=(30, 70, 200, 900), tvl=150_000)),
        normalized(pool_record("c", fees=(1, 10, 300, 2500), tvl=500_000)),
    ]
    first = metrics.rank_pools(copy.deepcopy(source))
    second = metrics.rank_pools(copy.deepcopy(source))
    assert first == second
    assert {row["pool_address"] for row in first} == {"a", "b", "c"}
    assert [row["neutral_rank"] for row in first] == [1, 2, 3]
    assert all("posture_fit" not in row for row in first)
    assert all(
        set(row["mcda"]["components"]) == set(metrics.MCDA_WEIGHTS) for row in first
    )


def test_complete_scan_covers_every_category_lens_without_selecting(monkeypatch):
    monkeypatch.setattr(scanner, "_fetch_json", lambda url: {"data": [pool_record()]})

    async def save_report(payload):
        assert payload["recommendations"]
        return "scan-report"

    monkeypatch.setattr(scanner, "_save_report", save_report)
    raw = run(scanner.run(scanner.Config(limit=20), None))
    assert raw.startswith('{"report_id":"scan-report"')
    payload = json.loads(raw)
    assert payload["status"] == "complete"
    assert payload["deployable"] is True
    assert payload["report_id"] == "scan-report"
    assert payload["report_error"] is None
    assert payload["source_coverage"]["required_requests"] == 20
    assert payload["source_coverage"]["completed_requests"] == 20
    assert payload["recommendations"][0]["source_count"] == 20
    forbidden = {"selected_candidate", "next_action", "allocation", "range_plan"}

    def all_keys(value):
        if isinstance(value, dict):
            yield from value
            for item in value.values():
                yield from all_keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from all_keys(item)

    assert forbidden.isdisjoint(set(all_keys(payload)))


def test_partial_scan_is_diagnostic_but_not_deployable(monkeypatch):
    def fetch(url):
        if "categories=memecoin" in url and "sortBy=volume7d" in url:
            raise TimeoutError("slow")
        return {"data": [pool_record()]}

    monkeypatch.setattr(scanner, "_fetch_json", fetch)

    async def save_report(payload):
        return "partial-report"

    monkeypatch.setattr(scanner, "_save_report", save_report)
    payload = json.loads(run(scanner.run(scanner.Config(limit=20), None)))
    assert payload["status"] == "incomplete"
    assert payload["deployable"] is False
    assert payload["report_id"] == "partial-report"
    assert payload["source_coverage"]["completed_requests"] == 19
    assert len(payload["source_coverage"]["failed_requests"]) == 1
    assert payload["recommendations"]


def test_scan_report_failure_preserves_scan_result(monkeypatch):
    monkeypatch.setattr(scanner, "_fetch_json", lambda url: {"data": [pool_record()]})

    async def fail_report(payload):
        raise OSError("report storage unavailable")

    monkeypatch.setattr(scanner, "_save_report", fail_report)
    payload = json.loads(run(scanner.run(scanner.Config(limit=20), None)))
    assert payload["status"] == "complete"
    assert payload["deployable"] is True
    assert payload["report_id"] is None
    assert payload["report_error"] == "OSError: report storage unavailable"


def test_scan_saves_inspectable_report(tmp_path, monkeypatch):
    from condor import reports

    monkeypatch.setattr(reports, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(reports, "INDEX_FILE", tmp_path / "reports_index.json")
    monkeypatch.setattr(scanner, "_fetch_json", lambda url: {"data": [pool_record()]})
    payload = json.loads(run(scanner.run(scanner.Config(limit=20), None)))
    assert payload["report_id"]
    entries = json.loads((tmp_path / "reports_index.json").read_text())
    report = next(item for item in entries if item["id"] == payload["report_id"])
    html = (tmp_path / report["filename"]).read_text()
    assert "Orca LP Pool Scan" in html
    assert "SOL-USDC" in html
    assert "pool-1" in html
    assert SOL in html
    assert "base_decimals" in html


def test_scanner_config_is_small_and_strict():
    assert set(scanner.Config.model_fields) == {"limit"}
    with pytest.raises(ValidationError):
        scanner.Config()
    with pytest.raises(ValidationError):
        scanner.Config(limit=0)
    with pytest.raises(ValidationError):
        scanner.Config(limit=True)
    assert scanner.Config(limit=100).limit == 100
    with pytest.raises(ValidationError):
        scanner.Config(limit=20, risk_profile="steady")


def test_planner_outputs_tick_aligned_double_sided_plan_and_digest():
    plan = initial_plan()
    assert plan["status"] == "planned"
    assert plan["report_id"] == "clmm_position_plan-report"
    assert plan["report_error"] is None
    assert plan["inventory"]["base_amount"] > "0"
    assert plan["inventory"]["quote_amount"] > "0"
    assert plan["inventory"]["inventory_ready"] is False
    assert plan["range"]["lower_tick"] % 64 == 0
    assert plan["range"]["upper_tick"] % 64 == 0
    assert (
        float(plan["range"]["lower_limit_price"])
        < float(plan["range"]["lower_price"])
        < 180
        < float(plan["range"]["upper_price"])
        < float(plan["range"]["upper_limit_price"])
    )
    without_digest = dict(plan)
    for field in ("status", "plan_digest", "report_id", "report_error"):
        without_digest.pop(field)
    assert planner.plan_digest(without_digest) == plan["plan_digest"]
    assert plan["executor_config"]["keep_position"] is False
    assert "total_amount_quote" not in plan["executor_config"]
    assert float(plan["inventory"]["schema_native_exposure_quote"]) <= 5
    assert Decimal(plan["inventory"]["maximum_total_usdc"]) <= Decimal("5")
    assert Decimal(plan["inventory"]["preparation_slippage_headroom_quote"]) > 0
    assert float(plan["inventory"]["estimated_total_usdc"]) <= 5


def test_checked_live_lp_schema_proves_native_controller_and_amount_fields():
    observed_fields = {
        "base_amount",
        "connector_name",
        "controller_id",
        "extra_params",
        "id",
        "keep_position",
        "lower_limit_price",
        "lower_price",
        "lp_provider",
        "pool_address",
        "quote_amount",
        "side",
        "swap_provider",
        "timestamp",
        "trading_pair",
        "type",
        "upper_limit_price",
        "upper_price",
    }
    schema = {"fields": [{"name": name} for name in sorted(observed_fields)]}
    assert guard._schema_fields(schema) == observed_fields
    assert set(initial_plan()["executor_config"]) | {"controller_id"} <= observed_fields
    assert "total_amount_quote" not in observed_fields


def test_planner_replans_from_exact_attributed_receipt():
    plan = ready_plan()
    assert plan["inventory"]["inventory_ready"] is True
    assert plan["inventory"]["base_shortfall"] == "0"
    assert plan["inventory"]["estimated_usdc_for_preparation_swap"] == "0.000000"


def test_planner_accepts_config_selected_executor_amount():
    plan = initial_plan(amount_quote="3.25")
    assert plan["inputs"]["amount_quote"] == "3.25"
    assert float(plan["inventory"]["schema_native_exposure_quote"]) <= 3.25


def test_planner_keeps_tick_aligned_absolute_widths_within_configured_bounds():
    plan = initial_plan(range_half_width_pct="20")
    assert float(plan["inputs"]["actual_lower_half_width_pct"]) >= 0.5
    assert float(plan["inputs"]["actual_upper_half_width_pct"]) <= 20


def test_planner_accepts_configured_range_bounds():
    plan = initial_plan(
        range_half_width_pct="12",
        minimum_range_half_width_pct="1",
        maximum_range_half_width_pct="15",
    )
    assert float(plan["inputs"]["actual_lower_half_width_pct"]) >= 1
    assert float(plan["inputs"]["actual_upper_half_width_pct"]) <= 15


def test_planner_accepts_configured_slippage_bound():
    plan = initial_plan(max_slippage_pct="2")
    assert plan["inputs"]["max_slippage_pct"] == "2"


def test_planner_reserves_configured_headroom_for_the_execute_requote():
    plan = initial_plan()
    inventory = plan["inventory"]
    estimate = Decimal(inventory["estimated_usdc_for_preparation_swap"])
    cap = Decimal(inventory["max_usdc_for_preparation_swap"])
    headroom = Decimal(inventory["preparation_slippage_headroom_quote"])
    slippage = Decimal(plan["inputs"]["max_slippage_pct"]) / 100
    assert cap + Decimal(inventory["quote_amount"]) <= Decimal(
        plan["inputs"]["amount_quote"]
    )
    assert headroom == cap - estimate
    assert headroom >= estimate * slippage
    assert estimate * Decimal("1.00025") <= cap


def test_planner_report_trace_includes_preparation_cap_and_headroom(monkeypatch):
    traces = []

    async def capture_trace(**values):
        traces.append(values)
        return "planner-headroom-report"

    monkeypatch.setattr(routine_reports, "save_trace", capture_trace)
    plan = initial_plan()
    assert plan["report_id"] == "planner-headroom-report"
    inventory = next(row for row in traces[0]["evidence"] if row["kind"] == "inventory")
    assert inventory["max_usdc_for_preparation_swap"]
    assert inventory["preparation_slippage_headroom_quote"]
    assert inventory["maximum_total_usdc"] == "5.000000"


def test_more_configured_slippage_reserves_more_headroom_without_hardcoded_amounts():
    one = initial_plan(amount_quote="7.25", max_slippage_pct="1")
    two = initial_plan(amount_quote="7.25", max_slippage_pct="2")
    assert Decimal(two["inventory"]["preparation_slippage_headroom_quote"]) > Decimal(
        one["inventory"]["preparation_slippage_headroom_quote"]
    )
    assert Decimal(two["inventory"]["base_amount"]) < Decimal(
        one["inventory"]["base_amount"]
    )


def test_planner_uses_configured_rebalance_threshold():
    plan = initial_plan(rebalance_threshold_pct="2.5")
    lower = Decimal(plan["range"]["lower_price"])
    upper = Decimal(plan["range"]["upper_price"])
    assert Decimal(plan["range"]["lower_limit_price"]) == lower * Decimal("0.975")
    assert Decimal(plan["range"]["upper_limit_price"]) == upper * Decimal("1.025")


def test_planner_runtime_rejection_still_has_a_report_trace():
    result = initial_plan(amount_quote="0.0000001")
    assert result["status"] == "rejected"
    assert result["mutation"] is False
    assert result["report_id"] == "clmm_position_plan-report"
    assert result["report_error"] is None


def test_planner_has_no_silent_strategy_policy_defaults():
    for field in (
        "amount_quote",
        "range_half_width_pct",
        "minimum_range_half_width_pct",
        "maximum_range_half_width_pct",
        "rebalance_threshold_pct",
        "max_slippage_pct",
    ):
        assert planner.Config.model_fields[field].is_required()


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_quote": "0"},
        {"range_half_width_pct": "0.49"},
        {"range_half_width_pct": "20.01"},
        {"rebalance_threshold_pct": "100"},
        {"quote_mint": "wrong"},
        {"max_slippage_pct": "100.01"},
    ],
)
def test_planner_rejects_out_of_contract_inputs(overrides):
    values = {
        "pool_address": "pool",
        "base_symbol": "SOL",
        "base_mint": SOL,
        "base_decimals": 9,
        "current_price": "180",
        "tick_spacing": 64,
        "amount_quote": "5",
        "range_half_width_pct": "10",
        "minimum_range_half_width_pct": "0.5",
        "maximum_range_half_width_pct": "20",
        "rebalance_threshold_pct": "1",
        "max_slippage_pct": "1",
    }
    values.update(overrides)
    with pytest.raises(ValidationError):
        planner.Config(**values)


@pytest.mark.parametrize(
    ("strategy_slug", "controller_id", "execution_mode"),
    [
        ("orca", "lp_expert.orca_1", "loop"),
        ("orca", "lp_expert.orca_e1", "dry_run"),
        ("meteora", "lp_expert.meteora_e2", "run_once"),
        ("meteora", "lp_expert.meteora_3", "loop"),
    ],
)
def test_gateway_runtime_binds_exact_active_mode(
    monkeypatch, strategy_slug, controller_id, execution_mode
):
    async def call_main_api(method, path):
        assert (method, path) == ("GET", "/agents/lp_expert")
        return {
            "strategies": [
                {
                    "slug": strategy_slug,
                    "instances": [
                        {
                            "agent_id": controller_id,
                            "status": "running",
                            "execution_mode": execution_mode,
                            "server_name": "local",
                            "total_amount_quote": (
                                250 if strategy_slug == "meteora" else 10
                            ),
                            "risk_limits": {
                                "max_position_size_quote": (
                                    250 if strategy_slug == "meteora" else 10
                                ),
                                "max_open_executors": (
                                    8 if strategy_slug == "meteora" else 2
                                ),
                            },
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr("mcp_servers.condor.condor_client.call_main_api", call_main_api)
    assert run(gateway_swap._runtime(controller_id))["execution_mode"] == execution_mode


def test_gateway_runtime_rejects_controller_strategy_mismatch(monkeypatch):
    async def call_main_api(method, path):
        return {
            "strategies": [
                {
                    "slug": "orca",
                    "instances": [
                        {
                            "agent_id": "lp_expert.meteora_1",
                            "status": "running",
                            "execution_mode": "loop",
                            "server_name": "local",
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr("mcp_servers.condor.condor_client.call_main_api", call_main_api)
    with pytest.raises(ValueError, match="mode or server scope"):
        run(gateway_swap._runtime("lp_expert.meteora_1"))


def test_gateway_is_strategy_connector_pair_and_amount_agnostic(monkeypatch):
    swap = MockGatewaySwap(quote_input="7", quote_output="3")
    client = MockClient(
        MockExecutors(ready_plan()),
        gateway_swap=swap,
        extra_balances=[{"symbol": "USDT", "available": "10"}],
    )
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "execute",
                    controller_id="lp_expert.meteora_1",
                    connector="another_router",
                    trading_pair="TOKEN-USDT",
                    amount="3",
                    max_quote_input="8",
                    reserve_token=None,
                    minimum_reserve_amount=None,
                    slippage_pct="2.5",
                ),
                None,
            )
        )
    )
    assert result["status"] == "confirmed"
    assert swap.execute_calls[0]["connector"] == "another_router"
    assert swap.execute_calls[0]["network"] == planner.NETWORK
    assert swap.execute_calls[0]["trading_pair"] == "TOKEN-USDT"
    assert swap.execute_calls[0]["amount"] == Decimal("3")


def test_gateway_scope_resolves_default_wallet_and_master_account(
    monkeypatch,
):
    plan = ready_plan()
    client = MockClient(MockExecutors(plan))
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(run(gateway_swap.run(gateway_config(), None)))
    assert result["status"] == "ready"
    assert result["report_id"] == "gateway_swap-report"
    assert result["report_error"] is None
    assert result["binding"] == {
        "execution_mode": "loop",
        "server_name": "local",
        "account_name": "master_account",
        "network": planner.NETWORK,
        "wallet_address": "wallet-1",
    }


def test_gateway_rejects_account_that_conflicts_with_active_config(monkeypatch):
    client = MockClient(MockExecutors(ready_plan()))
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(
        run(gateway_swap.run(gateway_config(account_name="another_account"), None))
    )
    assert result["status"] == "rejected"
    assert "account conflicts" in result["reason"]


def test_gateway_loop_uses_current_session_as_account_authority(tmp_path, monkeypatch):
    strategies = tmp_path / "strategies"
    path = strategies / "orca" / "sessions" / "session_1"
    path.mkdir(parents=True)
    (path / "config.yml").write_text(yaml.safe_dump(session_config()))
    monkeypatch.setattr(gateway_swap, "STRATEGIES_DIR", strategies)
    client = MockClient(MockExecutors(ready_plan()))
    install_client(monkeypatch, client)
    install_runtime(monkeypatch, include_strategy_config=False)
    result = json.loads(run(gateway_swap.run(gateway_config(), None)))
    assert result["status"] == "ready"
    assert result["binding"]["account_name"] == "master_account"


def test_gateway_ensure_token_registers_and_verifies_exact_metadata(monkeypatch):
    client = MockClient(MockExecutors(ready_plan()))
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)

    result = json.loads(run(gateway_swap.run(gateway_config("ensure_token"), None)))

    assert result["status"] == "registered"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert result["token"] == {
        "address": "TokenMint111111111111111111111111111111111",
        "symbol": "TOKEN",
        "decimals": 9,
        "registered_now": True,
    }
    assert client.token_add_calls == [
        {
            "network_id": planner.NETWORK,
            "address": "TokenMint111111111111111111111111111111111",
            "symbol": "TOKEN",
            "decimals": 9,
            "name": "Token",
        }
    ]
    assert result["report_id"] == "gateway_swap-report"


def test_gateway_ensure_token_is_idempotent_for_existing_metadata(monkeypatch):
    token = {
        "address": "TokenMint111111111111111111111111111111111",
        "symbol": "token",
        "decimals": 9,
    }
    client = MockClient(MockExecutors(ready_plan()), gateway_tokens=[token])
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)

    result = json.loads(run(gateway_swap.run(gateway_config("ensure_token"), None)))

    assert result["status"] == "ready"
    assert result["mutation"] is False
    assert result["token"]["registered_now"] is False
    assert client.token_add_calls == []


def test_gateway_ensure_token_rejects_symbol_collision_without_mutation(monkeypatch):
    client = MockClient(
        MockExecutors(ready_plan()),
        gateway_tokens=[{"address": "DifferentMint", "symbol": "TOKEN", "decimals": 9}],
    )
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)

    result = json.loads(run(gateway_swap.run(gateway_config("ensure_token"), None)))

    assert result["status"] == "rejected"
    assert result["mutation"] is False
    assert "symbol collision" in result["reason"]
    assert client.token_add_calls == []


def test_gateway_ensure_token_never_mutates_in_dry_run(monkeypatch):
    client = MockClient(MockExecutors(ready_plan()))
    install_client(monkeypatch, client)
    install_runtime(monkeypatch, "dry_run")

    result = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "ensure_token",
                    controller_id="lp_expert.orca_e1",
                ),
                None,
            )
        )
    )

    assert result["status"] == "missing"
    assert result["mutation"] is False
    assert "dry run cannot mutate" in result["reason"]
    assert client.token_add_calls == []


def test_gateway_ensure_token_submission_error_is_uncertain(monkeypatch):
    client = MockClient(
        MockExecutors(ready_plan()),
        token_add_error=TimeoutError("registration response lost"),
    )
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)

    result = json.loads(run(gateway_swap.run(gateway_config("ensure_token"), None)))

    assert result["status"] == "uncertain"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert len(client.token_add_calls) == 1


def test_gateway_returns_normalized_quote_without_mutation(monkeypatch):
    swap = MockGatewaySwap()
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(run(gateway_swap.run(gateway_config("quote"), None)))
    assert result["status"] == "quoted"
    assert result["quote"] == {"input_amount": "2", "output_amount": "0.0112"}
    assert len(swap.quote_calls) == 1
    assert not swap.execute_calls


def test_gateway_accepts_a_requote_inside_the_planner_preparation_cap(monkeypatch):
    plan = initial_plan()
    estimate = Decimal(plan["inventory"]["estimated_usdc_for_preparation_swap"])
    cap = Decimal(plan["inventory"]["max_usdc_for_preparation_swap"])
    base = plan["inventory"]["base_amount"]
    requote = estimate * Decimal("1.00025")
    assert requote <= cap
    swap = MockGatewaySwap(
        quote_input=str(requote),
        quote_output=base,
        execute_input=str(requote),
        execute_output=base,
    )
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "execute",
                    amount=base,
                    max_quote_input=str(cap),
                ),
                None,
            )
        )
    )
    assert result["status"] == "confirmed"
    assert result["mutation"] is True
    assert len(swap.execute_calls) == 1


@pytest.mark.parametrize(
    ("controller_id", "execution_mode"),
    [("lp_expert.orca_1", "loop"), ("lp_expert.orca_e1", "run_once")],
)
def test_gateway_executes_one_exact_live_swap(
    isolate_gateway_operation_receipts, monkeypatch, controller_id, execution_mode
):
    swap = MockGatewaySwap()
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch, execution_mode)
    result = json.loads(
        run(
            gateway_swap.run(
                gateway_config("execute", controller_id=controller_id),
                None,
            )
        )
    )
    assert result["status"] == "confirmed"
    assert result["receipt"]["transaction_hash"] == "tx-1"
    assert result["retry_allowed"] is False
    assert len(swap.quote_calls) == 1
    assert len(swap.execute_calls) == 1
    assert swap.execute_calls[0]["wallet_address"] == "wallet-1"
    receipt_dir = (
        isolate_gateway_operation_receipts
        / "orca"
        / "sessions"
        / "session_1"
        / "gateway_operations"
    )
    assert receipt_dir.exists() is (execution_mode == "loop")


def test_gateway_normalizes_connector_receipt_and_accepts_configured_slippage(
    monkeypatch,
):
    swap = MockGatewaySwap(
        quote_input="2.494935",
        quote_output="0.033691180",
        execute_response={
            "signature": "tx-live-shape",
            "status": 1,
            "data": {
                "amountIn": "2.494913",
                "amountOut": "0.033673038",
            },
        },
        status_response={
            "transaction_hash": "tx-live-shape",
            "status": "CONFIRMED",
            "input_amount": "2.494913",
            "output_amount": "0.033673038",
        },
    )
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=swap),
    )
    install_runtime(monkeypatch)

    result = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "execute",
                    amount="0.033691180",
                    max_quote_input="2.518882",
                ),
                None,
            )
        )
    )

    assert result["status"] == "confirmed"
    assert result["receipt"]["transaction_hash"] == "tx-live-shape"
    assert result["receipt"]["input_amount"] == "2.494913"
    assert result["receipt"]["output_amount"] == "0.033673038"
    assert swap.status_calls == ["tx-live-shape"]
    assert result["report_id"] == "gateway_swap-report"


def test_gateway_persists_hash_before_reconciling_missing_execute_amounts(
    isolate_gateway_operation_receipts, monkeypatch
):
    swap = MockGatewaySwap(
        execute_response={
            "transaction_hash": "tx-hash-only",
            "status": "SUBMITTED",
        },
        status_response={
            "transaction_hash": "tx-hash-only",
            "status": "CONFIRMED",
            "input_amount": "2",
            "output_amount": "0.01095",
        },
    )
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=swap),
    )
    install_runtime(monkeypatch)

    result = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    receipt_path = (
        isolate_gateway_operation_receipts
        / "orca"
        / "sessions"
        / "session_1"
        / "gateway_operations"
        / "operation-123.json"
    )
    saved = json.loads(receipt_path.read_text())

    assert result["status"] == "confirmed"
    assert result["receipt"]["transaction_hash"] == "tx-hash-only"
    assert saved["phase"] == "confirmed"
    assert saved["receipt"]["transaction_hash"] == "tx-hash-only"
    assert result["report_id"] == "gateway_swap-report"


def test_gateway_status_failure_keeps_hash_pending_without_retry(
    isolate_gateway_operation_receipts, monkeypatch
):
    swap = MockGatewaySwap(
        execute_response={
            "transaction_hash": "tx-pending",
            "status": "SUBMITTED",
        },
        status_error=TimeoutError("status unavailable"),
    )
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=swap),
    )
    install_runtime(monkeypatch)

    result = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    receipt_path = (
        isolate_gateway_operation_receipts
        / "orca"
        / "sessions"
        / "session_1"
        / "gateway_operations"
        / "operation-123.json"
    )
    saved = json.loads(receipt_path.read_text())

    assert result["status"] == "submitted"
    assert result["receipt"]["transaction_hash"] == "tx-pending"
    assert result["retry_allowed"] is False
    assert saved["phase"] == "submitted"
    assert saved["receipt"]["transaction_hash"] == "tx-pending"
    assert result["report_id"] == "gateway_swap-report"


def test_gateway_explicit_status_failure_remains_pending(monkeypatch):
    swap = MockGatewaySwap(status_error=TimeoutError("status unavailable"))
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=swap),
    )
    install_runtime(monkeypatch)

    result = json.loads(run(gateway_swap.run(gateway_config("status"), None)))

    assert result["status"] == "submitted"
    assert result["receipt"]["transaction_hash"] == "tx-1"
    assert result["mutation"] is True
    assert result["retry_allowed"] is False
    assert result["report_id"] == "gateway_swap-report"


def test_gateway_run_once_without_account_authority_never_mutates(monkeypatch):
    swap = MockGatewaySwap()
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch, "run_once", include_strategy_config=False)
    result = json.loads(
        run(
            gateway_swap.run(
                gateway_config("execute", controller_id="lp_expert.orca_e1"),
                None,
            )
        )
    )
    assert result["status"] == "rejected"
    assert "config authority is unavailable" in result["reason"]
    assert not swap.quote_calls
    assert not swap.execute_calls


def test_gateway_dry_run_and_wallet_drift_never_mutate(monkeypatch):
    swap = MockGatewaySwap()
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch, "dry_run")
    dry = json.loads(
        run(
            gateway_swap.run(
                gateway_config("execute", controller_id="lp_expert.orca_e1"),
                None,
            )
        )
    )
    assert dry["status"] == "rejected"
    assert not swap.execute_calls
    install_runtime(monkeypatch, "loop")
    drift = json.loads(
        run(
            gateway_swap.run(
                gateway_config("execute", wallet_address="wallet-other"),
                None,
            )
        )
    )
    assert drift["status"] == "rejected"
    assert "wallet binding failed" in drift["reason"]
    assert not swap.execute_calls


def test_gateway_execute_accepts_amounts_above_five(monkeypatch):
    swap = MockGatewaySwap(quote_input="12", quote_output="0.1")
    client = MockClient(MockExecutors(ready_plan()), usdc="20", gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "execute",
                    amount="0.1",
                    max_quote_input="15",
                ),
                None,
            )
        )
    )
    assert result["status"] == "confirmed"
    assert result["receipt"]["input_amount"] == "12"
    assert len(swap.execute_calls) == 1


def test_gateway_sell_has_no_five_usdc_output_cap(monkeypatch):
    swap = MockGatewaySwap(quote_input="0.1", quote_output="12")
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "execute",
                    side="SELL",
                    amount="0.1",
                    attributed_base_amount="0.1",
                    max_quote_input=None,
                ),
                None,
            )
        )
    )
    assert result["status"] == "confirmed"
    assert result["receipt"]["output_amount"] == "12"
    assert len(swap.execute_calls) == 1


def test_gateway_buy_ceiling_and_sell_attribution_are_strict():
    with pytest.raises(ValidationError):
        gateway_config("execute", max_quote_input=None)
    with pytest.raises(ValidationError):
        gateway_config(
            "execute",
            side="SELL",
            amount="0.01",
            attributed_base_amount="0.009",
            max_quote_input=None,
        )


def test_gateway_requires_explicit_config_scope_and_slippage():
    with pytest.raises(ValidationError):
        gateway_swap.Config(
            action="scope",
            controller_id="lp_expert.orca_1",
            network=planner.NETWORK,
        )
    values = gateway_config("quote").model_dump()
    values.pop("slippage_pct")
    with pytest.raises(ValidationError):
        gateway_swap.Config(**values)


def test_gateway_submission_error_is_uncertain_and_not_retried(monkeypatch):
    swap = MockGatewaySwap(execute_error=TimeoutError("unknown submission"))
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    assert result["status"] == "uncertain"
    assert result["retry_allowed"] is False
    assert len(swap.execute_calls) == 1


def test_gateway_recovery_distinguishes_absent_rejected_and_uncertain_operations(
    monkeypatch,
):
    swap = MockGatewaySwap(quote_input="3")
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)

    absent = json.loads(run(gateway_swap.run(gateway_config("recover"), None)))
    assert absent["status"] == "not_submitted"
    assert absent["mutation"] is False
    assert absent["retry_allowed"] is True
    assert absent["report_id"] == "gateway_swap-report"

    rejected = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    assert rejected["status"] == "rejected"
    assert rejected["mutation"] is False
    recovered_rejection = json.loads(
        run(gateway_swap.run(gateway_config("recover"), None))
    )
    assert recovered_rejection["status"] == "rejected"
    assert recovered_rejection["mutation"] is False
    assert recovered_rejection["retry_allowed"] is True
    assert recovered_rejection["report_id"] == "gateway_swap-report"

    uncertain_swap = MockGatewaySwap(execute_error=TimeoutError("unknown submission"))
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=uncertain_swap),
    )
    uncertain = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "execute",
                    operation_id="operation-uncertain",
                ),
                None,
            )
        )
    )
    assert uncertain["status"] == "uncertain"
    recovered_uncertainty = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "recover",
                    operation_id="operation-uncertain",
                ),
                None,
            )
        )
    )
    assert recovered_uncertainty["status"] == "uncertain"
    assert recovered_uncertainty["mutation"] is True
    assert recovered_uncertainty["retry_allowed"] is False
    assert recovered_uncertainty["report_id"] == "gateway_swap-report"


def test_gateway_recovers_one_exact_hash_lost_after_submission(
    isolate_gateway_operation_receipts, monkeypatch
):
    swap = MockGatewaySwap(execute_error=TimeoutError("unknown submission"))
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=swap),
    )
    install_runtime(monkeypatch)

    uncertain = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    receipt_path = (
        isolate_gateway_operation_receipts
        / "orca"
        / "sessions"
        / "session_1"
        / "gateway_operations"
        / "operation-123.json"
    )
    attempted_at = json.loads(receipt_path.read_text())["created_at"]
    swap.history_rows = [
        {
            "transaction_hash": "tx-recovered",
            "status": "CONFIRMED",
            "connector": "jupiter",
            "network": planner.NETWORK,
            "trading_pair": "SOL-USDC",
            "side": "BUY",
            "input_amount": "2",
            "output_amount": "0.01095",
            "timestamp": attempted_at,
        }
    ]
    swap.status_response = {
        "transaction_hash": "tx-recovered",
        "status": "CONFIRMED",
        "input_amount": "2",
        "output_amount": "0.01095",
    }

    recovered = json.loads(run(gateway_swap.run(gateway_config("recover"), None)))

    assert uncertain["status"] == "uncertain"
    assert recovered["status"] == "confirmed"
    assert recovered["receipt"]["transaction_hash"] == "tx-recovered"
    assert recovered["recovery_source"] == "gateway_swap_history"
    assert recovered["retry_allowed"] is False
    assert len(swap.execute_calls) == 1
    assert swap.status_calls == ["tx-recovered"]
    assert len(swap.search_calls) == 1
    assert recovered["report_id"] == "gateway_swap-report"


def test_gateway_upgrades_session5_legacy_receipt_then_replans_or_unwinds_exact_base(
    isolate_gateway_operation_receipts, monkeypatch
):
    operation_id = "orca5-t1-sol-buy-01"
    receipt_dir = (
        isolate_gateway_operation_receipts
        / "orca"
        / "sessions"
        / "session_1"
        / "gateway_operations"
    )
    receipt_dir.mkdir()
    receipt_path = receipt_dir / f"{operation_id}.json"
    attempted_at = "2026-07-30T10:05:12.658055+00:00"
    receipt_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": attempted_at,
                "controller_id": "lp_expert.orca_1",
                "strategy_slug": "orca",
                "operation_id": operation_id,
                "account_name": "master_account",
                "network": planner.NETWORK,
                "wallet_address": "wallet-1",
                "trading_pair": "SOL-USDC",
                "side": "BUY",
                "phase": "uncertain",
                "mutation_possible": True,
                "receipt": None,
                "reason": "InvalidOperation: decimal.ConversionSyntax",
            }
        )
    )
    recovered_base = "0.033673038"
    preparation_spend = "2.494913"
    swap = MockGatewaySwap(
        history_rows=[
            {
                "transaction_hash": "tx-session5",
                "status": "CONFIRMED",
                "connector": "jupiter",
                "network": planner.NETWORK,
                "trading_pair": "SOL-USDC",
                "side": "BUY",
                "input_amount": preparation_spend,
                "output_amount": recovered_base,
                "timestamp": "2026-07-30T10:05:12.715168+00:00",
            }
        ],
        status_response={
            "transaction_hash": "tx-session5",
            "status": "CONFIRMED",
            "input_amount": preparation_spend,
            "output_amount": recovered_base,
        },
    )
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=swap),
    )
    install_runtime(monkeypatch)
    recover_config = gateway_config(
        "recover",
        operation_id=operation_id,
        amount="0.033691180",
        max_quote_input="2.518882",
    )

    recovered = json.loads(run(gateway_swap.run(recover_config, None)))
    upgraded = json.loads(receipt_path.read_text())
    fresh_price = Decimal(preparation_spend) / Decimal(recovered_base)
    resumed_plan = initial_plan(
        current_price=str(fresh_price),
        attributed_base_amount=recovered["receipt"]["output_amount"],
    )

    assert recovered["status"] == "confirmed"
    assert recovered["receipt"]["output_amount"] == recovered_base
    assert upgraded["version"] == operation_receipts.VERSION
    assert upgraded["connector"] == "jupiter"
    assert upgraded["amount"] == "0.033691180"
    assert upgraded["max_quote_input"] == "2.518882"
    assert resumed_plan["status"] == "planned"
    assert resumed_plan["inventory"]["inventory_ready"] is True
    assert resumed_plan["inventory"]["base_shortfall"] == "0"

    unwind_swap = MockGatewaySwap(
        quote_input=recovered_base,
        quote_output="2.49",
        execute_input=recovered_base,
        execute_output="2.49",
    )
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=unwind_swap),
    )
    unwind = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "execute",
                    operation_id="session5-unwind-01",
                    side="SELL",
                    amount=recovered_base,
                    attributed_base_amount=recovered_base,
                    max_quote_input=None,
                ),
                None,
            )
        )
    )

    assert unwind["status"] == "confirmed"
    assert unwind_swap.execute_calls[0]["amount"] == Decimal(recovered_base)
    assert unwind["report_id"] == "gateway_swap-report"


def test_gateway_history_recovery_requires_one_unique_match(
    isolate_gateway_operation_receipts, monkeypatch
):
    swap = MockGatewaySwap(execute_error=TimeoutError("unknown submission"))
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=swap),
    )
    install_runtime(monkeypatch)

    run(gateway_swap.run(gateway_config("execute"), None))
    receipt_path = (
        isolate_gateway_operation_receipts
        / "orca"
        / "sessions"
        / "session_1"
        / "gateway_operations"
        / "operation-123.json"
    )
    attempted_at = json.loads(receipt_path.read_text())["created_at"]
    common = {
        "status": "CONFIRMED",
        "connector": "jupiter",
        "network": planner.NETWORK,
        "trading_pair": "SOL-USDC",
        "side": "BUY",
        "input_amount": "2",
        "output_amount": "0.01095",
        "timestamp": attempted_at,
    }
    swap.history_rows = [
        {"transaction_hash": "tx-first", **common},
        {"transaction_hash": "tx-second", **common},
    ]

    recovered = json.loads(run(gateway_swap.run(gateway_config("recover"), None)))

    assert recovered["status"] == "manual_review"
    assert recovered["retry_allowed"] is False
    assert not swap.status_calls
    assert recovered["report_id"] == "gateway_swap-report"


def test_gateway_receipts_are_current_loop_session_only(
    isolate_gateway_operation_receipts, monkeypatch
):
    client = MockClient(MockExecutors(ready_plan()))
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    confirmed = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    assert confirmed["status"] == "confirmed"
    recovered = json.loads(run(gateway_swap.run(gateway_config("recover"), None)))
    assert recovered["status"] == "confirmed"
    assert recovered["receipt"]["transaction_hash"] == "tx-1"

    receipt = (
        isolate_gateway_operation_receipts
        / "orca"
        / "sessions"
        / "session_1"
        / "gateway_operations"
        / "operation-123.json"
    )
    assert receipt.is_file()
    assert not (
        isolate_gateway_operation_receipts
        / "orca"
        / "sessions"
        / "session_2"
        / "gateway_operations"
    ).exists()

    other_session = json.loads(
        run(
            gateway_swap.run(
                gateway_config(
                    "recover",
                    controller_id="lp_expert.orca_2",
                ),
                None,
            )
        )
    )
    assert other_session["status"] == "not_submitted"


def test_gateway_recovery_rejects_changed_original_bounds(monkeypatch):
    swap = MockGatewaySwap()
    install_client(
        monkeypatch,
        MockClient(MockExecutors(ready_plan()), gateway_swap=swap),
    )
    install_runtime(monkeypatch)
    created = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))

    recovered = json.loads(
        run(
            gateway_swap.run(
                gateway_config("recover", max_quote_input="2.6"),
                None,
            )
        )
    )

    assert created["status"] == "confirmed"
    assert recovered["status"] == "manual_review"
    assert recovered["mutation"] is True
    assert recovered["retry_allowed"] is False
    assert not swap.search_calls
    assert recovered["report_id"] == "gateway_swap-report"


def test_gateway_operation_receipt_rejects_symlink_escape(tmp_path):
    strategies = tmp_path / "strategies"
    session = strategies / "orca" / "sessions" / "session_1"
    outside = tmp_path / "outside"
    session.mkdir(parents=True)
    outside.mkdir()
    (session / "gateway_operations").symlink_to(outside, target_is_directory=True)
    identity = {
        "controller_id": "lp_expert.orca_1",
        "strategy_slug": "orca",
        "operation_id": "operation-123",
        "account_name": "master_account",
        "network": planner.NETWORK,
        "wallet_address": "wallet-1",
        "trading_pair": "SOL-USDC",
        "side": "BUY",
    }
    with pytest.raises(ValueError, match="symlink"):
        operation_receipts.write(
            strategies,
            identity,
            phase="submitting",
            mutation_possible=True,
        )


def test_gateway_never_resubmits_an_existing_operation_id(monkeypatch):
    swap = MockGatewaySwap()
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    first = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    second = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    assert first["status"] == "confirmed"
    assert second["status"] == "rejected"
    assert second["mutation"] is False
    assert second["retry_allowed"] is False
    assert second["operation_state"]["phase"] == "confirmed"
    assert len(swap.execute_calls) == 1


def test_gateway_marks_out_of_bounds_confirmed_receipt_for_manual_review(monkeypatch):
    swap = MockGatewaySwap(execute_output="0.001")
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    assert result["status"] == "manual_review"
    assert result["receipt"]["transaction_hash"] == "tx-1"
    assert result["retry_allowed"] is False
    assert len(swap.execute_calls) == 1


def test_gateway_reconciles_known_hash_without_mutation(monkeypatch):
    swap = MockGatewaySwap()
    client = MockClient(MockExecutors(ready_plan()), gateway_swap=swap)
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    result = json.loads(run(gateway_swap.run(gateway_config("status"), None)))
    assert result["status"] == "confirmed"
    assert result["receipt"]["transaction_hash"] == "tx-1"
    assert swap.status_calls == ["tx-1"]
    assert not swap.execute_calls


def install_chain_rpc(monkeypatch, transactions, *, rows=None):
    calls = []

    async def scope(config):
        return None, "https://rpc.invalid/private-key"

    async def rpc(session, rpc_url, method, params):
        calls.append((method, copy.deepcopy(params)))
        if method == "getSignaturesForAddress":
            return (
                copy.deepcopy(rows)
                if rows is not None
                else [
                    {
                        "signature": CHAIN_SIGNATURE,
                        "blockTime": 1785423829,
                        "err": None,
                    }
                ]
            )
        return copy.deepcopy(transactions[params[0]])

    monkeypatch.setattr(chain_reconcile, "_scope", scope)
    monkeypatch.setattr(chain_reconcile, "_rpc", rpc)
    return calls


def test_chain_reconcile_confirms_one_exact_finalized_swap(monkeypatch):
    calls = install_chain_rpc(
        monkeypatch, {CHAIN_SIGNATURE: finalized_swap_transaction()}
    )

    result = json.loads(run(chain_reconcile.run(chain_config(), None)))

    assert result["status"] == "confirmed"
    assert result["confirmation_source"] == "solana_rpc"
    assert result["mutation"] is False
    assert result["retry_allowed"] is False
    assert result["report_id"] == "solana_transaction_reconcile-report"
    assert result["matches"][0]["transaction_hash"] == CHAIN_SIGNATURE
    assert result["matches"][0]["asset_changes"] == [
        {
            "amount": "1.819752",
            "direction": "decrease",
            "mint": USDC,
            "raw_delta": "-1819752",
        },
        {
            "amount": "278.273559729",
            "direction": "increase",
            "mint": GMT,
            "raw_delta": "278273559729",
        },
    ]
    assert [method for method, _ in calls] == [
        "getSignaturesForAddress",
        "getTransaction",
    ]
    assert "rpc.invalid" not in json.dumps(result)


def test_chain_reconcile_known_hash_skips_wallet_history(monkeypatch):
    calls = install_chain_rpc(
        monkeypatch, {CHAIN_SIGNATURE: finalized_swap_transaction()}
    )

    result = json.loads(
        run(
            chain_reconcile.run(
                chain_config(transaction_hash=CHAIN_SIGNATURE),
                None,
            )
        )
    )

    assert result["status"] == "confirmed"
    assert [method for method, _ in calls] == ["getTransaction"]


@pytest.mark.parametrize(
    ("operation_kind", "direction", "pre_amount", "post_amount"),
    [
        ("lp_open", "decrease", "8808958", "6989206"),
        ("lp_close", "increase", "6989206", "8808958"),
    ],
)
def test_chain_reconcile_supports_lp_open_and_close(
    monkeypatch, operation_kind, direction, pre_amount, post_amount
):
    transaction = finalized_swap_transaction()
    transaction["meta"]["preTokenBalances"][0]["uiTokenAmount"]["amount"] = pre_amount
    transaction["meta"]["postTokenBalances"][0]["uiTokenAmount"]["amount"] = post_amount
    install_chain_rpc(monkeypatch, {CHAIN_SIGNATURE: transaction})
    config = chain_config(
        operation_kind=operation_kind,
        required_accounts=[ORCA_POOL],
        required_program_ids=[ORCA_PROGRAM],
        asset_changes=[
            {
                "mint": USDC,
                "decimals": 6,
                "direction": direction,
                "minimum_amount": "1",
                "maximum_amount": "2",
            }
        ],
    )

    result = json.loads(run(chain_reconcile.run(config, None)))

    assert result["status"] == "confirmed"
    assert result["operation_kind"] == operation_kind
    assert result["matches"][0]["matched_accounts"] == [ORCA_POOL]
    assert result["matches"][0]["matched_program_ids"] == [ORCA_PROGRAM]


def test_chain_reconcile_rejects_a_balance_near_match(monkeypatch):
    calls = install_chain_rpc(
        monkeypatch,
        {CHAIN_SIGNATURE: finalized_swap_transaction(gmt_raw="277999999999")},
    )

    result = json.loads(run(chain_reconcile.run(chain_config(), None)))

    assert result["status"] == "not_found"
    assert result["matches"] == []
    assert result["retry_allowed"] is False
    assert [method for method, _ in calls] == [
        "getSignaturesForAddress",
        "getTransaction",
    ]


def test_chain_reconcile_never_selects_between_multiple_exact_matches(monkeypatch):
    second = finalized_swap_transaction(block_time=1785423830)
    second["transaction"]["signatures"] = [SECOND_CHAIN_SIGNATURE]
    rows = [
        {"signature": CHAIN_SIGNATURE, "blockTime": 1785423829, "err": None},
        {"signature": SECOND_CHAIN_SIGNATURE, "blockTime": 1785423830, "err": None},
    ]
    install_chain_rpc(
        monkeypatch,
        {
            CHAIN_SIGNATURE: finalized_swap_transaction(),
            SECOND_CHAIN_SIGNATURE: second,
        },
        rows=rows,
    )

    result = json.loads(run(chain_reconcile.run(chain_config(), None)))

    assert result["status"] == "ambiguous"
    assert len(result["matches"]) == 2
    assert result["retry_allowed"] is False


def test_chain_reconcile_fails_closed_when_signature_page_is_truncated(monkeypatch):
    rows = [
        {"signature": CHAIN_SIGNATURE, "blockTime": 1785423830, "err": None},
        {"signature": SECOND_CHAIN_SIGNATURE, "blockTime": 1785423829, "err": None},
    ]
    calls = install_chain_rpc(
        monkeypatch,
        {},
        rows=rows,
    )

    result = json.loads(run(chain_reconcile.run(chain_config(signature_limit=2), None)))

    assert result["status"] == "unavailable"
    assert result["search_truncated"] is True
    assert result["matches"] == []
    assert [method for method, _ in calls] == ["getSignaturesForAddress"]


def test_chain_reconcile_requires_exact_match_inputs():
    values = chain_config().model_dump()
    values["required_program_ids"] = []
    with pytest.raises(ValidationError):
        chain_reconcile.Config(**values)

    values = chain_config().model_dump()
    values["asset_changes"] = values["asset_changes"][:1]
    with pytest.raises(ValidationError, match="input and output"):
        chain_reconcile.Config(**values)


def test_portfolio_limits_flags_each_exact_executor_independently(
    tmp_path, monkeypatch
):
    now = 10_000
    changed = session_config()
    changed.update(
        executor_max_age_minutes=60,
        executor_take_profit_net_pnl_ratio=0.05,
        executor_stop_loss_net_pnl_ratio=0.04,
    )
    write_session(tmp_path, monkeypatch, changed, started_at=now - 30 * 60)
    rows = [
        active_row(
            "pool-age",
            "executor-age",
            timestamp=now - 61 * 60,
        ),
        active_row(
            "pool-profit",
            "executor-profit",
            timestamp=now - 10 * 60,
            net_pnl_pct="0.05",
        ),
        active_row(
            "pool-loss",
            "executor-loss",
            timestamp=now - 10 * 60,
            net_pnl_pct="-0.04",
        ),
        active_row(
            "pool-healthy",
            "executor-healthy",
            timestamp=now - 10 * 60,
            net_pnl_pct="0.01",
        ),
    ]
    executors = MockExecutors(ready_plan(), rows)
    install_client(monkeypatch, MockClient(executors))
    install_runtime(monkeypatch)
    monkeypatch.setattr(portfolio_limits.time, "time", lambda: now)

    result = json.loads(
        run(
            portfolio_limits.run(
                portfolio_limits.Config(controller_id="lp_expert.orca_1"),
                None,
            )
        )
    )

    assert result["status"] == "complete"
    assert result["mutation"] is False
    assert result["report_id"] == "lp_portfolio_limits-report"
    assert set(result["triggered_executor_ids"]) == {
        "executor-age",
        "executor-profit",
        "executor-loss",
    }
    assert set(result["close_required_executor_ids"]) == {
        "executor-age",
        "executor-profit",
        "executor-loss",
    }
    assert result["reconcile_required_executor_ids"] == []
    triggers = {row["executor_id"]: row["triggered_by"] for row in result["executors"]}
    assert triggers == {
        "executor-age": ["max_age"],
        "executor-profit": ["take_profit"],
        "executor-loss": ["stop_loss"],
        "executor-healthy": [],
    }
    assert executors.search_calls == [
        {
            "account_names": ["master_account"],
            "executor_types": ["lp_executor"],
            "controller_ids": ["lp_expert.orca_1"],
            "limit": 1000,
            "cursor": None,
        }
    ]


def test_portfolio_limits_latches_elapsed_session_and_closes_every_active_executor(
    tmp_path, monkeypatch
):
    now = 10_000
    changed = session_config()
    changed["session_max_age_minutes"] = 30
    write_session(tmp_path, monkeypatch, changed, started_at=now - 31 * 60)
    rows = [
        active_row("pool-a", "executor-a", timestamp=now - 10 * 60),
        active_row("pool-b", "executor-b", timestamp=now - 5 * 60),
    ]
    executors = MockExecutors(ready_plan(), rows)
    install_client(monkeypatch, MockClient(executors))
    install_runtime(monkeypatch)
    monkeypatch.setattr(portfolio_limits.time, "time", lambda: now)

    result = json.loads(
        run(
            portfolio_limits.run(
                portfolio_limits.Config(controller_id="lp_expert.orca_1"),
                None,
            )
        )
    )

    assert result["status"] == "complete"
    assert Decimal(result["session"]["age_minutes"]) == 31
    assert result["session"]["triggered_by"] == ["max_age"]
    assert result["session"]["stop_latched"] is True
    assert result["triggered_executor_ids"] == []
    assert set(result["close_required_executor_ids"]) == {
        "executor-a",
        "executor-b",
    }


def test_portfolio_limits_aggregates_terminal_and_active_pnl_for_session_limit(
    tmp_path, monkeypatch
):
    now = 10_000
    write_session(tmp_path, monkeypatch, started_at=now - 10 * 60)
    active = active_row(
        "pool-active",
        "executor-active",
        timestamp=now - 5 * 60,
        net_pnl_quote="0.2",
    )
    terminal = active_row(
        "pool-terminal",
        "executor-terminal",
        timestamp=now - 8 * 60,
        net_pnl_quote="0.3",
    )
    terminal.update(status="TERMINATED", is_active=False)
    executors = MockExecutors(ready_plan(), [active, terminal])
    install_client(monkeypatch, MockClient(executors))
    install_runtime(monkeypatch)
    monkeypatch.setattr(portfolio_limits.time, "time", lambda: now)

    result = json.loads(
        run(
            portfolio_limits.run(
                portfolio_limits.Config(controller_id="lp_expert.orca_1"),
                None,
            )
        )
    )

    assert result["status"] == "complete"
    assert result["session"]["net_pnl_quote"] == "0.5"
    assert result["session"]["triggered_by"] == ["take_profit"]
    assert result["session"]["stop_latched"] is True
    assert result["close_required_executor_ids"] == ["executor-active"]


def test_portfolio_limits_keeps_elapsed_session_latched_after_portfolio_is_flat(
    tmp_path, monkeypatch
):
    now = 10_000
    changed = session_config()
    changed["session_max_age_minutes"] = 30
    write_session(tmp_path, monkeypatch, changed, started_at=now - 31 * 60)
    install_client(monkeypatch, MockClient(MockExecutors(ready_plan(), [])))
    install_runtime(monkeypatch)
    monkeypatch.setattr(portfolio_limits.time, "time", lambda: now)

    result = json.loads(
        run(
            portfolio_limits.run(
                portfolio_limits.Config(controller_id="lp_expert.orca_1"),
                None,
            )
        )
    )

    assert result["status"] == "complete"
    assert result["session"]["stop_latched"] is True
    assert result["session"]["triggered_by"] == ["max_age"]
    assert result["executors"] == []
    assert result["close_required_executor_ids"] == []


def test_portfolio_limits_never_requests_an_already_closing_executor_stop(
    tmp_path, monkeypatch
):
    write_session(tmp_path, monkeypatch, started_at=1_500)
    row = active_row(
        "pool-closing",
        "executor-closing",
        timestamp=1_000,
        net_pnl_pct="-0.05",
    )
    row["custom_info"]["state"] = "CLOSING"
    executors = MockExecutors(ready_plan(), [row])
    install_client(monkeypatch, MockClient(executors))
    install_runtime(monkeypatch)
    monkeypatch.setattr(portfolio_limits.time, "time", lambda: 2_000)

    result = json.loads(
        run(
            portfolio_limits.run(
                portfolio_limits.Config(controller_id="lp_expert.orca_1"),
                None,
            )
        )
    )

    assert result["status"] == "complete"
    assert result["triggered_executor_ids"] == ["executor-closing"]
    assert result["close_required_executor_ids"] == []
    assert result["reconcile_required_executor_ids"] == ["executor-closing"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("timestamp", None, "timestamp"),
        ("net_pnl_pct", None, "net PnL"),
        ("timestamp", 10_001, "future"),
    ],
)
def test_portfolio_limits_rejects_incomplete_or_contradictory_evidence(
    tmp_path, monkeypatch, field, value, reason
):
    write_session(tmp_path, monkeypatch, started_at=9_000)
    row = active_row("pool-1", timestamp=9_000)
    if value is None:
        row.pop(field)
    else:
        row[field] = value
    executors = MockExecutors(ready_plan(), [row])
    install_client(monkeypatch, MockClient(executors))
    install_runtime(monkeypatch)
    monkeypatch.setattr(portfolio_limits.time, "time", lambda: 10_000)

    result = json.loads(
        run(
            portfolio_limits.run(
                portfolio_limits.Config(controller_id="lp_expert.orca_1"),
                None,
            )
        )
    )

    assert result["status"] == "rejected"
    assert result["mutation"] is False
    assert reason in result["reason"]
    assert result["report_id"] == "lp_portfolio_limits-report"


def test_guard_submits_once_with_top_level_controller(tmp_path, monkeypatch):
    plan = ready_plan()
    write_session(tmp_path, monkeypatch)
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "submitted"
    assert result["report_id"] == "lp_create_guard-report"
    assert result["report_error"] is None
    assert result["executor_id"] == "executor-new"
    assert len(executors.create_calls) == 1
    assert executors.create_calls[0]["controller_id"] == "lp_expert.orca_1"
    assert (
        executors.create_calls[0]["executor_config"]["controller_id"]
        == "lp_expert.orca_1"
    )
    assert executors.create_calls[0]["executor_config"]["keep_position"] is False
    assert not {
        "executor_max_age_minutes",
        "executor_take_profit_net_pnl_ratio",
        "executor_stop_loss_net_pnl_ratio",
        "time_limit",
        "take_profit",
        "stop_loss",
        "triple_barrier_config",
    } & set(executors.create_calls[0]["executor_config"])


def test_non_scan_report_failure_preserves_all_routine_outcomes(tmp_path, monkeypatch):
    async def fail_report(**values):
        raise OSError("report storage unavailable")

    monkeypatch.setattr(routine_reports, "save_trace", fail_report)
    plan = ready_plan()
    assert plan["status"] == "planned"
    assert plan["report_id"] is None
    assert plan["report_error"] == "OSError: report storage unavailable"

    client = MockClient(MockExecutors(plan))
    install_client(monkeypatch, client)
    install_runtime(monkeypatch)
    scope = json.loads(run(gateway_swap.run(gateway_config(), None)))
    assert scope["status"] == "ready"
    assert scope["report_id"] is None
    assert scope["report_error"] == "OSError: report storage unavailable"

    install_chain_rpc(monkeypatch, {CHAIN_SIGNATURE: finalized_swap_transaction()})
    chain = json.loads(run(chain_reconcile.run(chain_config(), None)))
    assert chain["status"] == "confirmed"
    assert chain["report_id"] is None
    assert chain["report_error"] == "OSError: report storage unavailable"

    write_session(tmp_path, monkeypatch)
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "submitted"
    assert result["report_id"] is None
    assert result["report_error"] == "OSError: report storage unavailable"
    assert len(client.executors.create_calls) == 1


def test_report_cancellation_does_not_cancel_planner_result(monkeypatch):
    async def cancel_report(**values):
        raise asyncio.CancelledError

    monkeypatch.setattr(routine_reports, "save_trace", cancel_report)
    plan = initial_plan()
    assert plan["status"] == "planned"
    assert plan["report_id"] is None
    assert plan["report_error"] == "CancelledError: report save was cancelled"


def test_gateway_and_guard_reports_capture_decision_evidence(tmp_path, monkeypatch):
    plan = ready_plan()
    traces = []

    async def capture_trace(**values):
        traces.append(values)
        return f"{values['source']}-captured"

    monkeypatch.setattr(routine_reports, "save_trace", capture_trace)
    swap = MockGatewaySwap()
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors, gateway_swap=swap))
    install_runtime(monkeypatch)
    gateway_result = json.loads(run(gateway_swap.run(gateway_config("execute"), None)))
    assert gateway_result["status"] == "confirmed"
    gateway_trace = traces[-1]
    assert gateway_trace["source"] == "gateway_swap"
    assert {row["kind"] for row in gateway_trace["evidence"]} >= {
        "binding",
        "quote",
        "receipt",
    }

    write_session(tmp_path, monkeypatch)
    guard_result = json.loads(run(guard.run(guard_config(plan), None)))
    assert guard_result["status"] == "submitted"
    guard_trace = traces[-1]
    assert guard_trace["source"] == "lp_create_guard"
    assert {row["kind"] for row in guard_trace["evidence"]} >= {
        "plan",
        "session_limits",
        "schema",
        "capacity",
        "balances",
        "executor",
    }


def test_planner_saves_inspectable_sanitized_report(tmp_path, monkeypatch):
    from condor import reports as condor_reports

    monkeypatch.setattr(routine_reports, "save_trace", REAL_SAVE_TRACE)
    monkeypatch.setattr(condor_reports, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(condor_reports, "INDEX_FILE", tmp_path / "reports_index.json")
    plan = initial_plan()
    assert plan["report_id"]
    entries = json.loads((tmp_path / "reports_index.json").read_text())
    report = next(item for item in entries if item["id"] == plan["report_id"])
    html = (tmp_path / report["filename"]).read_text()
    assert "Orca LP Position Plan" in html
    assert "pool-1" in html
    assert "wallet_address" not in html
    assert "master_account" not in html

    sanitized_id = run(
        REAL_SAVE_TRACE(
            title="Sanitizer Check",
            source="sanitizer_check",
            status="ready",
            summary={
                "wallet_address": "wallet-secret",
                "note": "https://private.invalid/path api_key=key-secret",
            },
        )
    )
    sanitized_report = next(
        item
        for item in json.loads((tmp_path / "reports_index.json").read_text())
        if item["id"] == sanitized_id
    )
    sanitized_html = (tmp_path / sanitized_report["filename"]).read_text()
    assert "wallet-secret" not in sanitized_html
    assert "private.invalid" not in sanitized_html
    assert "key-secret" not in sanitized_html
    assert "[redacted]" in sanitized_html


def test_guard_accepts_engine_default_for_omitted_shutdown_drawdown(
    tmp_path, monkeypatch
):
    plan = ready_plan()
    config = session_config()
    del config["risk_limits"]["shutdown_drawdown_pct"]
    write_session(tmp_path, monkeypatch, config)
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "submitted"
    assert len(executors.create_calls) == 1


def test_guard_accepts_configured_slippage_and_drawdown_limits(tmp_path, monkeypatch):
    plan = ready_plan(
        range_half_width_pct="12",
        minimum_range_half_width_pct="1",
        maximum_range_half_width_pct="15",
        rebalance_threshold_pct="2",
        max_slippage_pct="2",
    )
    config = session_config()
    config["max_slippage_pct"] = 2
    config["minimum_range_half_width_pct"] = 1
    config["maximum_range_half_width_pct"] = 15
    config["rebalance_threshold_pct"] = 2
    config["default_risk_posture"] = "steady"
    config["risk_limits"]["max_drawdown_pct"] = 5
    config["risk_limits"]["shutdown_drawdown_pct"] = 10
    write_session(tmp_path, monkeypatch, config)
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "submitted"
    assert len(executors.create_calls) == 1


def test_guard_allows_second_distinct_configured_executor(tmp_path, monkeypatch):
    plan = ready_plan("pool-2")
    write_session(tmp_path, monkeypatch)
    executors = MockExecutors(plan, [active_row("pool-1")])
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "submitted"
    assert len(executors.create_calls) == 1


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([active_row("pool-1"), active_row("pool-2", "executor-2")], "capacity"),
        ([active_row("pool-3")], "one-position-per-pool"),
    ],
)
def test_guard_rejects_capacity_and_same_pool(tmp_path, monkeypatch, rows, reason):
    plan = ready_plan("pool-3")
    write_session(tmp_path, monkeypatch)
    executors = MockExecutors(plan, rows)
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "rejected"
    assert reason in result["reason"]
    assert not executors.create_calls


def test_guard_rejects_foreign_live_scope(tmp_path, monkeypatch):
    plan = ready_plan()
    row = active_row("other-pool")
    row["controller_id"] = "another.agent_1"
    write_session(tmp_path, monkeypatch)
    executors = MockExecutors(plan, [row])
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "rejected"
    assert "overlaps" in result["reason"]


def test_guard_fails_closed_on_schema_drift_or_tampered_plan(tmp_path, monkeypatch):
    plan = ready_plan()
    write_session(tmp_path, monkeypatch)
    executors = MockExecutors(plan, missing_schema_field="quote_amount")
    install_client(monkeypatch, MockClient(executors))
    assert json.loads(run(guard.run(guard_config(plan), None)))["status"] == "rejected"
    assert not executors.create_calls
    changed = copy.deepcopy(plan)
    changed["executor_config"]["pool_address"] = "tampered"
    executors = MockExecutors(changed)
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(changed), None)))
    assert result["status"] == "rejected"
    assert "digest" in result["reason"]


def test_guard_uses_configured_three_slot_capacity(tmp_path, monkeypatch):
    plan_1 = ready_plan("pool-1", amount_quote="3")
    plan_2 = ready_plan("pool-2", amount_quote="3")
    plan_3 = ready_plan("pool-3", amount_quote="3")
    changed = session_config()
    changed.update(
        total_amount_quote=9,
        max_open_executors=3,
        min_quote_per_executor=3,
        max_quote_per_executor=3,
    )
    changed["risk_limits"]["max_open_executors"] = 3
    write_session(tmp_path, monkeypatch, changed)
    executors = MockExecutors(
        plan_3,
        [
            active_row("pool-1", "executor-1", plan_1),
            active_row("pool-2", "executor-2", plan_2),
        ],
    )
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan_3), None)))
    assert result["status"] == "submitted"
    assert len(executors.create_calls) == 1


def test_guard_rejects_incoherent_configured_capacity(tmp_path, monkeypatch):
    plan = ready_plan("pool-1", amount_quote="3")
    changed = session_config()
    changed.update(
        total_amount_quote=5,
        max_open_executors=2,
        min_quote_per_executor=3,
        max_quote_per_executor=3,
    )
    write_session(tmp_path, monkeypatch, changed)
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "rejected"
    assert "configured session safety" in result["reason"]


@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_guard_rejects_invalid_per_tick_deployment_cap(tmp_path, monkeypatch, value):
    plan = ready_plan()
    changed = session_config()
    changed["max_slot_deployments_per_tick"] = value
    write_session(tmp_path, monkeypatch, changed)
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "rejected"
    assert "max slot deployments per tick" in result["reason"]
    assert not executors.create_calls


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executor_max_age_minutes", 0),
        ("executor_max_age_minutes", 1.5),
        ("executor_take_profit_net_pnl_ratio", 0),
        ("executor_stop_loss_net_pnl_ratio", -0.01),
    ],
)
def test_guard_rejects_invalid_executor_exit_limits(
    tmp_path, monkeypatch, field, value
):
    plan = ready_plan()
    changed = session_config()
    changed[field] = value
    write_session(tmp_path, monkeypatch, changed)
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))

    result = json.loads(run(guard.run(guard_config(plan), None)))

    assert result["status"] == "rejected"
    assert "executor" in result["reason"]
    assert not executors.create_calls


def test_guard_rejects_total_budget_above_condor_risk_cap(tmp_path, monkeypatch):
    plan = ready_plan()
    changed = session_config()
    changed.update(
        total_amount_quote=12,
        min_quote_per_executor=5,
        max_quote_per_executor=6,
    )
    write_session(tmp_path, monkeypatch, changed)
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "rejected"
    assert "configured session safety" in result["reason"]


def test_guard_submits_once_in_live_run_once_without_session_file(monkeypatch):
    plan = ready_plan()
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    install_runtime(monkeypatch, "run_once")
    result = json.loads(
        run(
            guard.run(
                guard_config(plan, controller_id="lp_expert.orca_e1"),
                None,
            )
        )
    )
    assert result["status"] == "submitted"
    assert result["controller_id"] == "lp_expert.orca_e1"
    assert len(executors.create_calls) == 1


def test_guard_run_once_without_full_runtime_config_never_submits(monkeypatch):
    plan = ready_plan()
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    install_runtime(monkeypatch, "run_once", include_strategy_config=False)
    result = json.loads(
        run(
            guard.run(
                guard_config(plan, controller_id="lp_expert.orca_e1"),
                None,
            )
        )
    )
    assert result["status"] == "rejected"
    assert "config authority is unavailable" in result["reason"]
    assert not executors.create_calls


def test_guard_rejects_dry_run_experiment_without_submitting(monkeypatch):
    plan = ready_plan()
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    install_runtime(monkeypatch, "dry_run")
    result = json.loads(
        run(
            guard.run(
                guard_config(plan, controller_id="lp_expert.orca_e1"),
                None,
            )
        )
    )
    assert result["status"] == "rejected"
    assert "run-once" in result["reason"]
    assert not executors.create_calls


def test_guard_rejects_insufficient_sol_reserve(tmp_path, monkeypatch):
    plan = ready_plan()
    write_session(tmp_path, monkeypatch)
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors, sol="0.01"))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "rejected"
    assert not executors.create_calls


def test_guard_rejects_preparation_spend_above_slot_budget(tmp_path, monkeypatch):
    plan = ready_plan()
    write_session(tmp_path, monkeypatch)
    executors = MockExecutors(plan)
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(
        run(
            guard.run(
                guard_config(plan, preparation_quote_spent="5"),
                None,
            )
        )
    )
    assert result["status"] == "rejected"
    assert not executors.create_calls


def test_guard_marks_submission_exception_uncertain_without_retry(
    tmp_path, monkeypatch
):
    plan = ready_plan()
    write_session(tmp_path, monkeypatch)
    executors = MockExecutors(plan, create_error=TimeoutError("unknown submission"))
    install_client(monkeypatch, MockClient(executors))
    result = json.loads(run(guard.run(guard_config(plan), None)))
    assert result["status"] == "uncertain"
    assert result["retry_allowed"] is False
    assert len(executors.create_calls) == 1


def test_mocked_two_executor_close_targets_one_and_restores_exact_residual():
    positions = {
        "executor-1": {"status": "RUNNING", "base": "0.01"},
        "executor-2": {"status": "RUNNING", "base": "0.02"},
    }
    before_second = copy.deepcopy(positions["executor-2"])
    operations = [
        {"tool": "journal", "target": "executor-1"},
        {"tool": "stop", "target": "executor-1", "keep_position": False},
        {
            "tool": "reconcile",
            "target": "executor-1",
            "native_swap": "FAILED",
            "residual": "0.004",
        },
        {"tool": "jupiter_sell", "target": "executor-1", "amount": "0.004"},
    ]
    positions["executor-1"] = {"status": "COMPLETE", "base": "0"}
    assert all(item["target"] == "executor-1" for item in operations)
    assert operations[-1]["amount"] == operations[-2]["residual"]
    assert positions["executor-2"] == before_second


def test_only_minimal_current_session_receipt_state_and_no_cross_agent_imports():
    sources = {path.name: path.read_text() for path in (ROOT / "routines").glob("*.py")}
    source = "\n".join(sources.values())
    assert "lp_wizard" not in source
    assert "lpmaxxing" not in source
    assert "lifecycle.json" not in source
    assert "state.json" not in source
    writers = {name for name, value in sources.items() if "write_text(" in value}
    assert writers == {"_gateway_operation_receipt.py"}
    assert "gateway_operations" in sources["_gateway_operation_receipt.py"]
    assert "create_executor(" not in sources["orca_pool_scan.py"]
    assert "create_executor(" not in sources["clmm_position_plan.py"]
    assert "create_executor(" not in sources["lp_portfolio_limits.py"]
    assert "stop_executor(" not in sources["lp_portfolio_limits.py"]
    assert "execute_swap(" not in sources["solana_transaction_reconcile.py"]
    assert "create_executor(" not in sources["solana_transaction_reconcile.py"]
    assert "stop_executor(" not in sources["solana_transaction_reconcile.py"]
    assert sources["lp_create_guard.py"].count("create_executor(") == 1
    assert sources["gateway_swap.py"].count("manage_gateway_swaps(") == 1
    shared_gateway = sources["gateway_swap.py"].lower()
    assert all(
        strategy_term not in shared_gateway
        for strategy_term in ("orca", "meteora", "jupiter", "solana", "usdc")
    )
