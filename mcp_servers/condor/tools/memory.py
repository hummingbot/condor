"""User memory tool — thin MCP wrapper over condor.memory.MemoryStore.

Resolves the store by ``settings.user_id`` (already injected into the MCP
process) and derives the audit ``source`` from ``settings.agent_slug`` so the
LLM never has to report who is writing.
"""

from condor.memory import MemoryStore
from mcp_servers.condor.settings import settings


def _source() -> str:
    return f"agent:{settings.agent_slug}" if settings.agent_slug else "chat"


def _store() -> MemoryStore:
    return MemoryStore(settings.user_id)


async def manage_memory(
    action: str,
    name: str | None = None,
    content: str | None = None,
    description: str | None = None,
    type: str = "fact",
    query: str | None = None,
    max_entries: int = 30,
) -> dict:
    store = _store()

    if action == "write":
        if not name or not content or not description:
            return {"error": "name, content and description are required for write"}
        return store.write(name, content, description, type=type, source=_source())

    elif action == "read":
        if not name:
            return {"error": "name is required for read"}
        body = store.read(name)
        if body is None:
            return {"error": f"Memory '{name}' not found"}
        return {"name": name, "content": body}

    elif action == "search":
        if not query:
            return {"error": "query is required for search"}
        return {"results": store.search(query, limit=max_entries)}

    elif action == "list":
        return {"index": store.list_index()}

    elif action == "delete":
        if not name:
            return {"error": "name is required for delete"}
        ok = store.delete(name, source=_source())
        if not ok:
            return {"error": f"Memory '{name}' not found"}
        return {"deleted": True, "name": name}

    elif action == "audit":
        return {"entries": store.audit(limit=max_entries)}

    return {"error": f"Unknown action: {action}"}
