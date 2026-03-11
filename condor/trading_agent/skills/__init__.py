"""Skill registry -- discovers and runs skills for trading agents."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .base import BaseSkill, SkillResult, SkillTemplate, parse_skill_markdown

log = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent

# Python skill registry (core skills only)
_REGISTRY: dict[str, BaseSkill] = {}

# Markdown skill templates (optional skills)
_TEMPLATES: dict[str, SkillTemplate] = {}


def _auto_register() -> None:
    """Import built-in Python skill modules and discover markdown templates."""
    # Core Python skills
    from . import executors  # noqa: F401

    # Discover markdown skill templates
    for md_path in _SKILLS_DIR.glob("*.md"):
        try:
            template = parse_skill_markdown(md_path.read_text())
            template.name = template.name or md_path.stem
            _TEMPLATES[md_path.stem] = template
            log.debug("Registered skill template: %s", md_path.stem)
        except Exception:
            log.exception("Failed to load skill template: %s", md_path)


def register_skill(skill: BaseSkill) -> None:
    _REGISTRY[skill.name] = skill
    log.debug("Registered skill: %s (core=%s)", skill.name, skill.is_core)


def get_skill(name: str) -> BaseSkill | None:
    if not _REGISTRY:
        _auto_register()
    return _REGISTRY.get(name)


def list_skills() -> list[BaseSkill]:
    if not _REGISTRY:
        _auto_register()
    return list(_REGISTRY.values())


def list_core_skills() -> list[BaseSkill]:
    return [s for s in list_skills() if s.is_core]


def list_optional_skills() -> list[SkillTemplate]:
    """Return available markdown skill templates."""
    if not _TEMPLATES:
        _auto_register()
    return list(_TEMPLATES.values())


def get_skill_templates(names: list[str], config: dict) -> list[str]:
    """Render matching skill templates with config values.

    Returns list of formatted prompt sections like "[SKILL - Orderbook]\n..."
    """
    if not _TEMPLATES:
        _auto_register()

    results = []
    for name in names:
        template = _TEMPLATES.get(name)
        if not template:
            continue
        try:
            rendered = template.prompt.format_map(_SafeFormatDict(config))
        except Exception:
            log.warning("Failed to render skill template %s", name)
            rendered = template.prompt
        results.append(f"[SKILL - {template.name}]\n{rendered}")
    return results


class _SafeFormatDict(dict):
    """Dict that returns the placeholder unchanged for missing keys."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class SkillRegistry:
    """Convenience wrapper used by TickEngine."""

    async def run_core_skills(
        self, client: Any, config: dict, agent_id: str = ""
    ) -> dict[str, SkillResult]:
        """Run all core Python skills and return {name: SkillResult} dict."""
        if not _REGISTRY:
            _auto_register()

        results: dict[str, SkillResult] = {}
        for skill in list_core_skills():
            try:
                result = await skill.execute(client, config, agent_id=agent_id)
                results[result.name] = result
            except Exception:
                log.exception("Core skill %s failed", skill.name)
                results[skill.name] = SkillResult(
                    name=skill.name, data={}, summary=f"(skill {skill.name} failed)"
                )
        return results

    async def run_skill(self, name: str, client: Any, config: dict) -> SkillResult | None:
        """Run a single Python skill by name."""
        skill = get_skill(name)
        if not skill:
            return None
        return await skill.execute(client, config)
