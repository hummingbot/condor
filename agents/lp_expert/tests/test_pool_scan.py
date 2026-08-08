from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

from conftest import SOL_MINT, pool_record, runtime_scope, strategy_config

from agents.lp_expert.core import orca
from agents.lp_expert.routines import lp_pool_scan


def _candidate():
    normalized, error = orca.normalize_record(
        pool_record(),
        "all",
        "volume24h",
        1,
        10_000,
    )
    assert error is None
    unique, rejected = orca.deduplicate([normalized])
    assert not rejected
    return orca.rank_pools(unique)[0]


def _candidates(count):
    candidate = _candidate()
    return [
        {
            **copy.deepcopy(candidate),
            "pool_address": f"{candidate['pool_address'][:-1]}{(index + 1) % 10}",
            "neutral_rank": index + 1,
            "tvl_floor_multiple": 10.0,
            "meets_profile_target": True,
        }
        for index in range(count)
    ]


def _scan(candidates, *, status="complete"):
    return {
        "status": status,
        "deployable": status == "complete" and bool(candidates),
        "minimum_tvl_usd": 10_000,
        "source_coverage": {
            "required_requests": 4,
            "completed_requests": 4 if status == "complete" else 3,
            "requests": [],
        },
        "universe": {
            "raw_records": len(candidates),
            "normalized_records": len(candidates),
            "valid_unique_pools": len(candidates),
            "returned_candidates": len(candidates),
        },
        "technical_rejections": {},
        "candidates": candidates,
    }


class Gateway:
    def __init__(self, tokens=None):
        self.tokens = (
            [
                {
                    "address": SOL_MINT,
                    "symbol": "SOL",
                    "decimals": 9,
                }
            ]
            if tokens is None
            else copy.deepcopy(tokens)
        )

    async def get_network_tokens(self, network):
        assert network == "solana-mainnet-beta"
        return {"tokens": copy.deepcopy(self.tokens)}


def _install(
    monkeypatch,
    tmp_path,
    *,
    candidate_count,
    config=None,
    status="complete",
    gateway=None,
):
    scope = runtime_scope(tmp_path, tick=2, config=config)
    client = SimpleNamespace(gateway=gateway or Gateway())
    calls = []
    captured = {}
    monkeypatch.setattr(lp_pool_scan, "resolve_runtime", lambda _: scope)

    async def get_client(_):
        return client

    async def scan_pools(limit, minimum_tvl_usd):
        calls.append((limit, minimum_tvl_usd))
        return _scan(_candidates(candidate_count), status=status)

    async def attach(payload, **kwargs):
        captured["payload"] = copy.deepcopy(payload)
        captured["input"] = copy.deepcopy(kwargs["routine_input"])
        return {
            **payload,
            "report_id": "20260807_103105_lp_pool_scan_1234567890abcdef",
            "report_error": None,
        }

    monkeypatch.setattr(lp_pool_scan, "get_hummingbot_client", get_client)
    monkeypatch.setattr(lp_pool_scan.orca, "scan_pools", scan_pools)
    monkeypatch.setattr(lp_pool_scan, "attach_report", attach)
    return calls, captured


def _config():
    return lp_pool_scan.Config(controller_id="lp_expert.orca_1", tick=2)


def test_pool_scan_returns_complete_registered_candidates_and_full_report(
    monkeypatch, tmp_path
):
    calls, captured = _install(monkeypatch, tmp_path, candidate_count=3)

    raw = asyncio.run(lp_pool_scan.run(_config(), None))
    result = json.loads(raw)

    assert calls == [(3, 10_000)]
    assert result["status"] == "complete"
    assert result["coverage"] == [4, 4]
    assert result["returned_candidates"] == 3
    assert result["omitted_candidates"] == 0
    assert result["transport_limited"] is False
    assert result["deployable"] is True
    assert result["tvl_policy"] == ["balanced", 10_000, 50_000]
    assert [candidate["rank"] for candidate in result["candidates"]] == [1, 2, 3]
    assert all(candidate["tvl_x"] == 10 for candidate in result["candidates"])
    assert len(raw) <= lp_pool_scan._TRANSPORT_MAX_CHARS
    assert len(captured["payload"]["candidates"]) == 3
    assert "volume_usd" in captured["payload"]["candidates"][0]
    assert captured["payload"]["candidates"][0]["meets_profile_target"] is True
    assert "volume_usd" not in result["candidates"][0]
    assert captured["input"] == {
        "controller_id": "lp_expert.orca_1",
        "tick": 2,
    }


def test_pool_scan_returns_fixed_top_ranked_prefix(monkeypatch, tmp_path):
    configured = strategy_config(candidate_scan_limit=6)
    calls, captured = _install(
        monkeypatch,
        tmp_path,
        candidate_count=6,
        config=configured,
    )

    raw = asyncio.run(lp_pool_scan.run(_config(), None))
    result = json.loads(raw)

    assert calls == [(6, 10_000)]
    assert result["scanned_candidates"] == 6
    assert result["eligible_candidates"] == 6
    assert result["returned_candidates"] == 4
    assert result["omitted_candidates"] == 2
    assert result["transport_limited"] is True
    assert [candidate["rank"] for candidate in result["candidates"]] == [1, 2, 3, 4]
    assert len(captured["payload"]["candidates"]) == 6
    assert len(raw) <= lp_pool_scan._TRANSPORT_MAX_CHARS


def test_pool_scan_uses_configurable_hard_tvl_floor(monkeypatch, tmp_path):
    configured = strategy_config(
        min_pool_tvl_usd=25_000,
        risk_profile_tvl_targets={
            "steady": 100_000,
            "balanced": 50_000,
            "opportunistic": 25_000,
            "exploratory": 25_000,
        },
    )
    calls, captured = _install(
        monkeypatch,
        tmp_path,
        candidate_count=3,
        config=configured,
    )

    result = json.loads(asyncio.run(lp_pool_scan.run(_config(), None)))

    assert calls == [(3, 25_000)]
    assert result["tvl_policy"] == ["balanced", 25_000, 50_000]
    assert captured["payload"]["tvl_policy"]["minimum_tvl_usd"] == 25_000


def test_pool_scan_keeps_unregistered_orca_candidates(monkeypatch, tmp_path):
    calls, captured = _install(
        monkeypatch,
        tmp_path,
        candidate_count=3,
        gateway=Gateway(tokens=[]),
    )

    result = json.loads(asyncio.run(lp_pool_scan.run(_config(), None)))

    assert calls == [(3, 10_000)]
    assert result["status"] == "complete"
    assert result["eligible_candidates"] == 3
    assert result["returned_candidates"] == 3
    assert result["deployable"] is True
    assert [candidate["rank"] for candidate in result["candidates"]] == [1, 2, 3]
    assert len(captured["payload"]["token_registration_required"]) == 3
    assert captured["payload"]["token_conflicts"] == []
    assert all(
        candidate["gateway_registered"] is False
        for candidate in captured["payload"]["candidates"]
    )


def test_pool_scan_excludes_existing_token_metadata_conflicts(monkeypatch, tmp_path):
    _, captured = _install(
        monkeypatch,
        tmp_path,
        candidate_count=3,
        gateway=Gateway(
            tokens=[
                {
                    "address": SOL_MINT,
                    "symbol": "NOT_SOL",
                    "decimals": 9,
                }
            ]
        ),
    )

    result = json.loads(asyncio.run(lp_pool_scan.run(_config(), None)))

    assert result["eligible_candidates"] == 0
    assert result["deployable"] is False
    assert captured["payload"]["token_registration_required"] == []
    assert len(captured["payload"]["token_conflicts"]) == 3


def test_pool_scan_hard_limit_matches_reference_response_capacity(monkeypatch):
    assert lp_pool_scan.RETURNED_CANDIDATE_LIMIT == 4
    candidates = _candidates(5)
    payload = {
        "status": "complete",
        "controller_id": "lp_expert.orca_1",
        "tick": 999999,
        "candidate_scan": _scan(candidates),
        "candidates": candidates,
        "tvl_policy": {
            "default_risk_posture": "balanced",
            "minimum_tvl_usd": 10_000,
            "profile_target_tvl_usd": 50_000,
        },
        "report_id": "20260807_103105_lp_pool_scan_1234567890abcdef",
        "report_error": None,
    }

    monkeypatch.setattr(lp_pool_scan, "RETURNED_CANDIDATE_LIMIT", 4)
    four = json.dumps(
        lp_pool_scan._compact_result(payload),
        default=str,
        separators=(",", ":"),
    )
    monkeypatch.setattr(lp_pool_scan, "RETURNED_CANDIDATE_LIMIT", 5)
    five = json.dumps(
        lp_pool_scan._compact_result(payload),
        default=str,
        separators=(",", ":"),
    )

    assert len(four) <= lp_pool_scan._TRANSPORT_MAX_CHARS
    assert len(five) > lp_pool_scan._TRANSPORT_MAX_CHARS


def test_pool_scan_incomplete_coverage_fails_closed(monkeypatch, tmp_path):
    calls, _ = _install(
        monkeypatch,
        tmp_path,
        candidate_count=3,
        status="incomplete",
    )

    result = json.loads(asyncio.run(lp_pool_scan.run(_config(), None)))

    assert calls == [(3, 10_000)]
    assert result["status"] == "incomplete"
    assert result["coverage"] == [3, 4]
    assert result["eligible_candidates"] == 0
    assert result["candidates"] == []
    assert result["deployable"] is False
