"""YAML-frontmatter round-trip and slug helpers — the one implementation.

Every markdown-with-frontmatter file Condor persists (``AGENT.md``,
``strategy.md``, memory facts, skill playbooks) goes through this module, so
parse and render can never drift apart between callers. Consolidated from the
near-verbatim private copies in ``condor/agents/strategy.py`` and
``condor/memory/store.py`` (ARCH-202), following the same pattern that
collapsed the atomic-write copies into :mod:`condor.fsutil` (ARCH-148).

The rendered format is byte-identical to what both former copies produced
(``yaml.dump`` with ``default_flow_style=False``, ``allow_unicode=True``,
``sort_keys=False``), so existing on-disk files round-trip unchanged.
"""

from __future__ import annotations

import logging
import re

import yaml

log = logging.getLogger(__name__)


def slugify(name: str, fallback: str = "unnamed") -> str:
    """Convert a name to a filesystem-safe slug.

    Example: "RIVER Scalper v2" -> "river_scalper_v2"

    ``fallback`` is returned when nothing sluggable remains (empty or
    all-punctuation names); callers pass their historical word so existing
    on-disk slugs are preserved (agents/strategies use the default
    ``"unnamed"``, the memory/skill stores use ``"memory"``).
    """
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s-]+", "_", s)
    return s.strip("_") or fallback


def parse_frontmatter(text: str) -> tuple[dict, str]:
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


def render_frontmatter(meta: dict, body: str) -> str:
    """Render YAML frontmatter + markdown body."""
    frontmatter = yaml.dump(
        meta, default_flow_style=False, allow_unicode=True, sort_keys=False
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{body}\n"
