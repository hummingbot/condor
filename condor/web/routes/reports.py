"""Reports API routes — list, view, and delete generated reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from condor.reports import delete_report, get_report, list_reports, list_reports_grouped
from condor.web.auth import get_current_user
from condor.web.models import ReportSummary, ReportsListResponse, WebUser

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("", response_model=ReportsListResponse)
async def get_reports(
    source_type: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: WebUser = Depends(get_current_user),
):
    entries, total = list_reports(source_type=source_type, tag=tag, search=search, limit=limit, offset=offset)
    return ReportsListResponse(
        reports=[ReportSummary(**e) for e in entries],
        total=total,
    )


@router.get("/latest-by-source")
async def get_reports_grouped(user: WebUser = Depends(get_current_user)):
    return list_reports_grouped()


@router.get("/{report_id}", response_model=ReportSummary)
async def get_report_detail(report_id: str, user: WebUser = Depends(get_current_user)):
    entry = get_report(report_id)
    if not entry:
        raise HTTPException(404, "Report not found")
    return ReportSummary(**entry)


@router.delete("/{report_id}")
async def delete_report_endpoint(report_id: str, user: WebUser = Depends(get_current_user)):
    if not delete_report(report_id):
        raise HTTPException(404, "Report not found")
    return {"deleted": True}
