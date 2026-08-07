"""Private collector for a bounded identity-safe Memecoin discovery universe."""

import asyncio
from typing import Any, Literal

from pydantic import Field, field_validator

from agents.market_reporter.routines._evidence import (
    bundle_text,
    finalize_bundle,
    safe_float,
)
from agents.market_reporter.routines._http import FetchResult, fetch_json
from agents.market_reporter.routines._identity import (
    GECKO_NETWORKS,
    SUPPORTED_DISCOVERY_CHAINS,
    normalize_address,
    normalize_chain,
    registry_is_current,
)
from agents.market_reporter.routines._models import BaseSourceConfig
from agents.market_reporter.routines._token_selection import (
    build_items,
    confirm_contracts,
    coverage,
    fetch_stock_token_exclusions,
    normalize_dex_pair,
    observe_concentration,
    seed_tokens,
    select_pairs,
)
from routines.base import RoutineResult

CATEGORY = "Market Reporter"
REQUEST_INTERVAL_SEC = 0.75


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


async def collect_gecko(
    chains: list[str],
) -> tuple[list[dict[str, Any]], list[FetchResult]]:
    requests = []
    metadata = []
    for chain in chains:
        network = GECKO_NETWORKS.get(chain)
        if not network:
            continue
        for endpoint, origin in (
            ("new_pools", "organic_oriented:new_pool"),
            ("trending_pools", "organic_oriented:trending_pool"),
        ):
            requests.append(
                (
                    f"https://api.geckoterminal.com/api/v2/networks/{network}/{endpoint}",
                    {"page": 1},
                )
            )
            metadata.append((chain, endpoint, origin))
    results = await _sequential_fetches(requests)
    pairs = []
    for (chain, endpoint, origin), result in zip(metadata, results):
        if result.status != "complete":
            continue
        for raw in (result.data or {}).get("data") or []:
            candidate = dict(raw)
            candidate["network"] = chain
            normalized = normalize_dex_pair(candidate, origin=origin)
            if normalized:
                pairs.append(normalized)
    return pairs, results


async def collect_gecko_details(
    pairs: list[dict[str, Any]],
    *,
    maximum: int = 1,
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[FetchResult]]:
    selected = []
    seen_chains = set()
    for pair in pairs:
        chain = pair.get("chain_id")
        if chain not in GECKO_NETWORKS or chain in seen_chains:
            continue
        if not any(
            str(origin).startswith(("organic_oriented", "known_address:geckoterminal"))
            for origin in pair.get("discovery_origins")
            or [pair.get("discovery_origin")]
        ):
            continue
        selected.append(pair)
        seen_chains.add(chain)
        if len(selected) >= maximum:
            break
    requests = []
    for pair in selected:
        network = GECKO_NETWORKS[pair["chain_id"]]
        address = pair["pair_address"]
        requests.extend(
            [
                (
                    f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{address}/ohlcv/hour",
                    {"aggregate": 1, "limit": 24, "currency": "usd"},
                ),
                (
                    f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{address}/trades",
                    None,
                ),
            ]
        )
    results = await _sequential_fetches(requests)
    details = {}
    for index, pair in enumerate(selected):
        ohlcv_result = results[index * 2]
        trades_result = results[index * 2 + 1]
        ohlcv_rows = (
            (((ohlcv_result.data or {}).get("data") or {}).get("attributes") or {}).get(
                "ohlcv_list"
            )
            or []
            if ohlcv_result.status == "complete"
            else []
        )
        trades = (
            (trades_result.data or {}).get("data") or []
            if trades_result.status == "complete"
            else []
        )
        details[(pair["chain_id"], pair["pair_address"])] = {
            "ohlcv_status": ohlcv_result.status,
            "trade_status": trades_result.status,
            "hourly_observation_count": len(ohlcv_rows),
            "latest_hour_close_usd": (
                safe_float(ohlcv_rows[0][4])
                if ohlcv_rows and len(ohlcv_rows[0]) > 4
                else None
            ),
            "recent_trade_count": len(trades),
        }
    return details, results


async def _sequential_fetches(
    requests: list[tuple[str, dict[str, Any] | None]],
) -> list[FetchResult]:
    results = []
    for index, (url, params) in enumerate(requests):
        if index:
            await asyncio.sleep(REQUEST_INTERVAL_SEC)
        results.append(
            await fetch_json(
                "geckoterminal",
                url,
                params=params,
                retry=False,
            )
        )
    return results


class Config(BaseSourceConfig):
    """Discover exact Solana, Ethereum, and Robinhood Memecoin pairs."""

    chains: list[Literal["solana", "ethereum", "robinhood"]] = Field(
        default_factory=lambda: ["solana", "ethereum", "robinhood"],
        min_length=1,
        max_length=3,
    )
    min_pair_age_hours: float = Field(default=6, ge=0, le=720)
    min_liquidity_usd: float = Field(default=50_000, ge=0, le=100_000_000)
    max_discovery_candidates: int = Field(default=100, ge=10, le=100)
    max_detailed_candidates: int = Field(default=40, ge=5, le=40)
    max_gecko_detail_candidates: int = Field(default=1, ge=0, le=1)
    max_solana_concentration_candidates: int = Field(default=1, ge=0, le=1)

    @field_validator("chains")
    @classmethod
    def unique_supported_chains(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Discovery chains must be unique")
        if not set(values).issubset(SUPPORTED_DISCOVERY_CHAINS):
            raise ValueError("Unsupported discovery chain")
        return values


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    if config.strategy_key != "memecoin_market_intelligence":
        raise ValueError("Token discovery is Memecoin Strategy-only")
    async with asyncio.timeout(40):
        registry_task = (
            fetch_stock_token_exclusions()
            if "robinhood" in config.chains
            else _empty_registry()
        )
        (exclusions, stock_symbols, registry_fresh, registry_results), (
            gecko_pairs,
            gecko_results,
        ) = await asyncio.gather(registry_task, collect_gecko(config.chains))
        seeds = seed_tokens(
            chains=config.chains,
            focus_assets=config.focus_assets,
            gecko_pairs=gecko_pairs,
        )
        dex_pairs, dex_results, promotion_flags = await collect_dexscreener(
            seeds[: config.max_discovery_candidates],
            maximum_details=config.max_detailed_candidates,
        )
        selected = select_pairs(
            gecko_pairs + dex_pairs,
            maximum=config.max_detailed_candidates,
        )
        robinhood_addresses = [
            pair["token_address"]
            for pair in selected
            if pair["chain_id"] == "robinhood"
        ]
        confirmation_task = (
            confirm_contracts(robinhood_addresses)
            if robinhood_addresses
            else _empty_observations()
        )
        solana_addresses = [
            pair["token_address"] for pair in selected if pair["chain_id"] == "solana"
        ]
        solana_task = (
            observe_concentration(
                solana_addresses[: config.max_solana_concentration_candidates]
            )
            if solana_addresses and config.max_solana_concentration_candidates
            else _empty_observations()
        )
        gecko_task = (
            collect_gecko_details(
                selected,
                maximum=config.max_gecko_detail_candidates,
            )
            if config.max_gecko_detail_candidates
            else _empty_observations()
        )
        (
            (confirmations, confirmation_results),
            (solana_observations, solana_results),
            (gecko_details, gecko_detail_results),
        ) = await asyncio.gather(
            confirmation_task,
            solana_task,
            gecko_task,
        )

    items = build_items(
        selected,
        min_pair_age_hours=config.min_pair_age_hours,
        min_liquidity_usd=config.min_liquidity_usd,
        exclusions=exclusions,
        stock_symbols=stock_symbols,
        registry_fresh=registry_fresh,
        promotion_flags=promotion_flags,
        confirmations=confirmations,
        solana_observations=solana_observations,
        robinhood_registry_retrieved_at=(
            registry_results[0].retrieved_at if registry_results else ""
        ),
        gecko_details=gecko_details,
    )
    provider_results = (
        registry_results
        + gecko_results
        + dex_results
        + confirmation_results
        + solana_results
        + gecko_detail_results
    )
    successful_dexscreener = any(
        result.provider_id == "dexscreener" and result.status == "complete"
        for result in provider_results
    )
    optional_degradations = [
        result
        for result in provider_results
        if result.status != "complete"
        and (
            result.provider_id
            in {
                "geckoterminal",
                "solana_rpc",
                "robinhood_blockscout",
            }
            or (result.provider_id == "dexscreener" and successful_dexscreener)
        )
    ]
    if items and optional_degradations:
        provider_results = [
            result for result in provider_results if result not in optional_degradations
        ]
    coverage_payload = coverage(
        items,
        chains=config.chains,
        registry_fresh=registry_fresh,
    )
    coverage_payload["optional_source_degradations"] = [
        {
            "provider_id": result.provider_id,
            "error": result.error,
            "status_code": result.status_code,
        }
        for result in optional_degradations
    ]
    optional_warnings = []
    for result in optional_degradations:
        label = (
            "optional_request_degraded"
            if result.provider_id == "dexscreener"
            else "optional_source_unavailable"
        )
        optional_warnings.append(f"{label}:{result.provider_id}:{result.error}")
    bundle = finalize_bundle(
        source_type="token_discovery",
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=items,
        provider_results=provider_results,
        coverage=coverage_payload,
        warnings=(
            ["robinhood_stock_token_registry_unavailable"]
            if "robinhood" in config.chains and not registry_fresh
            else []
        )
        + (
            ["approved_quote_registry_stale"]
            if not registry_is_current("approved_quotes")
            else []
        )
        + ["geckoterminal_top_pool_scan_omitted_public_rate_budget"]
        + optional_warnings,
    )
    table = [
        {
            "chain": item["chain_id"],
            "symbol": item["market"].get("symbol"),
            "token": item["token_address"],
            "liquidity_usd": item["market"].get("liquidity_usd"),
            "eligibility": item["eligibility"],
            "paid_visibility": item["paid_visibility"],
        }
        for item in bundle["items"]
    ]
    return RoutineResult(
        text=bundle_text(bundle, config.run_id),
        table_data=table,
        table_columns=[
            "chain",
            "symbol",
            "token",
            "liquidity_usd",
            "eligibility",
            "paid_visibility",
        ],
    )


async def _empty_registry():
    return set(), {}, True, []


async def _empty_observations():
    return {}, []
