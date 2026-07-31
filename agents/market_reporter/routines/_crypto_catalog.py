"""Current keyless crypto rankings, global metrics, and Memecoin meta data."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Any

from agents.market_reporter.routines._evidence import evidence_id, safe_float
from agents.market_reporter.routines._http import FetchResult, fetch_json
from agents.market_reporter.routines._memecoin_catalog import (
    MEMECOIN_META_ORDER,
    collect_memecoin_catalog,
)

CATALOG_LIMIT = 100
MAX_DYNAMIC_SYMBOLS = 12

_STABLE_TAGS = {
    "stablecoin",
    "asset-backed-stablecoin",
    "algorithmic-stablecoin",
    "fiat-stablecoin",
    "usd-stablecoin",
}
_WRAPPED_OR_STAKED_MARKERS = (
    "wrapped",
    "staked ",
    "liquid staking",
    "bridged",
)


async def collect_catalog(
    *,
    include_meta_categories: bool,
) -> tuple[list[dict[str, Any]], list[FetchResult], dict[str, Any]]:
    """Fetch a bounded current catalog without credentials or hidden hosts."""
    requests = [
        fetch_json(
            "coinmarketcap",
            "https://pro-api.coinmarketcap.com/public-api/v3/cryptocurrency/listings/latest",
            params={"start": 1, "limit": CATALOG_LIMIT, "convert": "USD"},
            retry=False,
        ),
        fetch_json(
            "coinmarketcap",
            "https://pro-api.coinmarketcap.com/public-api/v1/global-metrics/quotes/latest",
            params={"convert": "USD"},
            retry=False,
        ),
        fetch_json(
            "coinmarketcap",
            "https://pro-api.coinmarketcap.com/public-api/v3/fear-and-greed/latest",
            retry=False,
        ),
    ]
    if include_meta_categories:
        requests.append(
            fetch_json(
                "coinmarketcap",
                "https://pro-api.coinmarketcap.com/public-api/v1/cryptocurrency/categories",
                params={"start": 1, "limit": 500},
                retry=False,
            )
        )

    catalog_task = asyncio.gather(*requests)
    memecoin_task = (
        collect_memecoin_catalog()
        if include_meta_categories
        else _empty_memecoin_catalog()
    )
    gathered, (memecoin_items, memecoin_results, memecoin_coverage) = (
        await asyncio.gather(catalog_task, memecoin_task)
    )
    results = list(gathered)
    listing_result = results[0]
    listings = _listing_items(listing_result)
    items = list(listings)
    if results[1].status == "complete":
        global_item = _global_metrics_item(results[1])
        if global_item:
            items.append(global_item)
    if results[2].status == "complete":
        fear_greed = _fear_greed_item(results[2])
        if fear_greed:
            items.append(fear_greed)

    category_items: list[dict[str, Any]] = []
    if include_meta_categories and len(results) > 3 and results[3].status == "complete":
        category_items = _meta_category_items(results[3])
        items.extend(category_items)
    items.extend(memecoin_items)
    results.extend(memecoin_results)
    sampled_meta = _sampled_meta_items(listings, listing_result)
    items.extend(sampled_meta)

    current_ranking = bool(listings)
    coverage = {
        "catalog_provider": "coinmarketcap",
        "catalog_status": listing_result.status,
        "catalog_retrieved_at": listing_result.retrieved_at,
        "catalog_limit": CATALOG_LIMIT,
        "ranked_asset_count": len(listings),
        "dynamic_universe_available": current_ranking,
        "universe_fallback": None if current_ranking else "static_emergency_fallback",
        "meta_category_count": len(category_items),
        "exclusive_sampled_meta_count": len(sampled_meta),
        "memecoin_taxonomy": memecoin_coverage,
        "meta_category_aggregation": (
            "provider_categories_may_overlap_do_not_sum"
            if category_items
            else "unavailable"
        ),
        "meta_sample_aggregation": (
            "mutually_exclusive_top_100_ranked_asset_sample"
            if sampled_meta
            else "unavailable"
        ),
    }
    return items, results, coverage


async def _empty_memecoin_catalog():
    return [], [], {}


def dynamic_symbols(
    catalog_items: list[dict[str, Any]],
    focus_assets: list[str],
    *,
    maximum: int = MAX_DYNAMIC_SYMBOLS,
) -> list[str]:
    """Select a current liquid universe while excluding cash and wrappers."""
    ranked = sorted(
        (
            item
            for item in catalog_items
            if item.get("metric") == "market_catalog_asset"
            and item.get("eligible_for_liquid_universe") is True
        ),
        key=lambda item: int(item.get("rank") or 10_000),
    )
    venue_tagged = [
        item
        for item in ranked
        if "binance-listing" in {str(tag).casefold() for tag in item.get("tags") or []}
    ]
    if len(venue_tagged) >= max(6, maximum // 2):
        ranked = venue_tagged
    output = ["BTC", "ETH"]
    for value in focus_assets:
        symbol = str(value).strip().upper()
        if symbol and symbol.isalnum() and symbol not in output:
            output.append(symbol)
    for item in ranked:
        symbol = str(item.get("symbol") or "").upper()
        if symbol and symbol not in output:
            output.append(symbol)
        if len(output) >= maximum:
            break
    return output[:maximum]


def _listing_items(result: FetchResult) -> list[dict[str, Any]]:
    if result.status != "complete":
        return []
    payload = result.data if isinstance(result.data, dict) else {}
    rows = payload.get("data") if isinstance(payload, dict) else []
    if isinstance(rows, dict):
        rows = rows.get("cryptoCurrencyList") or rows.get("data") or []
    output = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        quote = _usd_quote(row)
        symbol = str(row.get("symbol") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        rank = _integer(row.get("cmc_rank") or row.get("rank"))
        market_cap = safe_float(quote.get("market_cap"))
        if not symbol or rank is None or market_cap is None:
            continue
        tags = [str(value) for value in row.get("tags") or []]
        source_time = str(
            quote.get("last_updated") or row.get("last_updated") or result.retrieved_at
        )
        output.append(
            {
                "evidence_id": evidence_id(
                    "coinmarketcap",
                    f"ranking:{row.get('id') or symbol}",
                    source_time,
                ),
                "provider_id": "coinmarketcap",
                "source_family": "market_catalog",
                "metric": "market_catalog_asset",
                "source_time": source_time,
                "retrieved_at": result.retrieved_at,
                "provider_asset_id": row.get("id"),
                "name": name,
                "symbol": symbol,
                "slug": row.get("slug"),
                "rank": rank,
                "price_usd": safe_float(quote.get("price")),
                "market_cap_usd": market_cap,
                "market_cap_dominance_pct": safe_float(
                    quote.get("market_cap_dominance")
                ),
                "volume_24h_usd": safe_float(quote.get("volume_24h")),
                "price_change_24h_pct": safe_float(quote.get("percent_change_24h")),
                "price_change_7d_pct": safe_float(quote.get("percent_change_7d")),
                "price_change_30d_pct": safe_float(quote.get("percent_change_30d")),
                "tags": tags[:48],
                "eligible_for_liquid_universe": _liquid_universe_eligible(
                    symbol,
                    name,
                    tags,
                ),
            }
        )
    return output


def _global_metrics_item(result: FetchResult) -> dict[str, Any] | None:
    data = (result.data or {}).get("data") if isinstance(result.data, dict) else {}
    if not isinstance(data, dict):
        return None
    quote = _usd_quote(data)
    source_time = str(
        quote.get("last_updated") or data.get("last_updated") or result.retrieved_at
    )
    market_cap = safe_float(quote.get("total_market_cap"))
    if market_cap is None:
        return None
    return {
        "evidence_id": evidence_id(
            "coinmarketcap",
            "global_crypto_metrics",
            source_time,
        ),
        "provider_id": "coinmarketcap",
        "source_family": "market_catalog",
        "metric": "global_crypto_metrics",
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        "total_market_cap_usd": market_cap,
        "total_market_cap_change_24h_pct": safe_float(
            quote.get("total_market_cap_yesterday_percentage_change")
        ),
        "total_volume_24h_usd": safe_float(quote.get("total_volume_24h")),
        "total_volume_change_24h_pct": safe_float(
            quote.get("total_volume_24h_yesterday_percentage_change")
        ),
        "btc_dominance_pct": safe_float(data.get("btc_dominance")),
        "btc_dominance_change_24h_pct": safe_float(
            data.get("btc_dominance_24h_percentage_change")
        ),
        "eth_dominance_pct": safe_float(data.get("eth_dominance")),
        "eth_dominance_change_24h_pct": safe_float(
            data.get("eth_dominance_24h_percentage_change")
        ),
        "stablecoin_market_cap_usd": safe_float(quote.get("stablecoin_market_cap")),
        "defi_market_cap_usd": safe_float(quote.get("defi_market_cap")),
    }


def _fear_greed_item(result: FetchResult) -> dict[str, Any] | None:
    data = (result.data or {}).get("data") if isinstance(result.data, dict) else {}
    if not isinstance(data, dict):
        return None
    value = safe_float(data.get("value"))
    if value is None:
        return None
    source_time = str(data.get("update_time") or result.retrieved_at)
    return {
        "evidence_id": evidence_id(
            "coinmarketcap",
            "crypto_fear_greed",
            source_time,
        ),
        "provider_id": "coinmarketcap",
        "source_family": "sentiment",
        "metric": "crypto_fear_greed",
        "source_time": source_time,
        "retrieved_at": result.retrieved_at,
        "value": value,
        "classification": str(data.get("value_classification") or ""),
    }


def _meta_category_items(result: FetchResult) -> list[dict[str, Any]]:
    payload = result.data if isinstance(result.data, dict) else {}
    rows = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("title") or "").strip()
        meta, specificity = _category_meta(name)
        market_cap = safe_float(row.get("market_cap"))
        if meta is None or market_cap is None or market_cap <= 0:
            continue
        current = selected.get(meta)
        if current and current[0] > specificity:
            continue
        if current and current[0] == specificity:
            current_cap = safe_float(current[1].get("market_cap")) or 0.0
            if current_cap >= market_cap:
                continue
        selected[meta] = (specificity, row)

    output = []
    for meta in MEMECOIN_META_ORDER:
        if meta not in selected:
            continue
        row = selected[meta][1]
        name = str(row.get("name") or row.get("title") or meta)
        category_metadata_updated_at = str(row.get("last_updated") or "")
        source_time = result.retrieved_at
        output.append(
            {
                "evidence_id": evidence_id(
                    "coinmarketcap",
                    f"meta_category:{row.get('id') or name}",
                    source_time,
                ),
                "provider_id": "coinmarketcap",
                "source_family": "market_catalog",
                "metric": "memecoin_meta_category",
                "source_time": source_time,
                "retrieved_at": result.retrieved_at,
                "category_metadata_updated_at": category_metadata_updated_at,
                "primary_meta": meta,
                "provider_category_id": row.get("id"),
                "provider_category_name": name,
                "market_cap_usd": safe_float(row.get("market_cap")),
                "market_cap_change_24h_pct": safe_float(
                    row.get("market_cap_change") or row.get("market_cap_change_24h")
                ),
                "volume_24h_usd": safe_float(
                    row.get("volume") or row.get("volume_24h")
                ),
                "volume_change_24h_pct": safe_float(row.get("volume_change")),
                "constituent_count": _integer(row.get("num_tokens")),
                "aggregation_basis": "provider_category_non_additive",
                "categories_may_overlap": True,
            }
        )
    return output


def _sampled_meta_items(
    listings: list[dict[str, Any]],
    result: FetchResult,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in listings:
        tags = [str(value).casefold() for value in row.get("tags") or []]
        symbol = str(row.get("symbol") or "").upper()
        name = str(row.get("name") or "")
        if not _is_memecoin(symbol, name, tags):
            continue
        meta = _asset_primary_meta(name, tags)
        if meta:
            grouped[meta].append(row)

    output = []
    for meta in MEMECOIN_META_ORDER:
        rows = grouped.get(meta) or []
        if not rows:
            continue
        market_cap = sum(safe_float(row.get("market_cap_usd")) or 0.0 for row in rows)
        if market_cap <= 0:
            continue
        weighted_change = (
            sum(
                (safe_float(row.get("market_cap_usd")) or 0.0)
                * (safe_float(row.get("price_change_24h_pct")) or 0.0)
                for row in rows
            )
            / market_cap
        )
        ordered = sorted(
            rows,
            key=lambda row: safe_float(row.get("market_cap_usd")) or 0.0,
            reverse=True,
        )
        source_time = max(
            (str(row.get("source_time") or "") for row in rows),
            default=result.retrieved_at,
        )
        output.append(
            {
                "evidence_id": evidence_id(
                    "coinmarketcap",
                    f"sampled_meta:{meta}",
                    source_time,
                ),
                "provider_id": "coinmarketcap",
                "source_family": "market_catalog",
                "metric": "memecoin_meta_sample",
                "source_time": source_time,
                "retrieved_at": result.retrieved_at,
                "primary_meta": meta,
                "sample_market_cap_usd": round(market_cap, 2),
                "sample_market_cap_weighted_return_24h_pct": round(
                    weighted_change,
                    4,
                ),
                "sample_volume_24h_usd": round(
                    sum(safe_float(row.get("volume_24h_usd")) or 0.0 for row in rows),
                    2,
                ),
                "constituent_count": len(rows),
                "representative_symbols": [
                    str(row.get("symbol") or "") for row in ordered[:3]
                ],
                "aggregation_basis": "mutually_exclusive_top_100_ranked_asset_sample",
                "categories_may_overlap": False,
            }
        )
    return output


def _usd_quote(row: dict[str, Any]) -> dict[str, Any]:
    quote = row.get("quote")
    if isinstance(quote, list):
        return next(
            (
                value
                for value in quote
                if isinstance(value, dict)
                and str(value.get("symbol") or "").upper() == "USD"
            ),
            quote[0] if quote and isinstance(quote[0], dict) else {},
        )
    if isinstance(quote, dict):
        value = quote.get("USD")
        return value if isinstance(value, dict) else quote
    return {}


def _liquid_universe_eligible(symbol: str, name: str, tags: list[str]) -> bool:
    lowered_tags = {value.casefold() for value in tags}
    lowered_name = name.casefold()
    if lowered_tags & _STABLE_TAGS:
        return False
    if any(marker in lowered_name for marker in _WRAPPED_OR_STAKED_MARKERS):
        return False
    if symbol.startswith(("WST", "ST")) and "staking" in " ".join(lowered_tags):
        return False
    return symbol.isalnum() and 1 <= len(symbol) <= 12


def _category_meta(name: str) -> tuple[str | None, int]:
    value = name.casefold()
    words = set(re.findall(r"[a-z0-9]+", value))
    meme_words = {
        "meme",
        "memes",
        "memecoin",
        "memecoins",
        "doggerel",
        "cat",
        "cats",
        "feline",
        "frog",
        "pepe",
    }
    if not (meme_words & words):
        return None, 0
    if {"political", "politifi", "trump"} & words:
        return "political", 5
    if "celebrity" in words:
        return "celebrity", 5
    if ("ai" in words or "artificial intelligence" in value) and {
        "meme",
        "memes",
        "memecoin",
        "memecoins",
    } & words:
        return "ai", 5
    if {"cat", "cats", "feline"} & words:
        return "cat", 4
    if {"frog", "pepe"} & words:
        return "frog", 4
    if {"dog", "dogs", "shiba", "doggerel"} & words:
        return "dog", 4
    # "Memes" and equivalent names describe the whole sector, not a distinct
    # theme. Including that umbrella beside its subcategories double counts the
    # same assets and makes the theme comparison misleading.
    return None, 0


def _is_memecoin(symbol: str, name: str, tags: list[str]) -> bool:
    del symbol
    haystack = " ".join([name.casefold(), *tags])
    words = set(re.findall(r"[a-z0-9]+", haystack))
    return bool(
        {"meme", "memes", "memecoin", "memecoins"} & words
        or {"doggone-doggerel", "cat-themed"} & set(tags)
    )


def _asset_primary_meta(
    name: str,
    tags: list[str],
    description: str = "",
) -> str | None:
    value = " ".join([name.casefold(), description.casefold(), *tags])
    words = set(re.findall(r"[a-z0-9]+", value))
    if {"politifi", "political", "trump", "melania"} & words:
        return "political"
    if "celebrity" in words:
        return "celebrity"
    if "ai-meme" in value or "ai meme" in value or "ai" in words:
        return "ai"
    if {"cat", "cats", "feline", "kitten", "kitty"} & words or "cat-themed" in value:
        return "cat"
    if {"frog", "frogs", "pepe"} & words:
        return "frog"
    if {"dog", "dogs", "doge", "shiba", "doggerel"} & words:
        return "dog"
    return None


def primary_meta_for_asset(
    symbol: str,
    name: str = "",
    tags: list[str] | None = None,
    description: str = "",
) -> str | None:
    """Classify only assets supported by provider metadata, not ticker lists."""
    normalized_tags = [str(value).casefold() for value in tags or []]
    provider_text = " ".join((name, description)).casefold()
    words = set(re.findall(r"[a-z0-9]+", provider_text))
    compact_text = re.sub(r"[^a-z0-9]+", "", provider_text)
    meme_markers = {
        "meme",
        "memes",
        "memecoin",
        "memecoins",
        "dog",
        "dogs",
        "doge",
        "shiba",
        "cat",
        "cats",
        "feline",
        "kitten",
        "kitty",
        "frog",
        "frogs",
        "pepe",
        "politifi",
        "political",
        "trump",
        "melania",
        "celebrity",
    }
    if not (
        _is_memecoin(symbol, name, normalized_tags)
        or meme_markers & words
        or any(marker in compact_text for marker in ("doge", "memecoin"))
        or "ai-meme" in provider_text
        or "ai meme" in provider_text
    ):
        return None
    return _asset_primary_meta(name, normalized_tags, description)


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
