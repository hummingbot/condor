from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import condor.reports as report_storage
from condor.web.routes import (
    agents,
    archived,
    auth,
    backtesting,
    bots,
    chat_ws,
    controller_performance,
    executors,
    market,
    portfolio,
    positions,
    reports,
    routines,
    servers,
    settings,
    transcribe,
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

    # ── API routes ──
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(servers.router, prefix="/api/v1")
    app.include_router(portfolio.router, prefix="/api/v1")
    app.include_router(bots.router, prefix="/api/v1")
    app.include_router(controller_performance.router, prefix="/api/v1")
    app.include_router(archived.router, prefix="/api/v1")
    app.include_router(executors.router, prefix="/api/v1")
    app.include_router(positions.router, prefix="/api/v1")
    app.include_router(backtesting.router, prefix="/api/v1")
    app.include_router(market.router, prefix="/api/v1")
    app.include_router(ws.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(routines.router, prefix="/api/v1")
    app.include_router(reports.router, prefix="/api/v1")
    app.include_router(settings.router, prefix="/api/v1")
    app.include_router(chat_ws.router, prefix="/api/v1")
    app.include_router(transcribe.router, prefix="/api/v1")

    # ── Serve report HTML files ──
    reports_dir = Path(report_storage.CHARTS_DIR)
    reports_dir.mkdir(exist_ok=True)
    reports_root = reports_dir.resolve()

    @app.get("/reports/{filename:path}", include_in_schema=False)
    async def serve_report(filename: str):
        path = (reports_root / filename).resolve()
        try:
            path.relative_to(reports_root)
        except ValueError as exc:
            raise HTTPException(404, "Report not found") from exc
        if path.suffix.lower() != ".html" or not path.is_file():
            raise HTTPException(404, "Report not found")
        return FileResponse(
            path,
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    # ── Serve built frontend (production) ──
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.is_dir():
        index_html = dist / "index.html"
        dist_root = dist.resolve()
        app.mount(
            "/assets", StaticFiles(directory=str(dist / "assets")), name="static-assets"
        )

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            """SPA fallback: serve index.html for all non-API routes.

            An unknown ``/api/`` path is a real 404, never index.html: a frontend
            running ahead of the backend would otherwise get 200 HTML where it
            expects JSON, and surface the version skew as an opaque parse error
            instead of a missing route.
            """
            if full_path.startswith("api/"):
                raise HTTPException(
                    status_code=404, detail=f"No such API route: /{full_path}"
                )
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
                    return FileResponse(candidate)
            return FileResponse(index_html)

    return app
