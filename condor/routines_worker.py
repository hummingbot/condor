"""Disposable routine worker subprocess (§7.2 — fault containment).

Authored routines used to import and run IN-PROCESS under
``asyncio.wait_for`` — a synchronous loop, blocking import, ``os._exit``, or
cancellation suppression could freeze or kill the host process, and creation
imported the new module during validation *before* any timeout applied.

Here import/validate/run each execute in a short-lived worker subprocess the
parent can SIGKILL on timeout: the timeout is actually hard, and a crash
takes down only the worker.

Contract (honest, per the plan): the worker passes **no credentials, store
keys, execution clients, socket paths, or capabilities** — structured inputs
in, data out — and its environment is scrubbed of ``CONDOR_*``/token-shaped
variables. It still runs as the same OS user: filesystem/socket isolation is
explicitly NOT claimed (that would need an OS sandbox).

Parent side: :func:`run_routine_in_worker` / :func:`validate_routine_in_worker`.
Worker side: ``python -m condor.routines_worker`` (JSON on stdin → JSON on
stdout).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 120

# Environment variables the worker must never see: everything condor- or
# trading-credential-shaped. Conservative allowlist-of-denylist: prefix +
# substring. The boundary is TRADING authority, not "all keys" — a read-only
# data routine may legitimately hold a market-data key (see _ALLOW_EXACT); it
# must never hold anything that can move funds (wallet/private keys, exchange
# trading secrets, capabilities, bot tokens).
_DENY_PREFIXES = ("CONDOR_", "TELEGRAM_", "ANTHROPIC_", "OPENAI_")
_DENY_SUBSTRINGS = ("TOKEN", "SECRET", "PRIVATE", "API_KEY", "PASSPHRASE", "MNEMONIC")

# Read-only data keys routines ARE allowed to use — matched exactly, so a
# lookalike (e.g. a `_SECRET` variant) is still scrubbed. These grant data
# access only: no trading, withdrawal, or account authority.
_ALLOW_EXACT = {"COINGECKO_API_KEY"}


def scrubbed_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if ku in _ALLOW_EXACT:
            env[k] = v
            continue
        if ku.startswith(_DENY_PREFIXES):
            continue
        if any(part in ku for part in _DENY_SUBSTRINGS):
            continue
        env[k] = v
    return env


class RunContext:
    """The supported routine context: structured attribution only — no
    credentials, clients, sockets, or capabilities (§7.2)."""

    def __init__(self, agent_slug: str = ""):
        self.agent_slug = agent_slug


async def _spawn(payload: dict, timeout_s: int) -> dict:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "condor.routines_worker",
        cwd=str(Path(__file__).parent.parent),
        env=scrubbed_env(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(json.dumps(payload).encode()), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        proc.kill()  # SIGKILL — the hard timeout the in-process path never had
        await proc.wait()
        return {
            "error": f"routine worker timed out after {timeout_s}s (killed)"
        }
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-2000:]
        return {
            "error": f"routine worker died (exit {proc.returncode}): {tail}"
        }
    try:
        # The routine may print to stdout; the result is the LAST line.
        last_line = stdout.decode(errors="replace").strip().splitlines()[-1]
        return json.loads(last_line)
    except (json.JSONDecodeError, IndexError):
        return {"error": "routine worker returned no parseable result"}


async def run_routine_in_worker(
    name: str,
    config: dict | None = None,
    *,
    agent_slug: str = "",
    attribute: str = "",
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict:
    """Run a one-shot routine in a disposable worker. Returns the routine's
    normalized result dict, or {"error": ...}. ``attribute`` names the report
    producer (defaults to the agent_slug, else "condor")."""
    return await _spawn(
        {
            "action": "run",
            "name": name,
            "config": config or {},
            "agent_slug": agent_slug,
            "attribute": attribute or agent_slug or "condor",
        },
        timeout_s,
    )


async def validate_routine_in_worker(
    path: str, *, timeout_s: int = 30
) -> dict:
    """Import + validate a routine module in a disposable worker — a blocking
    top-level import can no longer hang the host."""
    return await _spawn({"action": "validate", "path": path}, timeout_s)


# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------


def _resolve(name: str, agent_slug: str):
    if agent_slug:
        from condor.agents.agent import agents_data_root
        from routines.base import discover_routines_from_path

        routines_dir = agents_data_root() / agent_slug / "routines"
        if routines_dir.exists():
            found = discover_routines_from_path(routines_dir, agent_slug=agent_slug)
            if name in found:
                return found[name]
    from routines.base import discover_routines

    return discover_routines().get(name)


async def _do_run(req: dict) -> dict:
    name = req["name"]
    routine = _resolve(name, req.get("agent_slug", ""))
    if routine is None:
        return {"error": f"Routine '{name}' not found"}
    if routine.is_continuous:
        return {
            "error": f"Routine '{name}' is continuous — routines are one-shot "
            "now; schedule it instead (the scheduler provides the repetition)"
        }
    try:
        config_obj = routine.config_class(**(req.get("config") or {}))
    except Exception as e:  # noqa: BLE001
        return {"error": f"Invalid config: {e}"}
    from condor.reports import attribute_to

    with attribute_to(req.get("attribute") or "condor"):
        result = await routine.run_fn(
            config_obj, RunContext(req.get("agent_slug", ""))
        )
    from routines.base import normalize_result

    nr = normalize_result(result)
    return {
        "name": name,
        "result": {
            "text": nr.text,
            "table_data": nr.table_data,
            "table_columns": nr.table_columns,
            "chart_image_b64": (
                base64.b64encode(nr.chart_image).decode()
                if nr.chart_image
                else None
            ),
            "sections": nr.sections,
        },
    }


def _do_validate(req: dict) -> dict:
    import importlib.util

    path = Path(req["path"])
    if not path.exists():
        return {"error": f"no such module: {path}"}
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # the import that used to run unguarded
    except Exception as e:  # noqa: BLE001
        return {"error": f"import failed: {e}"}
    problems = []
    if not hasattr(module, "Config"):
        problems.append("missing Config (pydantic BaseModel)")
    if not hasattr(module, "run"):
        problems.append("missing async run(config, context)")
    if problems:
        return {"error": "; ".join(problems)}
    return {"ok": True, "continuous": bool(getattr(module, "CONTINUOUS", False))}


def main() -> None:
    req = json.loads(sys.stdin.read() or "{}")
    action = req.get("action")
    if action == "run":
        out = asyncio.run(_do_run(req))
    elif action == "validate":
        out = _do_validate(req)
    else:
        out = {"error": f"unknown worker action: {action!r}"}
    print(json.dumps(out, default=str))


if __name__ == "__main__":
    main()
