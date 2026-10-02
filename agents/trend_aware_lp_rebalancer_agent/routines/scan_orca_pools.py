"""Read-only Orca admission ranking and active-pool formation refresh."""

from __future__ import annotations

import asyncio
import json
import math
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
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

from condor.reports import ReportBuilder
from routines.base import RoutineResult

CATEGORY = "Orca LP Pool Evidence"
SCHEMA = "trend_aware_lp_rebalancer_agent.orca_scan.v2"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WINDOWS = ("1h", "4h", "24h", "7d")
DISCOVERY_LENSES = (
    "yieldovertvl24h",
    "yieldovertvl7d",
    "volume24h",
    "volume7d",
)
REQUEST_SIZE = 100
REFRESH_CHUNK_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 12.0
MAX_RESPONSE_BYTES = 2_000_000
SOURCE_MAX_AGE_SECONDS = 300
# Maximum UTF-8 size of the strict-JSON Agent-facing RoutineResult projection.
# This is a transport bound, not a pool- or position-count limit.
MAX_STRUCTURED_RESULT_BYTES = 1_000_000

ADMISSION_COLUMNS = [
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
REFRESH_COLUMNS = [
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

_BASE_URL = "https://api.orca.so/v2/solana/pools"
_POOL_PATH = "/v2/solana/pools"
_SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {character: index for index, character in enumerate(_BASE58_ALPHABET)}
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+]{0,31}$")
_SENSITIVE_KEY = re.compile(
    r"(?i)(api[_-]?key|authorization|credential|header|jwt|password|"
    r"private[_-]?key|rpc[_-]?url|secret|seed|wallet)"
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|credential|jwt|password|"
    r"private[_-]?key|rpc[_-]?url|secret|seed|"
    r"wallet(?:[_-]?(?:address|id))?)\b\s*[:=]\s*\S+"
)
_AUTHORIZATION_TEXT = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^\s,;]+"
)
_BEARER_TEXT = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_COOKIE_TEXT = re.compile(r"(?im)\b(set-cookie|cookie)\s*[:=]\s*[^\r\n]+")
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----.*?" r"-----END (?P=label)-----",
    re.IGNORECASE | re.DOTALL,
)
_URL_WITH_QUERY = re.compile(r"(?i)(https?://[^?\s]+)\?\S+")

_MCDA_WEIGHTS = {
    "fee_productivity": Decimal("0.40"),
    "recent_activity": Decimal("0.25"),
    "price_stability": Decimal("0.15"),
    "liquidity_depth": Decimal("0.10"),
    "execution_simplicity": Decimal("0.10"),
}
_PROFILE_POLICY = {
    "conservative": (Decimal("2.0"), Decimal("4"), Decimal("20")),
    "balanced": (Decimal("1.5"), Decimal("2"), Decimal("12")),
    "high_yield": (Decimal("1.0"), Decimal("1"), Decimal("8")),
}


class _StructuredResultError(ValueError):
    """The Agent-facing result cannot be represented as bounded strict JSON."""


class _StructuredResultOverflow(_StructuredResultError):
    """The Agent-facing strict-JSON result exceeds its byte bound."""


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError("value must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError("value must be finite")
    return result


def _is_solana_address(value: Any) -> bool:
    """Return whether value is the base58 encoding of one 32-byte public key."""

    if not isinstance(value, str) or not _SOLANA_ADDRESS.fullmatch(value):
        return False
    number = 0
    for character in value:
        number = number * 58 + _BASE58_INDEX[character]
    decoded_size = (number.bit_length() + 7) // 8
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return leading_zeroes + decoded_size == 32


class Config(BaseModel):
    """Return exact Orca admission or active-pool formation evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    risk_profile: Literal["conservative", "balanced", "high_yield"]
    min_pool_tvl_usd: Decimal = Field(gt=0)
    min_fee_productivity_bps_per_day: Decimal = Field(gt=0)
    candidate_scan_limit: StrictInt = Field(gt=0)
    excluded_base_mints: list[StrictStr] = Field(default_factory=list)
    excluded_pool_addresses: list[StrictStr] = Field(default_factory=list)
    refresh_pool_addresses: list[StrictStr] = Field(default_factory=list)

    @field_validator(
        "min_pool_tvl_usd", "min_fee_productivity_bps_per_day", mode="before"
    )
    @classmethod
    def finite_decimal(cls, value: Any) -> Decimal:
        return _decimal(value)

    @field_validator(
        "excluded_base_mints", "excluded_pool_addresses", "refresh_pool_addresses"
    )
    @classmethod
    def solana_addresses(cls, values: list[str]) -> list[str]:
        if any(not _is_solana_address(value) for value in values):
            raise ValueError("pool and mint identities must be Solana base58 addresses")
        return values

    @model_validator(mode="after")
    def relationships(self) -> "Config":
        for name in ("excluded_base_mints", "excluded_pool_addresses"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self


Config.model_rebuild(
    _types_namespace={
        "Any": Any,
        "Decimal": Decimal,
        "Literal": Literal,
        "StrictInt": StrictInt,
        "StrictStr": StrictStr,
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_error(value: Any, limit: int = 300) -> str:
    text = str(value if value is not None else "unknown")
    text = _PEM_PRIVATE_KEY.sub("private_key=[redacted]", text)
    text = _COOKIE_TEXT.sub(r"\1=[redacted]", text)
    text = " ".join(text.split())
    text = _URL_WITH_QUERY.sub(r"\1?[redacted]", text)
    text = _AUTHORIZATION_TEXT.sub("authorization=[redacted]", text)
    text = _BEARER_TEXT.sub("bearer=[redacted]", text)
    text = _SENSITIVE_TEXT.sub(r"\1=[redacted]", text)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _sanitize_report(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_report(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_report(item) for item in value]
    if isinstance(value, str):
        return _safe_error(value, max(300, len(value)))
    return value


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
    *, lens: str | None = None, addresses: list[str] | None = None, min_tvl: Decimal
) -> str:
    query: dict[str, Any] = {
        "stats": ",".join(WINDOWS),
        "size": REQUEST_SIZE,
    }
    if lens is not None:
        if lens not in DISCOVERY_LENSES:
            raise ValueError("unsupported discovery lens")
        query.update(
            {
                "sortBy": lens,
                "sortDirection": "desc",
                "token": USDC_MINT,
                "minTvl": format(min_tvl, "f"),
            }
        )
    elif addresses:
        query["addresses"] = ",".join(addresses)
        query["size"] = len(addresses)
    else:
        raise ValueError("an Orca request needs a discovery lens or pool addresses")
    url = f"{_BASE_URL}?{urlencode(query)}"
    _validate_url(url)
    return url


def _fetch_json(
    url: str, timeout_seconds: float = REQUEST_TIMEOUT_SECONDS
) -> dict[str, Any]:
    _validate_url(url)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Condor-Trend-Aware-LP-Agent/2",
        },
    )
    with build_opener(_TrustedRedirects()).open(
        request, timeout=timeout_seconds
    ) as response:
        _validate_url(response.geturl())
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
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


def _first(record: dict[str, Any], paths: tuple[str, ...]) -> list[Any]:
    values = []
    for path in paths:
        value = _path(record, path)
        if value is not None:
            values.append(value)
    return values


def _one(
    record: dict[str, Any],
    paths: tuple[str, ...],
    parser,
    label: str,
    *,
    optional: bool = False,
) -> Any:
    return _one_of_values(_first(record, paths), parser, label, optional=optional)


def _one_of_values(
    raw_values: list[Any], parser, label: str, *, optional: bool = False
) -> Any:
    if not raw_values:
        if optional:
            return None
        raise ValueError(f"missing_{label}")
    parsed = []
    for value in raw_values:
        try:
            parsed.append(parser(value))
        except Exception as exc:
            raise ValueError(f"invalid_{label}") from exc
    comparable = [
        value.casefold() if isinstance(value, str) else value for value in parsed
    ]
    if len(set(comparable)) != 1:
        raise ValueError(f"contradictory_{label}")
    return parsed[0]


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text required")
    return value.strip()


def _positive(value: Any) -> Decimal:
    result = _decimal(value)
    if result <= 0:
        raise ValueError("positive number required")
    return result


def _non_negative(value: Any) -> Decimal:
    result = _decimal(value)
    if result < 0:
        raise ValueError("non-negative number required")
    return result


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("integer required")
    result = _decimal(value)
    if result != result.to_integral_value():
        raise ValueError("integer required")
    return int(result)


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("boolean required")
    return value


def _token(raw: dict[str, Any], side: str) -> tuple[str, str, int]:
    upper = side.upper()
    nested = raw.get(f"token{upper}")
    mint_record = raw.get(f"tokenMint{upper}")
    records = [record for record in (nested, mint_record) if isinstance(record, dict)]
    symbol_values = [
        record["symbol"] for record in records if record.get("symbol") is not None
    ]
    if raw.get(f"token{upper}Symbol") is not None:
        symbol_values.append(raw[f"token{upper}Symbol"])
    symbol = _one_of_values(
        symbol_values,
        _text,
        f"token_{side}_symbol",
    )
    mint_values = [
        record[field]
        for record in records
        for field in ("mint", "address")
        if record.get(field) is not None
    ]
    if mint_record is not None and not isinstance(mint_record, dict):
        mint_values.append(mint_record)
    mint = _one_of_values(
        mint_values,
        _text,
        f"token_{side}_mint",
    )
    decimal_values = [
        record["decimals"] for record in records if record.get("decimals") is not None
    ]
    if raw.get(f"token{upper}Decimals") is not None:
        decimal_values.append(raw[f"token{upper}Decimals"])
    decimals = _one_of_values(
        decimal_values,
        _integer,
        f"token_{side}_decimals",
    )
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError(f"invalid_token_{side}_symbol")
    if not _is_solana_address(mint):
        raise ValueError(f"invalid_token_{side}_mint")
    if not 0 <= decimals <= 18:
        raise ValueError(f"invalid_token_{side}_decimals")
    return symbol, mint, decimals


def _stat(
    raw: dict[str, Any], window: str, field: str, *, optional: bool = False
) -> Decimal | None:
    value = _path(raw, f"stats.{window}.{field}")
    if value is None and optional:
        return None
    try:
        return _non_negative(value) if field in {"volume", "fees"} else _decimal(value)
    except ValueError:
        if optional:
            return None
        raise ValueError(f"invalid_{field}_{window}")


def _history(raw: dict[str, Any]) -> tuple[list[Decimal], str | None]:
    values = raw.get("priceHistory7d")
    if not isinstance(values, list):
        return [], "missing_price_history_7d"
    try:
        if all(not isinstance(item, dict) for item in values):
            return [_positive(item) for item in values], None
        if not all(isinstance(item, dict) for item in values):
            raise ValueError("mixed history shape")
        parsed = []
        for item in values:
            timestamp = _one(
                item,
                ("timestamp", "time", "updatedAt"),
                _parse_timestamp,
                "history_timestamp",
            )
            price = _one(item, ("price", "value"), _positive, "history_price")
            parsed.append((timestamp, price))
        if len({timestamp for timestamp, _ in parsed}) != len(parsed):
            raise ValueError("duplicate history timestamp")
        parsed.sort(key=lambda item: item[0])
        return [price for _, price in parsed], None
    except Exception:
        return [], "invalid_price_history_7d"


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, bool):
        raise ValueError("invalid timestamp")
    if isinstance(value, (int, float, Decimal)) or (
        isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value.strip())
    ):
        epoch = float(value)
        if epoch > 10_000_000_000:
            epoch /= 1_000
        return datetime.fromtimestamp(epoch, timezone.utc)
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _source_reported_at(raw: dict[str, Any]) -> tuple[datetime | None, str | None]:
    values = _first(
        raw,
        (
            "updatedAt",
            "updated_at",
            "sourceReportedAt",
            "source_reported_at",
        ),
    )
    if not values:
        return None, None
    try:
        parsed = [_parse_timestamp(value) for value in values]
    except Exception:
        return None, "invalid_source_reported_at"
    if len(set(parsed)) != 1:
        return None, "contradictory_source_reported_at"
    return parsed[0], None


def _pct_return(first: Decimal, last: Decimal) -> Decimal:
    return (last / first - Decimal(1)) * Decimal(100)


def _trend_vote(value: Decimal, threshold: Decimal) -> str:
    return "UP" if value > threshold else "DOWN" if value < -threshold else "NEUTRAL"


def _trend_and_formation(
    raw: dict[str, Any], risk_profile: str
) -> tuple[dict[str, Any], str | None]:
    change_24h = _stat(raw, "24h", "priceDelta", optional=True)
    history, history_error = _history(raw)
    change_pct = change_24h * Decimal(100) if change_24h is not None else None
    recent_pct = None
    seven_day_pct = None
    movement_pct = None
    median_step_pct = None
    if change_24h is not None and history_error is None and len(history) >= 7:
        # Orca currently supplies fourteen samples across priceHistory7d. Use
        # the last seven samples so this vote does not duplicate the 24h stat.
        recent_pct = _pct_return(history[-7], history[-1])
        seven_day_pct = _pct_return(history[0], history[-1])
        consecutive = [
            abs(_pct_return(left, right)) for left, right in zip(history, history[1:])
        ]
        median_step_pct = Decimal(str(statistics.median(consecutive)))
        movement_pct = max(abs(change_pct) / Decimal(2), median_step_pct)
        short_threshold = max(Decimal("1.5"), movement_pct * Decimal("1.5"))
        recent_threshold = max(Decimal("3"), movement_pct * Decimal("2.5"))
        seven_day_threshold = max(Decimal("5"), movement_pct * Decimal("3.5"))
        votes = (
            _trend_vote(change_pct, short_threshold),
            _trend_vote(recent_pct, recent_threshold),
            _trend_vote(seven_day_pct, seven_day_threshold),
        )
        short_vote = votes[0]
        broader_votes = votes[1:]
        opposite_vote = "DOWN" if short_vote == "UP" else "UP"
        market_trend = short_vote if (
            short_vote in {"UP", "DOWN"}
            and short_vote in broader_votes
            and opposite_vote not in broader_votes
        ) else "SIDEWAYS"
    else:
        votes = ()
        market_trend = "UNKNOWN"

    result = {
        "market_trend": market_trend,
        "change_24h_pct": change_pct,
        "recent_history_pct": recent_pct,
        "seven_day_history_pct": seven_day_pct,
        "median_step_pct": median_step_pct,
        "movement_pct": movement_pct,
        "votes": list(votes),
        "position_width_pct": None,
        "downside_offset_pct": None,
        "rebalance_threshold_pct": None,
    }
    reason = (
        history_error
        or ("missing_price_change_24h" if change_24h is None else None)
        or ("insufficient_price_history_7d" if len(history) < 7 else None)
    )
    if market_trend == "UNKNOWN" or movement_pct is None:
        return result, reason or "unknown_trend"

    multiplier, minimum_width, maximum_width = _PROFILE_POLICY[risk_profile]
    base_width = min(max(movement_pct * multiplier, minimum_width), maximum_width)
    width = (
        min(base_width * Decimal("1.25"), maximum_width)
        if market_trend == "DOWN"
        else base_width
    )
    downside_offset = width * Decimal("0.25") if market_trend == "DOWN" else Decimal(0)
    threshold = min(max(width * Decimal("0.15"), Decimal("0.15")), Decimal("5"))
    result.update(
        {
            "position_width_pct": width,
            "downside_offset_pct": downside_offset,
            "rebalance_threshold_pct": threshold,
        }
    )
    return result, None


def _source_age_error(
    source_reported_at: datetime | None, observed_at: datetime
) -> str | None:
    if source_reported_at is None:
        return None
    age = (observed_at - source_reported_at).total_seconds()
    if age < 0:
        return "source_reported_at_is_in_future"
    if age > SOURCE_MAX_AGE_SECONDS:
        return "source_reported_at_is_stale"
    return None


def _normalize_common(
    raw: Any,
    observed_at: datetime,
    risk_profile: str,
    staged_refresh: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("record_not_object")
    pool_address = _one(raw, ("address",), _text, "pool_address")
    if not _is_solana_address(pool_address):
        raise ValueError("invalid_pool_address")
    base = _token(raw, "A")
    if staged_refresh is not None:
        staged_refresh.update(
            base_symbol=base[0], base_mint=base[1], base_decimals=base[2]
        )
    quote = _token(raw, "B")
    if base[1] == USDC_MINT and quote[1] != USDC_MINT:
        raise ValueError("unsupported_usdc_token_a_orientation")
    if quote != ("USDC", USDC_MINT, 6) or base[1] == USDC_MINT:
        raise ValueError("pool_does_not_have_canonical_usdc_as_token_b")
    if base[0].casefold() == "usdc":
        raise ValueError("base_symbol_conflicts_with_quote_symbol")
    price = _one(raw, ("price",), _positive, "price")
    if staged_refresh is not None:
        staged_refresh["price_quote"] = _json_number(price)
    tvl = _one(raw, ("tvlUsdc", "tvlUsd"), _non_negative, "tvl_usd")
    if staged_refresh is not None:
        staged_refresh["tvl_usd"] = _json_number(tvl)
    has_warning = _one(raw, ("hasWarning",), _boolean, "pool_warning")
    if staged_refresh is not None:
        staged_refresh["pool_warnings"] = ["orca_has_warning"] if has_warning else []
    tick_spacing = _one(raw, ("tickSpacing",), _integer, "tick_spacing")
    if tick_spacing <= 0:
        raise ValueError("invalid_tick_spacing")
    adaptive_fee = _one(raw, ("adaptiveFeeEnabled",), _boolean, "adaptive_fee_enabled")
    source_reported_at, source_error = _source_reported_at(raw)
    if staged_refresh is not None and source_reported_at is not None:
        staged_refresh["source_reported_at"] = _iso(source_reported_at)
    formation, formation_error = _trend_and_formation(raw, risk_profile)
    if staged_refresh is not None:
        staged_refresh.update(
            change_24h_pct=_json_number(formation["change_24h_pct"]),
            recent_history_pct=_json_number(formation["recent_history_pct"]),
            seven_day_history_pct=_json_number(formation["seven_day_history_pct"]),
            movement_pct=_json_number(formation["movement_pct"]),
        )
    return {
        "pool_address": pool_address,
        "base_symbol": base[0],
        "base_mint": base[1],
        "base_decimals": base[2],
        "price_quote": price,
        "tvl_usd": tvl,
        "tick_spacing": tick_spacing,
        "adaptive_fee_enabled": adaptive_fee,
        "pool_warnings": ["orca_has_warning"] if has_warning else [],
        "observed_at": observed_at,
        "source_reported_at": source_reported_at,
        "source_error": source_error,
        "source_age_error": _source_age_error(source_reported_at, observed_at),
        "formation": formation,
        "formation_error": formation_error,
    }


def _normalize_admission(
    raw: Any, observed_at: datetime, config: Config
) -> dict[str, Any]:
    row = _normalize_common(raw, observed_at, config.risk_profile)
    if row["tvl_usd"] < config.min_pool_tvl_usd:
        raise ValueError("below_min_pool_tvl")
    if row["pool_address"] in config.excluded_pool_addresses:
        raise ValueError("excluded_pool")
    if row["base_mint"] in config.excluded_base_mints:
        raise ValueError("excluded_base")
    if row["pool_warnings"]:
        raise ValueError("pool_has_warning")
    if row["source_error"] or row["source_age_error"]:
        raise ValueError(row["source_error"] or row["source_age_error"])
    if row["formation_error"]:
        raise ValueError(row["formation_error"])

    volumes = [
        _stat(raw, window, "volume", optional=index == 3)
        for index, window in enumerate(WINDOWS)
    ]
    fees = [
        _stat(raw, window, "fees", optional=index == 0)
        for index, window in enumerate(WINDOWS)
    ]
    if any(value is None or value <= 0 for value in volumes[:3]):
        raise ValueError("non_positive_volume_observation")
    if any(value is None or value <= 0 for value in fees[1:]):
        raise ValueError("non_positive_fee_observation")
    tvl = row["tvl_usd"]
    fee_24h_bps = fees[2] / tvl * Decimal(10_000)
    fee_7d_bps = (fees[3] / Decimal(7)) / tvl * Decimal(10_000)
    floor = config.min_fee_productivity_bps_per_day
    if fee_24h_bps < floor:
        raise ValueError("below_24h_fee_productivity")
    if fee_7d_bps < floor:
        raise ValueError("below_7d_fee_productivity")

    hours = (Decimal(1), Decimal(4), Decimal(24), Decimal(168))
    fee_rate_h = [fees[index] / (tvl * hours[index]) for index in range(1, 4)]
    turnover_h = [volumes[index] / (tvl * hours[index]) for index in range(3)]
    recent_activity = (turnover_h[0] + turnover_h[1]) / Decimal(2) + (
        turnover_h[0] - turnover_h[2]
    )
    execution = (
        Decimal("0.75") if not row["adaptive_fee_enabled"] else Decimal("0.45")
    ) + Decimal("0.25") / Decimal(str(1 + math.log10(max(1, row["tick_spacing"]))))
    result = {
        **row,
        "volumes": volumes,
        "fees": fees,
        "fee_productivity_24h_bps_per_day": fee_24h_bps,
        "fee_productivity_7d_bps_per_day": fee_7d_bps,
        "fee_productivity_raw": min(fee_rate_h),
        "recent_activity_raw": recent_activity,
        "execution_simplicity_raw": execution,
    }
    # Reject a candidate before ranking if any measurement cannot survive the
    # documented JSON-number representation. Bounded MCDA components are safe.
    for value in (
        result["price_quote"],
        result["tvl_usd"],
        result["fee_productivity_24h_bps_per_day"],
        result["fee_productivity_7d_bps_per_day"],
        result["fee_productivity_raw"],
        result["recent_activity_raw"],
        result["execution_simplicity_raw"],
        result["formation"]["change_24h_pct"],
        result["formation"]["recent_history_pct"],
        result["formation"]["seven_day_history_pct"],
        result["formation"]["movement_pct"],
        result["formation"]["position_width_pct"],
        result["formation"]["downside_offset_pct"],
        result["formation"]["rebalance_threshold_pct"],
    ):
        _json_number(value)
    return result


def _identity_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["base_symbol"],
        row["base_mint"],
        row["base_decimals"],
        row["tick_spacing"],
        row["adaptive_fee_enabled"],
        tuple(row["pool_warnings"]),
    )


def _admission_evidence_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    formation = row["formation"]
    return (
        row["price_quote"],
        row["tvl_usd"],
        row["source_reported_at"],
        tuple(row["volumes"]),
        tuple(row["fees"]),
        row["fee_productivity_24h_bps_per_day"],
        row["fee_productivity_7d_bps_per_day"],
        row["fee_productivity_raw"],
        row["recent_activity_raw"],
        row["execution_simplicity_raw"],
        formation["market_trend"],
        formation["change_24h_pct"],
        formation["recent_history_pct"],
        formation["seven_day_history_pct"],
        formation["movement_pct"],
        formation["position_width_pct"],
        formation["downside_offset_pct"],
        formation["rebalance_threshold_pct"],
    )


def _deduplicate_admission(
    rows: list[tuple[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int, int]:
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for lens, row in rows:
        grouped.setdefault(row["pool_address"], []).append((lens, row))
    accepted = []
    identity_conflicts = 0
    evidence_conflicts = 0
    for pool_address in sorted(grouped):
        group = grouped[pool_address]
        if len({_identity_signature(row) for _, row in group}) != 1:
            identity_conflicts += 1
            continue
        if len({_admission_evidence_signature(row) for _, row in group}) != 1:
            evidence_conflicts += 1
            continue
        chosen = group[0][1].copy()
        chosen["source_lenses"] = sorted({lens for lens, _ in group})
        accepted.append(chosen)
    return accepted, identity_conflicts, evidence_conflicts


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
        percentile = Decimal(cursor + end) / Decimal(2 * (len(values) - 1))
        for _, index in ordered[cursor : end + 1]:
            result[index] = Decimal(1) - percentile if reverse else percentile
        cursor = end + 1
    return result


def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    components = {
        "fee_productivity": _percentiles([row["fee_productivity_raw"] for row in rows]),
        "recent_activity": _percentiles([row["recent_activity_raw"] for row in rows]),
        "price_stability": _percentiles(
            [row["formation"]["movement_pct"] for row in rows], reverse=True
        ),
        "liquidity_depth": _percentiles([row["tvl_usd"] for row in rows]),
        "execution_simplicity": _percentiles(
            [row["execution_simplicity_raw"] for row in rows]
        ),
    }
    ranked = []
    for index, row in enumerate(rows):
        item = row.copy()
        item["components"] = {
            name: values[index] for name, values in components.items()
        }
        item["mcda_score"] = sum(
            item["components"][name] * weight for name, weight in _MCDA_WEIGHTS.items()
        )
        ranked.append(item)
    ranked.sort(key=lambda row: (-row["mcda_score"], row["pool_address"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    return ranked


def _json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if not value.is_finite():
        raise ValueError("non_finite_json_number")
    text = format(value, ".8g")
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("json_number_overflow")
    if value != 0 and number == 0:
        raise ValueError("json_number_underflow")
    return int(number) if number.is_integer() and "e" not in text.lower() else number


def _admission_table_row(row: dict[str, Any]) -> dict[str, Any]:
    formation = row["formation"]
    components = row["components"]
    values = {
        "rank": row["rank"],
        "pool_address": row["pool_address"],
        "base_symbol": row["base_symbol"],
        "base_mint": row["base_mint"],
        "base_decimals": row["base_decimals"],
        "price_quote": _json_number(row["price_quote"]),
        "tvl_usd": _json_number(row["tvl_usd"]),
        "change_24h_pct": _json_number(formation["change_24h_pct"]),
        "recent_history_pct": _json_number(formation["recent_history_pct"]),
        "seven_day_history_pct": _json_number(formation["seven_day_history_pct"]),
        "fee_productivity_24h_bps_per_day": _json_number(
            row["fee_productivity_24h_bps_per_day"]
        ),
        "fee_productivity_7d_bps_per_day": _json_number(
            row["fee_productivity_7d_bps_per_day"]
        ),
        "fee_productivity_raw": _json_number(row["fee_productivity_raw"]),
        "recent_activity_raw": _json_number(row["recent_activity_raw"]),
        "movement_pct": _json_number(formation["movement_pct"]),
        "execution_simplicity_raw": _json_number(row["execution_simplicity_raw"]),
        "fee_productivity_component": _json_number(components["fee_productivity"]),
        "recent_activity_component": _json_number(components["recent_activity"]),
        "price_stability_component": _json_number(components["price_stability"]),
        "liquidity_depth_component": _json_number(components["liquidity_depth"]),
        "execution_simplicity_component": _json_number(
            components["execution_simplicity"]
        ),
        "mcda_score": _json_number(row["mcda_score"]),
        "observed_at": _iso(row["observed_at"]),
        "source_reported_at": (
            _iso(row["source_reported_at"]) if row["source_reported_at"] else None
        ),
        "market_trend": formation["market_trend"],
        "position_width_pct": _json_number(formation["position_width_pct"]),
        "downside_offset_pct": _json_number(formation["downside_offset_pct"]),
        "rebalance_threshold_pct": _json_number(formation["rebalance_threshold_pct"]),
    }
    return {column: values[column] for column in ADMISSION_COLUMNS}


def _blank_refresh_row(pool_address: str, observed_at: datetime) -> dict[str, Any]:
    values = {column: None for column in REFRESH_COLUMNS}
    values.update(
        {
            "pool_address": pool_address,
            "row_status": "source_error",
            "observed_at": _iso(observed_at),
            "pool_warnings": [],
            "formation_valid": False,
        }
    )
    return values


def _refresh_table_row(
    pool_address: str, raw: Any, observed_at: datetime, risk_profile: str
) -> dict[str, Any]:
    values = _blank_refresh_row(pool_address, observed_at)
    staged: dict[str, Any] = {}
    try:
        row = _normalize_common(raw, observed_at, risk_profile, staged_refresh=staged)
    except Exception as exc:
        values.update(staged)
        values.update(row_status="invalid_evidence", reason=_safe_error(exc))
        return {column: values[column] for column in REFRESH_COLUMNS}
    if row["pool_address"] != pool_address:
        values.update(row_status="source_error", reason="source_returned_wrong_pool")
        return {column: values[column] for column in REFRESH_COLUMNS}
    formation = row["formation"]
    reason = row["source_error"] or row["source_age_error"] or row["formation_error"]
    formation_valid = reason is None
    values.update(
        {
            "base_symbol": row["base_symbol"],
            "base_mint": row["base_mint"],
            "base_decimals": row["base_decimals"],
            "row_status": "ok" if formation_valid else "invalid_evidence",
            "reason": reason,
            "source_reported_at": (
                _iso(row["source_reported_at"]) if row["source_reported_at"] else None
            ),
            "price_quote": _json_number(row["price_quote"]),
            "tvl_usd": _json_number(row["tvl_usd"]),
            "change_24h_pct": _json_number(formation["change_24h_pct"]),
            "recent_history_pct": _json_number(formation["recent_history_pct"]),
            "seven_day_history_pct": _json_number(formation["seven_day_history_pct"]),
            "movement_pct": _json_number(formation["movement_pct"]),
            "pool_warnings": row["pool_warnings"],
            "formation_valid": formation_valid,
            "market_trend": formation["market_trend"] if formation_valid else None,
            "position_width_pct": (
                _json_number(formation["position_width_pct"])
                if formation_valid
                else None
            ),
            "downside_offset_pct": (
                _json_number(formation["downside_offset_pct"])
                if formation_valid
                else None
            ),
            "rebalance_threshold_pct": (
                _json_number(formation["rebalance_threshold_pct"])
                if formation_valid
                else None
            ),
        }
    )
    return {column: values[column] for column in REFRESH_COLUMNS}


async def _scan_admission_candidates(
    config: Config, observed_at: datetime
) -> dict[str, Any]:
    specs = [
        (
            lens,
            _request_url(lens=lens, min_tvl=config.min_pool_tvl_usd),
        )
        for lens in DISCOVERY_LENSES
    ]
    responses = await asyncio.gather(
        *(
            asyncio.to_thread(_fetch_json, url, REQUEST_TIMEOUT_SECONDS)
            for _, url in specs
        ),
        return_exceptions=True,
    )
    observed_at = _utc_now()
    completed_lenses = []
    failed_lenses = []
    normalized: list[tuple[str, dict[str, Any]]] = []
    failed_pool_addresses: set[str] = set()
    rejection_summary: Counter[str] = Counter()
    raw_records = 0
    discovered_addresses = set()
    for (lens, _), response in zip(specs, responses, strict=True):
        if isinstance(response, BaseException) or not isinstance(response, dict):
            failed_lenses.append(
                {"lens": lens, "reason": _safe_error(response or "invalid response")}
            )
            continue
        records = response.get("data")
        if not isinstance(records, list):
            failed_lenses.append({"lens": lens, "reason": "missing_data_list"})
            continue
        completed_lenses.append(lens)
        raw_records += len(records)
        for raw in records:
            if isinstance(raw, dict) and isinstance(raw.get("address"), str):
                discovered_addresses.add(raw["address"])
            try:
                normalized.append(
                    (lens, _normalize_admission(raw, observed_at, config))
                )
            except Exception as exc:
                rejection_summary[_safe_error(exc, 100)] += 1
                if isinstance(raw, dict) and _is_solana_address(raw.get("address")):
                    failed_pool_addresses.add(raw["address"])
    normalized = [
        (lens, row)
        for lens, row in normalized
        if row["pool_address"] not in failed_pool_addresses
    ]
    eligible, identity_conflicts, evidence_conflicts = _deduplicate_admission(
        normalized
    )
    if identity_conflicts:
        rejection_summary["contradictory_pool_identity"] += identity_conflicts
    if evidence_conflicts:
        rejection_summary["contradictory_pool_evidence"] += evidence_conflicts
    ranked = _rank(eligible)
    status = (
        "complete"
        if len(completed_lenses) == len(DISCOVERY_LENSES)
        else "degraded" if len(completed_lenses) >= 2 else "unavailable"
    )
    selected = ranked[: config.candidate_scan_limit] if status != "unavailable" else []
    table_data = [_admission_table_row(row) for row in selected]
    return {
        "schema": SCHEMA,
        "status": status,
        "scan_kind": "admission",
        "observed_at": _iso(observed_at),
        "mutation": False,
        "report_error": None,
        "risk_profile": config.risk_profile,
        "completed_lenses": completed_lenses,
        "failed_lenses": failed_lenses,
        "raw_record_count": raw_records,
        "discovered_count": len(discovered_addresses),
        "eligible_count": len(ranked),
        "returned_count": len(table_data),
        "omitted_count": max(0, len(ranked) - len(table_data)),
        "rejection_summary": dict(sorted(rejection_summary.items())),
        "warnings": [],
        "errors": [],
        "table_columns": ADMISSION_COLUMNS,
        "table_data": table_data,
    }


def _unique_in_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


async def _refresh_pools(config: Config, observed_at: datetime) -> dict[str, Any]:
    requested = _unique_in_order(config.refresh_pool_addresses)
    chunks = [
        requested[index : index + REFRESH_CHUNK_SIZE]
        for index in range(0, len(requested), REFRESH_CHUNK_SIZE)
    ]
    responses = await asyncio.gather(
        *(
            asyncio.to_thread(
                _fetch_json,
                _request_url(addresses=chunk, min_tvl=config.min_pool_tvl_usd),
                REQUEST_TIMEOUT_SECONDS,
            )
            for chunk in chunks
        ),
        return_exceptions=True,
    )
    observed_at = _utc_now()
    table_by_pool: dict[str, dict[str, Any]] = {}
    warnings = []
    errors = []
    for chunk, response in zip(chunks, responses, strict=True):
        if isinstance(response, BaseException) or not isinstance(response, dict):
            reason = _safe_error(response or "invalid response")
            errors.append({"pool_addresses": chunk, "reason": reason})
            for address in chunk:
                row = _blank_refresh_row(address, observed_at)
                row["reason"] = reason
                table_by_pool[address] = row
            continue
        records = response.get("data")
        if not isinstance(records, list):
            reason = "missing_data_list"
            errors.append({"pool_addresses": chunk, "reason": reason})
            for address in chunk:
                row = _blank_refresh_row(address, observed_at)
                row["reason"] = reason
                table_by_pool[address] = row
            continue
        grouped: dict[str, list[Any]] = {address: [] for address in chunk}
        for raw in records:
            address = raw.get("address") if isinstance(raw, dict) else None
            if address in grouped:
                grouped[address].append(raw)
            elif isinstance(address, str):
                warnings.append({"unexpected_pool_address": address})
        for address in chunk:
            matches = grouped[address]
            if not matches:
                row = _blank_refresh_row(address, observed_at)
                row.update(row_status="missing", reason="pool_not_returned")
            elif len(matches) > 1:
                row = _blank_refresh_row(address, observed_at)
                row.update(
                    row_status="source_error", reason="multiple_records_for_pool"
                )
            else:
                row = _refresh_table_row(
                    address, matches[0], observed_at, config.risk_profile
                )
            table_by_pool[address] = row

    table_data = [table_by_pool[address] for address in requested]
    counts = Counter(row["row_status"] for row in table_data)
    reliable = counts["ok"] + counts["invalid_evidence"]
    if reliable == 0:
        status = "unavailable"
    elif counts["missing"] or counts["source_error"]:
        status = "degraded"
    else:
        status = "complete"
    return {
        "schema": SCHEMA,
        "status": status,
        "scan_kind": "formation_refresh",
        "observed_at": _iso(observed_at),
        "mutation": False,
        "report_error": None,
        "risk_profile": config.risk_profile,
        "requested_count": len(requested),
        "returned_count": reliable,
        "valid_count": counts["ok"],
        "invalid_count": counts["invalid_evidence"],
        "missing_count": counts["missing"],
        "source_error_count": counts["source_error"],
        "invalid_pool_addresses": [
            row["pool_address"]
            for row in table_data
            if row["row_status"] == "invalid_evidence"
        ],
        "missing_pool_addresses": [
            row["pool_address"] for row in table_data if row["row_status"] == "missing"
        ],
        "source_error_pool_addresses": [
            row["pool_address"]
            for row in table_data
            if row["row_status"] == "source_error"
        ],
        "warnings": warnings,
        "errors": errors,
        "table_columns": REFRESH_COLUMNS,
        "table_data": table_data,
    }


def _report_input(config: Config) -> dict[str, Any]:
    return {
        "risk_profile": config.risk_profile,
        "min_pool_tvl_usd": _json_number(config.min_pool_tvl_usd),
        "min_fee_productivity_bps_per_day": _json_number(
            config.min_fee_productivity_bps_per_day
        ),
        "candidate_scan_limit": config.candidate_scan_limit,
        "excluded_base_mints": list(config.excluded_base_mints),
        "excluded_pool_addresses": list(config.excluded_pool_addresses),
        "refresh_pool_addresses": _unique_in_order(config.refresh_pool_addresses),
    }


def _key_value_rows(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "field": key,
            "value": (
                json.dumps(value, separators=(",", ":"), sort_keys=True)
                if isinstance(value, (dict, list))
                else value
            ),
        }
        for key, value in values.items()
    ]


def _result_summary(payload: dict[str, Any]) -> str:
    return (
        f"Orca {payload['scan_kind']} {payload['status']}: "
        f"{len(payload['table_data'])} row(s), mutation=false."
    )


def _structured_result_projection(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        key: value
        for key, value in payload.items()
        if key not in {"table_columns", "table_data"}
    }
    return {
        "text": _result_summary(payload),
        "table_columns": payload["table_columns"],
        "table_data": payload["table_data"],
        "sections": [{"type": "scanner_metadata", "data": metadata}],
    }


def _validate_structured_result(payload: dict[str, Any]) -> int:
    """Return strict-JSON byte size or raise before any partial result is exposed."""

    try:
        encoded = json.dumps(
            _structured_result_projection(payload),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise _StructuredResultError("structured_result_is_not_strict_json") from exc
    size = len(encoded)
    if size > MAX_STRUCTURED_RESULT_BYTES:
        raise _StructuredResultOverflow(
            f"structured_result_exceeds_{MAX_STRUCTURED_RESULT_BYTES}_byte_limit"
        )
    return size


async def _save_report(payload: dict[str, Any], config: Config) -> str:
    safe_payload = _sanitize_report(payload)
    safe_input = _sanitize_report(_report_input(config))
    title_suffix = (
        "Admission" if payload["scan_kind"] == "admission" else "Formation Refresh"
    )
    builder = ReportBuilder(f"Orca Pool Scanner — {title_suffix}")
    builder.source("routine", "scan_orca_pools").tags(
        ["orca", "liquidity-provision", "pool-evidence"]
    ).manual_order()
    builder.section("Routine result")
    builder.kpi("Status", safe_payload["status"])
    builder.kpi("Scan kind", safe_payload["scan_kind"])
    builder.kpi("Mutation", "false")
    builder.table(_key_value_rows(safe_input), columns=["field", "value"])
    metadata = {
        key: value
        for key, value in safe_payload.items()
        if key not in {"table_columns", "table_data"}
    }
    builder.section("Coverage and outcome")
    builder.table(_key_value_rows(metadata), columns=["field", "value"])
    builder.section("Returned rows")
    builder.table(safe_payload["table_data"], columns=safe_payload["table_columns"])
    builder.markdown(
        "This report is a review copy of the routine result. It does not select "
        "pools, infer Agent lifecycle state, or authorize a mutation."
    )
    return await builder.save()


def _routine_result(payload: dict[str, Any]) -> RoutineResult:
    projection = _structured_result_projection(payload)
    return RoutineResult(
        text=projection["text"],
        table_columns=projection["table_columns"],
        table_data=projection["table_data"],
        sections=projection["sections"],
    )


def _unavailable_payload(
    config: Config,
    observed_at: datetime,
    error: BaseException,
    *,
    omit_decision_rows: bool = False,
) -> dict[str, Any]:
    scan_kind = "formation_refresh" if config.refresh_pool_addresses else "admission"
    reason = _safe_error(f"{type(error).__name__}: {error}")
    common = {
        "schema": SCHEMA,
        "status": "unavailable",
        "scan_kind": scan_kind,
        "observed_at": _iso(observed_at),
        "mutation": False,
        "report_error": None,
        "risk_profile": config.risk_profile,
        "warnings": [],
        "errors": [reason],
    }
    if scan_kind == "admission":
        return {
            **common,
            "completed_lenses": [],
            "failed_lenses": [
                {"lens": lens, "reason": reason} for lens in DISCOVERY_LENSES
            ],
            "raw_record_count": 0,
            "discovered_count": 0,
            "eligible_count": 0,
            "returned_count": 0,
            "omitted_count": 0,
            "rejection_summary": {},
            "table_columns": ADMISSION_COLUMNS,
            "table_data": [],
        }

    requested = _unique_in_order(config.refresh_pool_addresses)
    rows = []
    if not omit_decision_rows:
        for address in requested:
            row = _blank_refresh_row(address, observed_at)
            row["reason"] = reason
            rows.append(row)
    source_errors = [row["pool_address"] for row in rows]
    return {
        **common,
        "requested_count": len(requested),
        "returned_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "missing_count": 0,
        "source_error_count": len(rows),
        "invalid_pool_addresses": [],
        "missing_pool_addresses": [],
        "source_error_pool_addresses": source_errors,
        "table_columns": REFRESH_COLUMNS,
        "table_data": rows,
    }


def _transport_unavailable_payload(
    config: Config, observed_at: datetime, error: BaseException
) -> dict[str, Any]:
    payload = _unavailable_payload(config, observed_at, error, omit_decision_rows=True)
    _validate_structured_result(payload)
    return payload


async def run(config: Config, context: Any) -> RoutineResult:
    """Fetch current Orca facts, render one review report, and never mutate."""

    del context
    observed_at = _utc_now()
    try:
        payload = (
            await _refresh_pools(config, observed_at)
            if config.refresh_pool_addresses
            else await _scan_admission_candidates(config, observed_at)
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        payload = _unavailable_payload(config, observed_at, exc)

    try:
        _validate_structured_result(payload)
    except _StructuredResultError as exc:
        payload = _transport_unavailable_payload(config, observed_at, exc)

    try:
        await _save_report(payload, config)
    except asyncio.CancelledError:
        payload["report_error"] = "report save was cancelled"
    except Exception as exc:
        payload["report_error"] = _safe_error(f"{type(exc).__name__}: {exc}")
    try:
        _validate_structured_result(payload)
    except _StructuredResultError as exc:
        payload = _transport_unavailable_payload(config, observed_at, exc)
    return _routine_result(payload)
