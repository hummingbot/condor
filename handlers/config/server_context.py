"""
Unified server context utilities for config handlers.

Provides consistent server information display across all config modules.
"""

import logging
from typing import Optional, Dict, Any, Tuple
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)


async def get_server_context_header(chat_id: int = None) -> Tuple[str, bool]:
    """
    Get a standardized server context header showing current server and status.

    Args:
        chat_id: Optional chat ID to get per-chat server. If None, uses global default.

    Returns:
        Tuple of (header_text: str, is_online: bool)
        header_text: Formatted markdown text with server info and status
        is_online: True if server is online, False otherwise
    """
    try:
        from servers import server_manager

        # Get default server (per-chat if chat_id provided)
        if chat_id is not None:
            default_server = server_manager.get_default_server_for_chat(chat_id)
        else:
            default_server = server_manager.get_default_server()
        servers = server_manager.list_servers()

        if not servers:
            return "⚠️ _No servers configured_\n", False

        if not default_server:
            return "⚠️ _No default server set_\n", False

        # Get server config
        server_config = server_manager.get_server(default_server)
        if not server_config:
            return "⚠️ _Server configuration not found_\n", False

        # Check server status
        status_result = await server_manager.check_server_status(default_server)
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


async def get_gateway_status_info(chat_id: int = None) -> Tuple[str, bool]:
    """
    Get gateway status information for the current server.

    Args:
        chat_id: Optional chat ID to get per-chat server. If None, uses global default.

    Returns:
        Tuple of (status_text: str, is_running: bool)
        status_text: Formatted markdown text with gateway status
        is_running: True if gateway is running, False otherwise
    """
    try:
        from servers import server_manager

        if chat_id is not None:
            client = await server_manager.get_client_for_chat(chat_id)
        else:
            client = await server_manager.get_default_client()

        # Check gateway status
        try:
            status_response = await client.gateway.get_status()
            # The API returns "running": true/false, not "status": "running"
            is_running = status_response.get('running', False)

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
    chat_id: int = None
) -> Tuple[str, bool, bool]:
    """
    Build a standardized header for configuration messages.

    Args:
        title: The title/heading for this config screen (will be bolded automatically)
        include_gateway: Whether to include gateway status info
        chat_id: Optional chat ID to get per-chat server. If None, uses global default.

    Returns:
        Tuple of (header_text: str, server_online: bool, gateway_running: bool)
    """
    # Escape and bold the title
    title_escaped = escape_markdown_v2(title)
    header = f"*{title_escaped}*\n\n"

    # Add server context
    server_context, server_online = await get_server_context_header(chat_id)
    header += server_context

    # Add gateway status if requested
    gateway_running = False
    if include_gateway:
        gateway_info, gateway_running = await get_gateway_status_info(chat_id)
        header += gateway_info

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
