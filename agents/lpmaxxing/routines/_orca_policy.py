import math
import re
from datetime import datetime, timezone
from typing import Any, Callable

CANONICAL_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DISCOVERY_LENSES = (
    "yieldovertvl24h",
    "yieldovertvl7d",
    "volume24h",
    "volume7d",
)
LIVE_STATS = ("1h", "4h", "24h", "7d")
LIVE_PROFILES = {
    "yield_focused",
    "yield_high_risk",
    "yield_extreme_risk",
    "yield_no_limit",
}
DRY_RUN_ONLY_PROFILES = {"category_scout", "meme_scout"}
VALID_RISK_PROFILES = LIVE_PROFILES | DRY_RUN_ONLY_PROFILES
RANGE_SAFETY_FACTOR = 1.5
MCDA_WEIGHTS = {
    "fee_productivity": 0.40,
    "recent_activity": 0.25,
    "net_change_stability": 0.15,
    "liquidity_depth": 0.10,
    "execution_simplicity": 0.10,
}

# Live profile fields are policy, not caller-overridable routine settings.
PROFILE_POLICY: dict[str, dict[str, Any]] = {
    "yield_focused": {
        "categories": [
            "stablecoin",
            "liquid_staking_token",
            "utility",
            "governance",
        ],
        "min_tvl_usd": 300_000.0,
        "min_volume_24h_usd": 200_000.0,
        "min_volume_7d_usd": 1_000_000.0,
        "max_abs_net_price_change_24h": 0.03,
        "min_fee_productivity": 0.0002,
        "stability_reference": 0.03,
    },
    "yield_high_risk": {
        "categories": [
            "stablecoin",
            "liquid_staking_token",
            "utility",
            "governance",
            "memecoin",
        ],
        "min_tvl_usd": 100_000.0,
        "min_volume_24h_usd": 150_000.0,
        "min_volume_7d_usd": 500_000.0,
        "max_abs_net_price_change_24h": 0.05,
        "min_fee_productivity": 0.0002,
        "stability_reference": 0.05,
    },
    "yield_extreme_risk": {
        "categories": [
            "stablecoin",
            "liquid_staking_token",
            "utility",
            "governance",
            "memecoin",
        ],
        "min_tvl_usd": 100_000.0,
        "min_volume_24h_usd": 150_000.0,
        "min_volume_7d_usd": 500_000.0,
        "max_abs_net_price_change_24h": 0.50,
        "min_fee_productivity": 0.0002,
        "stability_reference": 0.50,
    },
    "yield_no_limit": {
        "categories": [
            "stablecoin",
            "liquid_staking_token",
            "utility",
            "governance",
            "memecoin",
        ],
        "min_tvl_usd": 100_000.0,
        "min_volume_24h_usd": 150_000.0,
        "min_volume_7d_usd": 500_000.0,
        "max_abs_net_price_change_24h": None,
        "min_fee_productivity": 0.0002,
        "stability_reference": 0.50,
    },
    # Scouts are retained only for analysis. Their permissive gates are not live policy.
    "category_scout": {
        "categories": ["utility", "governance", "liquid_staking_token", "security"],
        "min_tvl_usd": 100_000.0,
        "min_volume_24h_usd": 50_000.0,
        "min_volume_7d_usd": 250_000.0,
        "max_abs_net_price_change_24h": 0.25,
        "min_fee_productivity": 0.0,
        "stability_reference": 0.25,
    },
    "meme_scout": {
        "categories": ["memecoin"],
        "min_tvl_usd": 25_000.0,
        "min_volume_24h_usd": 25_000.0,
        "min_volume_7d_usd": 50_000.0,
        "max_abs_net_price_change_24h": 0.75,
        "min_fee_productivity": 0.0,
        "stability_reference": 0.50,
    },
}


def first(obj: Any, paths: list[str]) -> Any:
    for path in paths:
        current = obj
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            if current is not None:
                return current
    return None


def text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def integer(value: Any) -> int | None:
    parsed = number(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def decimal_ratio(value: Any) -> float | None:
    """Accept Orca's bare decimal ratio, but never infer or convert units."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str) and re.fullmatch(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value.strip()
    ):
        parsed = float(value)
    else:
        return None
    return parsed if math.isfinite(parsed) else None


def candidate_age_seconds(
    candidate: dict[str, Any], now: datetime | None = None
) -> float | None:
    value = candidate.get("observed_at")
    if not isinstance(value, str):
        return None
    try:
        observed_at = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed_at.tzinfo is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("current time must be timezone-aware")
    return (
        current.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)
    ).total_seconds()


def candidate_is_fresh(candidate: dict[str, Any], now: datetime | None = None) -> bool:
    age = candidate_age_seconds(candidate, now)
    return age is not None and -30.0 <= age <= 600.0


def optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def token(record: dict[str, Any], side: str) -> dict[str, Any]:
    upper = side.upper()
    lower = side.lower()
    token_value = record.get(f"token{upper}")
    mint = record.get(f"tokenMint{upper}")
    merged = {
        **(mint if isinstance(mint, dict) else {}),
        **(token_value if isinstance(token_value, dict) else {}),
    }
    wrapped = {"merged": merged, "record": record}
    return {
        "symbol": text(
            first(
                wrapped,
                [
                    "merged.symbol",
                    f"record.token{upper}Symbol",
                    f"record.token_{lower}_symbol",
                ],
            )
        ),
        "mint": text(
            first(
                wrapped,
                [
                    "merged.mint",
                    "merged.address",
                    "merged.pubkey",
                    f"record.tokenMint{upper}",
                    f"record.token_mint_{lower}",
                ],
            )
        ),
        "decimals": integer(
            first(
                wrapped,
                [
                    "merged.decimals",
                    f"record.token{upper}Decimals",
                    f"record.token_{lower}.decimals",
                ],
            )
        ),
    }


def window_value(record: dict[str, Any], window: str, field: str) -> float | None:
    compact = window.replace("h", "h").replace("d", "d")
    aliases = {
        "volume": ["volume", "volumeUsd", "volumeUsdc"],
        "fees": ["fees", "feesUsd", "feesUsdc"],
    }
    paths = [f"stats.{window}.{name}" for name in aliases[field]]
    paths.extend(
        [
            f"record.{field}{compact}",
            f"record.{field}{compact}Usd",
            f"record.{field}_{compact}",
            f"record.{field}_{compact}_usd",
        ]
    )
    return number(first({"stats": record.get("stats", {}), "record": record}, paths))


def normalize_candidate(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {"malformed": True, "raw": record}
    stats = record.get("stats") if isinstance(record.get("stats"), dict) else {}
    wrapped = {"record": record, "stats": stats}
    candidate: dict[str, Any] = {
        "malformed": False,
        "address": text(
            first(record, ["address", "id", "pubkey", "poolAddress", "pool_address"])
        ),
        "updated_at": text(first(record, ["updatedAt", "updated_at"])),
        "token_a": token(record, "a"),
        "token_b": token(record, "b"),
        "scanner_price": number(
            first(record, ["price", "currentPrice", "current_price", "tokenPrice"])
        ),
        "tvl_usd": number(
            first(record, ["tvlUsdc", "tvlUsd", "tvlUSD", "tvl", "totalValueLockedUsd"])
        ),
        "net_price_change_24h": decimal_ratio(
            first(
                wrapped,
                [
                    "stats.24h.priceDelta",
                    "stats.24h.price_delta",
                    "record.priceDelta24h",
                    "record.price_delta_24h",
                ],
            )
        ),
        "price_delta_24h_raw": first(
            wrapped,
            [
                "stats.24h.priceDelta",
                "stats.24h.price_delta",
                "record.priceDelta24h",
                "record.price_delta_24h",
            ],
        ),
        "fee_rate_raw": number(first(record, ["feeRate", "fee_rate"])),
        "adaptive_fee_enabled": optional_bool(
            first(record, ["adaptiveFeeEnabled", "adaptive_fee_enabled"])
        ),
        "fee_tier_index": integer(
            first(record, ["feeTierIndex", "fee_tier_index", "feeTier", "fee_tier"])
        ),
        "tick_spacing": integer(first(record, ["tickSpacing", "tick_spacing"])),
        "has_warning": optional_bool(
            first(record, ["hasWarning", "has_warning", "warning", "isWarning"])
        ),
        "source_categories": sorted(set(record.get("_scan_categories", []))),
        "source_lenses": sorted(set(record.get("_scan_lenses", []))),
    }
    for window in (*LIVE_STATS, "30d"):
        candidate[f"volume_{window}_usd"] = window_value(record, window, "volume")
        candidate[f"fees_{window}_usd"] = window_value(record, window, "fees")
    return candidate


def derive(candidate: dict[str, Any], budget: float) -> None:
    tvl = candidate.get("tvl_usd")
    if tvl is None or tvl <= 0:
        return
    factors = {"1h": 24.0, "4h": 6.0, "24h": 1.0, "7d": 1.0 / 7.0}
    for window, factor in factors.items():
        fees = candidate.get(f"fees_{window}_usd")
        candidate[f"fee_productivity_{window}"] = (
            fees / tvl * factor if fees is not None and fees >= 0 else None
        )
    fee_24h = candidate.get("fee_productivity_24h")
    fee_7d = candidate.get("fee_productivity_7d")
    candidate["fee_tvl_24h"] = fee_24h
    candidate["fee_tvl_7d_daily"] = fee_7d
    candidate["sustained_fee_productivity"] = (
        min(fee_24h, fee_7d) if fee_24h is not None and fee_7d is not None else None
    )
    denominator = candidate.get("fee_productivity_7d")
    candidate["fee_momentum_1h_x"] = (
        candidate.get("fee_productivity_1h") / denominator
        if candidate.get("fee_productivity_1h") is not None and denominator
        else None
    )
    candidate["fee_momentum_4h_x"] = (
        candidate.get("fee_productivity_4h") / denominator
        if candidate.get("fee_productivity_4h") is not None and denominator
        else None
    )
    candidate["volume_tvl_24h"] = (
        candidate["volume_24h_usd"] / tvl
        if candidate.get("volume_24h_usd") is not None
        else None
    )
    candidate["volume_tvl_7d_daily"] = (
        candidate["volume_7d_usd"] / 7.0 / tvl
        if candidate.get("volume_7d_usd") is not None
        else None
    )
    sustained = candidate.get("sustained_fee_productivity")
    candidate["gross_fee_estimate_quote_per_day"] = (
        budget * sustained if sustained is not None else None
    )


def metadata_ok(candidate: dict[str, Any]) -> bool:
    return all(
        token_value.get("symbol")
        and token_value.get("mint")
        and isinstance(token_value.get("decimals"), int)
        and token_value["decimals"] >= 0
        for token_value in (candidate.get("token_a", {}), candidate.get("token_b", {}))
    )


def gate(
    candidate: dict[str, Any],
    policy: dict[str, Any],
    live_profile: bool,
    excluded: set[str],
) -> str | None:
    if candidate.get("malformed"):
        return "malformed_record"
    if not candidate.get("address"):
        return "missing_pool_address"
    if candidate["address"] in excluded:
        return "explicitly_excluded"
    if not metadata_ok(candidate):
        return "missing_token_metadata"
    if candidate.get("has_warning") is None:
        return "missing_warning_state"
    if candidate["has_warning"]:
        return "has_warning"
    if live_profile:
        token_a = candidate["token_a"]
        token_b = candidate["token_b"]
        if (
            token_b.get("mint") != CANONICAL_USDC_MINT
            or str(token_b.get("symbol") or "").upper() != "USDC"
            or token_b.get("decimals") != 6
            or token_a.get("mint") == token_b.get("mint")
        ):
            return "invalid_canonical_usdc_token_b"
    candidate["base_symbol"] = candidate["token_a"]["symbol"]
    candidate["quote_symbol"] = candidate["token_b"]["symbol"]
    candidate["trading_pair"] = (
        f"{candidate['base_symbol']}-{candidate['quote_symbol']}"
    )
    candidate["price_orientation"] = "token_b_per_token_a"

    required = [
        "tvl_usd",
        "volume_24h_usd",
        "volume_7d_usd",
        "fees_24h_usd",
        "fees_7d_usd",
    ]
    if live_profile:
        required.extend(
            [
                "scanner_price",
                "volume_1h_usd",
                "volume_4h_usd",
                "fees_1h_usd",
                "fees_4h_usd",
                "fee_rate_raw",
                "adaptive_fee_enabled",
                "fee_tier_index",
                "tick_spacing",
            ]
        )
    for field in required:
        value = candidate.get(field)
        if value is None:
            return f"missing_{field}"
        if isinstance(value, (int, float)) and value < 0:
            return f"negative_{field}"
    if candidate.get("scanner_price") is not None and candidate["scanner_price"] <= 0:
        return "invalid_scanner_price"
    if candidate.get("net_price_change_24h") is None:
        raw = candidate.get("price_delta_24h_raw")
        return (
            "ambiguous_price_delta_24h"
            if raw is not None
            else "missing_price_delta_24h"
        )
    if candidate.get("tick_spacing") is None or candidate["tick_spacing"] <= 0:
        return "invalid_tick_spacing"
    if candidate["tvl_usd"] < policy["min_tvl_usd"]:
        return "tvl_below_minimum"
    if candidate["volume_24h_usd"] < policy["min_volume_24h_usd"]:
        return "volume_24h_below_minimum"
    if candidate["volume_7d_usd"] < policy["min_volume_7d_usd"]:
        return "volume_7d_below_minimum"
    maximum = policy["max_abs_net_price_change_24h"]
    if maximum is not None and abs(candidate["net_price_change_24h"]) > maximum:
        return "net_price_change_24h_above_maximum"
    if candidate.get("fee_productivity_24h") is None:
        return "missing_fee_productivity_24h"
    if candidate["fee_productivity_24h"] < policy["min_fee_productivity"]:
        return "fee_productivity_24h_below_minimum"
    if candidate.get("fee_productivity_7d") is None:
        return "missing_fee_productivity_7d_daily"
    if candidate["fee_productivity_7d"] < policy["min_fee_productivity"]:
        return "fee_productivity_7d_daily_below_minimum"
    return None


def multiple_score(value: float, minimum: float) -> float:
    if minimum <= 0:
        return 5.0 if value > 0 else 1.0
    multiple = value / minimum
    if multiple <= 0:
        return 0.0
    points = ((1.0, 1.0), (2.0, 2.0), (5.0, 3.0), (10.0, 4.0), (20.0, 5.0))
    if multiple < 1.0:
        return multiple
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if multiple <= right_x:
            return left_y + (multiple - left_x) / (right_x - left_x) * (
                right_y - left_y
            )
    return 5.0


def score(candidate: dict[str, Any], policy: dict[str, Any]) -> None:
    stability_reference = policy["stability_reference"]
    scores = {
        "fee_productivity": multiple_score(
            candidate["sustained_fee_productivity"], policy["min_fee_productivity"]
        ),
        "recent_activity": 0.6
        * multiple_score(candidate["volume_24h_usd"], policy["min_volume_24h_usd"])
        + 0.4 * multiple_score(candidate["volume_7d_usd"], policy["min_volume_7d_usd"]),
        "net_change_stability": 5.0
        * (
            1.0
            - min(abs(candidate["net_price_change_24h"]), stability_reference)
            / stability_reference
        ),
        "liquidity_depth": multiple_score(candidate["tvl_usd"], policy["min_tvl_usd"]),
        "execution_simplicity": 4.0 if candidate["adaptive_fee_enabled"] else 5.0,
    }
    candidate["criteria_raw"] = {
        "sustained_fee_productivity": candidate["sustained_fee_productivity"],
        "volume_24h_usd": candidate["volume_24h_usd"],
        "volume_7d_usd": candidate["volume_7d_usd"],
        "abs_net_price_change_24h": abs(candidate["net_price_change_24h"]),
        "tvl_usd": candidate["tvl_usd"],
        "adaptive_fee_enabled": candidate["adaptive_fee_enabled"],
    }
    candidate["criteria_scores"] = {
        key: round(max(0.0, min(5.0, value)), 6) for key, value in scores.items()
    }
    candidate["weighted_score"] = round(
        sum(
            candidate["criteria_scores"][key] * MCDA_WEIGHTS[key]
            for key in MCDA_WEIGHTS
        ),
        6,
    )


def range_plan(
    candidate: dict[str, Any], risk_profile: str
) -> tuple[dict[str, Any], str | None]:
    tick_spacing = candidate.get("tick_spacing")
    if not isinstance(tick_spacing, int) or tick_spacing <= 0:
        return {"status": "infeasible"}, "invalid_tick_spacing"
    try:
        tick_floor = 1.0001 ** (tick_spacing * 2) - 1.0
    except OverflowError:
        return {"status": "infeasible"}, "invalid_tick_spacing"
    if not math.isfinite(tick_floor) or tick_floor <= 0:
        return {"status": "infeasible"}, "invalid_tick_spacing"
    change_width = abs(candidate["net_price_change_24h"]) * RANGE_SAFETY_FACTOR
    required = max(tick_floor, change_width)
    sustained = candidate["sustained_fee_productivity"]
    preset: str | None = None
    width: float | None = None
    capped = False
    maximum: float | None = None
    if required <= 0.015 and sustained >= 0.0005:
        preset, width, maximum = "concentrated", min(0.015, max(0.005, required)), 0.015
    elif required <= 0.03:
        preset, width, maximum = "balanced", min(0.03, max(0.01, required)), 0.03
    elif required <= 0.08 and sustained >= 0.0004:
        preset, width, maximum = "defensive", min(0.08, max(0.02, required)), 0.08
    elif risk_profile == "yield_extreme_risk" and required <= 0.95:
        preset, width, maximum = "extreme", min(0.95, max(0.08, required)), 0.95
    elif risk_profile == "yield_no_limit":
        preset, width, maximum = "extreme", min(0.95, max(0.08, required)), 0.95
        capped = required > 0.95
    if risk_profile == "yield_no_limit":
        maximum = 0.95
    plan = {
        "status": "feasible" if preset else "infeasible",
        "range_safety_factor": RANGE_SAFETY_FACTOR,
        "tick_spacing_floor": tick_floor,
        "tick_spacing_floor_pct": tick_floor * 100.0,
        "change_based_half_width": change_width,
        "change_based_half_width_pct": change_width * 100.0,
        "uncapped_required_half_width": required,
        "uncapped_required_half_width_pct": required * 100.0,
        "maximum_executable_half_width": maximum,
        "maximum_executable_half_width_pct": (
            maximum * 100.0 if maximum is not None else None
        ),
        "width_capped": capped,
        "preset": preset,
        "provisional_half_width": width,
        "provisional_half_width_pct": width * 100.0 if width is not None else None,
    }
    return plan, None if preset else "range_infeasible_for_profile"


def rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -candidate["weighted_score"],
        -candidate["sustained_fee_productivity"],
        -candidate["criteria_scores"]["recent_activity"],
        abs(candidate["net_price_change_24h"]),
        -candidate["tvl_usd"],
        candidate["address"],
    )


def revalidate_candidate_contract(
    candidate: dict[str, Any],
    normalized: dict[str, Any],
    budget: float,
    risk_profile: str,
    contains_executable_bounds: Callable[[Any], bool],
    values_match: Callable[[Any, Any], bool],
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    errors: list[str] = []
    if risk_profile not in LIVE_PROFILES:
        return False, {"errors": ["invalid live risk profile"]}, None
    if contains_executable_bounds(candidate):
        errors.append("scanner candidate contains executable bounds")

    policy = PROFILE_POLICY[risk_profile]
    derive(normalized, budget)
    gate_reason = gate(normalized, policy, True, set())
    if gate_reason:
        errors.append(f"candidate hard gate failed: {gate_reason}")

    categories = candidate.get("source_categories")
    lenses = candidate.get("source_lenses")
    if (
        not isinstance(categories, list)
        or not categories
        or categories != sorted(set(categories))
        or not set(categories).issubset(policy["categories"])
    ):
        errors.append("candidate lacks allowed Orca category evidence")
    if (
        not isinstance(lenses, list)
        or not lenses
        or lenses != sorted(set(lenses))
        or not set(lenses).issubset(DISCOVERY_LENSES)
    ):
        errors.append("candidate lacks Orca discovery-lens evidence")

    expected_range = None
    if not gate_reason:
        score(normalized, policy)
        expected_range, range_reason = range_plan(normalized, risk_profile)
        if range_reason:
            errors.append(f"candidate range is infeasible: {range_reason}")
        normalized["range_plan"] = expected_range

    maximum = policy["max_abs_net_price_change_24h"]
    expected_fields = {
        "risk_profile": risk_profile,
        "trading_pair": normalized.get("trading_pair"),
        "price_orientation": "token_b_per_token_a",
        "fee_tvl_24h": normalized.get("fee_tvl_24h"),
        "fee_tvl_7d_daily": normalized.get("fee_tvl_7d_daily"),
        "fee_productivity_1h_dailyized_bps": (
            normalized.get("fee_productivity_1h") * 10_000.0
            if normalized.get("fee_productivity_1h") is not None
            else None
        ),
        "fee_productivity_4h_dailyized_bps": (
            normalized.get("fee_productivity_4h") * 10_000.0
            if normalized.get("fee_productivity_4h") is not None
            else None
        ),
        "fee_productivity_24h_bps_per_day": (
            normalized.get("fee_productivity_24h") * 10_000.0
            if normalized.get("fee_productivity_24h") is not None
            else None
        ),
        "fee_productivity_7d_daily_bps": (
            normalized.get("fee_productivity_7d") * 10_000.0
            if normalized.get("fee_productivity_7d") is not None
            else None
        ),
        "sustained_fee_productivity": normalized.get("sustained_fee_productivity"),
        "sustained_fee_productivity_bps_per_day": (
            normalized.get("sustained_fee_productivity") * 10_000.0
            if normalized.get("sustained_fee_productivity") is not None
            else None
        ),
        "fee_momentum_1h_x": normalized.get("fee_momentum_1h_x"),
        "fee_momentum_4h_x": normalized.get("fee_momentum_4h_x"),
        "volume_tvl_24h": normalized.get("volume_tvl_24h"),
        "volume_tvl_7d_daily": normalized.get("volume_tvl_7d_daily"),
        "net_price_change_24h_pct": (
            normalized.get("net_price_change_24h") * 100.0
            if normalized.get("net_price_change_24h") is not None
            else None
        ),
        "profile_net_change_limit_pct": (
            maximum * 100.0 if maximum is not None else None
        ),
        "profile_net_change_limit_enabled": maximum is not None,
        "fee_rate_fraction": (
            normalized.get("fee_rate_raw") / 1_000_000.0
            if normalized.get("fee_rate_raw") is not None
            else None
        ),
        "fee_rate_bps": (
            normalized.get("fee_rate_raw") / 100.0
            if normalized.get("fee_rate_raw") is not None
            else None
        ),
        "gross_fee_estimate_quote_per_day": normalized.get(
            "gross_fee_estimate_quote_per_day"
        ),
        "session_budget_quote": budget,
        "total_amount_quote": budget,
    }
    if not gate_reason:
        expected_fields.update(
            {
                "criteria_raw": normalized.get("criteria_raw"),
                "criteria_scores": normalized.get("criteria_scores"),
                "mcda_weights": MCDA_WEIGHTS,
                "weighted_score": normalized.get("weighted_score"),
                "score": normalized.get("weighted_score"),
                "range_plan": expected_range,
                "preset_suggestion": (expected_range or {}).get("preset"),
            }
        )
    for field, expected in expected_fields.items():
        if field not in candidate or not values_match(candidate.get(field), expected):
            errors.append(f"candidate field '{field}' does not match recomputed policy")

    return (
        not errors,
        {"passed": not errors, "errors": errors, "recomputed": expected_fields},
        normalized if not errors else None,
    )
