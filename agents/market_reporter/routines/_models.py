"""Strict source, digest, and report-package contracts for Market Reporter."""

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
Stance = Literal[
    "bullish",
    "cautiously_bullish",
    "neutral",
    "cautiously_bearish",
    "bearish",
    "mixed",
]
Confidence = Literal["low", "moderate", "high"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BaseSourceConfig(StrictModel):
    strategy_key: StrategyKey
    scope: Scope
    run_id: str | None = Field(default=None, min_length=1, max_length=160)
    focus_assets: list[str] = Field(default_factory=list, max_length=12)
    themes: list[str] = Field(default_factory=list, max_length=8)
    report_timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @field_validator("focus_assets", "themes")
    @classmethod
    def bounded_strings(cls, values: list[str]) -> list[str]:
        return _bounded_strings(values)

    @field_validator("report_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        _timezone(value)
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
        if self.run_id is not None:
            expected = (
                rf"^market_reporter\.{re.escape(self.strategy_key)}_(?:e)?[1-9]\d*$"
            )
            if not re.fullmatch(expected, self.run_id):
                raise ValueError("Run ID does not match the active Strategy")
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
        _timezone(value)
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
        _timezone(value)
        return value

    @field_validator("as_of_utc")
    @classmethod
    def valid_as_of(cls, value: str) -> str:
        _parse_timestamp(value, require_timezone=True)
        return value


class CoverageAssessment(StrictModel):
    grade: Literal["complete", "sufficient", "limited", "unavailable"]
    confidence_cap: Confidence
    reason_codes: list[str] = Field(default_factory=list, max_length=16)
    missing_sources: list[str] = Field(default_factory=list, max_length=8)
    truncated: bool = False


class AnalysisContext(StrictModel):
    """Bounded deterministic facts shared by the analyst and renderer."""

    schema_version: Literal["2.0"] = "2.0"
    strategy_key: StrategyKey
    scope: Scope
    as_of_utc: str
    display_timezone: str
    research_posture: Literal["conservative", "extreme_risk_research"]
    coverage_assessment: CoverageAssessment
    coverage_summary: list[dict[str, Any]] = Field(max_length=7)
    snapshot_metrics: list[dict[str, Any]] = Field(max_length=6)
    market_snapshot: dict[str, Any]
    leaders_laggards: dict[str, Any]
    news_clusters: list[dict[str, Any]] = Field(max_length=5)
    social_attention: list[dict[str, Any]] = Field(max_length=5)
    events: list[dict[str, Any]] = Field(max_length=8)
    data_limitations: list[str] = Field(max_length=12)
    evidence_lookup: dict[str, dict[str, Any]]
    strategy_features: dict[str, Any]

    @field_validator("as_of_utc")
    @classmethod
    def valid_as_of(cls, value: str) -> str:
        _parse_timestamp(value, require_timezone=True)
        return value

    @field_validator("display_timezone")
    @classmethod
    def valid_display_timezone(cls, value: str) -> str:
        _timezone(value)
        return value


class AnalysisCard(StrictModel):
    title: str = Field(min_length=1, max_length=140)
    observation: str = Field(min_length=1, max_length=900)
    interpretation: str = Field(min_length=1, max_length=900)
    stance: Stance
    confidence: Confidence
    confidence_reason: str = Field(min_length=1, max_length=320)
    horizon: str = Field(min_length=1, max_length=80)
    what_to_watch: list[str] = Field(min_length=1, max_length=3)
    invalidation_conditions: list[str] = Field(min_length=1, max_length=3)
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=8)
    contrary_evidence_ids: list[str] = Field(default_factory=list, max_length=4)


class MarketDriver(StrictModel):
    short_label: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    direction: Literal["bullish", "bearish", "mixed", "unclear"]
    importance: int = Field(ge=1, le=5)
    explanation: str = Field(min_length=1, max_length=500)
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=8)
    contrary_evidence_ids: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("short_label")
    @classmethod
    def one_to_three_word_label(cls, value: str) -> str:
        label = " ".join(value.split())
        if not 1 <= len(label.split()) <= 3:
            raise ValueError("short_label must contain one to three words")
        return label


class EventImpact(StrictModel):
    event_evidence_id: str = Field(min_length=1, max_length=96)
    why_it_matters: str = Field(min_length=1, max_length=420)
    most_affected: list[str] = Field(min_length=1, max_length=5)
    priority: Literal["high", "medium", "watch"]
    watch_for: str = Field(min_length=1, max_length=320)

    @field_validator("most_affected", mode="before")
    @classmethod
    def normalize_most_affected(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return value


class ResearchHighlight(StrictModel):
    rank: int | None = Field(default=None, ge=1, le=3)
    asset_evidence_id: str = Field(min_length=1, max_length=96)
    research_state: Literal[
        "priority_research",
        "conditional_watch",
        "risk_watch",
        "avoid_for_now",
    ]
    why_now: str = Field(min_length=1, max_length=700)
    main_risk: str = Field(min_length=1, max_length=500)
    stance: Stance
    confidence: Confidence
    confidence_reason: str = Field(min_length=1, max_length=320)
    horizon: str = Field(min_length=1, max_length=80)
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=8)
    contrary_evidence_ids: list[str] = Field(default_factory=list, max_length=4)
    invalidation_conditions: list[str] = Field(min_length=1, max_length=3)


class AnalyticalDigest(StrictModel):
    """The small LLM-owned v3 analysis contract."""

    market_view: AnalysisCard | None = None
    movers_view: AnalysisCard | None = None
    event_outlook: AnalysisCard | None = None
    drivers: list[MarketDriver] = Field(default_factory=list, max_length=5)
    event_impacts: list[EventImpact] = Field(default_factory=list, max_length=5)
    research_highlights: list[ResearchHighlight] = Field(
        default_factory=list, max_length=3
    )
    data_limitations: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def assign_missing_research_ranks(self) -> "AnalyticalDigest":
        for rank, highlight in enumerate(self.research_highlights, start=1):
            if highlight.rank is None:
                highlight.rank = rank
        return self


class ReportPackage(AnalyticalDigest):
    schema_version: Literal["2.0"]
    metadata: ReportMetadata
    session_research_context: SessionResearchContext
    evidence_manifest: dict[str, Any]
    coverage_assessment: CoverageAssessment
    source_bundles: list[dict[str, Any]] = Field(min_length=1, max_length=7)
    research_posture: Literal["conservative", "extreme_risk_research"]
    analysis_context: AnalysisContext

    @model_validator(mode="after")
    def validate_package_contract(self) -> "ReportPackage":
        strategy = self.metadata.strategy_key
        if strategy != self.session_research_context.selected_strategy_key:
            raise ValueError("Metadata and session Strategy mismatch")
        if strategy != self.analysis_context.strategy_key:
            raise ValueError("Metadata and analysis-context Strategy mismatch")
        if self.metadata.scope != self.analysis_context.scope:
            raise ValueError("Metadata and analysis-context scope mismatch")
        if self.metadata.as_of_utc != self.analysis_context.as_of_utc:
            raise ValueError("Metadata and analysis-context as-of time mismatch")
        if (
            self.metadata.report_timezone
            != self.session_research_context.report_timezone
            or self.metadata.report_timezone != self.analysis_context.display_timezone
        ):
            raise ValueError("Report timezone mismatch")
        if self.coverage_assessment != self.analysis_context.coverage_assessment:
            raise ValueError("Coverage assessment is not deterministic")

        allowed_scopes = {
            "crypto_market_intelligence": {"crypto", "both"},
            "tradfi_market_intelligence": {"tradfi", "both"},
            "memecoin_market_intelligence": {"memecoin"},
        }
        if self.metadata.scope not in allowed_scopes[strategy]:
            raise ValueError("Report scope does not match Strategy")

        expected_posture = (
            "extreme_risk_research"
            if strategy == "memecoin_market_intelligence"
            else "conservative"
        )
        if self.research_posture != expected_posture:
            raise ValueError("Research posture does not match Strategy")
        if self.analysis_context.research_posture != expected_posture:
            raise ValueError("Analysis-context posture does not match Strategy")
        expected_mode = "both" if self.metadata.scope == "both" else "primary"
        if self.session_research_context.coverage_mode != expected_mode:
            raise ValueError("Coverage mode does not match report scope")

        evidence, evidence_items = self._evidence()
        required_source = (
            "token_discovery"
            if strategy == "memecoin_market_intelligence"
            else "market"
        )
        for label, card in (
            ("market view", self.market_view),
            ("movers view", self.movers_view),
        ):
            if card is not None:
                self._validate_card(card, label, evidence)
                self._require_cross_bundle(
                    card.supporting_evidence_ids, required_source, label, evidence
                )
        if self.event_outlook is not None:
            self._validate_card(self.event_outlook, "event outlook", evidence)
        if (
            self.coverage_assessment.grade not in {"limited", "unavailable"}
            and self.market_view is None
        ):
            raise ValueError("Usable coverage requires a market view")

        cap = {"low": 0, "moderate": 1, "high": 2}[
            self.coverage_assessment.confidence_cap
        ]
        cards = [
            card
            for card in (self.market_view, self.movers_view, self.event_outlook)
            if card is not None
        ]
        if any(
            {"low": 0, "moderate": 1, "high": 2}[card.confidence] > cap
            for card in cards
        ):
            raise ValueError("Analysis confidence exceeds coverage cap")

        selected_events = {
            str(event.get("evidence_id") or "")
            for event in self.analysis_context.events
            if event.get("verified_scheduled") is True
        }
        impact_ids = [impact.event_evidence_id for impact in self.event_impacts]
        if len(impact_ids) != len(set(impact_ids)):
            raise ValueError("Event impacts must reference unique events")
        if set(impact_ids) - selected_events:
            raise ValueError("Event impact references an unselected event")
        if self.event_outlook is not None:
            outlook_event_ids = {
                value
                for value in self.event_outlook.supporting_evidence_ids
                if value in selected_events
            }
            if not outlook_event_ids:
                raise ValueError("Event outlook lacks a selected verified event")

        if strategy == "memecoin_market_intelligence" and self.drivers:
            raise ValueError(
                "Memecoin reports use deterministic meta trends, not drivers"
            )
        for driver in self.drivers:
            self._validate_refs(
                driver.supporting_evidence_ids,
                f"driver {driver.title}",
                evidence,
            )
            self._validate_refs(
                driver.contrary_evidence_ids,
                f"driver {driver.title}",
                evidence,
            )
        ranks = [highlight.rank for highlight in self.research_highlights]
        if len(ranks) != len(set(ranks)):
            raise ValueError("Research-highlight ranks must be unique")
        for highlight in self.research_highlights:
            self._validate_refs(
                highlight.supporting_evidence_ids,
                f"research highlight {highlight.rank}",
                evidence,
            )
            self._validate_refs(
                highlight.contrary_evidence_ids,
                f"research highlight {highlight.rank}",
                evidence,
            )
            source_types = {
                evidence[value][0] for value in highlight.supporting_evidence_ids
            }
            if required_source not in source_types or len(source_types) < 2:
                raise ValueError("Research highlight requires cross-bundle evidence")
            if {"low": 0, "moderate": 1, "high": 2}[highlight.confidence] > cap:
                raise ValueError("Research-highlight confidence exceeds coverage cap")
            if highlight.asset_evidence_id not in evidence_items:
                raise ValueError("Research-highlight asset evidence is unknown")
            if highlight.asset_evidence_id not in highlight.supporting_evidence_ids:
                raise ValueError("Asset evidence must support its research highlight")
            if strategy == "memecoin_market_intelligence":
                if evidence[highlight.asset_evidence_id][0] != "token_discovery":
                    raise ValueError(
                        "Memecoin highlight must select exact-pair evidence"
                    )
            elif evidence[highlight.asset_evidence_id][0] != "market":
                raise ValueError("Research highlight must select market evidence")
        return self

    def _evidence(self) -> tuple[dict[str, tuple[str, str]], dict[str, dict[str, Any]]]:
        evidence: dict[str, tuple[str, str]] = {}
        items: dict[str, dict[str, Any]] = {}
        source_types: set[str] = set()
        allowed = {
            "news",
            "social",
            "market",
            "fundamentals",
            "events",
            "token_discovery",
        }
        for bundle in self.source_bundles:
            source_type = str(bundle.get("source_type") or "")
            if source_type not in allowed or source_type in source_types:
                raise ValueError("Source bundle types must be valid and unique")
            source_types.add(source_type)
            if bundle.get("schema_version") != "1.0":
                raise ValueError("Source bundle schema mismatch")
            if bundle.get("status") not in {"complete", "partial", "unavailable"}:
                raise ValueError("Source bundle status is invalid")
            if bundle.get("mutation") is not False:
                raise ValueError("Source bundle must be read-only")
            if (
                bundle.get("strategy_key") != self.metadata.strategy_key
                or bundle.get("scope") != self.metadata.scope
            ):
                raise ValueError("Source bundle identity mismatch")
            _parse_timestamp(str(bundle.get("as_of_utc") or ""), require_timezone=True)
            bundle_items = bundle.get("items") or []
            if int(bundle.get("retained_item_count") or 0) != len(bundle_items):
                raise ValueError("Source bundle retained count is inconsistent")
            if int(bundle.get("raw_item_count") or 0) < len(bundle_items):
                raise ValueError("Source bundle raw count is inconsistent")
            for item in bundle_items:
                evidence_id = str(item.get("evidence_id") or "")
                if not evidence_id:
                    continue
                if evidence_id in evidence:
                    raise ValueError("Evidence IDs must be unique")
                evidence[evidence_id] = (
                    source_type,
                    str(item.get("source_family") or source_type),
                )
                items[evidence_id] = item
        if not evidence:
            raise ValueError("Report contains no retained evidence")
        return evidence, items

    @staticmethod
    def _validate_refs(
        refs: list[str],
        label: str,
        evidence: dict[str, tuple[str, str]],
    ) -> None:
        missing = sorted(set(refs) - evidence.keys())
        if missing:
            raise ValueError(f"{label} references unknown evidence: {missing}")

    @classmethod
    def _validate_card(
        cls,
        card: AnalysisCard,
        label: str,
        evidence: dict[str, tuple[str, str]],
    ) -> None:
        cls._validate_refs(card.supporting_evidence_ids, label, evidence)
        cls._validate_refs(card.contrary_evidence_ids, label, evidence)

    @staticmethod
    def _require_cross_bundle(
        refs: list[str],
        required_source: str,
        label: str,
        evidence: dict[str, tuple[str, str]],
    ) -> None:
        source_types = {evidence[value][0] for value in refs}
        if required_source not in source_types or len(source_types) < 2:
            raise ValueError(f"{label} requires cross-bundle evidence")


def validate_safe_urls(value: Any) -> None:
    """Recursively reject unsafe or credential-bearing URLs."""
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
    """Reject NaN and infinities anywhere in the package."""
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


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown IANA timezone") from exc


def _parse_timestamp(value: str, *, require_timezone: bool = False) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid ISO timestamp") from exc
    if require_timezone and parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed
