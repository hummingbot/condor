"""Build the one deterministic v3 fact summary used by analysis and rendering."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from agents.market_reporter.routines._evidence import safe_float
from agents.market_reporter.routines._identity import (
    TRADFI_BENCHMARKS,
    TRADFI_PROXIES,
    TRADFI_SECTORS,
    TRADFI_SP500_NAMES,
    TRADFI_SP500_STOCKS,
)
from agents.market_reporter.routines._memecoin_catalog import (
    MEMECOIN_META_LABELS,
)
from agents.market_reporter.routines._models import AnalysisContext
from agents.market_reporter.routines._providers import get_provider

_NEWS_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Rates and central banks", ("federal reserve", "interest rate", "treasury")),
    ("Inflation and employment", ("inflation", "cpi", "ppi", "jobs", "payroll")),
    ("Liquidity and positioning", ("liquidity", "funding", "open interest", "credit")),
    ("Government and regulation", ("regulation", "sec ", "cftc", "government")),
    ("Security and market integrity", ("hack", "exploit", "fraud", "outage")),
    ("Companies and institutions", ("earnings", "etf", "institution", "acquisition")),
    ("Technology and adoption", ("upgrade", "launch", "network", " ai ")),
    ("Geopolitics and trade", ("tariff", "sanction", "war", "election")),
)
_ENGLISH_HEADLINE_WORDS = {
    "a",
    "after",
    "amid",
    "an",
    "and",
    "are",
    "as",
    "at",
    "before",
    "but",
    "cuts",
    "from",
    "gains",
    "holds",
    "how",
    "in",
    "is",
    "issues",
    "keeps",
    "launches",
    "of",
    "on",
    "or",
    "reports",
    "rises",
    "says",
    "sees",
    "supports",
    "the",
    "to",
    "was",
    "why",
    "with",
}
_NON_ENGLISH_HEADLINE_WORDS = {
    "al",
    "con",
    "del",
    "el",
    "en",
    "esta",
    "las",
    "los",
    "mantiene",
    "mercado",
    "para",
    "pero",
    "por",
    "que",
    "se",
    "una",
}


def build_analysis_context(
    *,
    strategy_key: str,
    scope: str,
    as_of_utc: str,
    display_timezone: str,
    bundles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return bounded facts while full source bundles remain cached."""
    items_by_source = {
        str(bundle.get("source_type") or ""): [
            item for item in bundle.get("items") or [] if isinstance(item, dict)
        ]
        for bundle in bundles
    }
    market_items = items_by_source.get("market", [])
    market_assets = _market_assets(market_items)
    ranked_assets = _ranked_assets(market_items)
    latest_metrics = _latest_metrics(market_items)
    events = _events(items_by_source.get("events", []), display_timezone)
    leaders_laggards = _leaders_laggards(market_assets)

    if strategy_key == "tradfi_market_intelligence":
        features = _tradfi_features(
            market_assets,
            latest_metrics,
            items_by_source.get("fundamentals", []),
        )
    elif strategy_key == "memecoin_market_intelligence":
        features = _memecoin_features(
            market_items,
            items_by_source.get("token_discovery", []),
            market_assets,
        )
    else:
        features = _crypto_features(
            market_assets,
            ranked_assets,
            latest_metrics,
            bundles,
        )

    coverage = _coverage_assessment(
        strategy_key,
        scope,
        bundles,
        events,
        features,
    )
    selected_items = []
    for source_type, maximum in (
        ("market", 80),
        ("news", 24),
        ("events", 16),
        ("social", 16),
        ("token_discovery", 50),
        ("fundamentals", 16),
    ):
        selected_items.extend(
            _tag_source_items(
                source_type, items_by_source.get(source_type, [])[:maximum]
            )
        )

    context = AnalysisContext(
        strategy_key=strategy_key,
        scope=scope,
        as_of_utc=as_of_utc,
        display_timezone=display_timezone,
        research_posture=(
            "extreme_risk_research"
            if strategy_key == "memecoin_market_intelligence"
            else "conservative"
        ),
        coverage_assessment=coverage,
        coverage_summary=_coverage_summary(bundles),
        snapshot_metrics=_snapshot_metrics(
            strategy_key,
            features,
            leaders_laggards,
        ),
        market_snapshot={
            "assets": market_assets,
            "current_ranked_assets": ranked_assets,
            "latest_metrics": latest_metrics,
        },
        leaders_laggards=leaders_laggards,
        news_clusters=_news_clusters(items_by_source.get("news", [])),
        social_attention=_social_attention(
            strategy_key,
            items_by_source.get("social", []),
        ),
        events=events,
        data_limitations=_limitations(bundles),
        evidence_lookup=_evidence_lookup(selected_items),
        strategy_features=features,
    )
    return context.model_dump(mode="json")


def _coverage_assessment(
    strategy_key: str,
    scope: str,
    bundles: list[dict[str, Any]],
    events: list[dict[str, Any]],
    features: dict[str, Any],
) -> dict[str, Any]:
    retained = sum(int(bundle.get("retained_item_count") or 0) for bundle in bundles)
    missing = [
        str(bundle.get("source_type") or "")
        for bundle in bundles
        if bundle.get("status") == "unavailable"
    ]
    reasons = []
    truncated = False
    for bundle in bundles:
        source_type = str(bundle.get("source_type") or "source")
        status = str(bundle.get("status") or "unavailable")
        if status != "complete":
            reasons.append(f"{source_type}_{status}")
        if bundle.get("warnings"):
            reasons.append(f"{source_type}_warnings")
        if bundle.get("errors"):
            reasons.append(f"{source_type}_errors")
        if bundle.get("truncation_reasons"):
            reasons.append(f"{source_type}_truncated")
            truncated = True

    if not retained:
        grade = "unavailable"
    else:
        market = _bundle(bundles, "market")
        market_coverage = market.get("coverage") or {}
        crypto = market_coverage.get("crypto_universe") or {}
        tradfi = market_coverage.get("tradfi_universe") or {}
        crypto_gate = (
            bool(crypto.get("btc_eth_present"))
            and float(crypto.get("valid_pct") or 0) >= 70
        )
        tradfi_gate = (
            bool(tradfi.get("spy_present"))
            and int(tradfi.get("sp500_sample_valid_count") or 0) >= 8
            and bool(tradfi.get("treasury_curve_present"))
            and int(tradfi.get("cross_asset_component_count") or 0) >= 2
        )
        if strategy_key == "memecoin_market_intelligence":
            discovery = _bundle(bundles, "token_discovery")
            core_gate = (
                crypto_gate and int(discovery.get("retained_item_count") or 0) > 0
            )
        elif scope == "both":
            core_gate = crypto_gate and tradfi_gate
        elif strategy_key == "tradfi_market_intelligence":
            core_gate = tradfi_gate and bool(events)
        else:
            core_gate = crypto_gate

        if not core_gate:
            grade = "limited"
        elif (
            _complete_coverage(strategy_key, bundles, events, features) and not reasons
        ):
            grade = "complete"
        else:
            grade = "sufficient"

    return {
        "grade": grade,
        "confidence_cap": {
            "unavailable": "low",
            "limited": "low",
            "sufficient": "moderate",
            "complete": "high",
        }[grade],
        "reason_codes": list(dict.fromkeys(reasons))[:16],
        "missing_sources": list(dict.fromkeys(missing))[:8],
        "truncated": truncated,
    }


def _complete_coverage(
    strategy_key: str,
    bundles: list[dict[str, Any]],
    events: list[dict[str, Any]],
    features: dict[str, Any],
) -> bool:
    if any(bundle.get("status") != "complete" for bundle in bundles):
        return False
    if strategy_key == "crypto_market_intelligence":
        breadth = features.get("breadth") or {}
        return (
            float(breadth.get("valid_pct") or 0) >= 90
            and int(breadth.get("configured_count") or 0) >= 8
            and int(breadth.get("btc_eth_derivatives_count") or 0) >= 4
            and int(_bundle(bundles, "news").get("retained_item_count") or 0) >= 5
        )
    if strategy_key == "tradfi_market_intelligence":
        market = _bundle(bundles, "market")
        coverage = (market.get("coverage") or {}).get("tradfi_universe") or {}
        metrics = {str(item.get("metric") or "") for item in market.get("items") or []}
        return (
            len(features.get("sp500_stocks") or []) >= 8
            and len(events) >= 2
            and len(features.get("fundamentals") or []) >= 3
            and {"volatility", "credit", "dollar"}.issubset(
                set(coverage.get("cross_asset_components") or [])
            )
            and {
                "vix",
                "high_yield_spread",
                "broad_dollar",
                "treasury_curve",
            }.issubset(metrics)
        )
    counts = features.get("exact_pair_counts") or {}
    meta_count = len(features.get("provider_meta_categories") or []) + len(
        features.get("exclusive_ranked_sample_metas") or []
    )
    return int(counts.get("mature_eligible") or 0) >= 5 and meta_count >= 3


def _coverage_summary(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for bundle in bundles:
        timestamps = sorted(
            str(
                item.get("source_time")
                or item.get("published_at")
                or item.get("event_time_utc")
            )
            for item in bundle.get("items") or []
            if (
                item.get("source_time")
                or item.get("published_at")
                or item.get("event_time_utc")
            )
        )
        rows.append(
            {
                "source_type": bundle.get("source_type"),
                "status": bundle.get("status"),
                "retained_items": bundle.get("retained_item_count"),
                "raw_items": bundle.get("raw_item_count"),
                "oldest_source_time": timestamps[0] if timestamps else None,
                "newest_source_time": timestamps[-1] if timestamps else None,
                "warnings": (bundle.get("warnings") or [])[:4],
                "errors": (bundle.get("errors") or [])[:4],
            }
        )
    return rows


def _market_assets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        metrics = item.get("metrics")
        if not item.get("symbol") or not isinstance(metrics, dict):
            continue
        rows.append(
            {
                "evidence_id": item.get("evidence_id"),
                "symbol": item.get("symbol"),
                "asset_class": item.get("asset_class"),
                "source_time": item.get("source_time"),
                "last_price": metrics.get("last_price"),
                "return_7d_pct": metrics.get("return_7d_pct"),
                "return_30d_pct": metrics.get("return_30d_pct"),
                "above_sma20": metrics.get("above_sma20"),
                "above_sma50": metrics.get("above_sma50"),
                "rsi14": metrics.get("rsi14"),
                "realized_volatility_20d_pct": metrics.get(
                    "realized_volatility_20d_pct"
                ),
                "relative_strength_vs_spy_7d_pct": metrics.get(
                    "relative_strength_vs_spy_7d_pct"
                ),
            }
        )
    return rows[:36]


def _ranked_assets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "evidence_id": item.get("evidence_id"),
            "rank": item.get("rank"),
            "name": item.get("name"),
            "symbol": item.get("symbol"),
            "market_cap_usd": item.get("market_cap_usd"),
            "market_cap_dominance_pct": item.get("market_cap_dominance_pct"),
            "volume_24h_usd": item.get("volume_24h_usd"),
            "price_change_24h_pct": item.get("price_change_24h_pct"),
            "price_change_7d_pct": item.get("price_change_7d_pct"),
            "eligible_for_liquid_universe": item.get("eligible_for_liquid_universe"),
            "source_time": item.get("source_time"),
        }
        for item in items
        if item.get("metric") == "market_catalog_asset"
    ]
    rows.sort(key=lambda row: int(row.get("rank") or 10_000))
    return rows[:24]


def _latest_metrics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {
        "evidence_id",
        "provider_id",
        "source_family",
        "metric",
        "symbol",
        "source_time",
        "value",
        "unit",
        "classification",
        "change_7d",
        "change_7d_pct",
        "change_30d_pct",
        "points_pct",
        "slope_2s10s_bps",
        "slope_3m10y_bps",
        "slope_10s30s_bps",
        "asset_manager_net",
        "leveraged_fund_net",
        "publication_lag",
        "total_market_cap_usd",
        "total_market_cap_change_24h_pct",
        "total_volume_24h_usd",
        "total_volume_change_24h_pct",
        "btc_dominance_pct",
        "btc_dominance_change_24h_pct",
        "eth_dominance_pct",
        "stablecoin_market_cap_usd",
        "defi_market_cap_usd",
        "configured_count",
        "observed_count",
        "above_sma20_count",
        "above_sma20_pct",
        "above_sma50_count",
        "above_sma50_pct",
        "positive_7d_count",
        "positive_7d_pct",
        "negative_7d_count",
        "underlying_evidence_ids",
        "derivation",
    }
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        metric = str(item.get("metric") or "")
        if not metric or metric == "market_catalog_asset":
            continue
        key = (metric, str(item.get("symbol") or ""))
        if key in latest and str(latest[key].get("source_time") or "") >= str(
            item.get("source_time") or ""
        ):
            continue
        latest[key] = {key: value for key, value in item.items() if key in allowed}
    return sorted(
        latest.values(),
        key=lambda row: (str(row.get("metric") or ""), str(row.get("symbol") or "")),
    )[:36]


def _leaders_laggards(assets: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = [row for row in assets if safe_float(row.get("return_7d_pct")) is not None]
    ranked.sort(
        key=lambda row: safe_float(row.get("return_7d_pct")) or 0.0,
        reverse=True,
    )
    return {
        "leaders": ranked[:3],
        "laggards": list(reversed(ranked[-3:])),
        "basis": "observed seven-day return",
    }


def _news_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if not _looks_english_headline(item.get("title")):
            continue
        text = f" {item.get('title') or ''} {item.get('summary') or ''} ".casefold()
        topic = next(
            (
                label
                for label, markers in _NEWS_TOPICS
                if any(marker in text for marker in markers)
            ),
            "Other market developments",
        )
        groups[topic].append(item)
    rows = []
    for topic, values in groups.items():
        ordered = sorted(
            values,
            key=lambda item: str(item.get("published_at") or ""),
            reverse=True,
        )
        rows.append(
            {
                "topic": topic,
                "item_count": len(values),
                "highlights": [
                    {
                        "evidence_id": item.get("evidence_id"),
                        "title": item.get("title"),
                        "summary": item.get("summary"),
                        "published_at": item.get("published_at"),
                        "publisher": item.get("publisher") or item.get("provider_id"),
                        "url": item.get("url"),
                    }
                    for item in ordered[:2]
                ],
            }
        )
    rows.sort(key=lambda row: int(row["item_count"]), reverse=True)
    return rows[:5]


def _looks_english_headline(value: Any) -> bool:
    text = " ".join(str(value or "").split())
    words = re.findall(r"[a-z]+", text.casefold())
    if len(words) < 3:
        return False
    non_ascii_letters = sum(
        character.isalpha() and not character.isascii() for character in text
    )
    all_letters = sum(character.isalpha() for character in text)
    if all_letters and non_ascii_letters / all_letters > 0.05:
        return False
    word_set = set(words)
    if len(word_set & _NON_ENGLISH_HEADLINE_WORDS) >= 2:
        return False
    return bool(word_set & _ENGLISH_HEADLINE_WORDS)


def _social_attention(
    strategy_key: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    topics = {
        "crypto_market_intelligence": (
            ("Bitcoin", ("bitcoin", " btc")),
            ("Ethereum", ("ethereum", " ether", " eth")),
            ("Broad crypto", ("crypto", "stablecoin", "defi")),
        ),
        "tradfi_market_intelligence": (
            ("U.S. stocks", ("stocks", "s&p", " spy", " qqq")),
            ("Rates and policy", ("federal reserve", " fed", "rates", "yield")),
            ("Large technology", ("apple", "microsoft", "nvidia", " ai ")),
        ),
        "memecoin_market_intelligence": (
            ("Solana", ("solana", "bonk", "pump.fun")),
            ("Ethereum", ("ethereum", "pepe", "shib")),
            ("Robinhood Chain", ("robinhood chain",)),
            ("Memecoin market", ("memecoin", "meme coin", "dexscreener")),
        ),
    }[strategy_key]
    rows = []
    for item in items:
        excerpt = _plain(item.get("excerpt"))
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id or not excerpt:
            continue
        text = f" {excerpt.casefold()} "
        matched = [
            label
            for label, markers in topics
            if any(marker in text for marker in markers)
        ]
        engagement = item.get("engagement") or {}
        likes = int(safe_float(engagement.get("likes")) or 0)
        reposts = int(safe_float(engagement.get("reposts")) or 0)
        replies = int(safe_float(engagement.get("replies")) or 0)
        rows.append(
            {
                "evidence_id": evidence_id,
                "topics": matched or ["Other retained discussion"],
                "excerpt": excerpt,
                "author": item.get("author_handle"),
                "provider_id": item.get("provider_id"),
                "published_at": item.get("published_at"),
                "engagement": {
                    "likes": likes,
                    "reposts": reposts,
                    "replies": replies,
                    "total": likes + reposts + replies,
                },
            }
        )
    rows.sort(
        key=lambda row: (
            int((row.get("engagement") or {}).get("total") or 0),
            str(row.get("published_at") or ""),
        ),
        reverse=True,
    )
    return rows[:5]


def _events(
    items: list[dict[str, Any]],
    display_timezone: str,
) -> list[dict[str, Any]]:
    rows = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not item.get("evidence_id") or not item.get("event_time_utc"):
            continue
        if item.get("verified_scheduled") is not True:
            continue
        provider_id = str(item.get("provider_id") or "")
        try:
            provider = get_provider(provider_id)
        except ValueError:
            continue
        if provider.get("source_family") != "official":
            continue
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        event_time = str(item.get("event_time_utc") or "")
        dedupe_key = (re.sub(r"\W+", "", title).casefold(), event_time)
        if not title or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        try:
            display_time = (
                datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                .astimezone(ZoneInfo(display_timezone))
                .isoformat()
            )
        except ValueError:
            continue
        rows.append(
            {
                "evidence_id": item.get("evidence_id"),
                "title": title,
                "event_time_utc": event_time,
                "display_time": display_time,
                "provider_id": provider_id,
                "verified_scheduled": True,
                "url": item.get("url"),
            }
        )
    rows.sort(key=lambda row: str(row.get("event_time_utc") or ""))
    return rows[:8]


def _crypto_features(
    assets: list[dict[str, Any]],
    ranked_assets: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    bundles: list[dict[str, Any]],
) -> dict[str, Any]:
    coverage = (_bundle(bundles, "market").get("coverage") or {}).get(
        "crypto_universe"
    ) or {}
    return {
        "product": "crypto_market_brief_v3",
        "market_assets": assets,
        "current_ranked_universe": ranked_assets,
        "breadth": {
            **{
                key: coverage.get(key)
                for key in (
                    "configured_symbols",
                    "valid_count",
                    "configured_count",
                    "valid_pct",
                    "above_sma20_pct",
                    "above_sma50_pct",
                    "btc_eth_present",
                    "btc_eth_derivatives_count",
                )
            },
            "aggregate_observation": next(
                (
                    row
                    for row in metrics
                    if row.get("metric") == "liquid_crypto_breadth"
                ),
                {},
            ),
        },
        "global_market": next(
            (row for row in metrics if row.get("metric") == "global_crypto_metrics"),
            {},
        ),
        "sentiment": next(
            (row for row in metrics if row.get("metric") == "crypto_fear_greed"),
            {},
        ),
        "liquidity_and_positioning": [
            row
            for row in metrics
            if row.get("metric")
            in {
                "stablecoin_supply_usd",
                "stablecoin_supply_trend",
                "defi_tvl_usd",
                "funding_rate",
                "open_interest",
            }
        ][:8],
    }


def _tradfi_features(
    assets: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    fundamentals: list[dict[str, Any]],
) -> dict[str, Any]:
    by_symbol = {str(row.get("symbol") or ""): row for row in assets}
    sectors = [by_symbol[symbol] for symbol in TRADFI_SECTORS if symbol in by_symbol]
    sectors.sort(
        key=lambda row: safe_float(row.get("return_7d_pct")) or 0.0,
        reverse=True,
    )
    sp500_stocks = [
        {
            **by_symbol[symbol],
            "company_name": TRADFI_SP500_NAMES[symbol],
        }
        for symbol in TRADFI_SP500_STOCKS
        if symbol in by_symbol
    ]
    sp500_stocks.sort(
        key=lambda row: safe_float(row.get("return_7d_pct")) or 0.0,
        reverse=True,
    )
    return {
        "product": "tradfi_market_brief_v3",
        "benchmarks": [
            by_symbol[symbol] for symbol in TRADFI_BENCHMARKS if symbol in by_symbol
        ],
        "sectors": sectors,
        "sector_leaders": sectors[:3],
        "sector_laggards": list(reversed(sectors[-3:])),
        "cross_asset_proxies": [
            by_symbol[symbol] for symbol in TRADFI_PROXIES if symbol in by_symbol
        ],
        "sp500_stocks": sp500_stocks,
        "stock_leaders": sp500_stocks[:3],
        "stock_laggards": list(reversed(sp500_stocks[-3:])),
        "macro_metrics": metrics,
        "sector_breadth": next(
            (row for row in metrics if row.get("metric") == "tradfi_sector_breadth"),
            {},
        ),
        "sp500_stock_breadth": next(
            (
                row
                for row in metrics
                if row.get("metric") == "tradfi_sp500_sample_breadth"
            ),
            {},
        ),
        "fundamentals": [_compact_fundamental(item) for item in fundamentals[:12]],
    }


def _memecoin_features(
    market_items: list[dict[str, Any]],
    token_items: list[dict[str, Any]],
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    meta_items = [
        item for item in market_items if item.get("metric") == "memecoin_meta_category"
    ]
    provider_meta_candidates = [_compact_meta(item) for item in meta_items]
    coingecko_meta = [
        _compact_meta(item)
        for item in meta_items
        if item.get("provider_id") == "coingecko"
    ]
    provider_meta = coingecko_meta or provider_meta_candidates
    sample_meta = [
        _compact_meta(item)
        for item in market_items
        if item.get("metric") == "memecoin_meta_sample"
    ]
    provider_meta_chain = [
        _compact_meta(item)
        for item in market_items
        if item.get("metric") == "memecoin_meta_chain_sample"
    ]
    categorized_assets = [
        {
            "evidence_id": item.get("evidence_id"),
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "controlled_metas": item.get("controlled_metas") or [],
            "primary_chain": item.get("primary_chain"),
            "market_cap_usd": item.get("market_cap_usd"),
            "volume_24h_usd": item.get("volume_24h_usd"),
            "price_change_24h_pct": item.get("price_change_24h_pct"),
        }
        for item in market_items
        if item.get("metric") == "memecoin_categorized_asset"
    ]
    sample_total = sum(
        safe_float(row.get("sample_market_cap_usd")) or 0.0 for row in sample_meta
    )
    for row in sample_meta:
        value = safe_float(row.get("sample_market_cap_usd"))
        row["sample_share_pct"] = (
            value / sample_total * 100 if value is not None and sample_total else None
        )
    tokens = [_compact_token(item) for item in token_items]
    eligible = [
        row
        for row in tokens
        if row.get("eligibility") == "eligible"
        and safe_float(row.get("liquidity_usd")) is not None
    ]
    eligible.sort(
        key=lambda row: (
            safe_float(row.get("volume_24h_usd")) or 0.0,
            safe_float(row.get("liquidity_usd")) or 0.0,
        ),
        reverse=True,
    )
    mature_eligible = sum(
        row.get("chain") in {"solana", "ethereum"} for row in eligible
    )
    highlights = []
    for selector in (
        lambda row: row.get("cohort") == "established",
        lambda row: not row.get("paid_visibility"),
        lambda row: bool(row.get("paid_visibility")),
    ):
        for row in eligible:
            if selector(row) and row not in highlights:
                highlights.append(row)
                break
    highlights.extend(row for row in eligible if row not in highlights)
    return {
        "product": "memecoin_meta_chain_brief_v3",
        "risk_class": "extreme_inherent_risk",
        "provider_meta_categories": provider_meta,
        "exclusive_ranked_sample_metas": sample_meta,
        "provider_meta_chain_samples": provider_meta_chain,
        "categorized_asset_sample": categorized_assets[:24],
        "categorized_asset_count": len(categorized_assets),
        "chain_overview": _chain_overview(tokens),
        "meta_chain_overview": _meta_chain_overview(tokens),
        "eligible_token_highlights": highlights[:6],
        "exact_pair_counts": {
            "observed": len(tokens),
            "eligible": len(eligible),
            "excluded": sum(row.get("eligibility") == "excluded" for row in tokens),
            "mature_eligible": mature_eligible,
        },
        "btc_eth_backdrop": [
            row for row in assets if row.get("symbol") in {"BTC", "ETH"}
        ],
    }


def _compact_fundamental(item: dict[str, Any]) -> dict[str, Any]:
    facts = item.get("facts") or {}
    return {
        "evidence_id": item.get("evidence_id"),
        "symbol": item.get("symbol"),
        "issuer_name": item.get("issuer_name"),
        "source_time": item.get("source_time"),
        "revenue_change_pct": _fact_change(facts, "revenue"),
        "operating_income_change_pct": _fact_change(facts, "operating_income"),
        "net_income_change_pct": _fact_change(facts, "net_income"),
        "operating_cash_flow_change_pct": _fact_change(facts, "operating_cash_flow"),
    }


def _compact_meta(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "evidence_id",
            "primary_meta",
            "provider_category_name",
            "provider_category_id",
            "market_cap_usd",
            "market_cap_change_24h_pct",
            "volume_24h_usd",
            "sample_market_cap_usd",
            "sample_market_cap_weighted_return_24h_pct",
            "sample_volume_24h_usd",
            "sampled_constituent_count",
            "constituent_count",
            "constituent_count_complete",
            "representative_coin_ids",
            "chain",
            "market_cap_weighted_change_24h_pct",
            "representative_symbols",
            "aggregation_basis",
            "categories_may_overlap",
            "source_time",
        )
        if item.get(key) is not None
    }


def _compact_token(item: dict[str, Any]) -> dict[str, Any]:
    market = item.get("market") or {}
    return {
        "evidence_id": item.get("evidence_id"),
        "chain": item.get("chain_id"),
        "chain_id": item.get("chain_id"),
        "symbol": market.get("symbol"),
        "token_address": item.get("token_address"),
        "pair_address": item.get("pair_address"),
        "quote_token_address": item.get("quote_token_address"),
        "cohort": item.get("cohort"),
        "asset_identity": {
            "chain_id": item.get("chain_id"),
            "token_address": item.get("token_address"),
            "pair_address": item.get("pair_address"),
            "quote_token_address": item.get("quote_token_address"),
            "symbol": market.get("symbol"),
            "cohort": item.get("cohort"),
        },
        "eligibility": item.get("eligibility"),
        "primary_meta": item.get("primary_meta") or "unclassified",
        "memecoin_classification": item.get("memecoin_classification"),
        "market_cap_usd": market.get("market_cap_usd"),
        "price_change_24h_pct": market.get("price_change_24h_pct"),
        "liquidity_usd": market.get("liquidity_usd"),
        "volume_24h_usd": market.get("volume_24h_usd"),
        "volume_to_liquidity": market.get("volume_to_liquidity"),
        "pair_age_hours": market.get("pair_age_hours"),
        "paid_visibility": item.get("paid_visibility"),
        "reason_codes": item.get("reason_codes"),
    }


def _chain_overview(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for chain in ("solana", "ethereum", "robinhood"):
        rows = [row for row in tokens if row.get("chain") == chain]
        eligible = [row for row in rows if row.get("eligibility") == "eligible"]
        market_caps = _numbers(eligible, "market_cap_usd")
        liquidity = _numbers(eligible, "liquidity_usd")
        volume = _numbers(eligible, "volume_24h_usd")
        ages = _numbers(eligible, "pair_age_hours")
        metas: dict[str, float] = defaultdict(float)
        for row in eligible:
            metas[str(row.get("primary_meta") or "unclassified")] += (
                safe_float(row.get("liquidity_usd")) or 0.0
            )
        output.append(
            {
                "chain": chain,
                "coverage": (
                    "promotion-biased and not directly comparable"
                    if chain == "robinhood"
                    else "organic-oriented plus attention discovery"
                ),
                "observed_pairs": len(rows) if rows else None,
                "eligible_pairs": len(eligible) if rows else None,
                "observed_market_cap_usd": sum(market_caps) if market_caps else None,
                "observed_liquidity_usd": sum(liquidity) if liquidity else None,
                "observed_volume_24h_usd": sum(volume) if volume else None,
                "median_pair_age_hours": median(ages) if ages else None,
                "paid_visibility_share_pct": (
                    sum(bool(row.get("paid_visibility")) for row in rows)
                    / len(rows)
                    * 100
                    if rows
                    else None
                ),
                "leading_observed_metas": [
                    key
                    for key, _ in sorted(
                        metas.items(),
                        key=lambda value: value[1],
                        reverse=True,
                    )[:3]
                ],
            }
        )
    return output


def _meta_chain_overview(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in tokens:
        if row.get("eligibility") != "eligible":
            continue
        grouped[
            (
                str(row.get("primary_meta") or "unclassified"),
                str(row.get("chain") or ""),
            )
        ].append(row)
    output = []
    for (meta, chain), rows in grouped.items():
        market_caps = _numbers(rows, "market_cap_usd")
        liquidity = _numbers(rows, "liquidity_usd")
        volume = _numbers(rows, "volume_24h_usd")
        weighted_moves = [
            (liquidity_usd, change_24h_pct)
            for row in rows
            if (liquidity_usd := safe_float(row.get("liquidity_usd"))) is not None
            and liquidity_usd > 0
            and (change_24h_pct := safe_float(row.get("price_change_24h_pct")))
            is not None
        ]
        weighted_liquidity = sum(value[0] for value in weighted_moves)
        output.append(
            {
                "primary_meta": meta,
                "chain": chain,
                "eligible_pair_count": len(rows),
                "observed_market_cap_usd": (sum(market_caps) if market_caps else None),
                "observed_liquidity_usd": sum(liquidity) if liquidity else None,
                "observed_volume_24h_usd": sum(volume) if volume else None,
                "liquidity_weighted_change_24h_pct": (
                    round(
                        sum(
                            liquidity_usd * change_24h_pct
                            for liquidity_usd, change_24h_pct in weighted_moves
                        )
                        / weighted_liquidity,
                        4,
                    )
                    if weighted_liquidity
                    else None
                ),
                "paid_visibility_share_pct": round(
                    sum(bool(row.get("paid_visibility")) for row in rows)
                    / len(rows)
                    * 100,
                    1,
                ),
                "representative_symbols": sorted(
                    {str(row.get("symbol") or "") for row in rows if row.get("symbol")}
                )[:4],
                "coverage": (
                    "promotion-biased sample"
                    if chain == "robinhood"
                    else "observed eligible-pair sample"
                ),
            }
        )
    output.sort(
        key=lambda row: (
            safe_float(row.get("observed_liquidity_usd")) or 0.0,
            int(row.get("eligible_pair_count") or 0),
        ),
        reverse=True,
    )
    return output


def _snapshot_metrics(
    strategy_key: str,
    features: dict[str, Any],
    leaders_laggards: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if strategy_key == "memecoin_market_intelligence":
        metas = (
            features.get("provider_meta_categories")
            or features.get("exclusive_ranked_sample_metas")
            or []
        )
        metas = sorted(
            metas,
            key=lambda row: safe_float(
                row.get("sample_market_cap_weighted_return_24h_pct")
                if row.get("aggregation_basis")
                == "mutually_exclusive_top_100_ranked_asset_sample"
                else row.get("market_cap_change_24h_pct")
            )
            or float("-inf"),
            reverse=True,
        )
        if metas:
            rows.append(
                {
                    "label": "Strongest theme today",
                    "value": MEMECOIN_META_LABELS.get(
                        str(metas[0].get("primary_meta") or ""),
                        "No classified leader",
                    ),
                    "evidence_id": metas[0].get("evidence_id"),
                }
            )
        counts = features.get("exact_pair_counts") or {}
        rows.extend(
            [
                {"label": "Eligible exact pairs", "value": counts.get("eligible")},
                {"label": "Observed exact pairs", "value": counts.get("observed")},
            ]
        )
        chain_rows = features.get("chain_overview") or []
        rows.append(
            {
                "label": "Observed 24h turnover",
                "value": sum(
                    safe_float(row.get("observed_volume_24h_usd")) or 0.0
                    for row in chain_rows
                ),
                "unit": "USD",
            }
        )
    elif strategy_key == "tradfi_market_intelligence":
        for row in (features.get("benchmarks") or [])[:2]:
            rows.append(
                {
                    "label": f"{row.get('symbol')} seven-day move",
                    "value": row.get("return_7d_pct"),
                    "unit": "%",
                    "evidence_id": row.get("evidence_id"),
                }
            )
        sectors = features.get("sectors") or []
        if sectors:
            positive = sum(
                (safe_float(row.get("return_7d_pct")) or 0.0) > 0 for row in sectors
            )
            rows.append(
                {
                    "label": "Positive sector participation",
                    "value": round(positive / len(sectors) * 100, 1),
                    "unit": "%",
                }
            )
        for metric_name, label, adverse_when_up in (
            ("vix", "VIX", True),
            ("high_yield_spread", "High-yield credit spread", True),
            ("broad_dollar", "Broad U.S. dollar index", True),
        ):
            metric = next(
                (
                    row
                    for row in features.get("macro_metrics") or []
                    if row.get("metric") == metric_name
                ),
                None,
            )
            if metric:
                rows.append(
                    {
                        "label": label,
                        "value": metric.get("value"),
                        "change": metric.get("change_7d")
                        or metric.get("change_7d_pct"),
                        "unit": metric.get("unit"),
                        "adverse_when_up": adverse_when_up,
                        "evidence_id": metric.get("evidence_id"),
                    }
                )
    else:
        global_market = features.get("global_market") or {}
        for label, key, unit in (
            ("Total crypto market cap", "total_market_cap_usd", "USD"),
            ("24h market-cap change", "total_market_cap_change_24h_pct", "%"),
            ("BTC dominance", "btc_dominance_pct", "%"),
        ):
            if global_market.get(key) is not None:
                rows.append(
                    {
                        "label": label,
                        "value": global_market.get(key),
                        "unit": unit,
                        "evidence_id": global_market.get("evidence_id"),
                    }
                )
        breadth = features.get("breadth") or {}
        if breadth.get("above_sma20_pct") is not None:
            rows.append(
                {
                    "label": "Assets above their 20-day average",
                    "value": breadth.get("above_sma20_pct"),
                    "unit": "%",
                }
            )
        sentiment = features.get("sentiment") or {}
        if sentiment.get("value") is not None:
            rows.append(
                {
                    "label": "Fear and Greed",
                    "value": sentiment.get("value"),
                    "unit": sentiment.get("classification") or "",
                    "evidence_id": sentiment.get("evidence_id"),
                }
            )
    for label, key in (
        ("Seven-day leader", "leaders"),
        ("Seven-day laggard", "laggards"),
    ):
        values = leaders_laggards.get(key) or []
        if values and len(rows) < 6:
            rows.append(
                {
                    "label": label,
                    "value": values[0].get("symbol"),
                    "change": values[0].get("return_7d_pct"),
                    "unit": "%",
                    "evidence_id": values[0].get("evidence_id"),
                }
            )
    return rows[:6]


def _limitations(bundles: list[dict[str, Any]]) -> list[str]:
    output = []
    for bundle in bundles:
        source_type = str(bundle.get("source_type") or "source").replace("_", " ")
        if bundle.get("status") != "complete":
            output.append(f"{source_type.title()} coverage is {bundle.get('status')}.")
        for warning in bundle.get("warnings") or []:
            output.append(f"{source_type.title()}: {_plain(warning)}")
        for error in bundle.get("errors") or []:
            output.append(f"{source_type.title()}: {_plain(error)}")
    return list(dict.fromkeys(output))[:12]


def _evidence_lookup(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output = {}
    for item in items:
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id or evidence_id in output:
            continue
        output[evidence_id] = {
            "source_type": item.get("_bundle_source_type"),
            "provider_id": item.get("provider_id"),
            "source_family": item.get("source_family"),
            "source_time": item.get("source_time")
            or item.get("published_at")
            or item.get("event_time_utc"),
            "title": item.get("title")
            or item.get("name")
            or item.get("symbol")
            or item.get("metric"),
        }
        if item.get("excerpt"):
            output[evidence_id]["excerpt"] = _plain(item.get("excerpt"))
            output[evidence_id]["engagement"] = item.get("engagement") or {}
        if len(output) >= 160:
            break
    return output


def _tag_source_items(
    source_type: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {**item, "_bundle_source_type": source_type}
        for item in items
        if isinstance(item, dict)
    ]


def _fact_change(facts: dict[str, Any], key: str) -> float | None:
    return safe_float(
        ((facts.get(key) or {}).get("prior_comparable") or {}).get("change_pct")
    )


def _numbers(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [
        value
        for value in (safe_float(row.get(key)) for row in rows)
        if value is not None
    ]


def _bundle(bundles: list[dict[str, Any]], source_type: str) -> dict[str, Any]:
    return next(
        (
            bundle
            for bundle in bundles
            if isinstance(bundle, dict) and bundle.get("source_type") == source_type
        ),
        {},
    )


def _plain(value: Any) -> str:
    return str(value).replace("_", " ").strip().capitalize()
