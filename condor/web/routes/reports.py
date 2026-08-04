"""Reports API routes — list, view, and delete generated reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from condor.reports import (
    delete_report,
    get_report,
    get_report_raw_html,
    list_reports,
    list_reports_grouped,
)
from condor.web.auth import get_current_user
from condor.web.models import ReportSummary, ReportsListResponse, WebUser

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("", response_model=ReportsListResponse)
async def get_reports(
    source_type: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    agent: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: WebUser = Depends(get_current_user),
):
    entries, total = list_reports(
        source_type=source_type,
        tag=tag,
        search=search,
        agent=agent,
        limit=limit,
        offset=offset,
    )
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


@router.get("/{report_id}/html", response_class=HTMLResponse)
async def get_report_html(report_id: str, user: WebUser = Depends(get_current_user)):
    """Return a report's rendered HTML body to an authenticated caller.

    Report bodies carry portfolio value, PnL, positions and strategy internals,
    so they are served from behind ``get_current_user`` like every other reports
    surface — there is no unauthenticated static mount for them (SEC-112). The
    client fetches this with an ``Authorization`` header and hands the result to
    a sandboxed iframe via ``srcDoc``, which is why the body is returned inline
    rather than as a file the browser navigates to.
    """
    result = get_report_raw_html(report_id)
    if result is None:
        raise HTTPException(404, "Report not found")
    html, _filename = result
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@router.delete("/{report_id}")
async def delete_report_endpoint(
    report_id: str, user: WebUser = Depends(get_current_user)
):
    if not await delete_report(report_id):
        raise HTTPException(404, "Report not found")
    return {"deleted": True}
