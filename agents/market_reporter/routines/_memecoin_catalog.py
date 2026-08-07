"""Provider-maintained Memecoin taxonomy and bounded constituent coverage."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from typing import Any

from agents.market_reporter.routines._evidence import evidence_id, safe_float
from agents.market_reporter.routines._http import FetchResult, fetch_json

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
MEMECOIN_CATEGORY_LIMIT = 100
KEYLESS_THEME_LIMIT = 3
_CATEGORY_REQUEST_INTERVAL_SECONDS = 0.8
_RATE_LIMIT_RETRY_DELAY_SECONDS = 1.6
MEMECOIN_META_CATEGORIES = (
    ("dog", "dog-themed-coins", "Dog-themed"),
    ("cat", "cat-themed-coins", "Cat-themed"),
    ("frog", "frog-themed-coins", "Frog-themed"),
    ("political", "politifi", "Political"),
    ("ai", "ai-meme-coins", "AI-themed"),
    ("celebrity", "celebrity-themed-coins", "Celebrity"),
)
MEMECOIN_META_ORDER = tuple(row[0] for row in MEMECOIN_META_CATEGORIES)
MEMECOIN_META_LABELS = {row[0]: row[2] for row in MEMECOIN_META_CATEGORIES}
_CATEGORY_ID_TO_META = {row[1]: row[0] for row in MEMECOIN_META_CATEGORIES}
_CHAIN_CATEGORY_IDS = {
    "solana": "solana-meme-coins",
    "robinhood": "robinhood-chain-meme",
}


async def collect_memecoin_catalog(
    *,
    constituent_limit: int = MEMECOIN_CATEGORY_LIMIT,
) -> tuple[list[dict[str, Any]], list[FetchResult], dict[str, Any]]:
    """Collect broad category evidence separately from exact DEX pair screening."""
    headers = _coingecko_headers()
    summary_task = fetch_json(
        "coingecko",
        f"{COINGECKO_BASE_URL}/coins/categories",
        headers=headers,
        retry=False,
    )
    platform_task = fetch_json(
        "coingecko",
        f"{COINGECKO_BASE_URL}/coins/list",
        params={"include_platform": "true"},
        headers=headers,
        retry=False,
    )
    summary_result, platform_result = await asyncio.gather(
        summary_task,
        platform_task,
    )
    platform_map = _platform_map(platform_result)
    selected_theme_ids = (
        [row[1] for row in MEMECOIN_META_CATEGORIES]
        if headers
        else _keyless_theme_category_ids(summary_result, platform_map)
    )
    category_ids = [
        *selected_theme_ids,
        *(_CHAIN_CATEGORY_IDS.values() if headers else []),
    ]
    market_results = await _category_market_results(
        category_ids,
        constituent_limit,
        headers,
    )
    results = [summary_result, platform_result, *market_results]
    markets_by_category = {
        category_id: _market_rows(result)
        for category_id, result in zip(category_ids, market_results)
    }
    categorized_assets = _categorized_assets(
        markets_by_category,
        platform_map,
    )
    category_items = _category_items(
        summary_result,
        markets_by_category,
        constituent_limit,
    )
    chain_items = _chain_meta_items(categorized_assets)
    items = [*category_items, *categorized_assets, *chain_items]
    coverage = {
        "taxonomy_provider": "coingecko_public_api",
        "authentication": "free_demo_key" if headers else "keyless_shared_pool",
        "category_summary_status": summary_result.status,
        "platform_map_status": platform_result.status,
        "controlled_theme_count": len(category_items),
        "categorized_asset_count": len(categorized_assets),
        "chain_theme_observation_count": len(chain_items),
        "constituent_limit_per_theme": constituent_limit,
        "constituent_scope": (
            "top_market_cap_constituents_for_all_controlled_themes"
            if headers
            else "largest_fastest_mover_and_robinhood_anchor_themes"
        ),
        "selected_theme_category_ids": selected_theme_ids,
        "theme_status": {
            meta: (
                market_results[category_ids.index(category_id)].status
                if category_id in category_ids
                else "not_requested_keyless_budget"
            )
            for meta, category_id, _ in MEMECOIN_META_CATEGORIES
        },
        "chain_membership_status": {
            chain: (
                market_results[category_ids.index(category_id)].status
                if category_id in category_ids
                else "inferred_from_provider_platform_map"
            )
            for chain, category_id in _CHAIN_CATEGORY_IDS.items()
        },
    }
    return items, results, coverage


def _keyless_theme_category_ids(
    result: FetchResult,
    platform_map: dict[str, dict[str, str]],
) -> list[str]:
    if result.status != "complete" or not isinstance(result.data, list):
        return [row[1] for row in MEMECOIN_META_CATEGORIES[:KEYLESS_THEME_LIMIT]]
    summaries = {
        str(row.get("id")): row
        for row in result.data
        if isinstance(row, dict) and row.get("id") in _CATEGORY_ID_TO_META
    }
    available = [
        category_id
        for _, category_id, _ in MEMECOIN_META_CATEGORIES
        if category_id in summaries
    ]
    selections = []
    rankings = (
        sorted(
            available,
            key=lambda category_id: (
                safe_float(summaries[category_id].get("market_cap"))
                if safe_float(summaries[category_id].get("market_cap")) is not None
                else 0.0
            ),
            reverse=True,
        ),
        sorted(
            available,
            key=lambda category_id: (
                abs(
                    safe_float(summaries[category_id].get("market_cap_change_24h"))
                    or 0.0
                )
            ),
            reverse=True,
        ),
        sorted(
            (
                category_id
                for category_id in available
                if any(
                    "robinhood" in platform_map.get(str(coin_id), {})
                    for coin_id in summaries[category_id].get("top_3_coins_id") or []
                )
            ),
            key=lambda category_id: safe_float(summaries[category_id].get("market_cap"))
            or 0.0,
            reverse=True,
        ),
    )
    for ranking in rankings:
        if ranking and ranking[0] not in selections:
            selections.append(ranking[0])
    for category_id in available:
        if category_id not in selections:
            selections.append(category_id)
        if len(selections) >= KEYLESS_THEME_LIMIT:
            break
    return selections[:KEYLESS_THEME_LIMIT]


async def _category_market_results(
    category_ids: list[str],
    constituent_limit: int,
    headers: dict[str, str] | None,
) -> list[FetchResult]:
    results = []
    for index, category_id in enumerate(category_ids):
        if index:
            await asyncio.sleep(_CATEGORY_REQUEST_INTERVAL_SECONDS)
        results.append(
            await _category_market_result(category_id, constituent_limit, headers)
        )
    for index, result in enumerate(results):
        if result.status_code != 429:
            continue
        await asyncio.sleep(_RATE_LIMIT_RETRY_DELAY_SECONDS)
        results[index] = await _category_market_result(
            category_ids[index],
            constituent_limit,
            headers,
        )
    return results


async def _category_market_result(
    category_id: str,
    constituent_limit: int,
    headers: dict[str, str] | None,
) -> FetchResult:
    return await fetch_json(
        "coingecko",
        f"{COINGECKO_BASE_URL}/coins/markets",
        params={
            "vs_currency": "usd",
            "category": category_id,
            "order": "market_cap_desc",
            "per_page": constituent_limit,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
            "locale": "en",
        },
        headers=headers,
        timeout=6,
        retry=False,
    )


def _coingecko_headers() -> dict[str, str] | None:
    key = os.getenv("COINGECKO_DEMO_API_KEY", "").strip()
    return {"x-cg-demo-api-key": key} if key else None


def _market_rows(result: FetchResult) -> list[dict[str, Any]]:
    if result.status != "complete" or not isinstance(result.data, list):
        return []
    return [row for row in result.data if isinstance(row, dict) and row.get("id")]


def _platform_map(result: FetchResult) -> dict[str, dict[str, str]]:
    if result.status != "complete" or not isinstance(result.data, list):
        return {}
    output = {}
    for row in result.data:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        platforms = row.get("platforms")
        output[str(row["id"])] = (
            {
                str(chain): str(address)
                for chain, address in platforms.items()
                if address
            }
            if isinstance(platforms, dict)
            else {}
        )
    return output


def _category_items(
    result: FetchResult,
    markets_by_category: dict[str, list[dict[str, Any]]],
    constituent_limit: int,
) -> list[dict[str, Any]]:
    if result.status != "complete" or not isinstance(result.data, list):
        return []
    summaries = {
        str(row.get("id")): row
        for row in result.data
        if isinstance(row, dict) and row.get("id")
    }
    output = []
    for meta, category_id, label in MEMECOIN_META_CATEGORIES:
        row = summaries.get(category_id)
        if not row:
            continue
        market_cap = safe_float(row.get("market_cap"))
        if market_cap is None or market_cap <= 0:
            continue
        constituents_requested = category_id in markets_by_category
        sampled_rows = markets_by_category.get(category_id) or []
        source_time = str(row.get("updated_at") or result.retrieved_at)
        output.append(
            {
                "evidence_id": evidence_id(
                    "coingecko",
                    f"meta_category:{category_id}",
                    source_time,
                ),
                "provider_id": "coingecko",
                "source_family": "market_catalog",
                "metric": "memecoin_meta_category",
                "source_time": source_time,
                "retrieved_at": result.retrieved_at,
                "primary_meta": meta,
                "provider_category_id": category_id,
                "provider_category_name": str(row.get("name") or label),
                "market_cap_usd": market_cap,
                "market_cap_change_24h_pct": safe_float(
                    row.get("market_cap_change_24h")
                ),
                "volume_24h_usd": safe_float(row.get("volume_24h")),
                "sampled_constituent_count": (
                    len(sampled_rows) if constituents_requested else None
                ),
                "constituent_count": (
                    len(sampled_rows)
                    if constituents_requested and len(sampled_rows) < constituent_limit
                    else None
                ),
                "constituent_count_complete": (
                    len(sampled_rows) < constituent_limit
                    if constituents_requested
                    else None
                ),
                "representative_coin_ids": [
                    str(value) for value in row.get("top_3_coins_id") or []
                ][:3],
                "aggregation_basis": "provider_category_non_additive",
                "categories_may_overlap": True,
                "url": f"https://www.coingecko.com/en/categories/{category_id}",
            }
        )
    return output


def _categorized_assets(
    markets_by_category: dict[str, list[dict[str, Any]]],
    platform_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    memberships: dict[str, set[str]] = defaultdict(set)
    assets: dict[str, dict[str, Any]] = {}
    for category_id, meta in _CATEGORY_ID_TO_META.items():
        for row in markets_by_category.get(category_id) or []:
            coin_id = str(row["id"])
            memberships[coin_id].add(meta)
            current = assets.get(coin_id)
            current_cap = safe_float((current or {}).get("market_cap")) or -1.0
            if (safe_float(row.get("market_cap")) or -1.0) > current_cap:
                assets[coin_id] = row

    solana_ids = {
        str(row["id"])
        for row in markets_by_category.get(_CHAIN_CATEGORY_IDS["solana"]) or []
    }
    robinhood_ids = {
        str(row["id"])
        for row in markets_by_category.get(_CHAIN_CATEGORY_IDS["robinhood"]) or []
    }
    output = []
    for coin_id, row in assets.items():
        source_time = str(row.get("last_updated") or "")
        platforms = platform_map.get(coin_id, {})
        primary_chain = _primary_chain(
            coin_id,
            platforms,
            solana_ids=solana_ids,
            robinhood_ids=robinhood_ids,
        )
        output.append(
            {
                "evidence_id": evidence_id(
                    "coingecko",
                    f"categorized_memecoin:{coin_id}",
                    source_time,
                ),
                "provider_id": "coingecko",
                "source_family": "market_catalog",
                "metric": "memecoin_categorized_asset",
                "source_time": source_time,
                "provider_asset_id": coin_id,
                "name": str(row.get("name") or ""),
                "symbol": str(row.get("symbol") or "").upper(),
                "controlled_metas": [
                    meta for meta in MEMECOIN_META_ORDER if meta in memberships[coin_id]
                ],
                "primary_chain": primary_chain,
                "platforms": {
                    chain: platforms[chain]
                    for chain in ("ethereum", "solana", "robinhood")
                    if chain in platforms
                },
                "market_cap_usd": safe_float(row.get("market_cap")),
                "volume_24h_usd": safe_float(row.get("total_volume")),
                "price_change_24h_pct": safe_float(
                    row.get("price_change_percentage_24h")
                ),
                "market_cap_rank": row.get("market_cap_rank"),
                "url": f"https://www.coingecko.com/en/coins/{coin_id}",
            }
        )
    output.sort(
        key=lambda row: safe_float(row.get("market_cap_usd")) or 0.0,
        reverse=True,
    )
    return output


def _primary_chain(
    coin_id: str,
    platforms: dict[str, str],
    *,
    solana_ids: set[str],
    robinhood_ids: set[str],
) -> str | None:
    if coin_id in robinhood_ids:
        return "robinhood"
    if coin_id in solana_ids:
        return "solana"
    if "robinhood" in platforms:
        return "robinhood"
    for chain in platforms:
        if chain in {"ethereum", "solana"}:
            return chain
    return None


def _chain_meta_items(
    categorized_assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in categorized_assets:
        chain = row.get("primary_chain")
        if chain not in {"ethereum", "solana", "robinhood"}:
            continue
        for meta in row.get("controlled_metas") or []:
            grouped[(chain, str(meta))].append(row)

    output = []
    for (chain, meta), rows in grouped.items():
        market_cap = sum(safe_float(row.get("market_cap_usd")) or 0.0 for row in rows)
        weighted_rows = [
            (cap, change)
            for row in rows
            if (cap := safe_float(row.get("market_cap_usd"))) is not None
            and cap > 0
            and (change := safe_float(row.get("price_change_24h_pct"))) is not None
        ]
        weighted_cap = sum(row[0] for row in weighted_rows)
        ordered = sorted(
            rows,
            key=lambda row: safe_float(row.get("market_cap_usd")) or 0.0,
            reverse=True,
        )
        source_time = max(
            (str(row.get("source_time") or "") for row in rows),
            default="",
        )
        output.append(
            {
                "evidence_id": evidence_id(
                    "coingecko",
                    f"meta_chain_sample:{chain}:{meta}",
                    source_time,
                ),
                "provider_id": "coingecko",
                "source_family": "market_catalog",
                "metric": "memecoin_meta_chain_sample",
                "source_time": source_time,
                "primary_meta": meta,
                "chain": chain,
                "sampled_constituent_count": len(rows),
                "sample_market_cap_usd": round(market_cap, 2),
                "sample_volume_24h_usd": round(
                    sum(safe_float(row.get("volume_24h_usd")) or 0.0 for row in rows),
                    2,
                ),
                "market_cap_weighted_change_24h_pct": (
                    round(
                        sum(cap * change for cap, change in weighted_rows)
                        / weighted_cap,
                        4,
                    )
                    if weighted_cap
                    else None
                ),
                "representative_symbols": [
                    str(row.get("symbol") or "") for row in ordered[:4]
                ],
                "aggregation_basis": (
                    "top_provider_category_constituents_assigned_to_primary_chain"
                ),
                "categories_may_overlap": True,
            }
        )
    output.sort(
        key=lambda row: safe_float(row.get("sample_market_cap_usd")) or 0.0,
        reverse=True,
    )
    return output
