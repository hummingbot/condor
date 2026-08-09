"""Return a read-only, bounded Orca USDC-pool shortlist with neutral MCDA."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections import Counter
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from agents.lp_agent_lite.routines._reporting import DiagnosticTrace, report_result

CATEGORY = "Orca LP Candidate Evidence"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WINDOWS = ("1h", "4h", "24h", "7d")
DISCOVERY_LENSES = (
    "yieldovertvl24h",
    "yieldovertvl7d",
    "volume24h",
    "volume7d",
)
_WEIGHT_FIELDS = (
    "fee_productivity",
    "recent_activity",
    "price_stability",
    "liquidity_depth",
    "execution_simplicity",
)
_SCHEMA = "lp_agent_lite.orca_scan.v1"
_POOL_PATH = "/v2/solana/pools"
_BASE_URL = "https://api.orca.so/v2/solana/pools"
_TRANSPORT_TARGET_CHARS = 1_700
_TRANSPORT_MAX_CHARS = 1_900
_MAX_RESPONSE_BYTES = 2_000_000
_SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_SYMBOL = re.compile(r"^[A-Za-z0-9._+\-]{1,16}$")


def _finite_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise ValueError("value must be a decimal number") from exc
    if not number.is_finite():
        raise ValueError("value must be finite")
    return number


class McdaWeights(BaseModel):
    """Five neutral Orca ranking weights; values must sum exactly to one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fee_productivity: Decimal = Field(default=Decimal("0.40"), ge=0, le=1)
    recent_activity: Decimal = Field(default=Decimal("0.25"), ge=0, le=1)
    price_stability: Decimal = Field(default=Decimal("0.15"), ge=0, le=1)
    liquidity_depth: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)
    execution_simplicity: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)

    @field_validator(*_WEIGHT_FIELDS, mode="before")
    @classmethod
    def finite_weight(cls, value: Any) -> Decimal:
        return _finite_decimal(value)

    @model_validator(mode="after")
    def sum_to_one(self) -> "McdaWeights":
        if sum((getattr(self, name) for name in _WEIGHT_FIELDS), Decimal(0)) != 1:
            raise ValueError("MCDA weights must sum exactly to 1")
        return self


class Config(BaseModel):
    """Scan four official Orca discovery lenses and return bounded MCDA facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_pool_tvl_usd: Decimal = Field(gt=0)
    candidate_scan_limit: StrictInt = Field(default=4, ge=1, le=100)
    mcda_weights: McdaWeights = Field(default_factory=McdaWeights)
    request_size: StrictInt = Field(default=100, ge=1, le=100)
    timeout_seconds: Decimal = Field(default=Decimal("12"), ge=1, le=30)

    @field_validator("min_pool_tvl_usd", "timeout_seconds", mode="before")
    @classmethod
    def finite_number(cls, value: Any) -> Decimal:
        return _finite_decimal(value)

    @field_validator("mcda_weights", mode="before")
    @classmethod
    def exact_weight_keys(cls, value: Any) -> Any:
        if isinstance(value, dict) and set(value) != set(_WEIGHT_FIELDS):
            raise ValueError("mcda_weights must contain exactly the five MCDA keys")
        return value


# Agent-local routines are executed without normal package module registration.
McdaWeights.model_rebuild(_types_namespace={"Any": Any, "Decimal": Decimal})
Config.model_rebuild(
    _types_namespace={
        "Any": Any,
        "Decimal": Decimal,
        "McdaWeights": McdaWeights,
        "StrictInt": StrictInt,
    }
)


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.orca.so"
        or parsed.path != _POOL_PATH
        or parsed.fragment
    ):
        raise ValueError("untrusted Orca API URL")


class _TrustedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request_url(lens: str, config: Config) -> str:
    query = urlencode(
        {
            "sortBy": lens,
            "sortDirection": "desc",
            "stats": ",".join(WINDOWS),
            "size": config.request_size,
            "minTvl": format(config.min_pool_tvl_usd, "f"),
        }
    )
    url = f"{_BASE_URL}?{query}"
    _validate_url(url)
    return url


def _fetch_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    _validate_url(url)
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Condor-lp-agent-lite/1"},
    )
    with build_opener(_TrustedRedirects()).open(
        request, timeout=timeout_seconds
    ) as response:
        _validate_url(response.geturl())
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Orca response exceeds byte limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Orca response must contain a data list")
    return payload


def _first(record: dict[str, Any], paths: tuple[str, ...]) -> list[Any]:
    found: list[Any] = []
    for path in paths:
        value: Any = record
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                break
            value = value[part]
        else:
            if value is not None:
                found.append(value)
    return found


def _one(record, paths, parser, label, *, optional=False):
    raw = _first(record, paths)
    if not raw:
        if optional:
            return None
        raise ValueError(f"missing_{label}")
    parsed = [parser(value) for value in raw]
    if any(value is None for value in parsed):
        raise ValueError(f"invalid_{label}")
    comparable = [
        value.casefold() if isinstance(value, str) else value for value in parsed
    ]
    if len(set(comparable)) != 1:
        raise ValueError(f"contradictory_{label}")
    return parsed[0]


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
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return None


def _token(raw: dict[str, Any], side: str) -> tuple[str, str, int]:
    upper = side.upper()
    nested = raw.get(f"token{upper}")
    mint = raw.get(f"tokenMint{upper}")
    token = {
        **(mint if isinstance(mint, dict) else {}),
        **(nested if isinstance(nested, dict) else {}),
    }
    wrapped = {"raw": raw, "token": token}
    symbol = _one(
        wrapped,
        ("token.symbol", f"raw.token{upper}Symbol"),
        _text,
        f"token_{side}_symbol",
    )
    address = _one(
        wrapped,
        ("token.mint", "token.address", f"raw.tokenMint{upper}"),
        _text,
        f"token_{side}_mint",
    )
    decimals = _one(
        wrapped,
        ("token.decimals", f"raw.token{upper}Decimals"),
        _integer,
        f"token_{side}_decimals",
    )
    if not _SYMBOL.fullmatch(symbol) or not _SOLANA_ADDRESS.fullmatch(address):
        raise ValueError(f"invalid_token_{side}_identity")
    if not 0 <= decimals <= 18:
        raise ValueError(f"invalid_token_{side}_decimals")
    return symbol, address, decimals


def _window(raw: dict[str, Any], window: str, field: str, *, optional=False):
    value = _one(
        raw,
        (
            f"stats.{window}.{field}",
            f"stats.{window}.{field}Usd",
            f"{field}{window}",
            f"{field}{window}Usd",
        ),
        _number,
        f"{field}_{window}",
        optional=optional,
    )
    if value is not None and field != "priceDelta" and value < 0:
        raise ValueError(f"negative_{field}_{window}")
    return value


def _normalize(raw: Any, lens: str, index: int, minimum_tvl: float):
    if not isinstance(raw, dict):
        raise ValueError("record_not_object")
    address = _one(
        raw,
        ("address", "id", "pubkey", "poolAddress"),
        _text,
        "pool_address",
    )
    if not _SOLANA_ADDRESS.fullmatch(address):
        raise ValueError("invalid_pool_address")
    base = _token(raw, "a")
    quote = _token(raw, "b")
    if quote != ("USDC", USDC_MINT, 6) or base[1] == USDC_MINT:
        raise ValueError("noncanonical_usdc_quote")
    price = _one(raw, ("price", "currentPrice"), _number, "price")
    tvl = _one(raw, ("tvlUsdc", "tvlUsd", "tvl"), _number, "tvl")
    spacing = _one(raw, ("tickSpacing",), _integer, "tick_spacing")
    fee_rate = _one(raw, ("feeRate",), _number, "fee_rate", optional=True)
    fee_tier = _one(
        raw, ("feeTierIndex", "feeTier"), _integer, "fee_tier", optional=True
    )
    adaptive = _one(
        raw,
        ("adaptiveFeeEnabled",),
        _boolean,
        "adaptive_fee_enabled",
        optional=True,
    )
    warning = _one(raw, ("hasWarning",), _boolean, "warning")
    if price <= 0 or tvl < minimum_tvl or spacing <= 0:
        raise ValueError("invalid_pool_metric")
    if fee_rate is None and fee_tier is None:
        raise ValueError("missing_fee_metadata")
    if fee_rate is not None and fee_rate < 0:
        raise ValueError("negative_fee_rate")
    if warning is not False:
        raise ValueError("orca_warning")
    volumes = [_window(raw, window, "volume") for window in WINDOWS]
    fees = [_window(raw, window, "fees") for window in WINDOWS]
    changes = [_window(raw, window, "priceDelta", optional=True) for window in WINDOWS]
    if changes[2] is None:
        raise ValueError("missing_price_change_24h")
    return {
        "pool": address,
        "base": base,
        "price": price,
        "tvl": tvl,
        "spacing": spacing,
        "fee_rate": fee_rate,
        "fee_tier": fee_tier,
        "adaptive": adaptive,
        "volume": volumes,
        "fees": fees,
        "changes": changes,
        "lenses": [lens],
        "_index": index,
    }


def _deduplicate(records: list[dict[str, Any]]):
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["pool"], []).append(record)
    accepted: list[dict[str, Any]] = []
    conflicts = 0
    for group in grouped.values():
        signatures = {
            (
                row["base"],
                row["spacing"],
                row["fee_rate"],
                row["fee_tier"],
                row["adaptive"],
            )
            for row in group
        }
        if len(signatures) != 1:
            conflicts += len(group)
            continue
        chosen = min(group, key=lambda row: row["_index"]).copy()
        chosen["lenses"] = sorted({lens for row in group for lens in row["lenses"]})
        chosen.pop("_index", None)
        accepted.append(chosen)
    return sorted(accepted, key=lambda row: row["pool"]), conflicts


def _percentiles(values: list[float], *, reverse: bool = False) -> list[float]:
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
            result[index] = 1 - rank if reverse else rank
        cursor = end + 1
    return result


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _rank(records: list[dict[str, Any]], weights: McdaWeights):
    if not records:
        return []
    hours = (1, 4, 24, 168)
    derived = []
    for row in records:
        hourly = [row["fees"][i] / row["tvl"] / hours[i] for i in range(4)]
        turnover = [value / row["tvl"] for value in row["volume"]]
        movement = _mean([abs(value) for value in row["changes"] if value is not None])
        derived.append(
            {
                **row,
                "sustainable": min(hourly[1:]),
                "acceleration": hourly[0] - hourly[2],
                "turnover": turnover,
                "movement": movement,
                "persistence": min(hourly[1:]) / max(hourly[0], 1e-30),
            }
        )
    fee = _percentiles([row["sustainable"] for row in derived])
    turn_1h = _percentiles([row["turnover"][0] for row in derived])
    turn_4h = _percentiles([row["turnover"][1] for row in derived])
    acceleration = _percentiles([row["acceleration"] for row in derived])
    stability = _percentiles([row["movement"] for row in derived], reverse=True)
    liquidity = _percentiles([row["tvl"] for row in derived])
    persistence = _percentiles([row["persistence"] for row in derived])
    spacing = _percentiles(
        [
            (0.75 if row["adaptive"] is False else 0.45)
            + 0.25 / (1 + math.log10(max(1, row["spacing"])))
            for row in derived
        ]
    )
    weight_values = [float(getattr(weights, name)) for name in _WEIGHT_FIELDS]
    for index, row in enumerate(derived):
        components = [
            fee[index],
            _mean([turn_1h[index], turn_4h[index], acceleration[index]]),
            stability[index],
            liquidity[index],
            _mean([spacing[index], persistence[index]]),
        ]
        row["components"] = components
        row["score"] = sum(
            component * weight
            for component, weight in zip(components, weight_values, strict=True)
        )
    ranked = sorted(derived, key=lambda row: (-row["score"], row["pool"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    return ranked


def _num(value: float | None):
    if value is None:
        return None
    rounded = float(format(value, ".10g"))
    return int(rounded) if rounded.is_integer() else rounded


def _candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pool": row["pool"],
        "base": list(row["base"]),
        "price": _num(row["price"]),
        "tvl_usd": _num(row["tvl"]),
        "tick_spacing": row["spacing"],
        "fee_meta": [_num(row["fee_rate"]), row["fee_tier"], row["adaptive"]],
        "source_count": len(row["lenses"]),
        "rank": row["rank"],
        "score": round(row["score"], 6),
        "mcda": [round(value, 6) for value in row["components"]],
        "volume_usd": [_num(value) for value in row["volume"]],
        "fees_usd": [_num(value) for value in row["fees"]],
        "price_change": [_num(value) for value in row["changes"]],
    }


def _result(status, completed, failed, raw_count, rejected, ranked, limit):
    projected = [_candidate(row) for row in ranked]
    maximum = min(limit, len(projected)) if status != "unavailable" else 0
    while maximum >= 0:
        payload = {
            "schema": _SCHEMA,
            "status": status,
            "coverage": {
                "completed": completed,
                "required": 4,
                "failed_lenses": failed,
            },
            "windows": list(WINDOWS),
            "raw_records": raw_count,
            "eligible_pools": len(ranked),
            "rejected_records": rejected,
            "returned": maximum,
            "omitted": len(ranked) - maximum,
            "candidates": projected[:maximum],
            "mutation": False,
        }
        encoded = json.dumps(payload, separators=(",", ":"))
        if len(encoded) <= _TRANSPORT_TARGET_CHARS:
            return encoded
        maximum -= 1
    return json.dumps(
        {
            "schema": _SCHEMA,
            "status": "unavailable",
            "error": "transport_limit",
            "mutation": False,
        },
        separators=(",", ":"),
    )


async def run(config: Config, context: Any) -> str:
    """Fetch four bounded lenses and return facts, never a trade recommendation."""
    trace = DiagnosticTrace()
    trace.record(
        "scan_planned",
        lenses=list(DISCOVERY_LENSES),
        request_size=config.request_size,
    )

    async def finish(raw: str) -> str:
        return await report_result(
            raw,
            routine_name="scan_orca_pools",
            title="LP Agent Lite — Orca Pool Scan Diagnostic",
            config=config,
            trace=trace,
            context=context,
        )

    try:
        specs = [(lens, _request_url(lens, config)) for lens in DISCOVERY_LENSES]
        responses = await asyncio.gather(
            *(
                asyncio.to_thread(_fetch_json, url, float(config.timeout_seconds))
                for _, url in specs
            ),
            return_exceptions=True,
        )
        trace.record(
            "discovery_lenses_fetched",
            received=sum(not isinstance(row, BaseException) for row in responses),
            failed=sum(isinstance(row, BaseException) for row in responses),
        )
        records: list[dict[str, Any]] = []
        rejections: Counter[str] = Counter()
        failed: list[str] = []
        raw_count = 0
        index = 0
        minimum_tvl = float(config.min_pool_tvl_usd)
        for (lens, _), response in zip(specs, responses, strict=True):
            if isinstance(response, BaseException) or not isinstance(response, dict):
                failed.append(lens)
                continue
            raw_records = response.get("data")
            if not isinstance(raw_records, list):
                failed.append(lens)
                continue
            raw_count += len(raw_records)
            for raw in raw_records:
                index += 1
                try:
                    records.append(_normalize(raw, lens, index, minimum_tvl))
                except ValueError as exc:
                    rejections[str(exc)] += 1
        unique, conflicts = _deduplicate(records)
        rejected_count = sum(rejections.values()) + conflicts
        trace.record(
            "records_normalized",
            raw_records=raw_count,
            normalized_records=len(records),
            unique_pools=len(unique),
            rejected_records=rejected_count,
            rejection_reasons=dict(rejections),
            identity_conflicts=conflicts,
        )
        ranked = _rank(unique, config.mcda_weights)
        completed = 4 - len(failed)
        status = (
            "complete"
            if completed == 4
            else "degraded" if completed >= 2 else "unavailable"
        )
        trace.record(
            "mcda_ranked",
            status=status,
            eligible_pools=len(ranked),
            completed_lenses=completed,
        )
        return await finish(
            _result(
                status,
                completed,
                failed,
                raw_count,
                rejected_count,
                ranked,
                config.candidate_scan_limit,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        trace.record("scan", "error", error_type=type(exc).__name__)
        encoded = json.dumps(
            {
                "schema": _SCHEMA,
                "status": "unavailable",
                "error": "scan_error",
                "mutation": False,
            },
            separators=(",", ":"),
        )
        assert len(encoded) < _TRANSPORT_MAX_CHARS
        return await finish(encoded)
