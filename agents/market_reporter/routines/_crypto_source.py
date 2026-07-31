"""Private Crypto market adapters for the concurrent data gatherer."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from agents.market_reporter.routines._crypto_catalog import (
    MAX_DYNAMIC_SYMBOLS,
    collect_catalog,
    dynamic_symbols,
)
from agents.market_reporter.routines._crypto_metrics import (
    calculate_ohlcv_metrics,
    compact_series,
)
from agents.market_reporter.routines._evidence import evidence_id, safe_float
from agents.market_reporter.routines._http import FetchResult, fetch_json
from agents.market_reporter.routines._identity import CRYPTO_UNIVERSE, crypto_symbols

KRAKEN_PAIRS = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "BNB": "BNBUSD",
    "XRP": "XRPUSD",
    "ADA": "ADAUSD",
    "DOGE": "DOGEUSD",
    "AVAX": "AVAXUSD",
    "LINK": "LINKUSD",
    "SUI": "SUIUSD",
}


async def collect_crypto(
    *,
    scope: str,
    focus_assets: list[str],
    history_days: int,
) -> tuple[list[dict[str, Any]], list[FetchResult], dict[str, Any]]:
    catalog_items, catalog_results, catalog_coverage = await collect_catalog(
        include_meta_categories=scope == "memecoin",
    )
    if scope == "memecoin":
        candidate_symbols = ["BTC", "ETH"]
    elif catalog_coverage["dynamic_universe_available"]:
        candidate_symbols = dynamic_symbols(
            catalog_items,
            focus_assets,
            maximum=MAX_DYNAMIC_SYMBOLS + 6,
        )
    else:
        candidate_symbols = crypto_symbols(focus_assets)
    tasks = []
    meta = []
    for symbol in candidate_symbols:
        tasks.append(
            fetch_json(
                "binance_spot",
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": CRYPTO_UNIVERSE.get(symbol, f"{symbol}USDT"),
                    "interval": "1d",
                    "limit": history_days,
                },
            )
        )
        meta.append(("ohlcv", symbol))
    for symbol in ("BTC", "ETH"):
        tasks.append(
            fetch_json(
                "binance_futures",
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": CRYPTO_UNIVERSE.get(symbol, f"{symbol}USDT")},
            )
        )
        meta.append(("funding", symbol))
        tasks.append(
            fetch_json(
                "binance_futures",
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": CRYPTO_UNIVERSE.get(symbol, f"{symbol}USDT")},
            )
        )
        meta.append(("open_interest", symbol))
    tasks.extend(
        [
            fetch_json(
                "alternative_fng",
                "https://api.alternative.me/fng/",
                params={"limit": min(history_days, 90), "format": "json"},
            ),
            fetch_json(
                "defillama",
                "https://stablecoins.llama.fi/stablecoins",
                params={"includePrices": "true"},
            ),
            fetch_json(
                "defillama",
                "https://stablecoins.llama.fi/stablecoincharts/all",
            ),
            fetch_json(
                "defillama",
                "https://api.llama.fi/v2/historicalChainTvl",
            ),
        ]
    )
    meta.extend(
        [
            ("fear_greed", ""),
            ("stablecoins", ""),
            ("stablecoin_history", ""),
            ("defi_tvl", ""),
        ]
    )
    preliminary_results = catalog_results
    gathered_results = list(await asyncio.gather(*tasks))

    items = list(catalog_items)
    market_rows = []
    failed_spot_symbols = []
    unusable_spot_symbols = []
    for (kind, symbol), result in zip(meta, gathered_results):
        if result.status != "complete":
            if kind == "ohlcv":
                failed_spot_symbols.append(symbol)
            continue
        if kind == "ohlcv":
            item = _ohlcv_item(symbol, result, history_days)
            if item:
                items.append(item)
                market_rows.append(item)
            else:
                unusable_spot_symbols.append(symbol)
        elif kind == "funding":
            items.append(_funding_item(symbol, result))
        elif kind == "open_interest":
            items.append(_open_interest_item(symbol, result))
        elif kind == "fear_greed":
            items.extend(_fear_greed_items(result))
        elif kind == "stablecoins":
            item = _stablecoin_item(result)
            if item:
                items.append(item)
        elif kind == "stablecoin_history":
            item = _stablecoin_history_item(result)
            if item:
                items.append(item)
        elif kind == "defi_tvl":
            item = _defi_item(result)
            if item:
                items.append(item)
    fallback_symbols = [
        symbol
        for symbol in dict.fromkeys(failed_spot_symbols + unusable_spot_symbols)
        if symbol in KRAKEN_PAIRS
    ]
    fallback_results: list[FetchResult] = []
    fallback_success_symbols: set[str] = set()
    if fallback_symbols:
        fallback_results = list(
            await asyncio.gather(
                *[
                    fetch_json(
                        "kraken",
                        "https://api.kraken.com/0/public/OHLC",
                        params={
                            "pair": KRAKEN_PAIRS[symbol],
                            "interval": 1440,
                        },
                        retry=False,
                    )
                    for symbol in fallback_symbols
                ]
            )
        )
        for symbol, result in zip(fallback_symbols, fallback_results):
            if result.status != "complete":
                continue
            item = _kraken_ohlcv_item(symbol, result, history_days)
            if item:
                items.append(item)
                market_rows.append(item)
                fallback_success_symbols.add(symbol)
    symbols_present = {item.get("symbol") for item in market_rows}
    selected_symbols = [
        symbol for symbol in candidate_symbols if symbol in symbols_present
    ][: (len(candidate_symbols) if scope == "memecoin" else MAX_DYNAMIC_SYMBOLS)]
    required = set(selected_symbols)
    unselected_market_evidence = {
        str(item.get("evidence_id"))
        for item in market_rows
        if item.get("symbol") not in required
    }
    if unselected_market_evidence:
        items = [
            item
            for item in items
            if str(item.get("evidence_id")) not in unselected_market_evidence
        ]
        market_rows = [item for item in market_rows if item.get("symbol") in required]
        symbols_present = {item.get("symbol") for item in market_rows}
    valid_market_rows = [item for item in market_rows if item.get("symbol") in required]
    breadth_item = _breadth_item(valid_market_rows)
    if breadth_item is not None:
        items.append(breadth_item)
    provider_results = list(preliminary_results)
    for (kind, symbol), result in zip(meta, gathered_results):
        if kind != "ohlcv":
            provider_results.append(result)
        elif symbol in required and symbol not in fallback_success_symbols:
            provider_results.append(result)
        elif symbol in {"BTC", "ETH"} and symbol not in fallback_success_symbols:
            provider_results.append(result)
    provider_results.extend(
        result
        for symbol, result in zip(fallback_symbols, fallback_results)
        if symbol in required or symbol in {"BTC", "ETH"}
    )
    rejection_reasons = []
    for symbol in candidate_symbols:
        if symbol in required:
            continue
        reason = (
            "primary_http_failure"
            if symbol in failed_spot_symbols
            else (
                "stale_or_unparseable_history"
                if symbol in unusable_spot_symbols
                else "outside_bounded_technical_set"
            )
        )
        rejection_reasons.append({"symbol": symbol, "reason": reason})
    derivative_observations = {
        (item.get("symbol"), item.get("metric"))
        for item in items
        if item.get("source_family") == "derivatives" and item.get("value") is not None
    }
    coverage = {
        "valid_count": len(symbols_present & required),
        "configured_count": len(required),
        "valid_pct": (
            round(len(symbols_present & required) / len(required) * 100, 2)
            if required
            else 0
        ),
        "configured_symbols": selected_symbols,
        "universe_source": (
            "coinmarketcap_current_rankings"
            if catalog_coverage["dynamic_universe_available"]
            else "static_emergency_fallback"
        ),
        "catalog": catalog_coverage,
        "spot_pair_selection": (
            "current_rank_order_then_successful_bounded_primary_or_fallback_history"
        ),
        "btc_eth_present": {"BTC", "ETH"}.issubset(symbols_present),
        "btc_eth_derivatives_count": len(
            {
                observation
                for observation in derivative_observations
                if observation[0] in {"BTC", "ETH"}
            }
        ),
        "derivatives_venue_count": 1 if derivative_observations else 0,
        "spot_fallback_used": bool(fallback_success_symbols & required),
        "failed_primary_spot_symbols": sorted(set(failed_spot_symbols)),
        "unusable_primary_spot_symbols": sorted(set(unusable_spot_symbols)),
        "spot_selection_probe_count": len(candidate_symbols),
        "spot_selection_rejections": rejection_reasons,
        "above_sma20_pct": (
            round(
                sum(
                    (item.get("metrics") or {}).get("above_sma20") is True
                    for item in valid_market_rows
                )
                / len(valid_market_rows)
                * 100,
                2,
            )
            if valid_market_rows
            else None
        ),
        "above_sma50_pct": (
            round(
                sum(
                    (item.get("metrics") or {}).get("above_sma50") is True
                    for item in valid_market_rows
                )
                / len(valid_market_rows)
                * 100,
                2,
            )
            if valid_market_rows
            else None
        ),
    }
    return items, provider_results, coverage


def _breadth_item(
    market_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Create one auditable breadth observation from the retained technical set."""
    if not market_rows:
        return None
    ordered = sorted(
        market_rows,
        key=lambda item: str(item.get("symbol") or ""),
    )
    symbols = [str(item.get("symbol") or "") for item in ordered]
    source_times = [
        str(item.get("source_time") or "")
        for item in ordered
        if item.get("source_time")
    ]
    if not source_times:
        return None
    above_sma20_count = sum(
        (item.get("metrics") or {}).get("above_sma20") is True for item in ordered
    )
    above_sma50_count = sum(
        (item.get("metrics") or {}).get("above_sma50") is True for item in ordered
    )
    count = len(ordered)
    source_time = max(source_times)
    return {
        "evidence_id": evidence_id(
            "binance_spot",
            "liquid_crypto_breadth:" + ",".join(symbols),
            source_time,
        ),
        "provider_id": "binance_spot",
        "source_family": "derived_market",
        "metric": "liquid_crypto_breadth",
        "title": "Derived liquid-crypto technical breadth",
        "source_time": source_time,
        "configured_symbols": symbols,
        "configured_count": count,
        "above_sma20_count": above_sma20_count,
        "above_sma20_pct": round(above_sma20_count / count * 100, 2),
        "above_sma50_count": above_sma50_count,
        "above_sma50_pct": round(above_sma50_count / count * 100, 2),
        "underlying_evidence_ids": [
            str(item.get("evidence_id")) for item in ordered if item.get("evidence_id")
        ],
        "derivation": (
            "Count of retained fresh spot histories above their deterministic "
            "20-day and 50-day simple moving averages."
        ),
    }


def _ohlcv_item(
    symbol: str, result: FetchResult, history_days: int
) -> dict[str, Any] | None:
    rows = []
    for candle in result.data if isinstance(result.data, list) else []:
        if not isinstance(candle, list) or len(candle) < 6:
            continue
        timestamp = datetime.fromtimestamp(
            float(candle[0]) / 1000, tz=timezone.utc
        ).isoformat()
        rows.append(
            {
                "timestamp": timestamp.replace("+00:00", "Z"),
                "open": safe_float(candle[1]),
                "high": safe_float(candle[2]),
                "low": safe_float(candle[3]),
                "close": safe_float(candle[4]),
                "volume": safe_float(candle[5]),
            }
        )
    metrics = calculate_ohlcv_metrics(rows)
    if not metrics:
        return None
    source_time = metrics["last_observation"]
    if not _source_is_recent(source_time):
        return None
    return {
        "evidence_id": evidence_id("binance_spot", symbol, source_time),
        "provider_id": "binance_spot",
        "source_family": "market",
        "asset_class": "crypto",
        "symbol": symbol,
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        "metrics": metrics,
        "series": compact_series(
            rows,
            symbol=symbol,
            maximum=min(history_days, 180) if symbol in {"BTC", "ETH"} else 0,
        ),
    }


def _kraken_ohlcv_item(
    symbol: str, result: FetchResult, history_days: int
) -> dict[str, Any] | None:
    payload = result.data or {}
    result_map = payload.get("result") if isinstance(payload, dict) else {}
    if not isinstance(result_map, dict):
        return None
    rows = []
    for pair_name, candles in result_map.items():
        if pair_name == "last" or not isinstance(candles, list):
            continue
        for candle in candles[-history_days:]:
            if not isinstance(candle, list) or len(candle) < 7:
                continue
            rows.append(
                {
                    "timestamp": _epoch(candle[0]),
                    "open": safe_float(candle[1]),
                    "high": safe_float(candle[2]),
                    "low": safe_float(candle[3]),
                    "close": safe_float(candle[4]),
                    "volume": safe_float(candle[6]),
                }
            )
        break
    metrics = calculate_ohlcv_metrics(rows)
    if not metrics:
        return None
    source_time = metrics["last_observation"]
    if not _source_is_recent(source_time):
        return None
    return {
        "evidence_id": evidence_id("kraken", symbol, source_time),
        "provider_id": "kraken",
        "source_family": "market",
        "asset_class": "crypto",
        "symbol": symbol,
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        "metrics": metrics,
        "series": compact_series(
            rows,
            symbol=symbol,
            maximum=min(history_days, 180) if symbol in {"BTC", "ETH"} else 0,
        ),
    }


def _funding_item(symbol: str, result: FetchResult) -> dict[str, Any]:
    payload = result.data or {}
    source_time = _epoch(payload.get("time"), milliseconds=True)
    return {
        "evidence_id": evidence_id("binance_futures", f"{symbol}:funding", source_time),
        "provider_id": "binance_futures",
        "source_family": "derivatives",
        "metric": "funding_rate",
        "symbol": symbol,
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        "value": safe_float(payload.get("lastFundingRate")),
        "venue_bias": "single_venue",
    }


def _open_interest_item(symbol: str, result: FetchResult) -> dict[str, Any]:
    payload = result.data or {}
    source_time = _epoch(payload.get("time"), milliseconds=True)
    return {
        "evidence_id": evidence_id(
            "binance_futures", f"{symbol}:open_interest", source_time
        ),
        "provider_id": "binance_futures",
        "source_family": "derivatives",
        "metric": "open_interest",
        "symbol": symbol,
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        "value": safe_float(payload.get("openInterest")),
        "unit": "base_asset",
        "venue_bias": "single_venue",
    }


def _fear_greed_items(result: FetchResult) -> list[dict[str, Any]]:
    output = []
    for row in (result.data or {}).get("data") or []:
        source_time = _epoch(row.get("timestamp"))
        value = safe_float(row.get("value"))
        if value is None:
            continue
        output.append(
            {
                "evidence_id": evidence_id(
                    "alternative_fng", "crypto_fear_greed", source_time
                ),
                "provider_id": "alternative_fng",
                "source_family": "sentiment",
                "metric": "crypto_fear_greed",
                "source_time": source_time,
                "retrieved_at": result.retrieved_at,
                "value": value,
                "classification": str(row.get("value_classification") or ""),
            }
        )
    return output[:30]


def _stablecoin_item(result: FetchResult) -> dict[str, Any] | None:
    total = 0.0
    count = 0
    for asset in (result.data or {}).get("peggedAssets") or []:
        value = safe_float((asset.get("circulating") or {}).get("peggedUSD"))
        if value is not None and value >= 0:
            total += value
            count += 1
    if not count:
        return None
    return {
        "evidence_id": evidence_id(
            "defillama", "stablecoin_supply", result.retrieved_at
        ),
        "provider_id": "defillama",
        "source_family": "liquidity",
        "metric": "stablecoin_supply_usd",
        "source_time": result.retrieved_at,
        "retrieved_at": result.retrieved_at,
        "value": round(total, 2),
        "asset_count": count,
    }


def _defi_item(result: FetchResult) -> dict[str, Any] | None:
    rows = result.data if isinstance(result.data, list) else []
    if not rows:
        return None
    value = safe_float(rows[-1].get("tvl"))
    source_time = _epoch(rows[-1].get("date"))
    if value is None:
        return None
    return {
        "evidence_id": evidence_id("defillama", "defi_tvl", source_time),
        "provider_id": "defillama",
        "source_family": "liquidity",
        "metric": "defi_tvl_usd",
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        "value": value,
        "change_7d_pct": _history_change(rows, 7, "tvl"),
        "change_30d_pct": _history_change(rows, 30, "tvl"),
    }


def _stablecoin_history_item(result: FetchResult) -> dict[str, Any] | None:
    rows = result.data if isinstance(result.data, list) else []
    normalized = []
    for row in rows:
        value = safe_float((row.get("totalCirculating") or {}).get("peggedUSD"))
        if value is None:
            continue
        normalized.append({"date": row.get("date"), "value": value})
    if not normalized:
        return None
    latest = normalized[-1]
    source_time = _epoch(latest.get("date"))
    return {
        "evidence_id": evidence_id(
            "defillama", "stablecoin_supply_history", source_time
        ),
        "provider_id": "defillama",
        "source_family": "liquidity",
        "metric": "stablecoin_supply_trend",
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        "value": latest["value"],
        "change_7d_pct": _history_change(normalized, 7, "value"),
        "change_30d_pct": _history_change(normalized, 30, "value"),
    }


def _history_change(rows: list[dict[str, Any]], periods: int, key: str) -> float | None:
    if len(rows) <= periods:
        return None
    latest = safe_float(rows[-1].get(key))
    previous = safe_float(rows[-periods - 1].get(key))
    if latest is None or previous is None or previous == 0:
        return None
    return round((latest / previous - 1) * 100, 4)


def _epoch(value: Any, *, milliseconds: bool = False) -> str:
    number = safe_float(value)
    if number is None:
        return ""
    divisor = 1000 if milliseconds else 1
    return (
        datetime.fromtimestamp(number / divisor, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _source_is_recent(value: str, *, maximum_age_days: int = 3) -> bool:
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
    return -86_400 <= age.total_seconds() <= maximum_age_days * 86_400
