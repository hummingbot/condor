"""Per-agent store path resolver.

Memory (FEAT-001) and skills (FEAT-002) used to be **per-user and shared**: the
``/agent`` chat and every trading agent of a user read/wrote the same store.
This module makes them **per-agent**: each agent gets its own store, co-located
with its definition, and nothing is shared across agents (FEAT-003).

The repo root IS the chat coordinator's agent-home (refactor-06): its brain is
the ``condor`` skill (``skills/condor/SKILL.md`` — the same file external
harnesses load as /condor), its skills the repo-root ``skills/``, its routines the repo-root
``routines/``, and its store the repo-root ``store/`` — mirroring
``agents/{slug}/{AGENT.md, skills/, routines/, store/}`` one-for-one. Two
stores never resolve to the same root: the chat's lives at the root and
trading agents' under ``agents/{slug}/``, so even an agent literally named
``condor`` cannot collide with the chat.

Memory is two tiers only (§4.3): the **global/local tier** (repo-root
``store/memory/``) and the **agent tier** (``agents/{slug}/store/memory/``).
The legacy per-user dimension (``store/user_{id}/``) is gone; the sole
key of a store is the agent slug (``None`` = the chat/global tier).

Pure filesystem logic with **no** MCP/transport deps, so it runs from the main
process (prompt injection) and from the MCP subprocess (the tools) alike.
"""

from __future__ import annotations

from pathlib import Path

# Anchor to the project root (…/condor) so paths are stable regardless of cwd.
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def store_root(agent_slug: str | None = None) -> Path:
    """Root of an agent's memory store.

    ``agent_slug`` set  -> trading agent: ``agents/{slug}/store/memory``
    ``agent_slug`` None  -> global tier:   ``store/memory`` (repo root)
    """
    if agent_slug:
        base = _PROJECT_ROOT / "agents" / agent_slug
    else:
        base = _PROJECT_ROOT
    return base / "store" / "memory"


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
    """The SHARED skills tier: the repo-root ``skills/`` library.

    A single shared library serves everyone: it is the chat's own library AND
    the shared tier every domain agent reads (resolution: local > shared).
    Host-visible — any harness opened in the repo (Claude Code, OpenClaw,
    Hermes) indexes it natively. Agents read it but cannot write it; shared
    edits happen from the chat, whose default library is this same directory.
    """
    return _PROJECT_ROOT / "skills"


def iter_stores() -> list[tuple[str, str | None, Path]]:
    """``(label, agent_slug, root)`` for each existing memory store.

    Used by ``/memory`` to show one section per agent. Scans the global tier
    (repo-root ``store/memory``) and ``agents/*/store/memory`` and returns
    only the stores that exist on disk (so empty agents don't clutter the
    view). ``agent_slug`` is ``None`` for the global tier and the slug for a
    trading agent, so a caller can rebuild the store via
    ``MemoryStore(agent_slug)``. The global tier is labelled ``condor (chat)``
    and listed first, then agents alphabetically.
    """
    found: list[tuple[str, str | None, Path]] = []

    chat_root = store_root(None)
    if chat_root.exists():
        found.append(("condor (chat)", None, chat_root))

    agents_dir = _PROJECT_ROOT / "agents"
    if agents_dir.exists():
        for d in sorted(agents_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("_") or d.name == "strategies":
                continue
            root = d / "store" / "memory"
            if root.exists():
                found.append((d.name, d.name, root))

    return found
