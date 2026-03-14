"""Strategy definitions and persistence.

Each strategy is stored as ``agent.md`` (YAML frontmatter + markdown body)
inside its own agent folder::

    data/trading_agents/
        river_scalper/
            agent.md          # strategy definition
            trading_sessions/
                session_1/
                    ...
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "data" / "trading_agents"
_LEGACY_STRATEGIES_DIR = _DATA_ROOT / "strategies"


def _slugify(name: str) -> str:
    """Convert a strategy name to a filesystem-safe slug.

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
    body = text[end + 3:].strip()

    try:
        meta = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError:
        log.warning("Failed to parse YAML frontmatter")
        meta = {}

    return meta, body


def _render_frontmatter(meta: dict, body: str) -> str:
    """Render YAML frontmatter + markdown body."""
    frontmatter = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n\n{body}\n"


def _load_strategy_from_file(path: Path, fallback_id: str = "") -> Strategy | None:
    """Load a Strategy from an agent.md file."""
    try:
        text = path.read_text()
        meta, body = _parse_frontmatter(text)
        return Strategy(
            id=meta.get("id", fallback_id),
            name=meta.get("name", ""),
            description=meta.get("description", ""),
            agent_key=meta.get("agent_key", "claude-code"),
            instructions=body,
            skills=meta.get("skills", []),
            default_config=meta.get("default_config", {}),
            created_by=meta.get("created_by", 0),
            created_at=meta.get("created_at", ""),
        )
    except Exception:
        log.exception("Failed to load strategy from %s", path)
        return None


@dataclass
class Strategy:
    id: str
    name: str
    description: str
    agent_key: str  # "claude-code" or "gemini"
    instructions: str  # The strategy logic text for the LLM
    skills: list[str] = field(default_factory=list)  # Optional skill names
    default_config: dict[str, Any] = field(default_factory=dict)
    created_by: int = 0  # user_id
    created_at: str = ""  # ISO timestamp

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def slug(self) -> str:
        """Filesystem-safe slug derived from the strategy name."""
        return _slugify(self.name)

    @property
    def agent_dir(self) -> Path:
        """Path to this strategy's agent folder: data/trading_agents/{slug}/."""
        return _DATA_ROOT / self.slug


class StrategyStore:
    """CRUD for strategy definitions stored as agent.md in agent folders.

    Primary location: ``data/trading_agents/{slug}/agent.md``
    We also maintain an ID-based index for fast lookup by strategy ID.
    """

    def __init__(self):
        _DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_strategies()
        # Move old hex-ID agent folders to _legacy/
        from .journal import migrate_legacy_agents
        migrate_legacy_agents()

    def _migrate_legacy_strategies(self) -> None:
        """Migrate strategies from old strategies/ dir and .json files to agent folders."""
        if not _LEGACY_STRATEGIES_DIR.exists():
            return

        # Migrate .json files
        for json_path in list(_LEGACY_STRATEGIES_DIR.glob("*.json")):
            try:
                data = json.loads(json_path.read_text())
                strategy = Strategy(**data)
                self._save(strategy)
                json_path.unlink()
                log.info("Migrated strategy %s from JSON to agent folder", strategy.id)
            except Exception:
                log.exception("Failed to migrate strategy file: %s", json_path)

        # Migrate .md files from strategies/ to agent folders
        for md_path in list(_LEGACY_STRATEGIES_DIR.glob("*.md")):
            try:
                s = _load_strategy_from_file(md_path, fallback_id=md_path.stem)
                if s:
                    self._save(s)
                    md_path.unlink()
                    log.info("Migrated strategy %s to agent folder %s/", s.id, s.slug)
            except Exception:
                log.exception("Failed to migrate strategy file: %s", md_path)

        # Remove strategies/ dir if empty
        try:
            if _LEGACY_STRATEGIES_DIR.exists() and not any(_LEGACY_STRATEGIES_DIR.iterdir()):
                _LEGACY_STRATEGIES_DIR.rmdir()
                log.info("Removed empty strategies/ directory")
        except Exception:
            pass

    def _agent_md_path(self, strategy: Strategy) -> Path:
        """Primary path: data/trading_agents/{slug}/agent.md."""
        return strategy.agent_dir / "agent.md"

    def create(
        self,
        name: str,
        description: str,
        agent_key: str,
        instructions: str,
        skills: list[str] | None = None,
        default_config: dict | None = None,
        created_by: int = 0,
    ) -> Strategy:
        strategy = Strategy(
            id=uuid.uuid4().hex[:12],
            name=name,
            description=description,
            agent_key=agent_key,
            instructions=instructions,
            skills=skills or [],
            default_config=default_config or {},
            created_by=created_by,
        )
        self._save(strategy)
        log.info("Created strategy %s: %s (dir: %s)", strategy.id, strategy.name, strategy.slug)
        return strategy

    def get(self, strategy_id: str) -> Strategy | None:
        """Look up a strategy by ID — scans all agent folders."""
        for agent_dir in self._iter_agent_dirs():
            agent_md = agent_dir / "agent.md"
            if not agent_md.exists():
                continue
            s = _load_strategy_from_file(agent_md, fallback_id=agent_dir.name)
            if s and s.id == strategy_id:
                return s
        return None

    def list_all(self, user_id: int | None = None) -> list[Strategy]:
        strategies = []
        for agent_dir in sorted(self._iter_agent_dirs()):
            agent_md = agent_dir / "agent.md"
            if not agent_md.exists():
                continue
            s = _load_strategy_from_file(agent_md, fallback_id=agent_dir.name)
            if s is None:
                continue
            if user_id is None or s.created_by == user_id:
                strategies.append(s)
        return strategies

    def update(self, strategy: Strategy) -> None:
        self._save(strategy)

    def delete(self, strategy_id: str) -> bool:
        strategy = self.get(strategy_id)
        if not strategy:
            return False
        agent_md = self._agent_md_path(strategy)
        if agent_md.exists():
            agent_md.unlink()
            # Remove the agent dir if it's now empty (no sessions)
            agent_dir = strategy.agent_dir
            has_sessions = (agent_dir / "trading_sessions").exists()
            if not has_sessions:
                try:
                    shutil.rmtree(agent_dir)
                except Exception:
                    pass
            log.info("Deleted strategy %s (%s)", strategy_id, strategy.slug)
            return True
        return False

    def _save(self, strategy: Strategy) -> None:
        meta = {
            "id": strategy.id,
            "name": strategy.name,
            "description": strategy.description,
            "agent_key": strategy.agent_key,
            "skills": strategy.skills,
            "default_config": strategy.default_config,
            "created_by": strategy.created_by,
            "created_at": strategy.created_at,
        }
        content = _render_frontmatter(meta, strategy.instructions)

        agent_dir = strategy.agent_dir
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "agent.md").write_text(content)

    def _iter_agent_dirs(self):
        """Yield directories under data/trading_agents/ that could contain an agent.md."""
        if not _DATA_ROOT.exists():
            return
        for d in _DATA_ROOT.iterdir():
            if not d.is_dir():
                continue
            # Skip internal/legacy dirs
            if d.name.startswith("_") or d.name == "strategies":
                continue
            yield d
