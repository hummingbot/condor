"""Consult a domain-expert agent.

condor (the coordinator) delegates domain work to a specialized expert. The expert
runs in the MAIN process (where the agent runtime and server credentials live), so
this tool just calls back via the web API and returns the expert's answer. The
consult may block on a user confirmation (the expert is allowed to execute mutating
actions), so we use a generous timeout.
"""

from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.settings import settings

# Long enough to cover a pending user confirmation (CONFIRMATION_TIMEOUT=120) plus
# the expert's own model/tool latency.
_CONSULT_TIMEOUT = 180.0


async def consult(expert: str, task: str, context: str = "") -> dict:
    """Run a domain-expert consult and return its answer."""
    if not expert or not task:
        return {"error": "expert and task are required"}

    data = await call_main_api(
        "POST",
        f"/agents/{expert}/consult",
        {
            "task": task,
            "context": context,
            "chat_id": settings.chat_id,
            "user_id": settings.user_id,
            "server_name": settings.active_server or None,
        },
        timeout=_CONSULT_TIMEOUT,
    )
    return data if isinstance(data, dict) else {"answer": str(data)}
