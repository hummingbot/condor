"""Unified domain *Agent* model + discovery/CRUD store.

An **Agent** is a specialized domain agent with an identity, domain knowledge, a
tool allowlist, an ``agent_key`` (its default model) and its own memory/skills
store (FEAT-003, keyed by the directory slug — the "brain"). It replaces the old
split between ``experts.py`` (consult-only) and the identity half of
``strategy.py`` (loop-only).

**Every Agent can do all three things, always** — there is no capability flag and
nothing to opt into:

- **consult** — run its brain to completion, human-gated, and return the answer,
- **delegate** — the same run, detached and unattended, notifying when done,
- **loop** — tick a playbook via ``TickEngine``. An Agent that has never been
  given a bespoke playbook loops its *default* one, materialized on first start
  from its own identity (see ``strategy.ensure_default``).

``when_to_consult`` is therefore NOT a switch — it is an optional one-line routing
hint for the coordinator's ``[AGENTS]`` index, falling back to ``description``
then ``name`` (see :attr:`Agent.consult_hint`). An Agent missing it is still
consulted, delegated to and looped exactly like every other.

Disk layout::

    agents/{slug}/
        AGENT.md                       # Agent identity + domain knowledge (no `role`)
        skills/<slug>/SKILL.md         # shared skills (consult + every strategy) [FEAT-002/003]
        store/user_{id}/               # learned memory (the shared brain) [FEAT-003]
        strategies/{sslug}/            # owned playbooks (see strategy.py)

An Agent may be **authored in the repo** (e.g. ``executor_manager``) or **created
at runtime**; either way ``AgentStore`` can create/update/delete it.

``condor`` is one of these directories (FEAT-033) — the **default** agent, the
one answering when no specialist is bound. What makes it default is that a falsy
``agent_slug`` resolves to it, not a different kind of record. Its slug is
reserved (``create``/``delete`` refuse it) and it is excluded from the peer
indexes, because a coordinator is not a peer.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from condor.memory.paths import CHAT_SLUG
from condor.memory.store import _parse_frontmatter

from .strategy import _render_frontmatter, _slugify

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "agents"


@dataclass
class Agent:
    slug: str  # directory name == agent_slug for the domain store (FEAT-003)
    name: str
    description: str = ""
    instructions: str = ""  # AGENT.md body: identity + domain knowledge
    agent_key: str = ""  # default model (pydantic-ai or ACP, e.g. "claude-code")
    # Tool-name allowlist (pydantic-ai only), enforced on BOTH consult and loop.
    # Names match full (``mcp__condor__manage_skill``) or short (``manage_skill``).
    # Empty => UNRESTRICTED (all discovered tools, subject to tool_filter_mode).
    tools: list[str] = field(default_factory=list)
    # Optional one-line routing hint ("when should condor pick this agent?").
    # NOT a capability switch — see consult_hint and the module docstring.
    when_to_consult: str = ""
    server_required: bool = True
    # Pin this agent to a specific hummingbot-api server. When set, the agent's
    # mcp-hummingbot subprocess is initialized against THIS server regardless of
    # the chat's active server. Empty => fall back to the ambient chat server.
    server_name: str = ""
    created_by: int = 0
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def agent_dir(self) -> Path:
        return _DATA_ROOT / self.slug

    @property
    def routines_dir(self) -> Path:
        """Agent-level routines, shared across all of this agent's strategies."""
        return self.agent_dir / "routines"

    @property
    def consult_hint(self) -> str:
        """The one line that describes this Agent in the coordinator's index.

        Every Agent is consultable on any model — a pydantic-ai key runs the
        consult with the tool allowlist enforced, an ACP key (claude-code/gemini/
        copilot) runs it unrestricted with mutations confirmation-gated (see
        consult.py). So this never gates anything; it only helps condor *route*.
        An Agent that never got an explicit ``when_to_consult`` still gets a
        useful line from its description (or, failing that, its name).
        """
        return self.when_to_consult or self.description or self.name


def identity_header(slug: str, name: str = "") -> str:
    """The first thing a bound Agent's brain reads: *which* assistant it is.

    An Agent's AGENT.md says what it knows ("You are a specialist in …") but
    never that it IS this agent and is NOT Condor, the chat assistant. Without
    that line the model reads the coordinator framing it also receives and
    answers in the third person about itself, offering to consult itself
    (FEAT-025). Shared verbatim by the condor MCP server's instructions (the
    only system-level channel ACP v1 gives us) and by the session's opening
    context, so the two framings can never drift apart.

    Takes the slug/name rather than an :class:`Agent` because the MCP
    subprocess and the runtime reach it from opposite sides — one has settings,
    the other a resolved binding — and neither should re-read AGENT.md for a
    single line.

    Condor is an agent like any other now (FEAT-033), so it can reach here — and
    the specialist text would be self-contradictory for it ("You ARE Condor …
    You are NOT Condor"). It gets the coordinator's framing instead.
    """
    label = name or slug
    if slug == CHAT_SLUG:
        return (
            "You ARE Condor (slug: `condor`), the chat assistant the user talks "
            "to. You are the coordinator, not a specialist: domain work goes to "
            "the agents listed for you, and their answers come back through you. "
            "Never consult or delegate to `condor`, because that is you."
        )
    return (
        f'You ARE the "{label}" agent (slug: `{slug}`) running inside Condor. '
        "You are NOT Condor, the chat assistant — Condor is a different "
        f"assistant that can consult you. Answer in the first person as {label}: "
        f"never describe {label} in the third person, and never consult or "
        f"delegate to `{slug}`, because that is you."
    )


def _load_agent_from_dir(agent_dir: Path) -> Agent | None:
    """Load an Agent from ``<agent_dir>/AGENT.md`` (any dir with the file)."""
    path = agent_dir / "AGENT.md"
    if not path.exists():
        return None
    try:
        meta, body = _parse_frontmatter(path.read_text())
        return Agent(
            slug=agent_dir.name,
            name=meta.get("name", agent_dir.name),
            description=meta.get("description", ""),
            instructions=body,
            agent_key=meta.get("agent_key", ""),
            tools=meta.get("tools", []) or [],
            when_to_consult=meta.get("when_to_consult", ""),
            server_required=meta.get("server_required", True),
            server_name=meta.get("server_name", "") or "",
            created_by=meta.get("created_by", 0),
            created_at=meta.get("created_at", ""),
        )
    except Exception:
        log.exception("Failed to load agent from %s", path)
        return None


class AgentStore:
    """Discovery + CRUD for Agents under ``agents/*/AGENT.md``.

    Replaces ``ExpertStore`` and the identity half of ``StrategyStore``. There is
    no ``role`` discriminator and no capability flag: every directory with an
    ``AGENT.md`` is an Agent, and every Agent can be consulted, delegated to and
    looped.
    """

    def get(self, slug: str) -> Agent | None:
        if not slug:
            return None
        return _load_agent_from_dir(_DATA_ROOT / slug)

    def list_all(self) -> list[Agent]:
        agents: list[Agent] = []
        for d in self._iter_agent_dirs():
            a = _load_agent_from_dir(d)
            if a is not None:
                agents.append(a)
        return agents

    def list_specialists(self) -> list[Agent]:
        """Every Agent a chat can *bind* to — the registry minus the coordinator.

        Binding is what names a specialist; Condor is who answers when nothing
        is bound (FEAT-033), so a picker that also offered it would show one
        identity twice — and picking that second entry would bind the chat to
        the coordinator as if it were a specialist, flipping what ``is_agent``
        means for the session.

        Not a filter in the sense :meth:`list_index` forbids: nothing is hidden
        here. The coordinator is reached by binding nothing, which every picker
        already offers as its first row.
        """
        return [a for a in self.list_all() if a.slug != CHAT_SLUG]

    def list_index(self, exclude: str | Iterable[str] = "") -> str:
        """Injectable index — one line per Agent (mirrors SKILLS).

        EVERY consultable Agent is listed. An agent missing from this index is
        invisible to whoever reads it, which means it can never be consulted or
        delegated to — so filtering here is the same as deleting the agent.
        Empty string only when no agents exist at all, so callers inject nothing.

        ``exclude`` drops slugs that are not *peers* of the reader, and nothing
        else — the rule is still "never filter to hide an agent":

        - the reader itself, which must not be told to consult itself (FEAT-025);
        - ``condor``, which is an agent now (FEAT-033) but is the coordinator,
          not a peer. A specialist offered Condor could consult back into the
          chat, with auto-approved tools on the delegate path.

        Callers pass a set of both. Filtering for any other reason stays
        forbidden, which is why this takes slugs rather than a predicate.
        """
        dropped = {exclude} if isinstance(exclude, str) else set(exclude)
        return "\n".join(
            f"- [{a.slug}] {a.consult_hint}"
            for a in self.list_all()
            if a.slug not in dropped
        )

    def create(
        self,
        name: str,
        description: str = "",
        instructions: str = "",
        agent_key: str = "",
        tools: list[str] | None = None,
        when_to_consult: str = "",
        server_required: bool = True,
        server_name: str = "",
        created_by: int = 0,
    ) -> Agent:
        slug = _slugify(name)
        if slug == CHAT_SLUG:
            # Reserved: `condor` names the default agent. The separate trees used
            # to make the collision impossible by construction (FEAT-003); with
            # one registry it takes one rule (FEAT-033).
            raise ValueError(
                f"'{CHAT_SLUG}' is reserved for the default agent — pick another name"
            )
        agent = Agent(
            slug=slug,
            name=name,
            description=description,
            instructions=instructions,
            agent_key=agent_key,
            tools=tools or [],
            when_to_consult=when_to_consult,
            server_required=server_required,
            server_name=server_name,
            created_by=created_by,
        )
        self._save(agent)
        log.info("Created agent %s (dir: %s)", agent.name, agent.slug)
        return agent

    def update(self, agent: Agent) -> None:
        self._save(agent)

    def delete(self, slug: str) -> bool:
        if slug == CHAT_SLUG:
            # Same reservation as `create`: deleting the default agent's AGENT.md
            # would leave every unbound session without instructions or a model.
            raise ValueError(
                f"'{CHAT_SLUG}' is the default agent and cannot be deleted"
            )
        agent_dir = _DATA_ROOT / slug
        path = agent_dir / "AGENT.md"
        if not path.exists():
            return False
        path.unlink()
        # Remove the whole dir only when nothing else lives there (no strategies,
        # store or skills) — the brain/strategies must not be silently dropped.
        try:
            if not any(agent_dir.iterdir()):
                agent_dir.rmdir()
        except Exception:
            pass
        log.info("Deleted agent %s", slug)
        return True

    def _save(self, agent: Agent) -> None:
        meta = {
            "name": agent.name,
            "description": agent.description,
            "agent_key": agent.agent_key,
            "tools": agent.tools,
            "when_to_consult": agent.when_to_consult,
            "server_required": agent.server_required,
            "server_name": agent.server_name,
            "created_by": agent.created_by,
            "created_at": agent.created_at,
        }
        agent.agent_dir.mkdir(parents=True, exist_ok=True)
        (agent.agent_dir / "AGENT.md").write_text(
            _render_frontmatter(meta, agent.instructions)
        )

    def _iter_agent_dirs(self):
        if not _DATA_ROOT.exists():
            return
        for d in sorted(_DATA_ROOT.iterdir()):
            if not d.is_dir() or d.name.startswith("_") or d.name == "strategies":
                continue
            yield d
