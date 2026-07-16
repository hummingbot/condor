"""Notify the user (outbox chokepoint) + read the outbox.

The outbox has ONE serialized writer in the main process (§4.1): this
subprocess never appends the file itself — ``send_notification`` enqueues
over the control socket ("notify.emit"), and the main-process ``notify()``
handles the append (fsynced, stable id) plus the delivery mirror. Reads are
direct (read-only file access is fine).
"""

from mcp_servers.condor.settings import settings


async def send_notification(text: str, parse_mode: str = "Markdown") -> dict:
    """Notify the user; always recorded in the outbox.

    Returns:
        {"sent": true, "id": ...} on success, {"error": "..."} on failure.
    """
    from mcp_servers.condor.condor_client import call_control
    from mcp_servers.condor.exceptions import APIError

    try:
        out = await call_control(
            "notify.emit",
            {
                "text": text,
                "user_id": settings.user_id,
                "chat_id": settings.chat_id,
                "agent_id": settings.agent_id or settings.agent_slug or "",
                "kind": "agent",
                "origin": "mcp",
            },
        )
    except APIError as e:
        return {"error": f"notification enqueue failed: {e}"}
    entry = out.get("entry", {}) if isinstance(out, dict) else {}
    return {
        "sent": True,
        "id": entry.get("id", ""),
        "delivered_telegram": entry.get("delivered_telegram", False),
    }


async def get_notifications(
    limit: int = 30, since_ts: float | None = None, agent_id: str | None = None
) -> dict:
    """Read recent notifications from the outbox (oldest first)."""
    from condor.notifications import read_notifications

    return {"notifications": read_notifications(limit=limit, since_ts=since_ts,
                                                agent_id=agent_id)}
