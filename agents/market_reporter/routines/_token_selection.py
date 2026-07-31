"""Deterministic Memecoin seed, pair, evidence, and coverage selection."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from agents.market_reporter.routines._crypto_catalog import primary_meta_for_asset
from agents.market_reporter.routines._evidence import evidence_id, utc_now
from agents.market_reporter.routines._identity import (
    APPROVED_QUOTES,
    DEXSCREENER_CHAINS,
    ESTABLISHED_MEMECOINS,
    GECKO_NETWORKS,
    MEMECOIN_INFRASTRUCTURE_SYMBOLS,
    normalize_address,
    normalize_chain,
    registry_is_current,
)
from agents.market_reporter.routines._memecoin_metrics import (
    add_quality_metrics,
    eligibility,
)


def seed_tokens(
    *,
    chains: list[str],
    focus_assets: list[str],
    gecko_pairs: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    seeds = []
    for row in ESTABLISHED_MEMECOINS:
        if row["chain"] in chains and not row["token_address"].startswith("native:"):
            seeds.append((row["chain"], row["token_address"]))
    for pair in gecko_pairs:
        seeds.append((pair["chain_id"], pair["token_address"]))
    for value in focus_assets:
        if ":" not in value:
            continue
        chain, address = value.split(":", 1)
        chain = normalize_chain(chain)
        if chain in chains and address:
            seeds.append((chain, normalize_address(chain, address)))
    return list(dict.fromkeys(seeds))


def select_pairs(pairs: list[dict[str, Any]], *, maximum: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        grouped[(pair["chain_id"], pair["token_address"])].append(pair)
    selected = []
    for (chain, _), rows in grouped.items():
        rows.sort(
            key=lambda row: (
                row["quote_token_address"] in APPROVED_QUOTES.get(chain, set()),
                float(row.get("liquidity_usd") or 0),
            ),
            reverse=True,
        )
        best = dict(rows[0])
        best["observed_pair_count"] = len(rows)
        best["identity_conflict"] = (
            len({str(row.get("symbol") or "").upper() for row in rows}) > 1
        )
        best["discovery_origins"] = sorted(
            {str(row.get("discovery_origin") or "") for row in rows}
        )
        selected.append(best)
    selected.sort(key=lambda row: float(row.get("liquidity_usd") or 0), reverse=True)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        buckets[row["chain_id"]].append(row)
    balanced = []
    while len(balanced) < maximum:
        added = False
        for chain in ("solana", "ethereum", "robinhood"):
            if buckets[chain]:
                balanced.append(buckets[chain].pop(0))
                added = True
                if len(balanced) >= maximum:
                    break
        if not added:
            break
    return balanced


def build_items(
    selected: list[dict[str, Any]],
    *,
    min_pair_age_hours: float,
    min_liquidity_usd: float,
    exclusions: set[str],
    stock_symbols: dict[str, str],
    registry_fresh: bool,
    promotion_flags: dict[tuple[str, str], dict[str, Any]],
    confirmations: dict[str, dict[str, Any]],
    solana_observations: dict[str, dict[str, Any]],
    robinhood_registry_retrieved_at: str,
    gecko_details: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    established = {
        (row["chain"], normalize_address(row["chain"], row["token_address"])): row
        for row in ESTABLISHED_MEMECOINS
    }
    items = []
    for pair in selected:
        chain = pair["chain_id"]
        token = pair["token_address"]
        state, reasons = eligibility(
            pair,
            min_pair_age_hours=min_pair_age_hours,
            min_liquidity_usd=min_liquidity_usd,
            robinhood_exclusions=exclusions,
            robinhood_registry_fresh=registry_fresh,
            quote_registry_fresh=registry_is_current("approved_quotes"),
        )
        key = (chain, token)
        flags = promotion_flags.get(key, {})
        quality = add_quality_metrics(pair)
        symbol = str(quality.get("symbol") or pair.get("symbol") or "").upper()
        registry_entry = established.get(key)
        primary_meta = (
            registry_entry.get("primary_meta")
            if registry_entry
            else primary_meta_for_asset(
                symbol,
                str(pair.get("name") or ""),
                description=str(flags.get("provider_description") or ""),
            )
        )
        classification_source = (
            "exact_established_contract"
            if registry_entry
            else "provider_name_or_description" if primary_meta else "unverified"
        )
        approved_base_addresses = {
            normalize_address(chain, value)
            for value in APPROVED_QUOTES.get(chain, set())
        }
        if (
            symbol in MEMECOIN_INFRASTRUCTURE_SYMBOLS
            or token in approved_base_addresses
        ):
            state = "excluded"
            reasons = sorted(set(reasons + ["non_memecoin_infrastructure"]))
        if not primary_meta:
            state = "excluded"
            reasons = sorted(set(reasons + ["memecoin_classification_unverified"]))
        origins = pair.get("discovery_origins") or [pair["discovery_origin"]]
        pair_created_at = str(pair.get("pair_created_at") or "")
        observation_time = utc_now()
        provider_id = (
            "geckoterminal"
            if any(origin.startswith("organic_oriented") for origin in origins)
            else "dexscreener"
        )
        if provider_id == "geckoterminal":
            network = GECKO_NETWORKS[chain]
            source_url = (
                "https://api.geckoterminal.com/api/v2/networks/"
                f"{network}/pools/{pair['pair_address']}"
            )
        else:
            network = DEXSCREENER_CHAINS[chain]
            source_url = (
                "https://api.dexscreener.com/latest/dex/pairs/"
                f"{network}/{pair['pair_address']}"
            )
        item = {
            "evidence_id": evidence_id(
                "dex_market",
                f"{chain}:{token}:{pair['pair_address']}",
                observation_time,
            ),
            "provider_id": provider_id,
            "source_family": "token_discovery",
            "source_time": observation_time,
            "observation_time": observation_time,
            "pair_created_at": pair_created_at,
            "chain_id": chain,
            "token_address": token,
            "pair_address": pair["pair_address"],
            "quote_token_address": pair["quote_token_address"],
            "primary_meta": primary_meta,
            "memecoin_classification": {
                "status": "confirmed" if primary_meta else "unverified",
                "source": classification_source,
                "provider_description": flags.get("provider_description"),
            },
            "cohort": (registry_entry or {}).get("cohort", "discovery"),
            "eligibility": state,
            "reason_codes": reasons,
            "discovery_origins": origins,
            "promotion_flags": flags,
            "paid_visibility": bool(flags.get("paid_visibility")),
            "observed_pair_count": pair.get("observed_pair_count", 1),
            "identity_conflict": pair.get("identity_conflict", False),
            "market": quality,
            "url": source_url,
            "display_url": f"https://dexscreener.com/{chain}/{pair['pair_address']}",
            "gecko_detail": gecko_details.get((chain, pair["pair_address"])),
        }
        if chain == "robinhood":
            confirmation = confirmations.get(token, {})
            if not confirmation.get("contract_code_present"):
                state = "excluded"
                reasons = sorted(set(reasons + ["unconfirmed_contract_code"]))
                item["eligibility"] = state
                item["reason_codes"] = reasons
            item["robinhood_identity"] = {
                **confirmation,
                "stock_token_registry_fresh": registry_fresh,
                "stock_token_registry_retrieved_at": robinhood_registry_retrieved_at,
                "stock_token_registry_age_days": _age_days(
                    robinhood_registry_retrieved_at
                ),
                "stock_token_symbol": stock_symbols.get(token),
                "discovery_coverage": (
                    "organic_oriented"
                    if any(origin.startswith("organic_oriented") for origin in origins)
                    else "promotion_biased"
                ),
            }
        elif chain == "solana":
            item["solana_concentration"] = solana_observations.get(
                token,
                {
                    "largest_accounts_status": "unavailable",
                    "token_supply_status": "unavailable",
                    "top_account_count_observed": 0,
                    "top_10_share_of_supply": None,
                    "interpretation_limit": (
                        "Concentration was not observed; no ownership or safety "
                        "inference is permitted."
                    ),
                },
            )
        items.append(item)
    items.sort(
        key=lambda item: (
            {"eligible": 0, "watch_only": 1, "excluded": 2}[item["eligibility"]],
            -float((item["market"].get("liquidity_usd") or 0)),
        )
    )
    return items


def _age_days(value: str) -> int | None:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - timestamp).days)


def coverage(
    items: list[dict[str, Any]],
    *,
    chains: list[str],
    registry_fresh: bool,
) -> dict[str, Any]:
    chain_counts = {}
    for chain in chains:
        rows = [item for item in items if item["chain_id"] == chain]
        chain_counts[chain] = {
            "observed": len(rows),
            "eligible": sum(row["eligibility"] == "eligible" for row in rows),
            "excluded": sum(row["eligibility"] == "excluded" for row in rows),
            "paid_visibility": sum(row["paid_visibility"] for row in rows),
        }
    return {
        "chain_counts": chain_counts,
        "robinhood_stock_token_registry_fresh": registry_fresh,
        "approved_quote_registry_fresh": registry_is_current("approved_quotes"),
        "geckoterminal_endpoint_plan": {
            "new_and_trending_pools": "bounded_requests_attempted",
            "top_pool_scan": "omitted_public_rate_budget",
            "bounded_ohlcv_and_trade_detail": "at_most_one_candidate",
        },
        "base_supported": False,
    }
