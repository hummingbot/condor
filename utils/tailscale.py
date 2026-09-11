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
import subprocess

logger = logging.getLogger(__name__)

# Tailscale hands every node an address in the CGNAT range 100.64.0.0/10.
_TAILNET_PREFIXES = tuple(f"100.{n}." for n in range(64, 128))


def is_tailnet_ip(host: str) -> bool:
    """Whether ``host`` is a Tailscale CGNAT address (100.64.0.0/10)."""
    return host.startswith(_TAILNET_PREFIXES)


def tailnet_ip(timeout: float = 5) -> str | None:
    """This node's own IPv4 tailnet address, or ``None`` if it has none.

    Synchronous on purpose: the one caller is
    :func:`utils.config.resolve_web_host`, which runs at import time to decide
    a bind address, long before there is an event loop. ``None`` means "could
    not confirm", and every caller must treat that as *bind loopback*, never as
    permission to fall back to ``0.0.0.0`` -- the whole point of this address
    is that it is narrower than a public bind, so failing open would invert it.
    """
    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        candidate = line.strip()
        if candidate and is_tailnet_ip(candidate):
            return candidate
    return None


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

    # `tailscale serve <type>:<port> <target>` was removed; current clients take
    # a single target plus a port flag and reject the old two-positional form
    # outright ("invalid argument format", tested against 1.102.3).
    code, out = await _run(
        "tailscale", "serve", "--bg", f"--http={port}", f"http://127.0.0.1:{port}"
    )
    if code == 0:
        return True

    logger.error(
        "Could not confirm `tailscale serve` for port %s (%s). The dashboard "
        "is bound to 127.0.0.1 only and won't be reachable on the tailnet "
        "until this is fixed. Run manually: sudo tailscale serve --bg "
        "--http=%s http://127.0.0.1:%s -- note this needs root or a prior "
        "`sudo tailscale set --operator=$USER`; the daemon socket refuses "
        "unprivileged callers.",
        port,
        out,
        port,
        port,
    )
    return False
