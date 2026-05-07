from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from condor.web.routes import agents, archived, auth, backtesting, bots, executors, market, portfolio, positions, reports, routines, servers, ws


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
    app.include_router(archived.router, prefix="/api/v1")
    app.include_router(executors.router, prefix="/api/v1")
    app.include_router(positions.router, prefix="/api/v1")
    app.include_router(backtesting.router, prefix="/api/v1")
    app.include_router(market.router, prefix="/api/v1")
    app.include_router(ws.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(routines.router, prefix="/api/v1")
    app.include_router(reports.router, prefix="/api/v1")

    # ── Serve interactive charts ──
    charts_dir = Path(__file__).resolve().parent.parent.parent / "charts"
    charts_dir.mkdir(exist_ok=True)
    app.mount("/charts", StaticFiles(directory=str(charts_dir)), name="charts")

    # ── Serve built frontend (production) ──
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.is_dir():
        index_html = dist / "index.html"
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="static-assets")

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            """SPA fallback: serve index.html for all non-API routes."""
            file_path = dist / full_path
            if full_path and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(index_html)

    return app
