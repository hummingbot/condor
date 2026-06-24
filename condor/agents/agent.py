"""Unified domain *Agent* model + discovery/CRUD store.

An **Agent** is a specialized domain agent with an identity, domain knowledge, a
tool allowlist, an ``agent_key`` (its default model) and its own memory/skills
store (FEAT-003, keyed by the directory slug — the "brain"). It replaces the old
split between ``experts.py`` (consult-only) and the identity half of
``strategy.py`` (loop-only). An Agent:

- is **consultable** (CONSULT mode: run its own brain to completion → answer), and
- **owns strategies** (RUN mode: each strategy is a *playbook* looped by ``TickEngine``).

Capabilities are **derived**, not flagged: an Agent with ``when_to_consult`` + a
pydantic-ai ``agent_key`` is consultable; an Agent with ≥1 strategy is loopeable;
it can be both.

Disk layout::

    agents/{slug}/
        AGENT.md                       # Agent identity + domain knowledge (no `role`)
        skills/<slug>/SKILL.md         # shared skills (consult + every strategy) [FEAT-002/003]
        store/user_{id}/               # learned memory (the shared brain) [FEAT-003]
        strategies/{sslug}/            # owned playbooks (see strategy.py)

An Agent may be **authored in the repo** (e.g. ``executor_manager``) or **created
at runtime**; either way ``AgentStore`` can create/update/delete it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from condor.acp.pydantic_ai_client import is_pydantic_ai_model
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
    agent_key: str = ""  # default model (pydantic-ai to be consultable)
    # Tool-name allowlist (pydantic-ai only), enforced on BOTH consult and loop.
    # Names match full (``mcp__condor__manage_skill``) or short (``manage_skill``).
    # Empty => UNRESTRICTED (all discovered tools, subject to tool_filter_mode).
    tools: list[str] = field(default_factory=list)
    when_to_consult: str = ""  # empty => not offered as consultable
    server_required: bool = True
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
    def consultable(self) -> bool:
        """DERIVED capability: a non-empty trigger AND a pydantic-ai model.

        The pydantic-ai requirement preserves the old expert rule — the tool
        allowlist can only be enforced on a pydantic-ai client (see consult.py).
        """
        return bool(self.when_to_consult) and is_pydantic_ai_model(self.agent_key)


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
            created_by=meta.get("created_by", 0),
            created_at=meta.get("created_at", ""),
        )
    except Exception:
        log.exception("Failed to load agent from %s", path)
        return None


class AgentStore:
    """Discovery + CRUD for Agents under ``agents/*/AGENT.md``.

    Replaces ``ExpertStore`` and the identity half of ``StrategyStore``. There is
    no ``role`` discriminator anymore: every directory with an ``AGENT.md`` is an
    Agent; whether it is consultable/loopeable is derived from its definition.
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

    def list_consultable_index(self) -> str:
        """Injectable index — one line per *consultable* Agent (mirrors SKILLS).

        Empty string when none are consultable, so callers inject nothing.
        """
        lines = [
            f"- [{a.slug}] {a.when_to_consult or a.description}"
            for a in self.list_all()
            if a.consultable
        ]
        return "\n".join(lines)

    def create(
        self,
        name: str,
        description: str = "",
        instructions: str = "",
        agent_key: str = "",
        tools: list[str] | None = None,
        when_to_consult: str = "",
        server_required: bool = True,
        created_by: int = 0,
    ) -> Agent:
        agent = Agent(
            slug=_slugify(name),
            name=name,
            description=description,
            instructions=instructions,
            agent_key=agent_key,
            tools=tools or [],
            when_to_consult=when_to_consult,
            server_required=server_required,
            created_by=created_by,
        )
        self._save(agent)
        log.info("Created agent %s (dir: %s)", agent.name, agent.slug)
        return agent

    def update(self, agent: Agent) -> None:
        self._save(agent)

    def delete(self, slug: str) -> bool:
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
