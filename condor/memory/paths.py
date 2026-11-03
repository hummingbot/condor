"""Per-agent store path resolver — and the stock/local layering (FEAT-115).

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

**There are two roots, and only one of them is writable.**
:func:`condor.paths.stock_agents_root` is the library the repo ships — tracked,
curated, updated by ``git pull``. :func:`condor.paths.local_agents_root` is
what this install writes. Reads are layered, **local shadows stock, item by
item**; writes go to the local root, always.

Layering is not a new mechanism here — it is the rule this package already
applies twice, on a second axis. ``SkillStore._iter_skills`` merges an agent's
own library over the shared one with a ``seen`` set, and
``routines.base.discover_routines`` merges the general library over the shared
one by name. Stock-versus-local is that same first-root-wins rule applied to
install-versus-upstream, which is why the readers gained a longer tuple rather
than a new concept.

The two shapes are deliberate and paired:

* the **singular** resolver (:func:`agent_home`, :func:`shared_skills_root`,
  :func:`builtin_skills_root`, :func:`store_root`) is the *write* target and is
  always local;
* the **plural** one (:func:`agent_home_layers`, :func:`shared_skills_roots`,
  :func:`builtin_skills_roots`, :func:`defaults_layers`) is the *read* order,
  local first.

A caller that picks the wrong one is wrong in a visible direction: writing
through a plural is a type error, reading through a singular silently loses the
stock library.

Pure filesystem logic with **no** MCP/Telegram deps and no ``yaml``, so it runs
from the main process (prompt injection) and from the MCP subprocess (the
tools) alike.
"""

from __future__ import annotations

from pathlib import Path

from condor.paths import local_agents_root, stock_agents_root

# The default agent: the one answering when no specialist is bound. It is a
# normal agent directory like any other — what makes it default is that a falsy
# slug resolves to it (FEAT-033).
CHAT_SLUG = "condor"

# ``_``-prefixed directories are libraries, not agents; ``strategies`` is a
# legacy flat sibling. One rule, used by every walker of either root.
DEFAULTS_DIRNAME = "_defaults"


def _is_agent_dir(path: Path) -> bool:
    return path.is_dir() and not path.name.startswith("_") and path.name != "strategies"


def agent_home(agent_slug: str | None = None) -> Path:
    """The **writable** home of an agent: ``<local>/<slug>``.

    Everything the running product writes for an agent hangs off this — its
    store, its proposals, its mutes, its strategies and any definition it forked
    down from stock. A falsy slug resolves the default agent (Condor).

    Use :func:`agent_home_layers` to *read* something that may still be stock.
    """
    return local_agents_root() / (agent_slug or CHAT_SLUG)


def stock_agent_home(agent_slug: str | None = None) -> Path:
    """The shipped home of an agent: ``<stock>/<slug>``. Never written at runtime."""
    return stock_agents_root() / (agent_slug or CHAT_SLUG)


def agent_home_layers(agent_slug: str | None = None) -> tuple[Path, Path]:
    """``(local, stock)`` for an agent — read order, first hit wins."""
    return agent_home(agent_slug), stock_agent_home(agent_slug)


def resolve_agent_file(agent_slug: str | None, *rel: str) -> Path | None:
    """The first existing ``<home>/<*rel>`` across the layers, or ``None``.

    The single choke point for "where does this agent's authored X actually
    live". Per *item*, deliberately: an install that forked ``AGENT.md`` must
    still receive upstream's new ``skills/<slug>/``, so the fork can never be
    the whole directory.
    """
    for home in agent_home_layers(agent_slug):
        candidate = home.joinpath(*rel)
        if candidate.exists():
            return candidate
    return None


def iter_agent_slugs() -> list[str]:
    """Every agent slug either root knows, deduped and sorted.

    The union is what makes a stock agent visible on an install that has never
    written to it, and a locally-created one visible beside the shipped ones.
    """
    slugs: set[str] = set()
    for root in (local_agents_root(), stock_agents_root()):
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if _is_agent_dir(child):
                slugs.add(child.name)
    return sorted(slugs)


def defaults_layers() -> tuple[Path, Path]:
    """``_defaults/`` in both roots — the shipped fallbacks, overridable locally.

    Three callers used to reach this directory by walking *up* from an agent's
    home (``home.parent / "_defaults"``). With two roots that lands in whichever
    layer the child happened to resolve from, so they ask here instead.
    """
    return (
        local_agents_root() / DEFAULTS_DIRNAME,
        stock_agents_root() / DEFAULTS_DIRNAME,
    )


def shared_skills_root() -> Path:
    """Where a published skill is **written**: ``<local>/_shared/skills``.

    Publication is the **directory**, not a frontmatter flag: a playbook in here
    is global, one that isn't, isn't. That makes the boundary visible on disk and
    makes leaking an unpublished playbook structurally impossible rather than a
    check that must never be forgotten.

    The ``_`` prefix keeps it out of the agent registry — :func:`iter_agent_slugs`
    skips ``_``-prefixed dirs, the same convention that already hides
    ``agents/_defaults``. Only Condor may write here (see :class:`SkillStore`).
    """
    return local_agents_root() / "_shared" / "skills"


def shared_skills_roots() -> tuple[Path, Path]:
    """``(local, stock)`` shared skill libraries — read order."""
    return (
        local_agents_root() / "_shared" / "skills",
        stock_agents_root() / "_shared" / "skills",
    )


def shared_routines_root() -> Path:
    """Where a published routine is **written**: ``<local>/_shared/routines``.

    The routine twin of :func:`shared_skills_root`, deliberately the same
    convention: the directory *is* the publication flag (a Python module has no
    frontmatter to carry one), only the chat writes here, and every agent reads
    it *under* its own routines, which shadow it by name.

    The ``_`` prefix keeps it out of the agent registry, so a shared routine
    never surfaces as an agent-owned ``_shared/<name>`` — it is part of the
    general library, un-prefixed. See :func:`routines.base.assistant_routines`.
    """
    return local_agents_root() / "_shared" / "routines"


def shared_routines_roots() -> tuple[Path, Path]:
    """``(local, stock)`` shared routine libraries — read order."""
    return (
        local_agents_root() / "_shared" / "routines",
        stock_agents_root() / "_shared" / "routines",
    )


def store_root(user_id: int, agent_slug: str | None = None) -> Path:
    """Root of an agent's per-user store: ``<local home>/store/user_{id}``.

    Always local, with no stock counterpart to layer over: a memory is something
    this install accumulated, never something upstream can ship.
    """
    return agent_home(agent_slug) / "store" / f"user_{user_id}"


def builtin_skills_root(agent_slug: str | None = None) -> Path | None:
    """The **writable** skills library of an agent: ``<local home>/skills``.

    Authored playbooks live *beside* the agent's store, not inside it — one
    ``skills/<slug>/SKILL.md`` per playbook. A create, an edit or a promoted
    proposal lands here; a shipped playbook of the same slug is forked down
    first (see :func:`condor.layering.fork_if_stock`).

    Merged into the agent's [SKILLS]/[DOMAIN SKILLS] index alongside its learned
    skills. See :mod:`condor.memory.skills`.
    """
    return agent_home(agent_slug) / "skills"


def builtin_skills_roots(agent_slug: str | None = None) -> tuple[Path, Path]:
    """``(local, stock)`` own-skill libraries for an agent — read order."""
    local, stock = agent_home_layers(agent_slug)
    return local / "skills", stock / "skills"


def iter_user_stores(user_id: int) -> list[tuple[str, str | None, Path]]:
    """``(label, agent_slug, root)`` for each existing store of ``user_id``.

    Used by ``/memory`` to show one section per agent. Walks the union of both
    roots for agent names and returns only the stores that exist on disk (so
    empty agents don't clutter the view); the stores themselves are local by
    construction. ``agent_slug`` is ``None`` for the chat and the slug for a
    trading agent, so a caller can rebuild the store via ``MemoryStore(user_id,
    agent_slug)``. The chat is labelled ``condor (chat)`` and listed first, then
    agents alphabetically.
    """
    found: list[tuple[str, str | None, Path]] = []

    chat_root = store_root(user_id)
    if chat_root.exists():
        found.append((f"{CHAT_SLUG} (chat)", None, chat_root))

    for slug in iter_agent_slugs():
        if slug == CHAT_SLUG:
            continue  # already listed first, as the chat
        root = store_root(user_id, slug)
        if root.exists():
            found.append((slug, slug, root))

    return found
