"""Ad-hoc Python execution — the scratchpad below a routine (FEAT-047).

Same split as ``tools/routines.py``, for the same reason: reading history is
local (this subprocess shares the filesystem), *executing* is not. A snippet
must run where the cached API clients, the report index's single writer and the
real bot are, so ``run`` crosses to the main process over ``call_main_api`` and
everything else reads ``data/code_runs/`` directly.

What the model gets back is clipped, on every action. A snippet that failed is
read back with ``action="get"``, corrected, and re-run — which is the whole
point of persisting these at all — and ``get`` pages through long output with
``offset`` rather than returning all of it in one result (RT-08).
"""

from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.settings import caller_slug as _caller_slug
from mcp_servers.condor.settings import settings

# Snippet budget. The main process clamps to the same ceiling; this copy exists
# so a bad `timeout` is corrected before it becomes an HTTP deadline.
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 120

# Slack over the snippet's own budget, so the HTTP call never gives up on a run
# the main process is still cutting short itself — a timeout must come back as
# a recorded run, not as an unreachable-API error.
_HTTP_MARGIN = 15

# Per-field ceiling in the tool's *reply*. Enough for a table or a traceback,
# small enough that a runaway print loop cannot flood a context window.
_CLIP = 4000

# The same ceiling for ``get``, which is the "read it all" door and so gets a
# larger window — but a window: one result must still fit a context, and the
# rest is one more call away with ``offset``.
_GET_CLIP = 20000


def _store():
    """A fresh store per call — the index is written by the *other* process.

    A cached singleton here would answer ``history`` from whatever the index
    held when this subprocess started, which for a long-lived session is every
    run except the ones the caller just made.
    """
    from condor.code_runs import CodeRunStore

    return CodeRunStore()


def _clip(text: str) -> tuple[str, bool]:
    text = text or ""
    if len(text) <= _CLIP:
        return text, False
    return text[:_CLIP], True


def _reply(run: dict) -> dict:
    """The run as the caller sees it: clipped, with a pointer to the full copy."""
    stdout, stdout_cut = _clip(run.get("stdout", ""))
    result, result_cut = _clip(run.get("result", ""))
    reply = {
        "run_id": run.get("id", ""),
        "status": run.get("status", ""),
        "stdout": stdout,
        "result": result,
        "error": run.get("error", ""),
        "traceback": run.get("traceback", ""),
        "report_id": run.get("report_id", ""),
        "duration_ms": run.get("duration_ms", 0),
    }
    if stdout_cut or result_cut:
        reply["truncated"] = True
        reply["note"] = (
            f"Output was clipped at {_CLIP} chars. Read the full run with "
            f"run_code(action='get', run_id='{reply['run_id']}')."
        )
    return reply


async def run_snippet(code: str, label: str, timeout: int | None) -> dict:
    """Execute a snippet in the main process and return its recorded run."""
    if not (code or "").strip():
        return {"error": "code is required for action='run'"}

    budget = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    run = await call_main_api(
        "POST",
        "/code/run",
        {
            "code": code,
            # Named, not required: a snippet that never touches a client runs
            # fine without one, so a missing server is passed through rather
            # than refused.
            "server_name": settings.active_server or "",
            "label": label or "",
            "timeout": budget,
            "attribute_to": _caller_slug(),
            "session_key": settings.session_key,
        },
        timeout=budget + _HTTP_MARGIN,
    )
    if not isinstance(run, dict):
        return {"error": f"Unexpected response from the code runner: {run}"}
    return _reply(run)


def list_runs(agent: str | None, limit: int) -> dict:
    """Recent runs, newest first — the caller's own unless asked otherwise."""
    scope = None if agent == "all" else (agent or _caller_slug())
    return {
        "agent": scope or "all",
        "runs": _store().list(agent=scope, limit=max(1, min(int(limit or 20), 100))),
    }


def get_run(run_id: str | None, offset: int = 0) -> dict:
    """One past run — code and traceback whole, stdout and result as a window.

    ``offset`` is where the window starts in ``stdout`` and in ``result``
    (characters). A run whose output is longer than the window says so and names
    the offset that continues it, so a long run is read in pages instead of
    arriving as one result too large for the host to accept.
    """
    if not run_id:
        return {"error": "run_id is required for action='get'"}
    run = _store().get(run_id)
    if not run:
        return {"error": f"No code run '{run_id}'"}

    start = max(0, int(offset or 0))
    end = start + _GET_CLIP
    remaining = 0
    for field in ("stdout", "result"):
        text = run.get(field) or ""
        run[field] = text[start:end]
        remaining = max(remaining, len(text) - end)
    if start:
        run["offset"] = start
    if remaining > 0:
        run["truncated"] = True
        run["next_offset"] = end
        run["note"] = (
            f"stdout/result are shown from character {start} to {end}; up to "
            f"{remaining} more follow. Continue with run_code(action='get', "
            f"run_id='{run_id}', offset={end})."
        )
    return run


async def run_code(
    code: str | None = None,
    action: str = "run",
    label: str = "",
    timeout: int | None = None,
    run_id: str | None = None,
    agent: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Dispatch the tool's three actions."""
    if action == "run":
        return await run_snippet(code or "", label, timeout)
    if action == "history":
        return list_runs(agent, limit)
    if action == "get":
        return get_run(run_id, offset)
    return {"error": f"Unknown action '{action}'. Use run | history | get."}
