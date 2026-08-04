"""Delegate a one-off task to a background agent instance.

DELEGATE is the async, unattended sibling of CONSULT: instead of blocking for an
answer, it hands a goal-oriented task to a detached Agent that runs until done,
then notifies the user. This tool just calls back into the main process (where the
agent runtime lives) via the web API and returns a ``task_id`` to poll/stop.
"""

from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.settings import settings


async def delegate(
    action: str,
    agent: str = "",
    task: str = "",
    task_id: str = "",
) -> dict:
    """Dispatch a delegate action (start | list | get | stop)."""
    action = (action or "").lower()

    if action == "start":
        # Hard stop, not a prompt rule (FEAT-032): a background worker runs with
        # auto-approved tools, so a worker that can start delegations is unbounded
        # fan-out. The instructions say the same thing; this is what enforces it.
        if settings.delegate_worker:
            return {
                "error": (
                    "Nested delegation refused: you ARE a background delegation "
                    "worker, started to finish one task unattended. Starting "
                    "another detached agent from here would fan out unbounded "
                    "work with auto-approved tools and nobody watching. Do the "
                    "task in this session and report the result. (Polling an "
                    'existing delegation with action="get"/"list" is allowed.)'
                )
            }
        if not agent or not task:
            return {"error": "agent and task are required to start a delegation"}
        result = await call_main_api(
            "POST",
            f"/agents/{agent}/delegate",
            {
                "task": task,
                "chat_id": settings.chat_id,
                "user_id": settings.user_id,
                "server_name": settings.active_server or None,
                # Provenance: the route resolves this to the conversation that
                # asked for the work, so the chat can watch what it started.
                "session_key": settings.session_key,
            },
        )
        # Spell out how the user tracks this so the model never INVENTS a status
        # command. There is no "/task" command — the user-facing one is
        # "/delegations"; the user is also pinged automatically on completion.
        if isinstance(result, dict) and not result.get("error"):
            result["next_steps"] = (
                "Running in the background — the user is notified automatically "
                "when it finishes. Tell them they can check progress anytime with "
                "the /delegations command in Telegram. You can poll it yourself "
                'with delegate(action="get", task_id="<id>"). Do NOT invent any '
                "other status command (e.g. there is no /task command)."
            )
        return result

    if action == "list":
        return await call_main_api("GET", "/agents/delegations")

    if action == "get":
        if not task_id:
            return {"error": "task_id is required for get"}
        return await call_main_api("GET", f"/agents/delegations/{task_id}")

    if action == "stop":
        if not task_id:
            return {"error": "task_id is required for stop"}
        return await call_main_api("POST", f"/agents/delegations/{task_id}/stop")

    return {"error": f"Unknown action '{action}'. Use start | list | get | stop."}
