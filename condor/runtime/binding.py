"""Resolve *who* is answering a session.

A session has two orthogonal dimensions:

* ``mode``       — the assistant persona (``assistants/{name}``), e.g. the
  ``condor`` coordinator.
* ``agent_slug`` — a domain Agent (``agents/{slug}``), e.g. ``brigado``.

A session carries at most one of each, and when ``agent_slug`` is set it wins
for identity, tools, server pin and memory scope. Everything that needs to know
which brain is on the other end asks this module, so an Agent behaves the same
whether it is consulted once, looped, or chatted with.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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
    mcp_env: dict[str, str] = field(default_factory=dict)
    mcp_servers: list[dict] = field(default_factory=list)

    @property
    def is_agent(self) -> bool:
        return bool(self.agent_slug)


def resolve(
    spec: SessionSpec,
    user_data: dict | None = None,
) -> SessionBinding:
    """Resolve the binding for ``spec``.

    This is the only place the assistant-vs-Agent branch exists. Callers get a
    fully resolved toolset and identity and never re-derive either.
    """
    if spec.agent_slug:
        return _resolve_agent(spec, user_data)
    return _resolve_assistant(spec, user_data)


def _resolve_assistant(spec: SessionSpec, user_data: dict | None) -> SessionBinding:
    """Today's path: an assistant persona with the chat's ambient toolset."""
    from handlers.agents._shared import build_mcp_servers_for_session

    mcp_servers: list[dict] = []
    if spec.user_id:
        mcp_servers = build_mcp_servers_for_session(
            spec.user_id,
            spec.chat_id,
            user_data,
            server_name=spec.server_name,
        )

    return SessionBinding(
        label="Condor",
        agent_key=spec.agent_key,
        server_name=spec.server_name or "",
        mcp_servers=mcp_servers,
    )


def _resolve_agent(spec: SessionSpec, user_data: dict | None) -> SessionBinding:
    """A domain Agent answers: its model, its tools, its store."""
    from condor.agents.agent import AgentStore
    from handlers.agents._shared import build_mcp_servers_for_session

    agent = AgentStore().get(spec.agent_slug)
    if agent is None:
        raise UnknownAgent(f"No agent named '{spec.agent_slug}'")

    # An explicit model on the spec beats the Agent's configured default: the
    # user picking a model in the UI is a deliberate override.
    agent_key = spec.agent_key or agent.agent_key

    # A server pinned on the Agent wins over the chat's ambient server.
    effective_server = agent.server_name or spec.server_name or ""

    # A pinned server short-circuits resolution; None lets the builder fall back
    # to the chat's ambient server. Serverless agents still need their own scope
    # — without agent_slug the condor MCP tools would target the CHAT's memory
    # and skills.
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
