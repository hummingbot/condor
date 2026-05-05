from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from condor.web.routes import agents, archived, auth, backtesting, bots, chat_ws, executors, market, portfolio, positions, reports, routines, servers, ws


def create_app() -> FastAPI:
    app = FastAPI(title="Condor Dashboard API", version="0.1.0")

    # CORS – allow Vite dev server and common local origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8088",
        ],
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
    app.include_router(chat_ws.router, prefix="/api/v1")

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
