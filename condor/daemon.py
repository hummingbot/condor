"""Headless Condor daemon — executor runtime + control socket, no FastAPI/telegram.

Run this to drive Condor entirely through the MCP control socket (the
harness-agnostic, serverless mode): agents create/stop executors, consult,
delegate, and start/stop sessions over the socket; nothing needs the web app.

    python -m condor.daemon

The web app (`main.py`) is the *alternative* host — it starts the same runtime +
control socket in its lifespan and also serves the dashboard. Run **one** of the
two, never both: they share the executor runtime, the transaction logs, and the
single control socket.
"""

from __future__ import annotations

import asyncio
import logging
import signal

logger = logging.getLogger("condor.daemon")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from condor.control.handlers import build_all_handlers
    from condor.control.server import ControlServer
    from condor.executors.service import start_executor_service, stop_executor_service

    # Control socket FIRST (reads/stops available immediately), then reconcile
    # persisted executors against Gateway in the background — executor CREATE
    # is 503-gated until reconcile finishes (mirrors the web-app lifespan).
    control = ControlServer(build_all_handlers())
    await control.start()
    service_task = asyncio.create_task(start_executor_service())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("condor daemon ready — control socket + executor runtime up; Ctrl-C to stop")

    await stop.wait()
    logger.info("condor daemon shutting down")
    if not service_task.done():
        service_task.cancel()
        try:
            await service_task
        except (asyncio.CancelledError, Exception):
            pass
    await control.stop()
    await stop_executor_service()


if __name__ == "__main__":
    asyncio.run(main())
