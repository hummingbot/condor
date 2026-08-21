"""Tailscale `serve` integration for Condor's web dashboard.

When ``USE_TAILSCALE=true``, ``main.py`` binds the dashboard to 127.0.0.1
only (see ``_run_dual()``) instead of every interface, and calls
:func:`ensure_serve` here so the tailnet can still reach it — the same
check-then-set pattern hummingbot-api's own ``Makefile`` already uses for
its dev-mode Tailscale run (``tailscale serve status | grep ... || tailscale
serve --bg ...``).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def _run(*args: str, timeout: float = 15) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return 127, "tailscale not found on PATH"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        await proc.wait()
        return 124, "timed out"
    return proc.returncode, (stdout or b"").decode(errors="replace").strip()


async def ensure_serve(port: int) -> bool:
    """Ensure the tailnet forwards ``port`` to ``127.0.0.1:port``.

    Idempotent: checks ``tailscale serve status`` first and only runs
    ``tailscale serve --bg`` when the port isn't already forwarded. Never
    escalates with ``sudo`` — that's a one-time install-time step
    (``setup-environment.sh``), not something a long-running bot process
    should attempt unattended. Returns whether the port is confirmed
    forwarded; callers must not fall back to a public bind on failure, since
    the whole point of binding to 127.0.0.1 is to fail closed, not open.
    """
    code, out = await _run("tailscale", "serve", "status")
    if code == 0 and f":{port}" in out:
        return True

    code, out = await _run(
        "tailscale", "serve", "--bg", f"http:{port}", f"http://127.0.0.1:{port}"
    )
    if code == 0:
        return True

    logger.error(
        "Could not confirm `tailscale serve` for port %s (%s). The dashboard "
        "is bound to 127.0.0.1 only and won't be reachable on the tailnet "
        "until this is fixed. Run manually: sudo tailscale serve --bg "
        "http:%s http://127.0.0.1:%s",
        port,
        out,
        port,
        port,
    )
    return False
