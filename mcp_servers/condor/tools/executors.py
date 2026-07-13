"""Native executor tools — create/stop/list Condor-owned executors.

Executors run in the main Condor process (they must outlive this MCP
subprocess), so every action goes through the /executors REST routes.
"""

from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.settings import settings


async def manage_executors(
    action: str,
    executor_type: str | None = None,
    config: dict | None = None,
    executor_id: str | None = None,
    agent_id: str | None = None,
    keep_position: bool = True,  # stop: True = detach (position stays open), False = close on-chain
    group_by: str | None = None,  # performance: agent | run | strategy | venue | type
) -> dict | list:
    if action == "create":
        if not executor_type or config is None:
            return {"error": "create requires executor_type and config"}
        body = {
            "type": executor_type,
            "config": config,
            # Attribution trio threaded into this subprocess by the run:
            # slug = WHO, agent_id = WHICH RUN (session "{slug}_{N}" or
            # delegation "{slug}-dN"), strategy = WHICH PLAYBOOK (ticks only).
            "agent_slug": settings.agent_slug or "",
            "agent_id": agent_id or settings.agent_id or "",
            "strategy": settings.strategy or "",
            # Trade-notification routing back to this run's owner
            "user_id": settings.user_id or 0,
            "chat_id": settings.chat_id or 0,
        }
        return await call_main_api("POST", "/executors", body, timeout=60)

    if action == "stop":
        if not executor_id:
            return {"error": "stop requires executor_id"}
        return await call_main_api(
            "POST", f"/executors/{executor_id}/stop", {"keep_position": keep_position}
        )

    if action == "get":
        if not executor_id:
            return {"error": "get requires executor_id"}
        return await call_main_api("GET", f"/executors/{executor_id}")

    if action == "list":
        path = "/executors"
        if agent_id:
            path += f"?agent_id={agent_id}"
        return await call_main_api("GET", path)

    if action == "performance":
        params = [f"group_by={group_by or 'agent'}"]
        if agent_id:
            params.append(f"agent_id={agent_id}")
        return await call_main_api("GET", f"/executors/performance?{'&'.join(params)}")

    return {"error": f"Unknown action: {action} (create|stop|get|list|performance)"}
