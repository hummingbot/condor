"""Announce something to the user.

Goes through the main process (``POST /agents/notify``) like every other tool
with a user-visible side effect, so the notification is also noted in the
conversation that produced it (ARCH-088). Telegram-over-HTTP stays as the
fallback for when the main process is unreachable: a notification must never be
lost because the web app is down.
"""

import httpx

from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.settings import settings


async def send_notification(text: str, parse_mode: str = "Markdown") -> dict:
    """Send a message to the user.

    Returns:
        {"sent": true} on success, {"error": "..."} on failure.
    """
    try:
        result = await call_main_api(
            "POST",
            "/agents/notify",
            {
                "text": text,
                "parse_mode": parse_mode,
                "chat_id": settings.chat_id,
                "user_id": settings.user_id,
                # Provenance: the route resolves this to the conversation the
                # announcement came from, so the chat keeps a trace of it.
                "session_key": settings.session_key,
            },
        )
    except Exception:
        result = None

    # "recorded" counts as delivered: a web session's user reads the note in the
    # conversation they are looking at, and pushing it to Telegram too is not
    # what makes it arrive. Anything else falls through to the direct path --
    # the main process may be down, or have no way to reach the chat.
    if isinstance(result, dict) and (result.get("sent") or result.get("recorded")):
        return {"sent": True}

    return await _send_direct(text, parse_mode)


async def _send_direct(text: str, parse_mode: str) -> dict:
    """Last-resort Telegram push, straight from this subprocess."""
    if not settings.bot_token:
        return {"error": "TELEGRAM_BOT_TOKEN not configured"}
    if not settings.chat_id:
        return {"error": "CONDOR_CHAT_ID not configured"}

    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    payload = {
        "chat_id": settings.chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
            if data.get("ok"):
                return {"sent": True}
            # Retry without parse_mode if formatting fails
            if "can't parse" in data.get("description", "").lower():
                payload.pop("parse_mode")
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("ok"):
                    return {"sent": True}
            return {"error": data.get("description", "Unknown Telegram API error")}
    except Exception as e:
        return {"error": f"Failed to send: {e}"}
