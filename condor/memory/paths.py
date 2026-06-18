"""Per-assistant store path resolver.

Memory (FEAT-001) and skills (FEAT-002) used to be **per-user and shared**: the
``/agent`` chat and every trading agent of a user read/wrote the same store. This
module makes them **per-assistant**: each assistant gets its own store, co-located
with its definition, and nothing is shared across assistants (FEAT-003).

The key of a store is ``(assistant, user_id)`` — "per-assistant" *composes with*
``user_id``, it does not replace it (group chats share a chat but each user keeps
their own memory). Two assistants never resolve to the same root: the chat lives
under ``assistants/condor/`` and trading agents under ``trading_agents/{slug}/``,
which are different top-level dirs, so even a strategy literally named ``condor``
cannot collide with the chat.

Pure filesystem logic with **no** MCP/Telegram deps, so it runs from the main
process (prompt injection) and from the MCP subprocess (the tools) alike.
"""

from __future__ import annotations

from pathlib import Path

# Anchor to the project root (…/condor) so paths are stable regardless of cwd.
_PROJECT_ROOT = Path(__file__).parent.parent.parent

# Sentinel home for the interactive chat assistant. It is the single
# interactive agent; its builder capabilities ship as built-in skills (FEAT-004,
# see ``builtin_skills_root``) rather than as separate selectable assistants.
_CHAT_ASSISTANT = "condor"


def store_root(user_id: int, agent_slug: str | None = None) -> Path:
    """Root of an assistant's per-user store.

    ``agent_slug`` set  -> trading agent: ``trading_agents/{slug}/store/user_{id}``
    ``agent_slug`` None  -> chat condor:   ``assistants/condor/store/user_{id}``
    """
    if agent_slug:
        base = _PROJECT_ROOT / "trading_agents" / agent_slug
    else:
        base = _PROJECT_ROOT / "assistants" / _CHAT_ASSISTANT
    return base / "store" / f"user_{user_id}"


def builtin_skills_root(agent_slug: str | None = None) -> Path | None:
    """Read-only, repo-shipped skills for an agent (FEAT-004).

    These are authored playbooks that ship with Condor and are available without
    being copied into the mutable per-user store. They live *beside* the agent's
    store, not inside it — one ``skills/<slug>/SKILL.md`` per playbook:

    - chat ``condor`` (``agent_slug`` None) → ``assistants/condor/skills/``
      (e.g. agent_builder, routine_builder)
    - a trading agent / domain expert (``agent_slug`` set) →
      ``trading_agents/<slug>/skills/`` (e.g. an executor_manager's playbooks)

    Merged into the agent's [SKILLS]/[DOMAIN SKILLS] index alongside its learned
    skills; read-only (create/edit/delete refuse these slugs).
    """
    if agent_slug:
        return _PROJECT_ROOT / "trading_agents" / agent_slug / "skills"
    return _PROJECT_ROOT / "assistants" / _CHAT_ASSISTANT / "skills"


def iter_user_stores(user_id: int) -> list[tuple[str, str | None, Path]]:
    """``(label, agent_slug, root)`` for each existing store of ``user_id``.

    Used by ``/memory`` to show one section per assistant. Scans
    ``assistants/*/store/user_{id}`` and ``trading_agents/*/store/user_{id}`` and
    returns only the stores that exist on disk (so empty assistants don't clutter
    the view). ``agent_slug`` is ``None`` for the chat and the slug for a trading
    agent, so a caller can rebuild the store via ``MemoryStore(user_id, agent_slug)``.
    The chat is labelled ``condor (chat)`` and listed first, then agents alphabetically.
    """
    found: list[tuple[str, str | None, Path]] = []

    chat_root = (
        _PROJECT_ROOT / "assistants" / _CHAT_ASSISTANT / "store" / f"user_{user_id}"
    )
    if chat_root.exists():
        found.append((f"{_CHAT_ASSISTANT} (chat)", None, chat_root))

    agents_dir = _PROJECT_ROOT / "trading_agents"
    if agents_dir.exists():
        for d in sorted(agents_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("_") or d.name == "strategies":
                continue
            root = d / "store" / f"user_{user_id}"
            if root.exists():
                found.append((d.name, d.name, root))

    return found
