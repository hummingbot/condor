"""Cross-bundle evidence, coverage, and identity validation."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from agents.market_reporter.routines._evidence import canonical_json
from agents.market_reporter.routines._identity import (
    APPROVED_QUOTES,
    REGISTRY_VERSION,
    SUPPORTED_DISCOVERY_CHAINS,
    registry_is_current,
)
from agents.market_reporter.routines._models import ReportPackage
from agents.market_reporter.routines._providers import MANIFEST_VERSION, get_provider


def validate_manifest(package: ReportPackage) -> None:
    manifest = package.evidence_manifest
    if manifest.get("provider_manifest_version") != MANIFEST_VERSION:
        raise ValueError("Provider manifest version mismatch")
    if manifest.get("identity_registry_version") != REGISTRY_VERSION:
        raise ValueError("Identity registry version mismatch")
    checksums = manifest.get("source_bundle_checksums") or {}
    audit = manifest.get("source_bundle_audit") or {}
    expected = {}
    expected_audit = {}
    for bundle in package.source_bundles:
        checksum = str(bundle.get("bundle_checksum") or "")
        unsigned = dict(bundle)
        unsigned.pop("bundle_checksum", None)
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
        source_type = str(bundle.get("source_type"))
        expected[source_type] = checksum
        source_times = sorted(
            {
                str(
                    item.get("source_time")
                    or item.get("published_at")
                    or item.get("event_time_utc")
                    or ""
                )
                for item in bundle.get("items") or []
                if (
                    item.get("source_time")
                    or item.get("published_at")
                    or item.get("event_time_utc")
                )
            }
        )
        expected_audit[source_type] = {
            "adapter_versions": bundle.get("adapter_versions"),
            "as_of_utc": bundle.get("as_of_utc"),
            "oldest_source_time": source_times[0] if source_times else None,
            "newest_source_time": source_times[-1] if source_times else None,
            "status": bundle.get("status"),
            "raw_item_count": bundle.get("raw_item_count"),
            "retained_item_count": bundle.get("retained_item_count"),
            "truncation_reasons": bundle.get("truncation_reasons"),
            "bundle_checksum": checksum,
        }
    if checksums != expected or any(not value for value in expected.values()):
        raise ValueError("Source bundle checksum manifest mismatch")
    if audit != expected_audit:
        raise ValueError("Source bundle audit manifest mismatch")

    required_registries = {
        "crypto_market_intelligence": {"liquid_crypto_pairs"},
        "tradfi_market_intelligence": {"tradfi_identifiers"},
        "memecoin_market_intelligence": {
            "liquid_crypto_pairs",
            "established_memecoins",
            "approved_quotes",
        },
    }[package.metadata.strategy_key]
    if package.metadata.scope == "both":
        required_registries |= {"liquid_crypto_pairs", "tradfi_identifiers"}
    stale = sorted(
        name for name in required_registries if not registry_is_current(name)
    )
    if stale:
        raise ValueError(f"Identity registries are stale: {stale}")


def validate_coverage(package: ReportPackage) -> None:
    strategy = package.metadata.strategy_key
    market_bundle = _bundle(package, "market")
    discovery_bundle = _bundle(package, "token_discovery")
    reason_codes = set(package.coverage_assessment.reason_codes)
    if any(bundle.get("truncation_reasons") for bundle in package.source_bundles):
        if not package.coverage_assessment.truncated:
            raise ValueError("Coverage assessment hides source truncation")
    incomplete = [
        str(bundle.get("source_type"))
        for bundle in package.source_bundles
        if bundle.get("status") != "complete"
    ]
    if incomplete and package.coverage_assessment.grade == "complete":
        raise ValueError("Complete coverage hides partial or unavailable sources")

    if strategy == "crypto_market_intelligence":
        _validate_crypto_gate(package, market_bundle)
        if package.metadata.scope == "both":
            _validate_tradfi_gate(package, market_bundle)
    elif strategy == "tradfi_market_intelligence":
        _validate_tradfi_gate(package, market_bundle)
        if package.metadata.scope == "both":
            _validate_crypto_gate(package, market_bundle)
    else:
        market_coverage = (market_bundle.get("coverage") or {}).get(
            "crypto_universe"
        ) or {}
        if not market_coverage.get("btc_eth_present") and _has_directional_output(
            package
        ):
            raise ValueError("Memecoin backdrop gate failed")
        _validate_memecoin_candidates(package, discovery_bundle)

    if package.coverage_assessment.grade == "complete" and reason_codes:
        raise ValueError("Complete coverage cannot contain limitation reason codes")


def validate_chart_inputs(package: ReportPackage) -> None:
    metric_keys = {
        "last_price",
        "return_1d_pct",
        "return_7d_pct",
        "return_30d_pct",
        "sma20",
        "sma50",
        "rsi14",
        "realized_volatility_20d_pct",
        "volume_zscore_20d",
    }
    token_keys = {
        "pair_age_hours",
        "liquidity_usd",
        "volume_24h_usd",
        "price_change_24h_pct",
        "buys_24h",
        "sells_24h",
        "fdv_usd",
        "market_cap_usd",
        "volume_to_liquidity",
        "liquidity_to_fdv",
        "buy_share_24h",
    }
    series_keys = {"open", "high", "low", "close", "volume"}
    for bundle in package.source_bundles:
        for item in bundle.get("items") or []:
            _validate_numeric_fields(item.get("metrics") or {}, metric_keys)
            _validate_numeric_fields(item.get("market") or {}, token_keys)
            for row in item.get("series") or []:
                _validate_numeric_fields(row, series_keys)
    for theme in package.themes:
        _validate_numeric_fields(theme, {"direction_score"})


def _validate_numeric_fields(value: dict[str, Any], keys: set[str]) -> None:
    for key in keys:
        candidate = value.get(key)
        if candidate is None:
            continue
        if (
            isinstance(candidate, bool)
            or not isinstance(candidate, (int, float))
            or not math.isfinite(float(candidate))
        ):
            raise ValueError(f"Chart field {key} must be a finite number")


def _has_directional_output(package: ReportPackage) -> bool:
    return bool(package.market_views or package.research_candidates)


def _validate_crypto_gate(
    package: ReportPackage, market_bundle: dict[str, Any]
) -> None:
    coverage = (market_bundle.get("coverage") or {}).get("crypto_universe") or {}
    passed = (
        bool(coverage.get("btc_eth_present"))
        and float(coverage.get("valid_pct") or 0) >= 70
    )
    if not passed and _has_directional_output(package):
        raise ValueError("Crypto directional gate failed")

    derivatives_complete = int(coverage.get("btc_eth_derivatives_count") or 0) >= 4
    derivative_ids = {
        str(item.get("evidence_id"))
        for item in market_bundle.get("items") or []
        if item.get("source_family") == "derivatives"
    }
    for view in package.market_views:
        if (
            view.confidence == "high"
            and derivative_ids.intersection(view.supporting_evidence_ids)
            and not derivatives_complete
        ):
            raise ValueError("High-confidence derivatives view lacks BTC/ETH coverage")


def _validate_tradfi_gate(
    package: ReportPackage, market_bundle: dict[str, Any]
) -> None:
    coverage = (market_bundle.get("coverage") or {}).get("tradfi_universe") or {}
    passed = (
        bool(coverage.get("spy_qqq_present"))
        and int(coverage.get("sector_valid_count") or 0) >= 8
        and bool(coverage.get("treasury_curve_present"))
        and int(coverage.get("cross_asset_component_count") or 0) >= 2
    )
    if not passed and _has_directional_output(package):
        raise ValueError("TradFi directional gate failed")


def _validate_memecoin_candidates(
    package: ReportPackage, discovery_bundle: dict[str, Any]
) -> None:
    discovered = {}
    for item in discovery_bundle.get("items") or []:
        key = (
            str(item.get("chain_id") or ""),
            str(item.get("token_address") or "").lower(),
            str(item.get("pair_address") or "").lower(),
        )
        discovered[key] = item
    for candidate in package.research_candidates:
        identity = candidate.asset_identity
        key = (
            str(identity.get("chain_id") or ""),
            str(identity.get("token_address") or "").lower(),
            str(identity.get("pair_address") or "").lower(),
        )
        item = discovered.get(key)
        if not item:
            raise ValueError("Memecoin candidate is absent from discovery evidence")
        quote = str(identity.get("quote_token_address") or "").lower()
        if quote != str(item.get("quote_token_address") or "").lower():
            raise ValueError("Memecoin candidate quote identity mismatch")
        if identity.get("cohort") != item.get("cohort"):
            raise ValueError("Memecoin candidate cohort mismatch")
        _validate_discovery_item(item)
        if (
            candidate.candidate_state
            in {
                "priority_research",
                "conditional_watch",
            }
            and item.get("eligibility") != "eligible"
        ):
            raise ValueError("Constructive Memecoin candidate failed hard eligibility")
        if key[0] == "robinhood" and not (
            (item.get("robinhood_identity") or {}).get("stock_token_registry_fresh")
        ):
            raise ValueError("Robinhood candidate lacks fresh Stock Token exclusions")
        chain_coverage = (
            (discovery_bundle.get("coverage") or {}).get("chain_counts") or {}
        ).get(key[0]) or {}
        if (
            candidate.confidence == "high"
            and int(chain_coverage.get("eligible") or 0) < 5
        ):
            raise ValueError("High confidence exceeds Memecoin chain coverage")


def _validate_discovery_item(item: dict[str, Any]) -> None:
    chain = str(item.get("chain_id") or "")
    token = str(item.get("token_address") or "")
    pair = str(item.get("pair_address") or "")
    quote = str(item.get("quote_token_address") or "").lower()
    if chain not in SUPPORTED_DISCOVERY_CHAINS or not token or not pair:
        raise ValueError("Memecoin discovery identity is incomplete")
    approved = {value.lower() for value in APPROVED_QUOTES.get(chain, set())}
    if quote not in approved:
        raise ValueError("Memecoin discovery quote is not approved")
    market = item.get("market") or {}
    required_market = {
        "pair_age_hours",
        "price_usd",
        "liquidity_usd",
        "volume_24h_usd",
        "buys_24h",
        "sells_24h",
    }
    if any(market.get(field) is None for field in required_market):
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


def _bundle(package: ReportPackage, source_type: str) -> dict[str, Any]:
    for bundle in package.source_bundles:
        if bundle.get("source_type") == source_type:
            return bundle
    return {}
