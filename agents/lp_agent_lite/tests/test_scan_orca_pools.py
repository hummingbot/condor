from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.lp_agent_lite.routines import scan_orca_pools as scan

BASE_MINTS = ("2" * 44, "3" * 44, "4" * 44, "5" * 44, "6" * 44)
POOL_ADDRESSES = ("7" * 44, "8" * 44, "9" * 44, "A" * 44, "B" * 44)


def _record(
    index: int,
    *,
    symbol: str | None = None,
    tvl: float | None = None,
    volume_scale: float | None = None,
    fee_scale: float | None = None,
    movement: float | None = None,
):
    tvl = tvl if tvl is not None else 100_000 - index * 10_000
    volume_scale = volume_scale if volume_scale is not None else 5 - index
    fee_scale = fee_scale if fee_scale is not None else 5 - index
    movement = movement if movement is not None else 0.1 + index
    return {
        "address": POOL_ADDRESSES[index],
        "tokenA": {
            "symbol": symbol or f"T{index}",
            "mint": BASE_MINTS[index],
            "decimals": 9,
        },
        "tokenB": {"symbol": "USDC", "mint": scan.USDC_MINT, "decimals": 6},
        "price": 100 + index,
        "tvlUsdc": tvl,
        "tickSpacing": 64,
        "feeRate": 300,
        "adaptiveFeeEnabled": False,
        "hasWarning": False,
        "stats": {
            window: {
                "volume": volume_scale * hours * 1_000,
                "fees": fee_scale * hours,
                "priceDelta": movement,
            }
            for window, hours in zip(scan.WINDOWS, (1, 4, 24, 168), strict=True)
        },
    }


def _config(**overrides):
    values = {
        "min_pool_tvl_usd": "10000",
        "candidate_scan_limit": 4,
        "mcda_weights": {
            "fee_productivity": "0.40",
            "recent_activity": "0.25",
            "price_stability": "0.15",
            "liquidity_depth": "0.10",
            "execution_simplicity": "0.10",
        },
    }
    values.update(overrides)
    return scan.Config(**values)


def _install(monkeypatch, records, *, failed=(), transform=None):
    calls = []

    def fetch(url, timeout_seconds):
        lens = parse_qs(urlsplit(url).query)["sortBy"][0]
        calls.append((lens, timeout_seconds, parse_qs(urlsplit(url).query)))
        if lens in failed:
            raise TimeoutError("fixture timeout")
        payload = copy.deepcopy(records)
        if transform is not None:
            payload = transform(lens, payload)
        return {"data": payload}

    monkeypatch.setattr(scan, "_fetch_json", fetch)
    return calls


def _run(config):
    raw = asyncio.run(scan.run(config, None))
    assert len(raw) < 1_900
    result = json.loads(raw)
    assert result["report_id"] == "rpt001"
    assert result["report_error"] is None
    return raw, result


def test_complete_four_lens_scan_deduplicates_and_ranks_neutral_facts(monkeypatch):
    calls = _install(monkeypatch, [_record(0), _record(1), _record(2)])

    raw, result = _run(_config())

    assert result["status"] == "complete"
    assert result["coverage"] == {
        "completed": 4,
        "required": 4,
        "failed_lenses": [],
    }
    assert {call[0] for call in calls} == set(scan.DISCOVERY_LENSES)
    assert all(call[1] == 12.0 for call in calls)
    assert all(call[2]["stats"] == ["1h,4h,24h,7d"] for call in calls)
    assert result["raw_records"] == 12
    assert result["eligible_pools"] == 3
    assert result["rejected_records"] == 0
    assert [candidate["rank"] for candidate in result["candidates"]] == [1, 2, 3]
    assert result["candidates"][0]["pool"] == POOL_ADDRESSES[0]
    assert all(candidate["source_count"] == 4 for candidate in result["candidates"])
    assert result["windows"] == ["1h", "4h", "24h", "7d"]
    assert "recommendation" not in raw
    assert result["mutation"] is False


def test_two_completed_lenses_are_degraded_but_keep_observed_rank(monkeypatch):
    failed = {"yieldovertvl7d", "volume7d"}
    _install(monkeypatch, [_record(0), _record(1)], failed=failed)

    _, result = _run(_config())

    assert result["status"] == "degraded"
    assert result["coverage"]["completed"] == 2
    assert result["coverage"]["failed_lenses"] == [
        "yieldovertvl7d",
        "volume7d",
    ]
    assert result["returned"] == 2
    assert all(candidate["source_count"] == 2 for candidate in result["candidates"])


def test_fewer_than_two_lenses_are_unavailable_and_return_no_partial_rank(monkeypatch):
    _install(monkeypatch, [_record(0)], failed=set(scan.DISCOVERY_LENSES[1:]))

    _, result = _run(_config())

    assert result["status"] == "unavailable"
    assert result["coverage"]["completed"] == 1
    assert result["eligible_pools"] == 1
    assert result["returned"] == 0
    assert result["omitted"] == 1
    assert result["candidates"] == []


def test_technical_gates_and_duplicate_identity_conflicts_fail_closed(monkeypatch):
    warned = _record(0)
    warned["hasWarning"] = True
    wrong_quote = _record(1)
    wrong_quote["tokenB"]["mint"] = "C" * 44
    below_tvl = _record(2, tvl=9_999)

    def conflict_one_lens(lens, records):
        if lens == "volume7d":
            records[3]["tokenA"]["mint"] = "D" * 44
        return records

    _install(
        monkeypatch,
        [warned, wrong_quote, below_tvl, _record(3)],
        transform=conflict_one_lens,
    )

    _, result = _run(_config())

    assert result["status"] == "complete"
    assert result["eligible_pools"] == 0
    assert result["returned"] == 0
    assert result["rejected_records"] == 16


def test_equal_scores_use_pool_address_as_deterministic_tie_break(monkeypatch):
    first = _record(0, tvl=50_000, volume_scale=2, fee_scale=2, movement=1)
    second = _record(1, tvl=50_000, volume_scale=2, fee_scale=2, movement=1)
    _install(monkeypatch, [second, first])

    _, first_result = _run(_config())
    _, second_result = _run(_config())

    expected = sorted([POOL_ADDRESSES[0], POOL_ADDRESSES[1]])
    assert [item["pool"] for item in first_result["candidates"]] == expected
    assert first_result == second_result


def test_null_non_24h_price_deltas_match_live_orca_shape(monkeypatch):
    record = _record(0)
    for window in ("1h", "4h", "7d"):
        record["stats"][window]["priceDelta"] = None
    _install(monkeypatch, [record])

    _, result = _run(_config())

    assert result["status"] == "complete"
    assert result["eligible_pools"] == 1
    assert result["candidates"][0]["price_change"] == [None, None, 0.1, None]


def test_configurable_weights_change_only_neutral_rank(monkeypatch):
    fee_pool = _record(0, tvl=50_000, volume_scale=1, fee_scale=10, movement=1)
    activity_pool = _record(1, tvl=50_000, volume_scale=10, fee_scale=1, movement=1)
    _install(monkeypatch, [fee_pool, activity_pool])

    fee_weights = {
        "fee_productivity": 1,
        "recent_activity": 0,
        "price_stability": 0,
        "liquidity_depth": 0,
        "execution_simplicity": 0,
    }
    _, fee_result = _run(_config(mcda_weights=fee_weights))

    activity_weights = {
        "fee_productivity": 0,
        "recent_activity": 1,
        "price_stability": 0,
        "liquidity_depth": 0,
        "execution_simplicity": 0,
    }
    _, activity_result = _run(_config(mcda_weights=activity_weights))

    assert fee_result["candidates"][0]["pool"] == POOL_ADDRESSES[0]
    assert activity_result["candidates"][0]["pool"] == POOL_ADDRESSES[1]


def test_transport_returns_only_a_contiguous_ranked_prefix(monkeypatch):
    records = [_record(index, symbol="LONGSYMBOL123456") for index in range(5)]
    _install(monkeypatch, records)

    raw, result = _run(_config(candidate_scan_limit=100))

    assert len(raw) <= 1_800
    assert 1 <= result["returned"] <= 4
    assert result["omitted"] == 5 - result["returned"]
    assert [item["rank"] for item in result["candidates"]] == list(
        range(1, result["returned"] + 1)
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_pool_tvl_usd": "nan"},
        {"candidate_scan_limit": 101},
        {"candidate_scan_limit": True},
        {"request_size": 101},
        {"timeout_seconds": "inf"},
        {"unknown": 1},
        {"mcda_weights": {"fee_productivity": 1}},
        {
            "mcda_weights": {
                "fee_productivity": ".4",
                "recent_activity": ".2",
                "price_stability": ".1",
                "liquidity_depth": ".1",
                "execution_simplicity": ".1",
            }
        },
    ],
)
def test_strict_config_and_weight_validation(overrides):
    with pytest.raises(ValidationError):
        _config(**overrides)
