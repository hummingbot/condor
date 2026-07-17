"""Memory tool — thin MCP wrapper over condor.memory.MemoryStore.

ONE memory tier (insight-flow-simplification §7/§8): the global chat-curated
store at repo-root ``store/memory/``. Agent-scoped sessions may read/search
it but writes are rejected — an agent's own durable knowledge goes to
``record_learning``. The audit ``source`` still derives from
``settings.agent_slug`` so provenance is never self-reported.
"""

from condor.memory import MemoryStore
from mcp_servers.condor.settings import settings


def _source() -> str:
    return f"agent:{settings.agent_slug}" if settings.agent_slug else "chat"


def _store() -> MemoryStore:
    # ONE memory tier: the global, chat-curated store. Agents read it (their
    # prompts inject its index) but never write — see the write gate below.
    return MemoryStore(None)


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

    if settings.agent_slug and action in ("write", "delete"):
        return {
            "error": "agents do not write user memory — record durable "
            "operational knowledge with record_learning instead"
        }

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


async def record_learning(text: str, replaces: str | None = None) -> dict:
    """Append or consolidate one learning in this agent's ``learnings.md``."""
    if not settings.agent_slug:
        return {
            "error": "record_learning is agent-scoped — no agent in this context"
        }
    from condor.agents.agent import agents_data_root
    from condor.agents.learnings import append_learning, read_learnings

    agent_dir = agents_data_root() / settings.agent_slug
    if not agent_dir.is_dir():
        return {"error": f"unknown agent directory: {agent_dir}"}
    text = " ".join(str(text or "").split())
    if not text:
        return {"error": "text is required"}
    try:
        append_learning(agent_dir, text, replaces=replaces or None)
    except ValueError as e:
        # Full list / bad replaces target — the agent must consolidate.
        return {"error": str(e)}
    return {
        "recorded": text,
        "replaced": bool(replaces),
        "total": len(read_learnings(agent_dir).splitlines()),
    }
