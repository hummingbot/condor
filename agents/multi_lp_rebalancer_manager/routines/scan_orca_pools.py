"""Read-only Orca USDC pool ranking with deterministic trend and range evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import statistics
import time
from collections import Counter
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

CATEGORY = "Orca Multi LP Candidate Evidence"
SCHEMA = "multi_lp_rebalancer_manager.orca_scan.v3"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WINDOWS = ("1h", "4h", "24h", "7d")
DISCOVERY_LENSES = ("yieldovertvl24h", "yieldovertvl7d", "volume24h", "volume7d")
MCDA_FIELDS = (
    "fee_productivity",
    "recent_activity",
    "price_stability",
    "liquidity_depth",
    "execution_simplicity",
)
_PROFILE_NAMES = ("conservative", "balanced", "high_yield")
_BASE_URL = "https://api.orca.so/v2/solana/pools"
_POOL_PATH = "/v2/solana/pools"
_SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,31}$")
_MAX_RESPONSE_BYTES = 2_000_000
MAX_RESULT_CHARS = 1_899
MIN_TRANSPORT_CANDIDATES = 5
CANDIDATE_FIELDS = (
    "rank",
    "pool_address",
    "base_symbol",
    "base_mint",
    "base_decimals",
    "tvl_usd",
    "score",
    "price_stability",
    "liquidity_depth",
    "execution_simplicity",
    "trend",
    "trend_signal_id",
    "range_side",
    "position_width_pct",
    "downside_offset_pct",
    "rebalance_threshold_pct",
)


def _decimal(value: Any) -> Decimal:
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
    model_config = ConfigDict(extra="forbid", frozen=True)

    fee_productivity: Decimal = Field(ge=0, le=1)
    recent_activity: Decimal = Field(ge=0, le=1)
    price_stability: Decimal = Field(ge=0, le=1)
    liquidity_depth: Decimal = Field(ge=0, le=1)
    execution_simplicity: Decimal = Field(ge=0, le=1)

    @field_validator(*MCDA_FIELDS, mode="before")
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)

    @model_validator(mode="after")
    def exact_sum(self) -> "McdaWeights":
        if sum((getattr(self, key) for key in MCDA_FIELDS), Decimal(0)) != 1:
            raise ValueError("mcda_weights must sum exactly to 1")
        return self


class TrendThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    change_24h: Decimal = Field(gt=0)
    recent_history: Decimal = Field(gt=0)
    seven_day_history: Decimal = Field(gt=0)

    @field_validator("change_24h", "recent_history", "seven_day_history", mode="before")
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)


class RangeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    movement_multiplier: Decimal = Field(gt=0)
    min_width_pct: Decimal = Field(gt=0, lt=100)
    max_width_pct: Decimal = Field(gt=0, lt=100)

    @field_validator(
        "movement_multiplier", "min_width_pct", "max_width_pct", mode="before"
    )
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)

    @model_validator(mode="after")
    def ordered(self) -> "RangeProfile":
        if self.min_width_pct > self.max_width_pct:
            raise ValueError("range profile minimum width exceeds maximum")
        return self


class RangeProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conservative: RangeProfile
    balanced: RangeProfile
    high_yield: RangeProfile


class Config(BaseModel):
    """Fetch a bounded USDC universe and calculate comparable candidate facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    quote_token_mint: StrictStr = Field(min_length=32, max_length=44)
    min_pool_tvl_usd: Decimal = Field(gt=0)
    candidate_scan_limit: StrictInt = Field(ge=MIN_TRANSPORT_CANDIDATES, le=30)
    mcda_weights: McdaWeights
    risk_profile: Literal["conservative", "balanced", "high_yield"]
    trend_thresholds_pct: TrendThresholds
    trend_history_min_points: StrictInt = Field(ge=7, le=100)
    range_profiles: RangeProfiles
    downtrend_width_multiplier: Decimal = Field(gt=0)
    downside_offset_width_ratio: Decimal = Field(ge=0, le=1)
    rebalance_threshold_width_ratio: Decimal = Field(gt=0)
    minimum_rebalance_threshold_pct: Decimal = Field(gt=0, lt=100)
    maximum_rebalance_threshold_pct: Decimal = Field(gt=0, lt=100)
    excluded_base_mints: list[StrictStr] = Field(default_factory=list, max_length=100)
    excluded_pool_addresses: list[StrictStr] = Field(
        default_factory=list, max_length=100
    )
    required_pool_addresses: list[StrictStr] = Field(default_factory=list, max_length=3)
    request_size: StrictInt = Field(default=100, ge=3, le=100)
    timeout_seconds: Decimal = Field(default=Decimal("12"), ge=1, le=30)

    @field_validator(
        "min_pool_tvl_usd",
        "downtrend_width_multiplier",
        "downside_offset_width_ratio",
        "rebalance_threshold_width_ratio",
        "minimum_rebalance_threshold_pct",
        "maximum_rebalance_threshold_pct",
        "timeout_seconds",
        mode="before",
    )
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)

    @field_validator(
        "quote_token_mint",
        "excluded_base_mints",
        "excluded_pool_addresses",
        "required_pool_addresses",
    )
    @classmethod
    def solana_addresses(cls, value: Any) -> Any:
        values = value if isinstance(value, list) else [value]
        if any(
            not isinstance(item, str) or not _SOLANA_ADDRESS.fullmatch(item)
            for item in values
        ):
            raise ValueError(
                "token and pool identities must be Solana base58 addresses"
            )
        return value

    @model_validator(mode="after")
    def relationships(self) -> "Config":
        if self.quote_token_mint != USDC_MINT:
            raise ValueError("first-iteration quote_token_mint must be canonical USDC")
        if self.minimum_rebalance_threshold_pct > self.maximum_rebalance_threshold_pct:
            raise ValueError("rebalance threshold minimum exceeds maximum")
        for label, values in (
            ("excluded_base_mints", self.excluded_base_mints),
            ("excluded_pool_addresses", self.excluded_pool_addresses),
            ("required_pool_addresses", self.required_pool_addresses),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must not contain duplicates")
        if set(self.required_pool_addresses) & set(self.excluded_pool_addresses):
            raise ValueError("a required pool cannot also be excluded")
        return self


for model in (McdaWeights, TrendThresholds, RangeProfile):
    model.model_rebuild(_types_namespace={"Any": Any, "Decimal": Decimal})
RangeProfiles.model_rebuild(_types_namespace={"RangeProfile": RangeProfile})
Config.model_rebuild(
    _types_namespace={
        "Any": Any,
        "Decimal": Decimal,
        "Literal": Literal,
        "StrictInt": StrictInt,
        "StrictStr": StrictStr,
        "McdaWeights": McdaWeights,
        "TrendThresholds": TrendThresholds,
        "RangeProfiles": RangeProfiles,
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


def _request_url(
    config: Config, *, lens: str | None = None, addresses: list[str] | None = None
) -> str:
    query: dict[str, Any] = {
        "stats": ",".join(WINDOWS),
        "size": config.request_size,
        "minTvl": format(config.min_pool_tvl_usd, "f"),
    }
    if lens:
        query.update(
            {
                "sortBy": lens,
                "sortDirection": "desc",
                "token": config.quote_token_mint,
            }
        )
    if addresses:
        query["addresses"] = ",".join(addresses)
        query["size"] = len(addresses)
    url = f"{_BASE_URL}?{urlencode(query)}"
    _validate_url(url)
    return url


def _fetch_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    _validate_url(url)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Condor-Multi-LP-Manager/1",
        },
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


def _path(record: dict[str, Any], dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _number(
    value: Any, *, positive: bool = False, non_negative: bool = False
) -> Decimal:
    number = _decimal(value)
    if positive and number <= 0:
        raise ValueError("number must be positive")
    if non_negative and number < 0:
        raise ValueError("number must be non-negative")
    return number


def _token(raw: dict[str, Any], side: str) -> tuple[str, str, int]:
    token = raw.get(f"token{side}")
    if not isinstance(token, dict):
        raise ValueError(f"missing_token_{side.lower()}")
    symbol = token.get("symbol")
    address = token.get("address") or token.get("mint")
    decimals = token.get("decimals")
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise ValueError(f"invalid_token_{side.lower()}_symbol")
    if not isinstance(address, str) or not _SOLANA_ADDRESS.fullmatch(address):
        raise ValueError(f"invalid_token_{side.lower()}_mint")
    if isinstance(decimals, bool):
        raise ValueError(f"invalid_token_{side.lower()}_decimals")
    try:
        parsed_decimals = int(decimals)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_token_{side.lower()}_decimals") from exc
    if not 0 <= parsed_decimals <= 18 or Decimal(str(decimals)) != parsed_decimals:
        raise ValueError(f"invalid_token_{side.lower()}_decimals")
    return symbol, address, parsed_decimals


def _stat(
    raw: dict[str, Any], window: str, field: str, *, optional: bool = False
) -> Decimal | None:
    value = _path(raw, f"stats.{window}.{field}")
    if value is None and optional:
        return None
    try:
        return _number(value, non_negative=field in {"volume", "fees"})
    except ValueError:
        if optional:
            return None
        raise ValueError(f"invalid_{field}_{window}")


def _history(raw: dict[str, Any]) -> tuple[list[Decimal], str | None]:
    values = raw.get("priceHistory7d")
    if not isinstance(values, list):
        return [], "missing_price_history_7d"
    try:
        return [_number(value, positive=True) for value in values], None
    except ValueError:
        return [], "invalid_price_history_7d"


def _pct_return(first: Decimal, last: Decimal) -> Decimal:
    return (last / first - Decimal(1)) * Decimal(100)


def _trend_and_range(
    pool: str,
    price: Decimal,
    change_24h: Decimal | None,
    history: list[Decimal],
    history_error: str | None,
    config: Config,
    observed_at: Decimal,
) -> dict[str, Any]:
    threshold = config.trend_thresholds_pct
    valid = (
        change_24h is not None
        and history_error is None
        and len(history) >= config.trend_history_min_points
    )
    if valid:
        change_pct = change_24h * Decimal(100)
        recent_pct = _pct_return(history[-3], history[-1])
        seven_day_pct = _pct_return(history[0], history[-1])
        consecutive = [
            _pct_return(left, right) for left, right in zip(history, history[1:])
        ]
        median_move = Decimal(
            str(statistics.median(abs(value) for value in consecutive))
        )
        signals = (
            (change_pct, threshold.change_24h),
            (recent_pct, threshold.recent_history),
            (seven_day_pct, threshold.seven_day_history),
        )
        votes = [
            "UP" if value > band else "DOWN" if value < -band else "NEUTRAL"
            for value, band in signals
        ]
        trend = (
            "UP"
            if votes.count("UP") >= 2
            else "DOWN" if votes.count("DOWN") >= 2 else "SIDEWAYS"
        )
        movement_pct = max(abs(change_pct) / Decimal(2), median_move)
    else:
        change_pct = change_24h * Decimal(100) if change_24h is not None else None
        recent_pct = None
        seven_day_pct = None
        median_move = None
        votes = []
        trend = "UNKNOWN"
        movement_pct = None

    evidence = "|".join(
        [
            pool,
            format(observed_at, "f"),
            trend,
            str(change_pct),
            str(recent_pct),
            str(seven_day_pct),
            str(history[-1] if history else None),
        ]
    )
    signal_id = hashlib.sha256(evidence.encode()).hexdigest()[:32]
    result: dict[str, Any] = {
        "classification": trend,
        "observed_at": format(observed_at, "f"),
        "signal_id": signal_id,
        "history_points": len(history),
        "unknown_reason": (
            history_error
            or ("missing_price_change_24h" if change_24h is None else None)
            or (
                "insufficient_price_history_7d"
                if len(history) < config.trend_history_min_points
                else None
            )
        ),
        "change_24h_pct": _out(change_pct),
        "recent_history_pct": _out(recent_pct),
        "seven_day_history_pct": _out(seven_day_pct),
        "votes": votes,
        "median_abs_history_return_pct": _out(median_move),
    }
    if trend == "UNKNOWN" or movement_pct is None:
        result["range"] = None
        return result

    profile = getattr(config.range_profiles, config.risk_profile)
    base_width = min(
        max(movement_pct * profile.movement_multiplier, profile.min_width_pct),
        profile.max_width_pct,
    )
    width = (
        min(base_width * config.downtrend_width_multiplier, profile.max_width_pct)
        if trend == "DOWN"
        else base_width
    )
    threshold_pct = min(
        max(
            width * config.rebalance_threshold_width_ratio,
            config.minimum_rebalance_threshold_pct,
        ),
        config.maximum_rebalance_threshold_pct,
    )
    if trend == "DOWN":
        offset_pct = width * config.downside_offset_width_ratio
        upper = price * (Decimal(1) - offset_pct / Decimal(100))
        lower = upper * (Decimal(1) - width / Decimal(100))
        side = "BUY"
    else:
        offset_pct = Decimal(0)
        lower = price * (Decimal(1) - width / Decimal(200))
        upper = price * (Decimal(1) + width / Decimal(200))
        side = "RANGE"
    result["range"] = {
        "side": side,
        "movement_pct": _out(movement_pct),
        "position_width_pct": _out(width),
        "downside_offset_pct": _out(offset_pct),
        "rebalance_threshold_pct": _out(threshold_pct),
        "lower_price": _out(lower),
        "upper_price": _out(upper),
    }
    return result


def _normalize(
    raw: Any, source: str, config: Config, observed_at: Decimal
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("record_not_object")
    pool = raw.get("address")
    if not isinstance(pool, str) or not _SOLANA_ADDRESS.fullmatch(pool):
        raise ValueError("invalid_pool_address")
    token_a = _token(raw, "A")
    token_b = _token(raw, "B")
    # LPExecutor/Gateway map baseTokenAmount to pool token A, quoteTokenAmount to
    # token B, and consume pool price as B per A.  Until that interface carries
    # explicit mint orientation, only BASE(A)-USDC(B) pools are safe.
    if token_b[1] != config.quote_token_mint or token_a[1] == config.quote_token_mint:
        if (
            token_a[1] == config.quote_token_mint
            and token_b[1] != config.quote_token_mint
        ):
            raise ValueError("unsupported_usdc_token_a_orientation")
        raise ValueError("pool_does_not_have_one_usdc_quote")
    base, quote = token_a, token_b
    if quote != ("USDC", USDC_MINT, 6):
        raise ValueError("noncanonical_usdc_metadata")
    price = _number(raw.get("price"), positive=True)
    history, history_error = _history(raw)
    change_24h = _stat(raw, "24h", "priceDelta", optional=True)
    tvl = _number(raw.get("tvlUsdc"), positive=True)
    if tvl < config.min_pool_tvl_usd:
        raise ValueError("below_min_pool_tvl")
    tick_spacing = raw.get("tickSpacing")
    if isinstance(tick_spacing, bool):
        raise ValueError("invalid_tick_spacing")
    try:
        tick_spacing = int(tick_spacing)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_tick_spacing") from exc
    if tick_spacing <= 0:
        raise ValueError("invalid_tick_spacing")
    fee_rate = _number(raw.get("feeRate"), non_negative=True)
    adaptive = raw.get("adaptiveFeeEnabled")
    warning = raw.get("hasWarning")
    if not isinstance(adaptive, bool) or warning is not False:
        raise ValueError("unsafe_or_ambiguous_pool_flags")
    volumes = [_stat(raw, window, "volume") for window in WINDOWS]
    fees = [_stat(raw, window, "fees") for window in WINDOWS]
    return {
        "pool_address": pool,
        "base": base,
        "quote": quote,
        "quote_orientation": "tokenB",
        "current_price": price,
        "tvl_usd": tvl,
        "tick_spacing": tick_spacing,
        "fee_rate": fee_rate,
        "adaptive_fee_enabled": adaptive,
        "volumes": volumes,
        "fees": fees,
        "history": history,
        "change_24h": change_24h,
        "trend": _trend_and_range(
            pool,
            price,
            change_24h,
            history,
            history_error,
            config,
            observed_at,
        ),
        "sources": [source],
    }


def _deduplicate(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["pool_address"], []).append(row)
    accepted: list[dict[str, Any]] = []
    conflicts = 0
    for pool, group in groups.items():
        signatures = {
            (
                row["base"],
                row["quote"],
                row["quote_orientation"],
                row["tick_spacing"],
                row["fee_rate"],
                row["adaptive_fee_enabled"],
            )
            for row in group
        }
        if len(signatures) != 1:
            conflicts += len(group)
            continue
        chosen = group[0].copy()
        chosen["sources"] = sorted(
            {source for row in group for source in row["sources"]}
        )
        accepted.append(chosen)
    return accepted, conflicts


def _percentiles(values: list[Decimal], *, reverse: bool = False) -> list[Decimal]:
    if len(values) == 1:
        return [Decimal(1)]
    ordered = sorted((value, index) for index, value in enumerate(values))
    result = [Decimal(0)] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor
        while end + 1 < len(ordered) and ordered[end + 1][0] == ordered[cursor][0]:
            end += 1
        rank = Decimal(cursor + end) / Decimal(2 * (len(values) - 1))
        for _, index in ordered[cursor : end + 1]:
            result[index] = Decimal(1) - rank if reverse else rank
        cursor = end + 1
    return result


def _rank(rows: list[dict[str, Any]], weights: McdaWeights) -> list[dict[str, Any]]:
    if not rows:
        return []
    derived = []
    hours = (Decimal(1), Decimal(4), Decimal(24), Decimal(168))
    for row in rows:
        hourly_fee = [row["fees"][i] / row["tvl_usd"] / hours[i] for i in range(4)]
        turnover = [value / row["tvl_usd"] for value in row["volumes"]]
        movement = (
            row["trend"].get("range", {}).get("movement_pct")
            if row["trend"].get("range")
            else None
        )
        derived.append(
            {
                **row,
                "fee_productivity_raw": min(hourly_fee[1:]),
                "recent_activity_raw": (turnover[0] + turnover[1]) / Decimal(2)
                + (turnover[0] - turnover[2]),
                "movement_raw": (
                    _decimal(movement) if movement is not None else Decimal("Infinity")
                ),
                "execution_raw": (
                    Decimal("0.75")
                    if not row["adaptive_fee_enabled"]
                    else Decimal("0.45")
                )
                + Decimal("0.25")
                / Decimal(str(1 + math.log10(max(1, row["tick_spacing"])))),
            }
        )
    components = {
        "fee_productivity": _percentiles(
            [row["fee_productivity_raw"] for row in derived]
        ),
        "recent_activity": _percentiles(
            [row["recent_activity_raw"] for row in derived]
        ),
        "price_stability": _percentiles(
            [row["movement_raw"] for row in derived], reverse=True
        ),
        "liquidity_depth": _percentiles([row["tvl_usd"] for row in derived]),
        "execution_simplicity": _percentiles([row["execution_raw"] for row in derived]),
    }
    for index, row in enumerate(derived):
        row["mcda"] = {key: components[key][index] for key in MCDA_FIELDS}
        row["score"] = sum(
            row["mcda"][key] * getattr(weights, key) for key in MCDA_FIELDS
        )
    ranked = sorted(derived, key=lambda row: (-row["score"], row["pool_address"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    return ranked


def _out(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rounded = (
        value.quantize(Decimal("0.000000000001"))
        if abs(value) < Decimal("1e24")
        else value
    )
    text = format(rounded, "f").rstrip("0").rstrip(".")
    return text or "0"


def _transport_number(value: Any) -> str | None:
    """Bound one transported numeric value without changing internal calculations."""

    if value is None:
        return None
    return format(Decimal(str(value)), ".8g")


def _candidate_row(row: dict[str, Any]) -> list[Any]:
    trend = row["trend"]
    range_data = trend.get("range") or {}
    return [
        row["rank"],
        row["pool_address"],
        row["base"][0],
        row["base"][1],
        row["base"][2],
        _transport_number(row["tvl_usd"]),
        _transport_number(row["score"]),
        _transport_number(row["mcda"]["price_stability"]),
        _transport_number(row["mcda"]["liquidity_depth"]),
        _transport_number(row["mcda"]["execution_simplicity"]),
        trend["classification"],
        trend["signal_id"],
        range_data.get("side"),
        _transport_number(range_data.get("position_width_pct")),
        _transport_number(range_data.get("downside_offset_pct")),
        _transport_number(range_data.get("rebalance_threshold_pct")),
    ]


def _bounded_result(payload: dict[str, Any], rows: list[list[Any]]) -> str:
    payload["format"] = "compact_rows_v1"
    payload["candidates"] = []
    payload["returned"] = 0
    payload["omitted"] = payload["eligible_pools"]
    payload["transport_complete"] = not rows
    for row in rows:
        payload["candidates"].append(row)
        payload["returned"] = len(payload["candidates"])
        payload["omitted"] = max(0, payload["eligible_pools"] - payload["returned"])
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        if len(encoded) > MAX_RESULT_CHARS:
            payload["candidates"].pop()
            payload["returned"] = len(payload["candidates"])
            payload["omitted"] = max(0, payload["eligible_pools"] - payload["returned"])
            break
    payload["transport_complete"] = payload["returned"] == len(rows)
    required_floor = min(MIN_TRANSPORT_CANDIDATES, len(rows))
    if payload["returned"] < required_floor:
        return json.dumps(
            {
                "schema": SCHEMA,
                "status": "unavailable",
                "reason": "five_candidate_transport_floor_not_met",
                "eligible_pools": payload["eligible_pools"],
                "mutation": False,
            },
            separators=(",", ":"),
        )
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) <= MAX_RESULT_CHARS:
        return encoded
    return json.dumps(
        {
            "schema": SCHEMA,
            "status": "unavailable",
            "reason": "response_limit",
            "mutation": False,
        },
        separators=(",", ":"),
    )


async def run(config: Config, context: Any) -> str:
    """Fetch current official Orca facts and return rank/trend/range evidence."""

    del context
    observed_at = Decimal(str(round(time.time(), 3)))
    specs = [(lens, _request_url(config, lens=lens)) for lens in DISCOVERY_LENSES]
    if config.required_pool_addresses:
        specs.append(
            ("required", _request_url(config, addresses=config.required_pool_addresses))
        )
    responses = await asyncio.gather(
        *(
            asyncio.to_thread(_fetch_json, url, float(config.timeout_seconds))
            for _, url in specs
        ),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    failed_sources: list[str] = []
    raw_records = 0
    for (source, _), response in zip(specs, responses, strict=True):
        if isinstance(response, BaseException) or not isinstance(response, dict):
            failed_sources.append(source)
            continue
        records = response.get("data")
        if not isinstance(records, list):
            failed_sources.append(source)
            continue
        raw_records += len(records)
        for raw in records:
            try:
                row = _normalize(raw, source, config, observed_at)
                if row["pool_address"] in config.excluded_pool_addresses:
                    rejected["excluded_pool"] += 1
                elif row["base"][1] in config.excluded_base_mints:
                    rejected["excluded_base"] += 1
                else:
                    rows.append(row)
            except ValueError as exc:
                rejected[str(exc)] += 1
    unique, conflicts = _deduplicate(rows)
    ranked = _rank(unique, config.mcda_weights)
    completed = sum(source not in failed_sources for source in DISCOVERY_LENSES)
    status = (
        "complete"
        if completed == 4
        else "degraded" if completed >= 2 else "unavailable"
    )
    required = set(config.required_pool_addresses)
    selected = ranked[: config.candidate_scan_limit]
    selected_pools = {row["pool_address"] for row in selected}
    selected.extend(
        row for row in ranked if row["pool_address"] in required - selected_pools
    )
    missing_required = sorted(required - {row["pool_address"] for row in ranked})
    if missing_required and status != "unavailable":
        status = "degraded"
    if status == "unavailable":
        selected = []
    required_rows = sorted(
        (row for row in selected if row["pool_address"] in required),
        key=lambda row: row["rank"],
    )
    seen_bases = {row["base"][1] for row in required_rows}
    unique_base_rows = []
    duplicate_base_rows = []
    for row in sorted(
        (row for row in selected if row["pool_address"] not in required),
        key=lambda item: item["rank"],
    ):
        if row["base"][1] in seen_bases:
            duplicate_base_rows.append(row)
        else:
            seen_bases.add(row["base"][1])
            unique_base_rows.append(row)
    transport_order = [*required_rows, *unique_base_rows, *duplicate_base_rows]
    return _bounded_result(
        {
            "schema": SCHEMA,
            "status": status,
            "coverage": {
                "completed_lenses": completed,
                "failed_lens_count": len(failed_sources),
                "missing_required_count": len(missing_required),
            },
            "observed_at": format(observed_at, "f"),
            "risk_profile": config.risk_profile,
            "raw_records": raw_records,
            "eligible_pools": len(ranked),
            "rejected_records": sum(rejected.values()) + conflicts,
            "mutation": False,
        },
        [_candidate_row(row) for row in transport_order],
    )
