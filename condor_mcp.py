"""Condor MCP Server -- exposes Condor widget bridge tools to the AI agent.

Communicates with the WidgetBridge via TCP (localhost:CONDOR_WIDGET_PORT).
Environment variables (set by the agent session):
  CONDOR_WIDGET_PORT  -- TCP port of the widget bridge
  CONDOR_CHAT_ID      -- Telegram chat ID for this session
  CONDOR_USER_ID      -- Telegram user ID for this session
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

# --- Config from env ---
WIDGET_PORT = int(os.environ.get("CONDOR_WIDGET_PORT", "0"))
CHAT_ID = int(os.environ.get("CONDOR_CHAT_ID", "0"))
USER_ID = int(os.environ.get("CONDOR_USER_ID", "0"))

mcp = FastMCP("condor")


async def _call_bridge(method: str, params: dict[str, Any] | None = None) -> dict:
    """Send a request to the widget bridge and return the response."""
    request = {
        "method": method,
        "chat_id": CHAT_ID,
        "user_id": USER_ID,
    }
    if params:
        request["params"] = params

    reader, writer = await asyncio.open_connection("127.0.0.1", WIDGET_PORT)
    try:
        writer.write(json.dumps(request).encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(1_048_576), timeout=60)
        return json.loads(data.decode())
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@mcp.tool()
async def send_buttons(message: str, buttons: list[list[dict]]) -> dict:
    """Send a message to the user with inline buttons and wait for their selection.

    Args:
        message: Text to display above the buttons.
        buttons: 2D list of button rows. Each button is {"label": str, "value": str}.
                 Example: [[{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}]]

    Returns:
        {"selected": str} with the value of the button the user clicked,
        or {"error": str} on timeout/failure.
    """
    return await _call_bridge("send_buttons", {"message": message, "buttons": buttons})


@mcp.tool()
async def send_notification(message: str) -> dict:
    """Send a plain-text notification message to the user in Telegram.

    Use this to proactively inform the user of something without waiting for a reply.

    Args:
        message: Text to send.

    Returns:
        {"sent": True} on success.
    """
    return await _call_bridge("send_notification", {"message": message})


@mcp.tool()
async def manage_routines(action: str, **kwargs: Any) -> dict:
    """Manage Condor routines (scripts that run against the Hummingbot API).

    Actions:
        list          -- List all available routines.
        describe      -- Get details and input fields for a routine. Requires: name.
        run           -- Run a routine once. Requires: name, fields (dict of input values).
        schedule      -- Schedule a routine to run periodically. Requires: name, fields, interval_seconds.
        list_active   -- List currently running/scheduled routine instances.
        stop          -- Stop a running routine instance. Requires: instance_id.

    Returns a dict with results depending on the action.
    """
    params = {"action": action, **kwargs}
    return await _call_bridge("manage_routines", params)


@mcp.tool()
async def manage_servers(action: str, **kwargs: Any) -> dict:
    """Manage Hummingbot API server connections.

    Actions:
        list          -- List all servers the user has access to.
        get_active    -- Get the currently active server for this chat.
        set_active    -- Switch active server. Requires: server_name.

    Returns a dict with server info.
    """
    params = {"action": action, **kwargs}
    return await _call_bridge("manage_servers", params)


@mcp.tool()
async def get_user_context() -> dict:
    """Get current user context: active server, accessible servers, and permissions.

    Returns a dict with user_id, chat_id, active_server, and server list with permissions.
    """
    return await _call_bridge("get_user_context")


if __name__ == "__main__":
    mcp.run()
