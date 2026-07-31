"""Private TradFi market adapters for the concurrent data gatherer."""

from __future__ import annotations

import asyncio
import csv
import io
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
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
    TRADFI_SECTORS,
    TRADFI_SP500_STOCKS,
    tradfi_symbols,
)
from agents.market_reporter.routines._tradfi_metrics import (
    breadth_summary,
    relative_strength,
    treasury_curve,
)

FRED_CSV_SERIES = {
    "vix": "VIXCLS",
    "high_yield_spread": "BAMLH0A0HYM2",
    "broad_dollar": "DTWEXBGS",
    "yield_3m": "DGS3MO",
    "yield_2y": "DGS2",
    "yield_10y": "DGS10",
    "yield_30y": "DGS30",
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
            fetch_json(
                "robinhood_equity",
                f"https://api.robinhood.com/marketdata/historicals/{symbol}/",
                params={
                    "interval": "day",
                    "span": "5year" if history_days > 250 else "year",
                    "bounds": "regular",
                },
                retry=False,
            )
        )
        meta.append(("ohlcv", symbol))
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=max(90, history_days * 2))
    for label, series_id in FRED_CSV_SERIES.items():
        tasks.append(
            fetch_text(
                "fred_csv",
                "https://fred.stlouisfed.org/graph/fredgraph.csv",
                params={
                    "id": series_id,
                    "cosd": start.isoformat(),
                    "coed": today.isoformat(),
                },
                retry=False,
            )
        )
        meta.append(("fred_csv", label))
    tasks.append(
        fetch_text(
            "cftc",
            "https://www.cftc.gov/dea/newcot/FinFutWk.txt",
            retry=False,
        )
    )
    meta.append(("cftc", ""))
    results = list(await asyncio.gather(*tasks))

    items = []
    market_rows = []
    warnings = []
    normalized_results = list(results)
    yield_points: dict[str, float | None] = {}
    yield_times: dict[str, str] = {}
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
                warnings.append(f"robinhood_equity_unparseable:{identity}")
        elif kind == "fred_csv":
            observations = _fred_csv_observations(result, identity)
            if not observations:
                normalized_results[index] = replace(
                    result,
                    status="unavailable",
                    error="unparseable_fred_csv",
                )
                warnings.append(f"fred_csv_unparseable:{identity}")
                continue
            if identity.startswith("yield_"):
                tenor = identity.removeprefix("yield_")
                yield_points[tenor] = observations[-1]["value"]
                yield_times[tenor] = observations[-1]["date"]
            else:
                items.append(_fred_csv_item(identity, observations, result))
        elif kind == "cftc":
            items.extend(_cftc_items(result))
    if yield_points:
        items.append(_fred_curve_item(yield_points, yield_times, results))
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
    sector_breadth_item = _sector_breadth_item(market_rows)
    if sector_breadth_item:
        items.append(sector_breadth_item)
    sp500_breadth_item = _sp500_sample_breadth_item(market_rows)
    if sp500_breadth_item:
        items.append(sp500_breadth_item)
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
        "spy_present": "SPY" in symbols,
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
        "sp500_sample_valid_count": len(set(TRADFI_SP500_STOCKS) & symbols),
        "sp500_sample_configured_count": len(TRADFI_SP500_STOCKS),
        "sp500_stock_breadth": breadth_summary(
            [row for row in market_rows if row.get("symbol") in TRADFI_SP500_STOCKS]
        ),
        "price_history_available": bool(market_rows),
        "price_provider_status": "available" if market_rows else "unavailable",
        "keyless_mode": True,
        "fred_available": bool(
            {"vix", "high_yield_spread", "broad_dollar"} & item_metrics
        ),
        "price_provider_id": "robinhood_equity",
        "macro_provider_id": "fred_csv",
    }
    return items, normalized_results, coverage, warnings


def _sector_breadth_item(
    market_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Create one auditable aggregate from retained sector-ETF observations."""
    sectors = sorted(
        (
            item
            for item in market_rows
            if str(item.get("symbol") or "") in TRADFI_SECTORS
            and safe_float((item.get("metrics") or {}).get("return_7d_pct")) is not None
        ),
        key=lambda item: str(item.get("symbol") or ""),
    )
    if not sectors:
        return None
    positive = [
        item
        for item in sectors
        if (safe_float((item.get("metrics") or {}).get("return_7d_pct")) or 0.0) > 0
    ]
    negative = [
        item
        for item in sectors
        if (safe_float((item.get("metrics") or {}).get("return_7d_pct")) or 0.0) < 0
    ]
    source_time = max(
        (str(item.get("source_time") or "") for item in sectors),
        default="",
    )
    symbols = [str(item.get("symbol") or "") for item in sectors]
    return {
        "evidence_id": evidence_id(
            "robinhood_equity",
            "tradfi_sector_breadth:" + ",".join(symbols),
            source_time,
        ),
        "provider_id": "robinhood_equity",
        "source_family": "derived_market",
        "metric": "tradfi_sector_breadth",
        "title": "Derived U.S. sector-ETF seven-day breadth",
        "source_time": source_time,
        "configured_symbols": symbols,
        "configured_count": len(TRADFI_SECTORS),
        "observed_count": len(sectors),
        "positive_7d_count": len(positive),
        "positive_7d_pct": round(len(positive) / len(sectors) * 100, 2),
        "positive_symbols": [str(item.get("symbol") or "") for item in positive],
        "negative_7d_count": len(negative),
        "negative_symbols": [str(item.get("symbol") or "") for item in negative],
        "underlying_evidence_ids": [
            str(item.get("evidence_id")) for item in sectors if item.get("evidence_id")
        ],
        "derivation": (
            "Count of retained sector ETFs with a strictly positive deterministic "
            "seven-day close-to-close return."
        ),
    }


def _sp500_sample_breadth_item(
    market_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Summarize the observed representative S&P 500 stock sample."""
    stocks = [
        item
        for item in market_rows
        if str(item.get("symbol") or "") in TRADFI_SP500_STOCKS
        and safe_float((item.get("metrics") or {}).get("return_7d_pct")) is not None
    ]
    if not stocks:
        return None
    positive = [
        item
        for item in stocks
        if (safe_float((item.get("metrics") or {}).get("return_7d_pct")) or 0.0) > 0
    ]
    negative = [
        item
        for item in stocks
        if (safe_float((item.get("metrics") or {}).get("return_7d_pct")) or 0.0) < 0
    ]
    source_time = max(
        (str(item.get("source_time") or "") for item in stocks),
        default="",
    )
    symbols = [str(item.get("symbol") or "") for item in stocks]
    return {
        "evidence_id": evidence_id(
            "robinhood_equity",
            "tradfi_sp500_sample_breadth:" + ",".join(symbols),
            source_time,
        ),
        "provider_id": "robinhood_equity",
        "source_family": "derived_market",
        "metric": "tradfi_sp500_sample_breadth",
        "title": "Representative S&P 500 stock-sample seven-day breadth",
        "source_time": source_time,
        "configured_symbols": list(TRADFI_SP500_STOCKS),
        "configured_count": len(TRADFI_SP500_STOCKS),
        "observed_count": len(stocks),
        "positive_7d_count": len(positive),
        "positive_7d_pct": round(len(positive) / len(stocks) * 100, 2),
        "positive_symbols": [str(item.get("symbol") or "") for item in positive],
        "negative_7d_count": len(negative),
        "negative_symbols": [str(item.get("symbol") or "") for item in negative],
        "underlying_evidence_ids": [
            str(item.get("evidence_id")) for item in stocks if item.get("evidence_id")
        ],
        "derivation": (
            "Count of retained representative S&P 500 stocks with a strictly "
            "positive deterministic seven-day close-to-close return."
        ),
    }


def _ohlcv_item(
    symbol: str, result: FetchResult, history_days: int
) -> dict[str, Any] | None:
    rows = []
    for row in (result.data or {}).get("historicals") or []:
        close = safe_float(row.get("close_price"))
        if close is None:
            continue
        rows.append(
            {
                "timestamp": str(row.get("begins_at") or ""),
                "open": safe_float(row.get("open_price")),
                "high": safe_float(row.get("high_price")),
                "low": safe_float(row.get("low_price")),
                "close": close,
                "volume": safe_float(row.get("volume")) or 0.0,
            }
        )
    rows = rows[-history_days:]
    metrics = calculate_ohlcv_metrics(rows)
    if not metrics:
        return None
    source_time = metrics["last_observation"]
    return {
        "evidence_id": evidence_id("robinhood_equity", symbol, source_time),
        "provider_id": "robinhood_equity",
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
        asset_manager_net = _net(row[11], row[12])
        leveraged_fund_net = _net(row[14], row[15])
        output.append(
            {
                "evidence_id": evidence_id("cftc", name, source_time),
                "provider_id": "cftc",
                "source_family": "positioning",
                "metric": "cftc_positioning",
                "title": f"CFTC weekly positioning — {name[:160]}",
                "symbol": name[:160],
                "contract": name[:160],
                "source_time": source_time,
                "retrieved_at": result.retrieved_at,
                "asset_manager_net": asset_manager_net,
                "leveraged_fund_net": leveraged_fund_net,
                "value": leveraged_fund_net,
                "unit": "contracts_net",
                "publication_lag": "weekly",
                "url": result.url,
            }
        )
    return output[:20]


def _fred_csv_observations(
    result: FetchResult,
    label: str,
) -> list[dict[str, Any]]:
    rows = []
    for row in csv.DictReader(io.StringIO(result.text or "")):
        value = safe_float(
            next((item for key, item in row.items() if key != "observation_date"), None)
        )
        observed = str(row.get("observation_date") or "")
        if value is None or not observed:
            continue
        rows.append({"date": observed, "value": value, "label": label})
    return rows


def _fred_csv_item(
    label: str,
    observations: list[dict[str, Any]],
    result: FetchResult,
) -> dict[str, Any]:
    latest = observations[-1]
    return {
        "evidence_id": evidence_id("fred_csv", label, latest["date"]),
        "provider_id": "fred_csv",
        "source_family": "macro",
        "metric": label,
        "source_time": latest["date"],
        "retrieved_at": result.retrieved_at,
        "value": latest["value"],
        "change_7d": _dated_change(observations, 7),
        "change_30d": _dated_change(observations, 30),
    }


def _fred_curve_item(
    points: dict[str, float | None],
    source_times: dict[str, str],
    results: list[FetchResult],
) -> dict[str, Any]:
    source_time = max(source_times.values(), default="")
    retrieved_at = max(
        (result.retrieved_at for result in results if result.provider_id == "fred_csv"),
        default="",
    )
    return {
        "evidence_id": evidence_id("fred_csv", "yield_curve", source_time),
        "provider_id": "fred_csv",
        "source_family": "macro",
        "metric": "treasury_curve",
        "source_time": source_time,
        "retrieved_at": retrieved_at,
        "point_source_times": source_times,
        **treasury_curve(points),
    }


def _dated_change(observations: list[dict[str, Any]], days: int) -> float | None:
    latest = observations[-1]
    try:
        cutoff = date.fromisoformat(latest["date"]) - timedelta(days=days)
    except ValueError:
        return None
    prior = None
    for row in reversed(observations[:-1]):
        try:
            observed = date.fromisoformat(row["date"])
        except (TypeError, ValueError):
            continue
        if observed <= cutoff:
            prior = row
            break
    if prior is None:
        return None
    return round(latest["value"] - prior["value"], 4)


def _net(long_value: Any, short_value: Any) -> float | None:
    long = safe_float(long_value)
    short = safe_float(short_value)
    return long - short if long is not None and short is not None else None
