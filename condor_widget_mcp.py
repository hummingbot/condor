"""Condor Widget MCP Server -- provides widget tools to AI agents.

Communicates with the Widget Bridge inside the Condor bot via TCP.
Expects CONDOR_WIDGET_PORT and CONDOR_CHAT_ID environment variables.
"""

import asyncio
import json
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("condor-widgets")

WIDGET_PORT = int(os.environ.get("CONDOR_WIDGET_PORT", "0"))
CHAT_ID = int(os.environ.get("CONDOR_CHAT_ID", "0"))


async def _bridge_request(request: dict) -> dict:
    """Send a JSON request to the Widget Bridge and return the response."""
    reader, writer = await asyncio.open_connection("127.0.0.1", WIDGET_PORT)
    try:
        writer.write(json.dumps(request).encode())
        await writer.drain()
        writer.write_eof()
        data = await asyncio.wait_for(reader.read(65536), timeout=130)
        return json.loads(data.decode())
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@mcp.tool()
async def send_buttons(message: str, buttons: list[list[dict]]) -> dict:
    """Send a message with an inline keyboard to the Telegram chat.

    Args:
        message: The message text to display above the buttons.
        buttons: A list of rows, where each row is a list of button objects
                 with "label" (display text) and "value" (returned on click).
                 Example: [[{"label": "Option A", "value": "a"}, {"label": "Option B", "value": "b"}]]

    Returns:
        {"selected": "<value>"} when the user clicks a button, or {"timeout": true} after 120s.
    """
    return await _bridge_request({
        "method": "send_buttons",
        "chat_id": CHAT_ID,
        "message": message,
        "buttons": buttons,
    })


@mcp.tool()
async def ask_user_choice(question: str, options: list[str]) -> dict:
    """Ask the user to choose from a list of options using inline buttons.

    This is a convenience wrapper around send_buttons that creates a single-column layout.

    Args:
        question: The question to display.
        options: A list of option strings. Each becomes a button.

    Returns:
        {"selected": "<option text>"} when the user clicks, or {"timeout": true} after 120s.
    """
    buttons = [[{"label": opt, "value": opt}] for opt in options]
    return await _bridge_request({
        "method": "send_buttons",
        "chat_id": CHAT_ID,
        "message": question,
        "buttons": buttons,
    })


@mcp.tool()
async def send_notification(message: str) -> dict:
    """Send a one-way notification message to the Telegram chat (no buttons, no waiting).

    Args:
        message: The notification text to send.

    Returns:
        {"sent": true} on success.
    """
    return await _bridge_request({
        "method": "send_notification",
        "chat_id": CHAT_ID,
        "message": message,
    })


if __name__ == "__main__":
    mcp.run()
