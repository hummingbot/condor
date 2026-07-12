"""Per-agent store path resolver.

Memory (FEAT-001) and skills (FEAT-002) used to be **per-user and shared**: the
``/agent`` chat and every trading agent of a user read/wrote the same store.
This module makes them **per-agent**: each agent gets its own store, co-located
with its definition, and nothing is shared across agents (FEAT-003).

The repo root IS the chat coordinator's agent-home (refactor-06): its brain is
``CONDOR.md``, its skills the repo-root ``skills/``, its routines the repo-root
``routines/``, and its store the repo-root ``store/`` — mirroring
``agents/{slug}/{AGENT.md, skills/, routines/, store/}`` one-for-one. Two
stores never resolve to the same root: the chat's lives at the root and
trading agents' under ``agents/{slug}/``, so even an agent literally named
``condor`` cannot collide with the chat.

The key of a store is ``(agent, user_id)`` — "per-agent" *composes with*
``user_id``, it does not replace it (group chats share a chat but each user
keeps their own memory).

Pure filesystem logic with **no** MCP/Telegram deps, so it runs from the main
process (prompt injection) and from the MCP subprocess (the tools) alike.
"""

from __future__ import annotations

from pathlib import Path

# Anchor to the project root (…/condor) so paths are stable regardless of cwd.
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def store_root(user_id: int, agent_slug: str | None = None) -> Path:
    """Root of an agent's per-user store.

    ``agent_slug`` set  -> trading agent: ``agents/{slug}/store/user_{id}``
    ``agent_slug`` None  -> chat condor:   ``store/user_{id}`` (repo root)
    """
    if agent_slug:
        base = _PROJECT_ROOT / "agents" / agent_slug
    else:
        base = _PROJECT_ROOT
    return base / "store" / f"user_{user_id}"


def builtin_skills_root(agent_slug: str | None = None) -> Path | None:
    """Skills library root for an assistant (FEAT-004, refactor-05 Phase 1).

    One ``skills/<name>/SKILL.md`` (agentskills.io format) per playbook:

    - chat ``condor`` (``agent_slug`` None) → the repo-root ``skills/`` — the
      HOST-FACING library. This single directory serves every consumer at
      once: Condor's own chat, Claude Code (via ``.claude/skills`` symlinks),
      OpenClaw (default ``<workspace>/skills`` scan), and Hermes (tap
      layout). Anything here is visible to any host opened in the repo.
    - a trading agent / domain expert (``agent_slug`` set) →
      ``agents/<slug>/skills/`` — AGENT-INTERNAL, consumed only by Condor's
      own runs behind the MCP boundary, never surfaced to host indexes.

    Merged into the agent's [SKILLS]/[DOMAIN SKILLS] index alongside its learned
    skills. The library is editable at runtime: ``SkillStore`` create/edit/delete
    act on this same dir, so repo-shipped playbooks can be refined like any other
    (edits are version-controlled). See :mod:`condor.memory.skills`.
    """
    if agent_slug:
        return _PROJECT_ROOT / "agents" / agent_slug / "skills"
    return _PROJECT_ROOT / "skills"


def shared_skills_root() -> Path:
    """The SHARED skills tier: ``agents/_shared/skills/`` (refactor-05 Phase 2).

    Read by every domain agent (resolution: local > shared), writable only
    from the chat via ``manage_skill(scope="shared")`` — agents get a loud
    error on writes. The ``_``-prefixed dir keeps it out of AgentStore
    discovery and host skill indexes alike.
    """
    return _PROJECT_ROOT / "agents" / "_shared" / "skills"


def iter_user_stores(user_id: int) -> list[tuple[str, str | None, Path]]:
    """``(label, agent_slug, root)`` for each existing store of ``user_id``.

    Used by ``/memory`` to show one section per agent. Scans the root
    ``store/user_{id}`` (the chat's) and ``agents/*/store/user_{id}`` and
    returns only the stores that exist on disk (so empty agents don't clutter
    the view). ``agent_slug`` is ``None`` for the chat and the slug for a trading
    agent, so a caller can rebuild the store via ``MemoryStore(user_id, agent_slug)``.
    The chat is labelled ``condor (chat)`` and listed first, then agents alphabetically.
    """
    found: list[tuple[str, str | None, Path]] = []

    chat_root = _PROJECT_ROOT / "store" / f"user_{user_id}"
    if chat_root.exists():
        found.append(("condor (chat)", None, chat_root))

    agents_dir = _PROJECT_ROOT / "agents"
    if agents_dir.exists():
        for d in sorted(agents_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("_") or d.name == "strategies":
                continue
            root = d / "store" / f"user_{user_id}"
            if root.exists():
                found.append((d.name, d.name, root))

    return found
