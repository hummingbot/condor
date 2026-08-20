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

from condor.llm import options as llm_options
from condor.memory.paths import CHAT_SLUG
from condor.runtime import toolsets
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
    # One derivation for both channels: these are the very ids sessions.py puts
    # in CONDOR_USER_ID/CONDOR_CHAT_ID, and argv beats env in the subprocess, so
    # a local fallback here would silently override the env one (SEC-180).
    effective_user_id, effective_chat_id = spec.effective_ids()
    mcp_servers = toolsets.build_mcp_servers_for_session(
        effective_user_id,
        effective_chat_id,
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


def remember_model_choice(user_id: int | None, agent_slug: str, agent_key: str) -> None:
    """Persist a deliberate model pick where the *next* session will find it.

    A specialist's model lives in its own AGENT.md, so picking one in the chat
    moves the Agent itself — chat, consult, delegate and loop all read that
    record. Condor's does not: ``DEFAULT_AGENT`` is read from condor/AGENT.md at
    import and is everyone's default, so an unbound pick is the *user's*, and
    goes to the same preference Telegram's Change LLM writes.

    Silent no-op when the value already matches, when the key is a picker
    sentinel, or when there is no user — callers pass whatever the wire said.

    Called only from the web entry points that carry a user's pick. Consult,
    delegate and loops pass concrete keys for their own plumbing reasons and
    must not rewrite anything.
    """
    if not agent_key or not user_id:
        return
    # Drill-downs, not startable models: an agent_key of "openrouter:" would
    # fail at session start for everyone who inherited it.
    if llm_options.AGENT_OPTIONS.get(agent_key, {}).get("picker"):
        return

    try:
        if agent_slug and agent_slug != CHAT_SLUG:
            from condor.agents.agent import AgentStore

            store = AgentStore()
            agent = store.get(agent_slug)
            if agent is None or agent.agent_key == agent_key:
                return
            agent.agent_key = agent_key
            store.update(agent)
            log.info("Agent %s now runs on %s", agent_slug, agent_key)
        else:
            from condor.preferences import load_user_data_for, set_active_agent_key

            set_active_agent_key(load_user_data_for(user_id), agent_key)
    except Exception:
        # A chat must not fail because a preference could not be written.
        log.warning(
            "Could not remember model %r for %r", agent_key, agent_slug, exc_info=True
        )


def agent_identity_context(
    agent_slug: str, user_id: int, instructions: str, label: str = ""
) -> str:
    """Identity + domain memory/skills the bound Agent opens the chat with.

    Shares its domain memory/skills sections with ``build_agent_context`` (used
    by consult) via :func:`~condor.memory.domain_context`, and differs from it
    only by the identity header in front and the absent consult request behind —
    so a chatted Agent starts from the same self-knowledge a consulted one does.

    Leads with :func:`~condor.agents.agent.identity_header` — the same line the
    condor MCP server puts in the system prompt — because AGENT.md describes the
    domain but never says which agent this is (FEAT-025).
    """
    from condor.agents.agent import identity_header
    from condor.memory import domain_context

    sections: list[str] = [identity_header(agent_slug, label)]
    if instructions:
        sections.append(instructions)
    sections.extend(domain_context(agent_slug, user_id))

    return "\n\n".join(s for s in sections if s)
