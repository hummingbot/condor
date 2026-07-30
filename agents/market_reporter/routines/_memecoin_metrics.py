"""Identity-safe DEX pair normalization and hard eligibility gates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.market_reporter.routines._evidence import safe_float
from agents.market_reporter.routines._identity import (
    APPROVED_QUOTES,
    normalize_address,
    normalize_chain,
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
