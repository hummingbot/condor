"""Send Telegram notifications to the user (+ the notifications outbox)."""

import time

import httpx

from mcp_servers.condor.settings import settings


def _append_outbox(text: str, delivered: bool) -> None:
    """Record in the outbox — same file condor.notifications appends to;
    this subprocess shares the filesystem, so append directly (fail-soft)."""
    try:
        from condor.notifications import _append_outbox as append

        append({
            "ts": time.time(),
            "user_id": settings.user_id,
            "chat_id": settings.chat_id,
            "agent_id": settings.agent_id or settings.agent_slug or "",
            "kind": "agent",
            "origin": "mcp",
            "text": text,
            "delivered_telegram": delivered,
        })
    except Exception:
        pass


async def send_notification(text: str, parse_mode: str = "Markdown") -> dict:
    """Send a Telegram message to the user; always recorded in the outbox.

    Returns:
        {"sent": true} on success, {"error": "..."} on failure.
    """
    if not settings.bot_token:
        _append_outbox(text, delivered=False)
        return {"error": "TELEGRAM_BOT_TOKEN not configured (recorded in outbox)"}
    if not settings.chat_id:
        _append_outbox(text, delivered=False)
        return {"error": "CONDOR_CHAT_ID not configured (recorded in outbox)"}

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
                _append_outbox(text, delivered=True)
                return {"sent": True}
            # Retry without parse_mode if formatting fails
            if "can't parse" in data.get("description", "").lower():
                payload.pop("parse_mode")
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("ok"):
                    _append_outbox(text, delivered=True)
                    return {"sent": True}
            _append_outbox(text, delivered=False)
            return {"error": data.get("description", "Unknown Telegram API error")}
    except Exception as e:
        _append_outbox(text, delivered=False)
        return {"error": f"Failed to send: {e}"}


async def get_notifications(
    limit: int = 30, since_ts: float | None = None, agent_id: str | None = None
) -> dict:
    """Read recent notifications from the outbox (oldest first)."""
    from condor.notifications import read_notifications

    return {"notifications": read_notifications(limit=limit, since_ts=since_ts,
                                                agent_id=agent_id)}
