"""Pure Orca V3.1 pool normalization, eligibility, ranking, and revalidation.

Public callers use ``normalize_record`` before ``deduplicate_records``, then
``rank_pools`` for scanner output. ``revalidate_candidate`` reconstructs and
checks a complete scanner candidate and an optional chosen range option.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

CANONICAL_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DISCOVERY_LENSES = (
    "yieldovertvl24h",
    "yieldovertvl7d",
    "volume24h",
    "volume7d",
)
LIVE_STATS = ("1h", "4h", "24h", "7d")
LIVE_PROFILES = (
    "yield_focused",
    "yield_high_risk",
    "yield_extreme_risk",
    "yield_no_limit",
)
RANGE_SAFETY_FACTOR = 1.5
MCDA_WEIGHTS = {
    "fee_productivity": 0.40,
    "recent_activity": 0.25,
    "net_change_stability": 0.15,
    "liquidity_depth": 0.10,
    "execution_simplicity": 0.10,
}

_FOCUSED_CATEGORIES = (
    "stablecoin",
    "liquid_staking_token",
    "utility",
    "governance",
)
_BROAD_CATEGORIES = (*_FOCUSED_CATEGORIES, "memecoin")
PROFILE_POLICY: dict[str, dict[str, Any]] = {
    "yield_focused": {
        "categories": _FOCUSED_CATEGORIES,
        "min_tvl_usd": 300_000.0,
        "min_volume_24h_usd": 200_000.0,
        "min_volume_7d_usd": 1_000_000.0,
        "max_abs_net_price_change_24h": 0.03,
        "min_fee_productivity": 0.0002,
        "stability_reference": 0.03,
    },
    "yield_high_risk": {
        "categories": _BROAD_CATEGORIES,
        "min_tvl_usd": 100_000.0,
        "min_volume_24h_usd": 150_000.0,
        "min_volume_7d_usd": 500_000.0,
        "max_abs_net_price_change_24h": 0.05,
        "min_fee_productivity": 0.0002,
        "stability_reference": 0.05,
    },
    "yield_extreme_risk": {
        "categories": _BROAD_CATEGORIES,
        "min_tvl_usd": 100_000.0,
        "min_volume_24h_usd": 150_000.0,
        "min_volume_7d_usd": 500_000.0,
        "max_abs_net_price_change_24h": 0.50,
        "min_fee_productivity": 0.0002,
        "stability_reference": 0.50,
    },
    "yield_no_limit": {
        "categories": _BROAD_CATEGORIES,
        "min_tvl_usd": 100_000.0,
        "min_volume_24h_usd": 150_000.0,
        "min_volume_7d_usd": 500_000.0,
        "max_abs_net_price_change_24h": None,
        "min_fee_productivity": 0.0002,
        "stability_reference": 0.50,
    },
}

_DECIMAL_RATIO = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_FORBIDDEN_CANDIDATE_KEYS = {
    "selected",
    "selected_candidate",
    "lower_price",
    "upper_price",
    "lower_limit_price",
    "upper_limit_price",
    "mutation",
    "next_action",
}
_GROSS_FEE_QUALIFICATION = (
    "Pool-average quote per day before concentration, competing liquidity, "
    "inventory loss, swaps, transaction fees, rent, and close costs; not a "
    "position-yield forecast."
)


def get_profile(risk_profile: str) -> dict[str, Any]:
    """Return a JSON-safe copy of one controlling live profile."""
    if risk_profile not in PROFILE_POLICY:
        raise ValueError("invalid_risk_profile")
    result = deepcopy(PROFILE_POLICY[risk_profile])
    result["categories"] = list(result["categories"])
    return result


def _path_values(obj: Any, paths: Iterable[str]) -> list[Any]:
    values = []
    for path in paths:
        current = obj
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            values.append(current)
    return values


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip()
    return result or None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str) and _DECIMAL_RATIO.fullmatch(value.strip()):
        result = float(value)
    else:
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    result = _number(value)
    return int(result) if result is not None and result.is_integer() else None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _decimal_ratio(value: Any) -> float | None:
    return _number(value)


def _resolve_alias(
    obj: Any, paths: Iterable[str], normalizer: Any
) -> tuple[Any, str | None]:
    raw_values = [value for value in _path_values(obj, paths) if value is not None]
    if not raw_values:
        return None, None
    values = []
    for raw_value in raw_values:
        value = normalizer(raw_value)
        if value is None:
            return None, "invalid"
        values.append(value)
    if any(value != values[0] for value in values[1:]):
        return None, "conflicting"
    return values[0], None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def _token(
    record: dict[str, Any], side: str
) -> tuple[dict[str, Any] | None, str | None]:
    upper, lower = side.upper(), side.lower()
    token_value = record.get(f"token{upper}")
    mint_value = record.get(f"tokenMint{upper}")
    if token_value is not None and not isinstance(token_value, dict):
        return None, f"invalid_token_{lower}_metadata"
    if mint_value is not None and not isinstance(mint_value, (dict, str)):
        return None, f"invalid_token_{lower}_metadata"

    symbol, error = _resolve_alias(
        record,
        (
            f"token{upper}.symbol",
            f"tokenMint{upper}.symbol",
            f"token{upper}Symbol",
            f"token_{lower}_symbol",
        ),
        _text,
    )
    if error:
        return None, f"{error}_token_{lower}_symbol_aliases"
    mint_paths = [
        f"token{upper}.mint",
        f"token{upper}.address",
        f"token{upper}.pubkey",
        f"tokenMint{upper}.mint",
        f"tokenMint{upper}.address",
        f"tokenMint{upper}.pubkey",
        f"token_mint_{lower}",
    ]
    if isinstance(mint_value, str):
        mint_paths.append(f"tokenMint{upper}")
    mint, error = _resolve_alias(record, mint_paths, _text)
    if error:
        return None, f"{error}_token_{lower}_mint_aliases"
    decimals, error = _resolve_alias(
        record,
        (
            f"token{upper}.decimals",
            f"tokenMint{upper}.decimals",
            f"token{upper}Decimals",
            f"token_{lower}.decimals",
        ),
        _integer,
    )
    if error:
        return None, f"{error}_token_{lower}_decimals_aliases"
    return {"symbol": symbol, "mint": mint, "decimals": decimals}, None


def _window_value(
    record: dict[str, Any], window: str, field: str
) -> tuple[float | None, str | None]:
    aliases = {
        "volume": ("volume", "volumeUsd", "volumeUsdc"),
        "fees": ("fees", "feesUsd", "feesUsdc"),
    }
    paths = [f"stats.{window}.{name}" for name in aliases[field]]
    paths.extend(
        (
            f"{field}{window}",
            f"{field}{window}Usd",
            f"{field}_{window}",
            f"{field}_{window}_usd",
        )
    )
    return _resolve_alias(record, paths, _number)


def _metadata_valid(token: Any) -> bool:
    return (
        isinstance(token, dict)
        and bool(token.get("symbol"))
        and bool(token.get("mint"))
        and isinstance(token.get("decimals"), int)
        and not isinstance(token.get("decimals"), bool)
        and token["decimals"] >= 0
    )


def _invariant_rejection(pool: dict[str, Any]) -> str | None:
    if not pool.get("pool_address"):
        return "missing_pool_address"
    if not _metadata_valid(pool.get("token_a")) or not _metadata_valid(
        pool.get("token_b")
    ):
        return "missing_token_metadata"
    if (
        pool["token_b"]["mint"] != CANONICAL_USDC_MINT
        or pool["token_b"]["symbol"].upper() != "USDC"
        or pool["token_b"]["decimals"] != 6
        or pool["token_a"]["mint"] == pool["token_b"]["mint"]
    ):
        return "invalid_canonical_usdc_token_b"
    if pool.get("has_warning") is None:
        return "missing_warning_state"
    if pool["has_warning"] is not False:
        return "has_warning"
    for field in ("scanner_price", "tvl_usd"):
        value = pool.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            return f"missing_{field}"
        if value <= 0:
            return f"invalid_{field}"
    delta = pool.get("net_price_change_24h")
    if (
        not isinstance(delta, (int, float))
        or isinstance(delta, bool)
        or not math.isfinite(delta)
    ):
        return "missing_price_delta_24h"
    fee_rate = pool.get("fee_rate_raw")
    if (
        not isinstance(fee_rate, (int, float))
        or isinstance(fee_rate, bool)
        or not math.isfinite(fee_rate)
    ):
        return "missing_fee_rate"
    if fee_rate < 0:
        return "invalid_fee_rate"
    if not isinstance(pool.get("adaptive_fee_enabled"), bool):
        return "missing_adaptive_fee_state"
    for field in ("fee_tier_index", "tick_spacing"):
        value = pool.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"missing_{field}"
        if value < 0 or (field == "tick_spacing" and value == 0):
            return f"invalid_{field}"
    for window in LIVE_STATS:
        for field in ("fees", "volume"):
            name = f"{field}_{window}_usd"
            value = pool.get(name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                return f"missing_{name}"
            if value < 0:
                return f"negative_{name}"
    return None


def normalize_record(
    record: Any,
    source_category: str,
    discovery_lens: str,
    record_index: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize one raw Orca row, failing profile-independent live gates."""
    if source_category not in _BROAD_CATEGORIES:
        return None, "invalid_source_category"
    if discovery_lens not in DISCOVERY_LENSES:
        return None, "invalid_discovery_lens"
    if (
        not isinstance(record_index, int)
        or isinstance(record_index, bool)
        or record_index < 0
    ):
        return None, "invalid_record_index"
    if not isinstance(record, dict):
        return None, "malformed_record"

    if record.get("stats") is not None and not isinstance(record["stats"], dict):
        return None, "malformed_stats"
    pool_address, error = _resolve_alias(
        record,
        ("address", "id", "pubkey", "poolAddress", "pool_address"),
        _text,
    )
    if error:
        return None, f"{error}_pool_address_aliases"
    updated_at, error = _resolve_alias(record, ("updatedAt", "updated_at"), _text)
    if error:
        return None, f"{error}_updated_at_aliases"
    token_a, error = _token(record, "a")
    if error:
        return None, error
    token_b, error = _token(record, "b")
    if error:
        return None, error

    fields = {
        "scanner_price": (
            ("price", "currentPrice", "current_price", "tokenPrice"),
            _number,
        ),
        "tvl_usd": (
            ("tvlUsdc", "tvlUsd", "tvlUSD", "tvl", "totalValueLockedUsd"),
            _number,
        ),
        "net_price_change_24h": (
            (
                "stats.24h.priceDelta",
                "stats.24h.price_delta",
                "priceDelta24h",
                "price_delta_24h",
            ),
            _decimal_ratio,
        ),
        "fee_rate_raw": (("feeRate", "fee_rate"), _number),
        "adaptive_fee_enabled": (
            ("adaptiveFeeEnabled", "adaptive_fee_enabled"),
            _bool,
        ),
        "fee_tier_index": (
            ("feeTierIndex", "fee_tier_index", "feeTier", "fee_tier"),
            _integer,
        ),
        "tick_spacing": (("tickSpacing", "tick_spacing"), _integer),
        "has_warning": (
            ("hasWarning", "has_warning", "warning", "isWarning"),
            _bool,
        ),
    }
    resolved: dict[str, Any] = {}
    for field, (paths, normalizer) in fields.items():
        resolved[field], error = _resolve_alias(record, paths, normalizer)
        if error:
            if field == "net_price_change_24h" and error == "invalid":
                return None, "ambiguous_price_delta_24h"
            return None, f"{error}_{field}_aliases"

    for window in LIVE_STATS:
        for field in ("volume", "fees"):
            name = f"{field}_{window}_usd"
            resolved[name], error = _window_value(record, window, field)
            if error:
                return None, f"{error}_{name}_aliases"

    pool: dict[str, Any] = {
        "_normalized": True,
        "pool_address": pool_address,
        "source_updated_at": updated_at,
        "_updated_at_epoch": (
            _timestamp(updated_at).timestamp() if _timestamp(updated_at) else None
        ),
        "token_a": token_a,
        "token_b": token_b,
        **resolved,
        "source_evidence": [
            {
                "category": source_category,
                "discovery_lens": discovery_lens,
                "record_index": record_index,
                "updated_at": updated_at,
            }
        ],
        "source_categories": [source_category],
        "source_lenses": [discovery_lens],
    }

    invariant_rejection = _invariant_rejection(pool)
    if invariant_rejection and invariant_rejection != "missing_price_delta_24h":
        return None, invariant_rejection
    if pool["net_price_change_24h"] is None:
        return None, "missing_price_delta_24h"
    if invariant_rejection:
        return None, invariant_rejection
    return pool, None


def _source_key(source: dict[str, Any]) -> tuple[Any, ...]:
    return (source["category"], source["discovery_lens"], source["record_index"])


def _preferred_source(sources: list[dict[str, Any]]) -> dict[str, Any]:
    parseable = [(source, _timestamp(source["updated_at"])) for source in sources]
    parseable = [(source, stamp) for source, stamp in parseable if stamp is not None]
    if parseable:
        latest = max(stamp for _, stamp in parseable)
        choices = [source for source, stamp in parseable if stamp == latest]
    else:
        choices = sources
    return min(choices, key=_source_key)


def deduplicate_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate already-valid rows by address and union their source evidence."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("_normalized") is not True:
            raise ValueError("invalid_normalized_record")
        grouped.setdefault(record["pool_address"], []).append(deepcopy(record))

    result: list[dict[str, Any]] = []
    for address in sorted(grouped):
        duplicates = grouped[address]
        parseable = [row for row in duplicates if row["_updated_at_epoch"] is not None]
        if parseable:
            latest = max(row["_updated_at_epoch"] for row in parseable)
            choices = [row for row in parseable if row["_updated_at_epoch"] == latest]
        else:
            choices = duplicates
        selected = min(choices, key=lambda row: _source_key(row["source_evidence"][0]))
        sources = {
            (
                source["category"],
                source["discovery_lens"],
                source["record_index"],
                source["updated_at"],
            )
            for row in duplicates
            for source in row["source_evidence"]
        }
        selected["source_evidence"] = [
            {
                "category": category,
                "discovery_lens": lens,
                "record_index": index,
                "updated_at": updated_at,
            }
            for category, lens, index, updated_at in sorted(
                sources,
                key=lambda item: (item[0], item[1], item[2], item[3] or ""),
            )
        ]
        selected["source_categories"] = sorted(
            {source["category"] for source in selected["source_evidence"]}
        )
        selected["source_lenses"] = sorted(
            {source["discovery_lens"] for source in selected["source_evidence"]}
        )
        result.append(selected)
    return result


def _multiple_score(value: float, minimum: float) -> float:
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


def _range_options(
    pool: dict[str, Any], risk_profile: str
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        tick_floor = 1.0001 ** (pool["tick_spacing"] * 2) - 1.0
    except OverflowError:
        return [], "invalid_tick_spacing"
    if not math.isfinite(tick_floor) or tick_floor <= 0:
        return [], "invalid_tick_spacing"
    change_width = abs(pool["net_price_change_24h"]) * RANGE_SAFETY_FACTOR
    required = max(tick_floor, change_width)
    sustained = pool["sustained_fee_productivity"]
    specs: list[tuple[str, bool, float, float]] = [
        ("concentrated", required <= 0.015 and sustained >= 0.0005, 0.005, 0.015),
        ("balanced", required <= 0.03, 0.01, 0.03),
        ("defensive", required <= 0.08 and sustained >= 0.0004, 0.02, 0.08),
        (
            "extreme",
            (risk_profile == "yield_extreme_risk" and required <= 0.95)
            or risk_profile == "yield_no_limit",
            0.08,
            0.95,
        ),
    ]
    options = []
    for name, eligible, minimum, maximum in specs:
        if not eligible:
            continue
        width = min(maximum, max(minimum, required))
        capped = (
            risk_profile == "yield_no_limit" and name == "extreme" and required > 0.95
        )
        options.append(
            {
                "name": name,
                "range_safety_factor": RANGE_SAFETY_FACTOR,
                "tick_spacing_floor": tick_floor,
                "tick_spacing_floor_pct": tick_floor * 100.0,
                "change_based_half_width": change_width,
                "change_based_half_width_pct": change_width * 100.0,
                "uncapped_required_half_width": required,
                "uncapped_required_half_width_pct": required * 100.0,
                "maximum_executable_half_width": maximum,
                "maximum_executable_half_width_pct": maximum * 100.0,
                "width_capped": capped,
                "provisional_half_width": width,
                "provisional_half_width_pct": width * 100.0,
            }
        )
    return options, None if options else "range_infeasible_for_profile"


def evaluate_pool(
    pool: dict[str, Any],
    risk_profile: str,
    observed_at: str,
    total_amount_quote: float,
) -> tuple[dict[str, Any] | None, str | None]:
    """Apply profile gates and produce one complete, unranked public candidate."""
    if risk_profile not in PROFILE_POLICY:
        raise ValueError("invalid_risk_profile")
    budget = _number(total_amount_quote)
    if budget is None or budget <= 0:
        raise ValueError("invalid_total_amount_quote")
    if _timestamp(observed_at) is None:
        raise ValueError("invalid_observed_at")
    if not isinstance(pool, dict) or pool.get("_normalized") is not True:
        raise ValueError("invalid_normalized_record")

    policy = PROFILE_POLICY[risk_profile]
    if not pool.get("source_categories") or not set(pool["source_categories"]).issubset(
        policy["categories"]
    ):
        return None, "source_category_not_allowed"
    if pool["tvl_usd"] < policy["min_tvl_usd"]:
        return None, "tvl_below_minimum"
    if pool["volume_24h_usd"] < policy["min_volume_24h_usd"]:
        return None, "volume_24h_below_minimum"
    if pool["volume_7d_usd"] < policy["min_volume_7d_usd"]:
        return None, "volume_7d_below_minimum"
    maximum_change = policy["max_abs_net_price_change_24h"]
    if (
        maximum_change is not None
        and abs(pool["net_price_change_24h"]) > maximum_change
    ):
        return None, "net_price_change_24h_above_maximum"

    item = deepcopy(pool)
    tvl = item["tvl_usd"]
    productivity = {
        "1h": item["fees_1h_usd"] / tvl * 24.0,
        "4h": item["fees_4h_usd"] / tvl * 6.0,
        "24h": item["fees_24h_usd"] / tvl,
        "7d": item["fees_7d_usd"] / 7.0 / tvl,
    }
    if productivity["24h"] < policy["min_fee_productivity"]:
        return None, "fee_productivity_24h_below_minimum"
    if productivity["7d"] < policy["min_fee_productivity"]:
        return None, "fee_productivity_7d_daily_below_minimum"
    item["sustained_fee_productivity"] = min(productivity["24h"], productivity["7d"])

    stability_reference = policy["stability_reference"]
    scores = {
        "fee_productivity": _multiple_score(
            item["sustained_fee_productivity"], policy["min_fee_productivity"]
        ),
        "recent_activity": 0.6
        * _multiple_score(item["volume_24h_usd"], policy["min_volume_24h_usd"])
        + 0.4 * _multiple_score(item["volume_7d_usd"], policy["min_volume_7d_usd"]),
        "net_change_stability": 5.0
        * (
            1.0
            - min(abs(item["net_price_change_24h"]), stability_reference)
            / stability_reference
        ),
        "liquidity_depth": _multiple_score(item["tvl_usd"], policy["min_tvl_usd"]),
        "execution_simplicity": 4.0 if item["adaptive_fee_enabled"] else 5.0,
    }
    scores = {name: max(0.0, min(5.0, value)) for name, value in scores.items()}
    weighted_score = sum(scores[name] * MCDA_WEIGHTS[name] for name in MCDA_WEIGHTS)
    options, range_rejection = _range_options(item, risk_profile)
    if range_rejection:
        return None, range_rejection

    denominator = productivity["7d"]
    candidate = {
        "observed_at": observed_at.strip(),
        "risk_profile": risk_profile,
        "pool_address": item["pool_address"],
        "trading_pair": f"{item['token_a']['symbol']}-{item['token_b']['symbol']}",
        "token_a": deepcopy(item["token_a"]),
        "token_b": deepcopy(item["token_b"]),
        "price_orientation": "token_b_per_token_a",
        "source_updated_at": item["source_updated_at"],
        "scanner_price": item["scanner_price"],
        "tvl_usd": item["tvl_usd"],
        **{
            f"volume_{window}_usd": item[f"volume_{window}_usd"]
            for window in LIVE_STATS
        },
        **{f"fees_{window}_usd": item[f"fees_{window}_usd"] for window in LIVE_STATS},
        "fee_tvl_24h": productivity["24h"],
        "fee_tvl_7d_daily": productivity["7d"],
        "fee_productivity_1h_dailyized_bps": productivity["1h"] * 10_000.0,
        "fee_productivity_4h_dailyized_bps": productivity["4h"] * 10_000.0,
        "fee_productivity_24h_bps_per_day": productivity["24h"] * 10_000.0,
        "fee_productivity_7d_daily_bps": productivity["7d"] * 10_000.0,
        "sustained_fee_productivity": item["sustained_fee_productivity"],
        "sustained_fee_productivity_bps_per_day": item["sustained_fee_productivity"]
        * 10_000.0,
        "fee_momentum_1h_x": productivity["1h"] / denominator if denominator else None,
        "fee_momentum_4h_x": productivity["4h"] / denominator if denominator else None,
        "volume_tvl_24h": item["volume_24h_usd"] / tvl,
        "volume_tvl_7d_daily": item["volume_7d_usd"] / 7.0 / tvl,
        "net_price_change_24h": item["net_price_change_24h"],
        "net_price_change_24h_pct": item["net_price_change_24h"] * 100.0,
        "profile_net_change_limit_pct": (
            maximum_change * 100.0 if maximum_change is not None else None
        ),
        "profile_net_change_limit_enabled": maximum_change is not None,
        "fee_rate_raw": item["fee_rate_raw"],
        "fee_rate_fraction": item["fee_rate_raw"] / 1_000_000.0,
        "fee_rate_bps": item["fee_rate_raw"] / 100.0,
        "adaptive_fee_enabled": item["adaptive_fee_enabled"],
        "fee_tier_index": item["fee_tier_index"],
        "tick_spacing": item["tick_spacing"],
        "has_warning": item["has_warning"],
        "criteria_raw": {
            "sustained_fee_productivity": item["sustained_fee_productivity"],
            "volume_24h_usd": item["volume_24h_usd"],
            "volume_7d_usd": item["volume_7d_usd"],
            "abs_net_price_change_24h": abs(item["net_price_change_24h"]),
            "tvl_usd": item["tvl_usd"],
            "adaptive_fee_enabled": item["adaptive_fee_enabled"],
        },
        "criteria_scores": scores,
        "mcda_weights": dict(MCDA_WEIGHTS),
        "weighted_score": weighted_score,
        "source_categories": list(item["source_categories"]),
        "source_lenses": list(item["source_lenses"]),
        "source_evidence": deepcopy(item["source_evidence"]),
        "total_amount_quote": budget,
        "gross_fee_estimate_quote_per_day": budget * item["sustained_fee_productivity"],
        "gross_fee_estimate_qualification": _GROSS_FEE_QUALIFICATION,
        "range_options": options,
    }
    return candidate, None


def _rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -candidate["weighted_score"],
        -candidate["sustained_fee_productivity"],
        -candidate["criteria_scores"]["recent_activity"],
        abs(candidate["net_price_change_24h"]),
        -candidate["tvl_usd"],
        candidate["pool_address"],
    )


def rank_pools(
    pools: Iterable[dict[str, Any]],
    risk_profile: str,
    observed_at: str,
    total_amount_quote: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Evaluate all deduplicated pools, rank eligible rows, and count rejections."""
    eligible: list[dict[str, Any]] = []
    rejections: Counter[str] = Counter()
    for pool in pools:
        candidate, rejection = evaluate_pool(
            pool, risk_profile, observed_at, total_amount_quote
        )
        if rejection:
            rejections[rejection] += 1
        else:
            eligible.append(candidate)  # type: ignore[arg-type]
    eligible.sort(key=_rank_key)
    ranked = [{"rank": rank, **candidate} for rank, candidate in enumerate(eligible, 1)]
    return ranked, dict(sorted(rejections.items()))


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(_FORBIDDEN_CANDIDATE_KEYS.intersection(value)) or any(
            _contains_forbidden_key(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return (
            math.isfinite(float(left))
            and math.isfinite(float(right))
            and math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def revalidate_candidate(
    candidate: Any,
    risk_profile: str,
    total_amount_quote: float,
    option_name: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Strictly rebuild a public candidate and return its exact chosen option."""
    if risk_profile not in PROFILE_POLICY:
        return None, None, "invalid_risk_profile"
    if not isinstance(candidate, dict):
        return None, None, "candidate_not_object"
    if _contains_forbidden_key(candidate):
        return None, None, "candidate_contains_forbidden_field"
    rank = candidate.get("rank")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank <= 0:
        return None, None, "invalid_candidate_rank"
    if candidate.get("risk_profile") != risk_profile:
        return None, None, "candidate_profile_mismatch"
    observed = _timestamp(candidate.get("observed_at"))
    if observed is None:
        return None, None, "invalid_candidate_observed_at"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return None, None, "invalid_current_time"
    age = (current.astimezone(timezone.utc) - observed).total_seconds()
    if age < -30.0:
        return None, None, "candidate_observed_at_in_future"
    if age > 600.0:
        return None, None, "candidate_stale"

    sources = candidate.get("source_evidence")
    if not isinstance(sources, list) or not sources:
        return None, None, "invalid_source_evidence"
    expected_source_keys = {"category", "discovery_lens", "record_index", "updated_at"}
    if any(
        not isinstance(source, dict)
        or source.keys() != expected_source_keys
        or source["category"] not in _BROAD_CATEGORIES
        or source["discovery_lens"] not in DISCOVERY_LENSES
        or not isinstance(source["record_index"], int)
        or isinstance(source["record_index"], bool)
        or source["record_index"] < 0
        or (
            source["updated_at"] is not None
            and not isinstance(source["updated_at"], str)
        )
        for source in sources
    ):
        return None, None, "invalid_source_evidence"
    sorted_sources = sorted(
        deepcopy(sources),
        key=lambda source: (
            source["category"],
            source["discovery_lens"],
            source["record_index"],
            source["updated_at"] or "",
        ),
    )
    if not _same(sources, sorted_sources):
        return None, None, "invalid_source_evidence_order"
    if (
        candidate.get("source_updated_at")
        != _preferred_source(sorted_sources)["updated_at"]
    ):
        return None, None, "source_updated_at_mismatch"

    token_a, token_b = candidate.get("token_a"), candidate.get("token_b")
    raw_fields = (
        "scanner_price",
        "tvl_usd",
        "net_price_change_24h",
        "fee_rate_raw",
        "adaptive_fee_enabled",
        "fee_tier_index",
        "tick_spacing",
        "has_warning",
        *(f"volume_{window}_usd" for window in LIVE_STATS),
        *(f"fees_{window}_usd" for window in LIVE_STATS),
    )
    if any(field not in candidate for field in raw_fields):
        return None, None, "candidate_missing_raw_evidence"
    pool = {
        "_normalized": True,
        "pool_address": candidate.get("pool_address"),
        "source_updated_at": candidate.get("source_updated_at"),
        "_updated_at_epoch": None,
        "token_a": deepcopy(token_a),
        "token_b": deepcopy(token_b),
        **{field: candidate[field] for field in raw_fields},
        "source_evidence": sorted_sources,
        "source_categories": sorted({source["category"] for source in sorted_sources}),
        "source_lenses": sorted(
            {source["discovery_lens"] for source in sorted_sources}
        ),
    }
    invariant_rejection = _invariant_rejection(pool)
    if invariant_rejection:
        return None, None, invariant_rejection
    try:
        expected, rejection = evaluate_pool(
            pool,
            risk_profile,
            candidate["observed_at"],
            total_amount_quote,
        )
    except (KeyError, TypeError, ValueError):
        return None, None, "candidate_raw_evidence_invalid"
    if rejection:
        return None, None, rejection
    expected = {"rank": rank, **expected}  # type: ignore[arg-type]
    if not _same(candidate, expected):
        return None, None, "candidate_contract_mismatch"
    if option_name is None:
        return expected, None, None
    if not isinstance(option_name, str):
        return None, None, "invalid_range_option"
    option = next(
        (
            option
            for option in expected["range_options"]
            if option["name"] == option_name
        ),
        None,
    )
    if option is None:
        return None, None, "range_option_not_available"
    return expected, deepcopy(option), None
