"""Deterministic Memecoin seed, pair, evidence, and coverage selection."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from agents.market_reporter.routines._crypto_catalog import primary_meta_for_asset
from agents.market_reporter.routines._evidence import evidence_id, safe_float, utc_now
from agents.market_reporter.routines._http import FetchResult, fetch_json
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


def normalize_dex_pair(raw: dict[str, Any], *, origin: str) -> dict[str, Any] | None:
    """Normalize GeckoTerminal or DEX Screener pair fields."""
    if "attributes" in raw:
        attributes = raw.get("attributes") or {}
        relationships = raw.get("relationships") or {}
        network = normalize_chain(
            str(raw.get("network") or attributes.get("network") or "")
        )
        base_address = _relationship_address(relationships, "base_token")
        quote_address = _relationship_address(relationships, "quote_token")
        pair_address = str(attributes.get("address") or raw.get("id") or "")
        created_at = attributes.get("pool_created_at")
        liquidity = safe_float(attributes.get("reserve_in_usd"))
        volume = safe_float((attributes.get("volume_usd") or {}).get("h24"))
        price_change = safe_float(
            (attributes.get("price_change_percentage") or {}).get("h24")
        )
        transactions = attributes.get("transactions") or {}
        h24 = transactions.get("h24") or {}
        buys = safe_float(h24.get("buys"))
        sells = safe_float(h24.get("sells"))
        dex = str((relationships.get("dex") or {}).get("data", {}).get("id") or "")
        symbol = str(attributes.get("name") or "").split(" / ")[0]
        name = symbol
        fdv = safe_float(attributes.get("fdv_usd"))
        market_cap = safe_float(attributes.get("market_cap_usd"))
        price_usd = safe_float(attributes.get("base_token_price_usd"))
    else:
        network = normalize_chain(str(raw.get("chainId") or ""))
        base = raw.get("baseToken") or {}
        quote = raw.get("quoteToken") or {}
        base_address = str(base.get("address") or "")
        quote_address = str(quote.get("address") or "")
        pair_address = str(raw.get("pairAddress") or "")
        created_at = _epoch_to_iso(raw.get("pairCreatedAt"))
        liquidity = safe_float((raw.get("liquidity") or {}).get("usd"))
        volume = safe_float((raw.get("volume") or {}).get("h24"))
        price_change = safe_float((raw.get("priceChange") or {}).get("h24"))
        h24 = (raw.get("txns") or {}).get("h24") or {}
        buys = safe_float(h24.get("buys"))
        sells = safe_float(h24.get("sells"))
        dex = str(raw.get("dexId") or "")
        symbol = str(base.get("symbol") or "")
        name = str(base.get("name") or symbol)
        fdv = safe_float(raw.get("fdv"))
        market_cap = safe_float(raw.get("marketCap"))
        price_usd = safe_float(raw.get("priceUsd"))

    if not network or not base_address or not quote_address or not pair_address:
        return None
    base_address = normalize_address(network, base_address)
    quote_address = normalize_address(network, quote_address)
    return {
        "chain_id": network,
        "token_address": base_address,
        "pair_address": normalize_address(network, pair_address),
        "quote_token_address": quote_address,
        "symbol": symbol[:32],
        "name": name[:120],
        "dex": dex[:80],
        "pair_created_at": created_at,
        "liquidity_usd": liquidity,
        "volume_24h_usd": volume,
        "price_change_24h_pct": price_change,
        "buys_24h": buys,
        "sells_24h": sells,
        "fdv_usd": fdv,
        "market_cap_usd": market_cap,
        "price_usd": price_usd,
        "discovery_origin": origin,
    }


def eligibility(
    pair: dict[str, Any],
    *,
    min_pair_age_hours: float,
    min_liquidity_usd: float,
    robinhood_exclusions: set[str],
    robinhood_registry_fresh: bool,
    quote_registry_fresh: bool = True,
) -> tuple[str, list[str]]:
    reasons = []
    chain = normalize_chain(str(pair.get("chain_id") or ""))
    quote = normalize_address(chain, str(pair.get("quote_token_address") or ""))
    token = normalize_address(chain, str(pair.get("token_address") or ""))
    if chain not in APPROVED_QUOTES:
        reasons.append("unsupported_chain")
    if not quote_registry_fresh:
        reasons.append("stale_quote_token_registry")
    if chain == "robinhood":
        if not robinhood_registry_fresh:
            reasons.append("stale_robinhood_stock_token_registry")
        if token in robinhood_exclusions or quote in robinhood_exclusions:
            reasons.append("robinhood_stock_or_etf")
    approved = APPROVED_QUOTES.get(chain) or set()
    if approved and quote not in approved:
        reasons.append("unapproved_quote")
    age = pair_age_hours(pair.get("pair_created_at"))
    if age is None:
        reasons.append("missing_pair_age")
    elif age < min_pair_age_hours:
        reasons.append("pair_too_new")
    liquidity = safe_float(pair.get("liquidity_usd"))
    if liquidity is None:
        reasons.append("missing_liquidity")
    elif liquidity < min_liquidity_usd:
        reasons.append("insufficient_liquidity")
    for key in ("price_usd", "volume_24h_usd", "buys_24h", "sells_24h"):
        value = safe_float(pair.get(key))
        if value is None or value < 0:
            reasons.append(f"invalid_{key}")
    hard = {
        "unsupported_chain",
        "stale_quote_token_registry",
        "robinhood_stock_or_etf",
        "unapproved_quote",
        "missing_pair_age",
        "pair_too_new",
        "missing_liquidity",
        "insufficient_liquidity",
        "invalid_volume_24h_usd",
        "invalid_price_usd",
        "invalid_buys_24h",
        "invalid_sells_24h",
    }
    if any(reason in hard for reason in reasons):
        return "excluded", sorted(set(reasons))
    if reasons:
        return "watch_only", sorted(set(reasons))
    return "eligible", []


def add_quality_metrics(pair: dict[str, Any]) -> dict[str, Any]:
    liquidity = safe_float(pair.get("liquidity_usd"))
    volume = safe_float(pair.get("volume_24h_usd"))
    fdv = safe_float(pair.get("fdv_usd"))
    buys = safe_float(pair.get("buys_24h"))
    sells = safe_float(pair.get("sells_24h"))
    total_transactions = (
        buys + sells if buys is not None and sells is not None else None
    )
    return {
        **pair,
        "pair_age_hours": pair_age_hours(pair.get("pair_created_at")),
        "volume_to_liquidity": (
            round(volume / liquidity, 6)
            if volume is not None and liquidity and liquidity > 0
            else None
        ),
        "liquidity_to_fdv": (
            round(liquidity / fdv, 6)
            if liquidity is not None and fdv and fdv > 0
            else None
        ),
        "buy_share_24h": (
            round(buys / total_transactions, 6)
            if buys is not None and total_transactions and total_transactions > 0
            else None
        ),
    }


def pair_age_hours(value: Any) -> float | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - timestamp
    return max(0.0, round(delta.total_seconds() / 3600, 3))


def _relationship_address(relationships: dict[str, Any], key: str) -> str:
    data = (relationships.get(key) or {}).get("data") or {}
    identity = str(data.get("id") or "")
    return identity.rsplit("_", 1)[-1] if "_" in identity else identity


def _epoch_to_iso(value: Any) -> str | None:
    number = safe_float(value)
    if number is None:
        return None
    if number > 10_000_000_000:
        number /= 1000
    try:
        return (
            datetime.fromtimestamp(number, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError):
        return None


async def fetch_stock_token_exclusions() -> (
    tuple[set[str], dict[str, str], bool, list[FetchResult]]
):
    result = await fetch_json(
        "robinhood_registry",
        "https://api.robinhood.com/rhj/assets",
        retry=False,
    )
    exclusions: set[str] = set()
    symbols: dict[str, str] = {}
    if result.status == "complete":
        for asset in (result.data or {}).get("assets") or []:
            symbol = str(asset.get("tokenSymbol") or "")
            for deployment in asset.get("deployments") or []:
                if int(deployment.get("chainId") or 0) != 4663:
                    continue
                address = normalize_address(
                    "robinhood", str(deployment.get("contractAddress") or "")
                )
                if address:
                    exclusions.add(address)
                    symbols[address] = symbol
    return exclusions, symbols, result.status == "complete", [result]


async def confirm_contracts(
    addresses: list[str],
) -> tuple[dict[str, dict[str, Any]], list[FetchResult]]:
    unique = list(dict.fromkeys(addresses))[:10]
    rpc_tasks = [
        fetch_json(
            "robinhood_rpc",
            "https://rpc.mainnet.chain.robinhood.com",
            method="POST",
            json_body={
                "jsonrpc": "2.0",
                "id": index + 1,
                "method": "eth_getCode",
                "params": [address, "latest"],
            },
            retry=False,
        )
        for index, address in enumerate(unique)
    ]
    explorer_tasks = [
        fetch_json(
            "robinhood_blockscout",
            f"https://robinhoodchain.blockscout.com/api/v2/addresses/{address}",
            retry=False,
        )
        for address in unique
    ]
    results: list[FetchResult] = list(
        await asyncio.gather(*(rpc_tasks + explorer_tasks))
    )
    output = {}
    for index, address in enumerate(unique):
        rpc = results[index]
        explorer = results[len(unique) + index]
        code = (rpc.data or {}).get("result") if rpc.status == "complete" else None
        output[address] = {
            "chain_id_numeric": 4663,
            "contract_code_present": bool(code and code != "0x"),
            "rpc_status": rpc.status,
            "explorer_status": explorer.status,
            "explorer_url": (
                f"https://robinhoodchain.blockscout.com/address/{address}"
            ),
        }
    return output, results


async def observe_concentration(
    addresses: list[str],
) -> tuple[dict[str, dict[str, Any]], list[FetchResult]]:
    unique = list(dict.fromkeys(addresses))[:10]
    batch = []
    for index, address in enumerate(unique):
        batch.extend(
            [
                {
                    "jsonrpc": "2.0",
                    "id": index + 1,
                    "method": "getTokenLargestAccounts",
                    "params": [address, {"commitment": "confirmed"}],
                },
                {
                    "jsonrpc": "2.0",
                    "id": len(unique) + index + 1,
                    "method": "getTokenSupply",
                    "params": [address, {"commitment": "confirmed"}],
                },
            ]
        )
    result = await fetch_json(
        "solana_rpc",
        "https://api.mainnet-beta.solana.com",
        method="POST",
        json_body=batch,
        retry=False,
    )
    responses = {}
    if result.status == "complete" and isinstance(result.data, list):
        responses = {
            int(row["id"]): row
            for row in result.data
            if isinstance(row, dict) and row.get("id") is not None
        }
    observations = {}
    for index, address in enumerate(unique):
        largest_response = responses.get(index + 1) or {}
        supply_response = responses.get(len(unique) + index + 1) or {}
        largest_rows = (largest_response.get("result") or {}).get("value") or []
        total_supply = safe_float(
            ((supply_response.get("result") or {}).get("value") or {}).get("amount")
        )
        top_amounts = [
            value
            for value in (safe_float(row.get("amount")) for row in largest_rows)
            if value is not None and value >= 0
        ]
        observations[address] = {
            "largest_accounts_status": (
                "complete"
                if largest_response.get("result") is not None
                else "unavailable"
            ),
            "token_supply_status": (
                "complete"
                if supply_response.get("result") is not None
                else "unavailable"
            ),
            "top_account_count_observed": len(top_amounts),
            "top_10_share_of_supply": (
                round(sum(top_amounts[:10]) / total_supply, 6)
                if total_supply and total_supply > 0 and top_amounts
                else None
            ),
            "interpretation_limit": (
                "Token-account concentration is observable; beneficial ownership "
                "and exchange custody are not identified."
            ),
        }
    return observations, [result]


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
