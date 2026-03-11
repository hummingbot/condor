"""Strategy definitions and persistence.

Strategies are stored as Markdown files with YAML frontmatter
under ``data/trading_agents/strategies/``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_STRATEGIES_DIR = Path(__file__).parent.parent.parent / "data" / "trading_agents" / "strategies"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter and markdown body from a file.

    Expected format:
        ---
        key: value
        ---
        # Markdown body
    """
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    # Find closing ---
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


class StrategyStore:
    """CRUD for strategy definitions, persisted as Markdown files with YAML frontmatter."""

    def __init__(self):
        self._dir = _STRATEGIES_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._migrate_json_to_md()

    def _migrate_json_to_md(self) -> None:
        """Auto-migrate any legacy .json strategy files to .md format."""
        for json_path in list(self._dir.glob("*.json")):
            try:
                data = json.loads(json_path.read_text())
                strategy = Strategy(**data)
                self._save(strategy)
                json_path.unlink()
                log.info("Migrated strategy %s from JSON to Markdown", strategy.id)
            except Exception:
                log.exception("Failed to migrate strategy file: %s", json_path)

    def _path(self, strategy_id: str) -> Path:
        return self._dir / f"{strategy_id}.md"

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
        log.info("Created strategy %s: %s", strategy.id, strategy.name)
        return strategy

    def get(self, strategy_id: str) -> Strategy | None:
        path = self._path(strategy_id)
        if not path.exists():
            return None
        try:
            text = path.read_text()
            meta, body = _parse_frontmatter(text)
            return Strategy(
                id=meta.get("id", strategy_id),
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
            log.exception("Failed to load strategy %s", strategy_id)
            return None

    def list_all(self, user_id: int | None = None) -> list[Strategy]:
        strategies = []
        for path in sorted(self._dir.glob("*.md")):
            try:
                text = path.read_text()
                meta, body = _parse_frontmatter(text)
                s = Strategy(
                    id=meta.get("id", path.stem),
                    name=meta.get("name", ""),
                    description=meta.get("description", ""),
                    agent_key=meta.get("agent_key", "claude-code"),
                    instructions=body,
                    skills=meta.get("skills", []),
                    default_config=meta.get("default_config", {}),
                    created_by=meta.get("created_by", 0),
                    created_at=meta.get("created_at", ""),
                )
                if user_id is None or s.created_by == user_id:
                    strategies.append(s)
            except Exception:
                log.warning("Skipping corrupted strategy file: %s", path)
        return strategies

    def update(self, strategy: Strategy) -> None:
        self._save(strategy)

    def delete(self, strategy_id: str) -> bool:
        path = self._path(strategy_id)
        if path.exists():
            path.unlink()
            log.info("Deleted strategy %s", strategy_id)
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
        self._path(strategy.id).write_text(
            _render_frontmatter(meta, strategy.instructions)
        )
