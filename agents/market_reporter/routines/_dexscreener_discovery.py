"""DEX Screener attention seeds and exact token-pair enrichment."""

from __future__ import annotations

import asyncio
from typing import Any

from agents.market_reporter.routines._http import FetchResult, fetch_json
from agents.market_reporter.routines._identity import normalize_address, normalize_chain
from agents.market_reporter.routines._memecoin_metrics import normalize_dex_pair


async def collect_dexscreener(
    seed_tokens: list[tuple[str, str]],
    *,
    maximum_details: int,
) -> tuple[
    list[dict[str, Any]],
    list[FetchResult],
    dict[tuple[str, str], dict[str, Any]],
]:
    feed_tasks = [
        fetch_json(
            "dexscreener",
            "https://api.dexscreener.com/token-profiles/latest/v1",
            retry=False,
        ),
        fetch_json(
            "dexscreener",
            "https://api.dexscreener.com/community-takeovers/latest/v1",
            retry=False,
        ),
        fetch_json(
            "dexscreener",
            "https://api.dexscreener.com/token-boosts/latest/v1",
            retry=False,
        ),
    ]
    feed_results: list[FetchResult] = list(await asyncio.gather(*feed_tasks))
    flags: dict[tuple[str, str], dict[str, Any]] = {}
    feed_names = ("profile", "community_takeover", "boost")
    for feed_name, result in zip(feed_names, feed_results):
        if result.status != "complete":
            continue
        rows = result.data if isinstance(result.data, list) else []
        for row in rows:
            chain = normalize_chain(str(row.get("chainId") or ""))
            address = normalize_address(chain, str(row.get("tokenAddress") or ""))
            if not chain or not address:
                continue
            entry = flags.setdefault(
                (chain, address),
                {
                    "profile": False,
                    "community_takeover": False,
                    "boost": False,
                    "paid_visibility": False,
                },
            )
            entry[feed_name] = True
            description = str(row.get("description") or "").strip()
            if description and not entry.get("provider_description"):
                entry["provider_description"] = description[:1000]
            links = [
                str(link.get("url") or "")
                for link in row.get("links") or []
                if isinstance(link, dict)
                and str(link.get("url") or "").startswith("https://")
            ]
            if links:
                entry["provider_links"] = list(
                    dict.fromkeys([*(entry.get("provider_links") or []), *links])
                )[:8]
            if feed_name == "boost":
                entry["paid_visibility"] = True
                entry["boost_amount"] = row.get("amount")
                entry["boost_total_amount"] = row.get("totalAmount")

    candidates = _interleave_attention(seed_tokens, list(flags))
    ordered = _round_robin_chains(candidates, maximum_details)
    pair_tasks = [
        fetch_json(
            "dexscreener",
            f"https://api.dexscreener.com/token-pairs/v1/{chain}/{address}",
            retry=False,
        )
        for chain, address in ordered
    ]
    order_tasks = [
        fetch_json(
            "dexscreener",
            f"https://api.dexscreener.com/orders/v1/{chain}/{address}",
            retry=False,
        )
        for chain, address in ordered
    ]
    detail_results: list[FetchResult] = list(
        await asyncio.gather(*(pair_tasks + order_tasks))
    )
    pairs = []
    pair_results = detail_results[: len(ordered)]
    order_results = detail_results[len(ordered) :]
    for (chain, address), result, order_result in zip(
        ordered, pair_results, order_results
    ):
        order_rows = (
            order_result.data
            if isinstance(order_result.data, list)
            else (order_result.data or {}).get("orders") or []
        )
        if order_rows:
            entry = flags.setdefault(
                (chain, address),
                {
                    "profile": False,
                    "community_takeover": False,
                    "boost": False,
                    "paid_visibility": False,
                },
            )
            entry["paid_order"] = True
            entry["paid_visibility"] = True
        if result.status != "complete":
            continue
        rows = result.data if isinstance(result.data, list) else []
        origin = (
            "paid_attention:dexscreener"
            if (flags.get((chain, address)) or {}).get("paid_visibility")
            else "known_address:dexscreener"
        )
        for raw in rows:
            normalized = normalize_dex_pair(raw, origin=origin)
            if normalized:
                pairs.append(normalized)
    return pairs, feed_results + detail_results, flags


def _round_robin_chains(
    candidates: list[tuple[str, str]], maximum: int
) -> list[tuple[str, str]]:
    buckets: dict[str, list[tuple[str, str]]] = {}
    for candidate in candidates:
        buckets.setdefault(candidate[0], []).append(candidate)
    ordered = []
    while len(ordered) < maximum:
        added = False
        for chain in ("solana", "ethereum", "robinhood"):
            bucket = buckets.get(chain) or []
            if bucket:
                ordered.append(bucket.pop(0))
                added = True
                if len(ordered) >= maximum:
                    break
        if not added:
            break
    return ordered


def _interleave_attention(
    seeds: list[tuple[str, str]],
    attention: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    seed_buckets: dict[str, list[tuple[str, str]]] = {}
    attention_buckets: dict[str, list[tuple[str, str]]] = {}
    for source, buckets in ((seeds, seed_buckets), (attention, attention_buckets)):
        for chain, address in source:
            key = (normalize_chain(chain), normalize_address(chain, address))
            if key[0] and key[1] and key not in buckets.setdefault(key[0], []):
                buckets[key[0]].append(key)
    output = []
    for chain in ("solana", "ethereum", "robinhood"):
        while seed_buckets.get(chain) or attention_buckets.get(chain):
            for bucket in (seed_buckets.get(chain), attention_buckets.get(chain)):
                if bucket:
                    candidate = bucket.pop(0)
                    if candidate not in output:
                        output.append(candidate)
    return output
