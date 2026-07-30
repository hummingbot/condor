"""Validate one typed analytical package and save one Condor report."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from agents.market_reporter.routines._models import (
    ReportPackage,
    validate_finite_numbers,
    validate_safe_urls,
)
from agents.market_reporter.routines._report_validation import (
    validate_chart_inputs,
    validate_coverage,
    validate_manifest,
)
from agents.market_reporter.routines._report_visuals import render_report
from routines.base import RoutineResult

CATEGORY = "Market Reporter"


class Config(BaseModel):
    """Validate and render one completed Market Reporter package."""

    model_config = ConfigDict(extra="forbid")

    report_package: dict[str, Any]


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    try:
        validate_safe_urls(config.report_package)
        validate_finite_numbers(config.report_package)
        package = ReportPackage.model_validate(config.report_package)
        validate_manifest(package)
        validate_coverage(package)
        validate_chart_inputs(package)
    except (ValidationError, ValueError) as exc:
        return _result("rejected_before_save", str(exc))

    try:
        async with asyncio.timeout(20):
            report_id = await render_report(package)
    except Exception as exc:
        return _result("save_failed", type(exc).__name__)

    payload = {
        "status": "saved",
        "report_id": report_id,
        "report_error": None,
        "coverage_grade": package.coverage_assessment.grade,
        "truncated": package.coverage_assessment.truncated,
        "mutation": "report_artifact_only",
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


def _result(status: str, error: str) -> RoutineResult:
    return RoutineResult(
        text=json.dumps(
            {
                "status": status,
                "report_id": None,
                "report_error": error[:500],
                "mutation": False,
            },
            separators=(",", ":"),
        )
    )
