"""The domain memory/skills block every Agent opens with, built once.

An Agent behaves the same whether it is chatted with or consulted, so the two
prompt builders that stand at those doors — ``binding.agent_identity_context``
and ``_shared.build_agent_context`` — must inject byte-identical sections. They
used to hold their own copy of these literals; they now compose this one
(ARCH-099). Lives under ``condor/memory/`` beside the stores it reads, which is
the only place both a runtime module and a handler may import from.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def domain_context(agent_slug: str, user_id: int) -> list[str]:
    """The Agent's memory and skills indexes, as prompt sections.

    Returns zero, one or two sections — an empty index contributes nothing, and
    a store that cannot be read is skipped rather than allowed to break session
    start. Callers add their own prefix (identity header or bare instructions)
    and suffix (nothing, or the consult request).
    """
    sections: list[str] = []

    try:
        from condor.memory import MemoryStore

        memory_index = MemoryStore(user_id, agent_slug).list_index()
        if memory_index:
            sections.append(
                "[DOMAIN MEMORY — what you remember in this domain]\n"
                'Read a full memory with manage_memory(action="read", name="..."). '
                'Save new, stable domain facts with manage_memory(action="write", ...).\n\n'
                f"{memory_index}"
            )
    except Exception:
        log.debug("Could not load memory index for %s", agent_slug, exc_info=True)

    try:
        from condor.memory import SkillStore

        skills_index = SkillStore(agent_slug).list_index()
        if skills_index:
            sections.append(
                "[DOMAIN SKILLS — playbooks you can follow]\n"
                "Read-only playbooks shipped with this Agent. Read one with "
                'manage_skill(action="read", name="...") and follow its steps.\n\n'
                f"{skills_index}"
            )
    except Exception:
        log.debug("Could not load skill index for %s", agent_slug, exc_info=True)

    return sections
