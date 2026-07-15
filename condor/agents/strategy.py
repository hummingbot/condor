"""Strategy definitions and persistence — a *playbook* owned by an Agent.

A strategy is a pure playbook template (refactor-01b): ONE flat file of YAML
frontmatter + markdown body — all operational state (sessions, learnings,
experiments) lives at the agent level::

    agents/
        {agent_slug}/
            AGENT.md                       # the owning Agent (see agent.py)
            learnings.md  sessions/  experiments/ # agent-level history (journal.py)
            routines/  skills/  store/           # the shared brain
            strategies/
                {strategy_slug}.md         # this playbook: tactics + default_config

A strategy is identified by the pair ``(agent_slug, slug)``; its opaque composite
key ``"{agent_slug}.{slug}"`` is what MCP strategy-CRUD passes around as
``strategy_id``. It is a start-time selector plus session metadata — never part
of a session's identity (session ids are ``"{agent_slug}_{N}"``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "agents"


def _slugify(name: str) -> str:
    """Convert a name to a filesystem-safe slug.

    Example: "RIVER Scalper v2" -> "river_scalper_v2"
    """
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s-]+", "_", s)
    return s.strip("_") or "unnamed"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter and markdown body from a file."""
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    end = text.find("---", 3)
    if end == -1:
        return {}, text

    frontmatter_str = text[3:end].strip()
    body = text[end + 3 :].strip()

    try:
        meta = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError:
        log.warning("Failed to parse YAML frontmatter")
        meta = {}

    return meta, body


def _render_frontmatter(meta: dict, body: str) -> str:
    """Render YAML frontmatter + markdown body."""
    frontmatter = yaml.dump(
        meta, default_flow_style=False, allow_unicode=True, sort_keys=False
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{body}\n"


@dataclass
class Strategy:
    agent_slug: str  # the owning Agent's slug
    name: str
    description: str = ""
    instructions: str = ""  # body: the TACTIC of the tick (not the identity)
    agent_key: str | None = None  # optional model override of the Agent's default
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
        return _slugify(self.name)

    @property
    def key(self) -> str:
        """Opaque composite identity ``"{agent_slug}.{slug}"`` (MCP strategy_id)."""
        return f"{self.agent_slug}.{self.slug}"

    @property
    def path(self) -> Path:
        """This strategy's file: agents/{agent_slug}/strategies/{slug}.md."""
        return _DATA_ROOT / self.agent_slug / "strategies" / f"{self.slug}.md"


def split_key(key: str) -> tuple[str, str] | None:
    """Split an opaque strategy key ``"{agent_slug}.{slug}"`` into its parts.

    Slugs never contain ``.`` (``_slugify`` strips it), so the first dot is the
    boundary. Returns None when the key has no dot.
    """
    if "." not in key:
        return None
    agent_slug, sslug = key.split(".", 1)
    return agent_slug, sslug


def _load_strategy_from_file(path: Path, agent_slug: str) -> Strategy | None:
    """Load a Strategy from its ``{slug}.md`` file under an agent."""
    try:
        meta, body = _parse_frontmatter(path.read_text())
        return Strategy(
            agent_slug=agent_slug,
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            instructions=body,
            agent_key=meta.get("agent_key") or None,
            default_config=meta.get("default_config", {}) or {},
            default_trading_context=meta.get("default_trading_context", ""),
            created_by=meta.get("created_by", 0),
            created_at=meta.get("created_at", ""),
        )
    except Exception:
        log.exception("Failed to load strategy from %s", path)
        return None


class StrategyStore:
    """CRUD for strategies stored as flat ``{slug}.md`` files under
    ``{agent}/strategies/``.

    Every method is scoped to an owning ``agent_slug``; ``list_all`` and
    ``get_by_key`` span all agents for callers (overviews, MCP) that need a flat
    view keyed by the opaque composite id.
    """

    def _strategies_root(self, agent_slug: str) -> Path:
        return _DATA_ROOT / agent_slug / "strategies"

    def create(
        self,
        agent_slug: str,
        name: str,
        description: str = "",
        instructions: str = "",
        agent_key: str | None = None,
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
            default_config=default_config or {},
            default_trading_context=default_trading_context,
            created_by=created_by,
        )
        self._save(strategy)
        log.info(
            "Created strategy %s under agent %s (%s)",
            strategy.slug,
            agent_slug,
            strategy.path,
        )
        return strategy

    def get(self, agent_slug: str, sslug: str) -> Strategy | None:
        path = self._strategies_root(agent_slug) / f"{sslug}.md"
        if not path.exists():
            return None
        return _load_strategy_from_file(path, agent_slug)

    def get_by_key(self, key: str) -> Strategy | None:
        """Look up a strategy by its opaque ``"{agent_slug}.{slug}"`` key."""
        parts = split_key(key)
        if not parts:
            return None
        return self.get(parts[0], parts[1])

    def list(self, agent_slug: str) -> list[Strategy]:
        strategies: list[Strategy] = []
        root = self._strategies_root(agent_slug)
        if not root.exists():
            return strategies
        for md in sorted(root.glob("*.md")):
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
        path = self._strategies_root(agent_slug) / f"{sslug}.md"
        if not path.exists():
            return False
        try:
            path.unlink()
        except Exception:
            log.exception("Failed to remove strategy file %s", path)
            return False
        log.info("Deleted strategy %s under agent %s", sslug, agent_slug)
        return True

    def _save(self, strategy: Strategy) -> None:
        meta = {
            "name": strategy.name,
            "description": strategy.description,
            "agent_key": strategy.agent_key,
            "default_config": strategy.default_config,
            "default_trading_context": strategy.default_trading_context,
            "created_by": strategy.created_by,
            "created_at": strategy.created_at,
        }
        strategy.path.parent.mkdir(parents=True, exist_ok=True)
        strategy.path.write_text(_render_frontmatter(meta, strategy.instructions))
