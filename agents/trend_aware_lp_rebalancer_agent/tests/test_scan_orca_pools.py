from __future__ import annotations

import asyncio
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.trend_aware_lp_rebalancer_agent.routines import scan_orca_pools as scan
from condor import reports
from condor.agents.journal import JournalManager
from condor.routine_store import RoutineStore
from mcp_servers.condor.tools.routines import _result_payload as manager_result_payload
from routines.base import RoutineInfo

OBSERVED_AT = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
ORIGINAL_SAVE_REPORT = scan._save_report


def _base58_encode(value: bytes) -> str:
    alphabet = scan._BASE58_ALPHABET
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = alphabet[remainder] + encoded
    leading_zeroes = len(value) - len(value.lstrip(b"\0"))
    return "1" * leading_zeroes + (encoded or "")


def _address(character: str) -> str:
    return _base58_encode(b"\0" * 31 + bytes([ord(character)]))


def _config(**changes):
    values = {
        "risk_profile": "balanced",
        "min_pool_tvl_usd": "10000",
        "min_fee_productivity_bps_per_day": "2",
        "candidate_scan_limit": 6,
        "excluded_base_mints": [],
        "excluded_pool_addresses": [],
        "refresh_pool_addresses": [],
    }
    values.update(changes)
    return scan.Config(**values)


def _record(
    index: int = 0,
    *,
    pool: str | None = None,
    base: str | None = None,
    symbol: str | None = None,
    tvl: Decimal | str = "100000",
    change_24h: Decimal | str = "0.024",
    history: list | None = None,
    volumes: tuple[Decimal | int, ...] = (1000, 4000, 24000, 168000),
    fees: tuple[Decimal | int, ...] = (10, 40, 240, 1680),
    adaptive_fee: bool = False,
    warning: bool = False,
    tick_spacing: int = 64,
    source_reported_at: str | None = None,
    quote_first: bool = False,
):
    pool = pool or _address("A" if index == 0 else chr(ord("A") + index))
    base = base or _address("K" if index == 0 else chr(ord("K") + index))
    base_token = {
        "address": base,
        "symbol": symbol or f"T{index}",
        "decimals": 9,
    }
    quote_token = {"address": scan.USDC_MINT, "symbol": "USDC", "decimals": 6}
    record = {
        "address": pool,
        "tokenA": quote_token if quote_first else base_token,
        "tokenB": base_token if quote_first else quote_token,
        "price": "0.1" if quote_first else "10.123456789",
        "priceHistory7d": (
            history
            if history is not None
            else ["8", "8.2", "8.4", "8.8", "9", "9.4", "9.8", "10"]
        ),
        "tvlUsdc": str(tvl),
        "tickSpacing": tick_spacing,
        "adaptiveFeeEnabled": adaptive_fee,
        "hasWarning": warning,
        "stats": {
            window: {
                "volume": str(volume),
                "fees": str(fee),
                "priceDelta": str(change_24h) if window == "24h" else None,
            }
            for window, volume, fee in zip(scan.WINDOWS, volumes, fees, strict=True)
        },
    }
    if source_reported_at is not None:
        record["updatedAt"] = source_reported_at
    return record


def _install_discovery(monkeypatch, records, *, failed=(), transform=None):
    calls = []

    def fetch(url, timeout_seconds):
        query = parse_qs(urlsplit(url).query)
        lens = query["sortBy"][0]
        calls.append((lens, timeout_seconds, query))
        if lens in failed:
            raise TimeoutError(f"{lens} unavailable api_key=secret")
        payload = copy.deepcopy(records)
        if transform is not None:
            payload = transform(lens, payload)
        return {"data": payload}

    monkeypatch.setattr(scan, "_fetch_json", fetch)
    return calls


def _result_payload(result):
    assert result.sections and result.sections[0]["type"] == "scanner_metadata"
    return {
        **result.sections[0]["data"],
        "table_columns": result.table_columns,
        "table_data": result.table_data,
    }


def _run(config):
    result = asyncio.run(scan.run(config, None))
    assert result.text.endswith("mutation=false.")
    return _result_payload(result)


@pytest.fixture(autouse=True)
def _fixed_time_and_no_report_io(monkeypatch):
    monkeypatch.setattr(scan, "_utc_now", lambda: OBSERVED_AT)

    async def no_report_io(payload, config):
        return "test-report"

    monkeypatch.setattr(scan, "_save_report", no_report_io)


def test_config_exposes_only_v2_inputs_and_fails_closed():
    assert list(scan.Config.model_fields) == [
        "risk_profile",
        "min_pool_tvl_usd",
        "min_fee_productivity_bps_per_day",
        "candidate_scan_limit",
        "excluded_base_mints",
        "excluded_pool_addresses",
        "refresh_pool_addresses",
    ]
    assert scan.Config.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError):
        _config(required_pool_addresses=[])
    with pytest.raises(ValidationError):
        _config(candidate_scan_limit=True)
    with pytest.raises(ValidationError):
        _config(min_pool_tvl_usd=float("nan"))
    with pytest.raises(ValidationError):
        _config(excluded_pool_addresses=["not-an-address"])
    with pytest.raises(ValidationError):
        _config(excluded_pool_addresses=["z" * 44])
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        _config(excluded_base_mints=[_address("K"), _address("K")])

    assert scan._is_solana_address(scan.USDC_MINT)
    assert scan._is_solana_address(_address("A"))
    assert not scan._is_solana_address("z" * 44)


def test_version_controlled_scanner_policy_constants_are_exact():
    assert scan.USDC_MINT == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    assert scan._BASE_URL == "https://api.orca.so/v2/solana/pools"
    assert scan._POOL_PATH == "/v2/solana/pools"
    assert scan.WINDOWS == ("1h", "4h", "24h", "7d")
    assert scan.DISCOVERY_LENSES == (
        "yieldovertvl24h",
        "yieldovertvl7d",
        "volume24h",
        "volume7d",
    )
    assert scan.REQUEST_SIZE == 100
    assert scan.REFRESH_CHUNK_SIZE == 100
    assert scan.REQUEST_TIMEOUT_SECONDS == 12.0
    assert scan.MAX_RESPONSE_BYTES == 2_000_000
    assert scan.SOURCE_MAX_AGE_SECONDS == 300
    assert scan.MAX_STRUCTURED_RESULT_BYTES == 1_000_000
    assert scan._MCDA_WEIGHTS == {
        "fee_productivity": Decimal("0.40"),
        "recent_activity": Decimal("0.25"),
        "price_stability": Decimal("0.15"),
        "liquidity_depth": Decimal("0.10"),
        "execution_simplicity": Decimal("0.10"),
    }
    assert scan._PROFILE_POLICY == {
        "conservative": (Decimal("2.0"), Decimal("4"), Decimal("20")),
        "balanced": (Decimal("1.5"), Decimal("2"), Decimal("12")),
        "high_yield": (Decimal("1.0"), Decimal("1"), Decimal("8")),
    }

    url = scan._request_url(lens="volume24h", min_tvl=Decimal("10000"))
    parsed = urlsplit(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "api.orca.so",
        "/v2/solana/pools",
    )
    assert parse_qs(parsed.query)["token"] == [
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    ]


def test_result_column_schemas_are_literal_and_exact():
    assert scan.ADMISSION_COLUMNS == [
        "rank",
        "pool_address",
        "base_symbol",
        "base_mint",
        "base_decimals",
        "price_quote",
        "tvl_usd",
        "change_24h_pct",
        "recent_history_pct",
        "seven_day_history_pct",
        "fee_productivity_24h_bps_per_day",
        "fee_productivity_7d_bps_per_day",
        "fee_productivity_raw",
        "recent_activity_raw",
        "movement_pct",
        "execution_simplicity_raw",
        "fee_productivity_component",
        "recent_activity_component",
        "price_stability_component",
        "liquidity_depth_component",
        "execution_simplicity_component",
        "mcda_score",
        "observed_at",
        "source_reported_at",
        "market_trend",
        "position_width_pct",
        "downside_offset_pct",
        "rebalance_threshold_pct",
    ]
    assert scan.REFRESH_COLUMNS == [
        "pool_address",
        "base_symbol",
        "base_mint",
        "base_decimals",
        "row_status",
        "reason",
        "observed_at",
        "source_reported_at",
        "price_quote",
        "tvl_usd",
        "change_24h_pct",
        "recent_history_pct",
        "seven_day_history_pct",
        "movement_pct",
        "pool_warnings",
        "formation_valid",
        "market_trend",
        "position_width_pct",
        "downside_offset_pct",
        "rebalance_threshold_pct",
    ]


def test_admission_runs_four_bounded_lenses_and_returns_full_schema(monkeypatch):
    calls = _install_discovery(monkeypatch, [_record(0), _record(1)])

    payload = _run(_config())

    assert payload["status"] == "complete"
    assert payload["scan_kind"] == "admission"
    assert payload["mutation"] is False
    assert payload["completed_lenses"] == list(scan.DISCOVERY_LENSES)
    assert payload["failed_lenses"] == []
    assert payload["discovered_count"] == 2
    assert payload["eligible_count"] == payload["returned_count"] == 2
    assert payload["omitted_count"] == 0
    assert payload["table_columns"] == scan.ADMISSION_COLUMNS
    assert all(list(row) == scan.ADMISSION_COLUMNS for row in payload["table_data"])
    assert {call[0] for call in calls} == set(scan.DISCOVERY_LENSES)
    assert all(call[1] == 12.0 for call in calls)
    assert all(call[2]["size"] == ["100"] for call in calls)
    assert all(call[2]["stats"] == ["1h,4h,24h,7d"] for call in calls)
    assert all(call[2]["token"] == [scan.USDC_MINT] for call in calls)
    assert all(call[2]["sortDirection"] == ["desc"] for call in calls)
    assert all(call[2]["minTvl"] == ["10000"] for call in calls)


@pytest.mark.parametrize(
    ("failed", "status", "returned"),
    [
        (("volume7d",), "degraded", 1),
        (("volume7d", "volume24h"), "degraded", 1),
        (("volume7d", "volume24h", "yieldovertvl7d"), "unavailable", 0),
    ],
)
def test_admission_coverage_threshold_and_sanitized_failures(
    monkeypatch, failed, status, returned
):
    _install_discovery(monkeypatch, [_record()], failed=failed)

    payload = _run(_config())

    assert payload["status"] == status
    assert payload["returned_count"] == returned
    assert len(payload["completed_lenses"]) == 4 - len(failed)
    assert "secret" not in json.dumps(payload["failed_lenses"])


def test_independent_fee_productivity_gates(monkeypatch):
    low_24h = _record(
        0,
        fees=(10, 40, Decimal("19.9"), 1680),
    )
    low_7d = _record(
        1,
        fees=(10, 40, 240, Decimal("139.9")),
    )
    passing = _record(2)
    _install_discovery(monkeypatch, [low_24h, low_7d, passing])

    payload = _run(_config())

    assert payload["eligible_count"] == 1
    assert payload["table_data"][0]["pool_address"] == passing["address"]
    assert payload["rejection_summary"]["below_24h_fee_productivity"] == 4
    assert payload["rejection_summary"]["below_7d_fee_productivity"] == 4
    assert payload["table_data"][0]["fee_productivity_24h_bps_per_day"] == 24
    assert payload["table_data"][0]["fee_productivity_7d_bps_per_day"] == 24


def test_only_observations_used_by_admission_math_must_be_positive(monkeypatch):
    allowed = _record(0, volumes=(1000, 4000, 24000, 0), fees=(0, 40, 240, 1680))
    allowed["stats"]["7d"].pop("volume")
    missing_used_volume = _record(1)
    missing_used_volume["stats"]["4h"].pop("volume")
    zero_used_fee = _record(2, fees=(0, 0, 240, 1680))
    _install_discovery(monkeypatch, [allowed, missing_used_volume, zero_used_fee])

    payload = _run(_config())

    assert payload["eligible_count"] == 1
    assert payload["table_data"][0]["pool_address"] == allowed["address"]
    assert payload["rejection_summary"]["invalid_volume_4h"] == 4
    assert payload["rejection_summary"]["non_positive_fee_observation"] == 4


def test_fee_productivity_floor_is_inclusive(monkeypatch):
    exact_floor = _record(fees=(0, 40, 20, 140))
    _install_discovery(monkeypatch, [exact_floor])

    payload = _run(_config())

    assert payload["eligible_count"] == 1
    assert payload["table_data"][0]["fee_productivity_24h_bps_per_day"] == 2
    assert payload["table_data"][0]["fee_productivity_7d_bps_per_day"] == 2


def test_hourly_normalized_activity_and_fixed_mcda_components():
    row = scan._normalize_admission(_record(), OBSERVED_AT, _config())
    assert row["fee_productivity_raw"] == Decimal("0.0001")
    assert row["recent_activity_raw"] == Decimal("0.01")

    first = {
        **row,
        "pool_address": _address("A"),
        "tvl_usd": Decimal("100"),
        "recent_activity_raw": Decimal("1"),
        "fee_productivity_raw": Decimal("1"),
        "execution_simplicity_raw": Decimal("1"),
    }
    second = {
        **row,
        "pool_address": _address("B"),
        "tvl_usd": Decimal("200"),
        "recent_activity_raw": Decimal("2"),
        "fee_productivity_raw": Decimal("2"),
        "execution_simplicity_raw": Decimal("2"),
    }
    first["formation"] = {**row["formation"], "movement_pct": Decimal("2")}
    second["formation"] = {**row["formation"], "movement_pct": Decimal("1")}

    ranked = scan._rank([first, second])

    assert ranked[0]["pool_address"] == _address("B")
    assert ranked[0]["components"] == {
        "fee_productivity": Decimal(1),
        "recent_activity": Decimal(1),
        "price_stability": Decimal(1),
        "liquidity_depth": Decimal(1),
        "execution_simplicity": Decimal(1),
    }
    assert ranked[0]["mcda_score"] == Decimal(1)
    assert ranked[1]["mcda_score"] == Decimal(0)


def test_recent_activity_acceleration_and_negative_values_are_preserved():
    accelerated = scan._normalize_admission(
        _record(volumes=(1000, 2000, 12000, 0)), OBSERVED_AT, _config()
    )
    slowing = scan._normalize_admission(
        _record(volumes=(100, 400, 24000, 0)), OBSERVED_AT, _config()
    )

    assert accelerated["recent_activity_raw"] == Decimal("0.0125")
    assert slowing["recent_activity_raw"] == Decimal("-0.008")


def test_execution_simplicity_uses_fee_type_and_tick_spacing_formula():
    fixed_tight = scan._normalize_admission(
        _record(tick_spacing=1, adaptive_fee=False), OBSERVED_AT, _config()
    )
    adaptive_tight = scan._normalize_admission(
        _record(tick_spacing=1, adaptive_fee=True), OBSERVED_AT, _config()
    )
    fixed_wide = scan._normalize_admission(
        _record(tick_spacing=100, adaptive_fee=False), OBSERVED_AT, _config()
    )
    adaptive_wide = scan._normalize_admission(
        _record(tick_spacing=100, adaptive_fee=True), OBSERVED_AT, _config()
    )

    one_third = Decimal("0.25") / Decimal(3)
    assert fixed_tight["execution_simplicity_raw"] == Decimal("1.00")
    assert adaptive_tight["execution_simplicity_raw"] == Decimal("0.70")
    assert fixed_wide["execution_simplicity_raw"] == Decimal("0.75") + one_third
    assert adaptive_wide["execution_simplicity_raw"] == Decimal("0.45") + one_third


def test_mixed_mcda_components_use_the_exact_fixed_weights():
    row = scan._normalize_admission(_record(), OBSERVED_AT, _config())
    fee_leader = {
        **row,
        "pool_address": _address("A"),
        "fee_productivity_raw": Decimal("2"),
        "recent_activity_raw": Decimal("1"),
        "tvl_usd": Decimal("100"),
        "execution_simplicity_raw": Decimal("1"),
        "formation": {**row["formation"], "movement_pct": Decimal("2")},
    }
    diversified_leader = {
        **row,
        "pool_address": _address("B"),
        "fee_productivity_raw": Decimal("1"),
        "recent_activity_raw": Decimal("2"),
        "tvl_usd": Decimal("200"),
        "execution_simplicity_raw": Decimal("2"),
        "formation": {**row["formation"], "movement_pct": Decimal("1")},
    }

    ranked = scan._rank([fee_leader, diversified_leader])

    assert ranked[0]["pool_address"] == _address("B")
    assert ranked[0]["mcda_score"] == Decimal("0.60")
    assert ranked[1]["mcda_score"] == Decimal("0.40")


def test_fee_productivity_raw_excludes_the_one_hour_fee_observation():
    tiny_one_hour = scan._normalize_admission(
        _record(fees=(Decimal("0.00000001"), 40, 240, 1680)),
        OBSERVED_AT,
        _config(),
    )
    huge_one_hour = scan._normalize_admission(
        _record(fees=(Decimal("999999999"), 40, 240, 1680)),
        OBSERVED_AT,
        _config(),
    )

    assert tiny_one_hour["fee_productivity_raw"] == Decimal("0.0001")
    assert huge_one_hour["fee_productivity_raw"] == Decimal("0.0001")


def test_tied_and_one_candidate_percentiles_and_address_tie_break():
    assert scan._percentiles([Decimal("7")]) == [Decimal(1)]
    assert scan._percentiles([Decimal("1"), Decimal("1"), Decimal("3")]) == [
        Decimal("0.25"),
        Decimal("0.25"),
        Decimal(1),
    ]
    assert scan._percentiles(
        [Decimal("1"), Decimal("1"), Decimal("3")], reverse=True
    ) == [Decimal("0.75"), Decimal("0.75"), Decimal(0)]

    row = scan._normalize_admission(_record(), OBSERVED_AT, _config())
    higher_address = {**row, "pool_address": _address("B")}
    lower_address = {**row, "pool_address": _address("A")}
    ranked = scan._rank([higher_address, lower_address])
    assert [item["pool_address"] for item in ranked] == [
        _address("A"),
        _address("B"),
    ]
    assert all(item["mcda_score"] == Decimal("0.5") for item in ranked)


@pytest.mark.parametrize(
    ("change", "history", "expected"),
    [
        ("0.01", ["10", "10", "10", "10", "10", "10", "10", "10.1"], "SIDEWAYS"),
        ("-0.01", ["10", "10", "10", "10", "10", "10", "10", "9.9"], "SIDEWAYS"),
        ("0.024", ["100", "100.5", "101", "101.5", "102", "102.5", "103", "106"], "UP"),
        ("-0.024", ["100", "99.5", "99", "98.5", "98", "97.5", "97", "94"], "DOWN"),
    ],
)
def test_strict_trend_vote_boundaries(change, history, expected):
    formation, reason = scan._trend_and_formation(
        _record(change_24h=change, history=history), "balanced"
    )
    assert reason is None
    assert formation["market_trend"] == expected


@pytest.mark.parametrize(("last", "expected"), [("105", "NEUTRAL"), ("95", "NEUTRAL")])
def test_seven_day_vote_is_neutral_at_exact_plus_or_minus_five_percent(last, expected):
    history = ["100"] * 7 + [last]
    formation, reason = scan._trend_and_formation(
        _record(change_24h="0", history=history), "balanced"
    )

    assert reason is None
    assert formation["seven_day_history_pct"] == Decimal(last) - Decimal("100")
    assert formation["votes"][2] == expected


def test_timestamped_history_is_normalized_and_bad_history_is_unknown():
    history = [
        {"timestamp": index, "price": str(price)}
        for index, price in reversed(
            list(enumerate((100, 100.5, 101, 101.5, 102, 102.5, 103, 106)))
        )
    ]
    formation, reason = scan._trend_and_formation(_record(history=history), "balanced")
    assert reason is None
    assert formation["market_trend"] == "UP"

    formation, reason = scan._trend_and_formation(
        _record(history=["10", "bad"]), "balanced"
    )
    assert formation["market_trend"] == "UNKNOWN"
    assert reason == "invalid_price_history_7d"


def test_iso_timestamps_require_timezone_while_numeric_epochs_remain_utc():
    assert scan._parse_timestamp(0) == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert scan._parse_timestamp("0") == datetime(1970, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="explicit timezone"):
        scan._parse_timestamp("2026-08-30T12:00:00")

    history = [
        {"timestamp": f"2026-08-{index + 1:02d}T12:00:00", "price": "10"}
        for index in range(7)
    ]
    formation, reason = scan._trend_and_formation(
        _record(change_24h="0", history=history), "balanced"
    )
    assert formation["market_trend"] == "UNKNOWN"
    assert reason == "invalid_price_history_7d"


def test_source_timestamp_without_timezone_is_invalid_for_admission_and_refresh(
    monkeypatch,
):
    record = _record(source_reported_at="2026-08-30T12:00:00")
    _install_discovery(monkeypatch, [record])

    admission = _run(_config())

    assert admission["eligible_count"] == 0
    assert admission["rejection_summary"]["invalid_source_reported_at"] == 4

    monkeypatch.setattr(scan, "_fetch_json", lambda url, timeout: {"data": [record]})
    refresh = _run(_config(refresh_pool_addresses=[record["address"]]))
    assert refresh["table_data"][0]["row_status"] == "invalid_evidence"
    assert refresh["table_data"][0]["reason"] == "invalid_source_reported_at"


@pytest.mark.parametrize(
    ("history", "reason"),
    [
        (["10"] * 6, "insufficient_price_history_7d"),
        (["10", "10", "10", "0", "10", "10", "10"], "invalid_price_history_7d"),
        (
            [
                {"timestamp": 1 if index < 2 else index, "price": "10"}
                for index in range(7)
            ],
            "invalid_price_history_7d",
        ),
    ],
)
def test_insufficient_nonpositive_and_duplicate_history_are_unknown(history, reason):
    formation, observed_reason = scan._trend_and_formation(
        _record(history=history), "balanced"
    )

    assert formation["market_trend"] == "UNKNOWN"
    assert observed_reason == reason


def test_missing_history_is_unknown():
    record = _record()
    record.pop("priceHistory7d")

    formation, reason = scan._trend_and_formation(record, "balanced")

    assert formation["market_trend"] == "UNKNOWN"
    assert reason == "missing_price_history_7d"


@pytest.mark.parametrize(
    ("change", "history", "recent_bounds", "seven_day_bounds"),
    [
        (
            "-0.031151585",
            [
                "100.93949753526673239000",
                "98.19357123756191985000",
                "97.2009535545128669",
                "95.50861839630438066000",
                "101.27568970919811516000",
                "106.7881902435255869",
                "106.46820823139429131000",
                "105.57448515826434363000",
                "104.10644191707888067000",
                "105.30310892592977785000",
                "105.41129616355300174000",
                "106.84119542555482474000",
                "101.36676289627658551000",
                "103.2586081601465568",
            ],
            (Decimal("-2.20"), Decimal("-2.19")),
            (Decimal("2.29"), Decimal("2.30")),
        ),
        (
            "-0.022944793",
            [
                "856.9170236369235600",
                "818.7122387808715200",
                "784.4511151847169300",
                "765.3180767858306900",
                "782.1088152343732100",
                "796.5228989456247400",
                "784.2805970244670700",
                "809.2126728760184700",
                "806.5595127473375600",
                "837.3721180156803100",
                "835.6062579996247100",
                "853.1962637233231300",
                "809.8343755432008100",
                "829.7118641737031500",
            ],
            (Decimal("2.53"), Decimal("2.54")),
            (Decimal("-3.18"), Decimal("-3.17")),
        ),
    ],
)
def test_latest_sol_and_zec_mixed_horizons_are_sideways(
    change, history, recent_bounds, seven_day_bounds
):
    formation, reason = scan._trend_and_formation(
        _record(change_24h=change, history=history), "balanced"
    )

    assert reason is None
    assert formation["market_trend"] == "SIDEWAYS"
    assert recent_bounds[0] < formation["recent_history_pct"] < recent_bounds[1]
    assert seven_day_bounds[0] < formation["seven_day_history_pct"] < seven_day_bounds[1]


def test_timestamp_overflow_is_contained_per_candidate_and_refresh_row(monkeypatch):
    invalid = _record(
        0,
        history=[
            {"timestamp": "9" * 400, "price": str(price)}
            for price in (8, 8.2, 8.4, 8.8, 9, 9.4, 9.8, 10)
        ],
    )
    valid = _record(1)
    _install_discovery(monkeypatch, [invalid, valid])

    admission = _run(_config())

    assert admission["status"] == "complete"
    assert admission["eligible_count"] == 1
    assert admission["rejection_summary"]["invalid_price_history_7d"] == 4

    address = invalid["address"]
    monkeypatch.setattr(
        scan, "_fetch_json", lambda url, timeout: {"data": [copy.deepcopy(invalid)]}
    )
    refresh = _run(_config(refresh_pool_addresses=[address]))

    assert refresh["status"] == "complete"
    assert refresh["table_data"][0]["row_status"] == "invalid_evidence"
    assert refresh["table_data"][0]["reason"] == "invalid_price_history_7d"


@pytest.mark.parametrize(
    ("profile", "change", "history", "width", "offset", "threshold"),
    [
        ("conservative", "0", ["10"] * 8, Decimal("4"), Decimal("0"), Decimal("1")),
        ("balanced", "0", ["10"] * 8, Decimal("2"), Decimal("0"), Decimal("0.5")),
        ("high_yield", "0", ["10"] * 8, Decimal("1"), Decimal("0"), Decimal("0.25")),
        (
            "balanced",
            "-0.50",
            ["10", "9", "8", "7", "6", "5", "4", "3"],
            Decimal("12"),
            Decimal("3"),
            Decimal("3"),
        ),
    ],
)
def test_profile_clamps_and_downtrend_widening(
    profile, change, history, width, offset, threshold
):
    formation, reason = scan._trend_and_formation(
        _record(change_24h=change, history=history), profile
    )
    assert reason is None
    assert formation["position_width_pct"] == width
    assert formation["downside_offset_pct"] == offset
    assert formation["rebalance_threshold_pct"] == threshold


def test_downtrend_widening_is_visible_before_the_profile_maximum_cap():
    formation, reason = scan._trend_and_formation(
        _record(
            change_24h="-0.04",
            history=["10", "9.9", "9.8", "9.7", "9.6", "9.5", "9.4", "9.2"],
        ),
        "balanced",
    )

    assert reason is None
    assert formation["market_trend"] == "DOWN"
    assert formation["position_width_pct"] == Decimal("3.750")
    assert formation["downside_offset_pct"] == Decimal("0.93750")


def test_up_and_sideways_use_unskewed_width_and_threshold_max_is_five():
    sideways, sideways_reason = scan._trend_and_formation(
        _record(change_24h="0", history=["10"] * 8), "balanced"
    )
    up, up_reason = scan._trend_and_formation(
        _record(
            change_24h="0.024",
            history=["100", "100.5", "101", "101.5", "102", "102.5", "103", "106"],
        ),
        "balanced",
    )
    maximum, maximum_reason = scan._trend_and_formation(
        _record(
            change_24h="1",
            history=["10", "9", "8", "7", "6", "5", "4", "3"],
        ),
        "conservative",
    )

    assert sideways_reason is up_reason is maximum_reason is None
    assert sideways["market_trend"] == "SIDEWAYS"
    assert up["market_trend"] == "UP"
    for formation in (sideways, up):
        assert formation["downside_offset_pct"] == 0
        assert formation["position_width_pct"] == min(
            max(
                formation["movement_pct"] * Decimal("1.5"),
                Decimal("2"),
            ),
            Decimal("12"),
        )
    assert maximum["position_width_pct"] == Decimal("20")
    assert maximum["rebalance_threshold_pct"] == Decimal("5")


def test_admission_technical_gates_and_usdc_orientation(monkeypatch):
    warned = _record(0, warning=True)
    below_tvl = _record(1, tvl="9999")
    excluded_pool = _record(2)
    excluded_base = _record(3)
    reversed_quote = _record(4, base=_address("Z"), quote_first=True)
    hyphenated_base_symbol = _record(5, symbol="FAKE-USDC")
    quote_spoofing_base_symbol = _record(6, symbol="USDC")
    _install_discovery(
        monkeypatch,
        [
            warned,
            below_tvl,
            excluded_pool,
            excluded_base,
            reversed_quote,
            hyphenated_base_symbol,
            quote_spoofing_base_symbol,
        ],
    )
    payload = _run(
        _config(
            excluded_pool_addresses=[excluded_pool["address"]],
            excluded_base_mints=[excluded_base["tokenA"]["address"]],
        )
    )
    assert payload["eligible_count"] == 0
    assert set(payload["rejection_summary"]) >= {
        "pool_has_warning",
        "below_min_pool_tvl",
        "excluded_pool",
        "excluded_base",
        "unsupported_usdc_token_a_orientation",
        "invalid_token_A_symbol",
        "base_symbol_conflicts_with_quote_symbol",
    }


def test_scanner_preserves_case_sensitive_connector_symbol(monkeypatch):
    record = _record(symbol="cbBTC")
    _install_discovery(monkeypatch, [record])

    admission = _run(_config())

    assert admission["eligible_count"] == 1
    assert admission["table_data"][0]["base_symbol"] == "cbBTC"

    monkeypatch.setattr(scan, "_fetch_json", lambda url, timeout: {"data": [record]})
    refresh = _run(_config(refresh_pool_addresses=[record["address"]]))

    assert refresh["table_data"][0]["base_symbol"] == "cbBTC"


def test_cross_lens_metric_contradiction_rejects_the_pool(monkeypatch):
    def contradict_one_lens(lens, records):
        if lens == "volume7d":
            records[0]["price"] = "11"
        return records

    _install_discovery(monkeypatch, [_record()], transform=contradict_one_lens)

    payload = _run(_config())

    assert payload["eligible_count"] == 0
    assert payload["rejection_summary"]["contradictory_pool_evidence"] == 1


def test_one_lens_admission_failure_quarantines_the_same_valid_pool(monkeypatch):
    def invalidate_one_lens(lens, records):
        if lens == "volume7d":
            records[0]["hasWarning"] = True
        return records

    record = _record()
    _install_discovery(monkeypatch, [record], transform=invalidate_one_lens)

    payload = _run(_config())

    assert payload["status"] == "complete"
    assert payload["discovered_count"] == 1
    assert payload["eligible_count"] == payload["returned_count"] == 0
    assert payload["rejection_summary"]["pool_has_warning"] == 1


def test_source_timestamp_age_is_enforced_only_when_present(monkeypatch):
    fresh = _record(
        0, source_reported_at=(OBSERVED_AT - timedelta(seconds=299)).isoformat()
    )
    stale = _record(
        1, source_reported_at=(OBSERVED_AT - timedelta(seconds=301)).isoformat()
    )
    absent = _record(2)
    _install_discovery(monkeypatch, [fresh, stale, absent])

    payload = _run(_config())

    assert payload["eligible_count"] == 2
    assert payload["rejection_summary"]["source_reported_at_is_stale"] == 4
    rows = {row["pool_address"]: row for row in payload["table_data"]}
    assert rows[fresh["address"]]["source_reported_at"].endswith("Z")
    assert rows[absent["address"]]["source_reported_at"] is None


def test_sanitized_live_orca_shape_agrees_across_top_level_and_nested_identity(
    monkeypatch,
):
    record = _record(
        pool=_address("A"),
        base=_address("K"),
        symbol="SOL",
        source_reported_at=OBSERVED_AT.isoformat().replace("+00:00", "Z"),
    )
    record["tokenA"] = {
        "mint": record["tokenA"]["address"],
        **record["tokenA"],
    }
    record["tokenB"] = {
        "mint": record["tokenB"]["address"],
        **record["tokenB"],
    }
    for side in ("A", "B"):
        token = record[f"token{side}"]
        record[f"tokenMint{side}"] = token["mint"]
        record[f"token{side}Symbol"] = token["symbol"]
        record[f"token{side}Decimals"] = token["decimals"]
    _install_discovery(monkeypatch, [record])

    admission = _run(_config())

    assert admission["status"] == "complete"
    assert admission["table_data"][0]["base_symbol"] == "SOL"
    assert admission["table_data"][0]["base_mint"] == _address("K")
    assert admission["table_data"][0]["source_reported_at"] == scan._iso(OBSERVED_AT)

    monkeypatch.setattr(
        scan, "_fetch_json", lambda url, timeout: {"data": [copy.deepcopy(record)]}
    )
    refresh = _run(_config(refresh_pool_addresses=[record["address"]]))
    assert refresh["status"] == "complete"
    assert refresh["table_data"][0]["row_status"] == "ok"


@pytest.mark.parametrize(
    ("field", "conflicting_value", "reason"),
    [
        ("symbol", "OTHER", "contradictory_token_A_symbol"),
        ("address", _address("L"), "contradictory_token_A_mint"),
        ("decimals", 8, "contradictory_token_A_decimals"),
    ],
)
def test_same_record_contradictory_nested_token_metadata_is_rejected(
    monkeypatch, field, conflicting_value, reason
):
    record = _record()
    record["tokenMintA"] = {
        "symbol": record["tokenA"]["symbol"],
        "address": record["tokenA"]["address"],
        "decimals": record["tokenA"]["decimals"],
    }
    record["tokenMintA"][field] = conflicting_value
    _install_discovery(monkeypatch, [record])

    payload = _run(_config())

    assert payload["eligible_count"] == 0
    assert payload["rejection_summary"][reason] == 4


def test_observed_at_is_fetch_completion_time(monkeypatch):
    completed_at = OBSERVED_AT + timedelta(seconds=3)
    times = iter((OBSERVED_AT, completed_at))
    monkeypatch.setattr(scan, "_utc_now", lambda: next(times))
    _install_discovery(monkeypatch, [_record()])

    payload = _run(_config())

    assert payload["observed_at"] == scan._iso(completed_at)
    assert payload["table_data"][0]["observed_at"] == scan._iso(completed_at)


def test_refresh_is_chunked_exact_unranked_and_ignores_admission_gates(monkeypatch):
    addresses = [_address(character) for character in "ABCDE"]
    base_characters = "KLMNP"
    records = {
        address: _record(
            index,
            pool=address,
            base=_address(base_characters[index]),
            tvl="1",
            fees=(0, 0, 0, 0),
            warning=True,
        )
        for index, address in enumerate(addresses)
    }
    calls = []

    def fetch(url, timeout_seconds):
        query = parse_qs(urlsplit(url).query)
        requested = query["addresses"][0].split(",")
        calls.append((requested, timeout_seconds, query))
        return {"data": [copy.deepcopy(records[address]) for address in requested]}

    monkeypatch.setattr(scan, "REFRESH_CHUNK_SIZE", 2)
    monkeypatch.setattr(scan, "_fetch_json", fetch)
    config = _config(
        refresh_pool_addresses=[*addresses, addresses[0]],
        excluded_pool_addresses=[addresses[1]],
        excluded_base_mints=[records[addresses[2]]["tokenA"]["address"]],
        candidate_scan_limit=1,
    )

    payload = _run(config)

    assert payload["status"] == "complete"
    assert payload["scan_kind"] == "formation_refresh"
    assert payload["requested_count"] == payload["returned_count"] == 5
    assert [row["pool_address"] for row in payload["table_data"]] == addresses
    assert all(row["row_status"] == "ok" for row in payload["table_data"])
    assert all(row["formation_valid"] for row in payload["table_data"])
    assert all(
        row["pool_warnings"] == ["orca_has_warning"] for row in payload["table_data"]
    )
    for row in payload["table_data"]:
        assert row["price_quote"] == 10.123457
        assert row["tvl_usd"] == 1
        assert row["change_24h_pct"] == 2.4
        assert row["recent_history_pct"] is not None
        assert row["seven_day_history_pct"] is not None
        assert row["movement_pct"] is not None
    assert len(calls) == 3
    assert all("sortBy" not in query and "minTvl" not in query for _, _, query in calls)
    assert all(timeout == 12.0 for _, timeout, _ in calls)
    assert all(query["stats"] == ["1h,4h,24h,7d"] for _, _, query in calls)
    assert all(query["size"] == [str(len(requested))] for requested, _, query in calls)
    assert all(
        query["addresses"] == [",".join(requested)] for requested, _, query in calls
    )
    assert all(
        "token" not in query and "sortDirection" not in query for _, _, query in calls
    )
    assert payload["table_columns"] == scan.REFRESH_COLUMNS
    assert "rank" not in payload["table_data"][0]


def test_refresh_preserves_invalid_missing_and_source_error_rows(monkeypatch):
    addresses = [_address(character) for character in "ABCD"]
    invalid = _record(0, pool=addresses[0], history=["10", "bad"])
    valid = _record(1, pool=addresses[1])

    def fetch(url, timeout_seconds):
        requested = parse_qs(urlsplit(url).query)["addresses"][0].split(",")
        if addresses[3] in requested:
            raise TimeoutError("rpc_url=https://secret.example/?token=bad")
        return {
            "data": [
                record for record in (invalid, valid) if record["address"] in requested
            ]
        }

    monkeypatch.setattr(scan, "REFRESH_CHUNK_SIZE", 2)
    monkeypatch.setattr(scan, "_fetch_json", fetch)

    payload = _run(_config(refresh_pool_addresses=addresses))

    assert payload["status"] == "degraded"
    assert payload["invalid_count"] == 1
    assert payload["valid_count"] == 1
    assert payload["returned_count"] == 2
    assert payload["source_error_count"] == 2
    rows = {row["pool_address"]: row for row in payload["table_data"]}
    assert rows[addresses[0]]["row_status"] == "invalid_evidence"
    assert payload["invalid_pool_addresses"] == [addresses[0]]
    assert rows[addresses[0]]["base_mint"] == invalid["tokenA"]["address"]
    assert rows[addresses[0]]["formation_valid"] is False
    assert rows[addresses[0]]["market_trend"] is None
    assert rows[addresses[1]]["row_status"] == "ok"
    assert "secret" not in json.dumps(payload["errors"])


def test_refresh_stages_safe_identity_and_risk_observations_before_parse_failure(
    monkeypatch,
):
    address = _address("A")
    record = _record(pool=address, warning=True)
    record["tickSpacing"] = "not-an-integer"
    monkeypatch.setattr(scan, "_fetch_json", lambda url, timeout: {"data": [record]})

    payload = _run(_config(refresh_pool_addresses=[address]))

    row = payload["table_data"][0]
    assert payload["status"] == "complete"
    assert row["row_status"] == "invalid_evidence"
    assert row["reason"] == "invalid_tick_spacing"
    assert row["base_symbol"] == record["tokenA"]["symbol"]
    assert row["base_mint"] == record["tokenA"]["address"]
    assert row["base_decimals"] == 9
    assert row["price_quote"] == 10.123457
    assert row["tvl_usd"] == 100000
    assert row["pool_warnings"] == ["orca_has_warning"]


def test_refresh_preserves_zero_tvl_while_admission_rejects_it(monkeypatch):
    record = _record(tvl="0")
    _install_discovery(monkeypatch, [record])

    admission = _run(_config())

    assert admission["eligible_count"] == 0
    assert admission["rejection_summary"]["below_min_pool_tvl"] == 4

    monkeypatch.setattr(scan, "_fetch_json", lambda url, timeout: {"data": [record]})
    refresh = _run(_config(refresh_pool_addresses=[record["address"]]))

    assert refresh["status"] == "complete"
    row = refresh["table_data"][0]
    assert row["row_status"] == "ok"
    assert row["formation_valid"] is True
    assert row["tvl_usd"] == 0


def test_unexpected_refresh_failure_returns_one_source_error_row_per_unique_pool(
    monkeypatch,
):
    addresses = [_address("A"), _address("B")]

    async def fail_refresh(config, observed_at):
        raise OSError("source parser failed api_key=secret")

    monkeypatch.setattr(scan, "_refresh_pools", fail_refresh)

    payload = _run(
        _config(refresh_pool_addresses=[addresses[0], addresses[1], addresses[0]])
    )

    assert payload["status"] == "unavailable"
    assert payload["requested_count"] == 2
    assert payload["source_error_count"] == 2
    assert payload["source_error_pool_addresses"] == addresses
    assert [row["pool_address"] for row in payload["table_data"]] == addresses
    assert all(row["row_status"] == "source_error" for row in payload["table_data"])
    assert "secret" not in json.dumps(payload)


def test_refresh_all_unreliable_is_unavailable_and_missing_is_distinct(monkeypatch):
    addresses = [_address("A"), _address("B")]
    monkeypatch.setattr(scan, "_fetch_json", lambda url, timeout: {"data": []})

    payload = _run(_config(refresh_pool_addresses=addresses))

    assert payload["status"] == "unavailable"
    assert payload["returned_count"] == 0
    assert payload["missing_count"] == 2
    assert payload["source_error_count"] == 0
    assert payload["missing_pool_addresses"] == addresses
    assert all(row["row_status"] == "missing" for row in payload["table_data"])


def test_refresh_stale_source_keeps_observations_but_nulls_formation(monkeypatch):
    address = _address("A")
    record = _record(
        pool=address,
        source_reported_at=(OBSERVED_AT - timedelta(seconds=301)).isoformat(),
    )
    monkeypatch.setattr(scan, "_fetch_json", lambda url, timeout: {"data": [record]})

    payload = _run(_config(refresh_pool_addresses=[address]))

    row = payload["table_data"][0]
    assert payload["status"] == "complete"
    assert row["row_status"] == "invalid_evidence"
    assert row["reason"] == "source_reported_at_is_stale"
    assert row["price_quote"] == 10.123457
    assert row["tvl_usd"] == 100000
    assert row["change_24h_pct"] == 2.4
    assert row["recent_history_pct"] is not None
    assert row["seven_day_history_pct"] is not None
    assert row["movement_pct"] is not None
    assert row["market_trend"] is None


def test_numeric_output_is_json_number_with_at_most_eight_significant_digits(
    monkeypatch,
):
    _install_discovery(monkeypatch, [_record()])
    payload = _run(_config())
    row = payload["table_data"][0]
    assert row["price_quote"] == 10.123457
    assert isinstance(row["price_quote"], float)
    assert isinstance(row["tvl_usd"], int)
    for field in (
        "position_width_pct",
        "downside_offset_pct",
        "rebalance_threshold_pct",
    ):
        assert Decimal(str(row[field])).is_finite()
        assert Decimal(str(row[field])) == Decimal(
            format(
                scan._normalize_admission(_record(), OBSERVED_AT, _config())[
                    "formation"
                ][field],
                ".8g",
            )
        )


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (Decimal("1e10000"), "json_number_overflow"),
        (Decimal("1e-10000"), "json_number_underflow"),
        (Decimal("NaN"), "non_finite_json_number"),
        (Decimal("Infinity"), "non_finite_json_number"),
    ],
)
def test_json_number_rejects_float_overflow_underflow_and_nonfinite(value, reason):
    with pytest.raises(ValueError, match=reason):
        scan._json_number(value)


def test_json_number_round_trips_the_documented_eight_significant_digits():
    for value in (
        Decimal("10.123456789"),
        Decimal("0.000123456789"),
        Decimal("123456789.123"),
        Decimal("-3.75000009"),
    ):
        encoded = scan._json_number(value)
        assert Decimal(str(encoded)) == Decimal(format(value, ".8g"))


def test_candidate_limit_applies_after_ranking_without_character_truncation(
    monkeypatch,
):
    records = []
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    for index in range(20):
        record = _record(
            index,
            pool=_address(alphabet[index]),
            base=_address(alphabet[-(index + 1)]),
            symbol=f"TOKEN{index}",
        )
        records.append(record)
    _install_discovery(monkeypatch, records)

    payload = _run(_config(candidate_scan_limit=20))

    assert payload["returned_count"] == 20
    assert payload["omitted_count"] == 0
    assert len(json.dumps(payload["table_data"])) > 2_000
    assert [row["rank"] for row in payload["table_data"]] == list(range(1, 21))


@pytest.mark.parametrize("scan_kind", ["admission", "formation_refresh"])
def test_structured_transport_overflow_is_mode_complete_and_never_partial(
    monkeypatch, scan_kind
):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    records = [
        _record(
            index,
            pool=_address(character),
            base=_address(alphabet[-(index + 1)]),
            symbol=f"TOKEN{index}",
        )
        for index, character in enumerate("ABCDEFGHJKLMNPQRSTUVWXYZ")
    ]
    monkeypatch.setattr(scan, "MAX_STRUCTURED_RESULT_BYTES", 5_000)
    if scan_kind == "admission":
        _install_discovery(monkeypatch, records)
        config = _config(candidate_scan_limit=len(records))
    else:
        by_address = {record["address"]: record for record in records}

        def fetch(url, timeout):
            requested = parse_qs(urlsplit(url).query)["addresses"][0].split(",")
            return {
                "data": [copy.deepcopy(by_address[address]) for address in requested]
            }

        monkeypatch.setattr(scan, "_fetch_json", fetch)
        config = _config(refresh_pool_addresses=list(by_address))

    payload = _run(config)

    assert payload["status"] == "unavailable"
    assert payload["scan_kind"] == scan_kind
    assert payload["table_data"] == []
    assert "structured_result_exceeds_5000_byte_limit" in payload["errors"][0]
    if scan_kind == "admission":
        assert payload["completed_lenses"] == []
        assert payload["returned_count"] == 0
    else:
        assert payload["requested_count"] == len(records)
        assert payload["source_error_count"] == 0
        assert payload["source_error_pool_addresses"] == []


def test_strict_structured_json_rejects_nonfinite_metadata():
    payload = scan._unavailable_payload(_config(), OBSERVED_AT, RuntimeError("test"))
    payload["warnings"] = [float("nan")]

    with pytest.raises(scan._StructuredResultError, match="not_strict_json"):
        scan._validate_structured_result(payload)


def test_response_size_limit_fails_before_json_decode(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return "https://api.orca.so/v2/solana/pools"

        def read(self, size):
            return b"x" * (scan.MAX_RESPONSE_BYTES + 1)

    class Opener:
        def open(self, request, timeout):
            assert timeout == 12.0
            return Response()

    monkeypatch.setattr(scan, "build_opener", lambda *args: Opener())
    url = scan._request_url(lens="volume24h", min_tvl=Decimal("10000"))
    with pytest.raises(ValueError, match="exceeds byte limit"):
        scan._fetch_json(url)


def test_report_runs_for_complete_degraded_and_unavailable_outcomes(monkeypatch):
    observed = []

    async def spy(payload, config):
        observed.append(copy.deepcopy(payload))
        return "report"

    monkeypatch.setattr(scan, "_save_report", spy)
    for failed in ((), ("volume7d",), tuple(scan.DISCOVERY_LENSES[1:])):
        _install_discovery(monkeypatch, [_record()], failed=failed)
        _run(_config())

    assert [payload["status"] for payload in observed] == [
        "complete",
        "degraded",
        "unavailable",
    ]
    assert all(payload["mutation"] is False for payload in observed)


def test_refresh_report_runs_for_complete_degraded_and_unavailable_outcomes(
    monkeypatch,
):
    observed = []
    first = _record(0, pool=_address("A"))
    second = _record(1, pool=_address("B"))

    async def spy(payload, config):
        observed.append(copy.deepcopy(payload))
        return "report"

    monkeypatch.setattr(scan, "_save_report", spy)

    for records in ([first, second], [first], []):
        monkeypatch.setattr(
            scan,
            "_fetch_json",
            lambda url, timeout, returned=records: {"data": copy.deepcopy(returned)},
        )
        _run(_config(refresh_pool_addresses=[first["address"], second["address"]]))

    assert [payload["status"] for payload in observed] == [
        "complete",
        "degraded",
        "unavailable",
    ]
    assert all(payload["scan_kind"] == "formation_refresh" for payload in observed)
    assert all(payload["mutation"] is False for payload in observed)
    assert all(len(payload["table_data"]) == 2 for payload in observed)


def test_report_is_exact_sanitized_projection_of_canonical_payload(monkeypatch):
    captured = {}

    class FakeBuilder:
        def __init__(self, title):
            captured["title"] = title
            captured["tables"] = []
            captured["kpis"] = []
            captured["sections"] = []
            captured["markdown"] = []

        def source(self, source_type, source_name):
            captured["source"] = (source_type, source_name)
            return self

        def tags(self, tags):
            return self

        def manual_order(self):
            return self

        def section(self, title):
            captured["sections"].append(title)
            return self

        def kpi(self, label, value):
            captured["kpis"].append((label, value))
            return self

        def table(self, rows, columns=None):
            captured["tables"].append((copy.deepcopy(rows), copy.deepcopy(columns)))
            return self

        def markdown(self, text):
            captured["markdown"].append(text)
            return self

        async def save(self):
            return "report-1"

    monkeypatch.setattr(scan, "ReportBuilder", FakeBuilder)
    row = {column: None for column in scan.ADMISSION_COLUMNS}
    row.update(
        rank=1,
        pool_address=_address("A"),
        base_mint=_address("B"),
        base_symbol="TOKEN",
        base_decimals=9,
    )
    payload = {
        "schema": scan.SCHEMA,
        "status": "degraded",
        "scan_kind": "admission",
        "observed_at": scan._iso(OBSERVED_AT),
        "mutation": False,
        "report_error": None,
        "risk_profile": "balanced",
        "completed_lenses": ["yieldovertvl24h", "volume24h"],
        "failed_lenses": [
            {"lens": "volume7d", "reason": "api_key=secret"},
        ],
        "raw_record_count": 3,
        "discovered_count": 2,
        "eligible_count": 1,
        "returned_count": 1,
        "omitted_count": 0,
        "rejection_summary": {"below_min_pool_tvl": 1},
        "warnings": [],
        "errors": ["api_key=secret rpc_url=https://hidden.example/?token=x"],
        "table_columns": scan.ADMISSION_COLUMNS,
        "table_data": [row],
    }
    config = _config(
        candidate_scan_limit=1,
        excluded_base_mints=[_address("K")],
        excluded_pool_addresses=[_address("L")],
    )

    report_id = asyncio.run(ORIGINAL_SAVE_REPORT(payload, config))

    assert report_id == "report-1"
    assert captured["title"] == "Orca Pool Scanner — Admission"
    assert captured["source"] == ("routine", "scan_orca_pools")
    assert captured["sections"] == [
        "Routine result",
        "Coverage and outcome",
        "Returned rows",
    ]
    assert captured["kpis"] == [
        ("Status", "degraded"),
        ("Scan kind", "admission"),
        ("Mutation", "false"),
    ]
    assert captured["markdown"] == [
        "This report is a review copy of the routine result. It does not select "
        "pools, infer Agent lifecycle state, or authorize a mutation."
    ]
    input_rows, input_columns = captured["tables"][0]
    assert input_columns == ["field", "value"]
    assert {item["field"] for item in input_rows} == {
        "risk_profile",
        "min_pool_tvl_usd",
        "min_fee_productivity_bps_per_day",
        "candidate_scan_limit",
        "excluded_base_mints",
        "excluded_pool_addresses",
        "refresh_pool_addresses",
    }
    assert {item["field"]: item["value"] for item in input_rows}[
        "candidate_scan_limit"
    ] == 1
    metadata_rows, metadata_columns = captured["tables"][1]
    assert metadata_columns == ["field", "value"]
    metadata = {item["field"]: item["value"] for item in metadata_rows}
    assert set(metadata) == set(payload) - {"table_columns", "table_data"}
    assert metadata["status"] == "degraded"
    assert metadata["scan_kind"] == "admission"
    assert metadata["observed_at"] == "2026-08-30T12:00:00Z"
    assert metadata["mutation"] is False
    assert metadata["completed_lenses"] == '["yieldovertvl24h","volume24h"]'
    assert metadata["discovered_count"] == 2
    assert metadata["eligible_count"] == metadata["returned_count"] == 1
    assert metadata["omitted_count"] == 0
    report_rows, report_columns = captured["tables"][2]
    assert report_columns == scan.ADMISSION_COLUMNS
    assert report_rows == [row]
    assert report_rows[0]["pool_address"] == _address("A")
    rendered = json.dumps(captured)
    assert "secret" not in rendered
    assert "hidden.example" not in rendered
    assert "token=x" not in rendered


def test_report_string_redaction_covers_wallet_headers_cookies_and_pem(monkeypatch):
    captured = {}

    class FakeBuilder:
        def __init__(self, title):
            captured["tables"] = []

        def source(self, *args):
            return self

        def tags(self, *args):
            return self

        def manual_order(self):
            return self

        def section(self, *args):
            return self

        def kpi(self, *args):
            return self

        def table(self, rows, columns=None):
            captured["tables"].append(copy.deepcopy(rows))
            return self

        def markdown(self, *args):
            return self

        async def save(self):
            return "redacted-report"

    monkeypatch.setattr(scan, "ReportBuilder", FakeBuilder)
    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        "VERY_SECRET_PRIVATE_KEY_MATERIAL\n"
        "-----END PRIVATE KEY-----"
    )
    sensitive = (
        f"wallet_address={_address('Z')} "
        "Authorization: Bearer full-auth-token\n"
        "Bearer standalone-token\n"
        "Cookie: session=secret-cookie; csrf=secret-csrf\n"
        "Set-Cookie: refresh=secret-refresh; HttpOnly\n"
        f"{private_key}"
    )
    payload = scan._unavailable_payload(_config(), OBSERVED_AT, RuntimeError("safe"))
    payload["errors"] = [sensitive]
    original = copy.deepcopy(payload)

    sanitized_error = scan._safe_error(sensitive, limit=5_000)
    report_id = asyncio.run(ORIGINAL_SAVE_REPORT(payload, _config()))

    assert report_id == "redacted-report"
    assert payload == original
    rendered = json.dumps(captured)
    for secret in (
        _address("Z"),
        "full-auth-token",
        "standalone-token",
        "secret-cookie",
        "secret-csrf",
        "secret-refresh",
        "VERY_SECRET_PRIVATE_KEY_MATERIAL",
    ):
        assert secret not in sanitized_error
        assert secret not in rendered
    assert sanitized_error.count("[redacted]") >= 6
    assert rendered.count("[redacted]") >= 6


def test_large_report_keeps_every_returned_row_without_character_truncation(
    monkeypatch,
):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    records = [
        _record(
            index,
            pool=_address(alphabet[index]),
            base=_address(alphabet[-(index + 1)]),
            symbol=f"TOKEN{index}",
        )
        for index in range(20)
    ]
    _install_discovery(monkeypatch, records)
    payload = _run(_config(candidate_scan_limit=20))
    captured = {}

    class FakeBuilder:
        def __init__(self, title):
            captured["tables"] = []

        def source(self, *args):
            return self

        def tags(self, *args):
            return self

        def manual_order(self):
            return self

        def section(self, *args):
            return self

        def kpi(self, *args):
            return self

        def table(self, rows, columns=None):
            captured["tables"].append((copy.deepcopy(rows), copy.deepcopy(columns)))
            return self

        def markdown(self, *args):
            return self

        async def save(self):
            return "large-report"

    monkeypatch.setattr(scan, "ReportBuilder", FakeBuilder)

    report_id = asyncio.run(
        ORIGINAL_SAVE_REPORT(payload, _config(candidate_scan_limit=20))
    )

    report_rows, report_columns = captured["tables"][-1]
    assert report_id == "large-report"
    assert report_columns == scan.ADMISSION_COLUMNS
    assert report_rows == payload["table_data"]
    assert len(report_rows) == 20
    assert len(json.dumps(report_rows)) > 2_000


def test_real_store_snapshot_truncation_preserves_complete_native_report(
    tmp_path,
    monkeypatch,
):
    marker = "TRAILING_SCANNER_EVIDENCE"
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    records = [
        _record(
            index,
            pool=_address(character),
            base=_address(alphabet[-(index + 1)]),
            symbol=marker if index == 19 else f"TOKEN{index}",
        )
        for index, character in enumerate("ABCDEFGHJKLMNPQRSTUV")
    ]
    _install_discovery(monkeypatch, records)
    monkeypatch.setattr(scan, "_save_report", ORIGINAL_SAVE_REPORT)
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    reports.reset_last_report_id()
    monkeypatch.setattr(
        scan,
        "_result_summary",
        lambda payload: json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )

    routine = RoutineInfo(
        name="scan_orca_pools",
        config_class=scan.Config,
        run_fn=scan.run,
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
            "trend_aware_lp_rebalancer_agent/scan_orca_pools",
            _config(candidate_scan_limit=20).model_dump(mode="json"),
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
    assert marker in json.dumps(instance["table_data"])

    projected = manager_result_payload("scan_orca_pools", instance_id, instance)
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
        "scan_orca_pools",
    )
    html, _filename = report
    assert marker in html


def test_report_failure_preserves_structured_status_and_has_no_retry(monkeypatch):
    calls = 0

    async def fail_report(payload, config):
        nonlocal calls
        calls += 1
        raise RuntimeError("report failed api_key=secret")

    _install_discovery(monkeypatch, [_record()])
    monkeypatch.setattr(scan, "_save_report", fail_report)

    payload = _run(_config())

    assert calls == 1
    assert payload["status"] == "complete"
    assert payload["returned_count"] == 1
    assert payload["mutation"] is False
    assert payload["report_error"].startswith("RuntimeError: report failed")
    assert "secret" not in payload["report_error"]


def test_unexpected_handled_run_error_still_returns_unavailable_report(monkeypatch):
    reports = []

    async def fail_scan(config, observed_at):
        raise RuntimeError("unexpected")

    async def report(payload, config):
        reports.append(copy.deepcopy(payload))
        return "report"

    monkeypatch.setattr(scan, "_scan_admission_candidates", fail_scan)
    monkeypatch.setattr(scan, "_save_report", report)

    payload = _run(_config())

    assert payload["status"] == "unavailable"
    assert payload["errors"] == ["RuntimeError: unexpected"]
    assert reports and reports[0]["status"] == "unavailable"
