"""Private TradFi market adapters for ``market_signal_source``."""

from __future__ import annotations

import asyncio
import csv
import io
import os
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

from agents.market_reporter.routines._crypto_metrics import (
    calculate_ohlcv_metrics,
    compact_series,
)
from agents.market_reporter.routines._evidence import evidence_id, safe_float
from agents.market_reporter.routines._http import FetchResult, fetch_json, fetch_text
from agents.market_reporter.routines._identity import (
    TRADFI_BENCHMARKS,
    TRADFI_LARGE_CAPS,
    TRADFI_SECTORS,
    tradfi_symbols,
)
from agents.market_reporter.routines._tradfi_metrics import (
    breadth_summary,
    relative_strength,
    treasury_curve,
)

FRED_SERIES = {
    "vix": "VIXCLS",
    "high_yield_spread": "BAMLH0A0HYM2",
    "broad_dollar": "DTWEXBGS",
}


async def collect_tradfi(
    *,
    focus_assets: list[str],
    history_days: int,
) -> tuple[list[dict[str, Any]], list[FetchResult], dict[str, Any], list[str]]:
    tasks = []
    meta = []
    for symbol in tradfi_symbols(focus_assets):
        tasks.append(
            fetch_text(
                "stooq",
                "https://stooq.com/q/d/l/",
                params={"s": f"{symbol.lower()}.us", "i": "d"},
            )
        )
        meta.append(("ohlcv", symbol))
    tasks.extend(
        [
            fetch_text(
                "treasury",
                "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml",
                params={
                    "data": "daily_treasury_yield_curve",
                    "field_tdr_date_value": str(datetime.now(timezone.utc).year),
                },
            ),
            fetch_text(
                "cftc",
                "https://www.cftc.gov/dea/newcot/FinFutWk.txt",
                retry=False,
            ),
        ]
    )
    meta.extend([("treasury", ""), ("cftc", "")])
    fred_key = os.environ.get("FRED_API_KEY", "").strip()
    if fred_key:
        for label, series_id in FRED_SERIES.items():
            tasks.append(
                fetch_json(
                    "fred",
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={
                        "series_id": series_id,
                        "api_key": fred_key,
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": min(history_days, 365),
                    },
                    retry=False,
                )
            )
            meta.append(("fred", label))
    results = list(await asyncio.gather(*tasks))

    items = []
    market_rows = []
    warnings = [] if fred_key else ["fred_optional_key_not_configured"]
    normalized_results = list(results)
    for index, ((kind, identity), result) in enumerate(zip(meta, results)):
        if result.status != "complete":
            continue
        if kind == "ohlcv":
            item = _ohlcv_item(identity, result, history_days)
            if item:
                items.append(item)
                market_rows.append(item)
            else:
                normalized_results[index] = replace(
                    result,
                    status="unavailable",
                    error="unparseable_ohlcv",
                )
                warnings.append(f"stooq_unparseable:{identity}")
        elif kind == "treasury":
            item = _treasury_item(result)
            if item:
                items.append(item)
            else:
                normalized_results[index] = replace(
                    result,
                    status="unavailable",
                    error="unparseable_treasury_curve",
                )
                warnings.append("treasury_curve_unparseable")
        elif kind == "cftc":
            items.extend(_cftc_items(result))
        elif kind == "fred":
            item = _fred_item(identity, result)
            if item:
                items.append(item)
    symbols = {item.get("symbol") for item in market_rows}
    spy = next(
        (item for item in market_rows if item.get("symbol") == "SPY"),
        None,
    )
    spy_return = (spy.get("metrics") or {}).get("return_7d_pct") if spy else None
    for item in market_rows:
        item["metrics"]["relative_strength_vs_spy_7d_pct"] = relative_strength(
            item["metrics"].get("return_7d_pct"),
            spy_return,
        )
    item_families = {item.get("source_family") for item in items}
    item_metrics = {item.get("metric") for item in items}
    cross_asset_components = set()
    if "vix" in item_metrics:
        cross_asset_components.add("volatility")
    if "high_yield_spread" in item_metrics or "HYG" in symbols:
        cross_asset_components.add("credit")
    if "broad_dollar" in item_metrics or "UUP" in symbols:
        cross_asset_components.add("dollar")
    coverage = {
        "spy_qqq_present": {"SPY", "QQQ"}.issubset(symbols),
        "sector_valid_count": len(set(TRADFI_SECTORS) & symbols),
        "sector_configured_count": len(TRADFI_SECTORS),
        "benchmark_valid_count": len(set(TRADFI_BENCHMARKS) & symbols),
        "treasury_curve_present": "treasury_curve" in item_metrics,
        "cross_asset_components": sorted(cross_asset_components),
        "cross_asset_component_count": len(cross_asset_components),
        "cftc_positioning_present": "positioning" in item_families,
        "sector_breadth": breadth_summary(
            [row for row in market_rows if row.get("symbol") in TRADFI_SECTORS]
        ),
        "large_cap_breadth": breadth_summary(
            [row for row in market_rows if row.get("symbol") in TRADFI_LARGE_CAPS]
        ),
        "price_history_available": bool(market_rows),
        "price_provider_status": "available" if market_rows else "unavailable",
        "keyless_mode": not bool(fred_key),
        "fred_available": bool(fred_key),
    }
    return items, normalized_results, coverage, warnings


def _ohlcv_item(
    symbol: str, result: FetchResult, history_days: int
) -> dict[str, Any] | None:
    rows = []
    for row in csv.DictReader(io.StringIO(result.text or "")):
        close = safe_float(row.get("Close"))
        if close is None:
            continue
        rows.append(
            {
                "timestamp": str(row.get("Date") or ""),
                "open": safe_float(row.get("Open")),
                "high": safe_float(row.get("High")),
                "low": safe_float(row.get("Low")),
                "close": close,
                "volume": safe_float(row.get("Volume")) or 0.0,
            }
        )
    rows = rows[-history_days:]
    metrics = calculate_ohlcv_metrics(rows)
    if not metrics:
        return None
    source_time = metrics["last_observation"]
    return {
        "evidence_id": evidence_id("stooq", symbol, source_time),
        "provider_id": "stooq",
        "source_family": "market",
        "asset_class": "tradfi",
        "symbol": symbol,
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        "market_status": "unknown",
        "metrics": metrics,
        "series": compact_series(
            rows,
            symbol=symbol,
            maximum=(
                min(history_days, 180)
                if symbol in {"SPY", "QQQ", "TLT", "HYG", "UUP"}
                else 0
            ),
        ),
    }


def _treasury_item(result: FetchResult) -> dict[str, Any] | None:
    try:
        root = ElementTree.fromstring(result.text or "")
    except ElementTree.ParseError:
        return None
    entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if not entries:
        return None
    latest = entries[-1]
    values: dict[str, float | None] = {}
    mapping = {
        "BC_3MONTH": "3m",
        "BC_2YEAR": "2y",
        "BC_10YEAR": "10y",
        "BC_30YEAR": "30y",
    }
    source_time = ""
    for element in latest.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local == "NEW_DATE" and element.text:
            source_time = element.text
        if local in mapping:
            values[mapping[local]] = safe_float(element.text)
    if not values:
        return None
    return {
        "evidence_id": evidence_id("treasury", "yield_curve", source_time),
        "provider_id": "treasury",
        "source_family": "macro",
        "metric": "treasury_curve",
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        **treasury_curve(values),
    }


def _cftc_items(result: FetchResult) -> list[dict[str, Any]]:
    output = []
    for row in csv.reader(io.StringIO(result.text or "")):
        if len(row) < 17:
            continue
        name = row[0].strip()
        if not any(
            term in name.upper()
            for term in ("S&P", "NASDAQ", "DOLLAR", "TREASURY", "VIX")
        ):
            continue
        source_time = row[2].strip()
        output.append(
            {
                "evidence_id": evidence_id("cftc", name, source_time),
                "provider_id": "cftc",
                "source_family": "positioning",
                "contract": name[:160],
                "source_time": source_time,
                "retrieved_at": result.retrieved_at,
                "asset_manager_net": _net(row[11], row[12]),
                "leveraged_fund_net": _net(row[14], row[15]),
                "publication_lag": "weekly",
            }
        )
    return output[:20]


def _fred_item(label: str, result: FetchResult) -> dict[str, Any] | None:
    for observation in (result.data or {}).get("observations") or []:
        value = safe_float(observation.get("value"))
        if value is None:
            continue
        source_time = str(observation.get("date") or "")
        return {
            "evidence_id": evidence_id("fred", label, source_time),
            "provider_id": "fred",
            "source_family": "macro",
            "metric": label,
            "source_time": source_time,
            "retrieved_at": result.retrieved_at,
            "value": value,
        }
    return None


def _net(long_value: Any, short_value: Any) -> float | None:
    long = safe_float(long_value)
    short = safe_float(short_value)
    return long - short if long is not None and short is not None else None
