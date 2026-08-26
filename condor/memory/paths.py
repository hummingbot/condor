"""Per-agent store path resolver.

Memory (FEAT-001) and skills (FEAT-002) used to be **per-user and shared**: the
``/agent`` chat and every trading agent of a user read/wrote the same store. This
module makes them **per-agent**: each agent gets its own store, co-located with
its definition, and nothing is shared across agents (FEAT-003).

The key of a store is ``(agent, user_id)`` — "per-agent" *composes with*
``user_id``, it does not replace it (group chats share a chat but each user keeps
their own memory).

There is a single registry: every agent, Condor included, lives under
``agents/<slug>/`` (FEAT-033). ``agent_slug`` is optional at the boundary
because "no agent bound" *means* Condor — a falsy slug resolves
:data:`CHAT_SLUG`, so every existing ``SkillStore(None)`` / ``MemoryStore(uid,
None)`` caller keeps landing where its data lives.

Every path here hangs off :func:`condor.paths.agents_root`, the third root that
module names — a *call*, never a module constant, so ``$CONDOR_AGENTS_ROOT`` is
observable after import. That is what lets one autouse fixture keep the whole
suite out of the developer's live memory and skill library; it used to take a
per-module monkeypatch of a private name, which ten modules remembered and any
eleventh could forget (CORR-220).

Pure filesystem logic with **no** MCP/Telegram deps, so it runs from the main
process (prompt injection) and from the MCP subprocess (the tools) alike.
"""

from __future__ import annotations

from pathlib import Path

from condor.paths import agents_root

# The default agent: the one answering when no specialist is bound. It is a
# normal agent directory like any other — what makes it default is that a falsy
# slug resolves to it (FEAT-033).
CHAT_SLUG = "condor"


def assistant_home(agent_slug: str | None = None) -> Path:
    """``agents/<slug>``; a falsy slug resolves the default agent (Condor)."""
    return agents_root() / (agent_slug or CHAT_SLUG)


def shared_skills_root() -> Path:
    """The published skill library every assistant reads: ``agents/_shared/skills``.

    Publication is the **directory**, not a frontmatter flag: a playbook in here
    is global, one that isn't, isn't. That makes the boundary visible on disk and
    makes leaking an unpublished playbook structurally impossible rather than a
    check that must never be forgotten.

    The ``_`` prefix keeps it out of the agent registry — ``AgentStore``'s
    iterator skips ``_``-prefixed dirs, the same convention that already hides
    ``agents/_defaults``. Only Condor may write here (see :class:`SkillStore`).
    """
    return agents_root() / "_shared" / "skills"


def shared_routines_root() -> Path:
    """The published routine library every assistant reads: ``agents/_shared/routines``.

    The routine twin of :func:`shared_skills_root`, deliberately the same
    convention: the directory *is* the publication flag (a Python module has no
    frontmatter to carry one), only the chat writes here, and every agent reads
    it *under* its own routines, which shadow it by name.

    The ``_`` prefix keeps it out of the agent registry, so a shared routine
    never surfaces as an agent-owned ``_shared/<name>`` — it is part of the
    general library, un-prefixed. See :func:`routines.base.assistant_routines`.
    """
    return agents_root() / "_shared" / "routines"


def store_root(user_id: int, agent_slug: str | None = None) -> Path:
    """Root of an agent's per-user store: ``<home>/store/user_{id}``."""
    return assistant_home(agent_slug) / "store" / f"user_{user_id}"


def builtin_skills_root(agent_slug: str | None = None) -> Path | None:
    """Repo-shipped skills library for an agent (FEAT-004): ``<home>/skills``.

    These are authored playbooks that ship with Condor and are available without
    being copied into the mutable per-user store. They live *beside* the agent's
    store, not inside it — one ``skills/<slug>/SKILL.md`` per playbook.

    Merged into the agent's [SKILLS]/[DOMAIN SKILLS] index alongside its learned
    skills. The library is editable at runtime: ``SkillStore`` create/edit/delete
    act on this same dir, so repo-shipped playbooks can be refined like any other
    (edits are version-controlled). See :mod:`condor.memory.skills`.
    """
    return assistant_home(agent_slug) / "skills"


def iter_user_stores(user_id: int) -> list[tuple[str, str | None, Path]]:
    """``(label, agent_slug, root)`` for each existing store of ``user_id``.

    Used by ``/memory`` to show one section per agent. Scans
    ``agents/*/store/user_{id}`` and returns only the stores that exist on disk
    (so empty agents don't clutter the view). ``agent_slug`` is ``None`` for the
    chat and the slug for a trading agent, so a caller can rebuild the store via
    ``MemoryStore(user_id, agent_slug)``. The chat is labelled ``condor (chat)``
    and listed first, then agents alphabetically.
    """
    found: list[tuple[str, str | None, Path]] = []

    chat_root = store_root(user_id)
    if chat_root.exists():
        found.append((f"{CHAT_SLUG} (chat)", None, chat_root))

    agents_dir = agents_root()
    if agents_dir.exists():
        for d in sorted(agents_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("_") or d.name == "strategies":
                continue
            if d.name == CHAT_SLUG:
                continue  # already listed first, as the chat
            root = d / "store" / f"user_{user_id}"
            if root.exists():
                found.append((d.name, d.name, root))

    return found
