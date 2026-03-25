"""Loads and renders Claude Code SKILL.md files for trading agent snapshots."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Project root .claude/skills/ directory
_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "skills"


@dataclass
class SkillInfo:
    """Parsed SKILL.md metadata."""

    name: str
    description: str
    body: str  # markdown content below frontmatter


def _parse_skill_md(text: str) -> SkillInfo:
    """Parse a SKILL.md file with YAML frontmatter."""
    name = ""
    description = ""
    body = text

    # Extract YAML frontmatter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2].strip()

            for line in frontmatter.splitlines():
                line = line.strip()
                if line.startswith("name:"):
                    name = line[5:].strip().strip("\"'")
                elif line.startswith("description:"):
                    description = line[12:].strip().strip("\"'")

    return SkillInfo(name=name, description=description, body=body)


def _render_placeholders(text: str, config: dict) -> str:
    """Replace {{key}} placeholders with values from config. Missing keys left as-is."""

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        return str(config.get(key, "{{" + key + "}}"))

    return re.sub(r"\{\{(\w+)\}\}", replacer, text)


def load_skill(name: str) -> SkillInfo | None:
    """Load a single SKILL.md by skill directory name."""
    skill_path = _SKILLS_DIR / name / "SKILL.md"
    if not skill_path.is_file():
        log.warning("Skill not found: %s", skill_path)
        return None
    try:
        return _parse_skill_md(skill_path.read_text())
    except Exception:
        log.exception("Failed to load skill: %s", name)
        return None


def list_skills() -> list[SkillInfo]:
    """List all available SKILL.md files."""
    if not _SKILLS_DIR.is_dir():
        return []
    skills = []
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        skill_path = skill_dir / "SKILL.md"
        if skill_path.is_file():
            try:
                info = _parse_skill_md(skill_path.read_text())
                info.name = info.name or skill_dir.name
                skills.append(info)
            except Exception:
                log.exception("Failed to load skill: %s", skill_dir.name)
    return skills


def get_tick_skills(names: list[str], config: dict) -> list[str]:
    """Load, render, and format skills for tick prompt injection.

    Returns list of '[SKILL - Name]\\n...' sections.
    """
    results = []
    for name in names:
        info = load_skill(name)
        if not info:
            continue
        rendered = _render_placeholders(info.body, config)
        display_name = info.name or name
        results.append(f"[SKILL - {display_name}]\n{rendered}")
    return results
