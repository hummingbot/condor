"""The collector service: two endpoints and a background rollup.

``create_app()`` mirrors ``condor/web/app.py`` — a factory returning a
configured :class:`~fastapi.FastAPI`, so the test suite builds an app against a
throwaway database instead of importing a module-level singleton that has
already opened a connection to whatever the environment pointed at.

There is deliberately no read endpoint. Nothing here serves data back to an
install, so the only thing this process will do for an anonymous caller is
accept an envelope and say how much of it it kept.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from telemetry_server import config, ingest, rollup
from telemetry_server.store import Store, open_store

log = logging.getLogger(__name__)


async def _rollup_loop(app: FastAPI) -> None:
    """Recompute the permanent tables on a fixed interval.

    A plain asyncio task rather than a scheduler dependency: the job is "run
    this every few hours, never concurrently with itself", which is a loop, and
    one service with no extra runtime is easier to reason about than one with a
    scheduler in it.
    """
    interval = config.rollup_interval_s()
    while True:
        try:
            await asyncio.sleep(interval)
            await rollup.run(app.state.store)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Telemetry rollup failed; will retry next interval")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "store", None) is None:
        app.state.store = await open_store(config.dsn())
        app.state.owns_store = True
    task = asyncio.create_task(_rollup_loop(app))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if getattr(app.state, "owns_store", False):
            await app.state.store.close()


def create_app(store: Store | None = None) -> FastAPI:
    """Build the collector. Pass a ``store`` to drive it against a temp database."""
    app = FastAPI(
        title="Condor Telemetry Collector",
        version="1.0.0",
        lifespan=lifespan,
        # The ingest contract is documented in this repository; a public schema
        # browser on an unauthenticated endpoint is surface with no reader.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.state.owns_store = False
    app.include_router(ingest.router)

    @app.get("/health")
    async def health() -> JSONResponse:
        """Liveness plus a real database round trip."""
        try:
            await app.state.store.ping()
        except Exception:
            log.exception("Telemetry collector health check failed")
            return JSONResponse(status_code=503, content={"status": "degraded"})
        return JSONResponse(status_code=200, content={"status": "ok"})

    return app
