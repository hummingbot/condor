"""Reports API routes — list, view, and delete generated reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from condor.reports import (
    delete_report,
    get_report,
    get_report_raw_html,
    hydrate,
    list_reports,
    list_reports_grouped,
    resolve_report_asset,
)
from condor.web.auth import get_current_user
from condor.web.models import ReportsListResponse, ReportSummary, WebUser
from config_manager import get_config_manager

router = APIRouter(prefix="/reports", tags=["reports"])


def _owner_filter(user: WebUser) -> int | None:
    """Whose reports a listing may show: everyone's for admins, own otherwise.

    ``None`` disables the store's owner filter — the admin override the other
    server-data surfaces already grant (``_owner`` in conversations,
    ``_require_ownership`` in sessions). Anyone else is scoped to entries
    stamped with their own id; legacy entries with no owner are dropped for
    them (fail closed, SEC-196).
    """
    return None if get_config_manager().is_admin(user.id) else user.id


def _authorized_entry(report_id: str, user: WebUser) -> dict:
    """Fetch an index entry, 404 if absent and 403 if it belongs to someone else.

    Ownership is the ``user_id`` the producer stamped at save time. Admins see
    everything; an entry with no owner (written before SEC-196, or by a bare
    script outside every runner) is admin-only rather than world-readable —
    the same fail-closed rule ownerless routine instances follow.
    """
    entry = get_report(report_id)
    if not entry:
        raise HTTPException(404, "Report not found")
    if get_config_manager().is_admin(user.id):
        return entry
    owner = entry.get("user_id")
    if owner is not None and owner == user.id:
        return entry
    raise HTTPException(status_code=403, detail="Not your report")


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
        owner_id=_owner_filter(user),
    )
    return ReportsListResponse(
        reports=[ReportSummary(**e) for e in entries],
        total=total,
    )


@router.get("/latest-by-source")
async def get_reports_grouped(user: WebUser = Depends(get_current_user)):
    return list_reports_grouped(owner_id=_owner_filter(user))


@router.get("/assets/{filename}")
async def get_report_asset(filename: str):
    """Serve a persisted report asset — today, the pinned plotly.js bundle.

    Unauthenticated on purpose, and *not* a regression of SEC-112: SEC-112
    removed a public mount that served report **content** (portfolio value,
    PnL, positions). This serves vendored library bytes — byte-identical for
    every user, carrying nothing about anyone's account, the same class of
    asset ``/assets`` and the SPA catch-all already hand out. Reports reference
    it instead of inlining 4.85 MB apiece (PERF-267), and an iframe with no
    ``allow-same-origin`` cannot reach the dashboard JWT even given a plotly
    CVE. What would re-open SEC-112 is reading a caller-supplied path, so the
    name goes through ``resolve_report_asset``'s fixed allowlist.

    It lives under ``/api/v1`` because that is the only prefix Vite proxies in
    dev; it is also what makes a miss a real 404 rather than the SPA's
    ``index.html``, which browsers refuse to execute as a classic script and
    which would show up as a silently blank chart.
    """
    path = resolve_report_asset(filename)
    if path is None:
        raise HTTPException(404, "Asset not found")
    return FileResponse(
        path,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/{report_id}", response_model=ReportSummary)
async def get_report_detail(report_id: str, user: WebUser = Depends(get_current_user)):
    entry = _authorized_entry(report_id, user)
    return ReportSummary(**entry)


@router.get("/{report_id}/html", response_class=HTMLResponse)
async def get_report_html(
    report_id: str,
    hydrate_assets: bool = Query(False, alias="hydrate"),
    user: WebUser = Depends(get_current_user),
):
    """Return a report's rendered HTML body to its owner (or an admin).

    Report bodies carry portfolio value, PnL, positions and strategy internals,
    so they are served from behind ``get_current_user`` like every other reports
    surface — there is no unauthenticated static mount for them (SEC-112) — and,
    since SEC-196, only to the report's recorded owner or an admin. The
    client fetches this with an ``Authorization`` header and hands the result to
    a sandboxed iframe via ``srcDoc``, which is why the body is returned inline
    rather than as a file the browser navigates to.

    The body is served exactly as stored — charts reference the shared bundle
    under ``/api/v1/reports/assets/``, which the frame resolves against the
    parent origin and the browser caches once for every report. ``?hydrate=1``
    inlines that bundle instead, and is for the one caller whose output leaves
    the origin: the Download button, whose file is opened at ``file://``.
    """
    _authorized_entry(report_id, user)
    result = get_report_raw_html(report_id)
    if result is None:
        raise HTTPException(404, "Report not found")
    html, _filename = result
    if hydrate_assets:
        html = hydrate(html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@router.delete("/{report_id}")
async def delete_report_endpoint(
    report_id: str, user: WebUser = Depends(get_current_user)
):
    _authorized_entry(report_id, user)
    if not await delete_report(report_id):
        raise HTTPException(404, "Report not found")
    return {"deleted": True}
