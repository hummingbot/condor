"""Keyless organic-oriented GeckoTerminal pool discovery."""

from __future__ import annotations

import asyncio
from typing import Any

from agents.market_reporter.routines._evidence import safe_float
from agents.market_reporter.routines._http import FetchResult, fetch_json
from agents.market_reporter.routines._identity import GECKO_NETWORKS
from agents.market_reporter.routines._memecoin_metrics import normalize_dex_pair

REQUEST_INTERVAL_SEC = 0.75


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
