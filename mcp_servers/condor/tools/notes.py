"""Persistent key-value notes storage."""

import json
from pathlib import Path

from mcp_servers.condor.settings import settings


def _notes_file() -> Path:
    return Path("data") / "notes" / f"chat_{settings.chat_id}.json"


def _load() -> dict:
    f = _notes_file()
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            return {}
    return {}


def _save(notes: dict) -> None:
    f = _notes_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(notes, indent=2))


async def manage_notes(action: str, key: str | None = None, value: str | None = None) -> dict:
    if action == "list":
        return {"notes": _load()}

    elif action == "get":
        if not key:
            return {"error": "key is required"}
        notes = _load()
        v = notes.get(key)
        if v is None:
            return {"error": f"Note '{key}' not found"}
        return {"key": key, "value": v}

    elif action == "set":
        if not key or value is None:
            return {"error": "key and value are required"}
        notes = _load()
        notes[key] = str(value)
        _save(notes)
        return {"saved": True, "key": key, "value": str(value)}

    elif action == "delete":
        if not key:
            return {"error": "key is required"}
        notes = _load()
        if key not in notes:
            return {"error": f"Note '{key}' not found"}
        del notes[key]
        _save(notes)
        return {"deleted": True, "key": key}

    return {"error": f"Unknown action: {action}"}
