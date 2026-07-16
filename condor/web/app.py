from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
    native_executors,
    venues,
    notifications,
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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Native executor runtime (docs/condor-simple.md): reconcile persisted
    # executors + start the watchdog in this (main) process.
    import asyncio

    from condor.control.handlers import build_all_handlers
    from condor.control.server import ControlServer
    from condor.executors.service import start_executor_service, stop_executor_service

    # Control socket FIRST: the surface a harness-spawned MCP subprocess drives
    # (the headless/serverless path). Same process as the runtime + engines, so
    # the web host and the standalone `condor.daemon` expose the identical
    # surface. Reconciliation then runs in the background — reads and stops work
    # immediately; executor CREATE is 503-gated until runtime_ready() (a slow
    # venue once held the socket down ~20 min when this was sequential).
    from condor.executors.capabilities import get_capability_registry

    registry = get_capability_registry()
    control = ControlServer(
        build_all_handlers(),
        on_conn_close=registry.revoke_connection,
    )
    await control.start()
    # Direct-token bootstrap (§6.2): rotated every restart; a harness-spawned
    # MCP wrapper exchanges it over a persistent connection for a
    # condor-direct capability that dies with that connection.
    registry.write_direct_token()

    async def _start_services():
        # §12 ordering: scheduled fires enable LAST — a fire that came due
        # during a slow startup is skipped (no backfill), never launched
        # against half-reconciled state.
        await start_executor_service()
        from condor.agents.scheduler import get_scheduler

        get_scheduler().start()

    service_task = asyncio.create_task(_start_services())
    try:
        yield
    finally:
        if not service_task.done():
            service_task.cancel()
            try:
                await service_task
            except (asyncio.CancelledError, Exception):
                pass
        from condor.agents.scheduler import get_scheduler

        await get_scheduler().stop()
        await control.stop()
        await stop_executor_service()


def create_app() -> FastAPI:
    app = FastAPI(title="Condor Dashboard API", version="0.1.0", lifespan=_lifespan)

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
    app.include_router(native_executors.router, prefix="/api/v1")
    app.include_router(venues.router, prefix="/api/v1")
    app.include_router(notifications.router, prefix="/api/v1")

    # ── Serve report HTML files ──
    reports_dir = Path(__file__).resolve().parent.parent.parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    app.mount("/reports", StaticFiles(directory=str(reports_dir)), name="reports")

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
            """SPA fallback: serve index.html for all non-API routes."""
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
