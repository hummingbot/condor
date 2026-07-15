"""Native executor tools — create/stop/list Condor-owned executors.

Executors run in the persistent Condor process (they must outlive this MCP
subprocess), so every action goes through the unix control socket.
"""

from mcp_servers.condor.condor_client import call_control
from mcp_servers.condor.settings import settings

# type = {kind}_{instrument}: kind ∈ {order, position}, instrument ∈ {spot, perp, pred}.
_EXECUTOR_TYPES = {
    "order_spot", "order_perp", "order_pred",
    "position_spot", "position_perp", "position_pred",
}


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
        if executor_type not in _EXECUTOR_TYPES:
            return {
                "error": f"unknown executor_type '{executor_type}'; "
                f"expected one of {sorted(_EXECUTOR_TYPES)}"
            }
        # Code-enforced post-stop cooldown: refuse to re-enter a spot token this
        # agent was just stopped out of (the LLM does not hold the blacklist
        # reliably — it re-bought falling knives). This is the only create path,
        # so the block cannot be bypassed.
        if executor_type == "position_spot":
            from condor.agents.token_blacklist import stopped_out_reason

            reason = stopped_out_reason(
                settings.agent_slug or "", (config or {}).get("base_token", "")
            )
            if reason:
                return {"error": f"entry blocked — {reason}"}
        return await call_control(
            "executor.create",
            {
                "type": executor_type,
                "config": config,
                # Attribution pair threaded into this subprocess by the run:
                # slug = WHO, agent_id = WHICH RUN (session "{slug}_{N}" or
                # delegation "{slug}-dN"). strategy is retired (§5.3 Agent+
                # Strategy collapse) — kept "" for record-schema compat.
                "agent_slug": settings.agent_slug or "",
                "agent_id": agent_id or settings.agent_id or "",
                "strategy": "",
                # Trade-notification routing back to this run's owner
                "user_id": settings.user_id or 0,
                "chat_id": settings.chat_id or 0,
            },
            timeout=60,
        )

    if action == "stop":
        if not executor_id:
            return {"error": "stop requires executor_id"}
        return await call_control(
            "executor.stop",
            {"executor_id": executor_id, "keep_position": keep_position},
        )

    if action == "get":
        if not executor_id:
            return {"error": "get requires executor_id"}
        return await call_control("executor.get", {"executor_id": executor_id})

    if action == "list":
        return await call_control(
            "executor.list", {"agent_id": agent_id} if agent_id else {}
        )

    if action == "performance":
        params = {"group_by": group_by or "agent"}
        if agent_id:
            params["agent_id"] = agent_id
        return await call_control("executor.performance", params)

    return {"error": f"Unknown action: {action} (create|stop|get|list|performance)"}
