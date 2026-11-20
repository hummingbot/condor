"""Skill tool — thin MCP wrapper over condor.memory.SkillStore.

Skills are general to the assistant (playbooks shared by everyone using it), not
per-user. The library is selected by ``settings.specialist_slug`` (empty ->
chat condor) and is editable at runtime: read/search/list plus
create/edit/delete. Mirrors ``tools/memory.py``.
"""

from condor.memory import SkillStore
from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools._scope import ScopeError, UnknownAgent, resolve_target

# The actions that change a library. A bound specialist may not aim these at
# another agent's (MC-07); the read actions stay open across agents.
WRITE_ACTIONS = ("create", "edit", "delete", "write_file")


async def manage_skill(
    action: str,
    name: str | None = None,
    description: str | None = None,
    when_to_use: str | None = None,
    body: str | None = None,
    references_routine: str | None = None,
    query: str | None = None,
    max_entries: int = 30,
    agent: str | None = None,
    file: str | None = None,
    content: str | None = None,
    shared: bool | None = None,
) -> dict:
    # A skill library is keyed by agent, never by strategy — ``agent`` is the
    # only target selector. A bare agent slug is the canonical form; a composite
    # loop key "agent_slug.loop_slug" resolves to its *owning agent*. Without
    # one the current assistant is used — the launched agent, or the chat.
    target = agent
    try:
        resolved = resolve_target(target, write=action in WRITE_ACTIONS, what="skills")
    except UnknownAgent:
        # Errors rather than silently writing to the chat library.
        return {"error": f"No agent or loop found for '{target}'"}
    except ScopeError as exc:
        return {"error": str(exc)}
    # ``None`` is the chat's own library; a named target keeps its slug even
    # when that slug is Condor's.
    agent_slug = resolved if (target or settings.specialist_slug) else None
    store = SkillStore(agent_slug)
    source = f"agent:{agent_slug}" if agent_slug else "chat"

    if action == "create":
        if not name or not description or not when_to_use or not body:
            return {
                "error": "name, description, when_to_use and body are required for create"
            }
        return store.create(
            name,
            description,
            when_to_use,
            body,
            references_routine=references_routine,
            source=source,
            shared=shared,
        )

    elif action == "read":
        if not name:
            return {"error": "name is required for read"}
        skill = store.read(name)
        if skill is None:
            return {"error": f"Skill '{name}' not found"}
        return skill

    elif action == "read_file":
        if not name or not file:
            return {"error": "name and file are required for read_file"}
        return store.read_file(name, file)

    elif action == "write_file":
        if not name or not file:
            return {"error": "name and file are required for write_file"}
        if content is None:
            return {"error": "content is required for write_file"}
        return store.write_file(name, file, content)

    elif action == "search":
        if not query:
            return {"error": "query is required for search"}
        return {"results": store.search(query, limit=max_entries)}

    elif action == "list":
        return {"index": store.list_index()}

    elif action == "edit":
        if not name:
            return {"error": "name is required for edit"}
        fields = {}
        if description is not None:
            fields["description"] = description
        if when_to_use is not None:
            fields["when_to_use"] = when_to_use
        if body is not None:
            fields["body"] = body
        if references_routine is not None:
            fields["references_routine"] = references_routine
        if shared is not None:
            fields["shared"] = shared
        if not fields:
            return {"error": "provide at least one field to edit"}
        return store.edit(name, **fields)

    elif action == "delete":
        if not name:
            return {"error": "name is required for delete"}
        deleted = store.delete(name)
        if isinstance(deleted, dict):  # inherited (read-only) — carries the fix
            return deleted
        if not deleted:
            return {"error": f"Skill '{name}' not found"}
        return {"deleted": True, "name": name}

    return {"error": f"Unknown action: {action}"}
