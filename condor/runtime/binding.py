"""Resolve *who* is answering a session.

A session has exactly one identity dimension: ``agent_slug``, naming a directory
under ``agents/``. Empty means Condor — the **default** agent (``CHAT_SLUG``),
not the absence of one (FEAT-033). Whoever it names supplies the identity, the
model, the toolset, the server pin and the memory scope. Everything that needs
to know which brain is on the other end asks this module, so an Agent behaves
the same whether it is consulted once, looped, or chatted with.

There used to be a second dimension — a ``mode`` selecting an assistant persona
under ``assistants/`` — but it had a single value from FEAT-004 onward and no
reader: it is gone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from condor.memory.paths import CHAT_SLUG
from condor.runtime.models import SessionSpec

log = logging.getLogger(__name__)

# Passed to the condor MCP subprocess so manage_memory/manage_skill resolve to
# the Agent's own store rather than the chat assistant's. The subprocess reads
# either this or the --agent-slug CLI arg (mcp_servers/condor/settings.py).
AGENT_SLUG_ENV = "CONDOR_AGENT_SLUG"


class UnknownAgent(ValueError):
    """Raised when a session is bound to an agent slug that does not exist."""


@dataclass
class SessionBinding:
    """Resolved identity for a session: who answers, with what tools and memory."""

    label: str = "Condor"
    agent_slug: str = ""
    agent_key: str = ""
    instructions: str = ""
    tools: list[str] = field(default_factory=list)
    server_name: str = ""
    # The Agent's front matter chose this server, so the chat's ambient
    # selection was overridden and cannot be changed from the chat. Only the
    # Agent path can set it: an assistant's ``server_name`` *is* the ambient one.
    server_pinned: bool = False
    mcp_env: dict[str, str] = field(default_factory=dict)
    mcp_servers: list[dict] = field(default_factory=list)

    @property
    def is_agent(self) -> bool:
        """A **specialist** is bound — i.e. someone other than the coordinator.

        Every session resolves an agent now, so "has an agent_slug" no longer
        separates anything. What the callers of this actually branch on is
        whether the answering brain is a specialist: whether to assert a
        separate identity at system level, open with domain memory instead of
        the chat's, and lock the server chip. Condor is none of those.
        """
        return bool(self.agent_slug) and self.agent_slug != CHAT_SLUG

    @property
    def specialist_slug(self) -> str:
        """``agent_slug`` to *record and show*: empty when Condor answers.

        The full slug goes down to the MCP subprocess (it scopes the stores),
        but everything user-facing and everything persisted keeps the older,
        narrower meaning: ``""`` is the default chat. Widening it would rewrite
        the session-reuse check (an unbound spec would never match its own live
        session and would respawn on every prompt), the conversation records and
        the dashboard's "a specialist is answering" indicator, none of which
        this feature set out to change.
        """
        return self.agent_slug if self.is_agent else ""


def resolve(
    spec: SessionSpec,
    user_data: dict | None = None,
) -> SessionBinding:
    """Resolve the binding for ``spec``: one path, one registry.

    An empty ``agent_slug`` is not "no agent" — it is the default one, Condor.
    Callers get a fully resolved toolset and identity and never re-derive either.
    """
    from condor.agents.agent import Agent, AgentStore
    from handlers.agents._shared import build_mcp_servers_for_session

    slug = spec.agent_slug or CHAT_SLUG
    agent = AgentStore().get(slug)
    if agent is None:
        if spec.agent_slug:
            raise UnknownAgent(f"No agent named '{slug}'")
        # The default agent is the fallback, so it cannot fail closed: an
        # unreadable agents/condor/AGENT.md would take every chat down with it.
        # A bare record is exactly what the chat ran on before it had one.
        agent = Agent(slug=CHAT_SLUG, name="Condor")
        log.warning("No %s/AGENT.md — the chat starts without instructions", CHAT_SLUG)

    # An explicit model on the spec beats the Agent's configured default: the
    # user picking a model in the UI is a deliberate override.
    agent_key = spec.agent_key or agent.agent_key

    # A server pinned on the Agent wins over the chat's ambient server.
    effective_server = agent.server_name or spec.server_name or ""

    # A pinned server short-circuits resolution; None lets the builder fall back
    # to the ambient server. The slug always goes down to the subprocess, for
    # Condor too: it scopes manage_memory/manage_skill to whoever is answering,
    # and a specialist launched without it would read and write the CHAT's store.
    # What the slug must NOT decide down there is identity or routine scope —
    # see ``Settings.specialist_slug``.
    mcp_servers = build_mcp_servers_for_session(
        spec.user_id or 0,
        spec.chat_id or spec.user_id or 0,
        user_data,
        server_name=effective_server if agent.server_required else None,
        agent_slug=agent.slug,
    )

    return SessionBinding(
        label=agent.name or agent.slug,
        agent_slug=agent.slug,
        agent_key=agent_key,
        instructions=agent.instructions,
        tools=list(agent.tools),
        server_name=effective_server,
        server_pinned=bool(agent.server_name),
        mcp_env={AGENT_SLUG_ENV: agent.slug},
        mcp_servers=mcp_servers,
    )


def agent_identity_context(
    agent_slug: str, user_id: int, instructions: str, label: str = ""
) -> str:
    """Identity + domain memory/skills the bound Agent opens the chat with.

    Mirrors ``build_agent_context`` (used by consult) minus the consult request,
    so a chatted Agent starts from the same self-knowledge a consulted one does.

    Leads with :func:`~condor.agents.agent.identity_header` — the same line the
    condor MCP server puts in the system prompt — because AGENT.md describes the
    domain but never says which agent this is (FEAT-025).
    """
    from condor.agents.agent import identity_header

    sections: list[str] = [identity_header(agent_slug, label)]
    if instructions:
        sections.append(instructions)

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

    return "\n\n".join(s for s in sections if s)
