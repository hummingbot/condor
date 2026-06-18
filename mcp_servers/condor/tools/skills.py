"""Skill tool — thin MCP wrapper over condor.memory.SkillStore.

Skills are general to the assistant (playbooks shared by everyone using it), not
per-user. The library is selected by ``settings.agent_slug`` (empty -> chat
condor) and is editable at runtime: read/search/list plus create/edit/delete.
Mirrors ``tools/memory.py``.
"""

from condor.memory import SkillStore
from mcp_servers.condor.settings import settings


def _store() -> SkillStore:
    # agent_slug selects this assistant's skill library (FEAT-003); empty -> chat.
    return SkillStore(settings.agent_slug or None)


def _source() -> str:
    return f"agent:{settings.agent_slug}" if settings.agent_slug else "chat"


async def manage_skill(
    action: str,
    name: str | None = None,
    description: str | None = None,
    when_to_use: str | None = None,
    body: str | None = None,
    references_routine: str | None = None,
    query: str | None = None,
    max_entries: int = 30,
) -> dict:
    store = _store()

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
            source=_source(),
        )

    elif action == "read":
        if not name:
            return {"error": "name is required for read"}
        skill = store.read(name)
        if skill is None:
            return {"error": f"Skill '{name}' not found"}
        return skill

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
