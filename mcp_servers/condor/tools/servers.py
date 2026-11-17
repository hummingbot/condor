"""Server list and status tools.

``list`` also answers "who am I and where am I pointed" — the role/admin flags
and the active LLM identity that used to live in a separate ``get_user_context``
tool (FEAT-067). One tool, one question.
"""

from mcp_servers.condor.settings import settings


def _bound_server(cm) -> tuple[str | None, str | None]:
    """``(the server this session's tools act on, the chat's default)``.

    Not the same thing (MC-12). The binding is resolved once at spawn — an
    agent's pin, then an override, then the chat's preference — and frozen for
    the session, and it is what ``run_code``, routines, asks and controller
    syncs send. The chat default can name another account entirely: the agent
    is pinned elsewhere, or the user changed it after this session started.
    Reporting the default as "active" told the model it was on account A while
    its tools traded on account B.

    A session spawned with no binding (an external MCP host) has only the chat
    default to go on, which is what this answered before.
    """
    chat_default = cm.get_chat_default_server(settings.chat_id)
    return (settings.active_server or chat_default), chat_default


def list_servers() -> dict:
    from config_manager import get_config_manager

    cm = get_config_manager()
    accessible = cm.get_accessible_servers(settings.user_id)
    active_server, chat_default = _bound_server(cm)
    servers = []
    for name in accessible:
        server = cm.get_server(name)
        if not server:
            continue
        perm = cm.get_server_permission(settings.user_id, name)
        servers.append(
            {
                "name": name,
                "host": server["host"],
                "port": server["port"],
                "permission": perm.value if perm else "unknown",
                "is_active": name == active_server,
            }
        )
    user_role = cm.get_user_role(settings.user_id)
    result = {
        "servers": servers,
        "active_server": active_server,
        "user_role": user_role.value if user_role else None,
        "is_admin": cm.is_admin(settings.user_id),
    }
    if chat_default and chat_default != active_server:
        # Only when it differs, so the common case stays one unambiguous name.
        result["chat_default_server"] = chat_default

    # Which model the user is on, and which custom endpoints they've saved.
    # New Agents inherit active_agent_key by default, so the coordinator should
    # never invent one — an invented key names a backend that may not exist.
    try:
        from condor.preferences import (
            get_active_agent_key,
            get_custom_providers,
            load_user_data_for,
        )

        result["active_agent_key"] = get_active_agent_key(settings.user_id)
        result["custom_llm_endpoints"] = [
            p["name"]
            for p in get_custom_providers(load_user_data_for(settings.user_id))
        ]
    except Exception:
        result["active_agent_key"] = None
        result["custom_llm_endpoints"] = []

    return result


async def check_status(name: str | None) -> dict:
    from config_manager import get_config_manager

    cm = get_config_manager()
    if not name:
        name, _ = _bound_server(cm)
        if not name:
            return {"error": "No active server"}
    if not cm.has_server_access(settings.user_id, name):
        return {"error": f"No access to server '{name}'"}
    status = await cm.check_server_status(name)
    return {"server": name, **status}


async def manage_servers(action: str, name: str | None = None) -> dict:
    if action == "list":
        return list_servers()
    if action == "status":
        return await check_status(name)
    return {"error": f"Unknown action: {action}"}
