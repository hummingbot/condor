"""Native executor tools — create/stop/list Condor-owned executors.

Executors run in the persistent Condor process (they must outlive this MCP
subprocess), so every action goes through the unix control socket.

Creation authority (§6.2): an agent-run session carries an opaque run
capability injected at spawn (``settings.capability``); a chat session
registers as **condor-direct** on first create by exchanging the 0600
``store/.direct-token`` over a persistent control connection — the
capability dies with that connection. Absence of both is a rejection.
"""

import logging
import uuid

from mcp_servers.condor.condor_client import call_control
from mcp_servers.condor.settings import settings

log = logging.getLogger(__name__)

# type = {kind}_{instrument}: kind ∈ {order, position}, instrument ∈ {spot, perp, pred}.
_EXECUTOR_TYPES = {
    "order_spot", "order_perp", "order_pred",
    "position_spot", "position_perp", "position_pred",
}

# Lazy condor-direct state (chat sessions only): the persistent connection
# and the capability id it carries. Module-global — one per MCP process.
_direct_conn = None
_direct_capability: str = ""


async def _resolve_capability() -> str:
    """The capability this session presents on creates.

    Agent-run session → the injected run capability (an agent session must
    never downgrade to condor-direct — the main process also rejects it).
    Chat session → register condor-direct once, cache it.
    """
    global _direct_conn, _direct_capability
    if settings.capability:
        return settings.capability
    if _direct_capability and _direct_conn is not None and _direct_conn.connected:
        return _direct_capability

    from condor.control import CONTROL_SOCKET_PATH
    from condor.control.client import PersistentControlConnection
    from condor.executors.capabilities import DIRECT_TOKEN_PATH

    import os

    socket_path = os.environ.get("CONDOR_CONTROL_SOCKET") or CONTROL_SOCKET_PATH
    try:
        token = DIRECT_TOKEN_PATH.read_text().strip()
    except FileNotFoundError:
        raise RuntimeError(
            "condor-direct token not found — is the main Condor process "
            "running? (it writes store/.direct-token at startup)"
        )
    conn = PersistentControlConnection(socket_path)
    await conn.connect()
    result = await conn.call("direct.register", {"token": token})
    _direct_conn = conn
    _direct_capability = result["capability"]
    log.info("registered condor-direct capability for this chat session")
    return _direct_capability


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
        try:
            cap = await _resolve_capability()
        except Exception as e:
            return {"error": f"no creation capability: {e}"}
        # Client-generated create identity (§6.2): retrying the SAME create
        # after an ambiguous timeout with the same executor_id replays the
        # original result instead of double-trading. Pass executor_id to
        # retry; omit for a fresh trade.
        eid = executor_id or f"{executor_type}_{uuid.uuid4().hex}"
        return await call_control(
            "executor.create",
            {
                "type": executor_type,
                "config": config,
                # Authority + attribution come from the server-side capability
                # entry (§6.2) — never from caller-supplied fields.
                "capability": cap,
                "executor_id": eid,
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
