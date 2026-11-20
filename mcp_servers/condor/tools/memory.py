"""User memory tool — thin MCP wrapper over condor.memory.MemoryStore.

Resolves the store by ``settings.user_id`` (already injected into the MCP
process) and derives the audit ``source`` from ``settings.specialist_slug`` so
the LLM never has to report who is writing.
"""

from condor.memory import MemoryStore
from mcp_servers.condor.settings import settings

SEARCH_DEFAULT_LIMIT = 10
AUDIT_DEFAULT_LIMIT = 30


def _source() -> str:
    return f"agent:{settings.specialist_slug}" if settings.specialist_slug else "chat"


def _store() -> MemoryStore:
    # specialist_slug selects this assistant's store (FEAT-003); empty -> chat
    # condor.
    return MemoryStore(settings.user_id, settings.specialist_slug or None)


async def manage_memory(
    action: str,
    name: str | None = None,
    content: str | None = None,
    description: str | None = None,
    type: str = "fact",
    query: str | None = None,
    max_entries: int | None = None,
) -> dict:
    # Per-action defaults: a search page is short because its hits are ranked
    # and carry snippets (ST-12); the audit tail keeps its 30.
    search_limit = SEARCH_DEFAULT_LIMIT if max_entries is None else max_entries
    audit_limit = AUDIT_DEFAULT_LIMIT if max_entries is None else max_entries
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
        results, total = store.search_with_total(query, limit=search_limit)
        # ``total`` tells the caller whether narrowing the query is worth it;
        # a hit's full text is one ``read`` away.
        return {"results": results, "total": total}

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
        return {"entries": store.audit(limit=audit_limit)}

    return {"error": f"Unknown action: {action}"}
