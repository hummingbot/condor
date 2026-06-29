"""Discover Orca and Meteora pool candidates for optimal_pool_scorer."""

import asyncio
import json
import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

DEFAULT_PROTOCOLS = ["orca", "meteora"]
DEFAULT_ORCA_URLS = [
    "https://api.geckoterminal.com/api/v2/networks/solana/dexes/orca/pools?sort=h24_volume_usd_desc",
    "https://api.geckoterminal.com/api/v2/networks/solana/dexes/orca_whirlpool/pools?sort=h24_volume_usd_desc",
    "https://api.mainnet.orca.so/v1/whirlpool/list",
]
DEFAULT_METEORA_URLS = [
    "https://api.geckoterminal.com/api/v2/networks/solana/dexes/meteora/pools?sort=h24_volume_usd_desc",
    "https://api.geckoterminal.com/api/v2/networks/solana/dexes/meteora-dlmm/pools?sort=h24_volume_usd_desc",
    "https://dlmm-api.meteora.ag/pair/all",
    "https://app.meteora.ag/clmm-api/pair/all",
]


class Config(BaseModel):
    protocols: list[str] = Field(default_factory=lambda: list(DEFAULT_PROTOCOLS))
    max_pools_per_protocol: int = Field(default=20)
    min_liquidity_usd: float = Field(default=100_000)
    timeout_sec: float = Field(default=15.0)
    default_fee_rate: float = Field(default=0.0004, description="Fallback fee rate for volume-based fee proxy")
    report_title: str = Field(default="LP Pool Discovery")
    orca_urls: list[str] = Field(default_factory=lambda: list(DEFAULT_ORCA_URLS))
    meteora_urls: list[str] = Field(default_factory=lambda: list(DEFAULT_METEORA_URLS))

    @field_validator("protocols", mode="before")
    @classmethod
    def default_protocols(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return list(DEFAULT_PROTOCOLS)
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()] or list(DEFAULT_PROTOCOLS)
        return value

    @field_validator("orca_urls", mode="before")
    @classmethod
    def default_orca_urls(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return list(DEFAULT_ORCA_URLS)
        if isinstance(value, str):
            return [u.strip() for u in value.split(",") if u.strip()] or list(DEFAULT_ORCA_URLS)
        return value

    @field_validator("meteora_urls", mode="before")
    @classmethod
    def default_meteora_urls(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return list(DEFAULT_METEORA_URLS)
        if isinstance(value, str):
            return [u.strip() for u in value.split(",") if u.strip()] or list(DEFAULT_METEORA_URLS)
        return value

    @field_validator("report_title", mode="before")
    @classmethod
    def default_report_title(cls, value: Any) -> str:
        return "LP Pool Discovery" if value is None or value == "" else value


def deep_get(obj: Any, paths: list[str]) -> Any:
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return None


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").replace("$", "").strip()
        return float(value)
    except (TypeError, ValueError):
        return None


def as_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "pools", "whirlpools", "pairs", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        values = list(payload.values())
        if values and all(isinstance(v, dict) for v in values):
            return values
    return []


def fetch_json(url: str, timeout_sec: float) -> Any:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "Condor LP Pools Watcher/1.0"})
    with urlopen(req, timeout=timeout_sec) as response:
        return json.loads(response.read().decode("utf-8"))


async def fetch_first_working(urls: list[str], timeout_sec: float) -> tuple[Any | None, str | None, list[str]]:
    errors: list[str] = []

    def attempt() -> tuple[Any | None, str | None, list[str]]:
        for url in urls:
            try:
                return fetch_json(url, timeout_sec), url, errors
            except HTTPError as exc:
                errors.append(f"{url}: HTTP {exc.code}")
            except URLError as exc:
                errors.append(f"{url}: URL error {exc.reason}")
            except TimeoutError:
                errors.append(f"{url}: timeout")
            except json.JSONDecodeError:
                errors.append(f"{url}: invalid JSON")
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
        return None, None, errors

    return await asyncio.to_thread(attempt)


def normalize_pool(protocol: str, raw: dict[str, Any], fetched_at: float, default_fee_rate: float) -> dict[str, Any]:
    attrs = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    merged = {**raw, **attrs}
    token_a = deep_get(merged, ["tokenA.symbol", "token_a.symbol", "base.symbol", "mint_x_symbol", "tokenXSymbol", "tokenX.symbol"])
    token_b = deep_get(merged, ["tokenB.symbol", "token_b.symbol", "quote.symbol", "mint_y_symbol", "tokenYSymbol", "tokenY.symbol"])
    pair = deep_get(merged, ["pair", "symbol", "name", "poolName"])
    if pair and " / " in str(pair):
        pair = str(pair).replace(" / ", "/")
    if not pair and token_a and token_b:
        pair = f"{token_a}/{token_b}"

    liquidity = as_float(deep_get(merged, ["liquidity", "liquidityUsd", "liquidity_usd", "tvl", "tvlUsd", "tvl_usd", "reserveUSD", "reserve_in_usd", "stats.liquidity", "stats.tvl", "stats.tvlUsd"]))
    volume_24h = as_float(deep_get(merged, ["volume24h", "volume_24h", "volume_24h_usd", "trade_volume_24h", "volume", "stats.volume24h", "stats.24h.volume", "day.volume", "volume_usd.h24"]))
    fee_rate = as_float(deep_get(merged, ["feeRate", "fee_rate", "lpFeeRate", "baseFeeRate", "fee"]))
    if fee_rate is not None and fee_rate > 1:
        fee_rate = fee_rate / 10_000 if fee_rate > 100 else fee_rate / 100
    if fee_rate is None and volume_24h is not None:
        fee_rate = default_fee_rate

    fee_apr = as_float(deep_get(merged, ["feeApr", "fee_apr", "stats.feeApr"]))
    if fee_apr is not None and fee_apr > 10:
        fee_apr = fee_apr / 100
    fee_24h = as_float(deep_get(merged, ["fee24h", "fees24h", "fees_24h", "fee_24h", "fee_24h_usd", "stats.fee24h", "stats.fees24h", "stats.24h.fees", "day.fees", "trade_fee_24h", "fee_tvl_ratio.amount"]))
    if fee_24h is None and fee_apr is not None and liquidity is not None:
        fee_24h = liquidity * fee_apr / 365
    if fee_24h is None and volume_24h is not None and fee_rate is not None:
        fee_24h = volume_24h * fee_rate

    warnings: list[str] = []
    raw_warning = deep_get(merged, ["warning", "warnings", "apiWarning", "api_warning", "status.warning"])
    if isinstance(raw_warning, list):
        warnings.extend(str(w) for w in raw_warning if w)
    elif raw_warning:
        warnings.append(str(raw_warning))

    return {
        "protocol": protocol,
        "name": deep_get(merged, ["name", "poolName", "symbol", "pair"]) or pair or deep_get(merged, ["address", "poolAddress", "pool_address", "pubkey", "id", "pair_address"]),
        "pair": pair,
        "address": deep_get(merged, ["address", "poolAddress", "pool_address", "pubkey", "id", "pair_address"]),
        "fee_24h_usd": fee_24h,
        "volume_24h_usd": volume_24h,
        "fee_rate": fee_rate,
        "liquidity_usd": liquidity,
        "trend_pct_24h": as_float(deep_get(merged, ["priceChange24h", "price_change_24h", "priceChange24hPercent", "change24h", "stats.priceChange24h", "stats.24h.priceChange", "price_change_percentage.h24"])),
        "volatility_pct_24h": as_float(deep_get(merged, ["volatility24h", "volatility_24h", "stats.volatility24h"])),
        "warnings": warnings,
        "data_age_minutes": round((time.time() - fetched_at) / 60, 4),
        "raw_keys": sorted(merged.keys())[:30],
    }


def sort_key(pool: dict[str, Any]) -> float:
    return float(pool.get("fee_24h_usd") or 0) * 10 + float(pool.get("liquidity_usd") or 0) / 1_000_000


def money(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def input_parameter_rows(config: Config) -> list[dict[str, Any]]:
    return [
        {"Parameter": "protocols", "Value": ", ".join(config.protocols)},
        {"Parameter": "max_pools_per_protocol", "Value": config.max_pools_per_protocol},
        {"Parameter": "min_liquidity_usd", "Value": config.min_liquidity_usd},
        {"Parameter": "timeout_sec", "Value": config.timeout_sec},
        {"Parameter": "default_fee_rate", "Value": config.default_fee_rate},
        {"Parameter": "report_title", "Value": config.report_title},
        {"Parameter": "orca_urls", "Value": f"{len(config.orca_urls)} endpoint(s)"},
        {"Parameter": "meteora_urls", "Value": f"{len(config.meteora_urls)} endpoint(s)"},
    ]


def endpoint_rows(config: Config) -> list[dict[str, Any]]:
    return [{"Protocol": protocol, "Order": i, "URL": url} for protocol, urls in (("orca", config.orca_urls), ("meteora", config.meteora_urls)) for i, url in enumerate(urls, 1)]


def discovery_rows(pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"Pool": p.get("name"), "Protocol": p.get("protocol"), "Pair": p.get("pair") or "n/a", "24h Fees": money(p.get("fee_24h_usd")), "24h Volume": money(p.get("volume_24h_usd")), "Liquidity": money(p.get("liquidity_usd")), "24h Trend": pct(p.get("trend_pct_24h")), "Warnings": "none" if not p.get("warnings") else ", ".join(p.get("warnings", []))} for p in pools]


def format_text(result: dict[str, Any]) -> str:
    lines = ["LP Pool Discovery", f"Pools discovered: {result['count']}", "", "SOURCES"]
    lines.extend((f"- {p}: {u}" for p, u in result["sources"].items()) or ["No working sources found."])
    lines.extend(["", "TOP CANDIDATES"])
    if result["pools"]:
        for i, pool in enumerate(result["pools"][:10], 1):
            lines.append(f"{i}. {pool.get('name')} ({pool.get('protocol')}) | fees {money(pool.get('fee_24h_usd'))} | liq {money(pool.get('liquidity_usd'))} | trend {pct(pool.get('trend_pct_24h'))}")
    else:
        lines.append("No candidate pools found.")
    if result["data_gaps"]:
        lines.extend(["", "DATA GAPS", *[f"- {gap}" for gap in result["data_gaps"][:10]]])
    failed_errors = [f"{p}: {len(errors)} failed endpoint(s)" for p, errors in result["api_errors"].items() if errors]
    if failed_errors:
        lines.extend(["", "API ERRORS", *[f"- {item}" for item in failed_errors]])
    lines.extend(["", "No trades, swaps, LP changes, or executors were created."])
    return "\n".join(lines)


async def save_report(result: dict[str, Any], config: Config) -> None:
    try:
        from condor.reports import ReportBuilder
        builder = ReportBuilder(config.report_title)
        builder.source("routine", "discover_lp_pools").tags(["lp", "discovery", "orca", "meteora"])
        builder.markdown(f"Discovered {result['count']} candidate pools across: " + ", ".join(config.protocols))
        builder.markdown("### Input Parameters")
        builder.table(input_parameter_rows(config))
        builder.markdown("### Input Endpoints")
        builder.table(endpoint_rows(config))
        if result["pools"]:
            builder.markdown("### Candidate Pools")
            builder.table(discovery_rows(result["pools"]))
        if result["sources"]:
            builder.markdown("### Sources\n" + "\n".join(f"- {p}: {u}" for p, u in result["sources"].items()))
        if result["data_gaps"]:
            builder.markdown("### Data Gaps\n" + "\n".join(f"- {gap}" for gap in result["data_gaps"][:30]))
        error_lines = []
        for protocol, errors in result["api_errors"].items():
            if errors:
                error_lines.append(f"- {protocol}: {len(errors)} failed endpoint(s)")
                error_lines.extend(f"  - {err}" for err in errors[:5])
        if error_lines:
            builder.markdown("### API Errors\n" + "\n".join(error_lines))
        builder.markdown(f"### Discovery Config\n- Minimum liquidity: {money(config.min_liquidity_usd)}\n- Max pools per protocol: {config.max_pools_per_protocol}\n- Timeout: {config.timeout_sec:.0f}s\n- Default fee rate proxy: {config.default_fee_rate * 100:.2f}%")
        await builder.save()
    except Exception as exc:
        logger.warning("Discovery report generation failed: %s", exc)


async def run(config: Config, context: Any) -> dict[str, Any]:
    protocols = {p.lower().strip() for p in config.protocols}
    discovered: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    data_gaps: list[str] = []
    api_errors: dict[str, list[str]] = {}

    for protocol, urls in {"orca": config.orca_urls, "meteora": config.meteora_urls}.items():
        if protocol not in protocols:
            continue
        payload, source_url, errors = await fetch_first_working(urls, config.timeout_sec)
        api_errors[protocol] = errors
        if source_url:
            sources[protocol] = source_url
        if payload is None:
            data_gaps.append(f"{protocol}: no working API endpoint")
            continue
        rows = [row for row in as_list(payload) if isinstance(row, dict)]
        if not rows:
            data_gaps.append(f"{protocol}: response did not contain a pool list")
            continue
        fetched_at = time.time()
        normalized = [normalize_pool(protocol, row, fetched_at, config.default_fee_rate) for row in rows]
        candidates = [p for p in normalized if (p.get("liquidity_usd") or 0) >= config.min_liquidity_usd] or normalized
        candidates = sorted(candidates, key=sort_key, reverse=True)[: config.max_pools_per_protocol]
        discovered.extend(candidates)
        for pool in candidates[:10]:
            missing = []
            if pool.get("fee_24h_usd") is None:
                missing.append("fee")
            if pool.get("liquidity_usd") is None:
                missing.append("liquidity")
            if pool.get("trend_pct_24h") is None:
                missing.append("trend")
            if missing:
                data_gaps.append(f"{protocol} {pool.get('name') or pool.get('address')}: missing {', '.join(missing)}")

    result = {"pools": discovered, "count": len(discovered), "sources": sources, "api_errors": api_errors, "data_gaps": data_gaps[:30], "next_step": "Pass `pools` into optimal_pool_scorer as config.pools."}
    await save_report(result, config)
    result["text"] = format_text(result)
    return result
