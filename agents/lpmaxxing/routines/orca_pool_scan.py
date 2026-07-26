import asyncio
import json
import math
import re
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml
from pydantic import BaseModel, Field, field_validator

try:
    from routines.base import RoutineResult
except ModuleNotFoundError:
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[3]))
    from routines.base import RoutineResult


CATEGORY = "Orca LP Agent"
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
VALID_EXECUTION_MODES = {"dry_run", "loop"}
REPORT_QUALIFICATION = (
    "Orca categories are qualified pool classification from Orca's API.\n"
    "They are not independent token-security verification."
)
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


def _normalized_name(value: Any, default: str = "") -> str:
    text = str(value or default).strip().lower().replace("-", "_").replace(" ", "_")
    return text or default


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


class Config(BaseModel):
    """Scan public Orca Whirlpool pools and return one gated LP candidate."""

    execution_mode: str = Field(default="dry_run", description="dry_run or loop")
    controller_id: str = Field(
        default="", description="Dynamic controller id from the current loop tick"
    )
    risk_profile: str = Field(
        default="yield_focused", description="Structured Orca risk profile"
    )
    orca_api_base_url: str = "https://api.orca.so/v2/solana"
    pool_endpoint: str = "/pools"
    request_timeout_seconds: int = Field(default=15, gt=0)
    request_user_agent: str = "Mozilla/5.0 CondorOrcaLPAgent/2.0"
    min_tvl_query_usd: float = Field(default=0, ge=0)
    include_pool_addresses: list[str] = Field(default_factory=list)
    exclude_pool_addresses: list[str] = Field(default_factory=list)
    stats_windows: str = Field(
        default="1h,4h,24h,7d",
        description="Dry-run may append 30d; live requests are fixed",
    )
    total_amount_quote: float = Field(default=10, gt=0)
    top_n: int = Field(default=3, gt=0)

    @field_validator("execution_mode", "risk_profile", mode="before")
    @classmethod
    def _names(cls, value: Any) -> str:
        return _normalized_name(value)

    @field_validator("controller_id", mode="before")
    @classmethod
    def _controller(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("include_pool_addresses", "exclude_pool_addresses", mode="before")
    @classmethod
    def _pool_lists(cls, value: Any) -> list[str]:
        return _list_value(value)

    @field_validator("stats_windows", mode="before")
    @classmethod
    def _windows(cls, value: Any) -> str:
        aliases = {"1d": "24h", "day": "24h", "1w": "7d", "week": "7d", "month": "30d"}
        windows = [
            aliases.get(item.lower(), item.lower()) for item in _list_value(value)
        ]
        return ",".join(dict.fromkeys(windows or LIVE_STATS))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def _session_config(controller_id: str) -> dict[str, Any]:
    from condor.agents.journal import resolve_agent_dirs

    session_dir, _ = resolve_agent_dirs(controller_id)
    if session_dir is None or not session_dir.is_dir():
        raise ValueError(f"no loop session found for controller_id '{controller_id}'")
    path = session_dir / "config.yml"
    value = yaml.safe_load(path.read_text()) or {}
    if not isinstance(value, dict):
        raise ValueError("current session config must be a YAML object")
    return value


def _session_lifecycle_status(controller_id: str) -> str:
    from condor.agents.journal import resolve_agent_dirs

    session_dir, _ = resolve_agent_dirs(controller_id)
    if session_dir is None or not session_dir.is_dir():
        raise ValueError(f"no loop session found for controller_id '{controller_id}'")
    path = session_dir / "orca_lifecycle.json"
    if not path.exists():
        return "running"
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise ValueError("legacy Orca lifecycle state cannot be resumed")
    if value.get("controller_id") != controller_id:
        raise ValueError("Orca session-state controller mismatch")
    if value.get("active_position"):
        raise ValueError("Orca pool scan is blocked while an active position exists")
    return str(value.get("session_status") or "").strip().lower()


def _resolve_policy(config: Config) -> tuple[Config, dict[str, Any], list[str]]:
    errors: list[str] = []
    if config.execution_mode not in VALID_EXECUTION_MODES:
        errors.append("execution_mode must be dry_run or loop")
        return config, {}, errors

    requested_profile = config.risk_profile or "yield_focused"
    if config.execution_mode == "loop":
        if not config.controller_id:
            errors.append("controller_id is required in loop mode")
            return config, {}, errors
        try:
            session = _session_config(config.controller_id)
        except Exception as exc:
            errors.append(str(exc))
            return config, {}, errors
        runtime_mode = str(session.get("execution_mode") or "").strip().lower()
        mode_match = re.search(
            r"^\s*SESSION_MODE:\s*(dry_run|loop|run_once)\s*$",
            str(session.get("trading_context") or ""),
            re.MULTILINE | re.IGNORECASE,
        )
        requested_mode = mode_match.group(1).lower() if mode_match else ""
        if runtime_mode != "loop" or requested_mode != "loop":
            errors.append(
                "loop scan requires matching runtime execution_mode and SESSION_MODE"
            )
            return config, {}, errors
        session_profile = _normalized_name(session.get("risk_profile"))
        if not session_profile:
            errors.append("risk_profile is missing from the current session config")
            return config, {}, errors
        if (
            "risk_profile" in config.model_fields_set
            and requested_profile != session_profile
        ):
            errors.append(
                f"routine risk_profile '{requested_profile}' conflicts with current session risk_profile '{session_profile}'"
            )
        config = config.model_copy(update={"risk_profile": session_profile})
        try:
            lifecycle_status = _session_lifecycle_status(config.controller_id)
        except Exception as exc:
            errors.append(str(exc))
            return config, {}, errors
        if lifecycle_status != "running":
            errors.append(
                f"Orca session is '{lifecycle_status or 'unknown'}'; pool scan is blocked"
            )
        session_budget = _number(session.get("total_amount_quote"))
        if session_budget is not None and session_budget > 0:
            config = config.model_copy(update={"total_amount_quote": session_budget})

    if config.risk_profile not in VALID_RISK_PROFILES:
        errors.append(
            f"invalid risk_profile '{config.risk_profile}'; valid values: {', '.join(sorted(VALID_RISK_PROFILES))}"
        )
        return config, {}, errors
    if config.execution_mode == "loop" and config.risk_profile in DRY_RUN_ONLY_PROFILES:
        errors.append(f"risk_profile '{config.risk_profile}' is dry-run-only")
    if config.execution_mode == "loop" and config.include_pool_addresses:
        errors.append("manual pool inclusion is dry-run-only")

    windows = [item for item in config.stats_windows.split(",") if item]
    invalid_windows = sorted(set(windows) - {*LIVE_STATS, "30d"})
    if invalid_windows:
        errors.append(f"invalid stats windows: {', '.join(invalid_windows)}")
    return config, PROFILE_POLICY.get(config.risk_profile, {}), errors


def _url(config: Config, params: dict[str, Any]) -> str:
    endpoint = config.pool_endpoint
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    return f"{config.orca_api_base_url.rstrip('/')}{endpoint}?{urlencode(clean)}"


def _request_specs(
    config: Config, policy: dict[str, Any]
) -> list[tuple[str, str, str | None, str]]:
    stats = list(LIVE_STATS)
    if (
        config.execution_mode == "dry_run"
        and config.risk_profile in DRY_RUN_ONLY_PROFILES
        and "30d" in config.stats_windows.split(",")
    ):
        stats.append("30d")
    specs: list[tuple[str, str, str | None, str]] = []
    for category in policy["categories"]:
        for lens in DISCOVERY_LENSES:
            params = {
                "sortBy": lens,
                "sortDirection": "desc",
                "stats": ",".join(stats),
                "size": 100,
                "minTvl": config.min_tvl_query_usd,
                "categories": category,
            }
            name = f"{category}:{lens}"
            specs.append((name, lens, category, _url(config, params)))
    if config.execution_mode == "dry_run" and config.include_pool_addresses:
        params = {
            "addresses": ",".join(config.include_pool_addresses),
            "stats": ",".join(stats),
            "size": 100,
        }
        specs.append(("include", "include", None, _url(config, params)))
    return specs


def _retry_delay(error: HTTPError) -> float:
    value = error.headers.get("Retry-After") if error.headers else None
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.5


def _fetch_json(url: str, user_agent: str, timeout: int) -> dict[str, Any]:
    request = Request(
        url, headers={"User-Agent": user_agent, "Accept": "application/json"}
    )
    for attempt in range(2):
        try:
            with urlopen(request, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("data"), list):
                raise ValueError("Orca response missing top-level data list")
            return parsed
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if attempt == 0 and retryable:
                time.sleep(_retry_delay(exc))
                continue
            raise
        except (TimeoutError, URLError):
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise
    raise RuntimeError("unreachable request retry state")


def _first(obj: Any, paths: list[str]) -> Any:
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


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _decimal_ratio(value: Any) -> float | None:
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


def _optional_bool(value: Any) -> bool | None:
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


def _updated_at(record: Any) -> datetime | None:
    if not isinstance(record, dict):
        return None
    value = _first(record, ["updatedAt", "updated_at"])
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def _fetch_all(
    config: Config, policy: dict[str, Any]
) -> tuple[list[Any] | None, list[str], dict[str, Any]]:
    specs = _request_specs(config, policy)
    results = await asyncio.gather(
        *[
            asyncio.to_thread(
                _fetch_json,
                url,
                config.request_user_agent,
                config.request_timeout_seconds,
            )
            for _, _, _, url in specs
        ],
        return_exceptions=True,
    )
    errors: list[str] = []
    records: dict[str, Any] = {}
    evidence: dict[str, tuple[set[str], set[str]]] = {}
    diagnostics: dict[str, Any] = {
        "scope": "union of the first 100 Orca results per configured category and discovery lens",
        "request_size": 100,
        "stats": list(LIVE_STATS),
        "categories": list(policy["categories"]),
        "discovery_lenses": list(DISCOVERY_LENSES),
        "requests": [],
        "record_counts": {},
    }
    if (
        config.execution_mode == "dry_run"
        and config.risk_profile in DRY_RUN_ONLY_PROFILES
        and "30d" in config.stats_windows.split(",")
    ):
        diagnostics["stats"].append("30d")

    malformed_index = 0
    for (name, lens, category, _), result in zip(specs, results, strict=True):
        diagnostics["requests"].append(
            {"name": name, "category": category, "discovery_lens": lens}
        )
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
            address = (
                _text(
                    _first(
                        record,
                        ["address", "id", "pubkey", "poolAddress", "pool_address"],
                    )
                )
                if isinstance(record, dict)
                else None
            )
            if address:
                key = address
            else:
                malformed_index += 1
                key = f"malformed:{malformed_index}"
            categories, lenses = evidence.setdefault(key, (set(), set()))
            if category:
                categories.add(category)
            lenses.add(lens)
            if key not in records:
                records[key] = record
                continue
            existing_time = _updated_at(records[key])
            incoming_time = _updated_at(record)
            if incoming_time is not None and (
                existing_time is None or incoming_time > existing_time
            ):
                records[key] = record

    diagnostics["raw_records"] = sum(diagnostics["record_counts"].values())
    diagnostics["deduplicated_records"] = len(records)
    if errors:
        return None, errors, diagnostics
    merged: list[Any] = []
    for key, record in records.items():
        if isinstance(record, dict):
            record = dict(record)
            record["_scan_categories"] = sorted(evidence[key][0])
            record["_scan_lenses"] = sorted(evidence[key][1])
        merged.append(record)
    return merged, [], diagnostics


def _token(record: dict[str, Any], side: str) -> dict[str, Any]:
    upper = side.upper()
    lower = side.lower()
    token = record.get(f"token{upper}")
    mint = record.get(f"tokenMint{upper}")
    merged = {
        **(mint if isinstance(mint, dict) else {}),
        **(token if isinstance(token, dict) else {}),
    }
    wrapped = {"merged": merged, "record": record}
    return {
        "symbol": _text(
            _first(
                wrapped,
                [
                    "merged.symbol",
                    f"record.token{upper}Symbol",
                    f"record.token_{lower}_symbol",
                ],
            )
        ),
        "mint": _text(
            _first(
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
        "decimals": _integer(
            _first(
                wrapped,
                [
                    "merged.decimals",
                    f"record.token{upper}Decimals",
                    f"record.token_{lower}.decimals",
                ],
            )
        ),
    }


def _window_value(record: dict[str, Any], window: str, field: str) -> float | None:
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
    return _number(_first({"stats": record.get("stats", {}), "record": record}, paths))


def _normalize(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {"malformed": True, "raw": record}
    stats = record.get("stats") if isinstance(record.get("stats"), dict) else {}
    wrapped = {"record": record, "stats": stats}
    candidate: dict[str, Any] = {
        "malformed": False,
        "address": _text(
            _first(record, ["address", "id", "pubkey", "poolAddress", "pool_address"])
        ),
        "updated_at": _text(_first(record, ["updatedAt", "updated_at"])),
        "token_a": _token(record, "a"),
        "token_b": _token(record, "b"),
        "scanner_price": _number(
            _first(record, ["price", "currentPrice", "current_price", "tokenPrice"])
        ),
        "tvl_usd": _number(
            _first(
                record, ["tvlUsdc", "tvlUsd", "tvlUSD", "tvl", "totalValueLockedUsd"]
            )
        ),
        "net_price_change_24h": _decimal_ratio(
            _first(
                wrapped,
                [
                    "stats.24h.priceDelta",
                    "stats.24h.price_delta",
                    "record.priceDelta24h",
                    "record.price_delta_24h",
                ],
            )
        ),
        "price_delta_24h_raw": _first(
            wrapped,
            [
                "stats.24h.priceDelta",
                "stats.24h.price_delta",
                "record.priceDelta24h",
                "record.price_delta_24h",
            ],
        ),
        "fee_rate_raw": _number(_first(record, ["feeRate", "fee_rate"])),
        "adaptive_fee_enabled": _optional_bool(
            _first(record, ["adaptiveFeeEnabled", "adaptive_fee_enabled"])
        ),
        "fee_tier_index": _integer(
            _first(record, ["feeTierIndex", "fee_tier_index", "feeTier", "fee_tier"])
        ),
        "tick_spacing": _integer(_first(record, ["tickSpacing", "tick_spacing"])),
        "has_warning": _optional_bool(
            _first(record, ["hasWarning", "has_warning", "warning", "isWarning"])
        ),
        "source_categories": sorted(set(record.get("_scan_categories", []))),
        "source_lenses": sorted(set(record.get("_scan_lenses", []))),
    }
    for window in (*LIVE_STATS, "30d"):
        candidate[f"volume_{window}_usd"] = _window_value(record, window, "volume")
        candidate[f"fees_{window}_usd"] = _window_value(record, window, "fees")
    return candidate


def _derive(candidate: dict[str, Any], budget: float) -> None:
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


def _metadata_ok(candidate: dict[str, Any]) -> bool:
    return all(
        token.get("symbol")
        and token.get("mint")
        and isinstance(token.get("decimals"), int)
        and token["decimals"] >= 0
        for token in (candidate.get("token_a", {}), candidate.get("token_b", {}))
    )


def _gate(
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
    if not _metadata_ok(candidate):
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


def _multiple_score(value: float, minimum: float) -> float:
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


def _score(candidate: dict[str, Any], policy: dict[str, Any]) -> None:
    stability_reference = policy["stability_reference"]
    scores = {
        "fee_productivity": _multiple_score(
            candidate["sustained_fee_productivity"], policy["min_fee_productivity"]
        ),
        "recent_activity": 0.6
        * _multiple_score(candidate["volume_24h_usd"], policy["min_volume_24h_usd"])
        + 0.4
        * _multiple_score(candidate["volume_7d_usd"], policy["min_volume_7d_usd"]),
        "net_change_stability": 5.0
        * (
            1.0
            - min(abs(candidate["net_price_change_24h"]), stability_reference)
            / stability_reference
        ),
        "liquidity_depth": _multiple_score(candidate["tvl_usd"], policy["min_tvl_usd"]),
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


def _range_plan(
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


def _rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -candidate["weighted_score"],
        -candidate["sustained_fee_productivity"],
        -candidate["criteria_scores"]["recent_activity"],
        abs(candidate["net_price_change_24h"]),
        -candidate["tvl_usd"],
        candidate["address"],
    )


def _bps(value: float | None) -> float | None:
    return value * 10_000.0 if value is not None else None


def _candidate_row(
    candidate: dict[str, Any], observed_at: str, risk_profile: str, budget: float
) -> dict[str, Any]:
    fee_rate = candidate.get("fee_rate_raw")
    maximum = PROFILE_POLICY[risk_profile]["max_abs_net_price_change_24h"]
    row = {
        "observed_at": observed_at,
        "risk_profile": risk_profile,
        "pool_address": candidate["address"],
        "trading_pair": candidate["trading_pair"],
        "token_a": candidate["token_a"],
        "token_b": candidate["token_b"],
        "price_orientation": "token_b_per_token_a",
        "scanner_price": candidate.get("scanner_price"),
        "tvl_usd": candidate.get("tvl_usd"),
        "volume_1h_usd": candidate.get("volume_1h_usd"),
        "volume_4h_usd": candidate.get("volume_4h_usd"),
        "volume_24h_usd": candidate.get("volume_24h_usd"),
        "volume_7d_usd": candidate.get("volume_7d_usd"),
        "fees_1h_usd": candidate.get("fees_1h_usd"),
        "fees_4h_usd": candidate.get("fees_4h_usd"),
        "fees_24h_usd": candidate.get("fees_24h_usd"),
        "fees_7d_usd": candidate.get("fees_7d_usd"),
        "fee_tvl_24h": candidate.get("fee_tvl_24h"),
        "fee_tvl_7d_daily": candidate.get("fee_tvl_7d_daily"),
        "fee_productivity_1h_dailyized_bps": _bps(candidate.get("fee_productivity_1h")),
        "fee_productivity_4h_dailyized_bps": _bps(candidate.get("fee_productivity_4h")),
        "fee_productivity_24h_bps_per_day": _bps(candidate.get("fee_productivity_24h")),
        "fee_productivity_7d_daily_bps": _bps(candidate.get("fee_productivity_7d")),
        "sustained_fee_productivity": candidate.get("sustained_fee_productivity"),
        "sustained_fee_productivity_bps_per_day": _bps(
            candidate.get("sustained_fee_productivity")
        ),
        "fee_momentum_1h_x": candidate.get("fee_momentum_1h_x"),
        "fee_momentum_4h_x": candidate.get("fee_momentum_4h_x"),
        "volume_tvl_24h": candidate.get("volume_tvl_24h"),
        "volume_tvl_7d_daily": candidate.get("volume_tvl_7d_daily"),
        "net_price_change_24h": candidate.get("net_price_change_24h"),
        "net_price_change_24h_pct": candidate.get("net_price_change_24h") * 100.0,
        "profile_net_change_limit_pct": (
            maximum * 100.0 if maximum is not None else None
        ),
        "profile_net_change_limit_enabled": maximum is not None,
        "fee_rate_raw": fee_rate,
        "fee_rate_fraction": fee_rate / 1_000_000.0 if fee_rate is not None else None,
        "fee_rate_bps": fee_rate / 100.0 if fee_rate is not None else None,
        "adaptive_fee_enabled": candidate.get("adaptive_fee_enabled"),
        "fee_tier_index": candidate.get("fee_tier_index"),
        "tick_spacing": candidate.get("tick_spacing"),
        "has_warning": candidate.get("has_warning"),
        "criteria_raw": candidate["criteria_raw"],
        "criteria_scores": candidate["criteria_scores"],
        "mcda_weights": dict(MCDA_WEIGHTS),
        "weighted_score": candidate["weighted_score"],
        "score": candidate["weighted_score"],
        "source_categories": candidate.get("source_categories", []),
        "source_lenses": candidate.get("source_lenses", []),
        "session_budget_quote": budget,
        "total_amount_quote": budget,
        "gross_fee_estimate_quote_per_day": candidate.get(
            "gross_fee_estimate_quote_per_day"
        ),
        "gross_fee_estimate_qualification": (
            "Pool-average quote per day before concentration, competing liquidity, inventory loss, "
            "swaps, transaction fees, rent, and close costs; not a position-yield forecast."
        ),
        "range_plan": candidate["range_plan"],
        "preset_suggestion": candidate["range_plan"].get("preset"),
        "analysis_only": True,
        "live_ready": False,
    }
    if (
        candidate.get("volume_30d_usd") is not None
        or candidate.get("fees_30d_usd") is not None
    ):
        row["dry_run_30d_consult"] = {
            "volume_30d_usd": candidate.get("volume_30d_usd"),
            "fees_30d_usd": candidate.get("fees_30d_usd"),
        }
    return row


def _near_miss(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    token_a = candidate.get("token_a", {})
    token_b = candidate.get("token_b", {})
    return {
        "reason": reason,
        "pool_address": candidate.get("address"),
        "pair": candidate.get("trading_pair")
        or f"{token_a.get('symbol', 'n/a')}-{token_b.get('symbol', 'n/a')}",
        "tvl_usd": candidate.get("tvl_usd"),
        "volume_24h_usd": candidate.get("volume_24h_usd"),
        "volume_7d_usd": candidate.get("volume_7d_usd"),
        "fees_24h_usd": candidate.get("fees_24h_usd"),
        "fees_7d_usd": candidate.get("fees_7d_usd"),
        "fee_productivity_24h_bps_per_day": _bps(candidate.get("fee_productivity_24h")),
        "fee_productivity_7d_daily_bps": _bps(candidate.get("fee_productivity_7d")),
        "net_price_change_24h_pct": (
            candidate.get("net_price_change_24h") * 100.0
            if candidate.get("net_price_change_24h") is not None
            else None
        ),
        "source_categories": candidate.get("source_categories", []),
        "source_lenses": candidate.get("source_lenses", []),
        "range_plan": candidate.get("range_plan"),
    }


def _failure_payload(
    status: str,
    timestamp: str,
    errors: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "scan_status": status,
        "decision": (
            "blocked" if status in {"config-invalid", "api-failed"} else "no-trade"
        ),
        "analysis_only": True,
        "live_ready": False,
        "timestamp": timestamp,
        "selected_candidate": None,
        "top_candidates": [],
        "rejection_summary": {},
        "near_miss_candidates": {},
        "warnings": warnings or [],
        "errors": errors,
        "qualification": REPORT_QUALIFICATION,
        "agent_prompt_summary": "No trade: Orca pool scan failed closed before candidate selection.",
    }


def _fail(
    status: str,
    timestamp: str,
    warnings: list[str],
    rejection_summary: dict[str, int] | None = None,
) -> str:
    payload = _failure_payload(status, timestamp, [], warnings)
    payload["rejection_summary"] = rejection_summary or {}
    return _compact(payload)


def _format_money(value: Any) -> str:
    parsed = _number(value)
    return "n/a" if parsed is None else f"${parsed:,.2f}"


def _format_scan_text(payload: dict[str, Any]) -> str:
    selected = payload.get("selected_candidate") or {}
    lines = [f"Orca Pool Scan: {payload.get('scan_status', 'unknown')}"]
    if selected:
        plan = selected["range_plan"]
        lines.extend(
            [
                f"Selected: {selected['trading_pair']} ({selected['pool_address']})",
                f"Profile: {selected['risk_profile']}",
                f"Score: {selected['weighted_score']:.4f}",
                f"Sustained fees: {selected['sustained_fee_productivity_bps_per_day']:.4f} bp/day",
                f"TVL: {_format_money(selected['tvl_usd'])}",
                f"Range plan: {plan['preset']} at +/-{plan['provisional_half_width_pct']:.4f}%",
            ]
        )
    else:
        lines.append("Selected: none")
    if payload.get("rejection_summary"):
        lines.append(f"Rejected records: {sum(payload['rejection_summary'].values())}")
    if payload.get("errors"):
        lines.append("Errors: " + "; ".join(payload["errors"]))
    lines.extend(
        ["", REPORT_QUALIFICATION, "", payload.get("agent_prompt_summary", "")]
    )
    return "\n".join(lines)


def _candidate_table_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    selected = (payload.get("selected_candidate") or {}).get("pool_address")
    return [
        {
            "Rank": rank,
            "Selected": "yes" if candidate.get("pool_address") == selected else "no",
            "Pair": candidate.get("trading_pair"),
            "Pool": candidate.get("pool_address"),
            "Score": candidate.get("weighted_score"),
            "Preset": (candidate.get("range_plan") or {}).get("preset"),
            "TVL": _format_money(candidate.get("tvl_usd")),
            "24h Fees bp/day": candidate.get("fee_productivity_24h_bps_per_day"),
            "7d Fees bp/day": candidate.get("fee_productivity_7d_daily_bps"),
            "24h Net Change %": candidate.get("net_price_change_24h_pct"),
        }
        for rank, candidate in enumerate(payload.get("top_candidates") or [], 1)
    ]


def _routine_result(payload: dict[str, Any]) -> RoutineResult:
    preflight_input = {
        "scan_status": payload.get("scan_status"),
        "decision": payload.get("decision"),
        "selected_candidate": payload.get("selected_candidate"),
    }
    return RoutineResult(
        text=(
            _format_scan_text(payload)
            + "\n\nPreflight input:\n```json\n"
            + json.dumps(preflight_input, indent=2, sort_keys=True, default=str)
            + "\n```"
        ),
        table_data=_candidate_table_rows(payload),
    )


async def _save_scan_report(payload: dict[str, Any]) -> None:
    try:
        from condor.reports import ReportBuilder

        selected = payload.get("selected_candidate") or {}
        builder = ReportBuilder("Orca Pool Scan")
        builder.source("routine", "orca_pool_scan").tags(
            ["orca", "lp", "scanner", "agent"]
        ).manual_order()
        builder.kpi("Status", payload.get("scan_status", "unknown"))
        builder.kpi("Decision", payload.get("decision", "unknown"))
        builder.kpi("Selected Pair", selected.get("trading_pair") or "none")
        builder.kpi("Score", selected.get("weighted_score") or "n/a")
        builder.kpi("Preset", (selected.get("range_plan") or {}).get("preset") or "n/a")
        builder.markdown("## Qualification\n" + REPORT_QUALIFICATION)
        builder.markdown(
            "## Discovery Universe\n```json\n"
            + json.dumps(
                (payload.get("config_summary") or {}).get("discovery_universe", {}),
                indent=2,
                sort_keys=True,
            )
            + "\n```"
        )
        rows = _candidate_table_rows(payload)
        if rows:
            builder.markdown("## Ranked Gate-Passing Candidates")
            builder.table(rows)
        if payload.get("rejection_summary"):
            builder.markdown("## Rejection Summary")
            builder.table(
                [
                    {"Reason": reason, "Count": count}
                    for reason, count in sorted(payload["rejection_summary"].items())
                ]
            )
        builder.markdown(
            "## Debug JSON Payload\n```json\n"
            + json.dumps(payload, indent=2, sort_keys=True, default=str)
            + "\n```"
        )
        await builder.save()
    except Exception:
        return


async def run(config: Config, context: Any) -> RoutineResult:
    timestamp = _utc_now()
    input_config = config.model_dump(
        mode="json",
        exclude={"orca_api_base_url", "pool_endpoint", "request_user_agent"},
    )
    try:
        config, policy, config_errors = _resolve_policy(config)
        if config_errors:
            payload = _failure_payload("config-invalid", timestamp, config_errors)
            payload["input_config"] = input_config
            payload["config_summary"] = {"risk_profile": config.risk_profile}
            await _save_scan_report(payload)
            return _routine_result(payload)

        records, api_errors, diagnostics = await _fetch_all(config, policy)
        config_summary = {
            "risk_profile": config.risk_profile,
            "controller_id": config.controller_id or None,
            "categories": policy["categories"],
            "minimums": {
                "tvl_usd": policy["min_tvl_usd"],
                "volume_24h_usd": policy["min_volume_24h_usd"],
                "volume_7d_usd": policy["min_volume_7d_usd"],
                "fee_productivity_bps_per_day": policy["min_fee_productivity"]
                * 10_000.0,
            },
            "stability_reference": policy["stability_reference"],
            "max_abs_net_price_change_24h": policy["max_abs_net_price_change_24h"],
            "mcda_weights": MCDA_WEIGHTS,
            "discovery_universe": diagnostics,
        }
        if api_errors or records is None:
            payload = _failure_payload("api-failed", timestamp, api_errors)
            payload.update(
                {"input_config": input_config, "config_summary": config_summary}
            )
            await _save_scan_report(payload)
            return _routine_result(payload)

        rejections: Counter[str] = Counter()
        near_misses: dict[str, list[dict[str, Any]]] = {}
        eligible: list[dict[str, Any]] = []
        live_profile = config.risk_profile in LIVE_PROFILES
        excluded = set(config.exclude_pool_addresses)
        for record in records:
            candidate = _normalize(record)
            _derive(candidate, config.total_amount_quote)
            reason = _gate(candidate, policy, live_profile, excluded)
            if reason:
                rejections[reason] += 1
                rows = near_misses.setdefault(reason, [])
                if len(rows) < 5:
                    rows.append(_near_miss(candidate, reason))
                continue
            _score(candidate, policy)
            eligible.append(candidate)

        eligible.sort(key=_rank_key)
        selected: dict[str, Any] | None = None
        for candidate in eligible:
            candidate["range_plan"], reason = _range_plan(
                candidate, config.risk_profile
            )
            if reason:
                rejections[reason] += 1
                rows = near_misses.setdefault(reason, [])
                if len(rows) < 5:
                    rows.append(_near_miss(candidate, reason))
                continue
            if selected is None:
                selected = candidate

        feasible = [
            candidate
            for candidate in eligible
            if candidate["range_plan"]["status"] == "feasible"
        ]
        candidate_rows = [
            _candidate_row(
                candidate, timestamp, config.risk_profile, config.total_amount_quote
            )
            for candidate in feasible[: config.top_n]
        ]
        if selected is None:
            payload = _failure_payload("no-trade", timestamp, [])
            payload.update(
                {
                    "input_config": input_config,
                    "config_summary": config_summary,
                    "rejection_summary": dict(rejections),
                    "near_miss_candidates": near_misses,
                    "agent_prompt_summary": (
                        "No trade: no pool in the fetched Orca discovery universe passed every hard gate "
                        "and range-feasibility check."
                    ),
                }
            )
            await _save_scan_report(payload)
            return _routine_result(payload)

        selected_row = _candidate_row(
            selected, timestamp, config.risk_profile, config.total_amount_quote
        )
        payload = {
            "scan_status": "success",
            "decision": "analysis-only",
            "analysis_only": True,
            "live_ready": False,
            "timestamp": timestamp,
            "input_config": input_config,
            "config_summary": config_summary,
            "selected_candidate": selected_row,
            "top_candidates": candidate_rows,
            "rejection_summary": dict(rejections),
            "near_miss_candidates": near_misses,
            "warnings": [
                "Gateway pool identity, fresh price, wallet, executor, and risk preflight remain required."
            ],
            "errors": [],
            "qualification": REPORT_QUALIFICATION,
            "agent_prompt_summary": (
                f"Selected {selected_row['trading_pair']} pool {selected_row['pool_address']} with "
                f"score {selected_row['weighted_score']} and provisional "
                f"{selected_row['range_plan']['preset']} half-width "
                f"{selected_row['range_plan']['provisional_half_width_pct']:.4f}%. "
                "The scanner produced no executable prices."
            ),
        }
        await _save_scan_report(payload)
        return _routine_result(payload)
    except Exception as exc:
        payload = _failure_payload(
            "api-failed",
            timestamp,
            [f"unexpected scan failure: {type(exc).__name__}: {exc}"],
        )
        payload["input_config"] = input_config
        await _save_scan_report(payload)
        return _routine_result(payload)


def _fixture_candidate(
    change: float, sustained_bps: float, tick_spacing: int = 64
) -> dict[str, Any]:
    tvl = 1_000_000.0
    fee_ratio = sustained_bps / 10_000.0
    return {
        "net_price_change_24h": change,
        "tick_spacing": tick_spacing,
        "sustained_fee_productivity": fee_ratio,
    }


def _self_check() -> None:
    assert PROFILE_POLICY["yield_focused"]["categories"] == [
        "stablecoin",
        "liquid_staking_token",
        "utility",
        "governance",
    ]
    assert PROFILE_POLICY["yield_no_limit"]["max_abs_net_price_change_24h"] is None
    assert sum(MCDA_WEIGHTS.values()) == 1.0
    assert _decimal_ratio(0.02) == 0.02
    assert _decimal_ratio(1.01) == 1.01
    assert _decimal_ratio("0.02") == 0.02
    assert _decimal_ratio("2%") is None
    assert _multiple_score(2.0, 1.0) == 2.0
    assert _multiple_score(5.0, 1.0) == 3.0

    focused = _fixture_candidate(0.005, 5.0, 1)
    plan, reason = _range_plan(focused, "yield_focused")
    assert reason is None and plan["preset"] == "concentrated"
    balanced = _fixture_candidate(-0.02, 2.0, 1)
    plan, reason = _range_plan(balanced, "yield_focused")
    assert reason is None and plan["preset"] == "balanced"
    defensive = _fixture_candidate(0.04, 4.0, 1)
    plan, reason = _range_plan(defensive, "yield_high_risk")
    assert reason is None and plan["preset"] == "defensive"
    extreme = _fixture_candidate(0.50, 8.0, 1)
    plan, reason = _range_plan(extreme, "yield_extreme_risk")
    assert reason is None and plan["preset"] == "extreme"
    assert math.isclose(plan["provisional_half_width"], 0.75)
    no_limit = _fixture_candidate(0.64, 12.0, 1)
    plan, reason = _range_plan(no_limit, "yield_no_limit")
    assert reason is None and plan["width_capped"] is True
    assert plan["provisional_half_width"] == 0.95
    _, reason = _range_plan(
        _fixture_candidate(0.01, 12.0, 10_000_000), "yield_no_limit"
    )
    assert reason == "invalid_tick_spacing"

    policy = PROFILE_POLICY["yield_focused"]
    specs = _request_specs(Config(), policy)
    assert len(specs) == len(policy["categories"]) * 4
    assert all("stats=1h%2C4h%2C24h%2C7d" in spec[3] for spec in specs)
    assert all("size=100" in spec[3] for spec in specs)
    assert _resolve_policy(Config(execution_mode="loop", risk_profile="meme_scout"))[2]

    candidate = {
        "address": "pool",
        "token_a": {"symbol": "SOL", "mint": "sol", "decimals": 9},
        "token_b": {"symbol": "USDC", "mint": CANONICAL_USDC_MINT, "decimals": 6},
        "trading_pair": "SOL-USDC",
        "scanner_price": 150.0,
        "tvl_usd": 1_000_000.0,
        "volume_1h_usd": 10_000.0,
        "volume_4h_usd": 40_000.0,
        "volume_24h_usd": 300_000.0,
        "volume_7d_usd": 2_000_000.0,
        "fees_1h_usd": 25.0,
        "fees_4h_usd": 100.0,
        "fees_24h_usd": 500.0,
        "fees_7d_usd": 3_500.0,
        "fee_productivity_1h": 0.0006,
        "fee_productivity_4h": 0.0006,
        "fee_productivity_24h": 0.0005,
        "fee_productivity_7d": 0.0005,
        "sustained_fee_productivity": 0.0005,
        "fee_momentum_1h_x": 1.2,
        "fee_momentum_4h_x": 1.2,
        "volume_tvl_24h": 0.3,
        "volume_tvl_7d_daily": 2.0 / 7.0,
        "net_price_change_24h": 0.005,
        "fee_rate_raw": 400.0,
        "adaptive_fee_enabled": False,
        "fee_tier_index": 1,
        "tick_spacing": 1,
        "criteria_raw": {},
        "criteria_scores": {key: 5.0 for key in MCDA_WEIGHTS},
        "weighted_score": 5.0,
        "source_categories": ["utility"],
        "source_lenses": list(DISCOVERY_LENSES),
        "gross_fee_estimate_quote_per_day": 0.005,
        "range_plan": _range_plan(focused, "yield_focused")[0],
    }
    row = _candidate_row(candidate, "2026-07-24T00:00:00+00:00", "yield_focused", 10)
    forbidden = {"lower_price", "upper_price", "lower_limit_price", "upper_limit_price"}
    assert forbidden.isdisjoint(row)
    assert row["price_orientation"] == "token_b_per_token_a"
    assert row["token_b"]["mint"] == CANONICAL_USDC_MINT


if __name__ == "__main__":
    import sys

    if "--self-check" in sys.argv:
        _self_check()
        print("self-check passed")
        raise SystemExit(0)
    print(asyncio.run(run(Config(), None)).text)
