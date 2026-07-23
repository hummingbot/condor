"""Safety tests for the ANSEM/SOL CLMM monitoring routine."""

import asyncio
import json
from types import SimpleNamespace

from agents.clmm_manager.routines import clmm_monitor as monitor
from condor.agents.agent import AgentStore
from condor.agents.strategy import StrategyStore


def _pool() -> dict:
    return {
        "address": monitor.ANSEM_SOL_POOL,
        "name": "ANSEM-SOL",
        "current_price": 0.0025,
        "tvl": 1_000_000,
        "is_blacklisted": False,
        "pool_config": {"bin_step": 20},
        "token_x": {
            "address": monitor.ANSEM_MINT,
            "symbol": "ANSEM",
            "price": 0.2,
        },
        "token_y": {
            "address": monitor.WSOL_MINT,
            "symbol": "SOL",
            "price": 80.0,
        },
        "volume": {"24h": 2_000_000},
        "fees": {"24h": 4_000},
        "fee_tvl_ratio": {"24h": 0.4},
    }


def _ohlcv() -> dict:
    return {
        "data": [
            {
                "timestamp": 1,
                "open": 0.00245,
                "high": 0.00255,
                "low": 0.0024,
                "close": 0.0025,
            },
            {
                "timestamp": 2,
                "open": 0.0025,
                "high": 0.0026,
                "low": 0.00245,
                "close": 0.0025,
            },
        ]
    }


def _install_run_fakes(monkeypatch, positions):
    async def get_client(*_args, **_kwargs):
        return object()

    async def get_pool(_config):
        return _pool()

    async def get_ohlcv(_config):
        return _ohlcv()

    async def get_positions(*_args, **_kwargs):
        if isinstance(positions, BaseException):
            raise positions
        return positions

    async def get_gateway_pool(*_args, **_kwargs):
        return None

    monkeypatch.setattr(monitor, "get_client", get_client)
    monkeypatch.setattr(monitor, "_get_official_pool_info", get_pool)
    monkeypatch.setattr(monitor, "_get_ohlcv", get_ohlcv)
    monkeypatch.setattr(monitor, "_get_positions", get_positions)
    monkeypatch.setattr(monitor, "_get_gateway_pool_info", get_gateway_pool)


def _run(config=None) -> dict:
    payload = asyncio.run(
        monitor.run(
            config or monitor.Config(),
            SimpleNamespace(_chat_id=1),
        )
    )
    return json.loads(payload)


def test_main_pool_range_is_capped_at_gateway_bin_limit():
    result = monitor._build_bin_range(0.0025, 20, 15.0, 68)

    assert result["capped_by_bin_limit"] is True
    assert result["requested_total_bins"] > 68
    assert result["total_bins"] == 68
    assert result["lower"] < 0.0025 < result["upper"]
    assert result["effective_lower_pct"] < 7
    assert result["effective_upper_pct"] < 7


def test_agent_and_strategy_metadata_load_with_stable_identity():
    agent = AgentStore().get("clmm_manager")
    strategy = StrategyStore().get("clmm_manager", "auto_rebalance")

    assert agent is not None
    assert strategy is not None
    assert strategy.slug == "auto_rebalance"
    assert strategy.key == "clmm_manager.auto_rebalance"
    assert strategy.default_config["target_usd"] == 100
    assert strategy.default_config["total_amount_quote"] == 1.5
    assert strategy.default_config["live_execution_enabled"] is False


def test_position_activity_rejects_closed_and_ambiguous_rows():
    assert monitor._position_activity({"status": "CLOSED", "liquidity": 100}) is False
    assert monitor._position_activity({"liquidity": "10"}) is True
    assert monitor._position_activity({"base_amount": 0, "quote_amount": 0}) is False
    assert monitor._position_activity({"status": "OPEN", "liquidity": 0}) is None
    assert monitor._position_activity({"status": "OPEN"}) is None


def test_position_lookup_failure_pauses_instead_of_opening(monkeypatch):
    _install_run_fakes(
        monkeypatch, monitor.MonitoringError("gateway position endpoint timed out")
    )

    result = _run()

    assert result["action"] == "pause"
    assert result["ready_to_create"] is False
    assert "timed out" in " ".join(result["errors"])


def test_healthy_empty_wallet_returns_usd_sized_two_sided_target(monkeypatch):
    _install_run_fakes(monkeypatch, [])

    result = _run()

    assert result["action"] == "no_position"
    assert result["ready_to_create"] is True
    assert result["pool"]["mints_verified"] is True
    assert result["suggested_range"]["total_bins"] <= 68
    assert result["suggested_range"]["capped_by_bin_limit"] is False
    assert result["target_allocation"]["target_usd"] == 100.0
    assert result["target_allocation"]["base_amount"] == 250.0
    assert result["target_allocation"]["quote_amount"] == 0.625
    assert result["target_allocation"]["quote_exposure"] == 1.25


def test_ambiguous_open_position_pauses_to_prevent_duplicate(monkeypatch):
    _install_run_fakes(monkeypatch, [{"status": "OPEN", "position_address": "p1"}])

    result = _run()

    assert result["action"] == "pause"
    assert result["ready_to_create"] is False
    assert "ambiguous" in " ".join(result["blockers"])


def test_wrong_mint_pauses_even_when_symbol_matches(monkeypatch):
    _install_run_fakes(monkeypatch, [])

    result = _run(monitor.Config(base_mint="fake-mint"))

    assert result["action"] == "pause"
    assert any("mint mismatch" in reason for reason in result["blockers"])
