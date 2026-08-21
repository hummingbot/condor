import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agents.multi_lp_rebalancer_manager.routines import (
    register_gateway_token as registration,
)
from agents.multi_lp_rebalancer_manager.routines import (
    snapshot_trend_aware_lp_bots as snapshot,
)

NAMESPACE = "multi_lp_rebalancer_manager-orca"
BOT = f"{NAMESPACE}-slot-1-20260813-120000"
CONFIG_ID = f"{NAMESPACE}-slot-1"


class _Controllers:
    async def get_bot_controller_configs(self, bot_name):
        assert bot_name == BOT
        return [
            {
                "id": CONFIG_ID,
                "_config_name": CONFIG_ID,
                "controller_type": "generic",
                "controller_name": "trend_aware_lp_rebalancer",
                "connector_name": "solana-mainnet-beta",
                "pool_address": "A" * 32,
                "base_token_mint": "So11111111111111111111111111111111111111112",
                "quote_token_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "trading_pair": "SOL-USDC",
                "lp_provider": "orca/clmm",
                "swap_provider": "jupiter/router",
                "total_amount_quote": 10,
                "controller_started_at": 1786550000,
                "market_trend": "SIDEWAYS",
                "trend_observed_at": 1786550100,
                "trend_signal_id": "signal",
                "trend_signal_max_age_seconds": 600,
                "position_width_pct": 2,
                "downside_offset_pct": 0,
                "rebalance_threshold_pct": 0.5,
                "defensive_rearm_cooldown_minutes": 5,
                "controller_take_profit_ratio": 0.05,
                "controller_stop_loss_ratio": 0.05,
                "controller_time_limit_minutes": 720,
                "controller_pnl_grace_period_minutes": 5,
                "manual_kill_switch": False,
            }
        ]


class _BotOrchestration:
    def __init__(self, performance, status="running"):
        self.performance = performance
        self.status = status

    async def get_active_bots_status(self):
        return {
            "status": "success",
            "data": {
                BOT: {
                    "status": self.status,
                    "recently_active": self.status == "running",
                    "performance": self.performance,
                }
            },
        }


class _EmptyBotOrchestration:
    async def get_active_bots_status(self):
        return {"status": "success", "data": {}}


def _envelope():
    return {
        "schema_version": 1,
        "lifecycle_state": "ACTIVE",
        "readiness_state": "READY",
        "identity": {
            "controller_id": CONFIG_ID,
            "connector_name": "solana-mainnet-beta",
            "pool_address": "A" * 32,
            "base_token_mint": "So11111111111111111111111111111111111111112",
            "quote_token_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "trading_pair": "SOL-USDC",
            "lp_provider": "orca/clmm",
            "swap_provider": "jupiter/router",
            "controller_started_at": 1786550000,
        },
        "policy": {
            "position_width_pct": 2,
            "downside_offset_pct": 0,
            "rebalance_threshold_pct": 0.5,
            "trend_signal_max_age_seconds": 600,
            "defensive_rearm_cooldown_minutes": 5,
            "take_profit_ratio": 0.05,
            "stop_loss_ratio": 0.05,
            "time_limit_minutes": 720,
            "pnl_grace_period_minutes": 5,
        },
        "lp_executor": {
            "id": "lp-full-id",
            "status": "RUNNING",
            "close_type": "NONE",
            "position_address": "P" * 44,
        },
        "order_executor": None,
        "trend": {
            "market_trend": "SIDEWAYS",
            "observed_at": 1786550100,
            "signal_id": "signal",
        },
        "failure": {"consecutive_count": 0},
        "exit": {"requested": False, "reason": "none", "completed": False},
        "inventory": {
            "assigned_quote": 10,
            "attributed_base": 0,
            "attributed_quote": 0,
        },
        "pnl": {
            "global_quote": 0.2,
            "ratio": 0.02,
            "lifetime_seconds": 360,
            "grace_remaining_seconds": 0,
        },
    }


def _snapshot_config(**changes):
    data = {
        "namespace": NAMESPACE,
        "controller_type": "generic",
        "controller_name": "trend_aware_lp_rebalancer",
        "expected_bots": [BOT],
    }
    data.update(changes)
    return snapshot.Config(**data)


def _controller(result, index=0):
    return dict(
        zip(snapshot.CONTROLLER_FIELDS, result["controllers"][index], strict=True)
    )


def _group(values, fields):
    return None if values is None else dict(zip(fields, values, strict=True))


def test_snapshot_config_accepts_only_exact_disjoint_timestamped_bot_sets():
    with pytest.raises(ValidationError, match="exact timestamped slot bot names"):
        _snapshot_config(expected_bots=[f"{NAMESPACE}-slot-1"])
    with pytest.raises(ValidationError, match="both live-expected and archive-pending"):
        _snapshot_config(archive_check_bots=[BOT])


def test_snapshot_preserves_exact_custom_lifecycle(monkeypatch):
    performance = {
        CONFIG_ID: {
            "status": "running",
            "performance": {"global_pnl_quote": 0.2},
            "custom_info": _envelope(),
        }
    }
    client = SimpleNamespace(
        bot_orchestration=_BotOrchestration(performance), controllers=_Controllers()
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    raw = asyncio.run(snapshot.run(_snapshot_config(), None))
    result = json.loads(raw)
    row = _controller(result)
    assert result["status"] == "complete"
    assert row["bot_name"] == BOT
    assert row["controller_id"] == CONFIG_ID
    assert row["slot"] == 1
    assert row["lifecycle_state"] == "ACTIVE"
    assert _group(row["lp_executor"], snapshot.LP_EXECUTOR_FIELDS)["id"] == "lp-full-id"
    assert row["telemetry_complete"] is True
    assert row["policy_matches_config"] is True
    assert row["lifecycle_coherent"] is True
    assert result["mutation"] is False
    assert len(raw) <= snapshot.MAX_RESULT_CHARS


def test_empty_namespace_without_expected_bots_is_complete(monkeypatch):
    class ControllersMustNotBeCalled:
        async def get_bot_controller_configs(self, bot_name):
            raise AssertionError("an empty namespace has no bot config to fetch")

    client = SimpleNamespace(
        bot_orchestration=_EmptyBotOrchestration(),
        controllers=ControllersMustNotBeCalled(),
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    config = snapshot.Config(
        namespace=NAMESPACE,
        controller_type="generic",
        controller_name="trend_aware_lp_rebalancer",
    )
    raw = asyncio.run(snapshot.run(config, None))
    result = json.loads(raw)

    assert result["status"] == "complete"
    assert result["owned_bot_count"] == 0
    assert result["controllers"] == []
    assert "expected_missing" not in result
    assert result["mutation"] is False
    assert len(raw) <= snapshot.MAX_RESULT_CHARS


def test_snapshot_rejects_nonexact_schema_and_identity(monkeypatch):
    envelope = _envelope()
    envelope["schema_version"] = "1"
    envelope["identity"]["pool_address"] = "B" * 32
    performance = {
        CONFIG_ID: {"status": "running", "performance": {}, "custom_info": envelope}
    }
    client = SimpleNamespace(
        bot_orchestration=_BotOrchestration(performance), controllers=_Controllers()
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    result = json.loads(asyncio.run(snapshot.run(_snapshot_config(), None)))
    row = _controller(result)
    assert result["status"] == "degraded"
    assert row["identity_matches_config"] is False
    assert row["telemetry_complete"] is False


def test_snapshot_rejects_active_lifecycle_without_exact_position(monkeypatch):
    envelope = _envelope()
    envelope["lp_executor"]["position_address"] = None
    performance = {
        CONFIG_ID: {"status": "running", "performance": {}, "custom_info": envelope}
    }
    client = SimpleNamespace(
        bot_orchestration=_BotOrchestration(performance), controllers=_Controllers()
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    result = json.loads(asyncio.run(snapshot.run(_snapshot_config(), None)))
    row = _controller(result)
    assert result["status"] == "degraded"
    assert row["lifecycle_coherent"] is False
    assert row["telemetry_complete"] is False


def test_snapshot_rejects_runtime_policy_that_did_not_reload(monkeypatch):
    envelope = _envelope()
    envelope["policy"]["position_width_pct"] = 3
    performance = {
        CONFIG_ID: {"status": "running", "performance": {}, "custom_info": envelope}
    }
    client = SimpleNamespace(
        bot_orchestration=_BotOrchestration(performance), controllers=_Controllers()
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    result = json.loads(asyncio.run(snapshot.run(_snapshot_config(), None)))
    row = _controller(result)
    assert result["status"] == "degraded"
    assert row["policy_matches_config"] is False
    assert row["telemetry_complete"] is False


def test_snapshot_rejects_bot_controller_slot_mismatch(monkeypatch):
    wrong_bot = f"{NAMESPACE}-slot-2-20260813-120000"

    class WrongSlotControllers(_Controllers):
        async def get_bot_controller_configs(self, bot_name):
            assert bot_name == wrong_bot
            return await super().get_bot_controller_configs(BOT)

    class WrongSlotOrchestration:
        async def get_active_bots_status(self):
            return {
                "status": "success",
                "data": {
                    wrong_bot: {
                        "status": "running",
                        "recently_active": True,
                        "performance": {
                            CONFIG_ID: {
                                "status": "running",
                                "performance": {},
                                "custom_info": _envelope(),
                            }
                        },
                    }
                },
            }

    client = SimpleNamespace(
        bot_orchestration=WrongSlotOrchestration(), controllers=WrongSlotControllers()
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    config = snapshot.Config(
        namespace=NAMESPACE,
        controller_type="generic",
        controller_name="trend_aware_lp_rebalancer",
    )
    result = json.loads(asyncio.run(snapshot.run(config, None)))
    row = _controller(result)
    assert result["status"] == "degraded"
    assert result["invalid_slot_bots"] == [wrong_bot]
    assert row["slot"] == 2
    assert row["telemetry_complete"] is False


def test_archive_release_requires_exact_archived_bot_run(monkeypatch):
    class ArchivedBotOrchestration(_EmptyBotOrchestration):
        async def get_bot_runs(self, **kwargs):
            assert kwargs == {
                "bot_name": BOT,
                "deployment_status": "ARCHIVED",
                "limit": 1,
            }
            return {
                "status": "success",
                "data": [{"bot_name": BOT, "deployment_status": "ARCHIVED"}],
            }

    class ControllersMustNotBeCalled:
        async def get_bot_controller_configs(self, bot_name):
            raise AssertionError("archived bot is absent from active membership")

    client = SimpleNamespace(
        bot_orchestration=ArchivedBotOrchestration(),
        controllers=ControllersMustNotBeCalled(),
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    config = snapshot.Config(
        namespace=NAMESPACE,
        controller_type="generic",
        controller_name="trend_aware_lp_rebalancer",
        archive_check_bots=[BOT],
    )
    result = json.loads(asyncio.run(snapshot.run(config, None)))
    assert result["status"] == "complete"
    assert result["archive_confirmed"] == [BOT]
    assert "archive_pending" not in result


def test_missing_archive_record_keeps_release_pending(monkeypatch):
    class PendingBotOrchestration(_EmptyBotOrchestration):
        async def get_bot_runs(self, **kwargs):
            return {"status": "success", "data": []}

    client = SimpleNamespace(
        bot_orchestration=PendingBotOrchestration(),
        controllers=SimpleNamespace(),
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    config = snapshot.Config(
        namespace=NAMESPACE,
        controller_type="generic",
        controller_name="trend_aware_lp_rebalancer",
        archive_check_bots=[BOT],
    )
    result = json.loads(asyncio.run(snapshot.run(config, None)))
    assert result["status"] == "degraded"
    assert result["archive_pending"] == [BOT]
    assert result["archive_confirmed"] == []


def test_idle_bot_report_is_present_but_not_fresh_lifecycle_authority(monkeypatch):
    performance = {
        CONFIG_ID: {
            "status": "running",
            "performance": {"global_pnl_quote": 0.2},
            "custom_info": _envelope(),
        }
    }
    client = SimpleNamespace(
        bot_orchestration=_BotOrchestration(performance, status="idle"),
        controllers=_Controllers(),
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    result = json.loads(asyncio.run(snapshot.run(_snapshot_config(), None)))
    row = _controller(result)

    assert result["status"] == "degraded"
    assert result["owned_bot_count"] == 1
    assert row["run_state"] == "idle"
    assert row["lifecycle_state"] == "ACTIVE"
    assert row["telemetry_complete"] is False


def test_zero_or_multiple_expected_controllers_degrades_topology(monkeypatch):
    performance = {
        CONFIG_ID: {"status": "running", "performance": {}, "custom_info": _envelope()},
        f"{CONFIG_ID}-extra": {
            "status": "running",
            "performance": {},
            "custom_info": _envelope(),
        },
    }
    client = SimpleNamespace(
        bot_orchestration=_BotOrchestration(performance), controllers=_Controllers()
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    result = json.loads(asyncio.run(snapshot.run(_snapshot_config(), None)))
    assert result["status"] == "degraded"
    assert result["invalid_topology_bots"] == [BOT]


def test_multiple_loaded_configs_degrades_even_with_one_performance_row(monkeypatch):
    class MultipleConfigs(_Controllers):
        async def get_bot_controller_configs(self, bot_name):
            configs = await super().get_bot_controller_configs(bot_name)
            return [*configs, {**configs[0], "id": f"{CONFIG_ID}-extra"}]

    performance = {
        CONFIG_ID: {"status": "running", "performance": {}, "custom_info": _envelope()}
    }
    client = SimpleNamespace(
        bot_orchestration=_BotOrchestration(performance), controllers=MultipleConfigs()
    )

    async def get_client(context):
        return client

    monkeypatch.setattr(snapshot, "_get_client", get_client)
    result = json.loads(asyncio.run(snapshot.run(_snapshot_config(), None)))
    assert result["status"] == "degraded"
    assert result["controller_config_counts"][BOT] == 2
    assert result["invalid_topology_bots"] == [BOT]


def test_registration_preview_and_protected_tokens_never_call_gateway(monkeypatch):
    async def should_not_run(context):
        raise AssertionError("Gateway client should not be resolved")

    monkeypatch.setattr(registration, "_get_client", should_not_run)
    preview = registration.Config(
        network="solana-mainnet-beta",
        mint="A" * 32,
        symbol="AAA",
        decimals=6,
        preview=True,
    )
    protected = registration.Config(
        network="solana-mainnet-beta",
        mint=registration.USDC_MINT,
        symbol="USDC",
        decimals=6,
        preview=False,
    )
    preview_raw = asyncio.run(registration.run(preview, None))
    protected_raw = asyncio.run(registration.run(protected, None))
    assert json.loads(preview_raw)["mutation"] is False
    assert json.loads(protected_raw)["mutation"] is False
    assert len(preview_raw) <= registration.MAX_RESULT_CHARS
    assert len(protected_raw) <= registration.MAX_RESULT_CHARS


def test_registration_submits_once_without_registry_precheck(monkeypatch):
    class Gateway:
        def __init__(self):
            self.calls = []

        async def add_token(self, **kwargs):
            self.calls.append(kwargs)
            return {"ok": True}

        async def get_network_tokens(self, *_args, **_kwargs):
            raise AssertionError("registration must not precheck the registry")

    gateway = Gateway()

    async def get_client(context):
        return SimpleNamespace(gateway=gateway)

    monkeypatch.setattr(registration, "_get_client", get_client)
    config = registration.Config(
        network="solana-mainnet-beta",
        mint="A" * 32,
        symbol="cbBTC",
        decimals=6,
        preview=False,
    )
    raw = asyncio.run(registration.run(config, None))
    result = json.loads(raw)
    assert result["status"] == "confirmed"
    assert result["mutation"] is True
    assert result["token"]["symbol"] == "CBBTC"
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["symbol"] == "CBBTC"
    assert gateway.calls[0]["name"] == "CBBTC"
    assert len(raw) <= registration.MAX_RESULT_CHARS == 1_899


def test_registration_transport_error_is_uncertain_and_not_retried(monkeypatch):
    class Gateway:
        calls = 0

        async def add_token(self, **kwargs):
            self.calls += 1
            raise TimeoutError("request timeout")

    gateway = Gateway()

    async def get_client(context):
        return SimpleNamespace(gateway=gateway)

    monkeypatch.setattr(registration, "_get_client", get_client)
    config = registration.Config(
        network="solana-mainnet-beta",
        mint="A" * 32,
        symbol="AAA",
        decimals=6,
        preview=False,
    )
    raw = asyncio.run(registration.run(config, None))
    result = json.loads(raw)
    assert result["status"] == "uncertain"
    assert result["mutation"] is True
    assert gateway.calls == 1
    assert len(raw) <= registration.MAX_RESULT_CHARS


def test_three_controller_snapshot_stays_below_transport_limit():
    rows = []
    for slot, marker in enumerate("ABC", 1):
        bot = f"{NAMESPACE}-slot-{slot}-20260813-120000"
        controller_id = f"{NAMESPACE}-slot-{slot}"
        current_config = {
            "id": controller_id,
            "_config_name": controller_id,
            "controller_type": "generic",
            "controller_name": "trend_aware_lp_rebalancer",
            "connector_name": snapshot.NETWORK,
            "pool_address": marker * 44,
            "base_token_mint": chr(ord(marker) + 3) * 44,
            "quote_token_mint": snapshot.USDC_MINT,
            "trading_pair": f"T{marker}-USDC",
            "lp_provider": snapshot.LP_PROVIDER,
            "swap_provider": snapshot.SWAP_PROVIDER,
            "total_amount_quote": 10,
            "controller_started_at": 1786550000 + slot,
            "market_trend": "SIDEWAYS",
            "trend_observed_at": 1786550000 + slot,
            "trend_signal_id": "S" * 32,
            "trend_signal_max_age_seconds": 600,
            "position_width_pct": 2,
            "downside_offset_pct": 0,
            "rebalance_threshold_pct": 0.5,
            "defensive_rearm_cooldown_minutes": 5,
            "controller_take_profit_ratio": 0.05,
            "controller_stop_loss_ratio": 0.05,
            "controller_time_limit_minutes": 720,
            "controller_pnl_grace_period_minutes": 5,
            "manual_kill_switch": False,
        }
        envelope = _envelope()
        envelope["lp_executor"] = {
            "id": "L" * 44,
            "status": "RUNNING",
            "close_type": "NONE",
            "state": "OPEN",
            "position_address": "P" * 44,
            "side": "RANGE",
        }
        envelope["trend"].update(
            {
                "observed_at": 1786550000 + slot,
                "signal_id": "S" * 32,
            }
        )
        envelope["identity"].update(
            {
                "controller_id": controller_id,
                "pool_address": current_config["pool_address"],
                "base_token_mint": current_config["base_token_mint"],
                "trading_pair": current_config["trading_pair"],
                "controller_started_at": current_config["controller_started_at"],
            }
        )
        rows.extend(
            snapshot._compact(
                NAMESPACE,
                bot,
                {
                    "performance": {
                        controller_id: {
                            "status": "running",
                            "performance": {"global_pnl_quote": 0.2},
                            "custom_info": envelope,
                        }
                    }
                },
                [current_config],
            )
        )

    raw = snapshot._bounded(
        {
            "schema": snapshot.SCHEMA,
            "status": "complete",
            "observed_at": 1786551000,
            "namespace": NAMESPACE,
            "owned_bot_count": 3,
            "mutation": False,
        },
        rows,
    )
    result = json.loads(raw)

    assert len(raw) <= snapshot.MAX_RESULT_CHARS == 1_899
    assert result["status"] == "complete"
    assert result["format"] == "compact_rows_v1"
    assert len(result["controllers"]) == 3
