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

import hashlib
import json
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


# The parts a session's configuration is fingerprinted in. Named rather than
# folded into one digest because the name is what the log line and the note in
# the transcript say: "reloaded (tools)" is diagnosable, "something changed" is
# not. Order is the order they are reported in.
CONFIG_PARTS = ("model", "identity", "tools", "libraries", "server")


def _digest(part: object) -> str:
    """Short, stable digest of one configuration part.

    Determinism is load-bearing: a digest that moves on its own would respawn
    every chat session on every message and truncate each one to the replay
    budget. ``sort_keys`` settles dict ordering, ``default=str`` keeps an
    unexpected value from raising mid-turn, and every set fed in here is sorted
    by its caller.
    """
    blob = json.dumps(part, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


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

    def fingerprint(self) -> dict[str, str]:
        """Per-part digests of everything a spawn bakes into the session.

        A chat session holds one subprocess across every turn, and the whole
        configuration — model, instructions, tool profile, mute list, server —
        is handed over exactly once, inside ``session/new``. So staleness is not
        an event anybody can publish: ``manage_agents`` rewrites ``AGENT.md``
        from the MCP subprocess, the web routes write ``mutes.yml`` from the
        main process, and no signal reaches from one to the other. It is a
        *fact to be recomputed*, and this is the recomputation — the filesystem
        is the channel, this is the read.

        Compared per turn by the session registry (FEAT-093). Because it hashes
        content rather than counting writes, N changes before the next message
        coalesce into one reload and a change that is reverted before the next
        message costs none.

        ``libraries`` is the one part not already resolved onto the binding:
        skill and routine mutes are read at prompt-build time, not baked into
        argv, so they are read here. Tool mutes need no such read — they ride
        into ``mcp_servers`` as ``--mute-tools`` (FEAT-091).

        The **inputs** must never be logged: ``mcp_servers`` carries API keys in
        its ``env`` entries. The digests are safe to carry and only part *names*
        are ever rendered.
        """
        from condor.memory.mutes import load_mutes

        try:
            mutes = load_mutes(self.agent_slug)
        except Exception:  # noqa: BLE001 - an unreadable mute file is "none muted"
            mutes = {}
        libraries = {
            kind: sorted(mutes.get(kind) or ()) for kind in ("skills", "routines")
        }

        return {
            "model": _digest(self.agent_key),
            "identity": _digest([self.label, self.instructions]),
            # ``mcp_servers`` carries the seat profile and the tool mute list on
            # argv, so this one part covers both what is mounted and what the
            # allowlist narrows it to.
            "tools": _digest([self.tools, self.mcp_servers]),
            "libraries": _digest(libraries),
            "server": _digest([self.server_name, self.server_pinned]),
        }


def resolve(
    spec: SessionSpec,
    user_data: dict | None = None,
    session_key: str = "",
) -> SessionBinding:
    """Resolve the binding for ``spec``: one path, one registry.

    An empty ``agent_slug`` is not "no agent" — it is the default one, Condor.
    Callers get a fully resolved toolset and identity and never re-derive either.

    ``session_key`` is the seat this binding is for, passed down to the condor
    MCP subprocess so a tool that reports back to its origin (``delegate``,
    ``run_code``, ``send_notification``, ``manage_routines``) knows which
    conversation asked. Only a chat session has one; consult, the delegate
    worker and the tick engine call :func:`toolsets.build_mcp_servers_for_session`
    directly and correctly pass none.
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
        session_key=session_key,
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
