"""
Unified server context utilities for config handlers.

Provides consistent server information display across all config modules.
"""

import logging
from typing import Tuple

from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)


async def get_server_context_header(user_data: dict = None) -> Tuple[str, bool]:
    """
    Get a standardized server context header showing current server and status.

    Args:
        user_data: Optional user_data dict to get user's preferred server

    Returns:
        Tuple of (header_text: str, is_online: bool)
    """
    try:
        from config_manager import get_config_manager

        # Get user's preferred server
        default_server = None
        if user_data:
            from handlers.config.user_preferences import get_active_server

            default_server = get_active_server(user_data)
        if not default_server:
            default_server = get_config_manager().get_default_server()
        servers = get_config_manager().list_servers()

        if not servers:
            return "⚠️ _No servers configured_\n", False

        if not default_server:
            return "⚠️ _No default server set_\n", False

        # Get server config
        server_config = get_config_manager().get_server(default_server)
        if not server_config:
            return "⚠️ _Server configuration not found_\n", False

        # Check server status
        status_result = await get_config_manager().check_server_status(default_server)
        status = status_result.get("status", "unknown")

        # Format status with icon
        if status == "online":
            status_icon = "🟢"
            status_text = "Online"
            is_online = True
        elif status == "auth_error":
            status_icon = "🔴"
            status_text = "Auth Error"
            is_online = False
        elif status == "offline":
            status_icon = "🔴"
            status_text = "Offline"
            is_online = False
        else:
            status_icon = "🟡"
            status_text = "Unknown"
            is_online = False

        # Escape for markdown
        server_escaped = escape_markdown_v2(default_server)
        status_escaped = escape_markdown_v2(status_text)

        header = f"*Server:* `{server_escaped}` {status_icon} {status_escaped}\n"

        return header, is_online

    except Exception as e:
        logger.error(f"Error getting server context: {e}", exc_info=True)
        return f"⚠️ _Error loading server info: {escape_markdown_v2(str(e))}_\n", False


async def get_gateway_status_info(
    chat_id: int = None, user_data: dict = None
) -> Tuple[str, bool]:
    """
    Get gateway status information for the current server.

    Args:
        chat_id: Optional chat ID for getting the API client
        user_data: Optional user_data dict to get user's preferred server

    Returns:
        Tuple of (gateway_info: str, is_running: bool)
    """
    try:
        from config_manager import get_config_manager

        preferred = None
        if user_data:
            from handlers.config.user_preferences import get_active_server

            preferred = get_active_server(user_data)
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=preferred
        )

        # Check gateway status
        try:
            status_response = await client.gateway.get_status()
            # The API returns "running": true/false, not "status": "running"
            is_running = status_response.get("running", False)

            if is_running:
                status_icon = "🟢"
                status_text = "Running"
            else:
                status_icon = "🔴"
                status_text = "Not Running"

        except Exception as e:
            logger.warning(f"Failed to get gateway status: {e}")
            status_icon = "⚪️"
            status_text = "Unknown"
            is_running = False

        status_escaped = escape_markdown_v2(status_text)
        gateway_info = f"*Gateway:* {status_icon} {status_escaped}\n"

        return gateway_info, is_running

    except Exception as e:
        logger.error(f"Error getting gateway status: {e}", exc_info=True)
        return f"*Gateway:* ⚠️ {escape_markdown_v2('Error')}\n", False


async def build_config_message_header(
    title: str,
    include_gateway: bool = False,
    chat_id: int = None,
    user_data: dict = None,
) -> Tuple[str, bool, bool]:
    """Build a standardized header for configuration messages."""
    title_escaped = escape_markdown_v2(title)
    header = f"*{title_escaped}*\n\n"

    server_context, server_online = await get_server_context_header(user_data)
    header += server_context

    gateway_running = False
    if include_gateway and server_online:
        gateway_info, gateway_running = await get_gateway_status_info(
            chat_id, user_data
        )
        header += gateway_info
    elif include_gateway:
        header += f"*Gateway:* ⚪️ {escape_markdown_v2('N/A')}\n"

    header += "\n"
    return header, server_online, gateway_running


def format_server_selection_needed() -> str:
    """
    Format a message indicating that server configuration is needed.

    Returns:
        Formatted markdown message
    """
    return (
        "⚠️ *Configuration Required*\n\n"
        "No API servers are configured\\.\n\n"
        "_Please configure a server in the API Servers section first\\._"
    )
