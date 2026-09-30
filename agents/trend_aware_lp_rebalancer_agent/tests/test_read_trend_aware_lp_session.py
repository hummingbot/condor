from __future__ import annotations

import asyncio
import copy
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.trend_aware_lp_rebalancer_agent.routines import (
    read_trend_aware_lp_session as reader,
)
from condor import reports
from condor.agents.journal import JournalManager
from condor.routine_store import RoutineStore
from mcp_servers.condor.tools.routines import _result_payload as manager_result_payload
from routines.base import RoutineInfo

NAMESPACE = "trend_aware_lp_rebalancer_agent-orca-v2"
GENERATION = f"{NAMESPACE}_s7_20260830T120000Z"
RUNTIME = f"{NAMESPACE}-20260830-120001"
ACCOUNT = "master_account"
REAL_SAVE_REPORT = reader._save_report


def _config(**changes):
    values = {
        "namespace": NAMESPACE,
        "account_name": ACCOUNT,
        "expected_generation": GENERATION,
        "expected_config_name": GENERATION,
        "expected_runtime_instance": RUNTIME,
    }
    values.update(changes)
    return reader.Config(**values)


def _custom_info(reported_at=None):
    return {
        "schema_version": 3,
        "reported_at": time.time() if reported_at is None else reported_at,
        "controller_id": GENERATION,
        "controller_started_at": time.time() - 10,
        "lifecycle_state": "RUNNING",
        "pnl_quote": None,
        "pnl_ratio": None,
        "positions": [
            {
                "position_id": "pool_ExactOpaquePoolAddress",
                "pool_address": "ExactOpaquePoolAddress",
                "lifecycle_state": "ACTIVE",
                "executor_id": "executor-1",
                "position_address": "ExactOpaquePositionAddress",
                "ownership_state": "PRESENT",
                "lower_price": 95.0,
                "upper_price": 105.0,
                "lower_limit_price": 94.5,
                "upper_limit_price": 105.5,
                "pnl_quote": None,
                "pnl_ratio": None,
                "formation": {
                    "current": {
                        "market_trend": "SIDEWAYS",
                        "position_width_pct": 5.0,
                        "downside_offset_pct": 0.0,
                        "rebalance_threshold_pct": 0.5,
                    },
                    "next": {
                        "market_trend": "SIDEWAYS",
                        "position_width_pct": 5.0,
                        "downside_offset_pct": 0.0,
                        "rebalance_threshold_pct": 0.5,
                    },
                },
                "error": None,
            }
        ],
        "exit_reason": "none",
        "fault_reason": None,
    }


def _live_config():
    return {
        "id": GENERATION,
        "_config_name": GENERATION,
        "controller_type": "generic",
        "controller_name": "trend_aware_lp_rebalancer",
        "lp_positions": [
            {
                "position_id": "pool_ExactOpaquePoolAddress",
                "pool_address": "ExactOpaquePoolAddress",
                "market_trend": "SIDEWAYS",
                "position_width_pct": "5",
                "downside_offset_pct": "0",
                "rebalance_threshold_pct": "0.5",
            }
        ],
    }


class Controllers:
    def __init__(self, *, saved=None, live=None, saved_error=None, live_error=None):
        self.saved = saved
        self.live = live
        self.saved_error = saved_error
        self.live_error = live_error
        self.calls = []

    async def get_controller_config(self, config_name):
        self.calls.append(("saved", config_name))
        if self.saved_error:
            raise self.saved_error
        return copy.deepcopy(self.saved)

    async def get_bot_controller_configs(self, bot_name):
        self.calls.append(("live", bot_name))
        if self.live_error:
            raise self.live_error
        return copy.deepcopy(self.live)


class Orchestration:
    def __init__(self, *, active=None, runs=None, active_error=None, runs_error=None):
        self.active = active
        self.runs = runs
        self.active_error = active_error
        self.runs_error = runs_error
        self.calls = []

    async def get_active_bots_status(self):
        self.calls.append(("active",))
        if self.active_error:
            raise self.active_error
        return copy.deepcopy(self.active)

    async def get_bot_runs(self, **kwargs):
        self.calls.append(("runs", kwargs))
        if self.runs_error:
            raise self.runs_error
        return copy.deepcopy(self.runs)


class Accounts:
    def __init__(self, wallets=None, error=None):
        self.wallets = wallets if wallets is not None else []
        self.error = error
        self.calls = 0

    async def list_gateway_wallets(self):
        self.calls += 1
        if self.error:
            raise self.error
        return copy.deepcopy(self.wallets)


def _client(
    *,
    active=None,
    runs=None,
    saved=None,
    live=None,
    wallets=None,
    active_error=None,
    runs_error=None,
    saved_error=None,
    live_error=None,
    wallets_error=None,
):
    return SimpleNamespace(
        controllers=Controllers(
            saved=saved,
            live=live,
            saved_error=saved_error,
            live_error=live_error,
        ),
        bot_orchestration=Orchestration(
            active=active,
            runs=runs,
            active_error=active_error,
            runs_error=runs_error,
        ),
        accounts=Accounts(wallets=wallets, error=wallets_error),
    )


def _payload(result):
    assert result.sections[0]["type"] == "canonical_payload"
    return result.sections[0]["data"]


@pytest.fixture(autouse=True)
def no_persistent_reports(monkeypatch):
    calls = []

    async def save(payload):
        calls.append(copy.deepcopy(payload))

    monkeypatch.setattr(reader, "_save_report", save)
    return calls


def _run(monkeypatch, client, config=None):
    context = SimpleNamespace(_chat_id=42)

    async def get_client(received_context):
        assert received_context is context
        return client

    monkeypatch.setattr(reader, "_get_client", get_client)
    return asyncio.run(reader.run(config or _config(), context))


def _runtime_payload(
    monkeypatch,
    custom_info,
    *,
    bot_changes=None,
    omit_bot_fields=(),
    performance_changes=None,
    runs=None,
    live=None,
    config=None,
):
    bot = {
        "status": "running",
        "recently_active": True,
        "performance": {GENERATION: {"custom_info": custom_info}},
    }
    bot.update(bot_changes or {})
    for field in omit_bot_fields:
        bot.pop(field, None)
    bot["performance"].update(performance_changes or {})
    client = _client(
        active={"status": "success", "data": {RUNTIME: bot}},
        live=live if live is not None else [_live_config()],
        runs=(
            runs
            if runs is not None
            else {
                "status": "success",
                "data": [{"bot_name": RUNTIME, "account_name": ACCOUNT}],
            }
        ),
        wallets=[],
    )
    return _payload(_run(monkeypatch, client, config))


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"unknown": True}, "Extra inputs"),
        ({"namespace": "bad/name"}, "identities may contain"),
        ({"expected_config_name": None}, "must both be set"),
        ({"expected_config_name": f"{GENERATION}-other"}, "must be identical"),
        (
            {"expected_runtime_instance": "foreign-runtime"},
            "must be namespace-YYYYMMDD-HHMMSS",
        ),
        (
            {"expected_runtime_instance": NAMESPACE},
            "must be namespace-YYYYMMDD-HHMMSS",
        ),
        (
            {"expected_runtime_instance": f"{NAMESPACE}-99999999-999999"},
            "must be namespace-YYYYMMDD-HHMMSS",
        ),
        (
            {
                "expected_generation": None,
                "expected_config_name": None,
            },
            "requires expected_generation",
        ),
        (
            {
                "expected_runtime_instance": None,
                "include_archive_record": True,
            },
            "requires expected_runtime_instance",
        ),
        ({"timeout_seconds": 0}, "greater than or equal to 1"),
        ({"timeout_seconds": 31}, "less than or equal to 30"),
    ],
)
def test_config_rejects_unknown_malformed_and_contradictory_inputs(changes, match):
    with pytest.raises(ValidationError, match=match):
        _config(**changes)


def test_vacant_reads_only_status_and_wallets_and_ignores_prior_bot(monkeypatch):
    prior_runtime = f"{NAMESPACE}-20260830-110000"
    client = _client(
        active={
            "status": "success",
            "data": {prior_runtime: {"status": "running", "performance": {"old": {}}}},
        },
        wallets=[{"chain": "solana", "default_address": "wallet-secret"}],
    )
    config = _config(
        expected_generation=None,
        expected_config_name=None,
        expected_runtime_instance=None,
    )
    payload = _payload(_run(monkeypatch, client, config))

    assert payload["status"] == "degraded"
    assert payload["active_matches"] == []
    assert payload["namespace_conflicts"] == []
    assert payload["account_conflicts"] == []
    assert client.controllers.calls == []
    assert client.bot_orchestration.calls == [("active",)]
    assert client.accounts.calls == 1


def test_saved_config_path_uses_exact_read_and_preserves_complete_config(monkeypatch):
    saved = {
        "id": GENERATION,
        "controller_type": "generic",
        "controller_name": "trend_aware_lp_rebalancer",
        "lp_positions": [{"position_id": "pool_full", "opaque": {"pnl": None}}],
    }
    client = _client(
        active={"status": "success", "data": {}},
        saved=saved,
        wallets=[],
    )
    config = _config(expected_runtime_instance=None)
    payload = _payload(_run(monkeypatch, client, config))

    assert payload["config"] == saved
    assert payload["status"] == "degraded"
    assert client.controllers.calls == [("saved", GENERATION)]
    assert client.bot_orchestration.calls == [("active",)]


def test_runtime_path_preserves_schema_three_and_always_fetches_bot_runs(monkeypatch):
    custom_info = _custom_info()
    live = [_live_config()]
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "performance": {
                    GENERATION: {
                        "status": "running",
                        "performance": {"global_pnl_quote": None},
                        "custom_info": custom_info,
                    }
                },
            }
        },
    }
    runs = {
        "status": "success",
        "data": [
            {
                "id": 7,
                "bot_name": RUNTIME,
                "account_name": ACCOUNT,
                "deployment_status": "DEPLOYED",
                "stopped_at": None,
            }
        ],
    }
    client = _client(active=active, live=live, runs=runs, wallets=[])
    payload = _payload(_run(monkeypatch, client))

    assert payload["status"] == "complete"
    assert payload["custom_info"] == custom_info
    assert payload["custom_info"]["lifecycle_state"] == "RUNNING"
    assert payload["custom_info"]["pnl_quote"] is None
    assert payload["custom_info"]["positions"][0]["pnl_quote"] is None
    assert payload["config"] == live[0]
    assert payload["bot_run_matches"] == runs["data"]
    assert payload["archive_record"] is None
    assert payload["active_matches"][0]["controller_ids"] == [GENERATION]
    assert payload["authority_observability"] == {
        "account": "observed",
        "wallet": "not_observable",
    }
    assert client.controllers.calls == [("live", RUNTIME)]
    assert client.bot_orchestration.calls == [
        ("active",),
        (
            "runs",
            {
                "bot_name": RUNTIME,
                "limit": 2,
                "offset": 0,
                "include_final_status": False,
            },
        ),
    ]


def test_runtime_discovered_from_generation_fetches_live_config_and_run_same_call(
    monkeypatch,
):
    live = _live_config()
    live["live_only_marker"] = "exact-live-config"
    client = _client(
        active={
            "status": "success",
            "data": {
                RUNTIME: {
                    "status": "running",
                    "recently_active": True,
                    "account_name": ACCOUNT,
                    "wallet_address": "owned-wallet",
                    "performance": {GENERATION: {"custom_info": _custom_info()}},
                },
                "unrelated-agent-20260830-120002": {
                    "account_name": "other_account",
                    "wallet_address": "other-wallet",
                    "performance": {},
                },
            },
        },
        saved={"id": GENERATION, "saved_only_marker": True},
        live=[live],
        runs={
            "status": "success",
            "data": [
                {
                    "bot_name": RUNTIME,
                    "account_name": ACCOUNT,
                    "wallet_address": "owned-wallet",
                }
            ],
        },
        wallets=[],
    )

    payload = _payload(
        _run(monkeypatch, client, _config(expected_runtime_instance=None))
    )

    assert [row["runtime_instance"] for row in payload["active_matches"]] == [RUNTIME]
    assert payload["config"] == live
    assert payload["bot_run_matches"][0]["bot_name"] == RUNTIME
    assert payload["account_conflicts"] == []
    assert payload["wallet_conflicts"] == []
    assert payload["authority_observability"] == {
        "account": "observed",
        "wallet": "not_observable",
    }
    assert client.controllers.calls == [("saved", GENERATION), ("live", RUNTIME)]
    assert client.bot_orchestration.calls[1] == (
        "runs",
        {
            "bot_name": RUNTIME,
            "limit": 2,
            "offset": 0,
            "include_final_status": False,
        },
    )


def test_expected_runtime_absence_is_unavailable_outside_archive(monkeypatch):
    client = _client(
        active={"status": "success", "data": {}},
        live=[_live_config()],
        runs={
            "status": "success",
            "data": [{"bot_name": RUNTIME, "account_name": ACCOUNT}],
        },
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client))

    assert payload["active_matches"] == []
    assert payload["status"] == "unavailable"
    assert "expected runtime instance is absent" in payload["errors"][0]


def test_active_runtime_requires_exactly_one_expected_controller(monkeypatch):
    custom_info = _custom_info()
    payload = _runtime_payload(
        monkeypatch,
        custom_info,
        performance_changes={"foreign-controller": {"custom_info": {}}},
    )

    assert payload["custom_info"] == custom_info
    assert payload["status"] == "unavailable"
    assert payload["namespace_conflicts"][-1] == {
        "runtime_instance": RUNTIME,
        "field": "controller_ids",
        "expected": [GENERATION],
        "observed": [GENERATION, "foreign-controller"],
    }


@pytest.mark.parametrize(
    "bot_changes,omit_bot_fields,reported_at,error",
    [
        ({"status": "stopped"}, (), None, "raw status must be running"),
        ({"recently_active": False}, (), None, "recently_active must be true"),
        ({}, ("recently_active",), None, "recently_active must be true"),
        ({}, (), "stale", "reported_at is older than 30 seconds"),
        ({}, (), "future", "reported_at is in the future"),
        ({}, (), "malformed", "reported_at must be a finite number"),
    ],
)
def test_active_runtime_requires_structurally_valid_fresh_liveness(
    monkeypatch, bot_changes, omit_bot_fields, reported_at, error
):
    timestamps = {
        None: None,
        "stale": time.time() - 31,
        "future": time.time() + 5,
        "malformed": "not-a-timestamp",
    }
    custom_info = _custom_info(timestamps[reported_at])

    payload = _runtime_payload(
        monkeypatch,
        custom_info,
        bot_changes=bot_changes,
        omit_bot_fields=omit_bot_fields,
    )

    assert payload["custom_info"] == custom_info
    assert payload["active_matches"][0]["raw_status"] == bot_changes.get(
        "status", "running"
    )
    assert payload["active_matches"][0]["recently_active"] == bot_changes.get(
        "recently_active", None if omit_bot_fields else True
    )
    assert payload["status"] == "unavailable"
    assert any(error in message for message in payload["errors"])


def test_other_namespaced_bot_is_ignored_when_exact_runtime_is_selected(monkeypatch):
    other = f"{NAMESPACE}-20260830-130000"
    active = {
        "status": "success",
        "data": {
            RUNTIME: {"status": "running", "performance": {}},
            other: {"status": "running", "performance": {"foreign": {}}},
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={"status": "success", "data": []},
        wallets=[],
    )
    payload = _payload(_run(monkeypatch, client))

    assert [row["runtime_instance"] for row in payload["active_matches"]] == [RUNTIME]
    assert all(row["runtime_instance"] != other for row in payload["namespace_conflicts"])
    assert payload["status"] == "unavailable"


@pytest.mark.parametrize(
    "runtime_instance",
    [
        NAMESPACE,
        f"{NAMESPACE}-not-a-timestamp",
        f"{NAMESPACE}-20260230-120000",
        f"{NAMESPACE}-20260830-120000-extra",
    ],
)
def test_malformed_prior_runtime_is_ignored_for_vacant_session(
    monkeypatch, runtime_instance
):
    client = _client(
        active={
            "status": "success",
            "data": {
                runtime_instance: {
                    "status": "running",
                    "performance": {"opaque-controller": {}},
                }
            },
        },
        wallets=[],
    )
    config = _config(
        expected_generation=None,
        expected_config_name=None,
        expected_runtime_instance=None,
    )

    payload = _payload(_run(monkeypatch, client, config))

    assert payload["active_matches"] == []
    assert payload["namespace_conflicts"] == []
    assert payload["status"] == "degraded"


def test_runtime_unknown_ignores_bots_from_other_generations(monkeypatch):
    first = f"{NAMESPACE}-20260830-120001"
    second = f"{NAMESPACE}-20260830-120002"
    client = _client(
        active={
            "status": "success",
            "data": {
                first: {"status": "running", "performance": {}},
                second: {"status": "running", "performance": {}},
            },
        },
        saved={"id": GENERATION},
        wallets=[],
    )

    payload = _payload(
        _run(monkeypatch, client, _config(expected_runtime_instance=None))
    )

    assert payload["active_matches"] == []
    assert payload["namespace_conflicts"] == []
    assert payload["status"] == "degraded"


def test_multiple_live_configs_and_bot_runs_remain_explicit(monkeypatch):
    active = {"status": "success", "data": {RUNTIME: {"performance": {}}}}
    client = _client(
        active=active,
        live=[
            {"id": GENERATION, "_config_name": "wrong"},
            {"id": "wrong", "_config_name": GENERATION},
        ],
        runs={
            "status": "success",
            "data": [
                {"id": 1, "bot_name": RUNTIME},
                {"id": 2, "bot_name": RUNTIME},
            ],
        },
        wallets=[],
    )
    payload = _payload(_run(monkeypatch, client))

    assert payload["config"] is None
    assert len(payload["bot_run_matches"]) == 2
    assert [conflict["field"] for conflict in payload["namespace_conflicts"]] == [
        "controller_config_identity",
        "controller_config_identity",
        "controller_ids",
    ]
    assert payload["status"] == "unavailable"


def test_archive_record_is_only_returned_when_requested(monkeypatch):
    archived = {
        "id": 9,
        "bot_name": RUNTIME,
        "account_name": ACCOUNT,
        "deployment_status": "ARCHIVED",
        "stopped_at": "2026-08-30T12:30:00Z",
    }
    client = _client(
        active={"status": "success", "data": {}},
        live_error=RuntimeError("bot is no longer active"),
        runs={"status": "success", "data": [archived]},
        wallets=[],
    )
    payload = _payload(_run(monkeypatch, client, _config(include_archive_record=True)))

    assert payload["archive_record"] == archived
    assert payload["bot_run_matches"] == [archived]
    assert payload["status"] == "degraded"
    assert payload["custom_info"] is None


def test_bot_runs_are_fetched_but_archive_record_stays_null_when_not_requested(
    monkeypatch,
):
    archived = {
        "id": 9,
        "bot_name": RUNTIME,
        "account_name": ACCOUNT,
        "deployment_status": "ARCHIVED",
        "stopped_at": "2026-08-30T12:30:00Z",
    }
    client = _client(
        active={"status": "success", "data": {}},
        live=[_live_config()],
        runs={"status": "success", "data": [archived]},
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client))

    assert payload["bot_run_matches"] == [archived]
    assert payload["archive_record"] is None
    assert client.bot_orchestration.calls[1][0] == "runs"


def test_archive_confirmation_rejects_non_archived_run(monkeypatch):
    deployed = {
        "id": 9,
        "bot_name": RUNTIME,
        "account_name": ACCOUNT,
        "deployment_status": "DEPLOYED",
        "stopped_at": None,
    }
    client = _client(
        active={"status": "success", "data": {}},
        live=[_live_config()],
        runs={"status": "success", "data": [deployed]},
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client, _config(include_archive_record=True)))

    assert payload["bot_run_matches"] == [deployed]
    assert payload["archive_record"] is None
    assert payload["status"] == "unavailable"
    assert "deployment_status ARCHIVED" in payload["errors"][-1]


def test_archive_confirmation_rejects_still_active_runtime(monkeypatch):
    archived = {
        "id": 9,
        "bot_name": RUNTIME,
        "account_name": ACCOUNT,
        "deployment_status": "ARCHIVED",
        "stopped_at": "2026-08-30T12:30:00Z",
    }
    payload = _runtime_payload(
        monkeypatch,
        _custom_info(),
        runs={"status": "success", "data": [archived]},
        config=_config(include_archive_record=True),
    )

    assert payload["bot_run_matches"] == [archived]
    assert payload["archive_record"] is None
    assert payload["status"] == "unavailable"
    assert "absent from active status" in payload["errors"][-1]


@pytest.mark.parametrize(
    "matches",
    [
        [],
        [
            {"id": 9, "bot_name": RUNTIME, "deployment_status": "ARCHIVED"},
            {"id": 10, "bot_name": RUNTIME, "deployment_status": "ARCHIVED"},
        ],
    ],
)
def test_archive_confirmation_preserves_zero_or_multiple_matches_as_unavailable(
    monkeypatch, matches
):
    client = _client(
        active={"status": "success", "data": {}},
        live=[],
        runs={"status": "success", "data": matches},
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client, _config(include_archive_record=True)))

    assert payload["bot_run_matches"] == matches
    assert payload["archive_record"] is None
    assert payload["status"] == "unavailable"
    assert any(
        "archive confirmation requires exactly one" in error
        for error in payload["errors"]
    )


def test_archived_runtime_accepts_an_empty_live_config_list_as_degraded(monkeypatch):
    archived = {
        "id": 9,
        "bot_name": RUNTIME,
        "deployment_status": "ARCHIVED",
        "stopped_at": "2026-08-30T12:30:00Z",
    }
    client = _client(
        active={"status": "success", "data": {}},
        live=[],
        runs={"status": "success", "data": [archived]},
        wallets=[],
    )
    payload = _payload(_run(monkeypatch, client, _config(include_archive_record=True)))

    assert payload["archive_record"] == archived
    assert payload["status"] == "degraded"
    assert "no readable live controller config" in payload["warnings"][0]


def test_account_mismatch_is_explicit_and_wallet_default_is_not_association(
    monkeypatch,
):
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "performance": {GENERATION: {"custom_info": _custom_info()}},
            }
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={
            "status": "success",
            "data": [{"bot_name": RUNTIME, "account_name": "other_account"}],
        },
        wallets=[{"chain": "solana", "default_address": "default-wallet"}],
    )
    payload = _payload(_run(monkeypatch, client))

    assert payload["authority_observability"] == {
        "account": "observed",
        "wallet": "not_observable",
    }
    assert payload["account_conflicts"][0]["observed"] == "other_account"
    assert (
        payload["gateway_wallet_observation"]["solana_default_address"]
        == "default-wallet"
    )
    assert payload["status"] == "unavailable"


def test_active_status_account_does_not_replace_exact_bot_run_account(monkeypatch):
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "account_name": ACCOUNT,
                "performance": {GENERATION: {"custom_info": _custom_info()}},
            }
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={"status": "success", "data": [{"bot_name": RUNTIME}]},
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client))

    assert payload["authority_observability"]["account"] == "not_observable"
    assert payload["account_conflicts"] == []
    assert payload["status"] == "degraded"


def test_optional_wallet_failure_does_not_degrade_but_status_failure_is_unavailable(
    monkeypatch,
):
    base = dict(
        live=[_live_config()],
        runs={
            "status": "success",
            "data": [{"bot_name": RUNTIME, "account_name": ACCOUNT}],
        },
    )
    wallet_client = _client(
        active={
            "status": "success",
            "data": {
                RUNTIME: {
                    "status": "running",
                    "recently_active": True,
                    "performance": {GENERATION: {"custom_info": _custom_info()}},
                }
            },
        },
        wallets_error=RuntimeError("wallet route failed"),
        **base,
    )
    wallet_payload = _payload(_run(monkeypatch, wallet_client))
    assert wallet_payload["status"] == "complete"
    assert wallet_payload["warnings"] == []

    status_client = _client(
        active_error=RuntimeError("status route failed"),
        wallets=[],
        **base,
    )
    status_payload = _payload(_run(monkeypatch, status_client))
    assert status_payload["status"] == "unavailable"
    assert status_payload["errors"][0] == "active_status unavailable: RuntimeError"


def test_vacant_read_ignores_foreign_active_account_metadata(monkeypatch):
    foreign_runtime = "unrelated-agent-orca-20260830-120001"
    client = _client(
        active={
            "status": "success",
            "data": {
                foreign_runtime: {
                    "status": "running",
                    "recently_active": True,
                    "account_name": ACCOUNT,
                    "performance": {},
                }
            },
        },
        wallets=[
            {
                "chain": "solana",
                "walletAddresses": ["default-wallet-does-not-prove-overlap"],
                "default_address": "default-wallet-does-not-prove-overlap",
            }
        ],
    )

    payload = _payload(
        _run(
            monkeypatch,
            client,
            _config(
                expected_generation=None,
                expected_config_name=None,
                expected_runtime_instance=None,
            ),
        )
    )

    assert payload["active_matches"] == []
    assert payload["namespace_conflicts"] == []
    assert payload["account_conflicts"] == []
    assert payload["authority_observability"]["wallet"] == "not_observable"
    assert payload["status"] == "degraded"


def test_running_read_ignores_foreign_account_metadata(
    monkeypatch,
):
    foreign_runtime = "unrelated-agent-orca-20260830-120001"
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "account_name": ACCOUNT,
                "performance": {GENERATION: {"custom_info": _custom_info()}},
            },
            foreign_runtime: {
                "status": "running",
                "recently_active": True,
                "account_name": ACCOUNT,
                "performance": {},
            },
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={"status": "success", "data": [{"bot_name": RUNTIME}]},
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client))

    assert [row["runtime_instance"] for row in payload["active_matches"]] == [RUNTIME]
    assert payload["account_conflicts"] == []
    assert payload["status"] == "degraded"


def test_foreign_active_bot_with_unknown_metadata_does_not_hide_owned_account(
    monkeypatch,
):
    foreign_runtime = "unrelated-agent-orca-20260830-120001"
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "account_name": ACCOUNT,
                "wallet_address": "owned-wallet",
                "performance": {GENERATION: {"custom_info": _custom_info()}},
            },
            foreign_runtime: {
                "status": "running",
                "recently_active": True,
                "performance": {},
            },
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={
            "status": "success",
            "data": [
                {
                    "bot_name": RUNTIME,
                    "account_name": ACCOUNT,
                    "wallet_address": "owned-wallet",
                }
            ],
        },
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client))

    assert payload["authority_observability"] == {
        "account": "observed",
        "wallet": "not_observable",
    }
    assert payload["account_conflicts"] == []
    assert payload["status"] == "complete"


def test_prospective_gateway_wallet_is_diagnostic_only(
    monkeypatch,
):
    foreign_runtime = "unrelated-agent-orca-20260830-120001"
    client = _client(
        active={
            "status": "success",
            "data": {
                foreign_runtime: {
                    "account_name": "other_account",
                    "wallet_address": "prospective-wallet",
                    "performance": {},
                }
            },
        },
        wallets=[{"chain": "solana", "default_address": "prospective-wallet"}],
    )

    payload = _payload(
        _run(
            monkeypatch,
            client,
            _config(
                expected_generation=None,
                expected_config_name=None,
                expected_runtime_instance=None,
            ),
        )
    )

    assert payload["gateway_wallet_observation"]["solana_default_address"] == (
        "prospective-wallet"
    )
    assert payload["authority_observability"]["wallet"] == "not_observable"
    assert payload["wallet_conflicts"] == []
    assert payload["status"] == "degraded"


def test_owned_wallet_source_disagreement_is_diagnostic_only(
    monkeypatch,
):
    foreign_runtime = "unrelated-agent-orca-20260830-120001"
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "account_name": ACCOUNT,
                "wallet_address": "shared-wallet",
                "performance": {GENERATION: {"custom_info": _custom_info()}},
            },
            foreign_runtime: {
                "account_name": "other_account",
                "wallet_address": "shared-wallet",
                "performance": {},
            },
            "unknown-wallet-agent-orca-20260830-120002": {
                "account_name": "third_account",
                "performance": {},
            },
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={
            "status": "success",
            "data": [
                {
                    "bot_name": RUNTIME,
                    "account_name": ACCOUNT,
                    "wallet_address": "different-owned-wallet",
                }
            ],
        },
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client))

    assert payload["authority_observability"]["wallet"] == "not_observable"
    assert payload["wallet_conflicts"] == []
    assert payload["status"] == "complete"


def test_owned_wallet_overlap_with_foreign_bot_does_not_block(monkeypatch):
    foreign_runtime = "unrelated-agent-orca-20260830-120001"
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "account_name": ACCOUNT,
                "wallet_address": "shared-wallet",
                "performance": {GENERATION: {"custom_info": _custom_info()}},
            },
            foreign_runtime: {
                "account_name": "other_account",
                "wallet_address": "shared-wallet",
                "performance": {},
            },
            "unknown-wallet-agent-orca-20260830-120003": {
                "account_name": "third_account",
                "performance": {},
            },
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={
            "status": "success",
            "data": [
                {
                    "bot_name": RUNTIME,
                    "account_name": ACCOUNT,
                    "wallet_address": "shared-wallet",
                }
            ],
        },
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client))

    assert payload["authority_observability"]["wallet"] == "not_observable"
    assert payload["wallet_conflicts"] == []
    assert payload["status"] == "complete"


def test_active_expected_controller_requires_raw_schema_three_custom_info(monkeypatch):
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "performance": {GENERATION: {"status": "running"}},
            }
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={"status": "success", "data": [{"bot_name": RUNTIME}]},
        wallets=[],
    )
    payload = _payload(_run(monkeypatch, client))

    assert payload["custom_info"] is None
    assert payload["status"] == "unavailable"
    assert "does not contain custom_info" in payload["errors"][-1]


@pytest.mark.parametrize("controller_id", [None, "foreign-generation"])
def test_schema_three_custom_info_requires_exact_top_level_controller_id(
    monkeypatch, controller_id
):
    custom_info = _custom_info()
    if controller_id is None:
        custom_info.pop("controller_id")
    else:
        custom_info["controller_id"] = controller_id
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "performance": {GENERATION: {"custom_info": custom_info}},
            }
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={"status": "success", "data": []},
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client))

    assert payload["status"] == "unavailable"
    assert payload["custom_info"] == custom_info
    assert payload["namespace_conflicts"][-1] == {
        "runtime_instance": RUNTIME,
        "field": "custom_info.controller_id",
        "expected": GENERATION,
        "observed": controller_id,
    }


def test_schema_three_custom_info_rejects_invalid_top_level_lifecycle(monkeypatch):
    custom_info = _custom_info()
    custom_info["lifecycle_state"] = "ACTIVE"
    active = {
        "status": "success",
        "data": {
            RUNTIME: {
                "status": "running",
                "recently_active": True,
                "performance": {GENERATION: {"custom_info": custom_info}},
            }
        },
    }
    client = _client(
        active=active,
        live=[_live_config()],
        runs={"status": "success", "data": []},
        wallets=[],
    )

    payload = _payload(_run(monkeypatch, client))

    assert payload["status"] == "unavailable"
    assert payload["custom_info"] == custom_info
    assert payload["errors"][-1] == (
        "matching controller custom_info has an invalid lifecycle_state"
    )


@pytest.mark.parametrize(
    "position_lifecycle,controller_lifecycle,executor_id,ownership,exit_reason",
    [
        ("PENDING", "RUNNING", None, "UNKNOWN", "none"),
        ("COOLDOWN", "RUNNING", None, "ABSENT", "none"),
        ("BLOCKED", "RUNNING", None, "UNKNOWN", "none"),
        ("PENDING", "EXITING", None, "UNKNOWN", "operator"),
        ("ACTIVE", "EXITING", "executor-1", "PRESENT", "operator"),
        ("COOLDOWN", "EXITING", None, "ABSENT", "operator"),
        ("BLOCKED", "EXITING", None, "UNKNOWN", "operator"),
        ("CLEANING", "EXITING", None, "ABSENT", "operator"),
        ("RECOVERING", "RUNNING", None, "UNKNOWN", "none"),
        ("EXITED", "EXITED", None, "ABSENT", "time_limit"),
        ("BLOCKED", "FAULTED", None, "UNKNOWN", "fault"),
        ("FAULTED", "FAULTED", None, "UNKNOWN", "fault"),
    ],
)
def test_schema_three_validator_allows_documented_nullable_transitions(
    position_lifecycle,
    controller_lifecycle,
    executor_id,
    ownership,
    exit_reason,
):
    custom_info = _custom_info()
    custom_info["lifecycle_state"] = controller_lifecycle
    custom_info["exit_reason"] = exit_reason
    custom_info["fault_reason"] = "fault" if exit_reason == "fault" else None
    position = custom_info["positions"][0]
    position["lifecycle_state"] = position_lifecycle
    position["executor_id"] = executor_id
    position["ownership_state"] = ownership
    position["pnl_quote"] = None
    position["pnl_ratio"] = None
    if position_lifecycle not in {"PREPARING", "OPENING", "ACTIVE", "CLOSING"}:
        position["formation"]["current"] = None

    assert reader._schema_three_errors(custom_info, _live_config()) == []


@pytest.mark.parametrize(
    "controller_lifecycle,exit_reason,fault_reason,error",
    [
        ("RUNNING", "operator", None, "RUNNING requires exit_reason none"),
        ("EXITING", "none", None, "EXITING requires a non-none exit_reason"),
        ("EXITED", "fault", "fault", "EXITED cannot use exit_reason fault"),
        ("RUNNING", "none", "fault", "RUNNING requires fault_reason null"),
        ("EXITED", "operator", "fault", "EXITED requires fault_reason null"),
        ("FAULTED", "fault", None, "FAULTED requires a non-empty fault_reason"),
        (
            "EXITING",
            "fault",
            None,
            "exit_reason fault requires a non-empty fault_reason",
        ),
    ],
)
def test_schema_three_validator_rejects_impossible_controller_combinations(
    controller_lifecycle,
    exit_reason,
    fault_reason,
    error,
):
    custom_info = _custom_info()
    custom_info["lifecycle_state"] = controller_lifecycle
    custom_info["exit_reason"] = exit_reason
    custom_info["fault_reason"] = fault_reason
    if controller_lifecycle == "EXITED":
        position = custom_info["positions"][0]
        position["lifecycle_state"] = "EXITED"
        position["executor_id"] = None
        position["ownership_state"] = "ABSENT"

    errors = reader._schema_three_errors(custom_info, _live_config())

    assert any(error in message for message in errors)


@pytest.mark.parametrize(
    "controller_lifecycle,exit_reason,fault_reason,position_lifecycle,executor_id,ownership,error",
    [
        (
            "RUNNING",
            "none",
            None,
            "EXITED",
            None,
            "ABSENT",
            "EXITED is impossible while controller is RUNNING",
        ),
        (
            "EXITED",
            "operator",
            None,
            "CLOSING",
            "executor-1",
            "PRESENT",
            "must be EXITED while controller is EXITED",
        ),
    ],
)
def test_schema_three_validator_rejects_impossible_session_position_combinations(
    controller_lifecycle,
    exit_reason,
    fault_reason,
    position_lifecycle,
    executor_id,
    ownership,
    error,
):
    custom_info = _custom_info()
    custom_info["lifecycle_state"] = controller_lifecycle
    custom_info["exit_reason"] = exit_reason
    custom_info["fault_reason"] = fault_reason
    position = custom_info["positions"][0]
    position["lifecycle_state"] = position_lifecycle
    position["executor_id"] = executor_id
    position["ownership_state"] = ownership

    errors = reader._schema_three_errors(custom_info, _live_config())

    assert any(error in message for message in errors)


@pytest.mark.parametrize(
    "change,error",
    [
        ("reported_at", "reported_at must be a finite number"),
        ("global_pnl", "pnl_quote must be finite or null"),
        ("position_price", "lower_price must be finite or null"),
        ("position_pnl", "pnl_quote must be finite or null"),
        (
            "formation",
            "formation.next.position_width_pct must be a finite number",
        ),
    ],
)
def test_schema_three_numeric_fields_require_json_numbers(change, error):
    custom_info = _custom_info()
    position = custom_info["positions"][0]
    if change == "reported_at":
        custom_info["reported_at"] = str(custom_info["reported_at"])
    elif change == "global_pnl":
        custom_info["pnl_quote"] = "1.25"
        custom_info["pnl_ratio"] = "0.1"
    elif change == "position_price":
        position["lower_price"] = "95"
    elif change == "position_pnl":
        position["pnl_quote"] = "1.25"
        position["pnl_ratio"] = "0.1"
    else:
        position["formation"]["next"]["position_width_pct"] = "5"

    errors = reader._schema_three_errors(custom_info, _live_config())

    assert any(error in message for message in errors)


@pytest.mark.parametrize(
    "change,error",
    [
        ("pending_executor", "executor_id must be null for PENDING"),
        ("active_without_executor", "executor_id is required for ACTIVE"),
        ("exited_present", "ownership_state must be ABSENT for EXITED"),
        ("active_without_current", "formation.current is required"),
        ("missing_current_key", "formation is missing fields: current"),
        ("invalid_ownership", "ownership_state is invalid"),
        ("pnl_pair", "pnl_quote and pnl_ratio must both be numeric or both be null"),
        ("preparing_pnl", "PnL must be null for PREPARING"),
        (
            "empty_position_address",
            "position_address must be a non-empty string or null",
        ),
    ],
)
def test_schema_three_validator_rejects_impossible_position_combinations(change, error):
    custom_info = _custom_info()
    position = custom_info["positions"][0]
    if change == "pending_executor":
        position["lifecycle_state"] = "PENDING"
    elif change == "active_without_executor":
        position["executor_id"] = None
    elif change == "exited_present":
        position["lifecycle_state"] = "EXITED"
        position["executor_id"] = None
    elif change == "active_without_current":
        position["formation"]["current"] = None
    elif change == "missing_current_key":
        position["lifecycle_state"] = "PENDING"
        position["executor_id"] = None
        position["formation"].pop("current")
    elif change == "invalid_ownership":
        position["ownership_state"] = "OWNED"
    elif change == "pnl_pair":
        position["pnl_quote"] = 1
    elif change == "preparing_pnl":
        position["lifecycle_state"] = "PREPARING"
        position["pnl_quote"] = 1
        position["pnl_ratio"] = 0.1
    else:
        position["position_address"] = ""

    errors = reader._schema_three_errors(custom_info, _live_config())

    assert any(error in message for message in errors)


def test_formation_next_mismatch_is_preserved_for_strategy_reconciliation():
    custom_info = _custom_info()
    custom_info["positions"][0]["formation"]["next"]["position_width_pct"] = 6

    assert reader._schema_three_errors(custom_info, _live_config()) == []


def test_schema_three_identity_mismatch_preserves_raw_telemetry_and_is_unavailable(
    monkeypatch,
):
    custom_info = _custom_info()
    custom_info["positions"][0]["pool_address"] = "DifferentOpaquePoolAddress"

    payload = _runtime_payload(monkeypatch, custom_info)

    assert payload["custom_info"] == custom_info
    assert payload["status"] == "unavailable"
    assert any("pool_address does not match" in error for error in payload["errors"])


def test_multi_position_terminal_telemetry_is_preserved_exactly(monkeypatch):
    custom_info = _custom_info()
    custom_info.update(
        lifecycle_state="EXITED",
        exit_reason="operator",
        pnl_quote=2.5,
        pnl_ratio=0.25,
    )
    first = custom_info["positions"][0]
    first.update(
        lifecycle_state="EXITED",
        executor_id=None,
        ownership_state="ABSENT",
        pnl_quote=2.5,
        pnl_ratio=0.5,
    )
    second = copy.deepcopy(first)
    second.update(
        position_id="pool_SecondOpaquePoolAddress",
        pool_address="SecondOpaquePoolAddress",
        position_address="SecondOpaquePositionAddress",
        pnl_quote=None,
        pnl_ratio=None,
    )
    custom_info["positions"].append(second)

    live_config = _live_config()
    second_config = copy.deepcopy(live_config["lp_positions"][0])
    second_config.update(
        position_id="pool_SecondOpaquePoolAddress",
        pool_address="SecondOpaquePoolAddress",
    )
    live_config["lp_positions"].append(second_config)

    payload = _runtime_payload(
        monkeypatch,
        custom_info,
        live=[live_config],
    )

    assert payload["custom_info"] == custom_info
    assert payload["custom_info"]["positions"] == [first, second]
    assert payload["custom_info"]["pnl_quote"] == 2.5
    assert payload["custom_info"]["positions"][0]["pnl_quote"] == 2.5
    assert payload["custom_info"]["positions"][1]["pnl_quote"] is None
    assert not any(
        "positions must exactly match" in error for error in payload["errors"]
    )


def test_read_deadline_cancels_pending_source(monkeypatch):
    context = SimpleNamespace(_chat_id=42)
    client = _client(wallets=[])

    async def scenario():
        cancelled = asyncio.Event()

        async def slow_status():
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        async def get_client(_context):
            return client

        client.bot_orchestration.get_active_bots_status = slow_status
        monkeypatch.setattr(reader, "_get_client", get_client)
        config = _config(
            expected_generation=None,
            expected_config_name=None,
            expected_runtime_instance=None,
            timeout_seconds=1,
        )
        results = await reader._read_sources(config, context)
        return results, cancelled.is_set()

    results, cancelled = asyncio.run(scenario())

    assert isinstance(results["active_status"], TimeoutError)
    assert cancelled is True
    assert results["gateway_wallets"] == []


def test_read_deadline_includes_client_acquisition(monkeypatch):
    context = SimpleNamespace(_chat_id=42)

    async def scenario():
        cancelled = asyncio.Event()

        async def slow_get_client(_context):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        monkeypatch.setattr(reader, "_get_client", slow_get_client)
        config = _config(
            expected_generation=None,
            expected_config_name=None,
            expected_runtime_instance=None,
            timeout_seconds=1,
        )
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await reader._read_sources(config, context)
        return time.monotonic() - started, cancelled.is_set()

    elapsed, cancelled = asyncio.run(scenario())

    assert elapsed < 1.5
    assert cancelled is True


def test_discovered_runtime_followups_share_the_original_deadline(monkeypatch):
    context = SimpleNamespace(_chat_id=42)
    client = _client(saved={"id": GENERATION}, wallets=[])

    async def scenario():
        cancelled = asyncio.Event()

        async def delayed_status():
            await asyncio.sleep(0.4)
            return {
                "status": "success",
                "data": {RUNTIME: {"performance": {GENERATION: {}}}},
            }

        async def slow_live(_bot_name):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        async def get_client(_context):
            return client

        client.bot_orchestration.get_active_bots_status = delayed_status
        client.controllers.get_bot_controller_configs = slow_live
        client.bot_orchestration.runs = {"status": "success", "data": []}
        monkeypatch.setattr(reader, "_get_client", get_client)
        config = _config(expected_runtime_instance=None, timeout_seconds=1)
        started = time.monotonic()
        results = await reader._read_sources(config, context)
        return results, time.monotonic() - started, cancelled.is_set()

    results, elapsed, cancelled = asyncio.run(scenario())

    assert isinstance(results["live_configs"], TimeoutError)
    assert elapsed < 1.5
    assert cancelled is True


def test_get_client_uses_context_chat_id_and_context(monkeypatch):
    import config_manager

    context = SimpleNamespace(_chat_id=314)
    expected_client = object()
    calls = []

    async def get_client(chat_id, *, context):
        calls.append((chat_id, context))
        return expected_client

    monkeypatch.setattr(config_manager, "get_client", get_client)

    assert asyncio.run(reader._get_client(context)) is expected_client
    assert calls == [(314, context)]


def test_report_is_attempted_for_handled_outcomes(no_persistent_reports, monkeypatch):
    complete_client = _client(
        active={
            "status": "success",
            "data": {
                RUNTIME: {
                    "status": "running",
                    "recently_active": True,
                    "account_name": ACCOUNT,
                    "wallet_address": "owned-wallet",
                    "performance": {GENERATION: {"custom_info": _custom_info()}},
                }
            },
        },
        live=[_live_config()],
        runs={
            "status": "success",
            "data": [
                {
                    "bot_name": RUNTIME,
                    "account_name": ACCOUNT,
                    "wallet_address": "owned-wallet",
                }
            ],
        },
        wallets=[],
    )
    degraded_client = _client(
        active={"status": "success", "data": {}},
        saved={"id": GENERATION},
        wallets=[],
    )
    unavailable_client = _client(
        active_error=RuntimeError("status route failed"),
        live_error=RuntimeError("config route failed"),
        runs_error=RuntimeError("run route failed"),
        wallets_error=RuntimeError("wallet route failed"),
    )

    complete = _payload(_run(monkeypatch, complete_client))
    degraded = _payload(
        _run(
            monkeypatch,
            degraded_client,
            _config(expected_runtime_instance=None),
        )
    )
    unavailable = _payload(_run(monkeypatch, unavailable_client))

    assert [complete["status"], degraded["status"], unavailable["status"]] == [
        "complete",
        "degraded",
        "unavailable",
    ]
    assert [payload["status"] for payload in no_persistent_reports] == [
        "complete",
        "degraded",
        "unavailable",
    ]


def test_report_failure_preserves_evidence_and_returns_sanitized_error(monkeypatch):
    client = _client(
        active={"status": "success", "data": {}},
        saved={"id": GENERATION},
        wallets=[],
    )

    async def fail_report(_payload):
        raise RuntimeError("private-key=do-not-leak /private/path")

    monkeypatch.setattr(reader, "_save_report", fail_report)
    result = _run(monkeypatch, client, _config(expected_runtime_instance=None))
    payload = _payload(result)

    assert payload["status"] == "degraded"
    assert payload["config"] == {"id": GENERATION}
    assert payload["report_error"] == "Report generation failed: RuntimeError"
    assert "do-not-leak" not in result.text


def test_report_cancellation_preserves_evidence_and_is_non_fatal(monkeypatch):
    client = _client(
        active={"status": "success", "data": {}},
        saved={"id": GENERATION},
        wallets=[],
    )

    async def cancel_report(_payload):
        raise asyncio.CancelledError

    monkeypatch.setattr(reader, "_save_report", cancel_report)
    payload = _payload(
        _run(monkeypatch, client, _config(expected_runtime_instance=None))
    )

    assert payload["status"] == "degraded"
    assert payload["config"] == {"id": GENERATION}
    assert payload["report_error"] == "Report generation failed: CancelledError"


def test_routine_store_report_failure_has_no_report_id(monkeypatch):
    client = _client(
        active={"status": "success", "data": {}},
        saved={"id": GENERATION},
        wallets=[],
    )

    async def get_client(_context):
        return client

    async def fail_report(_payload):
        raise RuntimeError("report storage unavailable")

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(reader, "_get_client", get_client)
    monkeypatch.setattr(reader, "_save_report", fail_report)
    routine = RoutineInfo(
        name="read_trend_aware_lp_session",
        config_class=reader.Config,
        run_fn=reader.run,
        source="agent:trend_aware_lp_rebalancer_agent",
    )
    store = RoutineStore()
    monkeypatch.setattr(store, "_resolve_routine", lambda _name: routine)
    monkeypatch.setattr(store, "_report_run", no_op)
    monkeypatch.setattr(store, "_fire_hooks", no_op)

    async def execute():
        instance_id = await store.execute(
            "trend_aware_lp_rebalancer_agent/read_trend_aware_lp_session",
            _config(expected_runtime_instance=None).model_dump(mode="json"),
            "local",
            agent="trend_aware_lp_rebalancer_agent",
        )
        await store._tasks[instance_id]
        return instance_id

    instance_id = asyncio.run(execute())
    instance = store.get_instance(instance_id)
    result = store.get_result(instance_id)

    assert instance is not None
    assert result is not None
    assert instance["status"] == "completed"
    assert instance["report_id"] is None
    assert _payload(result)["report_error"] == "Report generation failed: RuntimeError"


def test_pre_execution_config_rejection_creates_no_report(
    no_persistent_reports, monkeypatch
):
    async def no_op(*_args, **_kwargs):
        return None

    routine = RoutineInfo(
        name="read_trend_aware_lp_session",
        config_class=reader.Config,
        run_fn=reader.run,
        source="agent:trend_aware_lp_rebalancer_agent",
    )
    store = RoutineStore()
    monkeypatch.setattr(store, "_resolve_routine", lambda _name: routine)
    monkeypatch.setattr(store, "_report_run", no_op)
    monkeypatch.setattr(store, "_fire_hooks", no_op)
    invalid = _config().model_dump(mode="json")
    invalid["unknown"] = True

    async def execute():
        instance_id = await store.execute(
            "trend_aware_lp_rebalancer_agent/read_trend_aware_lp_session",
            invalid,
            "local",
            agent="trend_aware_lp_rebalancer_agent",
        )
        await store._tasks[instance_id]
        return instance_id

    instance_id = asyncio.run(execute())
    instance = store.get_instance(instance_id)

    assert instance is not None
    assert instance["status"] == "failed"
    assert instance["report_id"] is None
    assert no_persistent_reports == []


@pytest.mark.parametrize(
    "unsafe_value,error",
    [
        (
            "x" * reader.MAX_AGENT_RESULT_BYTES,
            "Agent-facing result exceeds 1000000-byte strict-JSON limit",
        ),
        (object(), "Agent-facing result is not strict-JSON serializable"),
    ],
)
def test_agent_result_bound_fails_closed_without_partial_authority(
    no_persistent_reports,
    monkeypatch,
    unsafe_value,
    error,
):
    client = _client(
        active={"status": "success", "data": {}},
        saved={"id": GENERATION, "unsafe": unsafe_value},
        wallets=[
            {
                "chain": "solana",
                "walletAddresses": ["wallet-secret"],
                "default_address": "wallet-secret",
            }
        ],
    )

    result = _run(monkeypatch, client, _config(expected_runtime_instance=None))
    payload = _payload(result)

    assert payload["status"] == "unavailable"
    assert payload["authority_observability"] == {
        "account": "not_observable",
        "wallet": "not_observable",
    }
    for field, empty in (
        ("gateway_wallet_observation", None),
        ("active_matches", []),
        ("namespace_conflicts", []),
        ("account_conflicts", []),
        ("wallet_conflicts", []),
        ("config", None),
        ("custom_info", None),
        ("bot_run_matches", []),
        ("archive_record", None),
    ):
        assert payload[field] == empty
    assert error in payload["errors"][0]
    assert reader._projection_error(result) is None
    assert no_persistent_reports[-1] == payload


def test_result_is_revalidated_after_report_attempt(monkeypatch):
    client = _client(
        active={"status": "success", "data": {}},
        saved={"id": GENERATION},
        wallets=[],
    )

    async def mutate_report_input(payload):
        payload["warnings"].append("x" * reader.MAX_AGENT_RESULT_BYTES)

    monkeypatch.setattr(reader, "_save_report", mutate_report_input)
    result = _run(monkeypatch, client, _config(expected_runtime_instance=None))
    payload = _payload(result)

    assert payload["status"] == "unavailable"
    assert payload["config"] is None
    assert "exceeds 1000000-byte strict-JSON limit" in payload["errors"][0]
    assert reader._projection_error(result) is None


def test_report_copy_redacts_wallets_and_secrets_without_changing_other_identity():
    raw = {
        "runtime_instance": RUNTIME,
        "pool_address": "ExactPoolAddress",
        "base_token_mint": "ExactMintAddress",
        "wallet_address": "secret-wallet",
        "walletAddresses": ["camel-wallet-secret"],
        "wallet_addresses": ["snake-wallet-secret"],
        "wallet_conflicts": [
            {
                "field": "wallet_address_overlap",
                "expected": None,
                "observed": "conflicting-wallet-secret",
            }
        ],
        "chain": "solana",
        "network": "mainnet-beta",
        "message": (
            "failed url=https://rpc.example/?token=query-secret&commitment=confirmed "
            "private_key=[1,2,3] ownerWalletId=wallet-inline-secret"
        ),
        "pem": (
            "key -----BEGIN PRIVATE KEY-----\nprivate-block-secret\n"
            "-----END PRIVATE KEY----- retained"
        ),
        "nested": {
            "private_key": "secret",
            "privateKey": "camel-secret",
            "api_key": "secret-api",
            "x-api-key": "header-secret",
            "rpcApiToken": "rpc-secret",
            "ownerWalletId": "wallet-id",
            "headers": {
                "X-Custom-Credential": "secret-header",
                "Accept": "application/json",
            },
        },
    }
    safe = reader._redact_report_value(raw)

    assert raw["wallet_address"] == "secret-wallet"
    assert safe == {
        "runtime_instance": RUNTIME,
        "pool_address": "ExactPoolAddress",
        "base_token_mint": "ExactMintAddress",
        "wallet_address": "[redacted]",
        "walletAddresses": "[redacted]",
        "wallet_addresses": "[redacted]",
        "wallet_conflicts": [
            {
                "field": "wallet_address_overlap",
                "expected": None,
                "observed": "[redacted]",
            }
        ],
        "chain": "solana",
        "network": "mainnet-beta",
        "message": (
            "failed url=https://rpc.example/?token=[redacted]&commitment=confirmed "
            "private_key=[redacted] ownerWalletId=[redacted]"
        ),
        "pem": "key [redacted] retained",
        "nested": {
            "private_key": "[redacted]",
            "privateKey": "[redacted]",
            "api_key": "[redacted]",
            "x-api-key": "[redacted]",
            "rpcApiToken": "[redacted]",
            "ownerWalletId": "[redacted]",
            "headers": "[redacted]",
        },
    }
    assert reader._redact_report_value({"solana_default_address": "secret-wallet"}) == {
        "solana_default_address": "[redacted]"
    }


@pytest.mark.parametrize(
    "raw,secret",
    [
        ("request failed; Authorization: Bearer bearer-secret", "bearer-secret"),
        ("request failed; Proxy-Authorization=Basic proxy-secret", "proxy-secret"),
        ("request failed; Cookie: sessionid=cookie-secret", "cookie-secret"),
        (
            "request failed; Cookie: first=first-secret; second=second-secret",
            "second-secret",
        ),
        (
            "request failed; Set-Cookie: sessionid=set-cookie-secret",
            "set-cookie-secret",
        ),
        ("transport retained Bearer standalone-secret", "standalone-secret"),
    ],
)
def test_report_string_redaction_removes_complete_headers_and_bearer_tokens(
    raw, secret
):
    safe = reader._sanitize_report_string(raw)

    assert secret not in safe
    assert "[redacted]" in safe


def test_real_report_builder_preserves_complete_evidence_and_redacts_secrets(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path))
    reports.reset_last_report_id()
    payload = reader._unavailable_payload(_config(include_archive_record=True), "none")
    payload.update(
        {
            "status": "complete",
            "gateway_wallet_observation": {
                "solana_default_address": "default-wallet-secret",
                "solana_wallets": [
                    {
                        "chain": "solana",
                        "network": "mainnet-beta",
                        "label": "review-metadata",
                        "walletAddresses": ["camel-wallet-secret"],
                        "wallet_addresses": ["snake-wallet-secret"],
                        "default_address": "default-wallet-secret",
                    }
                ],
            },
            "config": {
                **_live_config(),
                "quote_token_mint": "ExactNonSecretQuoteMint",
                "privateKey": "private-key-secret",
                "review_note": "visible-review-evidence-" + "x" * 2500,
            },
            "custom_info": _custom_info(),
            "archive_record": {
                "bot_name": RUNTIME,
                "deployment_status": "ARCHIVED",
                "stopped_at": "2026-08-30T12:30:00Z",
            },
            "errors": [],
        }
    )

    async def save_and_get_report_id():
        await REAL_SAVE_REPORT(payload)
        return reports.get_last_report_id()

    report_id = asyncio.run(save_and_get_report_id())
    assert report_id
    raw_report = reports.get_report_raw_html(report_id)
    assert raw_report is not None
    html, _filename = raw_report
    assert len(html) > 2_000
    for preserved in (
        GENERATION,
        RUNTIME,
        "ExactOpaquePoolAddress",
        "ExactOpaquePositionAddress",
        "ExactNonSecretQuoteMint",
        "ARCHIVED",
        "2026-08-30T12:30:00Z",
        "mainnet-beta",
        "review-metadata",
        "visible-review-evidence-",
    ):
        assert preserved in html
    assert "[redacted]" in html
    for secret in (
        "default-wallet-secret",
        "camel-wallet-secret",
        "snake-wallet-secret",
        "private-key-secret",
    ):
        assert secret not in html
    assert payload["gateway_wallet_observation"]["solana_wallets"][0][
        "walletAddresses"
    ] == ["camel-wallet-secret"]


def test_real_store_snapshot_truncation_preserves_complete_native_report(
    tmp_path,
    monkeypatch,
):
    marker = "TRAILING_SESSION_EVIDENCE"
    live_config = {
        **_live_config(),
        "review_note": "x" * 3_000 + marker,
    }
    client = _client(
        active={
            "status": "success",
            "data": {
                RUNTIME: {
                    "status": "running",
                    "recently_active": True,
                    "performance": {GENERATION: {"custom_info": _custom_info()}},
                }
            },
        },
        live=[live_config],
        runs={
            "status": "success",
            "data": [{"bot_name": RUNTIME, "account_name": ACCOUNT}],
        },
        wallets=[],
    )

    async def get_client(_context):
        return client

    original_routine_result = reader._routine_result

    def verbose_routine_result(payload):
        result = original_routine_result(payload)
        result.text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return result

    monkeypatch.setattr(reader, "_get_client", get_client)
    monkeypatch.setattr(reader, "_routine_result", verbose_routine_result)
    monkeypatch.setattr(reader, "_save_report", REAL_SAVE_REPORT)
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    reports.reset_last_report_id()

    routine = RoutineInfo(
        name="read_trend_aware_lp_session",
        config_class=reader.Config,
        run_fn=reader.run,
        source="agent:trend_aware_lp_rebalancer_agent",
    )
    store = RoutineStore()
    monkeypatch.setattr(store, "_resolve_routine", lambda _name: routine)

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(store, "_report_run", no_op)
    monkeypatch.setattr(store, "_fire_hooks", no_op)

    async def execute():
        instance_id = await store.execute(
            "trend_aware_lp_rebalancer_agent/read_trend_aware_lp_session",
            _config().model_dump(mode="json"),
            "local",
            agent="trend_aware_lp_rebalancer_agent",
        )
        await store._tasks[instance_id]
        return instance_id

    instance_id = asyncio.run(execute())
    instance = store.get_instance(instance_id)
    full_result = store.get_result(instance_id)

    assert instance is not None
    assert full_result is not None
    assert instance["status"] == "completed"
    assert len(full_result.text) > 2_000
    assert len(instance["result_text"]) == 2_000
    assert marker not in instance["result_text"]
    assert marker in json.dumps(instance["sections"])

    projected = manager_result_payload(
        "read_trend_aware_lp_session", instance_id, instance
    )
    journal = JournalManager(
        "trend_aware_lp_rebalancer_agent.orca_1",
        session_dir=tmp_path / "session",
    )
    snapshot_path = journal.save_full_snapshot(
        tick=1,
        timestamp="2026-08-30T12:00:00Z",
        system_prompt="test",
        response_text="test",
        tool_calls=[
            {"name": "manage_routines", "status": "completed", "output": projected}
        ],
        executors_data="",
        risk_state={},
        duration=0,
    )
    assert marker not in snapshot_path.read_text()

    report = reports.get_report_raw_html(instance["report_id"])
    assert report is not None
    report_entry = reports.get_report(instance["report_id"])
    assert report_entry is not None
    assert report_entry["agent"] == "trend_aware_lp_rebalancer_agent"
    assert (report_entry["source_type"], report_entry["source_name"]) == (
        "routine",
        "read_trend_aware_lp_session",
    )
    html, _filename = report
    assert marker in html


def test_result_contains_no_lifecycle_decision_or_recommendation(monkeypatch):
    client = _client(
        active={"status": "success", "data": {}},
        saved={"id": GENERATION},
        wallets=[],
    )
    payload = _payload(
        _run(monkeypatch, client, _config(expected_runtime_instance=None))
    )

    assert "lifecycle_state" not in payload
    assert "recommended_action" not in payload
    assert payload["mutation"] is False
