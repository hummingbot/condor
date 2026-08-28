"""Delegate a one-off task to a background agent instance.

DELEGATE is the async, unattended sibling of CONSULT: instead of blocking for an
answer, it hands a goal-oriented task to a detached Agent that runs until done,
then notifies the user. This tool just calls back into the main process (where the
agent runtime lives) via the web API and returns a ``task_id`` to poll/stop.
"""

from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.settings import settings

# What the asking conversation gets when the task ends. Spelled out here rather
# than imported from ``condor.agents.delegate``: this runs in the MCP subprocess,
# which deliberately talks to the main process over HTTP and never imports it.
ON_COMPLETE_CHOICES = ("notify", "resume")

# How the user tracks a delegation, per surface. The two surfaces have genuinely
# different UIs for this, and the hint is quoted back to the user verbatim, so a
# dashboard-only install was being told to run a command it does not have
# (CORR-262). Only the middle clause varies -- the "do NOT invent a status
# command" guard is what the hint existed for and is surface-independent.
TRACK_TELEGRAM = (
    "they can check progress anytime with the /delegations command in Telegram"
)
TRACK_DASHBOARD = (
    "they can check progress anytime in the dashboard, in the Tasks list of the "
    "chat's context dock"
)


def _next_steps(session_key: str) -> str:
    """The tracking hint for the surface this subprocess was spawned from.

    ``session_key`` is a canonical key ("web:7:slot-1", "tg:42", …) minted by
    ``_spawn_session``; the prefix is the surface. An unknown or empty key means
    the seat is not a Telegram chat (a consult, a tick, an external MCP host), so
    the dashboard wording is the honest default: it names a UI that exists on
    every install, while /delegations only exists where Telegram does.
    """
    surface = (session_key or "").split(":", 1)[0].strip().lower()
    where = TRACK_TELEGRAM if surface == "tg" else TRACK_DASHBOARD
    return (
        "Running in the background — the user is notified automatically when it "
        "finishes, and the outcome is written into this conversation and shown "
        f"here as it lands. Tell them {where}. You can poll it yourself "
        'with delegate(action="get", task_id="<id>"). Do NOT invent any '
        "other status command (e.g. there is no /task command)."
    )


async def delegate(
    action: str,
    agent: str = "",
    task: str = "",
    task_id: str = "",
    on_complete: str = "notify",
) -> dict:
    """Dispatch a delegate action (start | list | get | stop)."""
    action = (action or "").lower()

    if action == "start":
        # Hard stop, not a prompt rule (FEAT-032): a background worker runs with
        # auto-approved tools, so a worker that can start delegations is unbounded
        # fan-out. The instructions say the same thing; this is what enforces it.
        if settings.delegate_worker and not settings.specialist_slug:
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
        # FEAT-041: an agent may spawn a background copy of ITSELF, which makes
        # self-recursion the shape that runs away — a copy that spawns a copy,
        # each auto-approving its own tools. Depth is bounded at one by refusing
        # it from the background seat. A specialist worker handing work to a PEER
        # is a different agent doing different work; that stays open, exactly as
        # `_agent_base` invites.
        if settings.delegate_worker and agent == settings.agent_slug:
            return {
                "error": (
                    f"Self-delegation refused: you ARE a background session of "
                    f"'{settings.agent_slug}', started to finish one task "
                    "unattended. Spawning another copy of yourself from here "
                    "would recurse with auto-approved tools and nobody watching. "
                    "Do the task in this session and report the result. (Polling "
                    'with action="get"/"list" is allowed, and you may still hand '
                    "work outside your own domain to a PEER agent.)"
                )
            }
        if not agent or not task:
            return {"error": "agent and task are required to start a delegation"}
        on_complete = (on_complete or "notify").lower()
        if on_complete not in ON_COMPLETE_CHOICES:
            # An error, not a silent default: a model that meant "resume" and
            # got "notify" would wait forever for a turn that never comes.
            return {
                "error": (
                    f"on_complete must be one of {list(ON_COMPLETE_CHOICES)}, "
                    f"got '{on_complete}'"
                )
            }
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
                "on_complete": on_complete,
            },
        )
        # Spell out how the user tracks this so the model never INVENTS a status
        # command, and name the surface they are actually on: there is no "/task"
        # command anywhere, and no "/delegations" one outside Telegram.
        if isinstance(result, dict) and not result.get("error"):
            result["next_steps"] = _next_steps(settings.session_key)
            if on_complete == "resume":
                # Said explicitly so the model ends its turn instead of
                # burning it polling for a result that will be handed to it.
                result["next_steps"] += (
                    " You asked to be resumed: when the task finishes this "
                    "conversation gets a new turn carrying the result, so end "
                    "your turn now and continue then — do not poll or wait."
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
