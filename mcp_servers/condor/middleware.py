"""Error-handling + tool-profile middleware for MCP tool functions."""

import functools
import logging

from mcp_servers.condor.exceptions import APIError, ToolError
from mcp_servers.condor.settings import settings

logger = logging.getLogger("condor.mcp")

# Named tool profiles: which tools a restricted subprocess may execute.
# Enforcement is call-time (the tools stay discoverable but refuse to run),
# which is the line that holds for ACP models — they cannot be tool-allowlisted
# client-side the way pydantic-ai models can. An unknown profile fails closed.
TOOL_PROFILES: dict[str, set[str]] = {
    # The skill-curation pass: read/patch its own skills, read journals, mark
    # learnings promoted, read memory. No lifecycle, no delegation, no routines,
    # no notifications — the curation flow itself notifies.
    "curation": {
        "manage_skill",
        "trading_agent_journal_read",
        "trading_agent_journal_write",
        "manage_memory",
        "get_user_context",
    },
}


def _profile_blocks(tool_name: str) -> str | None:
    """Return a refusal message when the active profile excludes ``tool_name``."""
    profile = settings.tool_profile
    if not profile:
        return None
    allowed = TOOL_PROFILES.get(profile)
    if allowed is None:
        return (
            f"unknown tool profile '{profile}' — failing closed; no tools are "
            "callable until the profile is fixed"
        )
    if tool_name not in allowed:
        return (
            f"the '{profile}' tool profile does not permit {tool_name} — this "
            f"run may only use: {', '.join(sorted(allowed))}"
        )
    return None


def handle_errors(action_name: str):
    """Decorator that enforces the tool profile and returns error dicts."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            blocked = _profile_blocks(fn.__name__)
            if blocked:
                logger.warning("Tool profile blocked %s: %s", fn.__name__, blocked)
                return {"error": blocked}
            try:
                return await fn(*args, **kwargs)
            except (ToolError, APIError):
                raise
            except Exception as e:
                logger.exception("MCP tool error in %s", action_name)
                return {"error": f"{action_name} failed: {e}"}

        return wrapper

    return decorator
