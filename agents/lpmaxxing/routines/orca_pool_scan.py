import asyncio
import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, field_validator


CATEGORY = "Orca LP Agent"

DEFAULT_SCAN_SORT_FIELDS = ["tvl", "volume24h", "fees24h", "yieldovertvl24h"]
DEFAULT_ALLOWED_QUOTE_SYMBOLS = ["USDC", "SOL", "mSOL", "JitoSOL"]
DEFAULT_PREFERRED_QUOTE_SYMBOLS = ["USDC", "SOL"]
VALID_CATEGORIES = {"memecoin", "utility", "governance", "liquid_staking_token", "security", "stablecoin"}
VALID_RISK_PROFILES = {
    "default_cautious",
    "balanced_fee_capture",
    "risk_on_volatile",
    "meme_scout",
    "meme_tiny_live",
    "safe_conservative",
    "category_scout",
}
RISK_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "default_cautious": {},
    "category_scout": {
        "categories": ["utility", "governance", "liquid_staking_token", "security"],
        "min_tvl_query_usd": 50_000,
        "min_tvl_usd": 100_000,
        "min_volume_24h_usd": 50_000,
        "min_volume_7d_usd": 250_000,
        "max_abs_price_delta_24h": 0.25,
        "weight_liquidity_depth": 0.15,
        "weight_recent_activity": 0.30,
        "weight_fee_productivity": 0.30,
        "weight_range_stability": 0.10,
        "weight_execution_simplicity": 0.10,
        "weight_sponsor_fit": 0.05,
    },
    "safe_conservative": {
        "categories": ["stablecoin", "liquid_staking_token"],
        "min_tvl_query_usd": 1_000_000,
        "min_tvl_usd": 2_000_000,
        "min_volume_24h_usd": 500_000,
        "min_volume_7d_usd": 3_000_000,
        "max_abs_price_delta_24h": 0.08,
        "allowed_quote_symbols": ["USDC", "SOL"],
        "preferred_quote_symbols": ["USDC"],
        "weight_liquidity_depth": 0.35,
        "weight_recent_activity": 0.15,
        "weight_fee_productivity": 0.10,
        "weight_range_stability": 0.25,
        "weight_execution_simplicity": 0.10,
        "weight_sponsor_fit": 0.05,
        "allow_wide_preset": False,
    },
    "balanced_fee_capture": {
        "categories": ["utility", "governance", "liquid_staking_token"],
        "min_tvl_usd": 300_000,
        "min_volume_24h_usd": 200_000,
        "min_volume_7d_usd": 1_000_000,
        "max_abs_price_delta_24h": 0.18,
        "weight_liquidity_depth": 0.20,
        "weight_recent_activity": 0.25,
        "weight_fee_productivity": 0.30,
        "weight_range_stability": 0.10,
        "weight_execution_simplicity": 0.10,
        "weight_sponsor_fit": 0.05,
    },
    "risk_on_volatile": {
        "categories": ["utility", "governance", "memecoin"],
        "min_tvl_query_usd": 50_000,
        "min_tvl_usd": 200_000,
        "min_volume_24h_usd": 150_000,
        "min_volume_7d_usd": 500_000,
        "max_abs_price_delta_24h": 0.35,
        "weight_liquidity_depth": 0.15,
        "weight_recent_activity": 0.30,
        "weight_fee_productivity": 0.30,
        "weight_range_stability": 0.05,
        "weight_execution_simplicity": 0.10,
        "weight_sponsor_fit": 0.10,
        "wide_min_half_width": 0.08,
        "wide_max_half_width": 0.30,
    },
    "meme_scout": {
        "categories": ["memecoin"],
        "min_tvl_query_usd": 10_000,
        "min_tvl_usd": 25_000,
        "min_volume_24h_usd": 25_000,
        "min_volume_7d_usd": 50_000,
        "max_abs_price_delta_24h": 0.75,
        "weight_liquidity_depth": 0.10,
        "weight_recent_activity": 0.30,
        "weight_fee_productivity": 0.35,
        "weight_range_stability": 0.05,
        "weight_execution_simplicity": 0.10,
        "weight_sponsor_fit": 0.10,
        "wide_min_half_width": 0.10,
        "wide_max_half_width": 0.50,
        "limit_price_buffer_pct": 0.05,
    },
    "meme_tiny_live": {
        "categories": ["memecoin"],
        "min_tvl_query_usd": 10_000,
        "min_tvl_usd": 50_000,
        "min_volume_24h_usd": 50_000,
        "min_volume_7d_usd": 100_000,
        "max_abs_price_delta_24h": 0.50,
        "weight_liquidity_depth": 0.10,
        "weight_recent_activity": 0.30,
        "weight_fee_productivity": 0.35,
        "weight_range_stability": 0.05,
        "weight_execution_simplicity": 0.10,
        "weight_sponsor_fit": 0.10,
        "wide_min_half_width": 0.08,
        "wide_max_half_width": 0.35,
        "limit_price_buffer_pct": 0.03,
    },
}
BASELINE_PROFILE_FIELDS: dict[str, Any] = {
    "categories": [],
    "min_tvl_query_usd": 100_000,
    "min_tvl_usd": 500_000,
    "min_volume_24h_usd": 100_000,
    "min_volume_7d_usd": 500_000,
    "max_abs_price_delta_24h": 0.20,
    "allowed_quote_symbols": DEFAULT_ALLOWED_QUOTE_SYMBOLS,
    "preferred_quote_symbols": DEFAULT_PREFERRED_QUOTE_SYMBOLS,
    "weight_liquidity_depth": 0.25,
    "weight_recent_activity": 0.20,
    "weight_fee_productivity": 0.20,
    "weight_range_stability": 0.15,
    "weight_execution_simplicity": 0.10,
    "weight_sponsor_fit": 0.10,
    "wide_min_half_width": 0.05,
    "wide_max_half_width": 0.20,
    "limit_price_buffer_pct": 0.02,
    "allow_wide_preset": True,
}


def _list_or_default(value: Any, default: list[str]) -> list[str]:
    """Accept Condor UI nulls and comma-separated values for list fields."""
    if value is None:
        return list(default)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return list(default)
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return value


def _normalized_name(value: Any, default: str = "") -> str:
    text = str(value or default).strip().lower().replace("-", "_").replace(" ", "_")
    return text or default


def _normalized_categories(value: Any) -> list[str]:
    raw_items = _list_or_default(value, [])
    if not isinstance(raw_items, list):
        raw_items = [raw_items]
    categories: list[str] = []
    for item in raw_items:
        normalized = _normalized_name(item)
        if normalized:
            categories.append(normalized)
    return categories


class Config(BaseModel):
    """Scan public Orca Whirlpool pools and return one gated LP candidate."""

    execution_mode: str = Field(default="dry_run", description="dry_run, run_once, or loop")
    risk_profile: str = Field(default="default_cautious", description="Named gate/weight/range default profile")
    orca_api_base_url: str = Field(default="https://api.orca.so/v2/solana", description="Public Orca API base URL")
    pool_endpoint: str = Field(default="/pools", description="Pool list endpoint")
    request_timeout_seconds: int = Field(default=15, description="HTTP timeout in seconds")
    request_user_agent: str = Field(
        default="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 CondorOrcaLPAgent/1.0",
        description="Browser-style User-Agent",
    )
    page_size: int = Field(default=25, description="Records per ranked lens")
    page: int = Field(default=1, description="API page to request")
    stats_windows: str = Field(default="24h,7d", description="Orca stats windows")
    scan_sort_fields: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SCAN_SORT_FIELDS),
        description="Ranked scan lenses",
    )
    categories: list[str] = Field(default_factory=list, description="Orca API categories filters; empty means all pools")
    min_tvl_query_usd: float = Field(default=100000, description="API-side minimum TVL hint")
    include_pool_addresses: list[str] = Field(default_factory=list, description="Manual pool addresses to include")
    exclude_pool_addresses: list[str] = Field(default_factory=list, description="Pool addresses to reject")

    min_tvl_usd: float = Field(default=500000, description="Minimum pool TVL")
    min_volume_24h_usd: float = Field(default=100000, description="Minimum 24h volume")
    min_volume_7d_usd: float = Field(default=500000, description="Minimum 7d volume")
    max_abs_price_delta_24h: float = Field(default=0.20, description="Maximum absolute 24h price move")
    allowed_quote_symbols: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_QUOTE_SYMBOLS),
        description="Allowed quote symbols",
    )
    preferred_quote_symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_PREFERRED_QUOTE_SYMBOLS), description="Preferred quote symbols")
    reject_has_warning: bool = Field(default=True, description="Deprecated: Orca warning pools are always rejected")
    require_token_metadata: bool = Field(default=True, description="Require symbols, decimals, and mints")
    require_price_delta: bool = Field(default=False, description="Require price delta even in dry-run mode")
    require_price_delta_in_live: bool = Field(default=True, description="Require price delta for run_once/loop")
    require_gateway_pool_info: bool = Field(default=True, description="Agent must preflight selected pool through Gateway")

    weight_liquidity_depth: float = Field(default=0.25, description="MCDA weight")
    weight_recent_activity: float = Field(default=0.20, description="MCDA weight")
    weight_fee_productivity: float = Field(default=0.20, description="MCDA weight")
    weight_range_stability: float = Field(default=0.15, description="MCDA weight")
    weight_execution_simplicity: float = Field(default=0.10, description="MCDA weight")
    weight_sponsor_fit: float = Field(default=0.10, description="MCDA weight")

    conservative_min_half_width: float = Field(default=0.03, description="Conservative preset floor")
    conservative_max_half_width: float = Field(default=0.12, description="Conservative preset cap")
    balanced_min_half_width: float = Field(default=0.02, description="Balanced preset floor")
    balanced_max_half_width: float = Field(default=0.08, description="Balanced preset cap")
    wide_min_half_width: float = Field(default=0.05, description="Wide preset floor")
    wide_max_half_width: float = Field(default=0.20, description="Wide preset cap")
    limit_price_buffer_pct: float = Field(default=0.02, description="Buffer outside LP range for hard limit prices")
    default_observed_move: float = Field(default=0.02, description="Fallback volatility proxy for dry-run missing delta")
    allow_wide_preset: bool = Field(default=True, description="Allow wide preset within risk caps")

    total_amount_quote: float = Field(default=10, description="Configured quote budget")
    max_capital_allocation_quote: float = Field(default=10, description="Maximum capital allocation")
    top_n: int = Field(default=3, description="Number of top candidates to return")

    @field_validator("scan_sort_fields", mode="before")
    @classmethod
    def _coerce_scan_sort_fields(cls, value: Any) -> list[str]:
        return _list_or_default(value, DEFAULT_SCAN_SORT_FIELDS)

    @field_validator("risk_profile", mode="before")
    @classmethod
    def _coerce_risk_profile(cls, value: Any) -> str:
        return _normalized_name(value, "default_cautious")

    @field_validator("categories", mode="before")
    @classmethod
    def _coerce_categories(cls, value: Any) -> list[str]:
        return _normalized_categories(value)

    @field_validator("include_pool_addresses", "exclude_pool_addresses", mode="before")
    @classmethod
    def _coerce_optional_pool_lists(cls, value: Any) -> list[str]:
        return _list_or_default(value, [])

    @field_validator("allowed_quote_symbols", mode="before")
    @classmethod
    def _coerce_allowed_quote_symbols(cls, value: Any) -> list[str]:
        return _list_or_default(value, DEFAULT_ALLOWED_QUOTE_SYMBOLS)

    @field_validator("preferred_quote_symbols", mode="before")
    @classmethod
    def _coerce_preferred_quote_symbols(cls, value: Any) -> list[str]:
        return _list_or_default(value, DEFAULT_PREFERRED_QUOTE_SYMBOLS)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def _failure_payload(
    status: str,
    timestamp: str,
    warnings: list[str],
    rejection_summary: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "scan_status": status,
        "timestamp": timestamp,
        "config_summary": {},
        "selected_candidate": None,
        "top_candidates": [],
        "rejection_summary": rejection_summary or {},
        "warnings": warnings,
        "agent_prompt_summary": "No trade: Orca pool scan failed closed before candidate selection.",
    }


def _fail(status: str, timestamp: str, warnings: list[str], rejection_summary: dict[str, int] | None = None) -> str:
    """Backward-compatible compact JSON failure result."""
    return _compact(_failure_payload(status, timestamp, warnings, rejection_summary))


def _is_baseline_value(field: str, value: Any) -> bool:
    baseline = BASELINE_PROFILE_FIELDS.get(field)
    if isinstance(baseline, list):
        return list(value or []) == baseline
    return value == baseline


def _stats_window_items(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _stats_windows_with_required_gates(config: Config) -> str:
    windows = _stats_window_items(config.stats_windows)
    if config.min_volume_24h_usd > 0 and "24h" not in windows:
        windows.append("24h")
    if config.min_volume_7d_usd > 0 and "7d" not in windows:
        windows.append("7d")
    return ",".join(windows) if windows else config.stats_windows


def _apply_risk_profile(config: Config) -> Config:
    profile = _normalized_name(config.risk_profile, "default_cautious")
    overrides = RISK_PROFILE_DEFAULTS.get(profile, {})
    updates: dict[str, Any] = {"risk_profile": profile}
    for field, value in overrides.items():
        if field == "categories" and "categories" in config.model_fields_set:
            continue
        if _is_baseline_value(field, getattr(config, field)):
            updates[field] = list(value) if isinstance(value, list) else value
    config = config.model_copy(update=updates)
    return config.model_copy(update={"stats_windows": _stats_windows_with_required_gates(config)})


def _config_errors(config: Config) -> list[str]:
    errors: list[str] = []
    if config.risk_profile not in VALID_RISK_PROFILES:
        errors.append(f"invalid risk_profile '{config.risk_profile}'; valid values: {', '.join(sorted(VALID_RISK_PROFILES))}")
    invalid_categories = [category for category in config.categories if category not in VALID_CATEGORIES]
    if invalid_categories:
        errors.append(
            "invalid categories "
            f"{invalid_categories}; valid values: {', '.join(sorted(VALID_CATEGORIES))}"
        )
    return errors


def _category_lenses(config: Config) -> list[str | None]:
    return config.categories or [None]


def _url(config: Config, params: dict[str, Any]) -> str:
    base = config.orca_api_base_url.rstrip("/")
    endpoint = config.pool_endpoint if config.pool_endpoint.startswith("/") else f"/{config.pool_endpoint}"
    clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
    return f"{base}{endpoint}?{urlencode(clean_params)}"


def _fetch_json(url: str, user_agent: str, timeout: int) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("data"), list):
        raise ValueError("Orca response missing top-level data list")
    return parsed


def _request_specs(config: Config) -> list[tuple[str, str, str | None, str]]:
    specs: list[tuple[str, str, str | None, str]] = []
    for category in _category_lenses(config):
        for field in config.scan_sort_fields:
            params = {
                "sortBy": field,
                "sortDirection": "desc",
                "stats": config.stats_windows,
                "limit": config.page_size,
                "pageSize": config.page_size,
                "page": config.page,
                "minTvl": config.min_tvl_query_usd,
                "categories": category,
            }
            request_name = f"{category}:{field}" if category else field
            specs.append((request_name, field, category, _url(config, params)))
    if config.include_pool_addresses:
        params = {
            "addresses": ",".join(config.include_pool_addresses),
            "stats": config.stats_windows,
            "limit": max(config.page_size, len(config.include_pool_addresses)),
            "pageSize": max(config.page_size, len(config.include_pool_addresses)),
        }
        specs.append(("include", "include", None, _url(config, params)))
    return specs


async def _fetch_all(config: Config) -> tuple[list[dict[str, Any]] | None, list[str], dict[str, Any]]:
    specs = _request_specs(config)
    tasks = [asyncio.to_thread(_fetch_json, url, config.request_user_agent, config.request_timeout_seconds) for _, _, _, url in specs]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors: list[str] = []
    records_by_address: dict[str, dict[str, Any]] = {}
    diagnostics: dict[str, Any] = {
        "categories": config.categories,
        "lenses": sorted({lens for _, lens, _, _ in specs}),
        "requests": [{"name": name, "lens": lens, "category": category} for name, lens, category, _ in specs],
        "record_counts": {},
    }

    for (name, lens, category, _), result in zip(specs, results, strict=False):
        if isinstance(result, Exception):
            if isinstance(result, HTTPError):
                errors.append(f"{name}: HTTP {result.code}")
            elif isinstance(result, URLError):
                errors.append(f"{name}: URL error {result.reason}")
            else:
                errors.append(f"{name}: {type(result).__name__}: {result}")
            continue
        data = result["data"]
        diagnostics["record_counts"][name] = len(data)
        for record in data:
            address = _as_str(_first(record, ["address", "id", "pubkey", "poolAddress", "pool_address"]))
            dedupe_key = address or f"missing:{len(records_by_address)}:{name}"
            if dedupe_key not in records_by_address:
                records_by_address[dedupe_key] = record
            target = records_by_address[dedupe_key]
            target.setdefault("_scan_lenses", [])
            target.setdefault("_scan_categories", [])
            if lens not in target["_scan_lenses"]:
                target["_scan_lenses"].append(lens)
            if category and category not in target["_scan_categories"]:
                target["_scan_categories"].append(category)

    if errors:
        return None, errors, diagnostics
    return list(records_by_address.values()), [], diagnostics


def _first(obj: Any, paths: list[str]) -> Any:
    for path in paths:
        current = obj
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current is not None:
            return current
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return float(value)
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
        if not text:
            return None
        if text.endswith("%"):
            text = text[:-1]
            try:
                return float(text) / 100.0
            except ValueError:
                return None
        try:
            parsed = float(text)
        except ValueError:
            return None
        if math.isfinite(parsed):
            return parsed
    return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "warning", "warn"}
    return bool(value)


def _pct_decimal(value: Any) -> float | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    if abs(parsed) > 1:
        return parsed / 100.0
    return parsed


def _token(record: dict[str, Any], side: str) -> dict[str, Any]:
    upper = side.upper()
    lower = side.lower()
    token_obj = record.get(f"token{upper}") if isinstance(record.get(f"token{upper}"), dict) else {}
    mint_obj = record.get(f"tokenMint{upper}") if isinstance(record.get(f"tokenMint{upper}"), dict) else {}
    merged = {**mint_obj, **token_obj}
    return {
        "symbol": _as_str(
            _first(
                {"merged": merged, "record": record},
                [
                    "merged.symbol",
                    f"record.token{upper}.symbol",
                    f"record.token_{lower}.symbol",
                    f"record.token{upper}Symbol",
                    f"record.token_{lower}_symbol",
                ],
            )
        ),
        "mint": _as_str(
            _first(
                {"merged": merged, "record": record},
                [
                    "merged.mint",
                    "merged.address",
                    "merged.pubkey",
                    f"record.tokenMint{upper}",
                    f"record.token_mint_{lower}",
                    f"record.token{upper}.mint",
                    f"record.token{upper}.address",
                ],
            )
        ),
        "decimals": _to_int(
            _first(
                {"merged": merged, "record": record},
                [
                    "merged.decimals",
                    f"record.token{upper}.decimals",
                    f"record.token_{lower}.decimals",
                    f"record.token{upper}Decimals",
                ],
            )
        ),
    }


def _normalize(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {"malformed": True, "raw": record}
    stats = record.get("stats") if isinstance(record.get("stats"), dict) else {}
    wrapped = {"record": record, "stats": stats}
    candidate = {
        "malformed": False,
        "address": _as_str(_first(record, ["address", "id", "pubkey", "poolAddress", "pool_address"])),
        "token_a": _token(record, "a"),
        "token_b": _token(record, "b"),
        "price_raw": _to_float(_first(record, ["price", "currentPrice", "current_price", "sqrtPrice", "tokenPrice"])),
        "tvl_usd": _to_float(_first(record, ["tvlUsdc", "tvlUsd", "tvlUSD", "tvl", "totalValueLockedUsd", "total_value_locked_usd"])),
        "volume_24h_usd": _to_float(
            _first(
                wrapped,
                [
                    "stats.24h.volume",
                    "stats.24h.volumeUsd",
                    "stats.24h.volumeUsdc",
                    "record.volume24h",
                    "record.volume24hUsd",
                    "record.volume24hUsdc",
                    "record.volume_24h",
                    "record.volume_24h_usd",
                ],
            )
        ),
        "volume_7d_usd": _to_float(
            _first(
                wrapped,
                [
                    "stats.7d.volume",
                    "stats.7d.volumeUsd",
                    "stats.7d.volumeUsdc",
                    "record.volume7d",
                    "record.volume7dUsd",
                    "record.volume7dUsdc",
                    "record.volume_7d",
                    "record.volume_7d_usd",
                ],
            )
        ),
        "fees_24h_usd": _to_float(
            _first(
                wrapped,
                [
                    "stats.24h.fees",
                    "stats.24h.feesUsd",
                    "stats.24h.feesUsdc",
                    "record.fees24h",
                    "record.fees24hUsd",
                    "record.fees_24h",
                    "record.fees_24h_usd",
                ],
            )
        ),
        "fees_7d_usd": _to_float(
            _first(
                wrapped,
                [
                    "stats.7d.fees",
                    "stats.7d.feesUsd",
                    "stats.7d.feesUsdc",
                    "record.fees7d",
                    "record.fees7dUsd",
                    "record.fees_7d",
                    "record.fees_7d_usd",
                ],
            )
        ),
        "yield_24h": _pct_decimal(_first(wrapped, ["stats.24h.yieldOverTvl", "stats.24h.yield_over_tvl", "record.yieldOverTvl24h", "record.yieldovertvl24h", "record.yield_over_tvl_24h"])),
        "yield_7d": _pct_decimal(_first(wrapped, ["stats.7d.yieldOverTvl", "stats.7d.yield_over_tvl", "record.yieldOverTvl7d", "record.yieldovertvl7d", "record.yield_over_tvl_7d"])),
        "price_delta_24h": _pct_decimal(_first(wrapped, ["stats.24h.priceDelta", "stats.24h.price_delta", "record.priceDelta24h", "record.price_delta_24h", "record.priceChange24h"])),
        "fee_rate": _to_float(_first(record, ["feeRate", "fee_rate", "feeTier", "fee_tier"])),
        "tick_spacing": _to_int(_first(record, ["tickSpacing", "tick_spacing"])),
        "has_warning": _to_bool(_first(record, ["hasWarning", "has_warning", "warning", "isWarning"])),
        "adaptive_fee_enabled": _to_bool(_first(record, ["adaptiveFeeEnabled", "adaptive_fee_enabled"])),
        "source_lenses": record.get("_scan_lenses", []),
        "source_categories": record.get("_scan_categories", []),
    }
    return candidate


def _metadata_ok(candidate: dict[str, Any]) -> bool:
    for token_key in ("token_a", "token_b"):
        token = candidate[token_key]
        if not token.get("symbol") or not token.get("mint") or token.get("decimals") is None:
            return False
    return True


def _choose_pair(candidate: dict[str, Any], config: Config, warnings: list[str]) -> bool:
    a = candidate["token_a"].get("symbol")
    b = candidate["token_b"].get("symbol")
    if not a or not b:
        return False
    allowed = {symbol.upper() for symbol in config.allowed_quote_symbols}
    preferred = [symbol.upper() for symbol in config.preferred_quote_symbols]
    a_upper = a.upper()
    b_upper = b.upper()

    quote_side: str | None = None
    if a_upper in allowed and b_upper in allowed:
        if b_upper in preferred:
            quote_side = "b"
        elif a_upper in preferred:
            quote_side = "a"
        else:
            quote_side = "b"
    elif b_upper in allowed:
        quote_side = "b"
    elif a_upper in allowed:
        quote_side = "a"

    if quote_side is None:
        return False

    raw_price = candidate.get("price_raw")
    if quote_side == "b":
        base, quote, current_price = a, b, raw_price
        orientation = "tokenA-tokenB"
    else:
        base, quote = b, a
        current_price = 1.0 / raw_price if raw_price and raw_price > 0 else None
        orientation = "inverted-tokenB-tokenA"
        warnings.append("price inverted because tokenA was selected as quote; Gateway must confirm before live open")

    candidate["base_symbol"] = base
    candidate["quote_symbol"] = quote
    candidate["trading_pair"] = f"{base}-{quote}"
    candidate["current_price"] = current_price
    candidate["price_orientation"] = orientation
    return True


def _gate(candidate: dict[str, Any], config: Config) -> tuple[bool, str | None, list[str]]:
    warnings: list[str] = []
    if candidate.get("malformed"):
        return False, "malformed_record", warnings
    if not candidate.get("address"):
        return False, "missing_pool_address", warnings
    if config.require_token_metadata and not _metadata_ok(candidate):
        return False, "missing_token_metadata", warnings
    if candidate.get("has_warning"):
        return False, "has_warning", warnings
    if candidate.get("tvl_usd") is None or candidate["tvl_usd"] < config.min_tvl_usd:
        return False, "tvl_below_minimum", warnings
    if candidate.get("volume_24h_usd") is None or candidate["volume_24h_usd"] < config.min_volume_24h_usd:
        return False, "volume_24h_below_minimum", warnings
    if candidate.get("volume_7d_usd") is None or candidate["volume_7d_usd"] < config.min_volume_7d_usd:
        return False, "volume_7d_below_minimum", warnings
    if not _choose_pair(candidate, config, warnings):
        return False, "disallowed_quote_symbol", warnings

    live_mode = config.execution_mode in {"run_once", "loop", "live"}
    require_delta = config.require_price_delta or (live_mode and config.require_price_delta_in_live)
    if candidate.get("current_price") is None or candidate.get("current_price") <= 0:
        return False, "missing_price", warnings
    if require_delta and candidate.get("price_delta_24h") is None:
        return False, "missing_price_delta", warnings
    if candidate.get("price_delta_24h") is None:
        warnings.append("missing price_delta_24h; dry-run fallback volatility used")
    elif abs(candidate["price_delta_24h"]) > config.max_abs_price_delta_24h:
        return False, "price_delta_above_maximum", warnings
    if candidate["address"] in set(config.exclude_pool_addresses):
        return False, "explicitly_excluded", warnings
    if config.require_gateway_pool_info:
        warnings.append("Gateway pool_info not checked inside routine; agent preflight required before live open")
    return True, None, warnings


def _multiple_score(value: float | None, minimum: float) -> float:
    if value is None or minimum <= 0:
        return 0.0
    multiple = value / minimum
    points = [(1.0, 1.0), (2.0, 2.0), (5.0, 3.0), (10.0, 4.0), (20.0, 5.0)]
    if multiple < 1:
        return 0.0
    previous_m, previous_s = points[0]
    for m, s in points[1:]:
        if multiple <= m:
            span = m - previous_m
            return previous_s + ((multiple - previous_m) / span) * (s - previous_s)
        previous_m, previous_s = m, s
    return 5.0


def _fee_score(candidate: dict[str, Any], warnings: list[str]) -> float:
    tvl = candidate.get("tvl_usd") or 0
    ratios: list[float] = []
    if tvl > 0:
        if candidate.get("fees_24h_usd") is not None:
            ratios.append(candidate["fees_24h_usd"] / tvl)
        if candidate.get("fees_7d_usd") is not None:
            ratios.append((candidate["fees_7d_usd"] / 7.0) / tvl)
    for key in ("yield_24h", "yield_7d"):
        if candidate.get(key) is not None:
            value = candidate[key]
            ratios.append(value / 7.0 if key == "yield_7d" else value)
    if not ratios:
        if candidate.get("fees_24h_usd") is not None or candidate.get("fees_7d_usd") is not None:
            return 1.0
        return 0.0
    proxy = max(ratios)
    if proxy >= 0.002:
        warnings.append("high-yield-risk: fee productivity proxy is above 0.20% daily")
        return 5.0
    if proxy >= 0.001:
        return 4.0 + (proxy - 0.001) / 0.001
    if proxy >= 0.0005:
        return 3.0 + (proxy - 0.0005) / 0.0005
    if proxy >= 0.0002:
        return 1.0 + ((proxy - 0.0002) / 0.0003) * 2.0
    if proxy > 0:
        return proxy / 0.0002
    return 0.0


def _range_stability_score(price_delta: float | None) -> float:
    if price_delta is None:
        return 2.0
    move = abs(price_delta)
    if move <= 0.02:
        return 5.0
    if move <= 0.05:
        return 4.0
    if move <= 0.10:
        return 3.0
    if move <= 0.15:
        return 2.0
    if move <= 0.20:
        return 1.0
    return 0.0


def _execution_simplicity_score(candidate: dict[str, Any], config: Config) -> float:
    score = 5.0
    symbols = {candidate["base_symbol"].upper(), candidate["quote_symbol"].upper()}
    if not symbols.intersection({"USDC", "SOL"}):
        score -= 2.0
    if candidate["quote_symbol"].upper() not in {symbol.upper() for symbol in config.preferred_quote_symbols}:
        score -= 1.0
    tick_spacing = candidate.get("tick_spacing")
    if tick_spacing is None or tick_spacing <= 0 or tick_spacing > 1024:
        score -= 1.0
    if candidate.get("adaptive_fee_enabled"):
        score -= 0.5
    for symbol in symbols:
        if "UNKNOWN" in symbol or len(symbol) > 16:
            score -= 1.0
            break
    if config.execution_mode in {"run_once", "loop", "live"} and config.require_gateway_pool_info:
        score -= 1.0
    return max(0.0, min(5.0, score))


def _sponsor_fit_score(candidate: dict[str, Any], config: Config) -> float:
    score = 2.0
    if (candidate.get("tvl_usd") or 0) >= config.min_tvl_usd * 5 and (candidate.get("volume_24h_usd") or 0) >= config.min_volume_24h_usd * 5:
        score += 1.0
    if candidate.get("price_delta_24h") is None or abs(candidate["price_delta_24h"]) <= config.max_abs_price_delta_24h * 0.75:
        score += 1.0
    if candidate["quote_symbol"].upper() in {"USDC", "SOL"} or candidate["base_symbol"].upper() in {"USDC", "SOL"}:
        score += 1.0
    return max(0.0, min(5.0, score))


def _weights(config: Config) -> tuple[dict[str, float], list[str]]:
    weights = {
        "liquidity_depth": config.weight_liquidity_depth,
        "recent_activity": config.weight_recent_activity,
        "fee_productivity": config.weight_fee_productivity,
        "range_stability": config.weight_range_stability,
        "execution_simplicity": config.weight_execution_simplicity,
        "sponsor_fit": config.weight_sponsor_fit,
    }
    total = sum(weights.values())
    warnings: list[str] = []
    if total <= 0:
        return weights, ["invalid scoring weights: sum must be positive"]
    if abs(total - 1.0) > 0.000001:
        warnings.append(f"scoring weights sum to {total:.6f}; normalized for this scan")
        weights = {key: value / total for key, value in weights.items()}
    return weights, warnings


def _score(candidate: dict[str, Any], config: Config) -> tuple[dict[str, float], float, list[str]]:
    warnings: list[str] = []
    liquidity_depth = _multiple_score(candidate.get("tvl_usd"), config.min_tvl_usd)
    volume_7d_daily = (candidate.get("volume_7d_usd") or 0.0) / 7.0
    recent_activity = 0.6 * _multiple_score(candidate.get("volume_24h_usd"), config.min_volume_24h_usd) + 0.4 * _multiple_score(volume_7d_daily, config.min_volume_24h_usd)
    fee_productivity = _fee_score(candidate, warnings)
    range_stability = _range_stability_score(candidate.get("price_delta_24h"))
    execution_simplicity = _execution_simplicity_score(candidate, config)
    sponsor_fit = _sponsor_fit_score(candidate, config)
    criteria = {
        "liquidity_depth": round(liquidity_depth, 4),
        "recent_activity": round(recent_activity, 4),
        "fee_productivity": round(fee_productivity, 4),
        "range_stability": round(range_stability, 4),
        "execution_simplicity": round(execution_simplicity, 4),
        "sponsor_fit": round(sponsor_fit, 4),
    }
    weights, weight_warnings = _weights(config)
    warnings.extend(weight_warnings)
    weighted_score = sum(criteria[key] * weights[key] for key in criteria)
    return criteria, round(weighted_score, 4), warnings


def _preset(candidate: dict[str, Any], config: Config) -> str:
    score = candidate["weighted_score"]
    if score < 3.0:
        return "no-trade"
    if score < 3.5:
        return "conservative"
    if score < 4.25:
        return "balanced" if (candidate.get("price_delta_24h") or 0) <= 0.08 else "conservative"
    move = abs(candidate.get("price_delta_24h") or 0)
    strong_fees = candidate["criteria_scores"]["fee_productivity"] >= 4.0
    strong_activity = candidate["criteria_scores"]["recent_activity"] >= 4.0
    if config.allow_wide_preset and 0.05 <= move <= config.max_abs_price_delta_24h * 0.9 and strong_fees and strong_activity:
        return "wide"
    return "balanced"


def _range(candidate: dict[str, Any], config: Config, preset: str) -> dict[str, float] | None:
    if preset == "no-trade":
        return None
    price = candidate.get("current_price")
    if price is None or price <= 0:
        return None
    move = abs(candidate.get("price_delta_24h") if candidate.get("price_delta_24h") is not None else config.default_observed_move)
    rules = {
        "conservative": (config.conservative_min_half_width, config.conservative_max_half_width, 2.5),
        "balanced": (config.balanced_min_half_width, config.balanced_max_half_width, 1.75),
        "wide": (config.wide_min_half_width, config.wide_max_half_width, 3.5),
    }
    floor, cap, multiplier = rules[preset]
    half_width = min(cap, max(floor, move * multiplier))
    lower = price * (1.0 - half_width)
    upper = price * (1.0 + half_width)
    return {
        "center_price": round(price, 12),
        "half_width_pct": round(half_width, 6),
        "lower_price": round(lower, 12),
        "upper_price": round(upper, 12),
        "lower_limit_price": round(lower * (1.0 - config.limit_price_buffer_pct), 12),
        "upper_limit_price": round(upper * (1.0 + config.limit_price_buffer_pct), 12),
    }


def _candidate_row(candidate: dict[str, Any], include_range: bool = False) -> dict[str, Any]:
    row = {
        "pool_address": candidate["address"],
        "trading_pair": candidate["trading_pair"],
        "quote_symbol": candidate["quote_symbol"],
        "score": candidate["weighted_score"],
        "preset_suggestion": candidate["preset_suggestion"],
        "tvl_usd": round(candidate.get("tvl_usd") or 0, 2),
        "volume_24h_usd": round(candidate.get("volume_24h_usd") or 0, 2),
        "volume_7d_usd": round(candidate.get("volume_7d_usd") or 0, 2),
        "fees_24h_usd": round(candidate.get("fees_24h_usd") or 0, 2),
        "price_delta_24h": candidate.get("price_delta_24h"),
        "criteria_scores": candidate["criteria_scores"],
        "source_lenses": candidate.get("source_lenses", []),
        "source_categories": candidate.get("source_categories", []),
    }
    if include_range:
        row["range_suggestion"] = candidate.get("range_suggestion")
    return row


def _round_optional(value: Any, digits: int = 2) -> float | None:
    parsed = _to_float(value)
    return None if parsed is None else round(parsed, digits)


def _near_miss_row(
    candidate: dict[str, Any],
    reason: str,
    score: float | None = None,
    criteria: dict[str, float] | None = None,
) -> dict[str, Any]:
    token_a = candidate.get("token_a") or {}
    token_b = candidate.get("token_b") or {}
    return {
        "reason": reason,
        "pool_address": candidate.get("address"),
        "pair": candidate.get("trading_pair") or f"{token_a.get('symbol', 'n/a')}-{token_b.get('symbol', 'n/a')}",
        "quote_symbol": candidate.get("quote_symbol"),
        "score": score,
        "tvl_usd": _round_optional(candidate.get("tvl_usd")),
        "volume_24h_usd": _round_optional(candidate.get("volume_24h_usd")),
        "volume_7d_usd": _round_optional(candidate.get("volume_7d_usd")),
        "fees_24h_usd": _round_optional(candidate.get("fees_24h_usd")),
        "price_delta_24h": candidate.get("price_delta_24h"),
        "source_categories": candidate.get("source_categories", []),
        "source_lenses": candidate.get("source_lenses", []),
        "criteria_scores": criteria,
    }


def _append_near_miss(
    near_misses: dict[str, list[dict[str, Any]]],
    reason: str,
    candidate: dict[str, Any],
    score: float | None = None,
    criteria: dict[str, float] | None = None,
    limit_per_reason: int = 5,
) -> None:
    rows = near_misses.setdefault(reason, [])
    if len(rows) < limit_per_reason:
        rows.append(_near_miss_row(candidate, reason, score, criteria))


def _format_money(value: Any) -> str:
    parsed = _to_float(value)
    return "n/a" if parsed is None else f"${parsed:,.2f}"


def _format_number(value: Any, digits: int = 4) -> str:
    parsed = _to_float(value)
    return "n/a" if parsed is None else f"{parsed:,.{digits}f}"


def _format_pct(value: Any) -> str:
    parsed = _to_float(value)
    return "n/a" if parsed is None else f"{parsed * 100:+.2f}%"


def _format_scan_text(payload: dict[str, Any]) -> str:
    selected = payload.get("selected_candidate") or {}
    warnings = payload.get("warnings") or []
    rejections = payload.get("rejection_summary") or {}
    lines = [f"Orca Pool Scan: {payload.get('scan_status', 'unknown')}"]
    if selected:
        range_suggestion = selected.get("range_suggestion") or {}
        lines.extend(
            [
                f"Selected: {selected.get('trading_pair', 'n/a')}",
                f"Pool: {selected.get('pool_address', 'n/a')}",
                f"Score: {_format_number(selected.get('score'), 4)}",
                f"Preset: {selected.get('preset_suggestion', 'n/a')}",
                f"TVL: {_format_money(selected.get('tvl_usd'))}",
                f"24h volume: {_format_money(selected.get('volume_24h_usd'))}",
            ]
        )
        if range_suggestion:
            lines.append(
                "Range: "
                f"{_format_number(range_suggestion.get('lower_price'), 6)} - "
                f"{_format_number(range_suggestion.get('upper_price'), 6)}"
            )
    else:
        lines.append("Selected: none")
    if rejections:
        lines.append(f"Rejected pools: {sum(rejections.values())}")
    near_misses = payload.get("near_miss_candidates") or {}
    if near_misses:
        lines.append(f"Near-miss samples: {sum(len(rows) for rows in near_misses.values())}")
    if warnings:
        lines.append(f"Warnings: {len(warnings)}")
    summary = payload.get("agent_prompt_summary")
    if summary:
        lines.extend(["", summary])
    return "\n".join(lines)


def _candidate_table_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    selected = payload.get("selected_candidate") or {}
    selected_address = selected.get("pool_address")
    rows: list[dict[str, Any]] = []
    for rank, candidate in enumerate(payload.get("top_candidates") or [], 1):
        rows.append(
            {
                "Rank": rank,
                "Selected": "yes" if candidate.get("pool_address") == selected_address else "no",
                "Pair": candidate.get("trading_pair"),
                "Pool": candidate.get("pool_address"),
                "Score": _format_number(candidate.get("score"), 4),
                "Preset": candidate.get("preset_suggestion"),
                "Quote": candidate.get("quote_symbol"),
                "TVL": _format_money(candidate.get("tvl_usd")),
                "24h Volume": _format_money(candidate.get("volume_24h_usd")),
                "24h Fees": _format_money(candidate.get("fees_24h_usd")),
                "24h Move": _format_pct(candidate.get("price_delta_24h")),
            }
        )
    if selected and not any(row["Pool"] == selected_address for row in rows):
        rows.insert(
            0,
            {
                "Rank": "selected",
                "Selected": "yes",
                "Pair": selected.get("trading_pair"),
                "Pool": selected_address,
                "Score": _format_number(selected.get("score"), 4),
                "Preset": selected.get("preset_suggestion"),
                "Quote": selected.get("quote_symbol"),
                "TVL": _format_money(selected.get("tvl_usd")),
                "24h Volume": _format_money(selected.get("volume_24h_usd")),
                "24h Fees": _format_money(selected.get("fees_24h_usd")),
                "24h Move": _format_pct(selected.get("price_delta_24h")),
            },
        )
    return rows


def _config_summary(config: Config, diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        "risk_profile": config.risk_profile,
        "categories": config.categories,
        "stats_windows": config.stats_windows,
        "min_tvl_query_usd": config.min_tvl_query_usd,
        "min_tvl_usd": config.min_tvl_usd,
        "min_volume_24h_usd": config.min_volume_24h_usd,
        "min_volume_7d_usd": config.min_volume_7d_usd,
        "max_abs_price_delta_24h": config.max_abs_price_delta_24h,
        "allowed_quote_symbols": config.allowed_quote_symbols,
        "weights_sum": round(
            config.weight_liquidity_depth
            + config.weight_recent_activity
            + config.weight_fee_productivity
            + config.weight_range_stability
            + config.weight_execution_simplicity
            + config.weight_sponsor_fit,
            6,
        ),
        "diagnostics": diagnostics,
    }


async def _save_scan_report(payload: dict[str, Any]) -> None:
    """Create a standard Condor dashboard report; report failures must not fail the routine."""
    try:
        from condor.reports import ReportBuilder

        selected = payload.get("selected_candidate") or {}
        warnings = payload.get("warnings") or []
        rejections = payload.get("rejection_summary") or {}
        status = payload.get("scan_status", "unknown")

        builder = ReportBuilder("Orca Pool Scan")
        builder.source("routine", "orca_pool_scan").tags(["orca", "lp", "scanner", "agent"]).manual_order()
        builder.kpi("Status", str(status), trend="up" if status == "success" else "down")
        builder.kpi("Selected Pair", selected.get("trading_pair") or "none")
        builder.kpi("Score", _format_number(selected.get("score"), 4))
        builder.kpi("Preset", selected.get("preset_suggestion") or "n/a")
        builder.kpi("TVL", _format_money(selected.get("tvl_usd")))
        builder.kpi("24h Volume", _format_money(selected.get("volume_24h_usd")))
        builder.markdown(
            "## Agent Summary\n"
            f"{payload.get('agent_prompt_summary', 'No summary available.')}\n\n"
            "No live LP executor was created by this scan. Gateway pool-info, quote orientation, "
            "portfolio balance, and risk-cap preflight are still required before any live open."
        )

        candidate_rows = _candidate_table_rows(payload)
        if candidate_rows:
            builder.markdown("## Ranked Candidates")
            builder.table(candidate_rows)
        if selected.get("range_suggestion"):
            range_suggestion = selected["range_suggestion"]
            builder.markdown("## Selected Range Suggestion")
            builder.table(
                [
                    {
                        "Center Price": _format_number(range_suggestion.get("center_price"), 6),
                        "Lower Price": _format_number(range_suggestion.get("lower_price"), 6),
                        "Upper Price": _format_number(range_suggestion.get("upper_price"), 6),
                        "Lower Limit": _format_number(range_suggestion.get("lower_limit_price"), 6),
                        "Upper Limit": _format_number(range_suggestion.get("upper_limit_price"), 6),
                        "Half Width": _format_pct(range_suggestion.get("half_width_pct")),
                    }
                ]
            )
        if rejections:
            builder.markdown("## Rejection Summary")
            builder.table([{"Reason": reason, "Count": count} for reason, count in sorted(rejections.items())])
        near_misses = payload.get("near_miss_candidates") or {}
        if near_misses:
            rows = []
            for reason, candidates in sorted(near_misses.items()):
                for candidate in candidates:
                    rows.append(
                        {
                            "Reason": reason,
                            "Pair": candidate.get("pair"),
                            "Pool": candidate.get("pool_address"),
                            "Score": _format_number(candidate.get("score"), 4),
                            "TVL": _format_money(candidate.get("tvl_usd")),
                            "24h Volume": _format_money(candidate.get("volume_24h_usd")),
                            "7d Volume": _format_money(candidate.get("volume_7d_usd")),
                            "24h Fees": _format_money(candidate.get("fees_24h_usd")),
                            "24h Move": _format_pct(candidate.get("price_delta_24h")),
                            "Categories": ",".join(candidate.get("source_categories") or []),
                        }
                    )
            if rows:
                builder.markdown("## Near-Miss Rejections")
                builder.table(rows)
        if warnings:
            builder.markdown("## Warnings\n" + "\n".join(f"- {warning}" for warning in warnings))
        builder.markdown(
            "## Debug JSON Payload\n"
            "```json\n"
            f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
            "```"
        )
        await builder.save()
    except Exception:
        return


async def run(config: Config, context: Any) -> str:
    timestamp = _utc_now()
    try:
        config = _apply_risk_profile(config)
        config_errors = _config_errors(config)
        if config_errors:
            payload = _failure_payload("config-invalid", timestamp, config_errors, {"config_invalid": len(config_errors)})
            payload["config_summary"] = {"risk_profile": config.risk_profile, "categories": config.categories}
            await _save_scan_report(payload)
            return _format_scan_text(payload)

        records, api_errors, diagnostics = await _fetch_all(config)
        config_summary = _config_summary(config, diagnostics)
        if api_errors or records is None:
            payload = _failure_payload("api-failed", timestamp, ["; ".join(api_errors)], {})
            payload["config_summary"] = config_summary
            await _save_scan_report(payload)
            return _format_scan_text(payload)
        if not records:
            payload = _failure_payload("no-trade", timestamp, ["Orca API returned no pool records"], {})
            payload["config_summary"] = config_summary
            await _save_scan_report(payload)
            return _format_scan_text(payload)

        rejection_counts: Counter[str] = Counter()
        warnings: list[str] = []
        candidates: list[dict[str, Any]] = []
        near_misses: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            candidate = _normalize(record)
            accepted, reason, gate_warnings = _gate(candidate, config)
            warnings.extend(gate_warnings)
            if not accepted:
                rejection_reason = reason or "rejected"
                rejection_counts[rejection_reason] += 1
                _append_near_miss(near_misses, rejection_reason, candidate)
                continue
            criteria, weighted_score, score_warnings = _score(candidate, config)
            warnings.extend(score_warnings)
            candidate["criteria_scores"] = criteria
            candidate["weighted_score"] = weighted_score
            candidate["preset_suggestion"] = _preset(candidate, config)
            candidate["range_suggestion"] = _range(candidate, config, candidate["preset_suggestion"])
            if candidate["preset_suggestion"] == "no-trade":
                rejection_counts["score_below_trade_threshold"] += 1
                _append_near_miss(near_misses, "score_below_trade_threshold", candidate, weighted_score, criteria)
                continue
            candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                item["weighted_score"],
                item["criteria_scores"]["execution_simplicity"],
                item.get("tvl_usd") or 0,
                item.get("volume_7d_usd") or 0,
                item.get("fees_7d_usd") or 0,
                item.get("quote_symbol", "").upper() in {symbol.upper() for symbol in config.preferred_quote_symbols},
                -(abs(item.get("price_delta_24h") or 0)),
            ),
            reverse=True,
        )

        selected = candidates[0] if candidates else None
        if not selected:
            payload = {
                "scan_status": "no-trade",
                "timestamp": timestamp,
                "config_summary": config_summary,
                "selected_candidate": None,
                "top_candidates": [],
                "rejection_summary": dict(rejection_counts),
                "near_miss_candidates": near_misses,
                "warnings": sorted(set(warnings)),
                "agent_prompt_summary": "No trade: no Orca pool survived hard gates and scoring thresholds.",
            }
            await _save_scan_report(payload)
            return _format_scan_text(payload)

        summary = (
            f"Selected {selected['trading_pair']} pool {selected['address']} with score "
            f"{selected['weighted_score']} and preset {selected['preset_suggestion']}; "
            "Gateway and portfolio preflight are still required before any live LP executor."
        )
        payload = {
            "scan_status": "success",
            "timestamp": timestamp,
            "config_summary": config_summary,
            "selected_candidate": _candidate_row(selected, include_range=True),
            "top_candidates": [_candidate_row(candidate) for candidate in candidates[: max(config.top_n, 1)]],
            "rejection_summary": dict(rejection_counts),
            "warnings": sorted(set(warnings)),
            "agent_prompt_summary": summary,
        }
        await _save_scan_report(payload)
        return _format_scan_text(payload)
    except Exception as exc:
        payload = _failure_payload("api-failed", timestamp, [f"unexpected scan failure: {type(exc).__name__}: {exc}"], {})
        await _save_scan_report(payload)
        return _format_scan_text(payload)


def _self_check() -> None:
    assert _apply_risk_profile(Config(risk_profile="meme_scout", stats_windows="24h")).stats_windows == "24h,7d"
    assert _apply_risk_profile(Config(risk_profile="category_scout")).categories == [
        "utility",
        "governance",
        "liquid_staking_token",
        "security",
    ]

    warned_candidate = {
        "address": "pool",
        "token_a": {"symbol": "SOL", "mint": "sol", "decimals": 9},
        "token_b": {"symbol": "USDC", "mint": "usdc", "decimals": 6},
        "has_warning": True,
    }
    assert _gate(warned_candidate, Config(reject_has_warning=False))[0:2] == (
        False,
        "has_warning",
    )

    sol_quote_candidate = {
        "address": "pool",
        "token_a": {"symbol": "FARTCOIN", "mint": "fart", "decimals": 6},
        "token_b": {"symbol": "SOL", "mint": "sol", "decimals": 9},
        "price_raw": 0.001,
        "tvl_usd": 600000,
        "volume_24h_usd": 100000,
        "volume_7d_usd": 500000,
        "has_warning": False,
    }
    usdc_only = Config(
        allowed_quote_symbols=["USDC"],
        preferred_quote_symbols=["USDC"],
        require_gateway_pool_info=False,
    )
    assert _gate(sol_quote_candidate, usdc_only)[0:2] == (
        False,
        "disallowed_quote_symbol",
    )


if __name__ == "__main__":
    import sys

    if "--self-check" in sys.argv:
        _self_check()
        print("self-check passed")
        raise SystemExit(0)
    print(asyncio.run(run(Config(), None)))
