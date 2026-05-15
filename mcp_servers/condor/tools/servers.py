"""Server list and status tools."""

from mcp_servers.condor.settings import settings


def list_servers() -> dict:
    from config_manager import get_config_manager

    cm = get_config_manager()
    accessible = cm.get_accessible_servers(settings.user_id)
    active_server = cm.get_chat_default_server(settings.chat_id)
    servers = []
    for name in accessible:
        server = cm.get_server(name)
        if not server:
            continue
        perm = cm.get_server_permission(settings.user_id, name)
        servers.append({
            "name": name,
            "host": server["host"],
            "port": server["port"],
            "permission": perm.value if perm else "unknown",
            "is_active": name == active_server,
        })
    return {"servers": servers, "active_server": active_server}


async def check_status(name: str | None) -> dict:
    from config_manager import get_config_manager

    cm = get_config_manager()
    if not name:
        name = cm.get_chat_default_server(settings.chat_id)
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
