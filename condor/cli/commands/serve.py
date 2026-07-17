"""``condor serve`` — THE process (§9.3): control socket + scheduler +
execution runtime + web app, in the web app's lifespan.

Logs go to stdout AND a rotating file under ``store/logs/condor.log`` (the
gap that made incident 2's model-switch cause unsettleable — docs/cli-plan.md
Tier 1). ``condor logs`` tails the file. The bind is mechanically
loopback-only (§5.5).
"""

import logging
import logging.handlers
import os

import typer

from condor.cli.commands._common import REPO_ROOT
from condor.cli.output import ExitCode, fail

LOG_DIR = REPO_ROOT / "store" / "logs"
LOG_FILE = LOG_DIR / "condor.log"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 5


def configure_file_logging() -> None:
    """Root logger → stdout + rotating file. uvicorn runs with
    ``log_config=None`` so its loggers propagate here instead of installing
    their own stdout-only handlers."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS
    )
    stream_handler = logging.StreamHandler()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (file_handler, stream_handler):
        handler.setFormatter(formatter)
        root.addHandler(handler)


def serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind host — loopback only; non-loopback is rejected (§5.5).",
    ),
    port: int = typer.Option(3000, "--port"),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Disable HTTP assets (no dashboard frontend; API + socket only).",
    ),
) -> None:
    """Run Condor: control socket + scheduler + executor runtime + web app."""
    import uvicorn

    from condor.web.security import validate_bind_host

    try:
        validate_bind_host(host)
    except SystemExit as e:
        fail(str(e), ExitCode.CONFIG_ERROR)
    if headless:
        os.environ["CONDOR_HEADLESS"] = "1"
    configure_file_logging()
    uvicorn.run(
        "condor.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_config=None,
    )
