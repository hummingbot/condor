"""Validate one compact v3 digest and save one Condor market report."""

from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents.market_reporter.routines._evidence import (
    clear_source_bundles,
    resolve_evidence_snapshot,
)
from agents.market_reporter.routines._identity import REGISTRY_VERSION
from agents.market_reporter.routines._models import (
    AnalyticalDigest,
    ReportPackage,
    validate_finite_numbers,
    validate_safe_urls,
)
from agents.market_reporter.routines._providers import MANIFEST_VERSION
from agents.market_reporter.routines._report_validation import (
    validate_chart_inputs,
    validate_consistency,
    validate_coverage,
    validate_manifest,
)
from agents.market_reporter.routines._report_visuals import render_report
from routines.base import RoutineResult

CATEGORY = "Market Reporter"

_REPORT_DEFAULTS = {
    "crypto_market_intelligence": (
        "Crypto Market Intelligence",
        "1-7 days",
        "2-6 weeks",
        "BTC",
    ),
    "tradfi_market_intelligence": (
        "TradFi Market Intelligence",
        "1-5 completed sessions",
        "2-6 weeks",
        "SPY",
    ),
    "memecoin_market_intelligence": (
        "Memecoin Meta and Chain Radar",
        "1-24 hours / 1-3 days",
        "1-2 weeks for established tokens only",
        None,
    ),
}
_RESERVED_DIGEST_FIELDS = {
    "schema_version",
    "metadata",
    "session_research_context",
    "evidence_manifest",
    "coverage_assessment",
    "source_bundles",
    "research_posture",
    "analysis_context",
}
_CONFIDENCE = {"low": 0, "moderate": 1, "high": 2}


class Config(BaseModel):
    """Validate and render one current-run Market Reporter digest."""

    model_config = ConfigDict(extra="forbid")

    report_package: AnalyticalDigest
    run_id: str = Field(min_length=1, max_length=160)
    evidence_snapshot_id: str = Field(pattern=r"^es_[0-9a-f]{40}$")


Config.model_rebuild(_types_namespace={"AnalyticalDigest": AnalyticalDigest})


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    started = time.perf_counter()
    try:
        package_data = _prepare_package(
            config.report_package.model_dump(mode="python"),
            run_id=config.run_id,
            evidence_snapshot_id=config.evidence_snapshot_id,
        )
        validate_safe_urls(package_data)
        validate_finite_numbers(package_data)
        package = ReportPackage.model_validate(package_data)
        validate_manifest(package)
        validate_coverage(package)
        validate_chart_inputs(package)
        validate_consistency(package)
    except (ValidationError, ValueError) as exc:
        return _result("rejected_before_save", str(exc), started)

    validated_at = time.perf_counter()
    try:
        async with asyncio.timeout(20):
            report_id = await render_report(package)
    except Exception as exc:
        return _result("save_failed", type(exc).__name__, started)

    finished = time.perf_counter()
    clear_source_bundles(config.run_id)
    payload = {
        "status": "saved",
        "report_id": report_id,
        "report_error": None,
        "coverage_grade": package.coverage_assessment.grade,
        "truncated": package.coverage_assessment.truncated,
        "mutation": "report_artifact_only",
        "debug_trace": {
            "schema_version": "2.0",
            "validation_seconds": round(validated_at - started, 3),
            "render_save_seconds": round(finished - validated_at, 3),
            "total_seconds": round(finished - started, 3),
        },
    }
    return RoutineResult(
        text=json.dumps(payload, separators=(",", ":")),
        sections=[
            {"type": "kpi", "label": "Report", "value": report_id},
            {
                "type": "kpi",
                "label": "Coverage",
                "value": package.coverage_assessment.grade.title(),
            },
        ],
    )


def _prepare_package(
    value: dict[str, Any],
    *,
    run_id: str,
    evidence_snapshot_id: str,
) -> dict[str, Any]:
    """Restore facts from the exact snapshot; never ask the LLM to reproduce them."""
    reserved = sorted(_RESERVED_DIGEST_FIELDS.intersection(value))
    if reserved:
        raise ValueError(
            "Analytical digest contains reserved deterministic fields: "
            + ", ".join(reserved)
        )
    snapshot = resolve_evidence_snapshot(run_id, evidence_snapshot_id)
    seed = snapshot.get("report_seed") or {}
    bundles = snapshot.get("source_bundles")
    context = seed.get("analysis_context")
    if not isinstance(bundles, list) or not isinstance(context, dict):
        raise ValueError("Evidence snapshot lacks the cached v3 fact summary")

    strategy = str(seed.get("strategy_key") or "")
    defaults = _REPORT_DEFAULTS.get(strategy)
    if defaults is None:
        raise ValueError("Evidence snapshot Strategy is invalid")
    scope = str(seed.get("scope") or "")
    as_of = str(seed.get("as_of_utc") or "")
    timezone = str(seed.get("report_timezone") or "")
    if (
        context.get("strategy_key") != strategy
        or context.get("scope") != scope
        or context.get("as_of_utc") != as_of
        or context.get("display_timezone") != timezone
    ):
        raise ValueError("Cached fact summary does not match its snapshot")

    package = deepcopy(value)
    coverage = deepcopy(context.get("coverage_assessment") or {})
    _apply_downward_limits(package, coverage)
    source_limitations = list(context.get("data_limitations") or [])
    analyst_limitations = list(package.get("data_limitations") or [])
    package["data_limitations"] = list(
        dict.fromkeys(analyst_limitations + source_limitations)
    )[:3]
    package.update(
        {
            "schema_version": "2.0",
            "metadata": {
                "title": defaults[0],
                "as_of_utc": as_of,
                "report_timezone": timezone,
                "strategy_key": strategy,
                "scope": scope,
                "near_horizon": defaults[1],
                "medium_horizon": defaults[2],
                "disclaimer": "Research only; not investment advice.",
            },
            "session_research_context": {
                "selected_strategy_key": strategy,
                "coverage_mode": "both" if scope == "both" else "primary",
                "resolution_source": (
                    "context"
                    if seed.get("focus_assets") or seed.get("themes")
                    else "default"
                ),
                "focus_assets": list(seed.get("focus_assets") or []),
                "themes": list(seed.get("themes") or []),
                "chains": list(seed.get("chains") or []),
                "preferred_horizons": [defaults[1], defaults[2]],
                "benchmark": defaults[3],
                "report_timezone": timezone,
            },
            "source_bundles": bundles,
            "analysis_context": context,
            "coverage_assessment": coverage,
            "research_posture": context.get("research_posture"),
            "evidence_manifest": _manifest(bundles),
        }
    )
    return package


def _apply_downward_limits(
    package: dict[str, Any],
    coverage: dict[str, Any],
) -> None:
    cap = str(coverage.get("confidence_cap") or "low")
    if cap not in _CONFIDENCE:
        raise ValueError("Cached coverage confidence cap is invalid")
    for key in ("market_view", "movers_view", "event_outlook"):
        card = package.get(key)
        if (
            isinstance(card, dict)
            and _CONFIDENCE.get(card.get("confidence"), 99) > _CONFIDENCE[cap]
        ):
            card["confidence"] = cap
    for highlight in package.get("research_highlights") or []:
        if _CONFIDENCE.get(highlight.get("confidence"), 99) > _CONFIDENCE[cap]:
            highlight["confidence"] = cap
    if coverage.get("grade") in {"limited", "unavailable"}:
        package["research_highlights"] = []
    if coverage.get("grade") == "unavailable":
        package["market_view"] = None
        package["movers_view"] = None
        package["drivers"] = []


def _manifest(bundles: list[dict[str, Any]]) -> dict[str, Any]:
    checksums = {}
    audit = {}
    for bundle in bundles:
        source_type = str(bundle.get("source_type") or "")
        checksum = str(bundle.get("bundle_checksum") or "")
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
    return {
        "provider_manifest_version": MANIFEST_VERSION,
        "identity_registry_version": REGISTRY_VERSION,
        "source_bundle_checksums": checksums,
        "source_bundle_audit": audit,
    }


def _result(status: str, error: str, started: float) -> RoutineResult:
    return RoutineResult(
        text=json.dumps(
            {
                "status": status,
                "report_id": None,
                "report_error": error[:500],
                "mutation": False,
                "debug_trace": {
                    "schema_version": "2.0",
                    "total_seconds": round(time.perf_counter() - started, 3),
                },
            },
            separators=(",", ":"),
        )
    )
