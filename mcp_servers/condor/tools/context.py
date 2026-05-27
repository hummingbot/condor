"""Get current user context."""

from mcp_servers.condor.settings import settings


async def get_user_context() -> dict:
    from config_manager import get_config_manager

    cm = get_config_manager()
    active_server = cm.get_chat_default_server(settings.chat_id)
    user_role = cm.get_user_role(settings.user_id)
    is_admin = cm.is_admin(settings.user_id)

    return {
        "active_server": active_server,
        "user_role": user_role.value if user_role else None,
        "is_admin": is_admin,
    }
