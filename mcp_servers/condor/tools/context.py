"""Get current user context."""

from mcp_servers.condor.settings import settings


async def get_user_context() -> dict:
    from config_manager import get_config_manager

    cm = get_config_manager()
    active_server = cm.get_chat_default_server(settings.chat_id)
    user_role = cm.get_user_role(settings.user_id)
    is_admin = cm.is_admin(settings.user_id)

    context = {
        "active_server": active_server,
        "user_role": user_role.value if user_role else None,
        "is_admin": is_admin,
    }

    # Which model the user is on, and which custom endpoints they've saved.
    # New Agents inherit active_agent_key by default, so the coordinator should
    # never invent one — an invented key names a backend that may not exist.
    try:
        from condor.preferences import get_active_agent_key, get_custom_providers, load_user_data_for

        context["active_agent_key"] = get_active_agent_key(settings.user_id)
        context["custom_llm_endpoints"] = [
            p["name"] for p in get_custom_providers(load_user_data_for(settings.user_id))
        ]
    except Exception:
        context["active_agent_key"] = None
        context["custom_llm_endpoints"] = []

    return context
