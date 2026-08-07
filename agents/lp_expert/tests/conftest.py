from __future__ import annotations

import copy
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from agents.lp_expert.core.orca import USDC_MINT
from agents.lp_expert.core.runtime import RuntimeScope

SOL_MINT = "So11111111111111111111111111111111111111112"
WALLET = "Cs8TbwfyEN1paECV8aarY4oi9FMevWDBdEdb2afxpWgP"
POOL = "7PNQ9rfSGCbCC3XTeL6CwwAzevqQGvKXeXMxP2TjS7rM"


def strategy_config(**overrides):
    value = {
        "execution_mode": "loop",
        "server_name": "local",
        "account_name": "master_account",
        "default_risk_posture": "balanced",
        "total_amount_quote": 12,
        "max_open_executors": 3,
        "max_slot_deployments_per_tick": 1,
        "candidate_scan_limit": 3,
        "min_quote_per_executor": 3,
        "max_quote_per_executor": 4,
        "max_slippage_pct": 1,
        "min_sol_reserve": 0.1,
        "use_existing_base_inventory": True,
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
        "risk_limits": {
            "max_position_size_quote": 12,
            "max_open_executors": 3,
            "max_drawdown_pct": -1,
            "shutdown_drawdown_pct": -1,
        },
    }
    value.update(overrides)
    return value


def runtime_scope(
    tmp_path: Path,
    *,
    mode: str = "loop",
    tick: int = 2,
    config: dict | None = None,
) -> RuntimeScope:
    values = copy.deepcopy(config or strategy_config(execution_mode=mode))
    return RuntimeScope(
        controller_id=("lp_expert.orca_1" if mode == "loop" else "lp_expert.orca_e1"),
        strategy_slug="orca",
        execution_mode=mode,
        server_name="local",
        account_name="master_account",
        network="solana-mainnet-beta",
        swap_connector="jupiter",
        quote_symbol="USDC",
        quote_mint=USDC_MINT,
        quote_decimals=6,
        tick_count=tick - 1,
        current_tick=tick,
        tick_started_at=1_000.0,
        last_tick_at=1_000.0,
        session_dir=tmp_path if mode == "loop" else None,
        config=MappingProxyType(values),
        wallet_address=WALLET,
    )


def executor_row(
    executor_id: str = "executor-1",
    *,
    controller_id: str = "lp_expert.orca_1",
    pool_address: str = POOL,
    state: str = "IN_RANGE",
    status: str = "RUNNING",
    timestamp: float = 1_000,
    net_pnl_pct: str = "0",
    net_pnl_quote: str = "0",
    base_mint: str = SOL_MINT,
) -> dict:
    active = status.upper() not in {
        "COMPLETE",
        "COMPLETED",
        "TERMINATED",
        "CANCELED",
        "CANCELLED",
        "CLOSED",
        "STOPPED",
    }
    return {
        "executor_id": executor_id,
        "account_name": "master_account",
        "controller_id": controller_id,
        "status": status,
        "is_active": active,
        "timestamp": timestamp,
        "net_pnl_pct": net_pnl_pct,
        "net_pnl_quote": net_pnl_quote,
        "config": {
            "type": "lp_executor",
            "controller_id": controller_id,
            "connector_name": "solana-mainnet-beta",
            "lp_provider": "orca/clmm",
            "trading_pair": "SOL-USDC",
            "pool_address": pool_address,
            "base_symbol": "SOL",
            "base_mint": base_mint,
            "base_decimals": 9,
            "base_amount": "0.01",
            "quote_amount": "2",
            "lower_price": "160",
            "upper_price": "200",
        },
        "custom_info": {
            "state": state,
            "pool_address": pool_address,
            "position_address": f"position-{executor_id}",
            "base_symbol": "SOL",
            "base_mint": base_mint,
            "base_decimals": 9,
        },
    }


def terminal_row(
    executor_id: str = "executor-closed",
    *,
    controller_id: str = "lp_expert.orca_1",
    pool_address: str = POOL,
    residual: str = "0",
    native_status: str = "CONFIRMED",
    base_mint: str = SOL_MINT,
) -> dict:
    row = executor_row(
        executor_id,
        controller_id=controller_id,
        pool_address=pool_address,
        status="CLOSED",
        state="CLOSED",
        base_mint=base_mint,
    )
    row["custom_info"].update(
        {
            "residual_base_amount": residual,
            "native_swap_status": native_status,
            "close_transaction_hash": f"tx-close-{executor_id}",
        }
    )
    return row


def prior_close(
    executor_id: str = "executor-closed",
    *,
    closed_tick: int = 1,
    pool_address: str = POOL,
    base_mint: str = SOL_MINT,
    stop_proof: dict | None = None,
) -> dict:
    return {
        "executor_id": executor_id,
        "closed_tick": closed_tick,
        "position_address": f"position-{executor_id}",
        "pool_address": pool_address,
        "base_symbol": "SOL",
        "base_mint": base_mint,
        "base_decimals": 9,
        "valuation_price": "180",
        "stop_proof": stop_proof
        or {
            "tick": closed_tick,
            "controller_id": "lp_expert.orca_1",
            "executor_id": executor_id,
            "keep_position": False,
        },
    }


def pool_record(address: str = POOL) -> dict:
    return {
        "address": address,
        "tokenA": {"symbol": "SOL", "mint": SOL_MINT, "decimals": 9},
        "tokenB": {"symbol": "USDC", "mint": USDC_MINT, "decimals": 6},
        "price": 180,
        "tvlUsdc": 100_000,
        "tickSpacing": 64,
        "feeRate": 3000,
        "adaptiveFeeEnabled": False,
        "hasWarning": False,
        "updatedAt": "2026-08-06T00:00:00Z",
        "stats": {
            window: {"fees": fee, "volume": volume, "priceDelta": change}
            for window, fee, volume, change in zip(
                ("1h", "4h", "24h", "7d"),
                (10, 35, 180, 1000),
                (1000, 5000, 30000, 150000),
                (0.001, 0.004, 0.01, 0.03),
                strict=True,
            )
        },
    }


@pytest.fixture
def fake_engine(tmp_path):
    config = strategy_config()
    strategy_dir = tmp_path / "orca"
    session_dir = strategy_dir / "sessions" / "session_1"
    session_dir.mkdir(parents=True)
    (session_dir / "config.yml").write_text(
        "\n".join(
            [
                "execution_mode: loop",
                "server_name: local",
                "account_name: master_account",
                "default_risk_posture: balanced",
                "total_amount_quote: 12",
                "max_open_executors: 3",
                "max_slot_deployments_per_tick: 1",
                "candidate_scan_limit: 3",
                "min_quote_per_executor: 3",
                "max_quote_per_executor: 4",
                "max_slippage_pct: 1",
                "min_sol_reserve: 0.1",
                "use_existing_base_inventory: true",
                "residual_base_dust_quote: 0.01",
                "minimum_range_half_width_pct: 0.5",
                "maximum_range_half_width_pct: 20",
                "rebalance_threshold_pct: 1",
                "executor_max_age_minutes: 1440",
                "executor_take_profit_net_pnl_ratio: 0.05",
                "executor_stop_loss_net_pnl_ratio: 0.05",
                "session_max_age_minutes: 1440",
                "session_take_profit_net_pnl_ratio: 0.05",
                "session_stop_loss_net_pnl_ratio: 0.05",
                "risk_limits:",
                "  max_position_size_quote: 12",
                "  max_open_executors: 3",
                "  max_drawdown_pct: -1",
                "  shutdown_drawdown_pct: -1",
            ]
        )
    )
    return SimpleNamespace(
        config=config,
        session_dir=session_dir,
        strategy=SimpleNamespace(slug="orca", dir=strategy_dir),
        agent=SimpleNamespace(slug="lp_expert"),
        agent_id="lp_expert.orca_1",
        status="running",
        journal=SimpleNamespace(tick_count=1),
        get_info=lambda: {
            "agent_id": "lp_expert.orca_1",
            "strategy_slug": "orca",
            "status": "running",
            "server_name": "local",
        },
    )
