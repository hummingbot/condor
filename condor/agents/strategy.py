"""Strategy definitions and persistence — a *playbook* owned by an Agent.

Each strategy is a tick-loop playbook that lives **under its owning Agent**, as
``strategy.md`` (YAML frontmatter + markdown body) inside a per-strategy folder::

    agents/
        {agent_slug}/
            AGENT.md                       # the owning Agent (see agent.py)
            routines/                      # routines shared by all of this agent's strategies
            skills/                        # skill playbooks (the agent "brain")
            strategies/
                {strategy_slug}/
                    strategy.md            # this playbook: tactics + config
                    learnings.md           # cross-session learnings of this strategy
                    sessions/session_N/    # per-run journal (format unchanged)
                    dry_runs/              # experiment snapshots

A strategy is identified by the pair ``(agent_slug, slug)``; its opaque composite
key ``"{agent_slug}.{slug}"`` is what MCP tools pass around as ``strategy_id``.
Authoring one is optional: an Agent that owns none still loops, on the *default*
playbook :func:`StrategyStore.ensure_default` materializes from its identity.
The Agent's memory/skills/routines (the "brain") are shared across all of its
strategies and its consults — they live one level up, at
``agents/{agent_slug}/``.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from condor.frontmatter import parse_frontmatter, render_frontmatter, slugify
from condor.fsutil import atomic_write_text

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "agents"

# Every Agent is loopable — one that was never given a bespoke playbook loops
# this one, created on its first start (see ``StrategyStore.ensure_default``).
DEFAULT_STRATEGY_NAME = "Default"
DEFAULT_STRATEGY_SLUG = "default"

_DEFAULT_STRATEGY_BODY = """\
Run one tick of your own domain loop. There is no bespoke playbook here on
purpose — your AGENT.md identity *is* the playbook.

Each tick:

1. Refresh your read of the market with your own routines and tools.
2. Decide what, if anything, this tick calls for — inside your domain and inside
   the configured risk limits.
3. Act on it, or explicitly decide to do nothing. Both are valid outcomes.
4. Journal the decision and the reasoning behind it.

Prefer doing nothing over acting on a weak read. When you want a tighter, more
specific loop, write a dedicated strategy under this agent and run that instead.
"""


@dataclass
class Strategy:
    agent_slug: str  # the owning Agent's slug
    name: str
    description: str = ""
    instructions: str = ""  # body: the TACTIC of the tick (not the identity)
    agent_key: str | None = None  # optional model override of the Agent's default
    skills: list[str] = field(default_factory=list)
    default_config: dict[str, Any] = field(default_factory=dict)
    default_trading_context: str = ""
    created_by: int = 0  # user_id
    created_at: str = ""  # ISO timestamp

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def slug(self) -> str:
        """Filesystem-safe slug derived from the strategy name (unique per agent)."""
        return slugify(self.name)

    @property
    def key(self) -> str:
        """Opaque composite identity ``"{agent_slug}.{slug}"`` (MCP strategy_id)."""
        return f"{self.agent_slug}.{self.slug}"

    @property
    def dir(self) -> Path:
        """This strategy's folder: agents/{agent_slug}/strategies/{slug}/."""
        return _DATA_ROOT / self.agent_slug / "strategies" / self.slug


def split_key(key: str) -> tuple[str, str] | None:
    """Split an opaque strategy key ``"{agent_slug}.{slug}"`` into its parts.

    Slugs never contain ``.`` (:func:`condor.frontmatter.slugify` strips it), so the first dot is the
    boundary. Returns None when the key has no dot.
    """
    if "." not in key:
        return None
    agent_slug, sslug = key.split(".", 1)
    return agent_slug, sslug


def _load_strategy_from_file(path: Path, agent_slug: str) -> Strategy | None:
    """Load a Strategy from a ``strategy.md`` file under an agent."""
    try:
        meta, body = parse_frontmatter(path.read_text())
        return Strategy(
            agent_slug=agent_slug,
            name=meta.get("name", path.parent.name),
            description=meta.get("description", ""),
            instructions=body,
            agent_key=meta.get("agent_key") or None,
            skills=meta.get("skills", []) or [],
            default_config=meta.get("default_config", {}) or {},
            default_trading_context=meta.get("default_trading_context", ""),
            created_by=meta.get("created_by", 0),
            created_at=meta.get("created_at", ""),
        )
    except Exception:
        log.exception("Failed to load strategy from %s", path)
        return None


class StrategyStore:
    """CRUD for strategies stored as ``strategy.md`` under ``{agent}/strategies/``.

    Every method is scoped to an owning ``agent_slug``; ``list_all`` and
    ``get_by_key`` span all agents for callers (overviews, MCP) that need a flat
    view keyed by the opaque composite id.
    """

    def _strategies_root(self, agent_slug: str) -> Path:
        return _DATA_ROOT / agent_slug / "strategies"

    def _strategy_md_path(self, strategy: Strategy) -> Path:
        return strategy.dir / "strategy.md"

    def create(
        self,
        agent_slug: str,
        name: str,
        description: str = "",
        instructions: str = "",
        agent_key: str | None = None,
        skills: list[str] | None = None,
        default_config: dict | None = None,
        default_trading_context: str = "",
        created_by: int = 0,
    ) -> Strategy:
        strategy = Strategy(
            agent_slug=agent_slug,
            name=name,
            description=description,
            instructions=instructions,
            agent_key=agent_key,
            skills=skills or [],
            default_config=default_config or {},
            default_trading_context=default_trading_context,
            created_by=created_by,
        )
        self._save(strategy)
        log.info(
            "Created strategy %s under agent %s (dir: %s)",
            strategy.slug,
            agent_slug,
            strategy.dir,
        )
        return strategy

    def get(self, agent_slug: str, sslug: str) -> Strategy | None:
        path = self._strategies_root(agent_slug) / sslug / "strategy.md"
        if not path.exists():
            return None
        return _load_strategy_from_file(path, agent_slug)

    def get_by_key(self, key: str) -> Strategy | None:
        """Look up a strategy by its opaque ``"{agent_slug}.{slug}"`` key."""
        parts = split_key(key)
        if not parts:
            return None
        return self.get(parts[0], parts[1])

    def ensure_default(self, agent_slug: str) -> Strategy | None:
        """Return this Agent's default playbook, creating it on first use.

        This is what makes *every* Agent loopable: authoring a strategy by hand
        is an optimization, not a prerequisite. Returns None only when no such
        Agent exists.
        """
        existing = self.get(agent_slug, DEFAULT_STRATEGY_SLUG)
        if existing is not None:
            return existing

        from .agent import AgentStore  # local: agent.py imports from this module

        agent = AgentStore().get(agent_slug)
        if agent is None:
            return None
        log.info("Materializing default strategy for agent %s", agent_slug)
        return self.create(
            agent_slug=agent_slug,
            name=DEFAULT_STRATEGY_NAME,
            description=f"Default loop — {agent.name}'s own identity is the playbook.",
            instructions=_DEFAULT_STRATEGY_BODY,
            created_by=agent.created_by,
        )

    def resolve_for_loop(self, key: str) -> Strategy | None:
        """Resolve what to loop from ``key``, materializing a default if needed.

        ``key`` is either an explicit composite ``"{agent_slug}.{strategy_slug}"``
        or a **bare agent slug** — the same leniency ``manage_routines`` and
        ``manage_skill`` already grant. A bare slug resolves to the agent's only
        playbook when it has exactly one, and otherwise to its default one
        (created here if this is the first start). Returns None when neither a
        strategy nor an agent matches.
        """
        if "." in key:
            return self.get_by_key(key)
        owned = self.list(key)
        if len(owned) == 1:
            return owned[0]
        return self.ensure_default(key)

    def list(self, agent_slug: str) -> list[Strategy]:
        strategies: list[Strategy] = []
        root = self._strategies_root(agent_slug)
        if not root.exists():
            return strategies
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            md = d / "strategy.md"
            if not md.exists():
                continue
            s = _load_strategy_from_file(md, agent_slug)
            if s is not None:
                strategies.append(s)
        return strategies

    def list_all(self) -> list[Strategy]:
        """Every strategy across every agent (flat view for overviews/MCP)."""
        strategies: list[Strategy] = []
        if not _DATA_ROOT.exists():
            return strategies
        for agent_dir in sorted(_DATA_ROOT.iterdir()):
            if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
                continue
            strategies.extend(self.list(agent_dir.name))
        return strategies

    def update(self, strategy: Strategy) -> None:
        self._save(strategy)

    def delete(self, agent_slug: str, sslug: str) -> bool:
        strategy = self.get(agent_slug, sslug)
        if not strategy:
            return False
        try:
            shutil.rmtree(strategy.dir)
        except Exception:
            log.exception("Failed to remove strategy dir %s", strategy.dir)
            return False
        log.info("Deleted strategy %s under agent %s", sslug, agent_slug)
        return True

    def _save(self, strategy: Strategy) -> None:
        meta = {
            "name": strategy.name,
            "description": strategy.description,
            "agent_key": strategy.agent_key,
            "skills": strategy.skills,
            "default_config": strategy.default_config,
            "default_trading_context": strategy.default_trading_context,
            "created_by": strategy.created_by,
            "created_at": strategy.created_at,
        }
        strategy.dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._strategy_md_path(strategy),
            render_frontmatter(meta, strategy.instructions),
        )
