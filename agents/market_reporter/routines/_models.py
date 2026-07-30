"""Strict shared configs and typed report-package validation."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StrategyKey = Literal[
    "crypto_market_intelligence",
    "tradfi_market_intelligence",
    "memecoin_market_intelligence",
]
Scope = Literal["crypto", "tradfi", "both", "memecoin"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BaseSourceConfig(StrictModel):
    strategy_key: StrategyKey
    scope: Scope
    focus_assets: list[str] = Field(default_factory=list, max_length=12)
    themes: list[str] = Field(default_factory=list, max_length=8)
    report_timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @field_validator("focus_assets", "themes")
    @classmethod
    def bounded_strings(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            item = re.sub(r"\s+", " ", str(value)).strip()
            if not item or len(item) > 120:
                raise ValueError("Context values must contain 1-120 characters")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned

    @field_validator("report_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def strategy_scope_match(self) -> "BaseSourceConfig":
        allowed = {
            "crypto_market_intelligence": {"crypto", "both"},
            "tradfi_market_intelligence": {"tradfi", "both"},
            "memecoin_market_intelligence": {"memecoin"},
        }
        if self.scope not in allowed[self.strategy_key]:
            raise ValueError("Scope does not match the active Strategy")
        return self


class SessionResearchContext(StrictModel):
    selected_strategy_key: StrategyKey
    coverage_mode: Literal["primary", "both"] = "primary"
    resolution_source: Literal["explicit", "context", "default"] = "explicit"
    focus_assets: list[str] = Field(default_factory=list, max_length=12)
    excluded_assets: list[str] = Field(default_factory=list, max_length=12)
    themes: list[str] = Field(default_factory=list, max_length=8)
    regions: list[str] = Field(default_factory=list, max_length=8)
    sectors: list[str] = Field(default_factory=list, max_length=12)
    chains: list[str] = Field(default_factory=list, max_length=3)
    preferred_horizons: list[str] = Field(default_factory=list, max_length=3)
    benchmark: str | None = Field(default=None, max_length=32)
    report_language: str = Field(default="en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    report_timezone: str = "UTC"
    user_supplied_exposure_context: list[str] = Field(
        default_factory=list, max_length=12
    )

    @field_validator("report_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @field_validator(
        "focus_assets",
        "excluded_assets",
        "themes",
        "regions",
        "sectors",
        "chains",
        "preferred_horizons",
        "user_supplied_exposure_context",
    )
    @classmethod
    def bounded_context_strings(cls, values: list[str]) -> list[str]:
        return _bounded_strings(values)


class ReportMetadata(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    as_of_utc: str
    report_timezone: str
    strategy_key: StrategyKey
    scope: Scope
    near_horizon: str = Field(min_length=1, max_length=80)
    medium_horizon: str | None = Field(default=None, max_length=80)
    disclaimer: str = Field(min_length=1, max_length=500)

    @field_validator("report_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @field_validator("as_of_utc")
    @classmethod
    def valid_as_of(cls, value: str) -> str:
        _parse_timestamp(value, require_timezone=True)
        return value


class CoverageAssessment(StrictModel):
    grade: Literal["complete", "sufficient", "limited", "unavailable"]
    confidence_cap: Literal["low", "moderate", "high"]
    reason_codes: list[str] = Field(default_factory=list, max_length=24)
    missing_sources: list[str] = Field(default_factory=list, max_length=16)
    truncated: bool = False


class AnalysisItem(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    observation: str = Field(min_length=1, max_length=1200)
    interpretation: str = Field(min_length=1, max_length=1200)
    stance: Literal[
        "bullish",
        "cautiously_bullish",
        "neutral",
        "cautiously_bearish",
        "bearish",
        "mixed",
    ]
    confidence: Literal["low", "moderate", "high"]
    horizon: str = Field(min_length=1, max_length=80)
    supporting_evidence_ids: list[str] = Field(min_length=2, max_length=12)
    contrary_evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=6)


class ResearchCandidate(StrictModel):
    rank: int = Field(ge=1, le=100)
    asset_identity: dict[str, Any]
    candidate_state: Literal[
        "priority_research",
        "conditional_watch",
        "risk_watch",
        "avoid_for_now",
    ]
    stance: Literal[
        "bullish",
        "cautiously_bullish",
        "neutral",
        "cautiously_bearish",
        "bearish",
        "mixed",
    ]
    confidence: Literal["low", "moderate", "high"]
    horizon: str = Field(min_length=1, max_length=80)
    why_now: str = Field(min_length=1, max_length=1200)
    supporting_evidence_ids: list[str] = Field(min_length=2, max_length=12)
    contrary_evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    catalysts: list[str] = Field(default_factory=list, max_length=6)
    invalidation_conditions: list[str] = Field(min_length=1, max_length=6)
    key_risks: list[str] = Field(min_length=1, max_length=8)
    dimension_assessments: dict[str, Any]
    coverage_grade: Literal["complete", "sufficient", "limited", "unavailable"]


class ReportPackage(StrictModel):
    schema_version: Literal["1.0"]
    metadata: ReportMetadata
    session_research_context: SessionResearchContext
    evidence_manifest: dict[str, Any]
    coverage_assessment: CoverageAssessment
    source_bundles: list[dict[str, Any]] = Field(min_length=1, max_length=7)
    executive_takeaways: list[str] = Field(min_length=1, max_length=5)
    market_views: list[AnalysisItem] = Field(default_factory=list, max_length=6)
    market_structure: dict[str, Any]
    sentiment_assessment: dict[str, Any]
    themes: list[dict[str, Any]] = Field(default_factory=list, max_length=6)
    research_candidates: list[ResearchCandidate] = Field(
        default_factory=list, max_length=15
    )
    opportunities: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    risks: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    scenarios: list[dict[str, Any]] = Field(default_factory=list, max_length=3)
    events_and_watch_conditions: list[dict[str, Any]] = Field(
        default_factory=list, max_length=16
    )
    data_limitations: list[str] = Field(default_factory=list, max_length=16)
    strategy_payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_package_contract(self) -> "ReportPackage":
        if (
            self.metadata.strategy_key
            != self.session_research_context.selected_strategy_key
        ):
            raise ValueError("Metadata and session Strategy mismatch")
        if (
            self.metadata.report_timezone
            != self.session_research_context.report_timezone
        ):
            raise ValueError("Metadata and session timezone mismatch")
        allowed_scopes = {
            "crypto_market_intelligence": {"crypto", "both"},
            "tradfi_market_intelligence": {"tradfi", "both"},
            "memecoin_market_intelligence": {"memecoin"},
        }
        if self.metadata.scope not in allowed_scopes[self.metadata.strategy_key]:
            raise ValueError("Report scope does not match Strategy")
        if (self.metadata.scope == "both") != (
            self.session_research_context.coverage_mode == "both"
        ):
            raise ValueError("Scope and session coverage mode mismatch")

        evidence: dict[str, tuple[str, str]] = {}
        source_types: set[str] = set()
        allowed_source_types = {
            "news",
            "social",
            "market",
            "fundamentals",
            "events",
            "token_discovery",
        }
        for bundle in self.source_bundles:
            source_type = str(bundle.get("source_type") or "")
            if not source_type or source_type in source_types:
                raise ValueError("Source bundle types must be present and unique")
            if source_type not in allowed_source_types:
                raise ValueError("Unknown source bundle type")
            source_types.add(source_type)
            if bundle.get("schema_version") != "1.0":
                raise ValueError("Source bundle schema mismatch")
            if bundle.get("status") not in {"complete", "partial", "unavailable"}:
                raise ValueError("Source bundle status is invalid")
            if bundle.get("mutation") is not False:
                raise ValueError("Source bundle must be read-only")
            if bundle.get("strategy_key") != self.metadata.strategy_key:
                raise ValueError("Source bundle Strategy mismatch")
            if bundle.get("scope") != self.metadata.scope:
                raise ValueError("Source bundle scope mismatch")
            _parse_timestamp(str(bundle.get("as_of_utc") or ""), require_timezone=True)
            items = bundle.get("items") or []
            retained = int(bundle.get("retained_item_count") or 0)
            raw = int(bundle.get("raw_item_count") or 0)
            if retained != len(items) or raw < retained:
                raise ValueError("Source bundle item counts are inconsistent")
            if not isinstance(bundle.get("adapter_versions"), dict):
                raise ValueError("Source bundle adapter versions are missing")
            for item in items:
                item_id = str(item.get("evidence_id") or "")
                if item_id:
                    if item_id in evidence:
                        raise ValueError("Evidence IDs must be unique")
                    evidence[item_id] = (
                        source_type,
                        str(item.get("source_family") or source_type),
                    )
                for timestamp_key in (
                    "source_time",
                    "published_at",
                    "event_time_utc",
                    "retrieved_at",
                ):
                    timestamp = item.get(timestamp_key)
                    if timestamp:
                        _parse_timestamp(str(timestamp))
        if not evidence:
            raise ValueError("Report contains no retained evidence")

        def validate_refs(refs: list[str], label: str) -> None:
            missing = sorted(set(refs) - evidence.keys())
            if missing:
                raise ValueError(f"{label} references unknown evidence: {missing}")

        for view in self.market_views:
            validate_refs(view.supporting_evidence_ids, view.title)
            validate_refs(view.contrary_evidence_ids, view.title)
            bundle_families = {
                evidence[item][0] for item in view.supporting_evidence_ids
            }
            required_observation = (
                "token_discovery"
                if self.metadata.strategy_key == "memecoin_market_intelligence"
                else "market"
            )
            if len(bundle_families) < 2 or required_observation not in bundle_families:
                raise ValueError("Market views require two source families")

        for collection_name, rows in (
            ("theme", self.themes),
            ("opportunity", self.opportunities),
            ("risk", self.risks),
            ("scenario", self.scenarios),
            ("event or watch condition", self.events_and_watch_conditions),
        ):
            for index, row in enumerate(rows, start=1):
                refs = row.get("supporting_evidence_ids") or row.get("evidence_ids")
                if not isinstance(refs, list) or not refs:
                    raise ValueError(f"{collection_name} {index} requires evidence IDs")
                validate_refs(
                    [str(value) for value in refs],
                    f"{collection_name} {index}",
                )

        max_candidates = (
            15 if self.metadata.strategy_key == "memecoin_market_intelligence" else 8
        )
        if len(self.research_candidates) > max_candidates:
            raise ValueError("Too many research candidates for Strategy")
        ranks = [candidate.rank for candidate in self.research_candidates]
        if len(ranks) != len(set(ranks)):
            raise ValueError("Candidate ranks must be unique")
        for candidate in self.research_candidates:
            validate_refs(
                candidate.supporting_evidence_ids, f"candidate {candidate.rank}"
            )
            validate_refs(
                candidate.contrary_evidence_ids, f"candidate {candidate.rank}"
            )
            bundle_families = {
                evidence[item][0] for item in candidate.supporting_evidence_ids
            }
            required_observation = (
                "token_discovery"
                if self.metadata.strategy_key == "memecoin_market_intelligence"
                else "market"
            )
            if len(bundle_families) < 2 or required_observation not in bundle_families:
                raise ValueError("Candidates require two source families")
            if self.metadata.strategy_key == "memecoin_market_intelligence":
                identity = candidate.asset_identity
                required = {
                    "chain_id",
                    "token_address",
                    "pair_address",
                    "quote_token_address",
                    "cohort",
                }
                if not required.issubset(identity) or any(
                    not str(identity.get(key) or "") for key in required
                ):
                    raise ValueError("Memecoin candidate identity is incomplete")
                if (
                    identity.get("cohort") != "established"
                    and candidate.confidence == "high"
                ):
                    raise ValueError("Discovery candidates cannot have high confidence")
                if (
                    identity.get("cohort") != "established"
                    and "week" in candidate.horizon.lower()
                ):
                    raise ValueError(
                        "Discovery candidates cannot have an extended horizon"
                    )
            elif not str(
                candidate.asset_identity.get("symbol")
                or candidate.asset_identity.get("ticker")
                or ""
            ):
                raise ValueError("Liquid-market candidate symbol is missing")

        required_payloads = {
            "crypto_market_intelligence": {
                "benchmark_regime",
                "breadth",
                "liquidity",
                "derivatives_positioning",
                "narrative_rotation",
            },
            "tradfi_market_intelligence": {
                "macro_regime",
                "rates_credit_dollar",
                "equity_breadth",
                "sector_rotation",
                "cftc_positioning",
            },
            "memecoin_market_intelligence": {
                "broad_backdrop",
                "established_basket",
                "chain_cohorts",
                "discovery_funnel",
                "candidate_quality",
                "exclusions",
            },
        }
        missing_payloads = (
            required_payloads[self.metadata.strategy_key] - self.strategy_payload.keys()
        )
        if missing_payloads:
            raise ValueError(
                f"Strategy payload is missing blocks: {sorted(missing_payloads)}"
            )
        cap_order = {"low": 0, "moderate": 1, "high": 2}
        cap = cap_order[self.coverage_assessment.confidence_cap]
        if any(cap_order[view.confidence] > cap for view in self.market_views):
            raise ValueError("Market-view confidence exceeds the coverage cap")
        if any(
            cap_order[candidate.confidence] > cap
            for candidate in self.research_candidates
        ):
            raise ValueError("Candidate confidence exceeds the coverage cap")
        return self


def validate_safe_urls(value: Any) -> None:
    """Recursively reject unsafe URL schemes and credential-bearing URLs."""
    if isinstance(value, dict):
        for key, item in value.items():
            if "url" in str(key).lower() and isinstance(item, str):
                parsed = urlparse(item)
                if parsed.scheme not in {"http", "https", "mailto"}:
                    raise ValueError("Unsafe URL scheme")
                if parsed.username or parsed.password:
                    raise ValueError("Credential-bearing URL")
            validate_safe_urls(item)
    elif isinstance(value, list):
        for item in value:
            validate_safe_urls(item)


def validate_finite_numbers(value: Any) -> None:
    """Reject NaN and infinities anywhere inside the untyped digest sections."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Report package contains a non-finite number")
    if isinstance(value, dict):
        for item in value.values():
            validate_finite_numbers(item)
    elif isinstance(value, list):
        for item in value:
            validate_finite_numbers(item)


def _bounded_strings(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        item = re.sub(r"\s+", " ", str(value)).strip()
        if not item or len(item) > 120:
            raise ValueError("Context values must contain 1-120 characters")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def _parse_timestamp(value: str, *, require_timezone: bool = False) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid ISO timestamp") from exc
    if require_timezone and parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed
