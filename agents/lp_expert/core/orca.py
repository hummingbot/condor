"""Pure Orca pool normalization, metrics, and neutral ranking."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WINDOWS = ("1h", "4h", "24h", "7d")
BASE_URL = "https://api.orca.so/v2/solana"
POOL_PATH = "/v2/solana/pools"
DISCOVERY_LENSES = (
    "yieldovertvl24h",
    "yieldovertvl7d",
    "volume24h",
    "volume7d",
)
MCDA_WEIGHTS = {
    "fee_productivity": 0.40,
    "recent_activity": 0.25,
    "price_stability": 0.15,
    "liquidity_depth": 0.10,
    "execution_simplicity": 0.10,
}


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.orca.so"
        or parsed.path != POOL_PATH
        or parsed.fragment
    ):
        raise ValueError("untrusted Orca API URL")


class _TrustedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _retry_delay(error: HTTPError) -> float:
    value = error.headers.get("Retry-After") if error.headers else None
    if value:
        try:
            return min(5.0, max(0.0, float(value)))
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return min(
                    5.0,
                    max(0.0, (target - datetime.now(timezone.utc)).total_seconds()),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.5


def fetch_json(url: str) -> dict[str, Any]:
    """Fetch one bounded Orca list page from the trusted endpoint."""
    _validate_url(url)
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Condor-lp-expert/2.0"},
    )
    opener = build_opener(_TrustedRedirects())
    for attempt in range(2):
        try:
            with opener.open(request, timeout=15) as response:
                _validate_url(response.geturl())
                value = json.loads(response.read().decode("utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("data"), list):
                raise ValueError("Orca response must contain a data list")
            return value
        except HTTPError as exc:
            if attempt == 0 and (exc.code == 429 or 500 <= exc.code <= 599):
                time.sleep(_retry_delay(exc))
                continue
            raise
        except (TimeoutError, URLError):
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise
    raise RuntimeError("unreachable Orca retry state")


def discovery_request(lens: str) -> str:
    if lens not in DISCOVERY_LENSES:
        raise ValueError("unsupported Orca discovery lens")
    query = urlencode(
        {
            "sortBy": lens,
            "sortDirection": "desc",
            "stats": ",".join(WINDOWS),
            "size": 100,
            "minTvl": 0,
        }
    )
    url = f"{BASE_URL}/pools?{query}"
    _validate_url(url)
    return url


def _first(value: Any, paths: tuple[str, ...]) -> Any:
    for path in paths:
        current = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            if current is not None:
                return current
    return None


def _aliases(value: dict[str, Any], paths: tuple[str, ...]) -> list[Any]:
    found = []
    for path in paths:
        item = _first(value, (path,))
        if item is not None:
            found.append(item)
    return found


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    result = _number(value)
    return int(result) if result is not None and result.is_integer() else None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def _one(
    record: dict[str, Any],
    paths: tuple[str, ...],
    parser,
    label: str,
) -> Any:
    parsed = [parser(item) for item in _aliases(record, paths)]
    if not parsed or any(item is None for item in parsed):
        raise ValueError(f"missing_or_invalid_{label}")
    distinct = {
        str(item).casefold() if isinstance(item, str) else item for item in parsed
    }
    if len(distinct) != 1:
        raise ValueError(f"contradictory_{label}")
    return parsed[0]


def _optional_one(
    record: dict[str, Any],
    paths: tuple[str, ...],
    parser,
    label: str,
) -> Any:
    aliases = _aliases(record, paths)
    if not aliases:
        return None
    parsed = [parser(item) for item in aliases]
    if any(item is None for item in parsed):
        raise ValueError(f"invalid_{label}")
    distinct = {
        str(item).casefold() if isinstance(item, str) else item for item in parsed
    }
    if len(distinct) != 1:
        raise ValueError(f"contradictory_{label}")
    return parsed[0]


def _token(record: dict[str, Any], side: str) -> dict[str, Any]:
    upper = side.upper()
    lower = side.lower()
    nested = record.get(f"token{upper}")
    mint_nested = record.get(f"tokenMint{upper}")
    wrapped = {
        "record": record,
        "token": {
            **(mint_nested if isinstance(mint_nested, dict) else {}),
            **(nested if isinstance(nested, dict) else {}),
        },
    }
    return {
        "symbol": _one(
            wrapped,
            (
                "token.symbol",
                f"record.token{upper}Symbol",
                f"record.token_{lower}_symbol",
            ),
            _text,
            f"token_{lower}_symbol",
        ),
        "mint": _one(
            wrapped,
            (
                "token.mint",
                "token.address",
                "token.pubkey",
                f"record.tokenMint{upper}",
                f"record.token_mint_{lower}",
                f"record.token{upper}Mint",
            ),
            _text,
            f"token_{lower}_mint",
        ),
        "decimals": _one(
            wrapped,
            (
                "token.decimals",
                f"record.token{upper}Decimals",
                f"record.token_{lower}_decimals",
            ),
            _integer,
            f"token_{lower}_decimals",
        ),
    }


def _window(record: dict[str, Any], window: str, field: str) -> float:
    title = field.capitalize()
    value = _one(
        record,
        (
            f"stats.{window}.{field}",
            f"stats.{window}.{field}Usd",
            f"stats.{window}.{field}Usdc",
            f"{field}{window}",
            f"{field}{window}Usd",
            f"{field}_{window}",
            f"{field}_{window}_usd",
        ),
        _number,
        f"{field}_{window}",
    )
    if value < 0:
        raise ValueError(f"negative_{field}_{window}")
    return value


def _price_change(record: dict[str, Any], window: str) -> float | None:
    return _optional_one(
        record,
        (
            f"stats.{window}.priceDelta",
            f"stats.{window}.price_delta",
            f"priceDelta{window}",
            f"price_delta_{window}",
        ),
        _number,
        f"price_change_{window}",
    )


def normalize_record(
    raw: Any,
    category: str,
    lens: str,
    source_index: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize one record before deduplication and apply technical gates only."""
    if not isinstance(raw, dict):
        return None, "record_not_object"
    try:
        address = _one(
            raw,
            ("address", "id", "pubkey", "poolAddress", "pool_address"),
            _text,
            "pool_address",
        )
        token_a = _token(raw, "a")
        token_b = _token(raw, "b")
        if (
            token_b["mint"] != USDC_MINT
            or token_b["symbol"].upper() != "USDC"
            or token_b["decimals"] != 6
        ):
            raise ValueError("noncanonical_usdc_quote")
        if (
            token_a["mint"] == USDC_MINT
            or token_a["symbol"].upper() == "USDC"
            or not 0 <= token_a["decimals"] <= 18
        ):
            raise ValueError("invalid_base_token")
        price = _one(
            raw,
            ("price", "currentPrice", "current_price", "tokenPrice"),
            _number,
            "price",
        )
        tvl = _one(
            raw,
            ("tvlUsdc", "tvlUsd", "tvlUSD", "tvl", "totalValueLockedUsd"),
            _number,
            "tvl",
        )
        tick_spacing = _one(
            raw, ("tickSpacing", "tick_spacing"), _integer, "tick_spacing"
        )
        fee_rate = _optional_one(raw, ("feeRate", "fee_rate"), _number, "fee_rate")
        fee_tier = _optional_one(
            raw,
            ("feeTierIndex", "fee_tier_index", "feeTier", "fee_tier"),
            _integer,
            "fee_tier",
        )
        adaptive = _optional_one(
            raw,
            ("adaptiveFeeEnabled", "adaptive_fee_enabled"),
            _boolean,
            "adaptive_fee_enabled",
        )
        warning = _optional_one(
            raw,
            ("hasWarning", "has_warning", "warning", "isWarning"),
            _boolean,
            "warning",
        )
        if price <= 0 or tvl <= 0 or tick_spacing <= 0:
            raise ValueError("nonpositive_pool_metric")
        if fee_rate is None and fee_tier is None:
            raise ValueError("missing_fee_metadata")
        if warning is not False:
            raise ValueError("orca_warning_or_unknown")
        if fee_rate is not None and fee_rate < 0:
            raise ValueError("negative_fee_rate")
        volumes = {window: _window(raw, window, "volume") for window in WINDOWS}
        fees = {window: _window(raw, window, "fees") for window in WINDOWS}
        price_change = {window: _price_change(raw, window) for window in WINDOWS}
        if price_change["24h"] is None:
            raise ValueError("missing_price_change_24h")
        updated = _optional_one(raw, ("updatedAt", "updated_at"), _text, "updated_at")
        return {
            "pool_address": address,
            "trading_pair": f"{token_a['symbol']}-USDC",
            "price_orientation": "token_b_per_token_a",
            "token_a": token_a,
            "token_b": token_b,
            "price": price,
            "tvl_usd": tvl,
            "tick_spacing": tick_spacing,
            "fee_rate_raw": fee_rate,
            "fee_tier_index": fee_tier,
            "adaptive_fee_enabled": adaptive,
            "has_warning": False,
            "volume_usd": volumes,
            "fees_usd": fees,
            "price_change": price_change,
            "source_categories": [category],
            "source_lenses": [lens],
            "source_count": 1,
            "_updated_at": updated,
            "_source_index": source_index,
        }, None
    except ValueError as exc:
        return None, str(exc)


def _timestamp(value: str | None) -> float:
    if not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    return parsed.timestamp()


def deduplicate(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Select one deterministic record per address and union source evidence."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["pool_address"], []).append(record)
    result: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    identity_fields = (
        "trading_pair",
        "price_orientation",
        "tick_spacing",
        "fee_rate_raw",
        "fee_tier_index",
    )
    for address, group in grouped.items():
        signatures = {
            (
                *(record[field] for field in identity_fields),
                record["token_a"]["symbol"],
                record["token_a"]["mint"],
                record["token_a"]["decimals"],
                record["token_b"]["symbol"],
                record["token_b"]["mint"],
                record["token_b"]["decimals"],
            )
            for record in group
        }
        if len(signatures) != 1:
            rejected["contradictory_duplicate_identity"] += len(group)
            continue
        chosen = max(
            group,
            key=lambda record: (
                _timestamp(record["_updated_at"]),
                -record["_source_index"],
            ),
        ).copy()
        chosen["source_categories"] = sorted(
            {item for record in group for item in record["source_categories"]}
        )
        chosen["source_lenses"] = sorted(
            {item for record in group for item in record["source_lenses"]}
        )
        chosen["source_count"] = len(group)
        chosen.pop("_updated_at", None)
        chosen.pop("_source_index", None)
        result.append(chosen)
    return sorted(result, key=lambda item: item["pool_address"]), rejected


def _percentiles(values: list[float], reverse: bool = False) -> list[float]:
    if len(values) == 1:
        return [1.0]
    ordered = sorted((value, index) for index, value in enumerate(values))
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor
        while end + 1 < len(ordered) and ordered[end + 1][0] == ordered[cursor][0]:
            end += 1
        rank = ((cursor + end) / 2) / (len(values) - 1)
        for _, index in ordered[cursor : end + 1]:
            result[index] = 1.0 - rank if reverse else rank
        cursor = end + 1
    return result


def _band(value: float, labels: tuple[str, str, str, str]) -> str:
    if value >= 0.75:
        return labels[3]
    if value >= 0.50:
        return labels[2]
    if value >= 0.25:
        return labels[1]
    return labels[0]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _round_tree(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, dict):
        return {key: _round_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_tree(item) for item in value]
    return value


def rank_pools(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive metrics and rank the complete valid universe deterministically."""
    if not records:
        return []
    rows = []
    for record in records:
        tvl = record["tvl_usd"]
        yield_by_window = {
            window: record["fees_usd"][window] / tvl for window in WINDOWS
        }
        hours = {"1h": 1, "4h": 4, "24h": 24, "7d": 168}
        hourly = {window: yield_by_window[window] / hours[window] for window in WINDOWS}
        baseline = hourly["24h"]
        acceleration = {}
        for window in ("1h", "4h"):
            acceleration[window] = {
                "difference_vs_24h": hourly[window] - baseline,
                "ratio_vs_24h": hourly[window] / baseline if baseline else None,
            }
        turnover = {window: record["volume_usd"][window] / tvl for window in WINDOWS}
        movements = [
            abs(value) for value in record["price_change"].values() if value is not None
        ]
        rows.append(
            {
                **record,
                "fee_yield": yield_by_window,
                "hourly_fee_yield": hourly,
                "sustainable_fee_yield_per_hour": min(
                    hourly["4h"], hourly["24h"], hourly["7d"]
                ),
                "fee_yield_acceleration": acceleration,
                "turnover": turnover,
                "_movement": _mean(movements),
            }
        )

    sustainable = _percentiles([row["sustainable_fee_yield_per_hour"] for row in rows])
    turn_1h = _percentiles([row["turnover"]["1h"] for row in rows])
    turn_4h = _percentiles([row["turnover"]["4h"] for row in rows])
    acceleration = _percentiles(
        [row["fee_yield_acceleration"]["1h"]["difference_vs_24h"] for row in rows]
    )
    stability = _percentiles([row["_movement"] for row in rows], reverse=True)
    liquidity = _percentiles([row["tvl_usd"] for row in rows])
    persistence = _percentiles(
        [
            row["sustainable_fee_yield_per_hour"]
            / max(row["hourly_fee_yield"]["1h"], 1e-30)
            for row in rows
        ]
    )
    predictability_and_spacing = [
        (0.75 if row["adaptive_fee_enabled"] is False else 0.45)
        + 0.25 / (1 + math.log10(max(1, row["tick_spacing"])))
        for row in rows
    ]
    spacing = _percentiles(predictability_and_spacing)
    simplicity = [
        _mean([spacing[index], persistence[index]]) for index in range(len(rows))
    ]
    coverage = _percentiles([row["source_count"] for row in rows])

    for index, row in enumerate(rows):
        components = {
            "fee_productivity": sustainable[index],
            "recent_activity": _mean(
                [turn_1h[index], turn_4h[index], acceleration[index]]
            ),
            "price_stability": stability[index],
            "liquidity_depth": liquidity[index],
            "execution_simplicity": simplicity[index],
        }
        score = sum(components[key] * MCDA_WEIGHTS[key] for key in MCDA_WEIGHTS)
        row["mcda"] = {
            "score": score,
            "weights": dict(MCDA_WEIGHTS),
            "components": components,
        }
        row["categories"] = {
            "asset_categories": row["source_categories"],
            "liquidity_band": _band(
                liquidity[index], ("thin", "developing", "deep", "very_deep")
            ),
            "volatility_band": _band(
                1.0 - stability[index], ("quiet", "moderate", "volatile", "extreme")
            ),
            "fee_regime": _band(
                sustainable[index], ("low", "ordinary", "strong", "exceptional")
            ),
            "fee_persistence": _band(
                persistence[index], ("spiky", "uneven", "persistent", "very_persistent")
            ),
            "execution_complexity": _band(
                1.0 - simplicity[index],
                ("simple", "moderate", "complex", "very_complex"),
            ),
            "evidence_confidence": _band(
                coverage[index], ("limited", "fair", "good", "broad")
            ),
            "recommendation_band": _band(
                score, ("watch", "consider", "strong", "leading")
            ),
        }

    ranked = sorted(rows, key=lambda row: (-row["mcda"]["score"], row["pool_address"]))
    for rank, row in enumerate(ranked, 1):
        row["neutral_rank"] = rank
    for row in ranked:
        row.pop("_movement", None)
    return _round_tree(ranked)


def _error_text(error: BaseException) -> str:
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, URLError):
        return f"URL error: {error.reason}"
    return f"{type(error).__name__}: {error}"


async def scan_pools(limit: int) -> dict[str, Any]:
    """Fetch the four bounded discovery lenses and return compact neutral evidence."""
    if isinstance(limit, bool) or not 1 <= limit <= 5:
        raise ValueError("candidate limit must be between one and five")
    specs = [(lens, discovery_request(lens)) for lens in DISCOVERY_LENSES]
    responses = await asyncio.gather(
        *(asyncio.to_thread(fetch_json, url) for _, url in specs),
        return_exceptions=True,
    )
    normalized: list[dict[str, Any]] = []
    rejections: Counter[str] = Counter()
    requests: list[dict[str, Any]] = []
    source_index = 0
    for (lens, _), response in zip(specs, responses, strict=True):
        if isinstance(response, BaseException):
            requests.append(
                {
                    "source": lens,
                    "status": "failed",
                    "records": 0,
                    "error": _error_text(response),
                }
            )
            continue
        records = response["data"]
        requests.append(
            {
                "source": lens,
                "status": "complete",
                "records": len(records),
                "error": None,
            }
        )
        for raw in records:
            source_index += 1
            record, reason = normalize_record(raw, "all", lens, source_index)
            if record is None:
                rejections[reason or "unknown_rejection"] += 1
            else:
                normalized.append(record)
    deduplicated, duplicate_rejections = deduplicate(normalized)
    rejections.update(duplicate_rejections)
    ranked = rank_pools(deduplicated)
    complete = all(row["status"] == "complete" for row in requests)
    return {
        "status": "complete" if complete else "incomplete",
        "deployable": complete and bool(ranked),
        "source_coverage": {
            "required_requests": len(specs),
            "completed_requests": sum(row["status"] == "complete" for row in requests),
            "requests": requests,
        },
        "universe": {
            "raw_records": sum(row["records"] for row in requests),
            "normalized_records": len(normalized),
            "valid_unique_pools": len(ranked),
            "returned_candidates": min(limit, len(ranked)),
        },
        "technical_rejections": dict(sorted(rejections.items())),
        "candidates": ranked[:limit],
    }


def _token_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = value.get("tokens", value.get("data"))
    else:
        rows = None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Gateway token registry response is invalid")
    return rows


def filter_registered_tokens(
    candidates: list[dict[str, Any]], registry_response: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep candidates whose exact base mint, symbol, and decimals are registered."""
    registry = _token_rows(registry_response)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        token = candidate["token_a"]
        matches = [
            row
            for row in registry
            if str(
                row.get("address") or row.get("token_address") or row.get("mint") or ""
            ).strip()
            == token["mint"]
        ]
        exact = [
            row
            for row in matches
            if str(row.get("symbol") or "").strip().casefold()
            == str(token["symbol"]).casefold()
            and not isinstance(row.get("decimals"), bool)
            and str(row.get("decimals")) == str(token["decimals"])
        ]
        if len(matches) == 1 and len(exact) == 1:
            accepted.append(candidate)
        else:
            rejected.append(
                {
                    "pool_address": candidate["pool_address"],
                    "base_mint": token["mint"],
                    "reason": (
                        "registered_token_identity_conflict"
                        if matches
                        else "base_token_not_registered"
                    ),
                }
            )
    return accepted, rejected


async def refresh_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Refetch the candidate's original bounded lens and validate exact identity."""
    lenses = candidate.get("source_lenses")
    if not isinstance(lenses, list) or not lenses:
        raise ValueError("candidate source lens is unavailable")
    lens = next((item for item in lenses if item in DISCOVERY_LENSES), None)
    if lens is None:
        raise ValueError("candidate source lens is unsupported")
    response = await asyncio.to_thread(fetch_json, discovery_request(lens))
    matches = []
    for index, raw in enumerate(response["data"], 1):
        normalized, _ = normalize_record(raw, "all", lens, index)
        if normalized and normalized["pool_address"] == candidate.get("pool_address"):
            matches.append(normalized)
    if len(matches) != 1:
        raise ValueError("selected Orca pool was not uniquely refreshed")
    refreshed = matches[0]
    expected = (
        candidate.get("pool_address"),
        candidate.get("trading_pair"),
        candidate.get("token_a", {}).get("mint"),
        candidate.get("token_a", {}).get("decimals"),
        candidate.get("token_b", {}).get("mint"),
        candidate.get("tick_spacing"),
    )
    observed = (
        refreshed["pool_address"],
        refreshed["trading_pair"],
        refreshed["token_a"]["mint"],
        refreshed["token_a"]["decimals"],
        refreshed["token_b"]["mint"],
        refreshed["tick_spacing"],
    )
    if observed != expected:
        raise ValueError("selected Orca pool identity changed during refresh")
    return refreshed
