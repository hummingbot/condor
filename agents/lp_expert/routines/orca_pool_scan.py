"""Read-only complete-union Orca pool scan with neutral MCDA evidence and report."""

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from agents.lp_expert.routines import _orca_pool_metrics as metrics

CATEGORY = "Orca LP Intelligence"
BASE_URL = "https://api.orca.so/v2/solana"
POOL_PATH = "/v2/solana/pools"
CATEGORIES = (
    "stablecoin",
    "liquid_staking_token",
    "utility",
    "governance",
    "memecoin",
)
LENSES = ("yieldovertvl24h", "yieldovertvl7d", "volume24h", "volume7d")
WINDOWS = ("1h", "4h", "24h", "7d")
QUALIFICATION = (
    "Orca categories classify pools; they are not independent token-security "
    "certification. Rankings cover the fetched category/lens union only."
)


class Config(BaseModel):
    """Rank the Orca discovery union and save an inspectable report."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: StrictInt = Field(gt=0)


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


def _fetch_json(url: str) -> dict[str, Any]:
    _validate_url(url)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Condor-lp-expert/1.0",
        },
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
    raise RuntimeError("unreachable retry state")


def _request_specs() -> list[tuple[str, str, str]]:
    result = []
    for category in CATEGORIES:
        for lens in LENSES:
            query = urlencode(
                {
                    "sortBy": lens,
                    "sortDirection": "desc",
                    "stats": ",".join(WINDOWS),
                    "size": 100,
                    "minTvl": 0,
                    "categories": category,
                }
            )
            url = f"{BASE_URL}/pools?{query}"
            _validate_url(url)
            result.append((category, lens, url))
    return result


def _error_text(error: BaseException) -> str:
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, URLError):
        return f"URL error: {error.reason}"
    return f"{type(error).__name__}: {error}"


def _report_rows(recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in recommendations:
        components = item["mcda"]["components"]
        categories = item["categories"]
        rows.append(
            {
                "rank": item["neutral_rank"],
                "pair": item["trading_pair"],
                "pool": item["pool_address"],
                "base_symbol": item["token_a"]["symbol"],
                "base_mint": item["token_a"]["mint"],
                "base_decimals": item["token_a"]["decimals"],
                "quote_symbol": item["token_b"]["symbol"],
                "quote_mint": item["token_b"]["mint"],
                "quote_decimals": item["token_b"]["decimals"],
                "price": item["price"],
                "tick_spacing": item["tick_spacing"],
                "mcda": item["mcda"]["score"],
                "tvl_usd": item["tvl_usd"],
                "fees_1h": item["fees_usd"]["1h"],
                "fees_4h": item["fees_usd"]["4h"],
                "fees_24h": item["fees_usd"]["24h"],
                "fees_7d": item["fees_usd"]["7d"],
                "volume_1h": item["volume_usd"]["1h"],
                "volume_4h": item["volume_usd"]["4h"],
                "volume_24h": item["volume_usd"]["24h"],
                "volume_7d": item["volume_usd"]["7d"],
                "fee_hour_1h": item["hourly_fee_yield"]["1h"],
                "fee_hour_4h": item["hourly_fee_yield"]["4h"],
                "fee_hour_24h": item["hourly_fee_yield"]["24h"],
                "fee_hour_7d": item["hourly_fee_yield"]["7d"],
                "sustainable_fee_hour": item["sustainable_fee_yield_per_hour"],
                "turnover_1h": item["turnover"]["1h"],
                "turnover_4h": item["turnover"]["4h"],
                "turnover_24h": item["turnover"]["24h"],
                "turnover_7d": item["turnover"]["7d"],
                "acceleration_1h": item["fee_yield_acceleration"]["1h"][
                    "difference_vs_24h"
                ],
                "price_change_1h": item["price_change"]["1h"],
                "price_change_4h": item["price_change"]["4h"],
                "price_change_24h": item["price_change"]["24h"],
                "price_change_7d": item["price_change"]["7d"],
                "fee_productivity": components["fee_productivity"],
                "recent_activity": components["recent_activity"],
                "price_stability": components["price_stability"],
                "liquidity_depth": components["liquidity_depth"],
                "execution_simplicity": components["execution_simplicity"],
                "liquidity_band": categories["liquidity_band"],
                "volatility_band": categories["volatility_band"],
                "fee_regime": categories["fee_regime"],
                "fee_persistence": categories["fee_persistence"],
                "complexity": categories["execution_complexity"],
                "confidence": categories["evidence_confidence"],
            }
        )
    return rows


async def _save_report(payload: dict[str, Any]) -> str:
    from condor.reports import ReportBuilder

    coverage = payload["source_coverage"]
    universe = payload["universe"]
    builder = ReportBuilder("Orca LP Pool Scan")
    builder.source("routine", "orca_pool_scan").tags(
        ["lp-expert", "orca", "pool-scan", "mcda"]
    )
    builder.kpi("Status", payload["status"])
    builder.kpi(
        "Source coverage",
        f"{coverage['completed_requests']}/{coverage['required_requests']}",
    )
    builder.kpi("Valid pools", str(universe["valid_unique_pools"]))
    builder.kpi("Returned", str(universe["returned_recommendations"]))
    builder.markdown(
        f"Observed: {payload['observed_at']}\n\n{payload['qualification']}"
    )
    builder.section(
        "Ranked candidates",
        "Neutral MCDA is comparison evidence, not an automatic pool selection.",
    )
    rows = _report_rows(payload["recommendations"])
    builder.table(
        rows,
        columns=[
            "rank",
            "pair",
            "pool",
            "base_symbol",
            "base_mint",
            "base_decimals",
            "quote_symbol",
            "quote_mint",
            "quote_decimals",
            "price",
            "tick_spacing",
            "mcda",
            "tvl_usd",
            "liquidity_band",
            "volatility_band",
            "fee_regime",
            "fee_persistence",
            "complexity",
            "confidence",
        ],
    )
    builder.section("Yield and activity evidence")
    builder.table(
        rows,
        columns=[
            "rank",
            "pair",
            "fees_1h",
            "fees_4h",
            "fees_24h",
            "fees_7d",
            "volume_1h",
            "volume_4h",
            "volume_24h",
            "volume_7d",
            "fee_hour_1h",
            "fee_hour_4h",
            "fee_hour_24h",
            "fee_hour_7d",
            "sustainable_fee_hour",
            "turnover_1h",
            "turnover_4h",
            "turnover_24h",
            "turnover_7d",
            "acceleration_1h",
            "price_change_1h",
            "price_change_4h",
            "price_change_24h",
            "price_change_7d",
        ],
    )
    builder.section("MCDA components")
    builder.table(
        rows,
        columns=[
            "rank",
            "pair",
            "fee_productivity",
            "recent_activity",
            "price_stability",
            "liquidity_depth",
            "execution_simplicity",
        ],
    )
    builder.section("Technical rejections")
    builder.table(
        [
            {"reason": reason, "count": count}
            for reason, count in payload["technical_rejections"].items()
        ],
        columns=["reason", "count"],
    )
    builder.section("Source requests")
    builder.table(
        coverage["requests"],
        columns=["source", "status", "records", "error"],
    )
    return await builder.save()


async def run(config: Config, context: Any) -> str:
    specs = _request_specs()
    responses = await asyncio.gather(
        *(asyncio.to_thread(_fetch_json, url) for _, _, url in specs),
        return_exceptions=True,
    )
    normalized: list[dict[str, Any]] = []
    rejections: Counter[str] = Counter()
    requests = []
    source_index = 0
    for (category, lens, _), response in zip(specs, responses, strict=True):
        name = f"{category}:{lens}"
        if isinstance(response, BaseException):
            requests.append(
                {"source": name, "status": "failed", "error": _error_text(response)}
            )
            continue
        records = response["data"]
        requests.append({"source": name, "status": "complete", "records": len(records)})
        for raw in records:
            source_index += 1
            record, reason = metrics.normalize_record(raw, category, lens, source_index)
            if record is not None:
                normalized.append(record)
            else:
                rejections[reason or "unknown_rejection"] += 1
    deduplicated, duplicate_rejections = metrics.deduplicate(normalized)
    rejections.update(duplicate_rejections)
    ranked = metrics.rank_pools(deduplicated)
    failed = [item["source"] for item in requests if item["status"] == "failed"]
    complete = not failed
    payload = {
        "status": "complete" if complete else "incomplete",
        "deployable": complete and bool(ranked),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "qualification": QUALIFICATION,
        "metric_definitions": {
            "fee_yield_<window>": "fees_<window> / tvl_usd",
            "hourly_fee_yield": "fee_yield divided by window hours (1, 4, 24, 168)",
            "sustainable_fee_yield_per_hour": "min(hourly_4h, hourly_24h, hourly_7d)",
            "acceleration_difference": "hourly short-window yield minus hourly_24h",
            "acceleration_ratio": "hourly short-window yield / hourly_24h; null when baseline is zero",
        },
        "source_coverage": {
            "scope": "first 100 Orca rows per required category/lens combination",
            "categories": list(CATEGORIES),
            "lenses": list(LENSES),
            "stats": list(WINDOWS),
            "required_requests": len(specs),
            "completed_requests": len(specs) - len(failed),
            "failed_requests": failed,
            "requests": requests,
        },
        "universe": {
            "raw_records": sum(
                item.get("records", 0)
                for item in requests
                if item["status"] == "complete"
            ),
            "normalized_records_before_dedupe": len(normalized),
            "valid_unique_pools": len(ranked),
            "returned_recommendations": min(config.limit, len(ranked)),
        },
        "technical_rejections": dict(sorted(rejections.items())),
        "recommendations": ranked[: config.limit],
    }
    payload["report_id"] = None
    payload["report_error"] = None
    try:
        payload["report_id"] = await _save_report(payload)
    except Exception as exc:
        payload["report_error"] = _error_text(exc)
    output = {
        "report_id": payload.pop("report_id"),
        "report_error": payload.pop("report_error"),
        **payload,
    }
    return json.dumps(output, separators=(",", ":"), sort_keys=False)
