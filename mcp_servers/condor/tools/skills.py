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
    body: str | None = None,
    references_routine: str | None = None,
    query: str | None = None,
    max_entries: int = 30,
    agent_slug: str | None = None,
    scope: str | None = None,
    file: str | None = None,
    content: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    changelog: str | None = None,
) -> dict:
    if scope == "shared":
        # The shared tier is chat-managed: a launched agent must never hold
        # the pen on knowledge that feeds every other agent.
        if settings.agent_slug:
            return {
                "error": "the shared skills tier is read-only for agents — it "
                "is managed from the chat (your shared skills already appear "
                "in your index; create a local skill to override one)"
            }
        if agent_slug:
            return {"error": "pass either scope='shared' or agent_slug, not both"}
        store = SkillStore(scope="shared")
        source = "shared"
    elif scope:
        return {"error": f"unknown scope '{scope}' (only 'shared')"}
    else:
        target_slug, ok = _resolve_agent_slug(agent_slug)
        if agent_slug and not ok:
            return {"error": f"No agent found for agent_slug '{agent_slug}'"}
        store = SkillStore(target_slug)
        source = f"agent:{target_slug}" if target_slug else "chat"

    if action == "create":
        if not name or not description or not body:
            return {
                "error": "name, description and body are required for create "
                "(description must state WHAT the skill does and WHEN to use it)"
            }
        return store.create(
            name,
            description,
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

    elif action == "patch":
        if not name:
            return {"error": "name is required for patch"}
        updated_by = f"agent:{settings.agent_slug}" if settings.agent_slug else source
        return store.patch(
            name,
            old_string or "",
            new_string if new_string is not None else "",
            changelog or "",
            updated_by=updated_by,
        )

    elif action == "edit":
        if not name:
            return {"error": "name is required for edit"}
        fields = {}
        if description is not None:
            fields["description"] = description
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
        return store.delete(name)

    return {"error": f"Unknown action: {action}"}
