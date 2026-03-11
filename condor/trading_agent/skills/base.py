"""Base skill interface for trading agent skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SkillResult:
    name: str
    data: dict[str, Any]
    summary: str  # 1-5 line text for LLM prompt


@dataclass
class SkillTemplate:
    """A markdown-based skill template (auto-discovered from .md files)."""
    name: str          # from filename stem
    description: str   # from ## Description
    prompt: str        # from ## Prompt (with {placeholders})


def parse_skill_markdown(text: str) -> SkillTemplate:
    """Parse a skill markdown file into a SkillTemplate.

    Expected sections: ## Description and ## Prompt
    """
    name = ""
    description = ""
    prompt = ""

    # Extract title from first # heading
    for line in text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            name = line[2:].strip()
            break

    # Extract sections
    current_section = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.strip().lower() == "## description":
            if current_section == "prompt":
                prompt = "\n".join(current_lines).strip()
            current_section = "description"
            current_lines = []
        elif line.strip().lower() == "## prompt":
            if current_section == "description":
                description = "\n".join(current_lines).strip()
            current_section = "prompt"
            current_lines = []
        elif current_section:
            current_lines.append(line)

    # Flush last section
    if current_section == "description":
        description = "\n".join(current_lines).strip()
    elif current_section == "prompt":
        prompt = "\n".join(current_lines).strip()

    return SkillTemplate(name=name, description=description, prompt=prompt)


class BaseSkill:
    """Abstract base for trading agent skills."""

    name: str = ""
    is_core: bool = False

    async def execute(self, client: Any, config: dict, agent_id: str = "") -> SkillResult:
        raise NotImplementedError
