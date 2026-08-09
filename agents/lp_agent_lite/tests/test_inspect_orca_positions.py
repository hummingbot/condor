import asyncio
import json

import pytest
from pydantic import ValidationError

from agents.lp_agent_lite.routines import inspect_orca_positions as routine

WALLET = "2" * 32
POSITION = "3" * 32
POOL = "4" * 32
OTHER_POOL = "5" * 32
SIGNATURE = "6" * 64


def _summary(action="open_position", timestamp=1_000, pool=POOL):
    return {
        "position_address": POSITION,
        "whirlpool_address": pool,
        "last_action_type": action,
        "last_action_timestamp": timestamp,
        "pnl_reliable": True,
        "cost_basis": {"usd_value": 2.1},
        "realized_pnl": {"usd_value": 0},
        "total_fees_collected": {"usd_value": 0.01},
    }


def _history(action="open_position", timestamp=1_000, pool=POOL):
    return {
        "position_address": POSITION,
        "whirlpool_address": pool,
        "user_wallet_address": WALLET,
        "action_type": action,
        "timestamp": timestamp,
        "transaction_signature": SIGNATURE,
        "pnl_reliable": True,
        "cost_basis": {"usd_value": 2.1},
        "realized_pnl": {"usd_value": 0},
        "total_fees_collected": {"usd_value": 0.01},
    }


def _envelope(rows, *, next_cursor=None):
    return {
        "data": rows,
        "meta": {"cursor": {"previous": None, "next": next_cursor}},
    }


def _config(**changes):
    values = {"wallet_address": WALLET, "position_address": POSITION}
    values.update(changes)
    return routine.Config(**values)


def _run(config):
    raw = asyncio.run(routine.run(config, None))
    assert len(raw) < 1_900
    result = json.loads(raw)
    assert result["report_id"] == "rpt001"
    assert result["report_error"] is None
    return result


def _install(monkeypatch, summary, history):
    calls = []

    async def get_json(path, params, timeout):
        calls.append((path, params, timeout))
        result = summary if path.endswith("summary") else history
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(routine, "_get_json", get_json)
    return calls


def test_matching_active_summary_and_history_are_complete(monkeypatch):
    calls = _install(
        monkeypatch,
        _envelope([_summary()]),
        _envelope([_history()]),
    )
    result = _run(_config(expected_pool_address=POOL))

    assert result["source"] == "orca_stats_api"
    assert result["consistency"] == "eventual"
    assert result["status"] == "complete"
    assert result["indexed_state"] == "active"
    assert result["consensus"] is True
    assert result["event"][:4] == ["open_position", 1_000, SIGNATURE, POOL]
    assert calls == [
        (
            "/api/pnl/summary",
            {"wallet": WALLET, "position": POSITION},
            12,
        ),
        (
            "/api/pnl/history",
            {"wallet": WALLET, "position": POSITION, "limit": "20"},
            12,
        ),
    ]


def test_matching_close_is_indexed_closed_not_onchain_absence(monkeypatch):
    _install(
        monkeypatch,
        _envelope([_summary("close_position", 1_100)]),
        _envelope([_history("close_position", 1_100)]),
    )
    result = _run(_config())

    assert result["status"] == "complete"
    assert result["indexed_state"] == "closed"
    assert result["consensus"] is True
    assert result["mutation"] is False


def test_mutation_inside_indexing_window_is_degraded_and_not_caught_up(
    monkeypatch,
):
    _install(
        monkeypatch,
        _envelope([_summary("open_position", 800)]),
        _envelope([_history("open_position", 800)]),
    )
    monkeypatch.setattr(routine.time, "time", lambda: 1_000)
    result = _run(
        _config(expected_action_type="close_position", mutation_started_at=950)
    )

    assert result["status"] == "degraded"
    assert result["indexed_state"] == "active"
    assert result["lag"] == {
        "assumed_seconds": 90,
        "window_elapsed": False,
        "wait_remaining_seconds": 40,
        "caught_up": False,
    }


def test_matching_expected_action_after_lag_is_caught_up(monkeypatch):
    _install(
        monkeypatch,
        _envelope([_summary("close_position", 1_900)]),
        _envelope([_history("close_position", 1_900)]),
    )
    monkeypatch.setattr(routine.time, "time", lambda: 2_000)
    result = _run(
        _config(
            expected_pool_address=POOL,
            expected_action_type="close_position",
            mutation_started_at=1_900,
        )
    )

    assert result["status"] == "complete"
    assert result["indexed_state"] == "closed"
    assert result["lag"]["window_elapsed"] is True
    assert result["lag"]["caught_up"] is True


def test_endpoint_disagreement_is_uncertain_not_false_state(monkeypatch):
    _install(
        monkeypatch,
        _envelope([_summary("open_position", 1_000)]),
        _envelope([_history("close_position", 1_100)]),
    )
    result = _run(_config())

    assert result["status"] == "degraded"
    assert result["indexed_state"] == "uncertain"
    assert result["consensus"] is False
    assert "event" not in result


def test_two_empty_exact_results_mean_not_indexed_not_absent(monkeypatch):
    _install(monkeypatch, _envelope([]), _envelope([]))
    result = _run(_config())

    assert result["status"] == "complete"
    assert result["indexed_state"] == "not_indexed"
    assert result["consensus"] is True


def test_one_available_endpoint_is_degraded_evidence(monkeypatch):
    _install(
        monkeypatch,
        _envelope([_summary()]),
        asyncio.TimeoutError(),
    )
    result = _run(_config())

    assert result["status"] == "degraded"
    assert result["indexed_state"] == "active"
    assert result["consensus"] is False
    assert result["api"]["history"] == "unavailable"
    assert result["errors"] == {"history": "timeout"}


def test_both_endpoints_unavailable_fail_closed(monkeypatch):
    _install(
        monkeypatch,
        asyncio.TimeoutError(),
        ValueError("http_503"),
    )
    result = _run(_config())

    assert result["status"] == "unavailable"
    assert result["indexed_state"] == "uncertain"
    assert result["errors"] == {"summary": "timeout", "history": "http_503"}


def test_expected_pool_mismatch_invalidates_consensus(monkeypatch):
    _install(
        monkeypatch,
        _envelope([_summary(pool=POOL)]),
        _envelope([_history(pool=POOL)]),
    )
    result = _run(_config(expected_pool_address=OTHER_POOL))

    assert result["status"] == "degraded"
    assert result["indexed_state"] == "uncertain"
    assert result["consensus"] is False


def test_history_cursor_is_exposed_without_expanding_response(monkeypatch):
    _install(
        monkeypatch,
        _envelope([_summary()]),
        _envelope([_history()], next_cursor="opaque"),
    )
    result = _run(_config())

    assert result["status"] == "complete"
    assert result["api"]["history_more"] is True


def test_config_requires_exact_identity_and_paired_mutation_context():
    with pytest.raises(ValidationError):
        routine.Config(wallet_address=WALLET, position_address="not-an-address")
    with pytest.raises(ValidationError):
        _config(expected_action_type="open_position")
    with pytest.raises(ValidationError):
        _config(mutation_started_at=1_000)
