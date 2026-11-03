from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from condor.telemetry.taps import web_tap
from condor.web.routes import (
    admin,
    agents,
    archived,
    auth,
    backtesting,
    bots,
    chat_ws,
    code,
    confirmations,
    controller_performance,
    conversations,
    dex,
    executors,
    market,
    meta,
    notifications,
    portfolio,
    positions,
    push,
    reports,
    routines,
    servers,
    sessions,
    settings,
    sharing,
    transcribe,
    updates,
    ws,
)


def _build_cors_origins() -> list[str]:
    """Build CORS allowed origins from env, including WEB_URL for Tailscale/VPS deployments."""
    web_url = os.environ.get("WEB_URL", "").strip().rstrip("/")
    web_port = int(os.environ.get("WEB_PORT", "8088") or "8088")
    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        f"http://localhost:{web_port}",
    ]
    if web_url:
        parsed = urlparse(web_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in origins:
            origins.append(origin)
    return origins


# The SPA shell and every unhashed file beside it: revalidate on each load.
# Cheap — a 304 — and the only thing that guarantees a refresh is looking at the
# build that is actually installed.
_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


class _HashedAssets(StaticFiles):
    """``/assets``, whose filenames carry a content hash.

    A hash in the name makes the bytes immutable by construction, so they are
    cacheable forever: a new build writes new names, and the shell that names
    them is never cached (see ``_NO_CACHE``). Left to browser heuristics the two
    could drift apart — a fresh shell asking for chunks the cache answers from a
    build ago — which is a version skew nobody can see.
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="Condor Dashboard API", version="0.1.0")

    # CORS – allow Vite dev server, local origins, and WEB_URL origin (e.g. Tailscale hostname)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_build_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Usage telemetry (FEAT-023). Reports the matched route *template*
    # (`/api/v1/bots/{name}`), never the URL, so cardinality is bounded by our
    # own router and no path parameter — a bot name, a server name, a report id
    # — is ever read. No-op unless the admin opted in.
    app.middleware("http")(web_tap)

    # ── API routes ──
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(servers.router, prefix="/api/v1")
    app.include_router(portfolio.router, prefix="/api/v1")
    app.include_router(bots.router, prefix="/api/v1")
    app.include_router(controller_performance.router, prefix="/api/v1")
    app.include_router(archived.router, prefix="/api/v1")
    app.include_router(executors.router, prefix="/api/v1")
    app.include_router(positions.router, prefix="/api/v1")
    app.include_router(backtesting.router, prefix="/api/v1")
    app.include_router(market.router, prefix="/api/v1")
    app.include_router(dex.router, prefix="/api/v1")
    app.include_router(meta.router, prefix="/api/v1")
    app.include_router(notifications.router, prefix="/api/v1")
    # Importing this module registered a Web Push sink on the notification
    # bus (FEAT-083), the same way ``chat_ws`` registers the socket one.
    app.include_router(push.router, prefix="/api/v1")
    app.include_router(ws.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(routines.router, prefix="/api/v1")
    app.include_router(code.router, prefix="/api/v1")
    app.include_router(reports.router, prefix="/api/v1")
    app.include_router(settings.router, prefix="/api/v1")
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(chat_ws.router, prefix="/api/v1")
    app.include_router(confirmations.router, prefix="/api/v1")
    app.include_router(conversations.router, prefix="/api/v1")
    app.include_router(sharing.router, prefix="/api/v1")
    app.include_router(transcribe.router, prefix="/api/v1")
    app.include_router(updates.router, prefix="/api/v1")

    # Report bodies are NOT mounted here. They used to be served by an
    # unauthenticated ``/reports/{filename:path}`` route, which made every
    # report — portfolio value, PnL, positions — readable by anyone who could
    # guess a filename. They now go through the authenticated
    # ``GET /api/v1/reports/{report_id}/html`` handler instead (SEC-112).
    # The one report-adjacent thing served without auth is the vendored plotly
    # bundle, at ``GET /api/v1/reports/assets/{filename}`` — library bytes, not
    # content, behind a fixed allowlist (PERF-267). It sits under ``/api/`` so
    # the arm below already turns a miss into a real 404 instead of index.html.

    # ── Serve built frontend (production) ──
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.is_dir():
        index_html = dist / "index.html"
        dist_root = dist.resolve()
        app.mount(
            "/assets",
            _HashedAssets(directory=str(dist / "assets")),
            name="static-assets",
        )

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            """SPA fallback: serve index.html for all non-API routes.

            An unknown ``/api/`` path is a real 404, never index.html: a frontend
            running ahead of the backend would otherwise get 200 HTML where it
            expects JSON, and surface the version skew as an opaque parse error
            instead of a missing route.

            ``/reports/`` is a real 404 too. It once served report bodies with no
            auth (SEC-112); a stale link to it must fail loudly rather than fall
            through to the SPA shell and look like it still works.
            """
            if full_path.startswith("api/"):
                raise HTTPException(
                    status_code=404, detail=f"No such API route: /{full_path}"
                )
            if full_path.startswith("reports/"):
                raise HTTPException(status_code=404, detail="Report not found")
            if full_path:
                try:
                    candidate = (dist / full_path).resolve()
                except (OSError, ValueError):
                    candidate = None
                # SEC-044: confine to dist — encoded traversal (%2e%2e, ..%2f)
                # reaches here decoded, and FileResponse applies no guard.
                if (
                    candidate is not None
                    and candidate.is_relative_to(dist_root)
                    and candidate.is_file()
                ):
                    return FileResponse(candidate, headers=_NO_CACHE)
            # Never from cache without asking. The shell names the hashed bundle
            # the whole app is, so a browser that reuses a stale one reuses a
            # stale *build*: a reader who refreshed the chat got a UI from
            # before the routine library existed — its "Browse all" gone and
            # `/routines` back in the nav — with no way to tell it was old.
            return FileResponse(index_html, headers=_NO_CACHE)

    return app
