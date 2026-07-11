"""Skill tool — thin MCP wrapper over condor.memory.SkillStore.

Skills are general to the assistant (playbooks shared by everyone using it), not
per-user. The library is selected by ``settings.agent_slug`` (empty -> chat
condor) and is editable at runtime: read/search/list plus create/edit/delete.
Mirrors ``tools/memory.py``.
"""

from condor.memory import SkillStore
from mcp_servers.condor.settings import settings


def _resolve_agent_slug(agent_slug: str | None) -> tuple[str | None, bool]:
    """Resolve which assistant's skill library an action targets.

    Mirrors routines' ``_get_agent_routines_dir``. An explicit ``agent_slug``
    lets the chat condor author/inspect a *specific* agent's local skills (the
    chat MCP has no ``agent_slug`` of its own). Without it the target is the
    current assistant — the launched agent (``settings.agent_slug``) or the
    chat condor (``None``).

    Returns ``(agent_slug, ok)``; ``ok`` is False only when an ``agent_slug``
    was given but matched no agent, so the caller errors instead of silently
    writing to the chat library.
    """
    if agent_slug:
        from condor.agents.agent import AgentStore

        if AgentStore().get(agent_slug):
            return agent_slug, True
        return None, False

    return (settings.agent_slug or None), True


async def manage_skill(
    action: str,
    name: str | None = None,
    description: str | None = None,
    when_to_use: str | None = None,
    body: str | None = None,
    references_routine: str | None = None,
    query: str | None = None,
    max_entries: int = 30,
    agent_slug: str | None = None,
    file: str | None = None,
    content: str | None = None,
) -> dict:
    target_slug, ok = _resolve_agent_slug(agent_slug)
    if agent_slug and not ok:
        return {"error": f"No agent found for agent_slug '{agent_slug}'"}
    store = SkillStore(target_slug)
    source = f"agent:{target_slug}" if target_slug else "chat"

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
        if not fields:
            return {"error": "provide at least one field to edit"}
        return store.edit(name, **fields)

    elif action == "delete":
        if not name:
            return {"error": "name is required for delete"}
        ok = store.delete(name)
        if not ok:
            return {"error": f"Skill '{name}' not found"}
        return {"deleted": True, "name": name}

    return {"error": f"Unknown action: {action}"}
