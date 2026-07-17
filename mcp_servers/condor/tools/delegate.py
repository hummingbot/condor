"""Delegate a one-off task to a background agent instance.

DELEGATE is the async, unattended sibling of CONSULT: instead of blocking for an
answer, it hands a goal-oriented task to a detached Agent that runs until done,
then notifies the user. It just calls back into the main process (where the
agent runtime lives) over the control socket and returns a ``run_id``.

A delegation IS a run: there is no delegate get/list/stop — track it with
``get_run(run_id)`` and stop it with ``control_run(run_id, "stop")``, like any
run (C.1).
"""

from mcp_servers.condor.condor_client import call_control


async def delegate(
    agent: str = "",
    task: str = "",
    risk_limits: dict | None = None,
) -> dict:
    """Start a detached background delegation; returns its ``run_id``."""
    if not agent or not task:
        return {"error": "agent and task are required to start a delegation"}
    result = await call_control(
        "delegate.start",
        {"agent": agent, "task": task, "risk_limits": risk_limits},
    )
    if isinstance(result, dict) and not result.get("error"):
        result["next_steps"] = (
            "Running in the background — the user is notified automatically when "
            "it finishes. A delegation IS a run: check progress with "
            'get_run(run_id="<id>") and stop it with '
            'control_run(run_id="<id>", verb="stop"). Do NOT invent a delegate '
            "get/list/stop command — there is none."
        )
    return result
