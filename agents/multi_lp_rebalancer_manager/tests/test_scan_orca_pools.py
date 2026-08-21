import asyncio
import json
from decimal import Decimal

import pytest

from agents.multi_lp_rebalancer_manager.routines import scan_orca_pools as scanner


def _address(character: str) -> str:
    return character * 32


def _config(**changes):
    data = {
        "quote_token_mint": scanner.USDC_MINT,
        "min_pool_tvl_usd": 10_000,
        "candidate_scan_limit": 9,
        "mcda_weights": {
            "fee_productivity": 0.4,
            "recent_activity": 0.25,
            "price_stability": 0.15,
            "liquidity_depth": 0.1,
            "execution_simplicity": 0.1,
        },
        "risk_profile": "balanced",
        "trend_thresholds_pct": {
            "change_24h": 1,
            "recent_history": 1,
            "seven_day_history": 3,
        },
        "trend_history_min_points": 7,
        "range_profiles": {
            "conservative": {
                "movement_multiplier": 2,
                "min_width_pct": 4,
                "max_width_pct": 20,
            },
            "balanced": {
                "movement_multiplier": 1.5,
                "min_width_pct": 2,
                "max_width_pct": 12,
            },
            "high_yield": {
                "movement_multiplier": 1,
                "min_width_pct": 1,
                "max_width_pct": 8,
            },
        },
        "downtrend_width_multiplier": 1.25,
        "downside_offset_width_ratio": 0.25,
        "rebalance_threshold_width_ratio": 0.25,
        "minimum_rebalance_threshold_pct": 0.25,
        "maximum_rebalance_threshold_pct": 5,
    }
    data.update(changes)
    return scanner.Config(**data)


def _record(*, pool="A", base="B", quote_first=False, delta="0.05", history=None):
    base_token = {"address": _address(base), "symbol": f"T{base}", "decimals": 6}
    quote_token = {"address": scanner.USDC_MINT, "symbol": "USDC", "decimals": 6}
    stats = {
        window: {
            "volume": str(value * 1000),
            "fees": str(value * 10),
            "priceDelta": delta if window == "24h" else None,
        }
        for window, value in zip(scanner.WINDOWS, (1, 4, 24, 168), strict=True)
    }
    return {
        "address": _address(pool),
        "tokenA": quote_token if quote_first else base_token,
        "tokenB": base_token if quote_first else quote_token,
        "price": "0.1" if quote_first else "10",
        "priceHistory7d": (
            history
            if history is not None
            else ["8", "8.2", "8.4", "8.8", "9", "9.4", "9.8", "10"]
        ),
        "tvlUsdc": "100000",
        "tickSpacing": 64,
        "feeRate": 300,
        "adaptiveFeeEnabled": False,
        "hasWarning": False,
        "stats": stats,
    }


def _candidate(result, index=0):
    return dict(zip(scanner.CANDIDATE_FIELDS, result["candidates"][index], strict=True))


def test_trend_and_range_are_deterministic():
    row = scanner._normalize(_record(), "volume24h", _config(), Decimal("1000"))
    trend = row["trend"]
    assert trend["classification"] == "UP"
    assert trend["votes"].count("UP") >= 2
    assert trend["range"]["side"] == "RANGE"
    assert (
        trend["signal_id"]
        == scanner._normalize(_record(), "volume7d", _config(), Decimal("1000"))[
            "trend"
        ]["signal_id"]
    )


def test_downtrend_uses_downside_buy_range():
    history = ["10", "9.8", "9.5", "9.2", "9", "8.7", "8.4", "8"]
    row = scanner._normalize(
        _record(delta="-0.05", history=history), "volume24h", _config(), Decimal("1000")
    )
    trend = row["trend"]
    assert trend["classification"] == "DOWN"
    assert trend["range"]["side"] == "BUY"
    assert Decimal(trend["range"]["upper_price"]) < row["current_price"]
    assert Decimal(trend["range"]["lower_price"]) < Decimal(
        trend["range"]["upper_price"]
    )


def test_usdc_token_a_is_rejected_until_executor_supports_explicit_orientation():
    record = _record(quote_first=True, delta="-0.0476190476190476")
    record["priceHistory7d"] = [
        str(Decimal(1) / Decimal(value))
        for value in (8, 8.2, 8.4, 8.8, 9, 9.4, 9.8, 10)
    ]
    with pytest.raises(ValueError, match="unsupported_usdc_token_a_orientation"):
        scanner._normalize(record, "volume24h", _config(), Decimal("1000"))


def test_missing_malformed_or_short_history_preserves_pool_as_unknown():
    variants = (None, ["10", "bad"], ["10", "10.1"])
    reasons = (
        "missing_price_history_7d",
        "invalid_price_history_7d",
        "insufficient_price_history_7d",
    )
    for history, reason in zip(variants, reasons, strict=True):
        record = _record()
        if history is None:
            record.pop("priceHistory7d")
        else:
            record["priceHistory7d"] = history
        row = scanner._normalize(record, "volume24h", _config(), Decimal("1000"))
        assert row["trend"]["classification"] == "UNKNOWN"
        assert row["trend"]["unknown_reason"] == reason
        assert row["trend"]["range"] is None


def test_run_returns_degraded_current_rank_without_mutation(monkeypatch):
    response = {"data": [_record()]}
    calls = 0

    def fetch(url, timeout):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise TimeoutError("lens unavailable")
        return response

    monkeypatch.setattr(scanner, "_fetch_json", fetch)
    raw = asyncio.run(scanner.run(_config(), None))
    result = json.loads(raw)
    assert result["status"] == "degraded"
    assert result["coverage"]["completed_lenses"] == 2
    assert _candidate(result)["trend"] == "UP"
    assert result["mutation"] is False
    assert len(raw) <= scanner.MAX_RESULT_CHARS


def test_nine_candidate_scan_returns_at_least_five_rows_below_transport_limit(
    monkeypatch,
):
    identities = "ABCDEFGHJK"
    records = [
        _record(pool=pool, base=base)
        for pool, base in zip(identities[:9], identities[1:10], strict=True)
    ]
    for record, pool, base in zip(
        records, identities[:9], identities[1:10], strict=True
    ):
        record["address"] = pool * 44
        record["tokenA"]["address"] = base * 44
        record["tokenA"]["symbol"] = base * 32
    response = {"data": records}
    monkeypatch.setattr(scanner, "_fetch_json", lambda _url, _timeout: response)

    raw = asyncio.run(scanner.run(_config(), None))
    result = json.loads(raw)

    assert len(raw) <= scanner.MAX_RESULT_CHARS == 1_899
    assert result["status"] == "complete"
    assert result["format"] == "compact_rows_v1"
    assert result["returned"] >= scanner.MIN_TRANSPORT_CANDIDATES == 5
    assert all(
        len(row) == len(scanner.CANDIDATE_FIELDS) for row in result["candidates"]
    )
    assert all(_candidate(result, index)["pool_address"] for index in range(5))
    assert len({_candidate(result, index)["base_mint"] for index in range(5)}) == 5


def test_transport_prioritizes_distinct_base_choices_without_changing_rank(monkeypatch):
    records = [_record(pool=pool, base="Z") for pool in "ABC"]
    records.extend([_record(pool="D", base="Y"), _record(pool="E", base="X")])
    response = {"data": records}
    monkeypatch.setattr(scanner, "_fetch_json", lambda _url, _timeout: response)

    result = json.loads(asyncio.run(scanner.run(_config(), None)))
    first_five = [_candidate(result, index) for index in range(5)]

    assert len({row["base_mint"] for row in first_five}) == 3
    assert [row["rank"] for row in first_five] == [1, 4, 5, 2, 3]


def test_session_four_sized_values_still_transport_five_candidates(monkeypatch):
    records = [
        _record(pool=pool, base=base)
        for pool, base in zip("ABCDE", "FGHJK", strict=True)
    ]
    for index, record in enumerate(records, 1):
        record["address"] = str(index) + "A" * 43
        record["tokenA"]["address"] = str(index) + "B" * 43
        record["tokenA"]["symbol"] = f"TOKEN{index}" + "X" * 25
        record["price"] = "224.790013751637"
        record["tvlUsdc"] = "562792.673138543298"
    response = {"data": records}
    monkeypatch.setattr(scanner, "_fetch_json", lambda _url, _timeout: response)

    raw = asyncio.run(scanner.run(_config(candidate_scan_limit=5), None))
    result = json.loads(raw)

    assert len(raw) <= scanner.MAX_RESULT_CHARS == 1_899
    assert result["status"] == "complete"
    assert result["returned"] == 5
    assert len(result["candidates"]) == 5
    assert all(
        len(row) == len(scanner.CANDIDATE_FIELDS) for row in result["candidates"]
    )


def test_five_candidate_floor_survives_worst_case_degraded_coverage():
    row = [
        30,
        "A" * 44,
        "S" * 32,
        "B" * 44,
        18,
        "1.0000000e+99",
        "0.12345678",
        "0.12345678",
        "0.12345678",
        "0.12345678",
        "SIDEWAYS",
        "f" * 32,
        "RANGE",
        "99.999999",
        "99.999999",
        "99.999999",
    ]
    raw = scanner._bounded_result(
        {
            "schema": scanner.SCHEMA,
            "status": "degraded",
            "coverage": {
                "completed_lenses": 2,
                "failed_lens_count": 2,
                "missing_required_count": 3,
            },
            "observed_at": "1786638132.354",
            "risk_profile": "balanced",
            "raw_records": 400,
            "eligible_pools": 400,
            "rejected_records": 400,
            "mutation": False,
        },
        [row] * 5,
    )
    result = json.loads(raw)

    assert len(raw) <= scanner.MAX_RESULT_CHARS == 1_899
    assert result["status"] == "degraded"
    assert result["returned"] == 5
