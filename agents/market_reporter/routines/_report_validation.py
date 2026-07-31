"""Small v3 validation layer for provenance, coverage, and exact identities."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from agents.market_reporter.routines._evidence import canonical_json
from agents.market_reporter.routines._identity import (
    APPROVED_QUOTES,
    REGISTRY_VERSION,
    SUPPORTED_DISCOVERY_CHAINS,
    TRADFI_SP500_STOCKS,
    registry_is_current,
)
from agents.market_reporter.routines._models import ReportPackage
from agents.market_reporter.routines._providers import (
    MANIFEST_VERSION,
    get_provider,
    validate_provider_url,
)


def validate_manifest(package: ReportPackage) -> None:
    """Verify the immutable snapshot and every declared provider."""
    manifest = package.evidence_manifest
    if manifest.get("provider_manifest_version") != MANIFEST_VERSION:
        raise ValueError("Provider manifest version mismatch")
    if manifest.get("identity_registry_version") != REGISTRY_VERSION:
        raise ValueError("Identity registry version mismatch")

    checksums = {}
    audit = {}
    for bundle in package.source_bundles:
        unsigned = dict(bundle)
        checksum = str(unsigned.pop("bundle_checksum", "") or "")
        actual = hashlib.sha256(canonical_json(unsigned).encode()).hexdigest()
        if not checksum or checksum != actual:
            raise ValueError("Source bundle checksum verification failed")
        if bundle.get("provider_manifest_version") != MANIFEST_VERSION:
            raise ValueError("Source bundle provider manifest mismatch")
        if bundle.get("identity_registry_version") != REGISTRY_VERSION:
            raise ValueError("Source bundle identity registry mismatch")
        for item in bundle.get("items") or []:
            get_provider(str(item.get("provider_id") or ""))
        for receipt in (bundle.get("coverage") or {}).get("providers") or []:
            get_provider(str(receipt.get("provider_id") or ""))

        source_type = str(bundle.get("source_type") or "")
        checksums[source_type] = checksum
        times = sorted(
            str(
                item.get("source_time")
                or item.get("published_at")
                or item.get("event_time_utc")
            )
            for item in bundle.get("items") or []
            if item.get("source_time")
            or item.get("published_at")
            or item.get("event_time_utc")
        )
        audit[source_type] = {
            "adapter_versions": bundle.get("adapter_versions"),
            "as_of_utc": bundle.get("as_of_utc"),
            "oldest_source_time": times[0] if times else None,
            "newest_source_time": times[-1] if times else None,
            "status": bundle.get("status"),
            "raw_item_count": bundle.get("raw_item_count"),
            "retained_item_count": bundle.get("retained_item_count"),
            "truncation_reasons": bundle.get("truncation_reasons"),
            "bundle_checksum": checksum,
        }
    if manifest.get("source_bundle_checksums") != checksums or any(
        not value for value in checksums.values()
    ):
        raise ValueError("Source bundle checksum manifest mismatch")
    if manifest.get("source_bundle_audit") != audit:
        raise ValueError("Source bundle audit manifest mismatch")

    required = {
        "crypto_market_intelligence": {"liquid_crypto_pairs"},
        "tradfi_market_intelligence": {"tradfi_identifiers"},
        "memecoin_market_intelligence": {
            "liquid_crypto_pairs",
            "established_memecoins",
            "approved_quotes",
        },
    }[package.metadata.strategy_key]
    if package.metadata.scope == "both":
        required |= {"liquid_crypto_pairs", "tradfi_identifiers"}
    stale = sorted(name for name in required if not registry_is_current(name))
    if stale:
        raise ValueError(f"Identity registries are stale: {stale}")


def validate_coverage(package: ReportPackage) -> None:
    """Make degraded data visible and prevent ranked output without support."""
    coverage = package.coverage_assessment
    incomplete = [
        str(bundle.get("source_type") or "")
        for bundle in package.source_bundles
        if bundle.get("status") != "complete"
    ]
    truncated = any(
        bundle.get("truncation_reasons") for bundle in package.source_bundles
    )
    if truncated and not coverage.truncated:
        raise ValueError("Coverage assessment hides source truncation")
    if incomplete and coverage.grade == "complete":
        raise ValueError("Complete coverage hides incomplete sources")
    if coverage.grade == "complete" and coverage.reason_codes:
        raise ValueError("Complete coverage cannot contain limitation reasons")
    if coverage.grade in {"limited", "unavailable"} and package.research_highlights:
        raise ValueError("Limited coverage cannot produce ranked research highlights")
    if coverage.grade == "unavailable" and any(
        (package.market_view, package.movers_view, package.drivers)
    ):
        raise ValueError("Unavailable market coverage cannot produce a market thesis")
    if (
        package.metadata.strategy_key == "tradfi_market_intelligence"
        and coverage.grade in {"complete", "sufficient"}
        and not package.analysis_context.events
    ):
        raise ValueError("Usable TradFi coverage requires a verified dated event")


def validate_chart_inputs(package: ReportPackage) -> None:
    """Reject non-finite numeric source values before chart serialization."""
    for bundle in package.source_bundles:
        _finite_tree(bundle.get("items") or [])
    _finite_tree(package.analysis_context.model_dump(mode="python"))


def validate_consistency(package: ReportPackage) -> None:
    """Validate the few facts the LLM is allowed to select."""
    evidence = {
        str(item.get("evidence_id") or ""): item
        for bundle in package.source_bundles
        for item in bundle.get("items") or []
        if item.get("evidence_id")
    }
    source_by_id = {
        str(item.get("evidence_id") or ""): str(bundle.get("source_type") or "")
        for bundle in package.source_bundles
        for item in bundle.get("items") or []
        if item.get("evidence_id")
    }
    _validate_events(package, evidence)
    _validate_news_links(package, evidence)

    for highlight in package.research_highlights:
        item = evidence[highlight.asset_evidence_id]
        if package.metadata.strategy_key == "memecoin_market_intelligence":
            source_types = {
                source_by_id[value] for value in highlight.supporting_evidence_ids
            }
            if not {
                "token_discovery",
                "market",
            }.issubset(
                source_types
            ) or not source_types.intersection({"news", "social"}):
                raise ValueError(
                    "Memecoin highlight requires pair, meta, and independent attention evidence"
                )
            primary_meta = str(item.get("primary_meta") or "").casefold()
            matching_meta = any(
                source_by_id[value] == "market"
                and str((evidence[value]).get("primary_meta") or "").casefold()
                == primary_meta
                and evidence[value].get("metric")
                in {"memecoin_meta_category", "memecoin_meta_sample"}
                for value in highlight.supporting_evidence_ids
            )
            if not primary_meta or not matching_meta:
                raise ValueError("Memecoin highlight lacks its matching meta evidence")
            symbol = str((item.get("market") or {}).get("symbol") or "")
            relevant_attention = any(
                source_by_id[value] in {"news", "social"}
                and _mentions_token_or_meta(evidence[value], symbol, primary_meta)
                for value in highlight.supporting_evidence_ids
            )
            if not relevant_attention:
                raise ValueError(
                    "Memecoin highlight lacks token- or meta-specific attention support"
                )
            _validate_discovery_item(item)
            if (
                highlight.research_state in {"priority_research", "conditional_watch"}
                and item.get("eligibility") != "eligible"
            ):
                raise ValueError("Constructive Memecoin highlight failed eligibility")
            if item.get("chain_id") == "robinhood" and not (
                (item.get("robinhood_identity") or {}).get("stock_token_registry_fresh")
            ):
                raise ValueError("Robinhood highlight lacks fresh exclusions")
        elif package.metadata.strategy_key == "tradfi_market_intelligence":
            symbol = str(item.get("symbol") or "")
            if symbol not in TRADFI_SP500_STOCKS:
                raise ValueError(
                    "TradFi research highlight must select a tracked S&P 500 stock"
                )
            supporting_types = {
                source_by_id[value] for value in highlight.supporting_evidence_ids
            }
            if not supporting_types.intersection({"fundamentals", "news", "events"}):
                raise ValueError(
                    "TradFi stock highlight lacks non-social primary or news evidence"
                )
            for value in highlight.supporting_evidence_ids:
                if source_by_id[value] != "fundamentals":
                    continue
                if str(evidence[value].get("symbol") or "") != symbol:
                    raise ValueError(
                        "TradFi stock highlight cites incompatible fundamentals"
                    )
    if package.metadata.strategy_key == "memecoin_market_intelligence":
        _validate_memecoin_news_freshness(package, evidence)


def _mentions_token_or_meta(
    attention_item: dict[str, Any],
    symbol: str,
    primary_meta: str,
) -> bool:
    """Return whether retained news/social text names the token or its meta."""
    values = [
        attention_item.get("title"),
        attention_item.get("summary"),
        attention_item.get("excerpt"),
        attention_item.get("topics"),
        attention_item.get("entities"),
        attention_item.get("tags"),
    ]
    tokens = set(
        re.findall(
            r"[a-z0-9]+",
            " ".join(str(value) for value in values if value).casefold(),
        )
    )
    symbol_tokens = set(re.findall(r"[a-z0-9]+", symbol.casefold()))
    meta_tokens = set(re.findall(r"[a-z0-9]+", primary_meta.casefold()))
    return bool(
        (symbol_tokens and symbol_tokens.issubset(tokens))
        or (meta_tokens and meta_tokens.issubset(tokens))
    )


def _validate_events(
    package: ReportPackage,
    evidence: dict[str, dict[str, Any]],
) -> None:
    selected = {
        str(row.get("evidence_id") or ""): row
        for row in package.analysis_context.events
    }
    for impact in package.event_impacts:
        context_event = selected[impact.event_evidence_id]
        item = evidence[impact.event_evidence_id]
        provider_id = str(item.get("provider_id") or "")
        provider = get_provider(provider_id)
        if (
            item.get("verified_scheduled") is not True
            or provider.get("source_family") != "official"
        ):
            raise ValueError("Calendar impact is not a verified official event")
        if (
            str(item.get("event_time_utc") or "")
            != str(context_event.get("event_time_utc") or "")
            or str(item.get("title") or "").strip()
            != str(context_event.get("title") or "").strip()
        ):
            raise ValueError("Calendar impact does not match cached event facts")
        validate_provider_url(provider_id, str(item.get("url") or ""))


def _validate_news_links(
    package: ReportPackage,
    evidence: dict[str, dict[str, Any]],
) -> None:
    refs = set()
    for card in (package.market_view, package.movers_view, package.event_outlook):
        if card is not None:
            refs.update(card.supporting_evidence_ids)
            refs.update(card.contrary_evidence_ids)
    for driver in package.drivers:
        refs.update(driver.supporting_evidence_ids)
        refs.update(driver.contrary_evidence_ids)
    for highlight in package.research_highlights:
        refs.update(highlight.supporting_evidence_ids)
        refs.update(highlight.contrary_evidence_ids)
    for evidence_id in refs:
        item = evidence[evidence_id]
        if item.get("source_family") != "news":
            continue
        parsed = urlparse(str(item.get("url") or ""))
        path = parsed.path.casefold().strip("/")
        generic = (
            not path
            or path in {"news", "markets", "research"}
            or path.startswith("tags/")
            or path.endswith((".rss", ".xml"))
        )
        if parsed.scheme != "https" or not parsed.hostname or generic:
            raise ValueError("Decision-relevant news lacks an article permalink")


def _validate_memecoin_news_freshness(
    package: ReportPackage,
    evidence: dict[str, dict[str, Any]],
) -> None:
    as_of = _utc(package.metadata.as_of_utc)
    refs = {
        evidence_id
        for highlight in package.research_highlights
        for evidence_id in (
            highlight.supporting_evidence_ids + highlight.contrary_evidence_ids
        )
    }
    for evidence_id in refs:
        item = evidence[evidence_id]
        if item.get("source_family") != "news":
            continue
        published = _utc(item.get("published_at") or item.get("source_time"))
        if published and (as_of - published).total_seconds() > 96 * 3600:
            raise ValueError("Memecoin highlight uses stale news as current evidence")


def _validate_discovery_item(item: dict[str, Any]) -> None:
    """Keep the exact-pair Memecoin safety gates from v2."""
    chain = str(item.get("chain_id") or "")
    token = str(item.get("token_address") or "")
    pair = str(item.get("pair_address") or "")
    quote = str(item.get("quote_token_address") or "").lower()
    if chain not in SUPPORTED_DISCOVERY_CHAINS or not token or not pair:
        raise ValueError("Memecoin discovery identity is incomplete")
    provider_id = str(item.get("provider_id") or "")
    source_url = str(item.get("url") or "")
    if not source_url:
        raise ValueError("Memecoin discovery source URL is missing")
    validate_provider_url(provider_id, source_url)
    if pair.casefold() not in urlparse(source_url).path.casefold():
        raise ValueError("Memecoin discovery URL does not resolve the exact pair")
    approved = {value.lower() for value in APPROVED_QUOTES.get(chain, set())}
    if quote not in approved:
        raise ValueError("Memecoin discovery quote is not approved")
    market = item.get("market") or {}
    required = {
        "pair_age_hours",
        "price_usd",
        "liquidity_usd",
        "volume_24h_usd",
        "buys_24h",
        "sells_24h",
    }
    if any(market.get(field) is None for field in required):
        raise ValueError("Memecoin discovery market evidence is incomplete")
    if not item.get("discovery_origins"):
        raise ValueError("Memecoin discovery origin is missing")
    if item.get("eligibility") == "eligible" and item.get("reason_codes"):
        raise ValueError("Eligible Memecoin evidence contains exclusion reasons")
    if chain == "robinhood":
        identity = item.get("robinhood_identity") or {}
        if not identity.get("contract_code_present"):
            raise ValueError("Robinhood contract identity is unconfirmed")
        if identity.get("stock_token_symbol"):
            raise ValueError("Robinhood Stock Token entered Memecoin evidence")


def _finite_tree(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Chart data contains a non-finite number")
    if isinstance(value, dict):
        numeric_keys = {
            "last_price",
            "return_1d_pct",
            "return_7d_pct",
            "return_30d_pct",
            "price_usd",
            "liquidity_usd",
            "volume_24h_usd",
            "market_cap_usd",
            "pair_age_hours",
        }
        for key, item in value.items():
            if (
                key in numeric_keys
                and item is not None
                and (isinstance(item, bool) or not isinstance(item, (int, float)))
            ):
                raise ValueError("Chart data requires a finite number")
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)


def _utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
