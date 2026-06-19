"""Domain-expert agent definitions and discovery.

A *domain expert* is a specialized agent that ``condor`` (the coordinator) can
**consult**: it has focused domain expertise, a restricted tool allowlist, and its
own domain-scoped memory/skills store (FEAT-003, keyed by the directory slug). It
lives beside trading strategies under ``trading_agents/<slug>/`` and is defined in
the standard ``AGENT.md`` file with ``role: expert`` in its frontmatter::

    trading_agents/
        executor_manager/
            AGENT.md          # domain-expert identity (role: expert)
            skills/<slug>/SKILL.md   # authored domain playbooks (read-only)
            store/user_<id>/  # learned domain memory + skills (per user)

The discriminator is the frontmatter ``role`` field, **not** the filename case:
macOS/Windows filesystems are case-insensitive, so a dir's ``AGENT.md`` and a
strategy's ``agent.md`` resolve to the same path. ``ExpertStore`` loads only dirs
whose ``AGENT.md`` declares ``role: expert``; :class:`StrategyStore
<condor.trading_agent.strategy.StrategyStore>` skips those same dirs. This is
read-only: experts are authored, not created from chat.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from condor.memory.store import _parse_frontmatter

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "trading_agents"


@dataclass
class Expert:
    slug: str  # directory name == agent_slug for the domain store
    name: str
    description: str
    when_to_consult: str  # the trigger condor uses to decide to consult
    agent_key: str  # MUST be a pydantic-ai model (tool allowlist needs it)
    tools: list[str] = field(default_factory=list)  # tool-name allowlist
    instructions: str = ""  # the expert's system prompt (AGENT.md body)
    server_required: bool = True  # needs a Hummingbot server (market/executor tools)

    @property
    def agent_dir(self) -> Path:
        return _DATA_ROOT / self.slug


def _load_expert_from_dir(agent_dir: Path) -> Expert | None:
    """Load an Expert from ``<agent_dir>/AGENT.md`` if it declares role: expert.

    Returns None when the file is absent or is a strategy (no ``role: expert``),
    so strategy dirs — whose ``agent.md`` matches ``AGENT.md`` on case-insensitive
    filesystems — are never mistaken for experts.
    """
    path = agent_dir / "AGENT.md"
    if not path.exists():
        return None
    try:
        meta, body = _parse_frontmatter(path.read_text())
        if meta.get("role") != "expert":
            return None
        return Expert(
            slug=agent_dir.name,
            name=meta.get("name", agent_dir.name),
            description=meta.get("description", ""),
            when_to_consult=meta.get("when_to_consult", ""),
            agent_key=meta.get("agent_key", ""),
            tools=meta.get("tools", []) or [],
            instructions=body,
            server_required=meta.get("server_required", True),
        )
    except Exception:
        log.exception("Failed to load expert from %s", path)
        return None


class ExpertStore:
    """Read-only discovery of domain experts under ``trading_agents/*/AGENT.md``."""

    def get(self, slug: str) -> Expert | None:
        if not slug:
            return None
        return _load_expert_from_dir(_DATA_ROOT / slug)

    def list_all(self) -> list[Expert]:
        experts: list[Expert] = []
        for d in self._iter_agent_dirs():
            e = _load_expert_from_dir(d)
            if e is not None:
                experts.append(e)
        return experts

    def list_index(self) -> str:
        """Injectable index — one line per expert, mirroring the SKILLS index.

        Empty string when there are no experts, so callers inject nothing.
        """
        lines = [
            f"- [{e.slug}] {e.when_to_consult or e.description}"
            for e in self.list_all()
        ]
        return "\n".join(lines)

    def _iter_agent_dirs(self):
        if not _DATA_ROOT.exists():
            return
        for d in sorted(_DATA_ROOT.iterdir()):
            if not d.is_dir() or d.name.startswith("_") or d.name == "strategies":
                continue
            yield d
