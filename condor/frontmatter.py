"""YAML-frontmatter round-trip and slug helpers — the one implementation.

Every markdown-with-frontmatter file Condor persists (``AGENT.md``,
``loop.md``, memory facts, skill playbooks) goes through this module, so
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

# libyaml's loader is ~10x faster than the pure-Python one and every store
# re-parses its headers on each listing. Loader only: ``CSafeDumper`` escapes
# and folds differently (emoji, long lines), which would break the byte-identical
# render promised above.
try:
    from yaml import CSafeLoader as _SafeLoader
except ImportError:  # PyYAML built without libyaml
    from yaml import SafeLoader as _SafeLoader

log = logging.getLogger(__name__)

# The closing delimiter is a whole line. Matching ``---`` as a substring ended
# the header at the first dash run inside a *value*, silently dropping every key
# after it (tool allowlist, server pin, loop config).
_CLOSING_DELIMITER_RE = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)


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

    closing = _CLOSING_DELIMITER_RE.search(text, 3)
    if closing is None:
        return {}, text

    frontmatter_str = text[3 : closing.start()].strip()
    body = text[closing.end() :].strip()

    try:
        meta = yaml.load(frontmatter_str, Loader=_SafeLoader) or {}
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
