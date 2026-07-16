"""Consult a domain agent.

condor (the coordinator) delegates domain work to a specialized agent. The agent
runs in the MAIN process (where the agent runtime and server credentials live), so
this tool just calls back via the web API and returns the agent's answer. The
consult may block on a user confirmation (the agent is allowed to execute mutating
actions), so we use a generous timeout.
"""

from mcp_servers.condor.condor_client import call_control
from mcp_servers.condor.settings import settings

# Long enough to cover a pending user confirmation (CONFIRMATION_TIMEOUT=120) plus
# the agent's own model/tool latency.
_CONSULT_TIMEOUT = 180.0


async def consult(agent: str, task: str, context: str = "") -> dict:
    """Run a domain agent consult and return its answer."""
    if not agent or not task:
        return {"error": "agent and task are required"}

    data = await call_control(
        "agent.consult",
        {
            "agent": agent,
            "task": task,
            "context": context,
            "chat_id": settings.chat_id,
            "user_id": settings.user_id,
        },
        timeout=_CONSULT_TIMEOUT,
    )
    return data if isinstance(data, dict) else {"answer": str(data)}
