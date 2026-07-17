"""Helpers shared across condor command modules (kept here so commands don't
import each other)."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

from condor.cli.output import ExitCode, fail

REPO_ROOT = Path(__file__).resolve().parents[3]

# One place for the wording every socket-backed command degrades with
# (docs/cli-plan.md, principle 2).
SOCKET_HINT = "control socket unavailable — is `condor serve` running?"


def control(
    method: str, params: Optional[dict] = None, *, timeout: float = 60.0
) -> Any:
    """One synchronous control-socket call with the exit-code mapping applied.

    503 (no socket) → NOT_RUNNING, 404 → NOT_FOUND, 400/422 → CONFIG_ERROR,
    anything else → ERROR; a wire timeout → TIMEOUT.
    """
    from condor.control.client import ControlError

    try:
        return asyncio.run(_call(method, params, timeout))
    except ControlError as e:
        if e.status == 503:
            fail(SOCKET_HINT, ExitCode.NOT_RUNNING)
        if e.status == 404:
            fail(e.message, ExitCode.NOT_FOUND)
        if e.status in (400, 422):
            fail(e.message, ExitCode.CONFIG_ERROR)
        fail(f"{method}: {e.message}", ExitCode.ERROR)
    except (asyncio.TimeoutError, TimeoutError):
        fail(f"{method} timed out after {timeout:g}s", ExitCode.TIMEOUT)


def try_control(
    method: str, params: Optional[dict] = None, *, timeout: float = 10.0
) -> Any:
    """Best-effort control call for commands that degrade gracefully when the
    server is down: returns None instead of exiting."""
    try:
        return asyncio.run(_call(method, params, timeout))
    except Exception:
        return None


async def _call(method: str, params: Optional[dict], timeout: float) -> Any:
    # Imported per call so tests can monkeypatch condor.control.client.call_control.
    from condor.control import client as control_client

    return await control_client.call_control(method, params, timeout=timeout)


def read_json_object_from_stdin() -> dict:
    """Read a JSON object {key: value} from stdin, failing clearly on bad input."""
    raw = sys.stdin.read()
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        fail(f"invalid JSON on stdin: {e}", ExitCode.CONFIG_ERROR)
    if not isinstance(parsed, dict):
        fail("stdin must be a JSON object of field -> value", ExitCode.CONFIG_ERROR)
    return parsed
