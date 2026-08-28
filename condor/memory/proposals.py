"""A playbook an agent *offered*, waiting for a human to accept it (FEAT-074).

The reflection pass (:mod:`condor.agents.reflection`) reads a finished
conversation back and sometimes notices that it just worked out a **procedure**
— not a fact about the user, but a repeatable way of doing something. That is a
skill's shape. It is not, however, a skill: a skill is per-agent and reaches
every future prompt of every user of that agent
(:func:`condor.memory.context.domain_context`), so the write is a human's.

**The boundary is the directory, not a flag.** A proposal lives in
``agents/<slug>/proposals/<slug>.md`` — a *sibling* of ``skills/``, and
therefore off the ``<root>/*/SKILL.md`` glob that :meth:`SkillStore._iter_skills`
walks. An unreviewed playbook is not merely filtered out of the injected index,
the catalog and the search: it is not on any path the agent reads. This is the
same reasoning the repo already applied to publication
(:func:`condor.memory.paths.shared_skills_root`) — "publication is the
directory, not a frontmatter flag" — for the same reason: a boundary you can see
with ``ls`` cannot be forgotten by a reader added next year, whereas a
``pending: true`` flag has to be honoured by the index, the catalog, the search
and every fifth reader somebody writes later.

**Accepting is :meth:`SkillStore.create` and nothing else.** The store already
takes exactly the fields a proposal carries and already owns slugging,
frontmatter and the atomic write, so an accepted playbook is byte-identical to
one authored by hand and everything the library does — shadowing, publishing,
editing, deleting — applies to it with no code of its own. The file is deleted
only once the create reports success, so a failed write leaves the proposal
standing instead of silently losing it.

**One pending proposal per agent.** A new proposal replaces the standing one
rather than queueing behind it. A review queue nobody drains is worse than a
single card that is either useful now or gone, and the merge rule is one line
against a list UI, an ordering and a "12 pending" badge that becomes furniture.

Pure filesystem, like the two stores it sits between: no MCP and no web deps, so
the runtime and the routes can both use it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from condor.frontmatter import parse_frontmatter, render_frontmatter

from .paths import assistant_home
from .skills import SkillStore
from .store import _atomic_write, _slugify, _utcnow

log = logging.getLogger(__name__)

PROPOSALS_DIRNAME = "proposals"

# Where a proposal came from. Written into the accepted skill's ``source`` too,
# so a playbook the agent proposed stays distinguishable in the library from one
# a human typed into the panel (``web``) or the chat wrote (``chat``).
SOURCE = "reflection"

# Bounds on model-supplied one-liners, matching what ``SkillStore`` stores: a
# description and a trigger are index lines, not paragraphs.
LINE_MAX_CHARS = 200


def proposals_root(agent_slug: str | None = None) -> Path:
    """``agents/<slug>/proposals`` — a sibling of the skill library, off its glob."""
    return assistant_home(agent_slug) / PROPOSALS_DIRNAME


def _line(value, limit: int = LINE_MAX_CHARS) -> str:
    """One bounded line of model-supplied text."""
    return " ".join(str(value or "").split())[:limit]


def _standing(agent_slug: str | None) -> Path | None:
    """The pending proposal's file, or ``None``.

    :func:`put` clears the directory before it writes, so there is only ever one
    ``.md`` in here. Should a hand-edit leave two, the first by name is the
    standing one and :func:`discard` takes both — one card is the contract.
    """
    root = proposals_root(agent_slug)
    if not root.is_dir():
        return None
    for path in sorted(root.glob("*.md")):
        return path
    return None


def put(
    agent_slug: str | None,
    *,
    name: str,
    description: str,
    when_to_use: str,
    body: str,
    conversation_id: str = "",
) -> dict:
    """File a proposal, replacing whatever was standing. Never raises.

    Refuses a half-filled proposal the way the stores do — by *returning* an
    error rather than raising — because its caller is a reflection pass applying
    a model's answer, where one malformed field must not cost the memories and
    intents beside it.
    """
    slug = _slugify(name or "")
    if not name or not description or not when_to_use or not body:
        return {"error": "name, description, when_to_use and body are required"}

    discard(agent_slug)
    meta = {
        "name": slug,
        "description": _line(description),
        "when_to_use": _line(when_to_use),
        "source": SOURCE,
        "from_conversation": str(conversation_id or ""),
        "created": _utcnow(),
    }
    _atomic_write(
        proposals_root(agent_slug) / f"{slug}.md",
        render_frontmatter(meta, str(body).strip()),
    )
    return {"saved": True, "name": slug}


def get(agent_slug: str | None = None) -> dict | None:
    """The pending proposal, or ``None``. An unreadable one reads as absent."""
    path = _standing(agent_slug)
    if path is None:
        return None
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a mangled proposal is not a crash
        log.warning("Could not read the proposal at %s", path, exc_info=True)
        return None
    return {
        "name": meta.get("name", path.stem),
        "description": meta.get("description", ""),
        "when_to_use": meta.get("when_to_use", ""),
        "body": body,
        "source": meta.get("source", SOURCE),
        "from_conversation": str(meta.get("from_conversation", "") or ""),
        "created": str(meta.get("created", "") or ""),
    }


def discard(agent_slug: str | None = None) -> bool:
    """Delete the pending proposal. ``True`` when there was one."""
    root = proposals_root(agent_slug)
    if not root.is_dir():
        return False
    removed = False
    for path in sorted(root.glob("*.md")):
        try:
            path.unlink()
            removed = True
        except OSError:
            log.warning("Could not delete the proposal at %s", path, exc_info=True)
    try:
        root.rmdir()
    except OSError:
        pass  # something else lives here — leave the folder
    return removed


def accept(agent_slug: str | None = None) -> dict:
    """Turn the pending proposal into a real skill in this agent's own library.

    The create is the whole of it, so what lands is indistinguishable from a
    playbook that shipped with the repo. The proposal is deleted only if the
    store reported success: a library that refused the write must not also lose
    the offer.
    """
    proposal = get(agent_slug)
    if proposal is None:
        return {"error": "no proposal is pending for this agent"}

    result = SkillStore(agent_slug).create(
        name=proposal["name"],
        description=proposal["description"],
        when_to_use=proposal["when_to_use"],
        body=proposal["body"],
        source=SOURCE,
    )
    if not isinstance(result, dict) or not result.get("saved"):
        return result if isinstance(result, dict) else {"error": "could not create"}

    discard(agent_slug)
    return {"accepted": True, "name": result.get("name", proposal["name"])}
