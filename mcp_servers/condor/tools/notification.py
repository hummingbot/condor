"""Send Telegram notifications to the user."""

import httpx

from mcp_servers.condor.settings import settings


async def send_notification(text: str, parse_mode: str = "Markdown") -> dict:
    """Send a Telegram message to the user.

    Returns:
        {"sent": true} on success, {"error": "..."} on failure.
    """
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
